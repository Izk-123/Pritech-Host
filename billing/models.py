"""
Billing domain models (SRS v1.5 — includes FR-85 to FR-95).

Payment schedules (FR-85 to FR-95)
----------------------------------
Each plan has a `payment_schedule` (list of instalments). At order time
the schedule is *snapshotted* onto the subscription, so later price /
schedule edits don't touch existing customers (FR-86).

Each instalment entry has:
    pct       : percentage of the contract total (e.g. 25, 50, 100)
    label     : human name ("Deposit", "Design approval", "Launch balance")
    trigger   : 'checkout' | 'content' | 'design_approval' | 'launch'

Lifecycle
---------
    1. Checkout → SubscriptionInstalment rows created (status='scheduled')
    2. First instalment (trigger='checkout') immediately issued as Invoice
    3. Payment verified → instalment.status='paid'
    4. When project reaches a milestone stage, the matching instalment
       is issued (Invoice with due_date) and the customer is notified
    5. Reminders go ONLY to issued-and-unpaid instalment invoices

Renewals (FR-91) are a separate flow. They produce a single renewal
invoice for the next year's price and do NOT touch instalments or
contract balance.
"""
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from users.models import User


# ---------------------------------------------------------------------------
# Default payment schedules. Admins can override per-plan by editing
# `payment_schedule` on the Plan in /admin/. Must sum to 100%.
# ---------------------------------------------------------------------------
DEFAULT_MONTHLY_SCHEDULE = [
    {"pct": 100, "label": "Full payment", "trigger": "checkout"},
]

DEFAULT_YEARLY_SCHEDULE = [
    {"pct": 25, "label": "Deposit",  "trigger": "checkout"},
    {"pct": 75, "label": "Balance",  "trigger": "launch"},
]


class Plan(models.Model):
    """Sellable hosting package. SRS FR-6, FR-7, FR-62, FR-85."""
    name = models.CharField(_("name"), max_length=100)
    audience = models.CharField(
        _("audience"),
        max_length=50,
        help_text=_("e.g. student, developer, business"),
    )
    price_mwk = models.DecimalField(
        _("price (MWK)"),
        max_digits=12, decimal_places=2,
    )
    billing_period = models.CharField(
        _("billing period"),
        max_length=20,
        choices=(('monthly', _('Monthly')), ('yearly', _('Yearly'))),
        default='yearly',
    )

    # --- Limits (FR-7) ----------------------------------------------------
    disk_space_gb   = models.PositiveIntegerField(_("disk space (GB)"), default=2)
    bandwidth_gb    = models.PositiveIntegerField(_("bandwidth (GB)"), default=50)
    email_accounts  = models.PositiveIntegerField(_("email accounts"), default=0)
    ssh_access      = models.BooleanField(_("SSH access"), default=False)

    backup_frequency = models.CharField(
        _("backup frequency"),
        max_length=20,
        choices=(('daily', _('Daily')), ('weekly', _('Weekly')), ('none', _('None'))),
        default='weekly',
    )

    # --- FR-85: payment schedule as data ----------------------------------
    # Empty list → fall back to `effective_schedule()` default for this
    # billing period. Populating this field lets admins sell e.g. a
    # Business plan as "25% deposit + 50% at design approval + 25% at launch".
    payment_schedule = models.JSONField(
        _("payment schedule"),
        default=list,
        blank=True,
        help_text=_(
            'List of instalments: '
            '[{"pct": 25, "label": "Deposit", "trigger": "checkout"}, ...]. '
            'Triggers: checkout, content, design_approval, launch. '
            'Leave empty to use the default for this billing period.'
        ),
    )

    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        verbose_name = _("Plan")
        verbose_name_plural = _("Plans")
        ordering = ['price_mwk']

    def __str__(self):
        return f"{self.name} (MWK {self.price_mwk:,.0f})"

    # --- Convenience ------------------------------------------------------
    @property
    def period_days(self) -> int:
        return 30 if self.billing_period == 'monthly' else 365

    @property
    def is_monthly(self) -> bool:
        return self.billing_period == 'monthly'

    def effective_schedule(self):
        """Return the schedule to use — custom or default for period."""
        if self.payment_schedule:
            return self.payment_schedule
        return (
            DEFAULT_MONTHLY_SCHEDULE if self.is_monthly
            else DEFAULT_YEARLY_SCHEDULE
        )

    def clean(self):
        """Validate that pct values sum to 100 and triggers are valid."""
        from django.core.exceptions import ValidationError

        schedule = self.effective_schedule()
        if not schedule:
            return

        total = sum(float(e.get('pct', 0)) for e in schedule)
        if abs(total - 100) > 0.01:
            raise ValidationError(
                f"Payment schedule percentages must sum to 100 (got {total})."
            )

        valid_triggers = {'checkout', 'content', 'design_approval', 'launch'}
        for entry in schedule:
            if entry.get('trigger') not in valid_triggers:
                raise ValidationError(
                    f"Invalid trigger '{entry.get('trigger')}'. "
                    f"Allowed: {', '.join(sorted(valid_triggers))}"
                )


class Subscription(models.Model):
    """
    Links a user to a plan. FR-9, FR-18, FR-19, FR-65, FR-86.

    The `payment_schedule` snapshot here is what makes FR-86 work: once
    an order is placed, future edits to the Plan's schedule or price
    don't retroactively change what this customer owes.
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
        _("status"), max_length=20, choices=STATUS_CHOICES, default='active',
    )

    # --- FR-86: contract snapshot -----------------------------------------
    total_contract_mwk = models.DecimalField(
        _("total contract (MWK)"),
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text=_("Snapshot of Plan.price_mwk at signup."),
    )
    payment_schedule = models.JSONField(
        _("payment schedule"),
        default=list, blank=True,
        help_text=_("Snapshot of Plan.effective_schedule() at signup."),
    )

    # --- Renewal tracking (FR-91) -----------------------------------------
    # Renewal invoices are tracked separately and do NOT count toward the
    # contract balance.
    renewal_invoice = models.ForeignKey(
        'Invoice', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='renewal_for',
        help_text=_("Single renewal invoice for the next billing period."),
    )

    # --- Renewal-cycle dunning flags (renewals only) ----------------------
    reminder_30d_sent       = models.BooleanField(default=False)
    reminder_7d_sent        = models.BooleanField(default=False)
    reminder_3d_sent        = models.BooleanField(default=False)
    reminder_1d_sent        = models.BooleanField(default=False)
    expired_notice_sent     = models.BooleanField(default=False)
    suspension_warning_sent = models.BooleanField(default=False)
    final_warning_sent      = models.BooleanField(default=False)

    suspended_at          = models.DateTimeField(null=True, blank=True)
    scheduled_deletion_at = models.DateTimeField(null=True, blank=True)
    cancelled_at          = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Subscription")
        verbose_name_plural = _("Subscriptions")
        ordering = ['-start_date']

    def __str__(self):
        return f"{self.user.email} - {self.plan.name} (ends {self.end_date})"

    # ==================================================================
    # Balance helpers (FR-89, FR-91)
    # ==================================================================

    @property
    def contract_total_mwk(self):
        """Snapshot price, with fallback to plan price for legacy rows."""
        return self.total_contract_mwk or self.plan.price_mwk

    @property
    def total_paid_mwk(self):
        """
        Sum of PAID instalments only (renewals excluded per FR-91).

        Uses SubscriptionInstalment, not Payment, because instalments
        are the authoritative record of what counts toward the contract.
        A renewal invoice has no linked instalment, so its Payment rows
        are invisible to this query — exactly what FR-91 requires.
        """
        from django.db.models import Sum
        return (
            self.instalments
            .filter(status='paid')
            .aggregate(total=Sum('amount_mwk'))
            ['total']
            or 0
        )

    @property
    def remaining_balance_mwk(self):
        """Amount still owed on the contract. Never negative."""
        from decimal import Decimal
        return max(Decimal('0'), self.contract_total_mwk - self.total_paid_mwk)

    @property
    def paid_pct(self) -> int:
        """Rounded percentage of contract paid (0–100)."""
        total = self.contract_total_mwk
        if not total:
            return 0
        return min(100, int((self.total_paid_mwk / total) * 100))

    @property
    def has_outstanding_balance(self) -> bool:
        return self.remaining_balance_mwk > 0

    @property
    def is_fully_paid(self) -> bool:
        return self.remaining_balance_mwk == 0

    @property
    def next_instalment(self):
        """
        The next instalment the customer needs to look at: issued-but-
        unpaid first, then the earliest scheduled one.

        Used by the dashboard to highlight the "next" line on the
        payment schedule card.
        """
        return (
            self.instalments
            .filter(status__in=['issued', 'scheduled'])
            .order_by('sequence')
            .first()
        )

    # ==================================================================
    # Renewal / lifecycle convenience
    # ==================================================================

    @property
    def days_until_expiry(self) -> int:
        return (self.end_date - timezone.localdate()).days

    @property
    def is_expired(self) -> bool:
        return self.days_until_expiry < 0

    @property
    def is_in_grace_period(self) -> bool:
        return -7 <= self.days_until_expiry < 0

    @property
    def is_at_risk(self) -> bool:
        return (
            self.is_active
            and self.status == 'active'
            and 0 <= self.days_until_expiry <= 30
            and self.renewal_invoice is not None
            and self.renewal_invoice.status != 'paid'
        )

    def reset_dunning_flags(self):
        """
        Called when a renewal payment succeeds — clears the per-cycle
        renewal/dunning state. Instalments are NOT touched here: the
        contract's instalment record persists across renewals (FR-91).
        """
        self.reminder_30d_sent       = False
        self.reminder_7d_sent        = False
        self.reminder_3d_sent        = False
        self.reminder_1d_sent        = False
        self.expired_notice_sent     = False
        self.suspension_warning_sent = False
        self.final_warning_sent      = False

        self.renewal_invoice       = None
        self.suspended_at          = None
        self.scheduled_deletion_at = None
        self.cancelled_at          = None

        self.status                = 'active'
        self.is_active             = True


class SubscriptionInstalment(models.Model):
    """
    One instalment of a subscription's payment schedule (FR-85, FR-87, FR-89).

    Status machine
    --------------
        scheduled → issued → paid
                     ↓
                  cancelled  (staff waived / plan changed)

    Only `issued` invoices appear as due on the dashboard and trigger
    reminders (FR-90). `scheduled` instalments are shown as "Upcoming"
    with their trigger label, locked (no Pay button).
    """
    STATUS_CHOICES = (
        ('scheduled', _('Scheduled')),
        ('issued',    _('Issued')),
        ('paid',      _('Paid')),
        ('cancelled', _('Cancelled')),
    )
    TRIGGER_CHOICES = (
        ('checkout',        _('At checkout')),
        ('content',         _('When content is received')),
        ('design_approval', _('On design approval')),
        ('launch',          _('Before launch')),
    )

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name='instalments',
    )
    sequence     = models.PositiveIntegerField(
        help_text="1-based order in the schedule (1 = first/deposit).",
    )
    percentage   = models.DecimalField(
        _("percentage"), max_digits=5, decimal_places=2,
        help_text="Share of the contract (e.g. 25.00 = 25%).",
    )
    amount_mwk   = models.DecimalField(
        _("amount (MWK)"), max_digits=12, decimal_places=2,
        help_text="Snapshot: percentage * contract_total_mwk at signup.",
    )
    label        = models.CharField(max_length=100)
    trigger      = models.CharField(max_length=30, choices=TRIGGER_CHOICES)

    status       = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='scheduled',
    )
    invoice      = models.OneToOneField(
        'Invoice', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='instalment_of',
        help_text="Set when the instalment is issued as an invoice.",
    )
    issued_at    = models.DateTimeField(null=True, blank=True)
    due_date     = models.DateField(null=True, blank=True)
    paid_at      = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Subscription Instalment")
        verbose_name_plural = _("Subscription Instalments")
        ordering = ['subscription', 'sequence']
        unique_together = ('subscription', 'sequence')

    def __str__(self):
        return (
            f"#{self.sequence} {self.label} "
            f"({self.percentage}% = {self.amount_mwk:,.0f} MWK) "
            f"[{self.status}]"
        )


class Invoice(models.Model):
    """
    Billable document. SRS FR-17, FR-64, FR-78.

    In v1.5 the `invoice_type` field was simplified: contract invoices
    (deposit, milestone, full) are all 'instalment' — the label and
    trigger live on the linked SubscriptionInstalment row. Renewals
    remain 'renewal' and are kept separate per FR-91.
    """
    STATUS_CHOICES = (
        ('draft',  _('Draft')),
        ('unpaid', _('Unpaid')),
        ('paid',   _('Paid')),
        ('void',   _('Void')),
    )
    TYPE_CHOICES = (
        ('instalment', _('Contract instalment')),   # deposit / milestone / full
        ('renewal',    _('Renewal')),               # FR-91: separate stream
        ('credit',     _('Credit note')),
        ('void',       _('Void')),
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
        default='instalment',
    )

    subtotal_mwk = models.DecimalField(_("subtotal (MWK)"), max_digits=12, decimal_places=2)
    tax_mwk      = models.DecimalField(_("tax (MWK)"), max_digits=12, decimal_places=2, default=0)
    total_mwk    = models.DecimalField(_("total (MWK)"), max_digits=12, decimal_places=2)

    is_deposit = models.BooleanField(
        _("is deposit"), default=False,
        help_text=_(
            "True for the very first instalment of a yearly schedule. "
            "Used to trigger Hestia provisioning on payment."
        ),
    )

    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True,
        verbose_name=_("subscription"),
        related_name='invoices',
    )

    # Only set for invoice_type='renewal' — the billing window the invoice covers.
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
    Records a payment against an invoice. SRS FR-20.
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