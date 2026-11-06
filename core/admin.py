from django.contrib import admin
from unfold.admin import ModelAdmin
from unfold.decorators import display
from .models import Ticket

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
        # Unfold's label=True renders the value as a styled badge
        return obj.get_status_display()