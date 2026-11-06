from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import User
from billing.models import Subscription


class Project(models.Model):
    """
    A managed website build project.
    SRS FR-63: Created when a package order is placed.
    SRS FR-64: Work starts only after the 25% deposit is verified.
    SRS FR-66: Records the requested domain and whether it's a free .com/.mw.
    """
    STAGES = (
        ('deposit', _('Deposit Pending')),
        ('content', _('Content Received')),
        ('design', _('Designing')),
        ('approval', _('Design Approval')),
        ('launch', _('Ready to Launch')),
        ('live', _('Live')),
    )
    customer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name=_("customer"),
        related_name='projects'
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name=_("subscription"),
        help_text=_("The subscription that funds this project.")
    )
    stage = models.CharField(
        _("stage"),
        max_length=20,
        choices=STAGES,
        default='deposit',
        help_text=_("Tracks progress from deposit to launch.")
    )
    staff_owner = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("staff owner"),
        related_name='managed_projects',
        help_text=_("The Pritech staff member responsible for this project.")
    )
    requested_domain = models.CharField(
        _("requested domain"),
        max_length=255,
        blank=True,
        help_text=_("The domain the customer requested (e.g. yourbusiness.com).")
    )
    # SRS FR-66: Free domain logic
    domain_is_free_com = models.BooleanField(
        _("free .com domain"),
        default=False,
        help_text=_("True if a free .com domain is included in the package.")
    )
    # NEW: Snapshot the price so milestone calculations remain accurate
    total_price_mwk = models.DecimalField(
        _("total price (MWK)"), 
        max_digits=12, 
        decimal_places=2, 
        null=True, 
        blank=True,
        help_text="Snapshot of the package price at time of order."
    )

    class Meta:
        verbose_name = _("Project")
        verbose_name_plural = _("Projects")
        ordering = ['-customer__date_joined']

    def __str__(self):
        return f"{self.customer.email} - {self.subscription.plan.name if self.subscription else 'No plan'} ({self.get_stage_display()})"