"""
Signal handlers for the core app.

Currently: records legal acceptance when a user signs up via allauth
(email or Google SSO). Checkout-time acceptance is recorded explicitly
inside ``CheckoutView.form_valid`` — that's a separate consent event
with its own context.

Why a signal instead of a form override: allauth owns the signup form
class, and subclassing it to add a BooleanField is more invasive. The
signal fires after the user object is created, which is exactly when
we want to log the acceptance. If signup fails (duplicate email, weak
password, etc.) the signal does NOT fire — so no false records are
created.
"""
from allauth.account.signals import user_signed_up
from django.dispatch import receiver

from .models import LegalDocument, TermsAcceptance


@receiver(user_signed_up)
def record_signup_acceptance(request, user, **kwargs):
    """
    Record acceptance of Terms, Privacy and AUP at signup time.

    ``request`` may be None in rare edge cases (e.g. certain programmatic
    flows) — in that case we still record the acceptance with a null
    IP, which is better than no record at all for compliance purposes.
    """
    # Lazy imports to avoid circular-import issues at Django startup
    # (apps.py imports this module during app init, before views is loaded).
    from .views import _get_client_ip

    client_ip = _get_client_ip(request) if request else None
    user_agent = (
        request.META.get('HTTP_USER_AGENT', '')[:1000]
        if request else ''
    )

    for slug in ('terms', 'privacy', 'aup'):
        doc = LegalDocument.current(slug)
        if doc:
            TermsAcceptance.objects.create(
                user=user,
                document=doc,
                context='signup',
                ip_address=client_ip,
                user_agent=user_agent,
                content_hash=doc.content_hash(),
            )