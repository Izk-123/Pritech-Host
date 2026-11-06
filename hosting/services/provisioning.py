import random
import string
from django.db import transaction
import logging

from hosting.models import HostingAccount
from hosting.services.hestia import HestiaCP
from core.services.email_utils import send_provisioning_welcome

logger = logging.getLogger(__name__)

def generate_username(email):
    """Generates a safe Linux username from the email."""
    base = email.split('@')[0].lower()
    # Keep only letters and numbers
    base = ''.join(c for c in base if c.isalnum())[:8]
    # Append random digits to ensure uniqueness
    random_suffix = ''.join(random.choices(string.digits, k=4))
    return f"{base}{random_suffix}"

def provision_hosting_account(invoice):
    """
    Triggered by the webhook when a deposit is paid.
    Creates the HostingAccount record and calls HestiaCP.
    """
    user = invoice.customer
    subscription = user.subscription_set.filter(is_active=True).order_by('-id').first()
    
    if not subscription:
        logger.error(f"Cannot provision: No active subscription for {user.email}")
        return False
        
    # Check if already provisioned to avoid duplicates
    if HostingAccount.objects.filter(user=user).exists():
        logger.info(f"Hosting account for {user.email} already exists.")
        return True

    username = generate_username(user.email)
    
    # Generate a secure random password for the client
    password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
    
    # Determine Hestia package based on the Plan
    plan_name = subscription.plan.name.lower()
    hestia_package = f"{plan_name}_pkg"  # e.g., "starter_pkg", "business_pkg"

    # 1. Call the HestiaCP API
    hestia = HestiaCP()
    result = hestia.create_user(
        username=username,
        password=password,
        email=user.email,
        package_name=hestia_package
    )

    # 2. If successful, save to database and send email
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
            
            # Send the Welcome Email with credentials
            send_provisioning_welcome(user, username, password)
            return True
    else:
        logger.error(f"Failed to provision hosting for {user.email}: {result.get('message')}")
        return False