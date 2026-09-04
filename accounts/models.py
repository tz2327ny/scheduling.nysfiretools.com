from django.conf import settings
from django.db import models


class AccountProfile(models.Model):
    """Site-wide access state kept separate from optional Scheduler enrollment."""

    class AccessStatus(models.TextChoices):
        PENDING = "pending", "Pending approval"
        ACTIVE = "active", "Active"
        REJECTED = "rejected", "Not approved"
        SUSPENDED = "suspended", "Suspended"

    class SignupSource(models.TextChoices):
        GENERAL = "general", "NYSFIRETOOLS protected tools"
        SCHEDULER = "scheduler", "Fire Training Scheduler"
        ADMIN = "admin", "Administrator-created"
        LEGACY = "legacy", "Existing account"

    class SchedulerStatus(models.TextChoices):
        NOT_ENROLLED = "not_enrolled", "Not enrolled"
        PENDING = "pending", "Enrollment pending"
        ACTIVE = "active", "Enrolled"
        REJECTED = "rejected", "Enrollment not approved"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="nysfiretools_profile",
    )
    access_status = models.CharField(
        max_length=16,
        choices=AccessStatus.choices,
        default=AccessStatus.PENDING,
        db_index=True,
    )
    signup_source = models.CharField(
        max_length=16,
        choices=SignupSource.choices,
        default=SignupSource.GENERAL,
    )
    scheduler_status = models.CharField(
        max_length=16,
        choices=SchedulerStatus.choices,
        default=SchedulerStatus.NOT_ENROLLED,
        db_index=True,
    )
    organization = models.ForeignKey(
        "scheduling.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="account_profiles",
    )
    requested_organization_name = models.CharField(max_length=180, blank=True)
    access_reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.user} — {self.get_access_status_display()}"

    @property
    def organization_name(self):
        return self.organization.name if self.organization_id else self.requested_organization_name


class UserOrganizationRole(models.Model):
    """Scopes an administrator to the organization they are allowed to manage."""

    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Organization administrator"
        VIEWER = "viewer", "Read-only viewer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_roles",
    )
    organization = models.ForeignKey(
        "scheduling.Organization",
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.CharField(max_length=24, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "organization", "role"),
                name="unique_user_organization_role",
            )
        ]
        ordering = ("organization__name", "user__last_name", "user__first_name")

    def __str__(self):
        return f"{self.user} — {self.organization} ({self.get_role_display()})"


class InstructorApplication(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending State approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Not approved"

    class TravelPreference(models.TextChoices):
        CONTACT_ME = "contact", "Contact me as needed"
        LOCAL_ONLY = "local", "Home organization only"
        LIMITED = "limited", "Limited travel"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="instructor_application",
    )
    sfi_number = models.CharField("SFI, CFI, or MFI number", max_length=30, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    home_organization = models.ForeignKey(
        "scheduling.Organization",
        on_delete=models.PROTECT,
        related_name="instructor_applications",
    )
    requested_courses = models.ManyToManyField(
        "scheduling.Course",
        blank=True,
        related_name="instructor_applications",
    )
    travel_preference = models.CharField(max_length=16, choices=TravelPreference.choices)
    travel_notes = models.CharField(max_length=250, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    instructor = models.OneToOneField(
        "scheduling.Instructor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="registration_application",
    )
    applied_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_instructor_applications",
    )

    class Meta:
        ordering = ("-applied_at",)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email} — {self.get_status_display()}"


class ExternalAccessCode(models.Model):
    """Short-lived, single-use authorization code for another NYSFIRETOOLS service."""

    token_hash = models.CharField(max_length=64, unique=True)
    state = models.CharField(max_length=200)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="external_access_codes",
    )
    return_path = models.CharField(max_length=500, default="/burn-plans")
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"External access for {self.user}"
