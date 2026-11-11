"""
Core middleware for the Pritech Host platform.

StaffMFAEnforcementMiddleware (SRS NFR-9)
-----------------------------------------
Staff and superusers MUST have TOTP 2FA enabled. If they don't, they
are redirected to the TOTP activation page on their next request.

Exemptions
~~~~~~~~~~
    /admin/            — superusers need to be able to bootstrap. Admin
                         login is protected by Django auth + axes; staff
                         without MFA land in the admin where the "2FA"
                         column in Users shows a red warning.

    /accounts/2fa/     — the enrollment flow itself, obviously.
    /accounts/logout/  — so staff can log out even without MFA.
    /static/ /media/   — served by Nginx; never reach Django anyway.

Why middleware, not allauth adapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
allauth's MFA adapter API is about *behaviour* (how the QR code renders,
what the TOTP issuer label is). It has no "force enrollment" hook —
``is_mfa_enabled(user)`` is meant to reflect whether they've actually
enrolled, and lying about that breaks the login flow (the TOTP step
asks for a code that doesn't exist). Middleware that runs AFTER login
is the only clean way to gate non-MFA users.
"""
import logging

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

logger = logging.getLogger(__name__)


# Paths that are NEVER blocked by the MFA gate, even for staff.
# Add new safe paths here if you need staff to reach something before
# enrolling in MFA.
_MFA_EXEMPT_PREFIXES = (
    '/admin/',
    '/accounts/2fa/',
    '/accounts/logout/',
    '/accounts/login/',
    '/accounts/password/',
    '/static/',
    '/media/',
)


class StaffMFAEnforcementMiddleware:
    """
    Redirect un-enrolled staff to the TOTP activation page.

    Dev mode
    --------
    If ``settings.MFA_ENFORCE_FOR_STAFF`` is False (the dev default),
    this middleware is a no-op — you can develop without setting up
    TOTP every time you recreate the admin user.

    Prod mode
    ---------
    deploy_hosting.sh sets ``MFA_ENFORCE_FOR_STAFF=True`` in .env.
    Every non-exempt request from an un-enrolled staff user is
    redirected to /accounts/2fa/totp/activate/. They complete enrollment
    once, then never see the redirect again.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enforce = getattr(settings, 'MFA_ENFORCE_FOR_STAFF', False)

    def __call__(self, request):
        if not self.enforce:
            return self.get_response(request)

        # Only staff / superusers are forced. Customers don't need 2FA.
        user = getattr(request, 'user', None)
        if not (user and user.is_authenticated):
            return self.get_response(request)

        if not (user.is_staff or user.is_superuser):
            return self.get_response(request)

        # Exempt the enrollment flow + admin itself (see docstring).
        if self._is_exempt(request.path):
            return self.get_response(request)

        # Skip if they've already enrolled.
        if self._has_mfa(user):
            return self.get_response(request)

        # Redirect to the TOTP activation page. Add ?next=<path> so
        # they come back to wherever they were trying to go after
        # enrollment completes.
        activate_url = reverse('mfa_activate_totp')
        logger.info(
            f"[MFA] Forcing TOTP enrollment for {user.email} "
            f"(was on {request.path})"
        )
        return redirect(f'{activate_url}?next={request.path}')

    # -- helpers -------------------------------------------------------

    def _is_exempt(self, path):
        return any(path.startswith(prefix) for prefix in _MFA_EXEMPT_PREFIXES)

    def _has_mfa(self, user):
        """
        Return True if the user has any enrolled authenticator.

        Wrapped in try/except so a DB hiccup doesn't lock staff out —
        on error we assume they DO have MFA (fail-open here, because
        locking staff out of the entire site is worse than letting
        them through to be caught by the TOTP prompt on login).
        """
        try:
            from allauth.mfa.models import Authenticator
            return Authenticator.objects.filter(user=user).exists()
        except Exception as exc:
            logger.warning(
                f"[MFA] Could not check MFA status for {user.email}: {exc}"
            )
            return True