"""
Core domain models.

Contents:
    - Ticket           : customer support tickets (SRS FR-34).
    - SiteSettings     : admin-editable site content (hero, contact info,
                         support hours, cPanel URL, footer, hero preview
                         card). Singleton.
    - LegalDocument    : versioned legal documents (Terms, Privacy, AUP,
                         Refund). SRS FR-80, NFR-14.
    - TermsAcceptance  : immutable audit log of who accepted which
                         version, when, from what IP. NFR-14, NFR-15.
    - CookieConsent    : cookie banner consent audit log. NFR-14.
"""
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Defaults for SiteSettings.preview_features (the 3-row hero card on the home
# page). Used on first boot so the singleton is never empty.
# ---------------------------------------------------------------------------
DEFAULT_PREVIEW_FEATURES = [
    {"label": "Your website",    "value": "Online"},
    {"label": "SSL certificate", "value": "Active"},
    {"label": "Backups",         "value": "Protected"},
]


class Ticket(models.Model):
    """
    SRS FR-34: Customers can open and follow support tickets.
    """
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('closed', 'Closed'),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tickets',
    )
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"#{self.id} - {self.subject} ({self.user.email})"


class SiteSettings(models.Model):
    """
    Singleton model for site-wide, admin-editable content.

    Never has more than one row (pk=1). Read via ``SiteSettings.load()``
    — it caches for 10 minutes and populates the hero preview card on
    first use.
    """
    # -- General ------------------------------------------------------------
    site_name = models.CharField(max_length=100, default="Pritech Host")
    contact_email = models.EmailField(default="info@pritechmw.com")
    contact_phone = models.CharField(max_length=50, default="+265 996 110 336")
    physical_address = models.CharField(
        max_length=255,
        blank=True,
        default="Area 51/24 Kanengo, Lilongwe, Malawi",
    )

    # -- Support ------------------------------------------------------------
    support_hours = models.CharField(
        max_length=200,
        default="Monday to Friday, 8:00 to 17:00",
    )
    cpanel_url = models.URLField(
        default="https://sme.pritechmw.com:8083",
        help_text="URL customers use to log into their hosting control panel.",
    )

    # -- Home page hero -----------------------------------------------------
    hero_badge = models.CharField(
        max_length=200,
        default="Local hosting, paid in kwacha",
    )
    hero_headline = models.CharField(
        max_length=300,
        default="Hosting you can pay for with mobile money.",
    )
    hero_subheadline = models.TextField(
        default=(
            "Websites, email and apps with local support. Pay with Airtel "
            "Money, TNM Mpamba or card, and go live without a dollar card."
        ),
    )
    preview_features = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'List of {"label": "...", "value": "..."} objects rendered in the '
            'hero preview card on the home page.'
        ),
    )

    # -- Footer -------------------------------------------------------------
    footer_tagline = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "Site Settings"
        verbose_name_plural = "Site Settings"

    def save(self, *args, **kwargs):
        """Force pk=1 so there is only ever one row."""
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete('core_site_settings')

    @classmethod
    def load(cls):
        """Return the cached singleton, creating it on first access."""
        cached = cache.get('core_site_settings')
        if cached is not None:
            return cached

        obj, _created = cls.objects.get_or_create(pk=1)
        if not obj.preview_features:
            obj.preview_features = DEFAULT_PREVIEW_FEATURES
            obj.save(update_fields=['preview_features'])

        cache.set('core_site_settings', obj, 600)
        return obj

    def __str__(self):
        return f"{self.site_name} — Site Settings"


# =============================================================================
# LEGAL DOCUMENTS (SRS FR-80, NFR-14)
# =============================================================================

class LegalDocument(models.Model):
    """
    Versioned legal document.

    When you edit the content, bump the version and create a new row
    (or deactivate the old one and save a new active version — see
    ``LegalDocumentAdmin.save_model``). Existing ``TermsAcceptance``
    rows stay linked to the version they were accepted against, so you
    always have an audit trail of exactly what a customer agreed to.

    Only one row per slug should have ``is_active=True`` at any time.
    """
    SLUG_CHOICES = (
        ('terms',   'Terms of Service'),
        ('privacy', 'Privacy Policy'),
        ('aup',     'Acceptable Use Policy'),
        ('refund',  'Refund Policy'),
    )

    slug           = models.CharField(max_length=50, choices=SLUG_CHOICES)
    title          = models.CharField(max_length=200)
    version        = models.CharField(
        max_length=20,
        help_text="e.g. 1.0, 1.1, 2.0. Bump this whenever you edit the content.",
    )
    content        = models.TextField(
        help_text="HTML content. Use <h2>, <h3>, <p>, <ul> for structure.",
    )
    effective_date = models.DateField(default=timezone.localdate)
    is_active      = models.BooleanField(
        default=True,
        help_text="Only one row per slug should be active.",
    )
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Legal Document"
        verbose_name_plural = "Legal Documents"
        ordering = ['slug', '-effective_date']
        unique_together = ('slug', 'version')

    def __str__(self):
        return f"{self.get_slug_display()} v{self.version}"

    @classmethod
    def current(cls, slug):
        """
        Return the currently active document for a given slug.

        Uses ``-effective_date, -id`` as a stable tiebreaker if two
        documents happen to share an effective_date (which shouldn't
        happen if admin is used correctly, but this defends against it).
        """
        return (
            cls.objects
            .filter(slug=slug, is_active=True)
            .order_by('-effective_date', '-id')
            .first()
        )

    def content_hash(self) -> str:
        """
        SHA-256 of the content, stored with acceptances so we can prove
        later that the doc hasn't been edited behind the customer's back.
        """
        return hashlib.sha256(self.content.encode('utf-8')).hexdigest()


class TermsAcceptance(models.Model):
    """
    Records that a user accepted a specific legal document version.

    Immutable audit log — never modified or deleted manually. Even when
    an admin edits a legal document, existing rows stay pointing to the
    old version so the historical consent remains provable.
    """
    user            = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='terms_acceptances',
    )
    document        = models.ForeignKey(LegalDocument, on_delete=models.PROTECT)
    accepted_at     = models.DateTimeField(auto_now_add=True)
    ip_address      = models.GenericIPAddressField(null=True, blank=True)
    user_agent      = models.TextField(blank=True)
    content_hash    = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256 of the document content at acceptance time.",
    )
    context         = models.CharField(
        max_length=30, default='signup',
        help_text="Where acceptance happened: signup, checkout, profile.",
    )

    class Meta:
        verbose_name = "Terms Acceptance"
        verbose_name_plural = "Terms Acceptances"
        ordering = ['-accepted_at']
        indexes = [
            models.Index(fields=['user', 'document']),
        ]

    def __str__(self):
        return (
            f"{self.user.email} accepted {self.document} "
            f"on {self.accepted_at:%d %b %Y}"
        )


class CookieConsent(models.Model):
    """
    Records cookie banner consent. One row per session_key — repeated
    banner acceptances from the same session update the existing row.

    Categories mirror the banner's checkboxes:
        {"necessary": true, "analytics": false, "marketing": false}
    """
    session_key    = models.CharField(max_length=64, db_index=True)
    user           = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
    )
    accepted_at    = models.DateTimeField(auto_now_add=True)
    ip_address     = models.GenericIPAddressField(null=True, blank=True)
    user_agent     = models.TextField(blank=True)
    categories     = models.JSONField(
        default=dict,
        help_text='e.g. {"necessary": true, "analytics": false, "marketing": false}',
    )

    class Meta:
        verbose_name = "Cookie Consent"
        verbose_name_plural = "Cookie Consents"
        ordering = ['-accepted_at']

    def __str__(self):
        return f"Consent {self.session_key[:8]}… at {self.accepted_at:%d %b %Y}"