from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User, CustomerProfile


# --- Inlines ---

class CustomerProfileInline(TabularInline):
    """Shows the customer profile directly on the User edit page."""
    model = CustomerProfile
    can_delete = False
    verbose_name_plural = 'Customer Profile'
    fk_name = 'user'
    tab = True  # Renders as a tab within the User form


# --- Admin Classes ---

# Unregister the default Group admin so we can re-register it with Unfold styling.
# This is required for a consistent look (see Unfold docs).
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    """
    Custom user admin using Unfold's ModelAdmin.
    SRS FR-41: Staff can search customers and view plan, invoices, history.
    SRS FR-42: Role-based permissions (staff, read-only).
    """
    # Forms required for Unfold styling (password change, creation, change)
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    inlines = [CustomerProfileInline]

    list_display = (
        'email', 'first_name', 'last_name',
        'is_student_verified', 'is_staff', 'is_active'
    )
    list_filter = ('is_student_verified', 'is_staff', 'is_active', 'profile__customer_type')
    search_fields = ('email', 'first_name', 'last_name', 'phone_number')
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('email', 'username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'is_student_verified', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'username', 'password1', 'password2'),
        }),
    )

    actions = ['verify_students']

    @admin.action(description='Verify selected students')
    def verify_students(self, request, queryset):
        """SRS FR-56: Bulk-verify student accounts."""
        updated = queryset.update(is_student_verified=True)
        self.message_user(request, f'{updated} student(s) verified successfully.')


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    """Re-registered Group admin with Unfold styling."""
    pass


@admin.register(CustomerProfile)
class CustomerProfileAdmin(ModelAdmin):
    """Standalone view for CustomerProfile (useful for searching)."""
    list_display = ('user', 'customer_type', 'company_name', 'tin_vat', 'student_verification_date')
    list_filter = ('customer_type',)
    search_fields = ('user__email', 'company_name', 'tin_vat')
    readonly_fields = ('student_verification_date',)