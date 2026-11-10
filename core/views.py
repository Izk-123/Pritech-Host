"""
Core views for the Pritech Host platform.

Sections
--------
1. Public storefront   — home, checkout, checkout success
2. Customer dashboard  — dashboard shell, profile shell, JSON APIs
3. Payment gateway     — PayChangu webhook + browser callbacks
4. Legal documents     — versioned legal pages + cookie consent API

Conventions
-----------
* Read-only site content lives on ``SiteSettings`` (exposed as ``site``).
* Email and Hestia provisioning run asynchronously via Celery.
* Every path that settles a payment funnels through
  ``_handle_renewal_payment()`` so a renewal invoice paid through any
  path extends the subscription and reactivates the Hestia account.
* Legal acceptance is recorded:
    - at signup     → via core.signals.record_signup_acceptance
    - at checkout   → inside ``CheckoutView.form_valid`` (below)
"""
import hashlib
import hmac
import json
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView, ListView, TemplateView, View

from billing.models import Invoice, Payment, Plan, Subscription
from billing.services.invoice_pdf import generate_invoice_pdf
from billing.services.paychangu import PayChanguService
from billing.services.payments import (
    finalize_invoice_payment,
    has_recent_pending_payment,
    sync_pending_payments,
)
from hosting.models import HostingAccount
from hosting.services.hestia import HestiaCP
from projects.models import Project
from users.models import CustomerProfile, User

from .forms import CheckoutForm, CustomerProfileForm, UserProfileForm
from .models import (
    SiteSettings,
    Ticket,
    LegalDocument,
    TermsAcceptance,
    CookieConsent,
)

from core.tasks import send_transactional_email_task
from hosting.tasks import provision_hosting_account_task

logger = logging.getLogger(__name__)


# ==============================================================================
# Helpers
# ==============================================================================

def _human_period(plan: Plan) -> str:
    """'monthly' → 'month', 'yearly' → 'year'."""
    return 'month' if plan.billing_period == 'monthly' else 'year'


def _get_client_ip(request) -> str | None:
    """
    Extract the real client IP behind Nginx.

    Nginx sets ``X-Forwarded-For`` to ``client, proxy1, proxy2`` — the
    leftmost entry is the origin. Used by TermsAcceptance, CookieConsent
    and signal handlers.
    """
    if request is None:
        return None
    x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded:
        return x_forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def _handle_renewal_payment(invoice: Invoice) -> None:
    """
    Extend the linked subscription when a renewal invoice is paid.

    Called from the webhook, the browser callback and the dashboard sync.
    Idempotent: after this runs once, ``renewal_invoice`` is None, so
    subsequent calls no-op.
    """
    if invoice.invoice_type != 'renewal' or not invoice.subscription_id:
        return

    sub = invoice.subscription

    if sub.status == 'cancelled':
        logger.error(
            f"[RENEWAL] Payment received for cancelled subscription "
            f"{sub.id} ({sub.user.email}) — manual intervention required."
        )
        return

    sub.end_date = sub.end_date + timedelta(days=sub.plan.period_days)
    sub.reset_dunning_flags()
    sub.save()

    account = HostingAccount.objects.filter(user=sub.user).first()
    if account and account.hestia_username and not account.is_active:
        HestiaCP().unsuspend_user(account.hestia_username)
        account.is_active = True
        account.save(update_fields=['is_active'])
        logger.info(f"[RENEWAL] Unsuspended Hestia account for {sub.user.email}")

    logger.info(
        f"[RENEWAL] Subscription {sub.id} renewed — "
        f"new end_date={sub.end_date}."
    )


def _post_sync_renewals(user: User) -> None:
    """Fallback for missed webhooks — apply renewal extension if any paid."""
    pending = (
        Subscription.objects
        .filter(user=user, is_active=True, renewal_invoice__isnull=False)
        .select_related('renewal_invoice', 'plan', 'user')
    )
    for sub in pending:
        if sub.renewal_invoice.status == 'paid':
            _handle_renewal_payment(sub.renewal_invoice)


# ==============================================================================
# 1. PUBLIC STOREFRONT
# ==============================================================================

class HomeView(ListView):
    """Landing page — plan cards + hero copy from SiteSettings."""
    model = Plan
    template_name = 'core/home.html'
    context_object_name = 'plans'

    def get_queryset(self):
        return Plan.objects.filter(is_active=True).order_by('price_mwk')


class CheckoutView(FormView):
    """
    SRS FR-15, FR-63, FR-64 — Checkout flow.

    Records Terms/Privacy/AUP acceptance inside ``form_valid`` — the
    acceptance is logged at the moment the customer completes checkout,
    even if the payment itself later fails.
    """
    template_name = 'core/checkout.html'
    form_class = CheckoutForm

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(
            Plan, id=self.kwargs.get('plan_id'), is_active=True
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['current_user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        deposit_amount = self.plan.price_mwk * Decimal('0.25')
        context['plan']           = self.plan
        context['deposit_amount'] = deposit_amount
        context['balance_amount'] = self.plan.price_mwk - deposit_amount
        context['billing_period'] = _human_period(self.plan)
        return context

    @transaction.atomic
    def form_valid(self, form):
        cleaned = form.cleaned_data

        # --- Step 1: resolve / create the customer ------------------------
        if self.request.user.is_authenticated:
            user = self.request.user
            user.phone_number  = user.phone_number  or cleaned.get('phone_number')
            user.date_of_birth = user.date_of_birth or cleaned.get('date_of_birth')
            user.gender        = user.gender        or cleaned.get('gender')
            user.save()

            profile, _ = CustomerProfile.objects.get_or_create(user=user)
            if not profile.company_name and cleaned.get('company_name'):
                profile.company_name = cleaned['company_name']
                profile.save()
        else:
            user = User.objects.create(
                username      = cleaned['email'],
                email         = cleaned['email'],
                first_name    = cleaned['first_name'],
                last_name     = cleaned.get('last_name', ''),
                phone_number  = cleaned['phone_number'],
                date_of_birth = cleaned['date_of_birth'],
                gender        = cleaned['gender'],
            )
            user.set_unusable_password()
            user.save()

            CustomerProfile.objects.create(
                user=user,
                company_name=cleaned.get('company_name', ''),
            )
            login(
                self.request, user,
                backend='django.contrib.auth.backends.ModelBackend',
            )

        # --- Step 2: subscription, project, deposit invoice ---------------
        end_date = timezone.now().date() + timedelta(days=self.plan.period_days)
        subscription = Subscription.objects.create(
            user=user, plan=self.plan, end_date=end_date,
        )

        Project.objects.create(
            customer=user,
            subscription=subscription,
            requested_domain=cleaned.get('requested_domain', ''),
            total_price_mwk=self.plan.price_mwk,
            stage='deposit',
        )

        deposit_amount = self.plan.price_mwk * Decimal('0.25')
        invoice = Invoice.objects.create(
            customer=user,
            due_date=timezone.now().date(),
            subtotal_mwk=deposit_amount,
            total_mwk=deposit_amount,
            is_deposit=True,
            invoice_type='deposit',
            subscription=subscription,
        )

        # --- NEW: record Terms / Privacy / AUP acceptance -----------------
        # The CheckoutForm has `accept_terms=True` (required=True) so we
        # only reach this point if the customer actually ticked the box.
        client_ip = _get_client_ip(self.request)
        user_agent = self.request.META.get('HTTP_USER_AGENT', '')[:1000]

        for slug in ('terms', 'privacy', 'aup'):
            doc = LegalDocument.current(slug)
            if doc:
                TermsAcceptance.objects.create(
                    user=user,
                    document=doc,
                    context='checkout',
                    ip_address=client_ip,
                    user_agent=user_agent,
                    content_hash=doc.content_hash(),
                )

        # --- Step 3: redirect to gateway ----------------------------------
        checkout_url = PayChanguService.initiate_payment(invoice)
        if checkout_url:
            return redirect(checkout_url)

        messages.error(self.request, "There was an issue connecting to the payment gateway.")
        return self.form_invalid(form)


class CheckoutSuccessView(TemplateView):
    """Post-payment landing page."""
    template_name = 'core/checkout_success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['invoice'] = get_object_or_404(Invoice, id=self.kwargs['invoice_id'])
        return context


# ==============================================================================
# 2. CUSTOMER DASHBOARD, PROFILE & JSON APIs
# ==============================================================================

class DashboardView(LoginRequiredMixin, TemplateView):
    """Shell template — data via ``/api/dashboard-data/``."""
    template_name = 'core/dashboard.html'
    login_url = '/accounts/login/'


class DashboardAPIView(LoginRequiredMixin, View):
    """
    Single JSON feed powering the React dashboard.
    Includes a ``renewal`` block consumed by the renewal warning banner.
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        site = SiteSettings.load()

        # Missed-webhook recovery
        sync_pending_payments(user)
        _post_sync_renewals(user)

        subscription    = Subscription.objects.filter(user=user, is_active=True).first()
        hosting_account = HostingAccount.objects.filter(user=user).first()
        project         = Project.objects.filter(customer=user).order_by('-id').first()

        if hosting_account and hosting_account.is_active:
            hosting_status  = 'active'
            hestia_username = hosting_account.hestia_username
        elif Invoice.objects.filter(customer=user, status='paid').exists():
            hosting_status  = 'provisioning'
            hestia_username = None
        else:
            hosting_status  = 'pending'
            hestia_username = None

        usage = {
            'disk_used': 0, 'disk_limit': 0, 'disk_pct': 0,
            'bw_used':   0, 'bw_limit':   0, 'bw_pct':   0,
        }
        if hosting_account and subscription:
            usage['disk_limit'] = subscription.plan.disk_space_gb
            usage['bw_limit']   = subscription.plan.bandwidth_gb
            usage['disk_used']  = round(hosting_account.disk_used_mb / 1024, 1)
            usage['bw_used']    = round(hosting_account.bandwidth_used_mb / 1024, 1)
            if usage['disk_limit']:
                usage['disk_pct'] = min(100, int(usage['disk_used'] / usage['disk_limit'] * 100))
            if usage['bw_limit']:
                usage['bw_pct']   = min(100, int(usage['bw_used']   / usage['bw_limit']   * 100))

        invoices     = Invoice.objects.filter(customer=user).order_by('-issue_date')[:5]
        next_invoice = (
            Invoice.objects
            .filter(customer=user, status='unpaid')
            .order_by('due_date')
            .first()
        )

        tickets            = Ticket.objects.filter(user=user).order_by('-created_at')[:5]
        open_tickets_count = Ticket.objects.filter(user=user, status='open').count()

        renewal_info = None
        if subscription:
            renewal_info = {
                'end_date':            subscription.end_date.strftime('%d %b %Y'),
                'days_until_expiry':   subscription.days_until_expiry,
                'status':              subscription.status,
                'is_expired':          subscription.is_expired,
                'is_in_grace_period':  subscription.is_in_grace_period,
                'has_pending_renewal': bool(
                    subscription.renewal_invoice
                    and subscription.renewal_invoice.status == 'unpaid'
                ),
                'renewal_invoice_id': (
                    subscription.renewal_invoice.id
                    if subscription.renewal_invoice else None
                ),
                'scheduled_deletion_at': (
                    subscription.scheduled_deletion_at.strftime('%d %b %Y')
                    if subscription.scheduled_deletion_at else None
                ),
            }

        return JsonResponse({
            'user_name': user.first_name or 'Customer',
            'site': {
                'name':          site.site_name,
                'cpanel_url':    site.cpanel_url,
                'support_hours': site.support_hours,
                'contact_email': site.contact_email,
                'contact_phone': site.contact_phone,
            },
            'subscription': {
                'plan_name':       subscription.plan.name,
                'price':           float(subscription.plan.price_mwk),
                'renewal_date':    subscription.end_date.strftime('%d %b %Y'),
                'billing_period':  _human_period(subscription.plan),
                'disk_limit':      subscription.plan.disk_space_gb,
                'bandwidth_limit': subscription.plan.bandwidth_gb,
                'email_limit':     subscription.plan.email_accounts,
                'ssh_access':      subscription.plan.ssh_access,
            } if subscription else None,
            'renewal': renewal_info,
            'hosting_status':  hosting_status,
            'hestia_username': hestia_username,
            'usage':           usage,
            'invoices': [
                {
                    'id':     inv.id,
                    'number': inv.invoice_number,
                    'date':   inv.issue_date.strftime('%d %b %Y'),
                    'amount': float(inv.total_mwk),
                    'status': inv.status.upper(),
                } for inv in invoices
            ],
            'next_invoice': {
                'id':       next_invoice.id,
                'number':   next_invoice.invoice_number,
                'amount':   float(next_invoice.total_mwk),
                'due_date': next_invoice.due_date.strftime('%d %b %Y'),
            } if next_invoice else None,
            'project': {
                'stage':         project.stage,
                'stage_display': project.get_stage_display(),
                'domain':        project.requested_domain,
                'total_price':   float(project.total_price_mwk),
            } if project else None,
            'tickets': [
                {
                    'id':             t.id,
                    'subject':        t.subject,
                    'status':         t.status,
                    'status_display': t.get_status_display(),
                    'created_at':     t.created_at.strftime('%d %b %Y'),
                } for t in tickets
            ],
            'open_tickets_count': open_tickets_count,
            'payment_processing': has_recent_pending_payment(user),
        })


class ProfileView(LoginRequiredMixin, TemplateView):
    template_name = 'core/profile.html'
    login_url = '/accounts/login/'


class ProfileAPIView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        user = request.user
        profile, _ = CustomerProfile.objects.get_or_create(user=user)
        return JsonResponse({
            'user': {
                'first_name':    user.first_name,
                'last_name':     user.last_name,
                'email':         user.email,
                'phone_number':  user.phone_number,
                'date_of_birth': (
                    user.date_of_birth.strftime('%Y-%m-%d')
                    if user.date_of_birth else ''
                ),
                'gender':        user.gender,
            },
            'profile': {
                'company_name': profile.company_name,
                'tin_vat':      profile.tin_vat,
                'address':      profile.address,
            },
        })

    def post(self, request, *args, **kwargs):
        user = request.user
        profile, _ = CustomerProfile.objects.get_or_create(user=user)
        data = json.loads(request.body)

        user_form    = UserProfileForm(data=data.get('user'), instance=user)
        profile_form = CustomerProfileForm(data=data.get('profile'), instance=profile)

        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            return JsonResponse({'success': True, 'message': 'Profile updated successfully.'})

        errors = {}
        if user_form.errors:    errors['user']    = user_form.errors
        if profile_form.errors: errors['profile'] = profile_form.errors
        return JsonResponse({'success': False, 'errors': errors}, status=400)


class PayInvoiceAPIView(LoginRequiredMixin, View):
    """Kick off PayChangu for an unpaid invoice (dashboard 'Pay now')."""
    def post(self, request, *args, **kwargs):
        data = json.loads(request.body)
        invoice = get_object_or_404(Invoice, id=data.get('invoice_id'), customer=request.user)
        if invoice.status == 'paid':
            return JsonResponse({'error': 'Invoice already paid'}, status=400)

        checkout_url = PayChanguService.initiate_payment(invoice)
        if checkout_url:
            return JsonResponse({'checkout_url': checkout_url})
        return JsonResponse({'error': 'Gateway error'}, status=500)


class CreateTicketAPIView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        data    = json.loads(request.body)
        subject = (data.get('subject') or '').strip()
        message = (data.get('message') or '').strip()

        if not subject or not message:
            return JsonResponse({'error': 'Subject and message are required'}, status=400)

        ticket = Ticket.objects.create(user=request.user, subject=subject, message=message)
        return JsonResponse({
            'success': True,
            'ticket': {
                'id':             ticket.id,
                'subject':        ticket.subject,
                'status':         ticket.status,
                'status_display': ticket.get_status_display(),
                'created_at':     ticket.created_at.strftime('%d %b %Y'),
            },
        })


class InvoicePDFView(LoginRequiredMixin, View):
    def get(self, request, invoice_id, *args, **kwargs):
        invoice = get_object_or_404(Invoice, id=invoice_id, customer=request.user)
        pdf_buffer = generate_invoice_pdf(invoice)
        return FileResponse(
            pdf_buffer, as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
            content_type='application/pdf',
        )


# ==============================================================================
# 3. PAYMENT GATEWAY (PayChangu)
# ==============================================================================

@method_decorator(csrf_exempt, name='dispatch')
class PayChanguWebhookView(View):
    """
    PayChangu → server notification.

    Verifies the HMAC signature, settles the invoice via
    ``finalize_invoice_payment``, then triggers the renewal extension
    if the settled invoice was a renewal.
    """
    def post(self, request, *args, **kwargs):
        logger.info(
            "PayChangu Webhook Payload: %s",
            request.body.decode('utf-8', errors='replace'),
        )

        signature = request.headers.get('Signature')
        if not signature:
            logger.warning("Webhook: Missing Signature header.")
            return HttpResponse(status=400)

        computed = hmac.new(
            settings.PAYCHANGU_WEBHOOK_SECRET.encode(),
            request.body, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(computed, signature):
            logger.warning("Webhook: Invalid Signature.")
            return HttpResponse(status=401)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        status = payload.get('status')
        tx_ref = payload.get('reference') or payload.get('tx_ref')

        if status != 'success' or not tx_ref:
            return HttpResponse(status=200)

        invoice, newly_paid = finalize_invoice_payment(tx_ref)

        if invoice is None:
            logger.warning(
                "Webhook: %s could not be verified or settled; the dashboard "
                "sync will retry.", tx_ref,
            )
            return HttpResponse(status=200)

        if newly_paid:
            logger.info("Webhook: %s paid, follow-up tasks queued.", invoice.invoice_number)
            _handle_renewal_payment(invoice)
        else:
            logger.info("Webhook: %s already paid - ignoring.", invoice.invoice_number)

        return HttpResponse(status=200)


class PaymentCallbackView(View):
    """Browser redirect after successful payment."""
    def get(self, request, *args, **kwargs):
        tx_ref = request.GET.get('tx_ref')
        if tx_ref and request.GET.get('status', 'success') == 'success':
            try:
                invoice, newly_paid = finalize_invoice_payment(tx_ref)
                if newly_paid and invoice is not None:
                    _handle_renewal_payment(invoice)
            except Exception:
                logger.exception("Callback: could not settle %s", tx_ref)
        return redirect('/dashboard/?payment=success')


class PaymentReturnView(View):
    """Browser redirect after a failed/cancelled payment."""
    def get(self, request, *args, **kwargs):
        return redirect('/dashboard/?payment=failed')


# ==============================================================================
# 4. LEGAL DOCUMENTS
# ==============================================================================

class LegalDocumentView(TemplateView):
    """
    Render any active legal document by slug.

    Valid slugs: ``terms``, ``privacy``, ``aup``, ``refund``. The
    sidebar tab switcher is populated from ``LegalDocument.SLUG_CHOICES``
    and only includes tabs for documents that actually exist (so a
    missing AUP doesn't render a broken tab).
    """
    template_name = 'core/legal/document.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        slug = self.kwargs['slug']

        doc = LegalDocument.current(slug)
        if not doc:
            raise Http404(f"Legal document '{slug}' not found.")

        # Only include tabs whose documents exist.
        available_tabs = []
        for tab_slug, tab_label in LegalDocument.SLUG_CHOICES:
            if LegalDocument.current(tab_slug) is not None:
                available_tabs.append((tab_slug, tab_label))

        context['document']       = doc
        context['active_slug']    = slug
        context['available_tabs'] = available_tabs
        return context


# ==============================================================================
# 5. COOKIE CONSENT API
# ==============================================================================

@method_decorator(csrf_exempt, name='dispatch')
class CookieConsentAPIView(View):
    """
    Records cookie banner consent from the AJAX banner.

    CSRF-exempt so the banner works for anonymous visitors who haven't
    established a session yet. This endpoint only stores consent flags
    keyed by session_key — it does not grant any privilege, so exemption
    is low-risk. Idempotent: re-submitting for the same session updates
    the existing row.
    """
    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        categories = data.get('categories', {})
        if not isinstance(categories, dict):
            return JsonResponse({'error': 'categories must be an object'}, status=400)

        # Ensure a session exists so we have a stable key.
        if not request.session.session_key:
            request.session.create()

        CookieConsent.objects.update_or_create(
            session_key=request.session.session_key,
            defaults={
                'user':       request.user if request.user.is_authenticated else None,
                'ip_address': _get_client_ip(request),
                'user_agent': request.META.get('HTTP_USER_AGENT', '')[:1000],
                'categories': categories,
            },
        )

        # Also flag in session so the banner doesn't reappear (belt and
        # braces — the banner primarily uses localStorage).
        request.session['cookie_consent_given'] = True

        return JsonResponse({'success': True})