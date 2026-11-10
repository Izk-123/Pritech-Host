"""
Service layer for the HestiaCP API.

All methods return a dict of shape ``{"status": "success" | "error", ...}``
and are safe to retry. When ``settings.HESTIA_MOCK`` is True the calls
are logged and immediately succeed — perfect for dev and CI.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class HestiaCP:
    """Thin synchronous client for the HestiaCP REST API."""

    def __init__(self):
        self.base_url = f"{settings.HESTIA_HOST}/api/v1"
        self.headers = {
            'Authorization': f'Bearer {settings.HESTIA_API_KEY}',
            'Accept': 'application/json',
        }

    def _is_mock(self) -> bool:
        return getattr(settings, 'HESTIA_MOCK', True)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    def create_user(self, username, password, email, package_name):
        """Create a new hosting account (SRS FR-24)."""
        if self._is_mock():
            logger.info(f"[MOCK HESTIA] Created user: {username}, package: {package_name}")
            return {"status": "success", "message": "User created (mock)"}

        payload = {
            "username": username,
            "password": password,
            "email": email,
            "package": package_name,
        }
        try:
            r = requests.post(
                f"{self.base_url}/user", json=payload,
                headers=self.headers, timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "success":
                return {"status": "success", "message": "User created successfully"}
            return {"status": "error", "message": data.get("error", "Unknown API error")}
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia create_user failed: {e}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Suspend / Unsuspend
    # ------------------------------------------------------------------
    def suspend_user(self, username):
        """Suspend an account (SRS FR-19, called at day +8 past expiry)."""
        if self._is_mock():
            logger.info(f"[MOCK HESTIA] Suspended user: {username}")
            return {"status": "success"}

        try:
            r = requests.post(
                f"{self.base_url}/user/{username}/suspend",
                headers=self.headers, timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia suspend_user failed for {username}: {e}")
            return {"status": "error", "message": str(e)}

    def unsuspend_user(self, username):
        """
        Reactivate an account after a renewal payment.
        Called from the renewal handler when a suspended sub is paid.
        """
        if self._is_mock():
            logger.info(f"[MOCK HESTIA] Unsuspended user: {username}")
            return {"status": "success"}

        try:
            r = requests.post(
                f"{self.base_url}/user/{username}/unsuspend",
                headers=self.headers, timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia unsuspend_user failed for {username}: {e}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------
    def delete_user(self, username):
        """
        Permanently delete a hosting account.

        IRREVERSIBLE — deletes all websites, mailboxes and databases
        owned by the user. Only called by the dunning task after the
        full 45-day grace window has elapsed.
        """
        if self._is_mock():
            logger.info(f"[MOCK HESTIA] Deleted user: {username}")
            return {"status": "success"}

        try:
            r = requests.delete(
                f"{self.base_url}/user/{username}",
                headers=self.headers, timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia delete_user failed for {username}: {e}")
            return {"status": "error", "message": str(e)}