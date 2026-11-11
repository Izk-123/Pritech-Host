"""
Core domain models.

Contents:
    - Ticket           : customer support tickets (SRS FR-34).
    - TicketReply      : threaded replies on a ticket (SRS FR-46).
    - SiteSettings     : admin-editable site content. Singleton.
    - LegalDocument    : versioned legal documents. SRS FR-80, NFR-14.
    - TermsAcceptance  : immutable audit log of legal consent. NFR-14, NFR-15.
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


# =============================================================================
# SUPPORT TICKETS (SRS FR-34, FR-46)
# =============================================================================

class Ticket(models.Model):
    """
    SRS FR-34: Customers can open and follow support tickets.

    Each ticket is a threaded conversation. The customer's opening
    message lives in ``message``; every subsequent message (customer
    or staff) lives in a related ``TicketReply`` row.

    Status lifecycle
    ----------------
        open              → just created, or customer replied; staff
                            needs to act.
        awaiting_customer → staff replied; waiting on the customer.
        closed            → resolved; reply form disabled for customers.
    """
    STATUS_CHOICES = (
        ('open',              'Open'),
        ('awaiting_customer', 'Awaiting Customer'),
        ('closed',            'Closed'),
    )
    PRIORITY_CHOICES = (
        ('low',    'Low'),
        ('normal', 'Normal'),
        ('high',   'High'),
    )

    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='tickets',
    )
    subject    = models.CharField(max_length=255)
    message    = models.TextField(
        help_text="The initial message from the customer.",
    )
    status     = models.CharField(
        max_length=25, choices=STATUS_CHOICES, default='open',
    )
    priority   = models.CharField(
        max_length=10, choices=PRIORITY_CHOICES, default='normal',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Newest activity first — matches what support and customers
        # both want to see.
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', '-updated_at']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"#{self.id} - {self.subject} ({self.user.email})"

    # -- Convenience properties (used in templates + API payloads) ----------

    @property
    def reply_count(self) -> int:
        """Total number of replies (customer + staff)."""
        return self.replies.count()

    @property
    def unread_customer_replies(self) -> int:
        """
        Number of staff replies the customer hasn't read yet.

        Only meaningful from the customer's perspective — this drives
        the red unread badge on the dashboard ticket list.
        """
        return self.replies.filter(
            is_staff_reply=True, read_by_customer=False,
        ).count()

    @property
    def last_activity(self):
        """Most recent timestamp (latest reply, or ticket creation)."""
        last = self.replies.order_by('-created_at').first()
        return last.created_at if last else self.created_at

    @property
    def last_message_preview(self) -> str:
        """Truncated snippet of the latest message (for list rows)."""
        last = self.replies.order_by('-created_at').first()
        text = last.message if last else self.message
        return text[:120] + ('…' if len(text) > 120 else '')


class TicketReply(models.Model):
    """
    SRS FR-46: A single reply in a support thread.

    The author can be the customer or a staff member. ``is_staff_reply``
    is captured at write time so historical replies keep their original
    semantics even if the author is later demoted from staff.
    """
    ticket = models.ForeignKey(
        Ticket, on_delete=models.CASCADE, related_name='replies',
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
    )
    is_staff_reply = models.BooleanField(
        default=False,
        help_text="Snapshot of author.is_staff at the time of writing.",
    )
    message = models.TextField()

    created_at       = models.DateTimeField(auto_now_add=True)
    read_by_customer = models.BooleanField(
        default=False,
        help_text="Only meaningful for staff replies.",
    )

    class Meta:
        ordering = ['created_at']       # chronological thread order
        indexes = [
            models.Index(fields=['ticket', 'created_at']),
        ]

    def __str__(self):
        who = 'Staff' if self.is_staff_reply else 'Customer'
        return (
            f"{who} reply on ticket #{self.ticket_id} "
            f"at {self.created_at:%d %b %Y %H:%M}"
        )


# =============================================================================
# SITE SETTINGS (singleton)
# =============================================================================

class SiteSettings(models.Model):
    """
    Singleton model for site-wide, admin-editable content.
    Never has more than one row (pk=1). Read via ``SiteSettings.load()``.
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

    Only one row per slug should have ``is_active=True`` at any time.
    Existing ``TermsAcceptance`` rows stay linked to the version they
    were accepted against, so you always have an audit trail.
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
        """Return the currently active document for a given slug."""
        return (
            cls.objects
            .filter(slug=slug, is_active=True)
            .order_by('-effective_date', '-id')
            .first()
        )

    def content_hash(self) -> str:
        """SHA-256 of the content, stored with acceptances."""
        return hashlib.sha256(self.content.encode('utf-8')).hexdigest()


class TermsAcceptance(models.Model):
    """Immutable audit log of legal consent per user per version."""
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