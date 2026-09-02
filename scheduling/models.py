from datetime import datetime, time, timedelta

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
    record_number = models.CharField("Course record number", max_length=30, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    number_of_units = models.CharField("Number of units", max_length=30, blank=True)
    student_contact_hours = models.CharField(max_length=30, blank=True)
    instructor_requirements = models.TextField(
        blank=True,
        help_text="Primary and additional instructor requirements by unit.",
    )
    safety_officer_requirements = models.TextField(blank=True)
    ems_requirements = models.TextField("EMS requirements", blank=True)
    admin_time = models.TextField(blank=True)
    county_hours_or_program_charge = models.TextField(blank=True)
    completion_type = models.CharField(max_length=40, blank=True)
    instructional_method = models.TextField(blank=True)
    class_size = models.CharField(max_length=40, blank=True)
    prerequisites = models.TextField(blank=True)
    in_service_hours = models.CharField("In-service hours / CEU credit", max_length=60, blank=True)
    national_certification = models.CharField(max_length=60, blank=True)
    course_version = models.CharField(max_length=40, blank=True)
    template_start_date = models.CharField(max_length=40, blank=True)
    template_end_date = models.CharField(max_length=40, blank=True)
    matrix_source = models.CharField(max_length=120, blank=True)
    minimum_instructors = models.PositiveSmallIntegerField(default=1)
    recommended_instructors = models.PositiveSmallIntegerField(default=1)
    instructor_intensive = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.record_number} — {self.name}"

    def clean(self):
        if self.recommended_instructors < self.minimum_instructors:
            raise ValidationError(
                {"recommended_instructors": "Recommended staffing cannot be below minimum staffing."}
            )


class CourseUnit(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="units",
    )
    unit_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=180, blank=True)
    required_instructors = models.PositiveSmallIntegerField(default=1)
    requires_safety_officer = models.BooleanField(default=False)
    notes = models.CharField(max_length=250, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("unit_number",)
        constraints = [
            models.UniqueConstraint(
                fields=("course", "unit_number"),
                name="unique_course_unit_number",
            ),
            models.CheckConstraint(
                condition=models.Q(required_instructors__gte=1),
                name="course_unit_requires_instructor",
            ),
        ]

    @property
    def display_name(self):
        return f"Unit {self.unit_number}" + (f" — {self.title}" if self.title else "")

    @property
    def total_staff_required(self):
        return self.required_instructors + int(self.requires_safety_officer)

    def __str__(self):
        return f"{self.course.record_number} — {self.display_name}"


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
    sfi_number = models.CharField("SFI number", max_length=30, blank=True, db_index=True)
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
        return f"{self.instructor} — {self.course.record_number}"


class TrainingEvent(models.Model):
    class Status(models.TextChoices):
        PROPOSED = "proposed", "Purposed"
        CONFIRMED = "confirmed", "Confirmed"
        COMPLETED = "completed", "Completed"
        CANCELED = "canceled", "Cancelled"

    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="events")
    host_organization = models.ForeignKey(
        Organization,
        on_delete=models.PROTECT,
        related_name="training_events",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    offering_number = models.CharField(
        "Course offering number",
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        help_text="Optional while Purposed; required before the training can be Confirmed.",
    )
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
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=("confirmed", "completed"))
                    | (
                        models.Q(offering_number__isnull=False)
                        & ~models.Q(offering_number="")
                    )
                ),
                name="confirmed_training_requires_offering_number",
            )
        ]

    def clean(self):
        super().clean()
        self.offering_number = (
            self.offering_number.strip() if self.offering_number else None
        )
        if (
            self.status in (self.Status.CONFIRMED, self.Status.COMPLETED)
            and not self.offering_number
        ):
            raise ValidationError(
                {
                    "offering_number": (
                        "Enter the Course Offering Number before confirming this training."
                    )
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.course.name} — {self.host_organization.short_name}"


class TrainingSession(models.Model):
    event = models.ForeignKey(
        TrainingEvent,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    course_unit = models.ForeignKey(
        CourseUnit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="training_sessions",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    location_override = models.CharField(max_length=180, blank=True)

    class Meta:
        ordering = ("course_unit__unit_number", "starts_at")
        constraints = [
            models.UniqueConstraint(
                fields=("event", "course_unit"),
                condition=models.Q(course_unit__isnull=False),
                name="unique_event_course_unit_session",
            )
        ]

    def clean(self):
        if bool(self.starts_at) != bool(self.ends_at):
            raise ValidationError("Enter both a start and end date/time for this unit.")
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The session must end after it starts."})
        if self.course_unit_id and self.event_id and self.course_unit.course_id != self.event.course_id:
            raise ValidationError({"course_unit": "This unit does not belong to the selected course."})

    @property
    def is_scheduled(self):
        return bool(self.starts_at and self.ends_at)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        unit_label = self.course_unit.display_name if self.course_unit_id else "General session"
        schedule_label = self.starts_at.strftime("%b %d, %Y %I:%M %p") if self.starts_at else "Unscheduled"
        return f"{self.event.course.record_number} — {unit_label} — {schedule_label}"


class InstructorAssignment(models.Model):
    class Role(models.TextChoices):
        LEAD = "lead", "Lead instructor"
        ASSISTANT = "assistant", "Assistant instructor"
        SAFETY_OFFICER = "safety", "Safety officer"

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
        if not self.session.is_scheduled:
            raise ValidationError("Schedule this unit before assigning instructors.")
        authorization = CourseAuthorization.objects.filter(
            instructor=self.instructor,
            course=self.session.event.course,
            status=CourseAuthorization.Status.ACTIVE,
        )
        if self.role == self.Role.LEAD:
            authorization = authorization.filter(can_lead=True)
        elif self.role == self.Role.ASSISTANT:
            authorization = authorization.filter(can_assist=True)
        if not authorization.exists():
            raise ValidationError("This instructor does not have an active authorization for this course and role.")
        conflicts = InstructorAssignment.objects.filter(
            instructor=self.instructor,
            confirmed=True,
            session__event__status__in=(
                TrainingEvent.Status.PROPOSED,
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
        AVAILABLE = "available", "Available (preferred time)"
        TENTATIVE = "tentative", "Tentative"
        UNAVAILABLE = "unavailable", "Unavailable"

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="availability_blocks",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices)
    notes = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ("starts_at",)

    def clean(self):
        super().clean()
        if self.all_day and self.starts_at and self.ends_at:
            current_timezone = timezone.get_current_timezone()
            start_date = timezone.localtime(self.starts_at).date()
            local_end = timezone.localtime(self.ends_at)
            end_date = local_end.date()
            if local_end.time() != time.min or end_date <= start_date:
                end_date += timedelta(days=1)
            self.starts_at = timezone.make_aware(
                datetime.combine(start_date, time.min),
                current_timezone,
            )
            self.ends_at = timezone.make_aware(
                datetime.combine(end_date, time.min),
                current_timezone,
            )
        if self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "Availability must end after it starts."})
        if not self.instructor_id:
            return
        overlaps = AvailabilityBlock.objects.filter(
            instructor=self.instructor,
            starts_at__lt=self.ends_at,
            ends_at__gt=self.starts_at,
        ).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError(
                "This entry overlaps another availability entry for this instructor."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.instructor} — {self.get_status_display()}"

    @property
    def all_day_end_date(self):
        return timezone.localtime(self.ends_at - timedelta(microseconds=1)).date()


class RecurringAvailabilityRule(models.Model):
    WEEKDAY_CHOICES = (
        ("0", "Monday"),
        ("1", "Tuesday"),
        ("2", "Wednesday"),
        ("3", "Thursday"),
        ("4", "Friday"),
        ("5", "Saturday"),
        ("6", "Sunday"),
    )

    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.CASCADE,
        related_name="recurring_availability_rules",
    )
    status = models.CharField(max_length=16, choices=AvailabilityBlock.Status.choices)
    weekdays = models.CharField(
        max_length=20,
        help_text="Comma-separated weekday numbers where Monday is 0.",
    )
    all_day = models.BooleanField(default=False)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=250, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("starts_on", "start_time", "pk")

    @property
    def weekday_values(self):
        return tuple(value for value in self.weekdays.split(",") if value)

    @property
    def weekday_labels(self):
        labels = dict(self.WEEKDAY_CHOICES)
        return tuple(labels[value] for value in self.weekday_values if value in labels)

    @property
    def weekday_summary(self):
        values = self.weekday_values
        if values == ("0", "1", "2", "3", "4"):
            return "Monday–Friday"
        if values == ("5", "6"):
            return "Saturday–Sunday"
        return ", ".join(self.weekday_labels)

    def occurs_on(self, calendar_date):
        return (
            str(calendar_date.weekday()) in self.weekday_values
            and calendar_date >= self.starts_on
            and (self.ends_on is None or calendar_date <= self.ends_on)
        )

    def overlaps(self, starts_at, ends_at):
        current_timezone = timezone.get_current_timezone()
        local_start = timezone.localtime(starts_at, current_timezone)
        local_end = timezone.localtime(ends_at, current_timezone)
        final_date = (local_end - timedelta(microseconds=1)).date()
        current_date = local_start.date()
        while current_date <= final_date:
            if self.occurs_on(current_date):
                if self.all_day:
                    return True
                rule_start = timezone.make_aware(
                    datetime.combine(current_date, self.start_time),
                    current_timezone,
                )
                rule_end = timezone.make_aware(
                    datetime.combine(current_date, self.end_time),
                    current_timezone,
                )
                if rule_start < ends_at and rule_end > starts_at:
                    return True
            current_date += timedelta(days=1)
        return False

    def clean(self):
        super().clean()
        weekday_values = self.weekday_values
        if not weekday_values or any(value not in dict(self.WEEKDAY_CHOICES) for value in weekday_values):
            raise ValidationError({"weekdays": "Select at least one valid weekday."})
        if len(set(weekday_values)) != len(weekday_values):
            raise ValidationError({"weekdays": "Each weekday may only be selected once."})
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError({"ends_on": "The end date cannot be before the start date."})
        if self.all_day:
            self.start_time = None
            self.end_time = None
        else:
            if not self.start_time or not self.end_time:
                raise ValidationError("Start and end times are required unless All day is selected.")
            if self.end_time <= self.start_time:
                raise ValidationError({"end_time": "The end time must be after the start time."})

    def save(self, *args, **kwargs):
        self.full_clean()
        self.weekdays = ",".join(sorted(set(self.weekday_values), key=int))
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.instructor} — {self.weekday_summary} ({self.get_status_display()})"


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
