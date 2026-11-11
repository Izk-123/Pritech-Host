"""
Admin configuration for the core app.
"""
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display

from .models import (
    SiteSettings,
    Ticket,
    TicketReply,
    LegalDocument,
    TermsAcceptance,
    CookieConsent,
)


# ---------------------------------------------------------------------------
# Support tickets (SRS FR-34, FR-46)
# ---------------------------------------------------------------------------

class TicketReplyInline(TabularInline):
    """
    Reply composer embedded inside the ticket admin page.

    ``extra = 1`` gives staff one blank row to write a new reply. Saving
    the ticket form triggers ``TicketAdmin.save_formset`` which sets
    ``author`` to the logged-in staff user and ``is_staff_reply = True``
    — then the model's post_save signal handles status change and email.
    """
    model = TicketReply
    fk_name = 'ticket'
    extra = 1
    fields = ('author', 'is_staff_reply', 'message', 'read_by_customer', 'created_at')
    readonly_fields = ('created_at', 'read_by_customer')
    can_delete = False

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """Restrict the author dropdown to staff users only."""
        if db_field.name == 'author':
            kwargs['queryset'] = db_field.related_model.objects.filter(
                is_staff=True,
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    """Support inbox with inline reply composer."""
    list_display = (
        'id', 'user', 'subject', 'status_display',
        'priority', 'reply_count_display', 'updated_at',
    )
    list_filter = ('status', 'priority', 'created_at')
    search_fields = ('user__email', 'subject', 'message')
    readonly_fields = (
        'user', 'subject', 'message',
        'created_at', 'updated_at', 'closed_at',
    )
    inlines = [TicketReplyInline]
    date_hierarchy = 'created_at'

    fieldsets = (
        ("Ticket", {
            'fields': ('user', 'subject', 'message', 'created_at', 'updated_at'),
        }),
        ("Status", {
            'fields': ('status', 'priority', 'closed_at'),
        }),
    )

    actions = ['mark_closed', 'mark_open']

    @display(description='Status', label=True)
    def status_display(self, obj):
        return obj.get_status_display()

    @display(description='Replies')
    def reply_count_display(self, obj):
        return obj.reply_count

    def save_formset(self, request, form, formset, change):
        """
        Auto-populate ``author`` and ``is_staff_reply`` for every reply
        added through the admin.

        Also handles the case where staff edit an existing reply — they
        keep their original author snapshot instead of being reattributed.
        """
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, TicketReply):
                # Default author = the logged-in admin
                if not obj.author:
                    obj.author = request.user
                # Snapshot is_staff so historical replies keep their meaning
                # even if the author is later demoted.
                obj.is_staff_reply = bool(obj.author and obj.author.is_staff)
                obj.save()
        formset.save_m2m()

    @admin.action(description='Mark selected tickets as CLOSED')
    def mark_closed(self, request, queryset):
        count = queryset.update(status='closed', closed_at=timezone.now())
        self.message_user(request, f'{count} ticket(s) closed.')

    @admin.action(description='Mark selected tickets as OPEN')
    def mark_open(self, request, queryset):
        count = queryset.update(status='open', closed_at=None)
        self.message_user(request, f'{count} ticket(s) re-opened.')

    def has_add_permission(self, request):
        # Tickets are created by customers, never by staff
        return False


# ---------------------------------------------------------------------------
# Site-wide editable content (singleton)
# ---------------------------------------------------------------------------
@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    """Singleton admin for site-wide editable content."""
    fieldsets = (
        ("General", {
            'fields': (
                'site_name', 'contact_email', 'contact_phone',
                'physical_address', 'footer_tagline',
            ),
        }),
        ("Support", {
            'fields': ('support_hours', 'cpanel_url'),
        }),
        ("Home page — Hero", {
            'fields': (
                'hero_badge', 'hero_headline',
                'hero_subheadline', 'preview_features',
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
    """Manage versioned legal documents."""
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
                "Use HTML tags: <h2>, <h3>, <p>, <ul>/<li>. Do NOT paste Word documents."
            ),
        }),
    )

    @display(description='Version')
    def version_display(self, obj):
        return f"v{obj.version}"

    @display(description='Preview')
    def content_preview(self, obj):
        """
        Content is superuser-edited → trusted. mark_safe is correct here
        (format_html(arg) would HTML-escape the legal tags themselves).
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
        Enforce "only one active document per slug" without a DB-level
        constraint (which would block the natural versioning workflow).
        """
        if obj.is_active:
            LegalDocument.objects.filter(
                slug=obj.slug, is_active=True,
            ).exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)


@admin.register(TermsAcceptance)
class TermsAcceptanceAdmin(ModelAdmin):
    """Read-only audit log of legal acceptances — compliance artifact."""
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