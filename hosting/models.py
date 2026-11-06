from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import User
from billing.models import Subscription


class HostingAccount(models.Model):
    """
    Links a customer to their HestiaCP hosting account.
    SRS FR-24: Created after verified first payment.
    SRS FR-27: Each customer is isolated in their own Linux user.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        verbose_name=_("user"),
        help_text=_("The customer who owns this hosting account.")
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name=_("subscription"),
        help_text=_("The active subscription this account belongs to.")
    )
    hestia_username = models.CharField(
        _("Hestia username"),
        max_length=50,
        unique=True,
        blank=True,
        help_text=_("The Linux user created in HestiaCP.")
    )
    hestia_package_name = models.CharField(
        _("Hestia package"),
        max_length=50,
        blank=True,
        help_text=_("The Hestia package assigned (e.g. business_pkg).")
    )
    disk_used_mb = models.PositiveIntegerField(
        _("disk used (MB)"),
        default=0,
        help_text=_("Updated from HestiaCP API.")
    )
    bandwidth_used_mb = models.PositiveIntegerField(
        _("bandwidth used (MB)"),
        default=0,
        help_text=_("Updated from HestiaCP API.")
    )
    is_active = models.BooleanField(
        _("active"),
        default=False,
        help_text=_("True once provisioning is complete and the account is live.")
    )

    class Meta:
        verbose_name = _("Hosting Account")
        verbose_name_plural = _("Hosting Accounts")
        ordering = ['-user__date_joined']

    def __str__(self):
        return f"{self.user.email} - {self.hestia_username or 'Pending provisioning'}"