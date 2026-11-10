# billing/services/payments.py
"""
One place that turns a verified PayChangu payment into a paid invoice.

The webhook, the browser callback and the dashboard all call
finalize_invoice_payment(). It is idempotent, so whichever runs first wins
and the others do nothing. Nothing depends on the webhook alone any more.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from billing.models import Invoice, Payment
from billing.services.paychangu import PayChanguService

logger = logging.getLogger(__name__)

# How long a started payment attempt is worth checking with PayChangu.
PENDING_WINDOW = timedelta(minutes=45)


def _method_from(data):
    """Map PayChangu's authorization info to our payment_method values."""
    auth = data.get('authorization') or {}
    text = f"{auth.get('provider', '')} {auth.get('channel', '')}".lower()
    if 'airtel' in text:
        return 'airtel'
    if 'tnm' in text or 'mpamba' in text:
        return 'tnm'
    if any(word in text for word in ('card', 'visa', 'master')):
        return 'card'
    return (auth.get('channel') or 'other').lower()[:50]


def _queue_follow_ups(invoice_id):
    """Receipt email, and provisioning for deposit invoices (async via Celery)."""
    from core.tasks import send_transactional_email_task
    from hosting.tasks import provision_hosting_account_task

    invoice = Invoice.objects.select_related('customer').get(id=invoice_id)
    send_transactional_email_task.delay(
        subject=f"Payment Received - {invoice.invoice_number}",
        template_name='payment_confirmed',
        context={
            'customer_name': invoice.customer.first_name,
            'invoice_number': invoice.invoice_number,
            'amount': invoice.total_mwk,
            'dashboard_url': "https://pritechhost.com/dashboard/",
        },
        to_email=invoice.customer.email,
    )
    if invoice.is_deposit:
        provision_hosting_account_task.delay(invoice.id)


def finalize_invoice_payment(tx_ref):
    """
    Verify tx_ref with PayChangu and, if it was paid in full, mark its invoice
    paid and record the payment.

    Returns (invoice, newly_paid). (None, False) means the payment could not
    be confirmed (not paid yet, failed, or unknown reference).
    """
    invoice_id = PayChanguService.invoice_id_from_ref(tx_ref)
    if invoice_id is None:
        logger.error("Payments: unrecognised reference %r", tx_ref)
        return None, False

    # Network call first, outside the database lock.
    data = PayChanguService.verify_payment(tx_ref)
    if data is None:
        return None, False

    with transaction.atomic():
        try:
            invoice = Invoice.objects.select_for_update().get(id=invoice_id)
        except Invoice.DoesNotExist:
            logger.error("Payments: no invoice %s for %s", invoice_id, tx_ref)
            return None, False

        if invoice.status == 'paid':
            return invoice, False  # someone else already settled it

        paid = Decimal(str(data.get('amount') or 0))
        if data.get('currency', 'MWK') != 'MWK' or paid < invoice.total_mwk:
            logger.error(
                "Payments: %s paid %s %s, expected MWK %s.",
                tx_ref, paid, data.get('currency'), invoice.total_mwk,
            )
            return None, False

        invoice.status = 'paid'
        invoice.save(update_fields=['status'])

        payment = Payment.objects.filter(gateway_reference=tx_ref).first()
        if payment:
            payment.status = 'success'
            payment.payment_method = _method_from(data)
            payment.amount_mwk = invoice.total_mwk
            payment.save(update_fields=['status', 'payment_method', 'amount_mwk'])
        else:
            Payment.objects.create(
                invoice=invoice,
                amount_mwk=invoice.total_mwk,
                gateway_reference=tx_ref,
                status='success',
                payment_method=_method_from(data),
            )

        transaction.on_commit(lambda: _queue_follow_ups(invoice.id))

    logger.info("Payments: %s marked PAID via %s.", invoice.invoice_number, tx_ref)
    return invoice, True


def _recent_pending(user):
    cutoff = timezone.now() - PENDING_WINDOW
    return Payment.objects.filter(
        invoice__customer=user,
        invoice__status='unpaid',
        status='pending',
        created_at__gte=cutoff,
    )


def sync_pending_payments(user):
    """Settle recent attempts the webhook has not confirmed (yet).
    Called whenever the dashboard loads."""
    changed = False
    for ref in list(_recent_pending(user).values_list('gateway_reference', flat=True)[:3]):
        _, newly_paid = finalize_invoice_payment(ref)
        changed = changed or newly_paid
    return changed


def has_recent_pending_payment(user):
    return _recent_pending(user).exists()
