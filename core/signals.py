"""
Signal handlers for the core app.

Two independent concerns:

    1. user_signed_up  → record legal acceptance at signup time.
    2. TicketReply.post_save → notify the other party of a new reply
                               and auto-flip ticket status. (SRS FR-46)

Both use lazy imports inside the handler to avoid Django's
"apps aren't loaded yet" startup errors (this module is imported from
``core/apps.py`` during app initialization).
"""
import logging

from allauth.account.signals import user_signed_up
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import LegalDocument, TermsAcceptance

logger = logging.getLogger(__name__)


# =============================================================================
# Legal acceptance at signup
# =============================================================================

@receiver(user_signed_up)
def record_signup_acceptance(request, user, **kwargs):
    """
    Record acceptance of Terms, Privacy and AUP at signup time.

    ``request`` may be None in rare programmatic flows — in that case we
    still record the acceptance with a null IP, which is better than no
    record at all for compliance purposes.
    """
    from .views import _get_client_ip

    client_ip = _get_client_ip(request) if request else None
    user_agent = (
        request.META.get('HTTP_USER_AGENT', '')[:1000]
        if request else ''
    )

    for slug in ('terms', 'privacy', 'aup'):
        doc = LegalDocument.current(slug)
        if doc:
            TermsAcceptance.objects.create(
                user=user,
                document=doc,
                context='signup',
                ip_address=client_ip,
                user_agent=user_agent,
                content_hash=doc.content_hash(),
            )


# =============================================================================
# Ticket reply notifications (SRS FR-46)
# =============================================================================

@receiver(post_save, sender='core.TicketReply')
def notify_on_reply(sender, instance, created, **kwargs):
    """
    Fires after every TicketReply is saved.

    Behaviour matrix
    ----------------
    Staff reply:
        - Ticket.status → 'awaiting_customer'
        - Email sent to the customer (queued via Celery)
        - Reply left as ``read_by_customer=False`` (drives the unread badge)

    Customer reply:
        - If Ticket.status was 'awaiting_customer', flip back to 'open'
        - Email sent to sales@pritechmw.com (queued via Celery)

    Guard: ``created`` check ensures we don't re-notify on edits or on
    status changes to the parent ticket (which don't touch this signal).
    """
    if not created:
        return

    # Imported here to avoid a circular import at Django startup —
    # apps.py imports this module before the tasks module is fully wired.
    from core.tasks import send_transactional_email_task

    ticket = instance.ticket

    # ------------------------------------------------------------------
    # STAFF replied
    # ------------------------------------------------------------------
    if instance.is_staff_reply:
        # Flip status so the support team knows the ball is in the
        # customer's court.
        ticket.status = 'awaiting_customer'
        ticket.save(update_fields=['status', 'updated_at'])

        send_transactional_email_task.delay(
            subject=f"Re: #{ticket.id} — {ticket.subject}",
            template_name='ticket_reply_notification',
            context={
                'customer_name':  ticket.user.first_name or ticket.user.email,
                'ticket_id':      ticket.id,
                'ticket_subject': ticket.subject,
                'reply_message':  instance.message,
                'ticket_url':     'https://pritechhost.com/dashboard/',
            },
            to_email=ticket.user.email,
        )
        logger.info(
            f"[TICKET] Staff reply on #{ticket.id} emailed to {ticket.user.email}"
        )
        return

    # ------------------------------------------------------------------
    # CUSTOMER replied
    # ------------------------------------------------------------------
    if ticket.status == 'awaiting_customer':
        ticket.status = 'open'
        ticket.save(update_fields=['status', 'updated_at'])

    send_transactional_email_task.delay(
        subject=f"[Customer Reply] #{ticket.id} — {ticket.subject}",
        template_name='ticket_new_customer_reply',
        context={
            'customer_name':  (
                f"{ticket.user.first_name} {ticket.user.last_name}".strip()
                or ticket.user.email
            ),
            'customer_email': ticket.user.email,
            'ticket_id':      ticket.id,
            'ticket_subject': ticket.subject,
            'reply_message':  instance.message,
            'admin_url': (
                f'https://pritechhost.com/admin/core/ticket/{ticket.id}/change/'
            ),
        },
        to_email='sales@pritechmw.com',
    )
    logger.info(f"[TICKET] Customer reply on #{ticket.id} emailed to sales@")