# core/tasks.py
import logging
from celery import shared_task
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_transactional_email_task(self, subject, template_name, context, to_email):
    """
    Celery task to send HTML emails with a plain-text fallback.
    Retries up to 3 times if sending fails.
    """
    try:
        html_content = render_to_string(f'emails/{template_name}.html', context)
        msg = EmailMultiAlternatives(
            subject=subject,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
            reply_to=[settings.SALES_EMAIL]
        )
        msg.attach_alternative(html_content, "text/html")
        msg.send()
        logger.info(f"Email '{subject}' sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}")
        # Retry the task with exponential backoff
        raise self.retry(exc=e)

# You can also create convenience wrappers if needed, or call the task directly.