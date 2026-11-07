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
    list_display = ('user', 'plan', 'start_date', 'end_date', 'is_active')
    list_filter = ('plan', 'is_active')
    search_fields = ('user__email', 'plan__name')
    autocomplete_fields = ('user', 'plan')


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    list_display = ('invoice_number', 'customer', 'total_mwk', 'status', 'due_date', 'is_deposit')
    list_filter = ('status', 'is_deposit')
    search_fields = ('invoice_number', 'customer__email')
    readonly_fields = ('invoice_number', 'issue_date')
    inlines = [PaymentInline]
    autocomplete_fields = ('customer',)
    
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
                    payment_method='manual'
                )
        self.message_user(request, f"{updated} invoice(s) successfully marked as PAID.")


@admin.register(Payment)
class PaymentAdmin(ModelAdmin):
    list_display = ('gateway_reference', 'invoice', 'amount_mwk', 'status', 'payment_method', 'created_at')
    list_filter = ('status', 'payment_method')
    search_fields = ('gateway_reference', 'invoice__invoice_number')
    readonly_fields = ('gateway_reference', 'created_at')