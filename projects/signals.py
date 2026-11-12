"""
Project signals — milestone instalment issuance (FR-88) and stage
transition bookkeeping (FR-94).

The old (pre-v1.5) approach hard-coded 37.5% invoices at 'approval' and
'launch' stages. Now the schedule is data on the plan, and this signal
simply *triggers* the appropriate instalments when the project reaches
the matching stage.

Auto-advancement (FR-94) lives in billing.services.instalments —
paying an instalment advances the project to the next stage.
"""
import logging

from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from .models import Project

logger = logging.getLogger(__name__)


# Trigger mapping: when the project enters this stage, issue these
# instalment triggers.
STAGE_TO_TRIGGER = {
    'content':  'content',
    'approval': 'design_approval',
    'launch':   'launch',
}


@receiver(pre_save, sender=Project)
def _capture_old_stage(sender, instance, **kwargs):
    """
    Snapshot the previous stage before saving so the post_save receiver
    can detect transitions. On create, `_old_stage` is set to None so
    the post_save handler can skip the newly-created case.
    """
    if instance.pk:
        try:
            instance._old_stage = Project.objects.get(pk=instance.pk).stage
        except Project.DoesNotExist:
            instance._old_stage = None
    else:
        instance._old_stage = None


@receiver(post_save, sender=Project)
def _issue_milestone_instalments(sender, instance, created, **kwargs):
    """
    FR-88: issue milestone instalments when the project reaches a
    milestone stage.

    Idempotent — `issue_milestone_instalments()` in billing.services
    only acts on `scheduled` instalments for the matching trigger, so
    re-saving the same stage (or a stage we don't have a trigger for)
    is a no-op.
    """
    if created:
        return

    old_stage = getattr(instance, '_old_stage', None)
    new_stage = instance.stage

    if not old_stage or old_stage == new_stage:
        return

    subscription = instance.subscription
    if not subscription:
        return

    trigger = STAGE_TO_TRIGGER.get(new_stage)
    if not trigger:
        return

    try:
        from billing.services.instalments import issue_milestone_instalments
        issued = issue_milestone_instalments(subscription, trigger)
        if issued:
            logger.info(
                f"[PROJECT] Advanced to '{new_stage}' — issued "
                f"{len(issued)} '{trigger}' instalment invoice(s) for sub {subscription.id}"
            )
    except Exception as exc:
        logger.exception(
            f"[PROJECT] Failed to issue '{trigger}' instalments for "
            f"subscription {subscription.id} on stage '{new_stage}': {exc}"
        )