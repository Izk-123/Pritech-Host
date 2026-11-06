from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import Project


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    """
    SRS FR-63, FR-66: Manage website build projects.
    """
    list_display = ('customer', 'plan_name', 'stage_display', 'staff_owner', 'requested_domain', 'domain_is_free_com')
    list_filter = ('stage', 'staff_owner', 'domain_is_free_com')
    search_fields = ('customer__email', 'requested_domain')
    list_editable = ('staff_owner',)
    autocomplete_fields = ('customer', 'subscription', 'staff_owner')

    @display(description='Plan', ordering='subscription__plan__name')
    def plan_name(self, obj):
        return obj.subscription.plan.name if obj.subscription else '—'

    @display(description='Stage', ordering='stage', label=True)
    def stage_display(self, obj):
        # Unfold's label=True will render the value as a styled badge
        return obj.get_stage_display()