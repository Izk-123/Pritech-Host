from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import Plan, Subscription, Invoice, Payment


# --- Inlines ---

class PaymentInline(TabularInline):
    """Shows payments made against an invoice."""
    model = Payment
    extra = 0
    tab = True
    readonly_fields = ('gateway_reference', 'amount_mwk', 'status', 'payment_method', 'created_at')
    can_delete = False


# ❌ REMOVED: InvoiceInline
# Invoice does not have a ForeignKey to Subscription, so it cannot be
# embedded inside SubscriptionAdmin. Use the standalone Invoice admin
# (with its search field) to find invoices per customer instead.

class InvoiceInline(TabularInline):
    model = Invoice
    fk_name = 'subscription'   # Explicit FK to use for the inline
    extra = 0
    tab = True
    fields = ('invoice_number', 'total_mwk', 'status', 'due_date', 'is_deposit')
    readonly_fields = ('invoice_number',)
    can_delete = False


# --- Admin Classes ---

@admin.register(Plan)
class PlanAdmin(ModelAdmin):
    """SRS FR-6, FR-7, FR-62: Manage sellable plans."""
    list_display = ('name', 'audience', 'price_mwk', 'billing_period', 'disk_space_gb', 'email_accounts', 'is_active')
    list_filter = ('audience', 'billing_period', 'is_active')
    search_fields = ('name',)
    list_editable = ('is_active',)

    @display(description='Price (MWK)', ordering='price_mwk')
    def price_display(self, obj):
        return f"MWK {obj.price_mwk:,.0f}"


@admin.register(Subscription)
class SubscriptionAdmin(ModelAdmin):
    list_display = ('user', 'plan', 'start_date', 'end_date', 'is_active')
    list_filter = ('plan', 'is_active')
    search_fields = ('user__email', 'plan__name')
    autocomplete_fields = ('user', 'plan')
    # Inline removed because Invoice has no FK to Subscription.



@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    """
    SRS FR-17, FR-78: Invoice management.
    SRS FR-64: Deposit invoices are flagged.
    """
    list_display = ('invoice_number', 'customer', 'total_mwk', 'status', 'due_date', 'is_deposit')
    list_filter = ('status', 'is_deposit')
    search_fields = ('invoice_number', 'customer__email')
    readonly_fields = ('invoice_number', 'issue_date')
    inlines = [PaymentInline]
    autocomplete_fields = ('customer',)
    
    actions = ['mark_as_paid']
    
    @admin.action(description='Mark selected invoices as PAID (Testing)')
    def mark_as_paid(self, request, queryset):
        updated = queryset.update(status='paid')
        # Create dummy payment records for the receipts
        for invoice in queryset:
            if not invoice.payment_set.exists():
                Payment.objects.create(
                    invoice=invoice,
                    amount_mwk=invoice.total_mwk,
                    gateway_reference=f"MANUAL-{invoice.id}",
                    status='success',
                    payment_method='manual'
                )
        self.message_user(request, f"{updated} invoice(s) successfully marked as PAID.")

    @display(description='Amount (MWK)', ordering='total_mwk')
    def amount_display(self, obj):
        return f"MWK {obj.total_mwk:,.0f}"


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = ('gateway_reference', 'invoice', 'amount_mwk', 'status', 'payment_method', 'created_at')
    list_filter = ('status', 'payment_method')
    search_fields = ('gateway_reference', 'invoice__invoice_number')
    readonly_fields = ('gateway_reference', 'created_at')