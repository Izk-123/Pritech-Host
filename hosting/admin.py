from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display

from .models import HostingAccount


@admin.register(HostingAccount)
class HostingAccountAdmin(ModelAdmin):
    """
    SRS FR-24, FR-27: View HestiaCP accounts.
    Designed for support staff (read-only usage).
    """
    list_display = ('user', 'hestia_username', 'hestia_package_name', 'disk_usage_display', 'bandwidth_usage_display', 'is_active')
    list_filter = ('is_active', 'hestia_package_name')
    search_fields = ('user__email', 'hestia_username')
    readonly_fields = ('disk_used_mb', 'bandwidth_used_mb', 'is_active')
    autocomplete_fields = ('user', 'subscription')

    @display(description='Disk Usage')
    def disk_usage_display(self, obj):
        return f"{obj.disk_used_mb} MB"

    @display(description='Bandwidth Usage')
    def bandwidth_usage_display(self, obj):
        return f"{obj.bandwidth_used_mb} MB"