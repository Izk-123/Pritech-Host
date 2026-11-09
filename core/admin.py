"""
Admin configuration for the core app.
"""
from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import SiteSettings, Ticket


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------
@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    """
    Admin interface to view and respond to customer support tickets.
    """
    list_display = ('id', 'user', 'subject', 'status_display', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'subject', 'message')
    readonly_fields = ('user', 'subject', 'message', 'created_at')

    @display(description='Status', label=True)
    def status_display(self, obj):
        # Unfold's label=True renders the value as a styled badge.
        return obj.get_status_display()


# ---------------------------------------------------------------------------
# Site-wide editable content (singleton)
# ---------------------------------------------------------------------------
@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    """
    Singleton admin for site-wide content: hero text, contact info,
    support hours, cPanel URL, footer tagline and hero preview card.
    """
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

    # Prevent creating a second row.
    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    # Prevent accidental deletion — the site depends on this singleton.
    def has_delete_permission(self, request, obj=None):
        return False