import json
import hmac
import hashlib
import logging
from decimal import Decimal
from datetime import timedelta

from django.views.generic import ListView, FormView, TemplateView, View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.db import transaction
from django.http import HttpResponse, FileResponse, JsonResponse
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings

# Local App Imports
from billing.models import Plan, Invoice, Subscription, Payment
from billing.services.paychangu import PayChanguService
from billing.services.invoice_pdf import generate_invoice_pdf
from projects.models import Project
from hosting.models import HostingAccount
from hosting.services.provisioning import provision_hosting_account
from users.models import User, CustomerProfile
from .models import Ticket
from .forms import CheckoutForm, UserProfileForm, CustomerProfileForm
from .services.email_utils import send_payment_confirmation

logger = logging.getLogger(__name__)


# ==============================================================================
# PUBLIC STOREFRONT VIEWS
# ==============================================================================

class HomeView(ListView):
    """SRS UI: Main Landing Page."""
    model = Plan
    template_name = 'core/home.html'
    context_object_name = 'plans'
    
    def get_queryset(self):
        return Plan.objects.filter(is_active=True).order_by('price_mwk')


class CheckoutView(FormView):
    """
    SRS FR-15, FR-63, FR-64: Handles the checkout process.
    Supports both guest checkout and logged-in users purchasing a plan.
    """
    template_name = 'core/checkout.html'
    form_class = CheckoutForm

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(Plan, id=self.kwargs.get('plan_id'), is_active=True)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['current_user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['plan'] = self.plan
        deposit_amount = self.plan.price_mwk * Decimal('0.25')
        context['deposit_amount'] = deposit_amount
        context['balance_amount'] = self.plan.price_mwk - deposit_amount
        return context

    @transaction.atomic
    def form_valid(self, form):
        cleaned_data = form.cleaned_data
        
        # Handle User Creation / Updating
        if self.request.user.is_authenticated:
            user = self.request.user
            user.phone_number = user.phone_number or cleaned_data.get('phone_number')
            user.date_of_birth = user.date_of_birth or cleaned_data.get('date_of_birth')
            user.gender = user.gender or cleaned_data.get('gender')
            user.save()
            
            profile, _ = CustomerProfile.objects.get_or_create(user=user)
            if not profile.company_name and cleaned_data.get('company_name'):
                profile.company_name = cleaned_data.get('company_name')
                profile.save()
        else:
            user = User.objects.create(
                username=cleaned_data['email'],
                email=cleaned_data['email'],
                first_name=cleaned_data['first_name'],
                last_name=cleaned_data.get('last_name', ''),
                phone_number=cleaned_data['phone_number'],
                date_of_birth=cleaned_data['date_of_birth'],
                gender=cleaned_data['gender'],
            )
            user.set_unusable_password()
            user.save()
            
            CustomerProfile.objects.create(
                user=user,
                company_name=cleaned_data.get('company_name', '')
            )
            login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')

        # Create Subscription
        end_date = timezone.now().date() + timedelta(days=365)
        subscription = Subscription.objects.create(
            user=user,
            plan=self.plan,
            end_date=end_date
        )

        # Create Project
        Project.objects.create(
            customer=user,
            subscription=subscription,
            requested_domain=cleaned_data.get('requested_domain', ''),
            total_price_mwk=self.plan.price_mwk, 
            stage='deposit'
        )

        # Create Deposit Invoice
        deposit_amount = self.plan.price_mwk * Decimal('0.25')
        invoice = Invoice.objects.create(
            customer=user,
            due_date=timezone.now().date(),
            subtotal_mwk=deposit_amount,
            total_mwk=deposit_amount,
            is_deposit=True
        )

        # Initiate Payment
        checkout_url = PayChanguService.initiate_payment(invoice)
        
        if checkout_url:
            return redirect(checkout_url)
        else:
            messages.error(self.request, "There was an issue connecting to the payment gateway.")
            return self.form_invalid(form)


class CheckoutSuccessView(TemplateView):
    template_name = 'core/checkout_success.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['invoice'] = get_object_or_404(Invoice, id=self.kwargs['invoice_id'])
        return context


# ==============================================================================
# CUSTOMER DASHBOARD, PROFILE & API VIEWS
# ==============================================================================

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'
    login_url = '/accounts/login/'


class DashboardAPIView(LoginRequiredMixin, View):
    """
    SRS FR-32: Provides dynamic JSON data for the React dashboard.
    Includes subscription, usage, invoices, project stage, and hosting status.
    """
    def get(self, request, *args, **kwargs):
        user = request.user
        subscription = Subscription.objects.filter(user=user, is_active=True).first()
        hosting_account = HostingAccount.objects.filter(user=user).first()
        project = Project.objects.filter(customer=user).order_by('-id').first()
        
        # Determine Hosting Status
        hosting_status = 'pending'
        hestia_username = None
        
        if hosting_account:
            hosting_status = 'active'
            hestia_username = hosting_account.hestia_username
        else:
            if Invoice.objects.filter(customer=user, status='paid').exists():
                hosting_status = 'provisioning'

        # Build Usage Data
        usage_data = {
            'disk_used': 0, 'disk_limit': 0, 'disk_pct': 0,
            'bw_used': 0, 'bw_limit': 0, 'bw_pct': 0,
            'email_used': 0, 'email_limit': 0, 'email_pct': 0,
        }

        if hosting_account and subscription:
            usage_data['disk_limit'] = subscription.plan.disk_space_gb
            usage_data['bw_limit'] = subscription.plan.bandwidth_gb
            usage_data['email_limit'] = subscription.plan.email_accounts
            
            usage_data['disk_used'] = round(hosting_account.disk_used_mb / 1024, 1)
            usage_data['bw_used'] = round(hosting_account.bandwidth_used_mb / 1024, 1)
            usage_data['email_used'] = 4 # Mock
            
            if usage_data['disk_limit'] > 0:
                usage_data['disk_pct'] = min(100, int((usage_data['disk_used'] / usage_data['disk_limit']) * 100))
            if usage_data['bw_limit'] > 0:
                usage_data['bw_pct'] = min(100, int((usage_data['bw_used'] / usage_data['bw_limit']) * 100))

        # Fetch Invoices
        invoices = Invoice.objects.filter(customer=user).order_by('-issue_date')[:5]
        next_invoice = Invoice.objects.filter(customer=user, status='unpaid').order_by('due_date').first()

        return JsonResponse({
            'user_name': user.first_name or 'Customer',
            'subscription': {
                'plan_name': subscription.plan.name,
                'price': float(subscription.plan.price_mwk),
                'renewal_date': subscription.end_date.strftime('%d %b %Y'),
            } if subscription else None,
            'hosting_status': hosting_status,
            'hestia_username': hestia_username,
            'usage': usage_data,
            'invoices': [
                {
                    'id': inv.id,
                    'number': inv.invoice_number,
                    'date': inv.issue_date.strftime('%d %b %Y'),
                    'amount': float(inv.total_mwk),
                    'status': inv.status.upper(),
                } for inv in invoices
            ],
            'next_invoice': {
                'id': next_invoice.id,
                'number': next_invoice.invoice_number,
                'amount': float(next_invoice.total_mwk),
                'due_date': next_invoice.due_date.strftime('%d %b %Y'),
            } if next_invoice else None,
            'project': {
                'stage': project.stage,
                'stage_display': project.get_stage_display(),
                'domain': project.requested_domain
            } if project else None,
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
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'phone_number': user.phone_number,
                'date_of_birth': user.date_of_birth.strftime('%Y-%m-%d') if user.date_of_birth else '',
                'gender': user.gender,
            },
            'profile': {
                'company_name': profile.company_name,
                'tin_vat': profile.tin_vat,
                'address': profile.address,
            }
        })

    def post(self, request, *args, **kwargs):
        user = request.user
        profile, _ = CustomerProfile.objects.get_or_create(user=user)
        data = json.loads(request.body)
        
        user_form = UserProfileForm(data=data.get('user'), instance=user)
        profile_form = CustomerProfileForm(data=data.get('profile'), instance=profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            return JsonResponse({'success': True, 'message': 'Profile updated successfully.'})
        else:
            errors = {}
            if user_form.errors: errors['user'] = user_form.errors
            if profile_form.errors: errors['profile'] = profile_form.errors
            return JsonResponse({'success': False, 'errors': errors}, status=400)


class PayInvoiceAPIView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        data = json.loads(request.body)
        invoice_id = data.get('invoice_id')
        
        invoice = get_object_or_404(Invoice, id=invoice_id, customer=request.user)
        if invoice.status == 'paid':
            return JsonResponse({'error': 'Invoice already paid'}, status=400)
            
        checkout_url = PayChanguService.initiate_payment(invoice)
        if checkout_url:
            return JsonResponse({'checkout_url': checkout_url})
        return JsonResponse({'error': 'Gateway error'}, status=500)


class CreateTicketAPIView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        data = json.loads(request.body)
        subject = data.get('subject')
        message = data.get('message')
        
        if not subject or not message:
            return JsonResponse({'error': 'Subject and message are required'}, status=400)
            
        Ticket.objects.create(user=request.user, subject=subject, message=message)
        return JsonResponse({'success': True})


class InvoicePDFView(LoginRequiredMixin, View):
    def get(self, request, invoice_id, *args, **kwargs):
        invoice = get_object_or_404(Invoice, id=invoice_id, customer=request.user)
        pdf_buffer = generate_invoice_pdf(invoice)
        return FileResponse(pdf_buffer, as_attachment=True, filename=f"{invoice.invoice_number}.pdf", content_type='application/pdf')


# ==============================================================================
# PAYMENT GATEWAY & WEBHOOK VIEWS
# ==============================================================================

@method_decorator(csrf_exempt, name='dispatch')
class PayChanguWebhookView(View):
    def post(self, request, *args, **kwargs):
        signature = request.headers.get('Signature')
        if not signature: return HttpResponse(status=400)

        computed_signature = hmac.new(
            settings.PAYCHANGU_WEBHOOK_SECRET.encode(), request.body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_signature, signature): return HttpResponse(status=401)

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return HttpResponse(status=400)

        if payload.get('status') == 'success':
            charge_id = payload.get('charge_id')
            invoice_id = payload.get('meta', {}).get('invoice_id')
            
            if not invoice_id or not charge_id: return HttpResponse(status=200)

            try:
                invoice = Invoice.objects.get(id=invoice_id)
                if invoice.status == 'paid': return HttpResponse(status=200)

                if PayChanguService.verify_payment(charge_id):
                    invoice.status = 'paid'
                    invoice.save()

                    Payment.objects.create(
                        invoice=invoice, amount_mwk=invoice.total_mwk, gateway_reference=charge_id,
                        status='success', payment_method='airtel' 
                    )
                    
                    # 1. Send Payment Receipt Email
                    send_payment_confirmation(invoice)

                    # 2. Trigger Automated Provisioning (only for the initial deposit)
                    if invoice.is_deposit:
                        provision_hosting_account(invoice)
                    
                    logger.info(f"Invoice {invoice.invoice_number} paid, receipt sent, and provisioning triggered.")
            except Exception as e:
                logger.error(f"Webhook error: {e}")
                return HttpResponse(status=500)

        return HttpResponse(status=200)


class PaymentCallbackView(View):
    """Redirects to dashboard with success flag."""
    def get(self, request, *args, **kwargs):
        return redirect('/dashboard/?payment=success')


class PaymentReturnView(View):
    """Redirects to dashboard with failure flag."""
    def get(self, request, *args, **kwargs):
        return redirect('/dashboard/?payment=failed')