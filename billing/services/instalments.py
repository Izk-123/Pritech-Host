"""
Instalment issuance & milestone handling (SRS FR-85 to FR-95).

Every ``.delay()`` call that fires inside a web request is wrapped in
``try/except`` so a broken email server (dev SMTP auth failure, prod
broker hiccup) can never roll back the transaction that created the
instalment. The failure is logged; the email will still be retried by
Celery in production.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from billing.models import (
    Invoice,
    Subscription,
    SubscriptionInstalment,
)

logger = logging.getLogger(__name__)


# How many days an issued instalment invoice stays "due" before the
# "overdue" reminder cadence kicks in. Configurable — bump to 14 if
# your customers need more time.
INSTALMENT_DUE_DAYS = 7


# ===========================================================================
# Creation
# ===========================================================================
def create_instalments_for_subscription(subscription):
    """
    Called once, at order time. Creates every SubscriptionInstalment
    in ``scheduled`` state, then immediately issues the first one
    (trigger='checkout').

    Returns the first instalment (which now has ``.invoice`` set).
    """
    schedule = subscription.payment_schedule
    if not schedule:
        # Legacy row without a schedule — synthesize from the plan.
        schedule = subscription.plan.effective_schedule()
        subscription.payment_schedule = schedule
        subscription.save(update_fields=['payment_schedule'])

    contract_total = subscription.contract_total_mwk

    instalments_to_create = []
    for i, entry in enumerate(schedule, start=1):
        pct = Decimal(str(entry.get('pct', 0)))
        amount = (contract_total * pct / Decimal('100')).quantize(Decimal('0.01'))
        instalments_to_create.append(SubscriptionInstalment(
            subscription=subscription,
            sequence=i,
            percentage=pct,
            amount_mwk=amount,
            label=entry.get('label', f'Instalment {i}'),
            trigger=entry.get('trigger', 'checkout'),
            status='scheduled',
        ))

    SubscriptionInstalment.objects.bulk_create(instalments_to_create)

    # Immediately issue any instalments triggered at checkout.
    for inst in SubscriptionInstalment.objects.filter(
        subscription=subscription, trigger='checkout', status='scheduled',
    ).order_by('sequence'):
        _issue_instalment(inst)

    return SubscriptionInstalment.objects.filter(
        subscription=subscription, sequence=1,
    ).first()


# ===========================================================================
# Issuance
# ===========================================================================
def _issue_instalment(instalment, due_date=None):
    """
    Create an Invoice for a scheduled instalment, mark it issued, and
    queue the "invoice issued" notification email.

    Idempotent: if the instalment is already issued/paid/cancelled,
    does nothing and returns the existing invoice (or None).

    The email dispatch is wrapped in try/except — a broken SMTP server
    must NOT prevent the customer from being able to pay.
    """
    if instalment.status in ('issued', 'paid', 'cancelled'):
        return instalment.invoice

    subscription = instalment.subscription
    today = timezone.localdate()
    due = due_date or (today + timedelta(days=INSTALMENT_DUE_DAYS))

    # The very first instalment (deposit/full) is due immediately.
    if instalment.sequence == 1:
        due = today

    invoice = Invoice.objects.create(
        customer=subscription.user,
        due_date=due,
        status='unpaid',
        invoice_type='instalment',
        subtotal_mwk=instalment.amount_mwk,
        total_mwk=instalment.amount_mwk,
        is_deposit=(instalment.sequence == 1 and not subscription.plan.is_monthly),
        subscription=subscription,
    )

    instalment.invoice   = invoice
    instalment.status    = 'issued'
    instalment.issued_at = timezone.now()
    instalment.due_date  = due
    instalment.save(update_fields=['invoice', 'status', 'issued_at', 'due_date'])

    # --- Notify the customer (defensively) --------------------------------
    # If this fails (SMTP down, credentials bad, broker unreachable) we
    # log a warning and move on. The instalment is still issued and the
    # customer can still pay from the dashboard. In production, Celery
    # will retry the actual send.
    try:
        from core.tasks import send_transactional_email_task
        send_transactional_email_task.delay(
            subject=f"Invoice {invoice.invoice_number} is ready — {instalment.label}",
            template_name='invoice_issued',
            context={
                'customer_name':    subscription.user.first_name or subscription.user.email,
                'plan_name':        subscription.plan.name,
                'instalment_label': instalment.label,
                'amount':           float(instalment.amount_mwk),
                'due_date':         due.strftime('%d %b %Y'),
                'invoice_number':   invoice.invoice_number,
                'pay_url':          'https://pritechhost.com/dashboard/',
            },
            to_email=subscription.user.email,
        )
    except Exception as exc:
        logger.warning(
            f"[INSTALMENT] Could not queue invoice-issued email for "
            f"#{instalment.sequence} ({instalment.label}): {exc}. "
            f"Invoice {invoice.invoice_number} was still issued."
        )

    logger.info(
        f"[INSTALMENT] Issued #{instalment.sequence} '{instalment.label}' "
        f"({instalment.amount_mwk} MWK) — due {due}."
    )
    return invoice


# ===========================================================================
# Milestones
# ===========================================================================
def issue_milestone_instalments(subscription, trigger):
    """
    Called when a project stage reaches a milestone (FR-88).
    Issues any scheduled instalments that match this trigger.

    Returns the list of invoices created (may be empty).
    """
    scheduled = SubscriptionInstalment.objects.filter(
        subscription=subscription,
        trigger=trigger,
        status='scheduled',
    ).order_by('sequence')

    issued = []
    for inst in scheduled:
        inv = _issue_instalment(inst)
        if inv:
            issued.append(inv)
    return issued


# ===========================================================================
# Payment settlement
# ===========================================================================
def mark_instalment_paid(invoice):
    """
    Called from the payment finalizer when an instalment invoice is paid.

    Marks the instalment as paid, records the timestamp, and triggers
    project stage auto-advancement (FR-94).
    """
    try:
        inst = invoice.instalment_of
    except SubscriptionInstalment.DoesNotExist:
        return

    inst.status  = 'paid'
    inst.paid_at = timezone.now()
    inst.save(update_fields=['status', 'paid_at'])
    logger.info(f"[INSTALMENT] #{inst.sequence} '{inst.label}' marked PAID.")

    # Auto-advance project stage per FR-94
    _maybe_advance_project_stage(invoice.subscription, inst)


def _maybe_advance_project_stage(subscription, instalment):
    """
    FR-94: project stage auto-advances when its condition is met.

        Deposit paid  → project moves from 'deposit' to 'content'
        Launch paid   → project moves to 'live'
    """
    from projects.models import Project

    project = Project.objects.filter(customer=subscription.user).order_by('-id').first()
    if not project:
        return

    # Deposit → content
    if instalment.sequence == 1 and project.stage == 'deposit':
        project.stage = 'content'
        project.save(update_fields=['stage'])
        logger.info(f"[PROJECT] Stage advanced to 'content' for sub {subscription.id}")

    # Launch paid → live
    if instalment.trigger == 'launch' and project.stage == 'launch':
        project.stage = 'live'
        project.save(update_fields=['stage'])
        logger.info(f"[PROJECT] Stage advanced to 'live' for sub {subscription.id}")


# ===========================================================================
# Launch gating (FR-92)
# ===========================================================================
def can_launch(subscription):
    """
    FR-92: launch blocked until every instalment up to and including
    the pre-launch ('launch' trigger) instalment is paid.

    Returns (allowed, reason_if_not).
    """
    pre_launch = subscription.instalments.filter(trigger='launch').first()
    if not pre_launch:
        # No launch instalment in schedule → no gate.
        return True, ''

    unpaid_before_launch = subscription.instalments.filter(
        sequence__lte=pre_launch.sequence,
        status__in=['scheduled', 'issued'],
    )
    if unpaid_before_launch.exists():
        first_blocking = unpaid_before_launch.order_by('sequence').first()
        return (
            False,
            f"Pay the {first_blocking.label} instalment "
            f"({first_blocking.amount_mwk:,.0f} MWK) before launching.",
        )
    return True, ''