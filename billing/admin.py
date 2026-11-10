from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Plan, Subscription, Invoice, Payment


class PaymentInline(TabularInline):
    model = Payment
    extra = 0
    tab = True
    readonly_fields = ('gateway_reference', 'amount_mwk', 'status', 'payment_method', 'created_at')
    can_delete = False


@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    list_display = ('name', 'audience', 'price_mwk', 'billing_period', 'disk_space_gb', 'email_accounts', 'is_active')
    list_filter = ('audience', 'billing_period', 'is_active')
    search_fields = ('name',)
    list_editable = ('is_active',)


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    """
    Dunning-aware subscription admin. The "Days Left" and "At Risk?"
    columns are the fastest way for staff to spot accounts that need
    a nudge before they get auto-suspended.
    """
    list_display = (
        'user', 'plan', 'end_date', 'days_left_display',
        'status', 'at_risk_display', 'is_active',
    )
    list_filter  = ('plan', 'status', 'is_active')
    search_fields = ('user__email', 'plan__name')
    autocomplete_fields = ('user', 'plan', 'renewal_invoice')

    # All flags are set by the cron — mark read-only so nobody
    # accidentally skips an email by toggling one.
    readonly_fields = (
        'suspended_at', 'scheduled_deletion_at', 'cancelled_at',
        'reminder_30d_sent', 'reminder_7d_sent', 'reminder_3d_sent',
        'reminder_1d_sent', 'expired_notice_sent',
        'suspension_warning_sent', 'final_warning_sent',
    )

    actions = ['trigger_dunning_check']

    @display(description='Days Left')
    def days_left_display(self, obj):
        d = obj.days_until_expiry
        return f"{abs(d)}d overdue" if d < 0 else f"{d}d"

    @display(description='At Risk?', label=True)
    def at_risk_display(self, obj):
        return "⚠ At risk" if obj.is_at_risk else "—"

    @admin.action(description='Trigger dunning check on selected + all subscriptions')
    def trigger_dunning_check(self, request, queryset):
        from billing.tasks import check_subscription_lifecycle
        check_subscription_lifecycle.delay()
        self.message_user(
            request,
            f"Dunning check queued. It will scan every active subscription.",
        )


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = ('invoice_number', 'customer', 'total_mwk', 'invoice_type', 'status', 'due_date', 'is_deposit')
    list_filter  = ('status', 'invoice_type', 'is_deposit')
    search_fields = ('invoice_number', 'customer__email')
    readonly_fields = ('invoice_number', 'issue_date')
    inlines = [PaymentInline]
    autocomplete_fields = ('customer', 'subscription')

    actions = ['mark_as_paid']

    @admin.action(description='Mark selected invoices as PAID (Manual)')
    def mark_as_paid(self, request, queryset):
        updated = queryset.update(status='paid')
        for invoice in queryset:
            if not invoice.payment_set.exists():
                Payment.objects.create(
                    invoice=invoice,
                    amount_mwk=invoice.total_mwk,
                    gateway_reference=f"MANUAL-{invoice.id}",
                    status='success',
                    payment_method='manual',
                )
        self.message_user(
            request,
            f"{updated} invoice(s) marked PAID. "
            f"NOTE: renewal invoices marked this way will NOT auto-extend the "
            f"subscription — extend it manually from the Subscription admin.",
        )


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = ('gateway_reference', 'invoice', 'amount_mwk', 'status', 'payment_method', 'created_at')
    list_filter = ('status', 'payment_method')
    search_fields = ('gateway_reference', 'invoice__invoice_number')
    readonly_fields = ('gateway_reference', 'created_at')