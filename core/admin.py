"""
Admin configuration for the core app.
"""
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import (
    SiteSettings,
    Ticket,
    LegalDocument,
    TermsAcceptance,
    CookieConsent,
)


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------
@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    """SRS FR-34: view and track customer support tickets."""
    list_display = ('id', 'user', 'subject', 'status_display', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'subject', 'message')
    readonly_fields = ('user', 'subject', 'message', 'created_at')

    @display(description='Status', label=True)
    def status_display(self, obj):
        return obj.get_status_display()


# ---------------------------------------------------------------------------
# Site-wide editable content (singleton)
# ---------------------------------------------------------------------------
@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    """Singleton admin for site-wide editable content."""
    fieldsets = (
        ("General", {
            'fields': (
                'site_name',
                'contact_email',
                'contact_phone',
                'physical_address',
                'footer_tagline',
            ),
        }),
        ("Support", {
            'fields': (
                'support_hours',
                'cpanel_url',
            ),
        }),
        ("Home page — Hero", {
            'fields': (
                'hero_badge',
                'hero_headline',
                'hero_subheadline',
                'preview_features',
            ),
        }),
    )

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


# =============================================================================
# LEGAL DOCUMENTS
# =============================================================================

@admin.register(LegalDocument)
class LegalDocumentAdmin(ModelAdmin):
    """
    Manage versioned legal documents.

    Workflow to publish a new version:
        1. Edit the active document → bump `version` → set `is_active` True
        2. Save. ``save_model`` automatically deactivates any other
           active row for the same slug.

    Or, to preserve the old version explicitly:
        1. Create a NEW LegalDocument row with the new content
        2. Set `is_active=True`. On save, the old active row is
           deactivated automatically but its content is preserved.
    """
    list_display = ('slug', 'title', 'version_display', 'effective_date', 'is_active')
    list_filter = ('slug', 'is_active')
    search_fields = ('title', 'content')
    readonly_fields = ('created_at', 'content_preview')

    fieldsets = (
        ("Document", {
            'fields': ('slug', 'title', 'version', 'effective_date', 'is_active'),
        }),
        ("Content", {
            'fields': ('content', 'content_preview'),
            'description': (
                "Use HTML tags: <h2> for section headings, <h3> for subsections, "
                "<p> for paragraphs, <ul>/<li> for lists. Do NOT paste Word documents."
            ),
        }),
    )

    @display(description='Version')
    def version_display(self, obj):
        return f"v{obj.version}"

    @display(description='Preview')
    def content_preview(self, obj):
        """
        Render the HTML so admins can see what the page will look like.

        NOTE: content is trusted (edited only by superusers), so mark_safe
        is correct here. Passing the raw string to format_html() would
        escape the HTML and display tags as literal text.
        """
        if not obj.content:
            return "—"
        return format_html(
            '<div style="max-height:300px;overflow-y:auto;border:1px solid #ddd;'
            'padding:12px;background:#fafafa;border-radius:6px;">{}</div>',
            mark_safe(obj.content),
        )

    def save_model(self, request, obj, form, change):
        """
        When saving an active document, deactivate all other active rows
        of the same slug. This enforces the "only one active per slug"
        invariant without a DB-level constraint (which would block the
        natural workflow of publishing a new version).
        """
        if obj.is_active:
            LegalDocument.objects.filter(
                slug=obj.slug, is_active=True,
            ).exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(ModelAdmin):
    """
    Read-only audit log of legal acceptances.

    No add, no change, no delete — this is a compliance artifact.
    If a record is wrong, it should be superseded by a new acceptance,
    not edited.
    """
    list_display = ('user', 'document', 'accepted_at', 'context', 'ip_address')
    list_filter = ('document__slug', 'context', 'accepted_at')
    search_fields = ('user__email', 'ip_address')
    readonly_fields = (
        'user', 'document', 'accepted_at', 'ip_address',
        'user_agent', 'content_hash', 'context',
    )
    date_hierarchy = 'accepted_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CookieConsent)
class CookieConsentAdmin(ModelAdmin):
    """Read-only audit log of cookie banner consent."""
    list_display = ('session_key', 'user', 'accepted_at', 'categories')
    list_filter = ('accepted_at',)
    search_fields = ('session_key', 'user__email')
    readonly_fields = (
        'session_key', 'user', 'accepted_at', 'ip_address',
        'user_agent', 'categories',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False