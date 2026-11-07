# billing/services/paychangu.py
import requests
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

class PayChanguService:
    """
    Service layer for interacting with the PayChangu API.
    Handles initiating transactions and verifying payments.
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
        # Tie the transaction directly to the invoice ID using a clear prefix.
        # PayChangu will send this back to us as the `reference` in the webhook.
        tx_ref = f"INV-{invoice.id}"
        
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
            }
        }

        try:
            response = requests.post(
                f"{settings.PAYCHANGU_BASE_URL}/payment",
                json=payload,
                headers=PayChanguService._get_headers(),
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') == 'success':
                return data['data']['checkout_url']
            else:
                logger.error(f"PayChangu initiation failed: {data}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"PayChangu API request failed: {e}")
            return None

    @staticmethod
    def verify_payment(tx_ref):
        """
        Verifies a payment using its tx_ref (e.g., INV-123).
        SRS FR-16: The system verifies the payment through the gateway's verify API
        before marking an invoice paid.
        """
        try:
            response = requests.get(
                f"{settings.PAYCHANGU_BASE_URL}/verify-payment/{tx_ref}",
                headers=PayChanguService._get_headers(),
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            # PayChangu returns {'status': 'success', 'data': {...}}
            return data.get('status') == 'success'
        except requests.exceptions.RequestException as e:
            logger.error(f"PayChangu verification failed for {tx_ref}: {e}")
            return False