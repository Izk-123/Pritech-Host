"""
Billing domain models.

Contents:
    - Plan          : sellable hosting packages (SRS FR-6, FR-7, FR-62).
    - Subscription  : user ↔ plan link + renewal/dunning state (SRS FR-9, FR-18, FR-19, FR-65).
    - Invoice       : billable document; supports renewal periods (SRS FR-17, FR-64, FR-78).
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

    # --- Convenience -------------------------------------------------------
    @property
    def period_days(self) -> int:
        """Length of one billing cycle in days."""
        return 30 if self.billing_period == 'monthly' else 365


class Subscription(models.Model):
    """
    Links a user to a plan for a period.

    Carries the state machine for the automated renewal + dunning
    lifecycle (SRS FR-18, FR-19). The dunning flags are booleans that
    are set once per cycle and reset only when a renewal payment
    succeeds (see ``reset_dunning_flags()``).

    Lifecycle
    ---------
        active    → default
        expired   → end_date passed, still in grace period
        suspended → 7+ days past expiry; Hestia account suspended
        cancelled → 45+ days past expiry; account deleted, sub archived
    """
    STATUS_CHOICES = (
        ('active',    _('Active')),
        ('expired',   _('Expired')),
        ('suspended', _('Suspended')),
        ('cancelled', _('Cancelled')),
    )

    user        = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name=_("user"))
    plan        = models.ForeignKey(Plan, on_delete=models.PROTECT, verbose_name=_("plan"))
    start_date  = models.DateField(_("start date"), auto_now_add=True)
    end_date    = models.DateField(_("end date"))
    is_active   = models.BooleanField(_("active"), default=True)
    status      = models.CharField(
        _("status"), max_length=20,
        choices=STATUS_CHOICES, default='active',
    )

    # --- Renewal tracking --------------------------------------------------
    # The auto-generated invoice for the next billing period. Cleared on
    # successful payment so the next cycle can start from scratch.
    renewal_invoice = models.ForeignKey(
        'Invoice', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='renewal_for',
        help_text=_("Auto-generated invoice for the next billing period."),
    )

    # --- Dunning state flags ----------------------------------------------
    # Each flag is set once per cycle and cleared by reset_dunning_flags()
    # on renewal. This lets the daily cron skip stages already handled.
    reminder_30d_sent       = models.BooleanField(default=False)
    reminder_7d_sent        = models.BooleanField(default=False)
    reminder_3d_sent        = models.BooleanField(default=False)
    reminder_1d_sent        = models.BooleanField(default=False)
    expired_notice_sent     = models.BooleanField(default=False)
    suspension_warning_sent = models.BooleanField(default=False)
    final_warning_sent      = models.BooleanField(default=False)

    # --- Timestamps for staff visibility / audit ---------------------------
    suspended_at          = models.DateTimeField(null=True, blank=True)
    scheduled_deletion_at = models.DateTimeField(null=True, blank=True)
    cancelled_at          = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.user.email} - {self.plan.name} (ends {self.end_date})"

    # --- Convenience properties -------------------------------------------
    @property
    def days_until_expiry(self) -> int:
        """Positive = days before expiry. Negative = days overdue."""
        return (self.end_date - timezone.localdate()).days

    @property
    def is_expired(self) -> bool:
        return self.days_until_expiry < 0

    @property
    def is_in_grace_period(self) -> bool:
        """1-7 days past expiry — still online, but warned."""
        return -7 <= self.days_until_expiry < 0

    @property
    def is_at_risk(self) -> bool:
        """Staff dashboard indicator — needs attention within 30 days."""
        return (
            self.is_active
            and self.status == 'active'
            and 0 <= self.days_until_expiry <= 30
            and self.renewal_invoice is not None
            and self.renewal_invoice.status != 'paid'
        )

    # --- Lifecycle helpers ------------------------------------------------
    def reset_dunning_flags(self):
        """
        Called when a renewal payment succeeds — restart the clock.

        Extends are done by the caller *before* calling this method.
        This method only clears the per-cycle state so a fresh dunning
        cycle can begin on the (new) end_date.
        """
        self.reminder_30d_sent       = False
        self.reminder_7d_sent        = False
        self.reminder_3d_sent        = False
        self.reminder_1d_sent        = False
        self.expired_notice_sent     = False
        self.suspension_warning_sent = False
        self.final_warning_sent      = False

        self.renewal_invoice          = None
        self.suspended_at             = None
        self.scheduled_deletion_at    = None
        self.cancelled_at             = None
        self.status                   = 'active'
        self.is_active                = True


class Invoice(models.Model):
    """
    Invoice for a subscription, deposit, balance, or renewal.

    SRS FR-17, FR-78: PDF invoices.
    SRS FR-64: 25% deposit invoice for managed packages.
    SRS FR-18/19: annual renewal invoices generated at day -14.
    """
    STATUS_CHOICES = (
        ('draft',  _('Draft')),
        ('unpaid', _('Unpaid')),
        ('paid',   _('Paid')),
        ('void',   _('Void')),
    )
    TYPE_CHOICES = (
        ('deposit',   _('Deposit (25%)')),
        ('balance',   _('Balance payment')),
        ('renewal',   _('Renewal')),
        ('milestone', _('Milestone')),
    )

    customer       = models.ForeignKey(User, on_delete=models.PROTECT, verbose_name=_("customer"))
    invoice_number = models.CharField(
        _("invoice number"), max_length=50, unique=True,
        help_text=_("Auto-generated if left blank."),
    )
    issue_date     = models.DateField(_("issue date"), auto_now_add=True)
    due_date       = models.DateField(_("due date"))
    status         = models.CharField(
        _("status"), max_length=20, choices=STATUS_CHOICES, default='unpaid',
    )
    invoice_type   = models.CharField(
        _("invoice type"), max_length=20, choices=TYPE_CHOICES,
        default='milestone',
        help_text=_("'deposit' for the 25% checkout, 'renewal' for auto-generated renewals."),
    )

    subtotal_mwk = models.DecimalField(_("subtotal (MWK)"), max_digits=12, decimal_places=2)
    tax_mwk      = models.DecimalField(_("tax (MWK)"), max_digits=12, decimal_places=2, default=0)
    total_mwk    = models.DecimalField(_("total (MWK)"), max_digits=12, decimal_places=2)

    is_deposit = models.BooleanField(
        _("is deposit"), default=False,
        help_text=_("True if this is the 25% deposit invoice (SRS FR-64)."),
    )

    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name=_("subscription"),
        help_text=_("The subscription this invoice is billing for, if any."),
    )

    # --- Renewal period (only populated for invoice_type='renewal') --------
    renewal_period_start = models.DateField(null=True, blank=True)
    renewal_period_end   = models.DateField(null=True, blank=True)

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
    invoice           = models.ForeignKey(Invoice, on_delete=models.PROTECT, verbose_name=_("invoice"))
    amount_mwk        = models.DecimalField(_("amount (MWK)"), max_digits=12, decimal_places=2)
    gateway_reference = models.CharField(
        _("gateway reference"), max_length=100, unique=True,
        help_text=_("PayChangu transaction reference"),
    )
    status = models.CharField(
        _("status"), max_length=20, default='pending',
        choices=(('pending', _('Pending')), ('success', _('Success')), ('failed', _('Failed'))),
    )
    payment_method = models.CharField(
        _("payment method"), max_length=50,
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