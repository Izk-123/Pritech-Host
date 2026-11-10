"""
Core views for the Pritech Host platform.

Sections
--------
1. Public storefront   — home, checkout, checkout success
2. Customer dashboard  — dashboard shell, profile shell, JSON APIs
3. Payment gateway     — PayChangu webhook + browser callbacks

Conventions
-----------
* Read-only site content (hero copy, contact info, support hours,
  cPanel URL) lives on ``SiteSettings`` and is exposed either via the
  ``core.context_processors.site_settings`` context processor (for
  template rendering) or directly inside JSON payloads (for API
  consumers like the React dashboard).
* Per-plan attributes (price, billing period, limits, backup cadence)
  live on ``billing.Plan`` and are streamed into JSON so the
  front-end never hardcodes values.
* Email delivery and HestiaCP provisioning run **asynchronously** via
  Celery (see ``core/tasks.py`` and ``hosting/tasks.py``). The
  PayChangu webhook must never block on SMTP or the Hestia API — it
  queues tasks and returns 200 immediately.
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
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView, ListView, TemplateView, View

# --- Local app imports -----------------------------------------------------
from billing.models import Invoice, Payment, Plan, Subscription
from billing.services.invoice_pdf import generate_invoice_pdf
from billing.services.paychangu import PayChanguService
from billing.services.payments import (
    finalize_invoice_payment,
    has_recent_pending_payment,
    sync_pending_payments,
)
from hosting.models import HostingAccount
from projects.models import Project
from users.models import CustomerProfile, User

from .forms import CheckoutForm, CustomerProfileForm, UserProfileForm
from .models import SiteSettings, Ticket

# --- Background tasks (Celery) ---------------------------------------------
# NOTE: we deliberately import the *tasks*, not the synchronous service
# functions. Everything that could take seconds (SMTP, Hestia API) now runs
# in a worker.
from core.tasks import send_transactional_email_task
from hosting.tasks import provision_hosting_account_task

logger = logging.getLogger(__name__)


# ==============================================================================
# Helpers
# ==============================================================================

def _human_period(plan: Plan) -> str:
    """
    Convert the verbose ``billing_period`` choice ("monthly" / "yearly")
    into a short, presentation-friendly singular form ("month" / "year").

    Used everywhere we render "Billed {period}." or "MWK X / {period}" so
    the copy stays grammatical regardless of which plan is being shown.
    """
    return 'month' if plan.billing_period == 'monthly' else 'year'


# ==============================================================================
# 1. PUBLIC STOREFRONT
# ==============================================================================

class HomeView(ListView):
    """
    Landing page.

    ``plans`` is consumed by the template to render plan cards and by
    the embedded JSON to render React cards. Site-wide content (hero,
    footer, contact) is injected by the ``site_settings`` context
    processor as ``site``.
    """
    model = Plan
    template_name = 'core/home.html'
    context_object_name = 'plans'

    def get_queryset(self):
        return Plan.objects.filter(is_active=True).order_by('price_mwk')


class CheckoutView(FormView):
    """
    SRS FR-15, FR-63, FR-64 — Checkout flow.

    Handles:
        * Guest checkout  → creates user + profile, logs them in.
        * Logged-in user  → fills any missing profile fields.
        * Always creates  → subscription, project, deposit invoice, then
                            redirects to PayChangu.
    """
    template_name = 'core/checkout.html'
    form_class = CheckoutForm

    def dispatch(self, request, *args, **kwargs):
        # Resolve the plan up front so template/context access is cheap.
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

        # Deposit = 25% of the plan price. Balance is the remaining 75%.
        deposit_amount = self.plan.price_mwk * Decimal('0.25')
        context['plan']           = self.plan
        context['deposit_amount'] = deposit_amount
        context['balance_amount'] = self.plan.price_mwk - deposit_amount

        # Short, human-readable billing period ("month" / "year").
        context['billing_period'] = _human_period(self.plan)
        return context

    @transaction.atomic
    def form_valid(self, form):
        cleaned = form.cleaned_data

        # ------------------------------------------------------------------
        # Step 1 — Resolve / create the customer
        # ------------------------------------------------------------------
        if self.request.user.is_authenticated:
            user = self.request.user

            # Fill missing fields only — never overwrite what the user
            # already set on their profile.
            user.phone_number  = user.phone_number  or cleaned.get('phone_number')
            user.date_of_birth = user.date_of_birth or cleaned.get('date_of_birth')
            user.gender        = user.gender        or cleaned.get('gender')
            user.save()

            profile, _ = CustomerProfile.objects.get_or_create(user=user)
            if not profile.company_name and cleaned.get('company_name'):
                profile.company_name = cleaned['company_name']
                profile.save()
        else:
            # Guest → create the user account and log them in.
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
                self.request,
                user,
                backend='django.contrib.auth.backends.ModelBackend',
            )

        # ------------------------------------------------------------------
        # Step 2 — Subscription, project, deposit invoice
        # ------------------------------------------------------------------
        end_date = timezone.now().date() + timedelta(days=365)
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
        )

        # ------------------------------------------------------------------
        # Step 3 — Redirect to PayChangu checkout
        # ------------------------------------------------------------------
        # initiate_payment is a synchronous HTTP call to the gateway and
        # must remain synchronous: the user is waiting on the redirect.
        checkout_url = PayChanguService.initiate_payment(invoice)
        if checkout_url:
            return redirect(checkout_url)

        messages.error(
            self.request,
            "There was an issue connecting to the payment gateway.",
        )
        return self.form_invalid(form)


class CheckoutSuccessView(TemplateView):
    """
    Post-payment landing page shown before the webhook has finished
    processing (or in case of delayed notification).
    """
    template_name = 'core/checkout_success.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['invoice'] = get_object_or_404(
            Invoice, id=self.kwargs['invoice_id']
        )
        return context


# ==============================================================================
# 2. CUSTOMER DASHBOARD, PROFILE & JSON APIs
# ==============================================================================

class DashboardView(LoginRequiredMixin, TemplateView):
    """Shell template — data is loaded via ``/api/dashboard-data/``."""
    template_name = 'core/dashboard.html'
    login_url = '/accounts/login/'


class DashboardAPIView(LoginRequiredMixin, View):
    """
    SRS FR-32 — Single JSON feed powering the React dashboard.

    Returns
    -------
    user_name           : str
    site                : dict    — cPanel URL, support hours, contact info
    subscription        : dict|None
    hosting_status      : 'pending' | 'provisioning' | 'active'
    hestia_username     : str|None
    usage               : dict    — disk / bandwidth counters
    invoices            : list    — 5 most recent
    next_invoice        : dict|None — earliest unpaid invoice
    project             : dict|None
    tickets             : list    — 5 most recent
    open_tickets_count  : int
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        site = SiteSettings.load()

        # Settle any payment PayChangu has confirmed but the webhook has not
        # delivered yet, so the invoice never shows UNPAID after a paid checkout.
        sync_pending_payments(user)

        # --- Related rows --------------------------------------------------
        subscription    = Subscription.objects.filter(user=user, is_active=True).first()
        hosting_account = HostingAccount.objects.filter(user=user).first()
        project         = Project.objects.filter(customer=user).order_by('-id').first()

        # --- Hosting status ladder -----------------------------------------
        # pending      → user hasn't paid anything yet
        # provisioning → user paid but Hestia account not yet created
        # active       → Hestia account exists
        if hosting_account:
            hosting_status  = 'active'
            hestia_username = hosting_account.hestia_username
        elif Invoice.objects.filter(customer=user, status='paid').exists():
            hosting_status  = 'provisioning'
            hestia_username = None
        else:
            hosting_status  = 'pending'
            hestia_username = None

        # --- Usage (real data — no mocks) ----------------------------------
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

        # --- Invoice slices ------------------------------------------------
        invoices     = Invoice.objects.filter(customer=user).order_by('-issue_date')[:5]
        next_invoice = (
            Invoice.objects
            .filter(customer=user, status='unpaid')
            .order_by('due_date')
            .first()
        )

        # --- Ticket slices -------------------------------------------------
        tickets            = Ticket.objects.filter(user=user).order_by('-created_at')[:5]
        open_tickets_count = Ticket.objects.filter(user=user, status='open').count()

        # --- Response ------------------------------------------------------
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
    """Shell for the profile page (data via ``/api/profile-data/``)."""
    template_name = 'core/profile.html'
    login_url = '/accounts/login/'


class ProfileAPIView(LoginRequiredMixin, View):
    """Read + update personal and billing details."""

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
            return JsonResponse(
                {'success': True, 'message': 'Profile updated successfully.'}
            )

        errors = {}
        if user_form.errors:
            errors['user'] = user_form.errors
        if profile_form.errors:
            errors['profile'] = profile_form.errors
        return JsonResponse({'success': False, 'errors': errors}, status=400)


class PayInvoiceAPIView(LoginRequiredMixin, View):
    """Kick off PayChangu for an unpaid invoice (dashboard 'Pay now')."""

    def post(self, request, *args, **kwargs):
        data = json.loads(request.body)
        invoice = get_object_or_404(
            Invoice, id=data.get('invoice_id'), customer=request.user,
        )
        if invoice.status == 'paid':
            return JsonResponse({'error': 'Invoice already paid'}, status=400)

        checkout_url = PayChanguService.initiate_payment(invoice)
        if checkout_url:
            return JsonResponse({'checkout_url': checkout_url})
        return JsonResponse({'error': 'Gateway error'}, status=500)


class CreateTicketAPIView(LoginRequiredMixin, View):
    """
    SRS FR-34 — Open a support ticket from the dashboard modal.

    Returns the new ticket so the client can update immediately rather
    than waiting on a full dashboard refetch (though it also triggers
    one for consistency).
    """

    def post(self, request, *args, **kwargs):
        data    = json.loads(request.body)
        subject = (data.get('subject') or '').strip()
        message = (data.get('message') or '').strip()

        if not subject or not message:
            return JsonResponse(
                {'error': 'Subject and message are required'}, status=400,
            )

        ticket = Ticket.objects.create(
            user=request.user, subject=subject, message=message,
        )
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
    """Serve the invoice PDF (or receipt for paid invoices)."""

    def get(self, request, invoice_id, *args, **kwargs):
        invoice = get_object_or_404(
            Invoice, id=invoice_id, customer=request.user,
        )
        pdf_buffer = generate_invoice_pdf(invoice)
        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=f"{invoice.invoice_number}.pdf",
            content_type='application/pdf',
        )


# ==============================================================================
# 3. PAYMENT GATEWAY (PayChangu)
# ==============================================================================

@method_decorator(csrf_exempt, name='dispatch')
class PayChanguWebhookView(View):
    """
    PayChangu -> server notification.

    Flow
    ----
        1. Verify the HMAC-SHA256 signature over the raw body.
        2. Read the reference (tx_ref, format "INV-{id}-{attempt}").
        3. finalize_invoice_payment(): verify with PayChangu's verify API,
           mark the invoice paid once, record the Payment, and queue the
           receipt email and provisioning (billing/services/payments.py).
        4. Return 200.

    The same function also runs from the browser callback and when the
    dashboard loads, so a late or missed webhook can no longer leave a paid
    invoice showing UNPAID.
    """

    def post(self, request, *args, **kwargs):
        logger.info(
            "PayChangu Webhook Payload: %s",
            request.body.decode('utf-8', errors='replace'),
        )

        # --- 1. Signature check -------------------------------------------
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

        # --- Parse body ---------------------------------------------------
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        status    = payload.get('status')
        tx_ref    = payload.get('reference') or payload.get('tx_ref')
        charge_id = payload.get('charge_id')

        if status != 'success':
            return HttpResponse(status=200)  # Ignore non-success events.

        if not tx_ref:
            logger.warning("Webhook: Missing tx_ref/reference in payload.")
            return HttpResponse(status=200)

        # --- 2. Verify with PayChangu and settle the invoice --------------
        invoice, newly_paid = finalize_invoice_payment(tx_ref)
        if invoice is None:
            logger.warning(
                "Webhook: %s could not be verified or settled; the dashboard "
                "sync will retry.", tx_ref,
            )
        elif newly_paid:
            logger.info("Webhook: %s paid, follow-up tasks queued.", invoice.invoice_number)
        else:
            logger.info("Webhook: %s already paid - ignoring.", invoice.invoice_number)
        return HttpResponse(status=200)


class PaymentCallbackView(View):
    """
    Browser redirect after a successful payment at the gateway.

    PayChangu adds tx_ref and status to the URL. We verify the payment with
    PayChangu right here, so the dashboard is already correct when it opens
    even if the webhook is a few seconds late. finalize_invoice_payment is
    idempotent, so running next to the webhook is safe, and it only marks an
    invoice paid when the gateway itself confirms the payment.
    """
    def get(self, request, *args, **kwargs):
        tx_ref = request.GET.get('tx_ref')
        if tx_ref and request.GET.get('status', 'success') == 'success':
            try:
                finalize_invoice_payment(tx_ref)
            except Exception:
                logger.exception("Callback: could not settle %s", tx_ref)
        return redirect('/dashboard/?payment=success')


class PaymentReturnView(View):
    """Browser redirect after a failed/cancelled payment."""
    def get(self, request, *args, **kwargs):
        return redirect('/dashboard/?payment=failed')