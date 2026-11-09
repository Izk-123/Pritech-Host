"""
Core domain models.

Contents:
    - Ticket        : customer support tickets (SRS FR-34).
    - SiteSettings  : admin-editable site content (hero, contact info, support
                      hours, cPanel URL, footer, hero preview card). Singleton.
"""
from django.conf import settings
from django.core.cache import cache
from django.db import models


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
    Stores messages sent via the dashboard modal.
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

    Never has more than one row (pk=1). Read the current instance via
    ``SiteSettings.load()`` — it caches the object for 10 minutes and
    automatically populates the hero preview card on first use.

    A context processor (``core.context_processors.site_settings``) injects
    the current instance into every template as ``site``.
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

    # -- Singleton semantics ------------------------------------------------
    def save(self, *args, **kwargs):
        """Force pk=1 so there is only ever one row."""
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete('core_site_settings')

    @classmethod
    def load(cls):
        """
        Return the cached singleton, creating it (with default preview
        features) on first access.
        """
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