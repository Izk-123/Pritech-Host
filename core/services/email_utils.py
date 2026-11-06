from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

def send_transactional_email(subject, template_name, context, to_email):
    """
    Helper function to send HTML emails with a plain-text fallback.
    """
    try:
        # Render the HTML template
        html_content = render_to_string(f'emails/{template_name}.html', context)
        
        # Create the email message
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

# --- Specific Email Functions ---

def send_payment_confirmation(invoice):
    """Sends a receipt when an invoice is paid."""
    subject = f"Payment Received - {invoice.invoice_number}"
    context = {
        'customer_name': invoice.customer.first_name,
        'invoice_number': invoice.invoice_number,
        'amount': invoice.total_mwk,
        'dashboard_url': "https://sme.pritechmw.com/dashboard/"
    }
    send_transactional_email(subject, 'payment_confirmed', context, invoice.customer.email)

def send_provisioning_welcome(user, username, password):
    """Sends hosting credentials when the Hestia account is created."""
    subject = "Your Pritech Hosting Account is Ready! 🚀"
    context = {
        'customer_name': user.first_name,
        'username': username,
        'password': password,
        'cpanel_url': "https://sme.pritechmw.com:8083",
        'dashboard_url': "https://sme.pritechmw.com/dashboard/"
    }
    send_transactional_email(subject, 'welcome_hosting', context, user.email)

def send_new_invoice_notification(invoice):
    """Sends a notification when a new milestone invoice is generated."""
    subject = f"New Invoice Generated - {invoice.invoice_number}"
    context = {
        'customer_name': invoice.customer.first_name,
        'invoice_number': invoice.invoice_number,
        'amount': invoice.total_mwk,
        'due_date': invoice.due_date.strftime('%d %b %Y'),
        'dashboard_url': "https://sme.pritechmw.com/dashboard/"
    }
    send_transactional_email(subject, 'new_invoice', context, invoice.customer.email)