from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
import logging

from .models import Project
from billing.models import Invoice
from core.services.email_utils import send_new_invoice_notification

logger = logging.getLogger(__name__)

@receiver(pre_save, sender=Project)
def track_old_stage(sender, instance, **kwargs):
    """Captures the stage before saving to detect changes."""
    if instance.pk:
        try:
            old_instance = Project.objects.get(pk=instance.pk)
            instance._old_stage = old_instance.stage
        except Project.DoesNotExist:
            instance._old_stage = None
    else:
        instance._old_stage = None


@receiver(post_save, sender=Project)
def generate_milestone_invoices(sender, instance, created, **kwargs):
    """
    Automatically generates invoices and sends notifications 
    when the project stage advances to Approval or Launch.
    SRS FR-64: Balance is due at design approval and launch.
    """
    if created or not getattr(instance, 'total_price_mwk', None):
        return

    old_stage = getattr(instance, '_old_stage', None)
    new_stage = instance.stage

    # If the stage hasn't changed, do nothing
    if old_stage == new_stage:
        return

    # 37.5% balance for Design Approval
    if new_stage == 'approval' and old_stage != 'approval':
        amount = instance.total_price_mwk * Decimal('0.375')
        invoice = Invoice.objects.create(
            customer=instance.customer,
            due_date=timezone.now().date() + timedelta(days=7),
            subtotal_mwk=amount,
            total_mwk=amount,
            is_deposit=False
        )
        logger.info(f"Generated milestone invoice {invoice.invoice_number} for Approval stage.")
        # Send Email Notification
        send_new_invoice_notification(invoice)
    
    # 37.5% balance for Launch
    elif new_stage == 'launch' and old_stage != 'launch':
        amount = instance.total_price_mwk * Decimal('0.375')
        invoice = Invoice.objects.create(
            customer=instance.customer,
            due_date=timezone.now().date() + timedelta(days=7),
            subtotal_mwk=amount,
            total_mwk=amount,
            is_deposit=False
        )
        logger.info(f"Generated milestone invoice {invoice.invoice_number} for Launch stage.")
        # Send Email Notification
        send_new_invoice_notification(invoice)