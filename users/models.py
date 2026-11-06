from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """
    Custom user model for Pritech Host.
    SRS FR-81: Holds full name, profile picture, DOB, gender, phone number.
    SRS FR-82: Name, phone, DOB, gender required before first order.
    """
    phone_number = models.CharField(
        _("phone number"),
        max_length=20,
        blank=True,
        help_text=_("Primary contact number, e.g. +265 999 000 000")
    )
    date_of_birth = models.DateField(
        _("date of birth"),
        null=True,
        blank=True,
        help_text=_("Used for identification and age checks (SRS FR-84)")
    )
    GENDER_CHOICES = (
        ('M', _('Male')),
        ('F', _('Female')),
    )
    gender = models.CharField(
        _("gender"),
        max_length=1,
        choices=GENDER_CHOICES,
        blank=True
    )
    is_student_verified = models.BooleanField(
        _("student verified"),
        default=False,
        help_text=_("SRS FR-56: Set to true after staff verify student ID or school email.")
    )

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ['-date_joined']

    def __str__(self):
        return self.email or self.username


class CustomerProfile(models.Model):
    """
    Extended profile for each customer.
    SRS FR-4: Records customer type, contact details, billing details.
    """
    CUSTOMER_TYPES = (
        ('student', _('Student')),
        ('developer', _('Developer')),
        ('business', _('Small Business')),
        ('organisation', _('Organisation')),
    )
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile',
        verbose_name=_("user")
    )
    customer_type = models.CharField(
        _("customer type"),
        max_length=20,
        choices=CUSTOMER_TYPES,
        default='business'
    )
    company_name = models.CharField(
        _("company name"),
        max_length=255,
        blank=True
    )
    tin_vat = models.CharField(
        _("TIN / VAT"),
        max_length=50,
        blank=True,
        help_text=_("Tax Identification Number for invoicing")
    )
    address = models.TextField(
        _("address"),
        blank=True
    )
    # SRS FR-56: Student eligibility evidence
    student_id_document = models.FileField(
        _("student ID document"),
        upload_to='student_ids/',
        blank=True,
        null=True
    )
    student_verification_date = models.DateField(
        _("student verification date"),
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = _("Customer Profile")
        verbose_name_plural = _("Customer Profiles")

    def __str__(self):
        return f"{self.user.email} - {self.get_customer_type_display()}"