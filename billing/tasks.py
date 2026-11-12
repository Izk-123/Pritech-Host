"""
Renewal, dunning & instalment reminders (SRS FR-18, FR-19, FR-90).

One master task runs daily at 00:30 Africa/Blantyre and does two things:

A. INSTALMENT REMINDERS (FR-90)
   For every ISSUED-and-UNPAID instalment invoice:
     - 3 days before due date
     - on the due date
     - every 30 days while overdue (configurable in the helper)

B. RENEWAL LIFECYCLE
   Standard renewal + dunning timeline relative to end_date:
     -30d  early reminder
     -14d  generate renewal invoice + reminder
      -7d  reminder
      -3d  urgent reminder
      -1d  final reminder
       0d  expiry notice
      +7d  suspension warning (24h)
      +8d  SUSPEND Hestia
     +30d  final warning (deletion in 15 days)
     +45d  delete account + cancel subscription

Renewal invoices never touch instalments (FR-91): they're tracked on
Subscription.renewal_invoice and don't reduce contract balance.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from core.tasks import send_transactional_email_task
from hosting.models import HostingAccount
from hosting.services.hestia import HestiaCP

logger = logging.getLogger(__name__)


# ===========================================================================
# Master task
# ===========================================================================
@shared_task
def check_subscription_lifecycle():
    """
    Runs daily.
    """
    from billing.models import Subscription

    processed = {
        'instalment_reminders': 0,
        'reminders':            0,
        'invoices':             0,
        'suspended':            0,
        'warned':               0,
        'flagged':              0,
    }

    # --- 1. Instalment reminders (independent of subscription lifecycle) --
    processed['instalment_reminders'] = _check_instalment_reminders()

    # --- 2. Renewal / dunning timeline ------------------------------------
    active_subs = Subscription.objects.filter(is_active=True)
    logger.info(f"[DUNNING] Scanning {active_subs.count()} active subscriptions")

    for sub in active_subs.select_related('user', 'plan', 'renewal_invoice'):
        days = sub.days_until_expiry

        try:
            # ════════════════════════════════════════════════════════
            # PHASE A: Before expiry (days ≥ 0)
            # ════════════════════════════════════════════════════════
            if days > 0:
                if days <= 30 and not sub.reminder_30d_sent:
                    _send_reminder(sub, '30d')
                    sub.reminder_30d_sent = True
                    sub.save(update_fields=['reminder_30d_sent'])
                    processed['reminders'] += 1

                if days <= 14 and sub.renewal_invoice is None:
                    invoice = _generate_renewal_invoice(sub)
                    processed['invoices'] += 1
                    _send_reminder(sub, '14d', invoice=invoice)
                    # Count the 14d email as the "first" invoice reminder
                    sub.reminder_7d_sent = True
                    sub.save(update_fields=['reminder_7d_sent'])

                if days <= 7 and not sub.reminder_7d_sent:
                    _send_reminder(sub, '7d', invoice=sub.renewal_invoice)
                    sub.reminder_7d_sent = True
                    sub.save(update_fields=['reminder_7d_sent'])
                    processed['reminders'] += 1

                if days <= 3 and not sub.reminder_3d_sent:
                    _send_reminder(sub, '3d', invoice=sub.renewal_invoice)
                    sub.reminder_3d_sent = True
                    sub.save(update_fields=['reminder_3d_sent'])
                    processed['reminders'] += 1

                if days <= 1 and not sub.reminder_1d_sent:
                    _send_reminder(sub, '1d', invoice=sub.renewal_invoice)
                    sub.reminder_1d_sent = True
                    sub.save(update_fields=['reminder_1d_sent'])
                    processed['reminders'] += 1

            # ════════════════════════════════════════════════════════
            # PHASE B: Day of expiry
            # ════════════════════════════════════════════════════════
            elif days == 0 and not sub.expired_notice_sent:
                _send_reminder(sub, 'expired', invoice=sub.renewal_invoice)
                sub.expired_notice_sent = True
                sub.save(update_fields=['expired_notice_sent'])
                processed['reminders'] += 1

            # ════════════════════════════════════════════════════════
            # PHASE C: Grace period (0 > days ≥ -7)
            # ════════════════════════════════════════════════════════
            elif -7 <= days < 0:
                if days == -7 and not sub.suspension_warning_sent:
                    _send_reminder(sub, 'suspension_warning', invoice=sub.renewal_invoice)
                    sub.suspension_warning_sent = True
                    sub.status = 'expired'
                    sub.save(update_fields=['suspension_warning_sent', 'status'])
                    processed['warned'] += 1

            # ════════════════════════════════════════════════════════
            # PHASE D: Suspension (days <= -8 and >= -29)
            # ════════════════════════════════════════════════════════
            elif -29 <= days <= -8:
                if sub.suspended_at is None:
                    _suspend_subscription(sub)
                    processed['suspended'] += 1

            # ════════════════════════════════════════════════════════
            # PHASE E: Final warning (days == -30)
            # ════════════════════════════════════════════════════════
            elif days == -30 and not sub.final_warning_sent:
                _send_reminder(sub, 'final_warning')
                sub.final_warning_sent = True
                sub.scheduled_deletion_at = timezone.now() + timedelta(days=15)
                sub.save(update_fields=['final_warning_sent', 'scheduled_deletion_at'])
                processed['warned'] += 1

            # ════════════════════════════════════════════════════════
            # PHASE F: Deletion / archival (days <= -45)
            # ════════════════════════════════════════════════════════
            elif days <= -45 and sub.status != 'cancelled':
                _archive_subscription(sub)
                processed['flagged'] += 1

        except Exception as e:
            logger.exception(f"[DUNNING] Error processing subscription {sub.id}: {e}")

    logger.info(f"[DUNNING] Completed: {processed}")
    return processed


# ===========================================================================
# Instalment reminders (FR-90)
# ===========================================================================
def _check_instalment_reminders():
    """
    Send reminders for issued-and-unpaid instalment invoices at:
        • 3 days before due date
        • on the due date
        • every 30 days past due

    Reminders stop automatically when the invoice is paid (queryset
    filters on `invoice__status='unpaid'`) or the subscription is
    cancelled.
    """
    from billing.models import SubscriptionInstalment

    today = timezone.localdate()
    issued = (
        SubscriptionInstalment.objects
        .filter(status='issued', invoice__status='unpaid')
        .select_related(
            'subscription', 'subscription__user',
            'subscription__plan', 'invoice',
        )
    )

    sent = 0
    for inst in issued:
        if not inst.due_date:
            continue

        days_to_due = (inst.due_date - today).days
        days_overdue = -days_to_due if days_to_due < 0 else 0

        stage = None
        if days_to_due == 3:
            stage = '3d_before'
        elif days_to_due == 0:
            stage = 'due'
        elif days_overdue > 0 and days_overdue % 30 == 0:
            stage = 'overdue'

        if stage:
            _send_instalment_reminder(inst, stage)
            sent += 1

    return sent


def _send_instalment_reminder(inst, stage):
    """Dispatch the appropriate instalment reminder email."""
    subject_map = {
        '3d_before': f"Reminder: {inst.label} payment due in 3 days",
        'due':       f"Payment due today: {inst.label}",
        'overdue':   f"Overdue: {inst.label} payment of {inst.amount_mwk:,.0f} MWK",
    }
    send_transactional_email_task.delay(
        subject=subject_map.get(stage, f"Reminder: {inst.label}"),
        template_name='instalment_reminder',
        context={
            'customer_name':  inst.subscription.user.first_name or inst.subscription.user.email,
            'plan_name':      inst.subscription.plan.name,
            'label':          inst.label,
            'amount':         float(inst.amount_mwk),
            'due_date':       inst.due_date.strftime('%d %b %Y'),
            'invoice_number': inst.invoice.invoice_number,
            'stage':          stage,
            'pay_url':        'https://pritechhost.com/dashboard/',
        },
        to_email=inst.subscription.user.email,
    )
    logger.info(f"[INSTALMENT] Sent '{stage}' reminder for #{inst.sequence} ({inst.id})")


# ===========================================================================
# Renewal helpers
# ===========================================================================
def _generate_renewal_invoice(sub):
    """Create an unpaid renewal invoice for the next billing period."""
    from billing.models import Invoice

    period_start = sub.end_date + timedelta(days=1)
    period_end   = period_start + timedelta(days=sub.plan.period_days)

    invoice = Invoice.objects.create(
        customer=sub.user,
        due_date=sub.end_date,
        status='unpaid',
        invoice_type='renewal',
        subtotal_mwk=sub.contract_total_mwk,
        total_mwk=sub.contract_total_mwk,
        is_deposit=False,
        subscription=sub,
        renewal_period_start=period_start,
        renewal_period_end=period_end,
    )
    sub.renewal_invoice = invoice
    sub.save(update_fields=['renewal_invoice'])

    logger.info(f"[DUNNING] Generated renewal invoice {invoice.invoice_number} for sub {sub.id}")
    return invoice


def _send_reminder(sub, stage, invoice=None):
    """Dispatch the appropriate renewal reminder email."""
    if invoice is None:
        invoice = sub.renewal_invoice

    payload = {
        'customer_name':  sub.user.first_name or sub.user.email,
        'plan_name':      sub.plan.name,
        'amount':         float(sub.contract_total_mwk),
        'end_date':       sub.end_date.strftime('%d %b %Y'),
        'days_remaining': sub.days_until_expiry,
        'invoice_number': invoice.invoice_number if invoice else None,
        'invoice_id':     invoice.id if invoice else None,
        'dashboard_url':  'https://pritechhost.com/dashboard/',
        'pay_url': (
            f'https://pritechhost.com/dashboard/?invoice={invoice.id}'
            if invoice else 'https://pritechhost.com/dashboard/'
        ),
    }

    subject_map = {
        '30d':                f"Your {sub.plan.name} plan renews in 30 days",
        '14d':                f"Renewal invoice for {sub.plan.name} is ready",
        '7d':                 f"Reminder: Renew your {sub.plan.name} plan",
        '3d':                 f"3 days until your {sub.plan.name} plan expires",
        '1d':                 f"Final reminder: Renew your plan tomorrow",
        'expired':            f"Your {sub.plan.name} plan has expired",
        'suspension_warning': "Your account will be suspended in 24 hours",
        'final_warning':      "Final notice: Account deletion in 15 days",
    }

    send_transactional_email_task.delay(
        subject=subject_map.get(stage, "Renewal reminder"),
        template_name='renewal_reminder',
        context={**payload, 'stage': stage},
        to_email=sub.user.email,
    )


def _suspend_subscription(sub):
    """Suspend the HestiaCP account and mark the subscription."""
    account = HostingAccount.objects.filter(user=sub.user).first()
    if account and account.hestia_username:
        result = HestiaCP().suspend_user(account.hestia_username)
        if result.get('status') == 'success':
            account.is_active = False
            account.save(update_fields=['is_active'])
            logger.info(f"[DUNNING] Suspended Hestia account {account.hestia_username}")

    sub.status = 'suspended'
    sub.suspended_at = timezone.now()
    sub.save(update_fields=['status', 'suspended_at'])

    send_transactional_email_task.delay(
        subject="Your Pritech Hosting account has been suspended",
        template_name='account_suspended',
        context={
            'customer_name':  sub.user.first_name or sub.user.email,
            'plan_name':      sub.plan.name,
            'invoice_number': (
                sub.renewal_invoice.invoice_number
                if sub.renewal_invoice else None
            ),
            'amount':         float(sub.contract_total_mwk),
            'reactivate_url': 'https://pritechhost.com/dashboard/',
        },
        to_email=sub.user.email,
    )


def _archive_subscription(sub):
    """
    Delete the HestiaCP account, flag the subscription as cancelled.
    Invoice records are preserved for accounting (SRS §6 data rules).
    """
    account = HostingAccount.objects.filter(user=sub.user).first()
    if account and account.hestia_username:
        HestiaCP().delete_user(account.hestia_username)
        logger.info(f"[DUNNING] Deleted Hestia account {account.hestia_username}")
        account.delete()

    sub.status = 'cancelled'
    sub.is_active = False
    sub.cancelled_at = timezone.now()
    sub.save(update_fields=['status', 'is_active', 'cancelled_at'])

    send_transactional_email_task.delay(
        subject="Your Pritech Hosting account has been deleted",
        template_name='account_deleted',
        context={
            'customer_name': sub.user.first_name or sub.user.email,
            'plan_name':     sub.plan.name,
        },
        to_email=sub.user.email,
    )