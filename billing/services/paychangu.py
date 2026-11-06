# billing/services/paychangu.py
import requests
import uuid
from django.conf import settings
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

class PayChanguService:
    """
    Service layer for interacting with the PayChangu API.
    Handles initiating transactions, verifying payments, and webhook validation.
    """
    
    @staticmethod
    def _get_headers():
        return {
            'Accept': 'application/json',
            'Authorization': f'Bearer {settings.PAYCHANGU_SECRET_KEY}'
        }

    @staticmethod
    def initiate_payment(invoice):
        """
        Initiates a Standard Checkout payment with PayChangu.
        Returns the checkout URL to redirect the user to.
        """
        tx_ref = str(uuid.uuid4())
        
        # PayChangu accepts the amount as an integer (e.g., 62500 for MWK 62,500)
        amount = int(invoice.total_mwk)
        
        payload = {
            "amount": amount,
            "currency": "MWK",
            "email": invoice.customer.email,
            "first_name": invoice.customer.first_name,
            "last_name": invoice.customer.last_name,
            "callback_url": settings.PAYCHANGU_CALLBACK_URL,
            "return_url": settings.PAYCHANGU_RETURN_URL,
            "tx_ref": tx_ref,
            "customization": {
                "title": f"Pritech Host - Invoice {invoice.invoice_number}",
                "description": "Payment for hosting services."
            },
            "meta": {
                "invoice_id": invoice.id
            }
        }

        try:
            response = requests.post(
                f"{settings.PAYCHANGU_BASE_URL}/payment",
                json=payload,
                headers=PayChanguService._get_headers()
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') == 'success':
                # Return the URL where the user can complete payment
                return data['data']['checkout_url']
            else:
                logger.error(f"PayChangu initiation failed: {data}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"PayChangu API request failed: {e}")
            return None

    @staticmethod
    def verify_payment(charge_id):
        """
        Verifies a payment using its charge_id.
        SRS FR-16: The system verifies the payment through the gateway's verify API
        before marking an invoice paid.
        """
        try:
            response = requests.get(
                f"{settings.PAYCHANGU_BASE_URL}/mobile-money/payments/{charge_id}/verify",
                headers=PayChanguService._get_headers()
            )
            response.raise_for_status()
            data = response.json()
            return data.get('status') == 'successful'
        except requests.exceptions.RequestException as e:
            logger.error(f"PayChangu verification failed: {e}")
            return False