# hosting/tasks.py
import random
import string
import logging
from celery import shared_task
from django.db import transaction

from hosting.models import HostingAccount
from hosting.services.hestia import HestiaCP
from core.tasks import send_transactional_email_task

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def provision_hosting_account_task(self, invoice_id):
    """
    Celery task to provision a hosting account after a deposit is paid.
    Retries up to 3 times if the API call fails.
    """
    from billing.models import Invoice # Import inside task to avoid circular imports
    try:
        invoice = Invoice.objects.get(id=invoice_id)
    except Invoice.DoesNotExist:
        logger.error(f"Cannot provision: Invoice {invoice_id} not found.")
        return

    user = invoice.customer
    subscription = user.subscription_set.filter(is_active=True).order_by('-id').first()
    if not subscription:
        logger.error(f"Cannot provision: No active subscription for {user.email}")
        return

    # Idempotency check
    if HostingAccount.objects.filter(user=user).exists():
        logger.info(f"Hosting account for {user.email} already exists.")
        return

    # Generate credentials
    base = user.email.split('@')[0].lower()
    base = ''.join(c for c in base if c.isalnum())[:8]
    username = f"{base}{''.join(random.choices(string.digits, k=4))}"
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    hestia_package = f"{subscription.plan.name.lower()}_pkg"

    # Call HestiaCP API
    hestia = HestiaCP()
    result = hestia.create_user(
        username=username,
        password=password,
        email=user.email,
        package_name=hestia_package
    )

    if result.get("status") == "success":
        with transaction.atomic():
            HostingAccount.objects.create(
                user=user,
                subscription=subscription,
                hestia_username=username,
                hestia_package_name=hestia_package,
                is_active=True
            )
        logger.info(f"Successfully provisioned hosting for {user.email}")
        
        # Chain the welcome email task
        send_transactional_email_task.delay(
            subject="Your Pritech Hosting Account is Ready! 🚀",
            template_name='welcome_hosting',
            context={
                'customer_name': user.first_name,
                'username': username,
                'password': password,
                'cpanel_url': "https://sme.pritechmw.com:8083",
                'dashboard_url': "https://pritechhost.com/dashboard/"
            },
            to_email=user.email
        )
    else:
        error_msg = result.get('message', 'Unknown error')
        logger.error(f"Failed to provision hosting for {user.email}: {error_msg}")
        # Retry the task
        raise self.retry(exc=Exception(error_msg))