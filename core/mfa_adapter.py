# core/mfa_adapter.py

from allauth.mfa.adapter import DefaultMFAAdapter
from django.shortcuts import redirect
from django.urls import reverse


class StaffRequiredMFAAdapter(DefaultMFAAdapter):
    """
    Custom MFA adapter that forces staff users to enroll in MFA.
    When a staff user without MFA logs in, they are redirected
    to the TOTP activation page.
    """

    def is_mfa_enabled(self, user, action="login"):
        """
        Check if MFA is enabled for the user.
        Returns True if the user has any MFA method activated.
        """
        from allauth.mfa.models import Authenticator

        return Authenticator.objects.filter(user=user).exists()

    def is_mfa_required(self, user):
        """
        Return True if the user MUST have MFA.
        We require it for all staff members.
        """
        return user.is_staff or user.is_superuser

    def get_mfa_redirect_url(self, user, action):
        """
        Where to redirect if MFA is required but not enabled.
        """
        if self.is_mfa_required(user) and not self.is_mfa_enabled(user):
            # Redirect to the TOTP setup page
            return reverse("mfa_activate_totp")
        return super().get_mfa_redirect_url(user, action)