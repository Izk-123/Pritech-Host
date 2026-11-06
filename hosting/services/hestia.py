import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class HestiaCP:
    """Service layer for interacting with the HestiaCP API."""
    
    def __init__(self):
        self.base_url = f"{settings.HESTIA_HOST}/api/v1"
        self.headers = {
            'Authorization': f'Bearer {settings.HESTIA_API_KEY}',
            'Accept': 'application/json'
        }

    def create_user(self, username, password, email, package_name):
        """Creates a new hosting account on HestiaCP."""
        
        # MOCK MODE: For local testing without a real server
        if getattr(settings, 'HESTIA_MOCK', False):
            logger.info(f"[MOCK HESTIA] Created user: {username}, package: {package_name}")
            return {"status": "success", "message": "User created (mock)"}

        # REAL API CALL
        payload = {
            "username": username,
            "password": password,
            "email": email,
            "package": package_name
        }
        
        try:
            response = requests.post(
                f"{self.base_url}/user", 
                json=payload, 
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") == "success":
                return {"status": "success", "message": "User created successfully"}
            else:
                return {"status": "error", "message": data.get("error", "Unknown API error")}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia API error: {e}")
            return {"status": "error", "message": str(e)}

    def suspend_user(self, username):
        """Suspends an account."""
        if getattr(settings, 'HESTIA_MOCK', False):
            logger.info(f"[MOCK HESTIA] Suspended user: {username}")
            return {"status": "success"}

        try:
            response = requests.post(
                f"{self.base_url}/user/{username}/suspend", 
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Hestia API error: {e}")
            return {"status": "error", "message": str(e)}