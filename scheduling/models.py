from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Organization(models.Model):
    class Kind(models.TextChoices):
        COUNTY = "county", "County"
        ACADEMY = "academy", "State academy"

    name = models.CharField(max_length=140, unique=True)
    short_name = models.CharField(max_length=50)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("display_order", "name")

    def __str__(self):
        return self.name


class Course(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    minimum_instructors = models.PositiveSmallIntegerField(default=1)
    recommended_instructors = models.PositiveSmallIntegerField(default=1)
    instructor_intensive = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.code} — {self.name}"

    def clean(self):
        if self.recommended_instructors < self.minimum_instructors:
            raise ValidationError(
                {"recommended_instructors": "Recommended staffing cannot be below minimum staffing."}
            )


class Instructor(models.Model):
    class TravelPreference(models.TextChoices):
        CONTACT_ME = "contact", "Contact me as needed"
        LOCAL_ONLY = "local", "Home organization only"
        LIMITED = "limited", "Limited travel"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructor_profile",
    )
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    home_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="instructors",
    )
    travel_preference = models.CharField(
        max_length=16,
        choices=TravelPreference.choices,
        default=TravelPreference.CONTACT_ME,
    )
    travel_notes = models.CharField(max_length=250, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("last_name", "first_name")

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def is_regional(self):
        return self.home_organization.kind == Organization.Kind.ACADEMY

    def __str__(self):
        return self.full_name


class CourseAuthorization(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PENDING = "pending", "Pending verification"
        EXPIRED = "expired", "Expired"
        SUSPENDED = "suspended", "Suspended"

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="course_authorizations",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="instructor_authorizations",
    )
    can_lead = models.BooleanField(default=True)
    can_assist = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    effective_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="verified_course_authorizations",
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("instructor", "course"),
                name="unique_instructor_course_authorization",
            )
        ]
        ordering = ("course__name", "instructor__last_name")

    @property
    def is_current(self):
        today = timezone.localdate()
        return (
            self.status == self.Status.ACTIVE
            and (self.effective_date is None or self.effective_date <= today)
            and (self.expiration_date is None or self.expiration_date >= today)
        )

    def __str__(self):
        return f"{self.instructor} — {self.course.code}"


class TrainingEvent(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        TENTATIVE = "tentative", "Tentative"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Canceled"

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="events")
    host_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="training_events",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    location_name = models.CharField(max_length=180)
    address = models.CharField(max_length=250, blank=True)
    contact_name = models.CharField(max_length=140, blank=True)
    contact_email = models.EmailField(blank=True)
    acadis_registration_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_training_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.course.name} — {self.host_organization.short_name}"


class TrainingSession(models.Model):
    event = models.ForeignKey(
        TrainingEvent,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    location_override = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ("starts_at",)

    def clean(self):
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The session must end after it starts."})

    def __str__(self):
        return f"{self.event.course.code} — {self.starts_at:%b %d, %Y %I:%M %p}"


class InstructorAssignment(models.Model):
    class Role(models.TextChoices):
        LEAD = "lead", "Lead instructor"
        ASSISTANT = "assistant", "Assistant instructor"

    session = models.ForeignKey(
        TrainingSession,
        on_delete=models.CASCADE,
        related_name="instructor_assignments",
    )
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    confirmed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("session", "instructor"),
                name="unique_session_instructor_assignment",
            )
        ]
        ordering = ("session__starts_at", "role", "instructor__last_name")

    def clean(self):
        if not self.session_id or not self.instructor_id:
            return
        conflicts = InstructorAssignment.objects.filter(
            instructor=self.instructor,
            confirmed=True,
            session__event__status__in=(
                TrainingEvent.Status.PROPOSED,
                TrainingEvent.Status.TENTATIVE,
                TrainingEvent.Status.CONFIRMED,
            ),
            session__starts_at__lt=self.session.ends_at,
            session__ends_at__gt=self.session.starts_at,
        ).exclude(pk=self.pk)
        if conflicts.exists():
            raise ValidationError(
                "This instructor is already assigned to another session during this time."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.instructor} — {self.session}"


class AvailabilityBlock(models.Model):
    class Status(models.TextChoices):
        UNAVAILABLE = "unavailable", "Unavailable"
        TENTATIVE = "tentative", "Tentatively available"

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="availability_blocks",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=16, choices=Status.choices)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ("starts_at",)

    def clean(self):
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "Availability must end after it starts."})


class AssistanceRequest(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        OPEN = "open", "Open"
        FILLED = "filled", "Filled"
        CLOSED = "closed", "Closed"

    event = models.ForeignKey(
        TrainingEvent,
        on_delete=models.CASCADE,
        related_name="assistance_requests",
    )
    role = models.CharField(max_length=16, choices=InstructorAssignment.Role.choices)
    instructors_needed = models.PositiveSmallIntegerField(default=1)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_assistance_requests",
    )
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scheduling_audit_events",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    summary = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

# Create your models here.
