"""
Billing domain models.

Contents:
    - Plan          : sellable hosting packages (SRS FR-6, FR-7, FR-62).
    - Subscription  : user ↔ plan link with a validity window (SRS FR-9, FR-65).
    - Invoice       : billable document (SRS FR-17, FR-64, FR-78).
    - Payment       : PayChangu transaction record (SRS FR-20).
"""
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from users.models import User


class Plan(models.Model):
    """
    A sellable hosting package.

    SRS FR-6, FR-7: Plans are configurable data (price, limits, features).
    SRS FR-62: Starter, Business, Enterprise packages are plan data — not code.
    """
    name = models.CharField(_("name"), max_length=100)
    audience = models.CharField(
        _("audience"),
        max_length=50,
        help_text=_("e.g. student, developer, business"),
    )
    price_mwk = models.DecimalField(
        _("price (MWK)"),
        max_digits=12,
        decimal_places=2,
    )
    billing_period = models.CharField(
        _("billing period"),
        max_length=20,
        choices=(('monthly', _('Monthly')), ('yearly', _('Yearly'))),
        default='yearly',
    )

    # --- Limits (SRS FR-7) -------------------------------------------------
    disk_space_gb = models.PositiveIntegerField(_("disk space (GB)"), default=2)
    bandwidth_gb = models.PositiveIntegerField(_("bandwidth (GB)"), default=50)
    email_accounts = models.PositiveIntegerField(_("email accounts"), default=0)
    ssh_access = models.BooleanField(_("SSH access"), default=False)

    # --- Feature flags -----------------------------------------------------
    backup_frequency = models.CharField(
        _("backup frequency"),
        max_length=20,
        choices=(
            ('daily',  _('Daily')),
            ('weekly', _('Weekly')),
            ('none',   _('None')),
        ),
        default='weekly',
        help_text=_("Controls the 'backups' bullet shown on the home page."),
    )

    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Inactive plans are hidden from the public site."),
    )

    class Meta:
        verbose_name = _("Plan")
        verbose_name_plural = _("Plans")
        ordering = ['price_mwk']

    def __str__(self):
        return f"{self.name} (MWK {self.price_mwk:,.0f})"


class Subscription(models.Model):
    """Links a user to a plan for a period. SRS FR-9, FR-65."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name=_("user"))
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, verbose_name=_("plan"))
    start_date = models.DateField(_("start date"), auto_now_add=True)
    end_date = models.DateField(_("end date"))
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.user.email} - {self.plan.name} (ends {self.end_date})"


class Invoice(models.Model):
    """
    Invoice generated for a subscription or project milestone.

    SRS FR-17, FR-78: PDF invoices with Pritech details, customer details,
    tax lines.
    SRS FR-64: 25% deposit invoice for managed packages.
    """
    STATUS_CHOICES = (
        ('draft',  _('Draft')),
        ('unpaid', _('Unpaid')),
        ('paid',   _('Paid')),
        ('void',   _('Void')),
    )

    customer = models.ForeignKey(
        User, on_delete=models.PROTECT, verbose_name=_("customer")
    )
    invoice_number = models.CharField(
        _("invoice number"),
        max_length=50,
        unique=True,
        help_text=_("Auto-generated if left blank."),
    )
    issue_date = models.DateField(_("issue date"), auto_now_add=True)
    due_date = models.DateField(_("due date"))
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=STATUS_CHOICES,
        default='unpaid',
    )
    subtotal_mwk = models.DecimalField(_("subtotal (MWK)"), max_digits=12, decimal_places=2)
    tax_mwk = models.DecimalField(_("tax (MWK)"), max_digits=12, decimal_places=2, default=0)
    total_mwk = models.DecimalField(_("total (MWK)"), max_digits=12, decimal_places=2)

    is_deposit = models.BooleanField(
        _("is deposit"),
        default=False,
        help_text=_("True if this is the 25% deposit invoice (SRS FR-64)."),
    )

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("subscription"),
        help_text=_("The subscription this invoice is billing for, if any."),
    )

    class Meta:
        verbose_name = _("Invoice")
        verbose_name_plural = _("Invoices")
        ordering = ['-issue_date']

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = f"Pritech-INV-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} - {self.customer.email} ({self.get_status_display()})"


class Payment(models.Model):
    """
    Records a payment against an invoice.

    SRS FR-20: Payments recorded automatically after PayChangu verification.
    """
    invoice = models.ForeignKey(
        Invoice, on_delete=models.PROTECT, verbose_name=_("invoice")
    )
    amount_mwk = models.DecimalField(_("amount (MWK)"), max_digits=12, decimal_places=2)
    gateway_reference = models.CharField(
        _("gateway reference"),
        max_length=100,
        unique=True,
        help_text=_("PayChangu transaction reference"),
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        default='pending',
        choices=(
            ('pending', _('Pending')),
            ('success', _('Success')),
            ('failed',  _('Failed')),
        ),
    )
    payment_method = models.CharField(
        _("payment method"),
        max_length=50,
        choices=(
            ('airtel', _('Airtel Money')),
            ('tnm',    _('TNM Mpamba')),
            ('card',   _('Card')),
        ),
    )
    created_at = models.DateTimeField(_("created at"), auto_now_add=True)

    class Meta:
        verbose_name = _("Payment")
        verbose_name_plural = _("Payments")
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.gateway_reference} - {self.amount_mwk} MWK ({self.status})"