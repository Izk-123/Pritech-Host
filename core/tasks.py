"""
Core Celery tasks.

Contains the single email-dispatch task used by every notification in
the platform. Centralising it here means retry/backoff behaviour and
the from/reply-to headers are consistent everywhere.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def send_transactional_email_task(self, subject, template_name, context, to_email):
    """
    Render an HTML email template and send it via SMTP.

    Retries up to 3 times with exponential backoff (60s → 120s → 240s)
    if SMTP fails — this gracefully handles transient network blips
    without losing the message.

    Arguments
    ---------
        subject         : plain string
        template_name   : filename under templates/emails/, without .html
        context         : dict passed to the Django template
        to_email        : recipient address
    """
    try:
        html_content = render_to_string(f'emails/{template_name}.html', context)
        msg = EmailMultiAlternatives(
            subject=subject,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
            reply_to=[settings.SALES_EMAIL],
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()
        logger.info(f"[EMAIL] Sent '{subject}' to {to_email}")
    except Exception as e:
        logger.error(f"[EMAIL] Failed to send '{subject}' to {to_email}: {e}")
        raise self.retry(exc=e)