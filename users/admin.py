from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import User, CustomerProfile


# --- Inlines ---

class CustomerProfileInline(TabularInline):
    """Shows the customer profile directly on the User edit page."""
    model = CustomerProfile
    can_delete = False
    verbose_name_plural = 'Customer Profile'
    fk_name = 'user'
    tab = True


# --- Admin Classes ---

admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    """
    Custom user admin using Unfold's ModelAdmin.

    SRS FR-41: Staff can search customers and view plan, invoices, history.
    SRS FR-42: Role-based permissions.
    SRS NFR-9: 2FA status is visible for every staff account — a visual
               reminder that "no ✓ here" means a compromised password
               gives an attacker the whole business.
    """
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    inlines = [CustomerProfileInline]

    list_display = (
        'email', 'first_name', 'last_name',
        'mfa_status_display',      # ← NEW
        'is_student_verified', 'is_staff', 'is_active',
    )
    list_filter = ('is_student_verified', 'is_staff', 'is_active', 'profile__customer_type')
    search_fields = ('email', 'first_name', 'last_name', 'phone_number')
    ordering = ('-date_joined',)

    fieldsets = (
        (None, {'fields': ('email', 'username', 'password')}),
        ('Personal info', {
            'fields': ('first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender'),
        }),
        ('Permissions', {
            'fields': (
                'is_active', 'is_staff', 'is_superuser',
                'is_student_verified', 'groups', 'user_permissions',
            ),
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'username', 'password1', 'password2'),
        }),
    )

    actions = ['verify_students']

    # ------------------------------------------------------------------
    # 2FA status indicator (SRS NFR-9)
    # ------------------------------------------------------------------
    # Uses a lazy import of the Authenticator model because:
    #   1. Module-level import would fail if allauth.mfa isn't in
    #      INSTALLED_APPS yet (e.g., during the very first migrate).
    #   2. The model is only needed when rendering a list page — pulling
    #      it in at admin load time is wasted work.
    @display(description='2FA', label=True)
    def mfa_status_display(self, obj):
        """
        Show whether a user has enrolled in 2FA.

        Customers see '—' — we don't require 2FA for them (yet).
        Staff see either a green ✓ or a red ⚠, which is the fastest
        way to spot an account that can be compromised by a single
        password leak.
        """
        # Only staff and superusers need 2FA — the middleware enforces it.
        if not (obj.is_staff or obj.is_superuser):
            return '—'

        try:
            from allauth.mfa.models import Authenticator
            has_mfa = Authenticator.objects.filter(user=obj).exists()
        except (ImportError, LookupError):
            # allauth.mfa isn't installed yet — surface '?' so it's
            # obvious something is wrong without crashing the admin.
            return '?'

        return '✓ Enabled' if has_mfa else '⚠ Not set up'

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
    list_display = (
        'user', 'customer_type', 'company_name', 'tin_vat',
        'student_verification_date',
    )
    list_filter = ('customer_type',)
    search_fields = ('user__email', 'company_name', 'tin_vat')
    readonly_fields = ('student_verification_date',)