# billing/services/paychangu.py
import logging
import re
import uuid

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# "INV-12" (old format) or "INV-12-a1b2c3d4" (one reference per payment attempt)
_REF_RE = re.compile(r'^INV-(\d+)(?:-[A-Za-z0-9]+)?$')


class PayChanguService:
    """
    Service layer for the PayChangu API: starting a payment and verifying it.
    """

    @staticmethod
    def _get_headers():
        return {
            'Accept': 'application/json',
            'Authorization': f'Bearer {settings.PAYCHANGU_SECRET_KEY}',
        }

    @staticmethod
    def new_tx_ref(invoice):
        """PayChangu requires a unique tx_ref for every transaction, so each
        attempt gets its own reference (a retry of the same invoice must not
        reuse the previous one)."""
        return f"INV-{invoice.id}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def invoice_id_from_ref(tx_ref):
        match = _REF_RE.match(tx_ref or '')
        return int(match.group(1)) if match else None

    @staticmethod
    def initiate_payment(invoice):
        """
        Starts a Standard Checkout payment and returns the checkout URL.

        Each attempt is stored as a pending Payment (gateway_reference =
        tx_ref). That record lets the webhook, the browser callback and the
        dashboard all find the invoice for a payment, whichever arrives first.
        """
        from billing.models import Payment  # local import avoids import cycles

        tx_ref = PayChanguService.new_tx_ref(invoice)
        payload = {
            "amount": int(invoice.total_mwk),
            "currency": "MWK",
            "email": invoice.customer.email,
            "first_name": invoice.customer.first_name,
            "last_name": invoice.customer.last_name,
            "callback_url": settings.PAYCHANGU_CALLBACK_URL,
            "return_url": settings.PAYCHANGU_RETURN_URL,
            "tx_ref": tx_ref,
            "customization": {
                "title": f"Pritech Host - Invoice {invoice.invoice_number}",
                "description": "Payment for hosting services.",
            },
        }

        try:
            response = requests.post(
                f"{settings.PAYCHANGU_BASE_URL}/payment",
                json=payload,
                headers=PayChanguService._get_headers(),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error("PayChangu API request failed: %s", e)
            return None

        if data.get('status') != 'success':
            logger.error("PayChangu initiation failed: %s", data)
            return None

        Payment.objects.create(
            invoice=invoice,
            amount_mwk=invoice.total_mwk,
            gateway_reference=tx_ref,
            status='pending',
            payment_method='',
        )
        return data['data']['checkout_url']

    @staticmethod
    def verify_payment(tx_ref):
        """
        SRS FR-16: verify with the gateway before marking an invoice paid.

        Returns the verified transaction data (a dict), or None when the
        payment is not successful. The top-level "status" of the response only
        says the API call worked; the payment result is data["status"].
        """
        try:
            response = requests.get(
                f"{settings.PAYCHANGU_BASE_URL}/verify-payment/{tx_ref}",
                headers=PayChanguService._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error("PayChangu verification failed for %s: %s", tx_ref, e)
            return None

        data = body.get('data') or {}
        if body.get('status') != 'success' or data.get('status') != 'success':
            logger.info("PayChangu: %s is not successful yet (%s).", tx_ref, data.get('status'))
            return None
        if data.get('tx_ref') != tx_ref:
            logger.error("PayChangu: reference mismatch for %s (got %s).", tx_ref, data.get('tx_ref'))
            return None
        return data