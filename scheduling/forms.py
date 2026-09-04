from datetime import timedelta

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import (
    AvailabilityBlock,
    Course,
    CourseAuthorization,
    CourseUnit,
    Instructor,
    InstructorAssignment,
    NotificationPreference,
    Organization,
    OrganizationAlias,
    RecurringAvailabilityRule,
    TrainingEvent,
    TrainingSession,
    normalize_organization_name,
)
from .widgets import OrganizationSelect


def county_from_organization_name(name):
    name = (name or "").strip()
    return name[:-7].strip() if name.casefold().endswith(" county") else name


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"


class OrganizationForm(forms.ModelForm):
    class Meta:
        model = Organization
        fields = (
            "name",
            "short_name",
            "kind",
            "parent",
            "county_name",
            "fdid_code",
            "address",
            "city",
            "state",
            "zip_code",
            "phone_number",
            "display_order",
            "lifecycle_status",
        )
        help_texts = {
            "name": "Use the full official or legal name.",
            "short_name": "The shorter label shown in schedules and staffing screens.",
            "parent": "Agencies should be assigned to their county authority.",
            "fdid_code": "Use the NYS Fire Department Directory code when available.",
            "display_order": "Lower numbers appear first in organization lists.",
            "lifecycle_status": "Use the dedicated merge action when one agency succeeds another.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lifecycle_status"].required = False
        self.fields["parent"].queryset = Organization.objects.filter(
            active=True,
            kind__in=(Organization.Kind.STATE, Organization.Kind.ACADEMY, Organization.Kind.COUNTY),
        ).exclude(pk=self.instance.pk).order_by("kind", "county_name", "name")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        county_name = str(self.data.get("county_name", "")).strip()
        kind = self.data.get("kind", "")
        if kind == Organization.Kind.COUNTY and not county_name:
            county_name = county_from_organization_name(name)
        normalized = normalize_organization_name(name)
        matches = Organization.objects.filter(
            kind=kind,
            county_name__iexact=county_name,
            normalized_name=normalized,
        )
        if self.instance.pk:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError("An organization with this name already exists.")
        if OrganizationAlias.objects.filter(
            county_name__iexact=county_name,
            normalized_name=normalized,
        ).exists():
            raise forms.ValidationError("That name is already recorded as an alias of another organization.")
        return name

    def clean_short_name(self):
        short_name = self.cleaned_data["short_name"].strip()
        matches = Organization.objects.filter(short_name__iexact=short_name)
        if self.instance.pk:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError("An organization with this short name already exists.")
        return short_name

    def clean_fdid_code(self):
        value = self.cleaned_data.get("fdid_code", "").strip().upper()
        if not value:
            return ""
        county_name = str(self.data.get("county_name", "")).strip()
        matches = Organization.objects.filter(county_name__iexact=county_name, fdid_code=value)
        if self.instance.pk:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError("That FDID is already assigned to an organization in this county.")
        return value

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        parent = cleaned.get("parent")
        county_name = cleaned.get("county_name", "").strip()
        if kind == Organization.Kind.COUNTY and not county_name:
            county_name = county_from_organization_name(cleaned.get("name", ""))
            cleaned["county_name"] = county_name
        if kind == Organization.Kind.AGENCY:
            if not county_name:
                self.add_error("county_name", "Select or enter the agency's county.")
            if parent and parent.kind != Organization.Kind.COUNTY:
                self.add_error("parent", "A fire department or agency must be assigned to a county organization.")
        if not cleaned.get("lifecycle_status"):
            cleaned["lifecycle_status"] = Organization.LifecycleStatus.ACTIVE
        if cleaned.get("lifecycle_status") == Organization.LifecycleStatus.MERGED and not self.instance.successor_id:
            self.add_error("lifecycle_status", "Use the Merge organization action so records and aliases are transferred safely.")
        return cleaned


class OrganizationMergeForm(forms.Form):
    target = forms.ModelChoiceField(
        label="Merge into",
        queryset=Organization.objects.none(),
        widget=OrganizationSelect,
        help_text="The old organization remains on historical training records and becomes an alias of the selected successor.",
    )

    def __init__(self, *args, source, **kwargs):
        self.source = source
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Organization.objects.filter(
            kind=source.kind,
            county_name=source.county_name,
            lifecycle_status=Organization.LifecycleStatus.ACTIVE,
            active=True,
        ).exclude(pk=source.pk).order_by("name")


class AvailabilityBlockForm(forms.ModelForm):
    class Meta:
        model = AvailabilityBlock
        fields = ("status", "all_day", "starts_at", "ends_at", "notes")
        widgets = {
            "starts_at": DateTimeInput(format="%Y-%m-%dT%H:%M"),
            "ends_at": DateTimeInput(format="%Y-%m-%dT%H:%M"),
            "notes": forms.TextInput(
                attrs={
                    "placeholder": "e.g., preferred evenings or availability depends on work schedule"
                }
            ),
        }
        labels = {
            "status": "Availability status",
            "all_day": "All day",
        }


class RecurringAvailabilityRuleForm(forms.ModelForm):
    weekdays = forms.MultipleChoiceField(
        choices=RecurringAvailabilityRule.WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Choose every weekday this schedule should repeat.",
    )

    class Meta:
        model = RecurringAvailabilityRule
        fields = (
            "status",
            "weekdays",
            "all_day",
            "start_time",
            "end_time",
            "starts_on",
            "ends_on",
            "notes",
        )
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "ends_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.TextInput(
                attrs={"placeholder": "e.g., Regular work schedule"}
            ),
        }
        labels = {
            "status": "Availability status",
            "all_day": "All day",
            "starts_on": "Effective starting",
            "ends_on": "Stop repeating after",
        }
        help_texts = {
            "ends_on": "Optional. Leave blank for an ongoing weekly schedule.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["weekdays"] = self.instance.weekday_values

    def clean_weekdays(self):
        return ",".join(sorted(set(self.cleaned_data["weekdays"]), key=int))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("all_day"):
            cleaned["start_time"] = None
            cleaned["end_time"] = None
        elif not cleaned.get("start_time") or not cleaned.get("end_time"):
            message = "Start and end times are required unless All day is selected."
            if not cleaned.get("start_time"):
                self.add_error("start_time", message)
            if not cleaned.get("end_time"):
                self.add_error("end_time", message)
        return cleaned


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = (
            "record_number",
            "name",
            "description",
            "number_of_units",
            "student_contact_hours",
            "instructor_requirements",
            "safety_officer_requirements",
            "ems_requirements",
            "admin_time",
            "county_hours_or_program_charge",
            "completion_type",
            "instructional_method",
            "class_size",
            "prerequisites",
            "in_service_hours",
            "national_certification",
            "course_version",
            "template_start_date",
            "template_end_date",
            "instructor_intensive",
            "active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "instructor_requirements": forms.Textarea(attrs={"rows": 4}),
            "safety_officer_requirements": forms.Textarea(attrs={"rows": 3}),
            "ems_requirements": forms.Textarea(attrs={"rows": 3}),
            "admin_time": forms.Textarea(attrs={"rows": 2}),
            "county_hours_or_program_charge": forms.Textarea(attrs={"rows": 2}),
            "instructional_method": forms.Textarea(attrs={"rows": 3}),
            "prerequisites": forms.Textarea(attrs={"rows": 3}),
        }


class CourseUnitForm(forms.ModelForm):
    class Meta:
        model = CourseUnit
        fields = (
            "unit_number",
            "title",
            "required_instructors",
            "requires_safety_officer",
            "notes",
            "active",
        )


CourseUnitFormSet = inlineformset_factory(
    Course,
    CourseUnit,
    form=CourseUnitForm,
    extra=0,
    can_delete=False,
)


class TrainingEventForm(forms.ModelForm):
    class Meta:
        model = TrainingEvent
        fields = (
            "course",
            "host_organization",
            "status",
            "offering_number",
            "location_name",
            "address",
            "contact_name",
            "contact_email",
            "acadis_registration_url",
            "notes",
        )
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, managed_organizations=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["host_organization"].widget = OrganizationSelect()
        if managed_organizations is not None:
            self.fields["host_organization"].queryset = managed_organizations
        if self.instance.pk:
            self.fields["course"].disabled = True
            self.fields["course"].help_text = "The course cannot be changed after unit scheduling begins."


class TrainingSessionForm(forms.ModelForm):
    class Meta:
        model = TrainingSession
        fields = ("course_unit", "starts_at", "ends_at", "location_override")
        widgets = {
            "course_unit": forms.HiddenInput(),
            "starts_at": DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"data-unit-start": ""},
            ),
            "ends_at": DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"data-unit-end": ""},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["starts_at"].required = False
        self.fields["ends_at"].required = False

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("starts_at")
        ends_at = cleaned_data.get("ends_at")
        raw_end = self.data.get(self.add_prefix("ends_at"), "") if self.is_bound else ""
        if starts_at and not ends_at and not raw_end.strip():
            cleaned_data["ends_at"] = starts_at + timedelta(hours=3)
            self.instance.ends_at = cleaned_data["ends_at"]
        return cleaned_data


TrainingSessionFormSet = inlineformset_factory(
    TrainingEvent,
    TrainingSession,
    form=TrainingSessionForm,
    extra=0,
    can_delete=False,
)


class InstructorAssignmentForm(forms.ModelForm):
    class Meta:
        model = InstructorAssignment
        fields = ("instructor", "role", "confirmed")

    def __init__(self, *args, session, role=None, **kwargs):
        from .services import eligible_instructors_for_session

        self.session = session
        self.locked_role = role
        super().__init__(*args, **kwargs)
        self.instance.session = session
        existing = session.instructor_assignments.exclude(pk=self.instance.pk)
        required_instructors = (
            session.course_unit.required_instructors if session.course_unit else 1
        )
        available_roles = []
        if not existing.filter(role=InstructorAssignment.Role.LEAD).exists():
            available_roles.append(InstructorAssignment.Role.LEAD)
        if existing.filter(role=InstructorAssignment.Role.ASSISTANT).count() < max(
            required_instructors - 1, 0
        ):
            available_roles.append(InstructorAssignment.Role.ASSISTANT)
        if (
            session.course_unit
            and session.course_unit.requires_safety_officer
            and not existing.filter(role=InstructorAssignment.Role.SAFETY_OFFICER).exists()
        ):
            available_roles.append(InstructorAssignment.Role.SAFETY_OFFICER)

        role_labels = dict(InstructorAssignment.Role.choices)
        if role is not None:
            self.fields["role"].choices = (
                [(role, role_labels[role])] if role in available_roles else []
            )
            self.fields["role"].initial = role
            self.fields["role"].widget = forms.HiddenInput()
        else:
            self.fields["role"].choices = [
                (available_role, role_labels[available_role])
                for available_role in available_roles
            ]

        selected_role = self.data.get("role") if self.is_bound else role
        eligibility_role = (
            InstructorAssignment.Role.LEAD
            if selected_role == InstructorAssignment.Role.LEAD
            else InstructorAssignment.Role.ASSISTANT
        )
        eligible = eligible_instructors_for_session(session, eligibility_role).exclude(
            assignments__session=session
        )
        self.fields["instructor"].queryset = eligible


class InstructorForm(forms.ModelForm):
    verified_courses = forms.ModelMultipleChoiceField(
        label="Verified course authorizations",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Select every course this instructor is currently authorized to teach. "
            "Selections are immediately available for staffing assignments."
        ),
    )

    class Meta:
        model = Instructor
        fields = (
            "first_name",
            "last_name",
            "sfi_number",
            "email",
            "phone",
            "home_organization",
            "travel_preference",
            "travel_notes",
            "active",
        )

    def __init__(
        self,
        *args,
        managed_organizations=None,
        authorization_verifier=None,
        **kwargs,
    ):
        self.authorization_verifier = authorization_verifier
        super().__init__(*args, **kwargs)
        self.fields["home_organization"].widget = OrganizationSelect()
        if managed_organizations is not None:
            self.fields["home_organization"].queryset = managed_organizations
        if authorization_verifier is None:
            self.fields.pop("verified_courses")
        else:
            self.fields["verified_courses"].queryset = Course.objects.filter(
                active=True
            ).order_by("name", "record_number")
            if self.instance.pk:
                self.fields["verified_courses"].initial = (
                    self.instance.course_authorizations.filter(
                        status=CourseAuthorization.Status.ACTIVE
                    ).values_list("course_id", flat=True)
                )

    def save(self, commit=True):
        instructor = super().save(commit=commit)
        if commit and self.authorization_verifier is not None:
            from .services import sync_verified_course_authorizations

            sync_verified_course_authorizations(
                instructor,
                self.cleaned_data["verified_courses"],
                self.authorization_verifier,
            )
        return instructor


class InstructorAuthorizationRequestForm(forms.Form):
    courses = forms.ModelMultipleChoiceField(
        label="Additional course authorizations",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select courses you are currently authorized to teach. New selections require State approval.",
    )

    def __init__(self, *args, instructor, **kwargs):
        self.instructor = instructor
        super().__init__(*args, **kwargs)
        pending_ids = instructor.course_authorizations.filter(
            status=CourseAuthorization.Status.PENDING
        ).values_list("course_id", flat=True)
        locked_ids = instructor.course_authorizations.exclude(
            status=CourseAuthorization.Status.PENDING
        ).values_list("course_id", flat=True)
        self.fields["courses"].queryset = Course.objects.filter(active=True).exclude(
            pk__in=locked_ids
        ).order_by("name", "record_number")
        self.fields["courses"].initial = pending_ids

    def save(self):
        selected_courses = self.cleaned_data["courses"]
        selected_ids = set(selected_courses.values_list("pk", flat=True))
        self.instructor.course_authorizations.filter(
            status=CourseAuthorization.Status.PENDING
        ).exclude(course_id__in=selected_ids).delete()
        for course in selected_courses:
            CourseAuthorization.objects.update_or_create(
                instructor=self.instructor,
                course=course,
                defaults={
                    "status": CourseAuthorization.Status.PENDING,
                    "verified_by": None,
                    "verified_at": None,
                },
            )


class NotificationPreferenceForm(forms.Form):
    email_enabled = forms.BooleanField(
        label="Email notifications",
        required=False,
        help_text="Send operational updates to the email address on your instructor profile.",
    )
    sms_enabled = forms.BooleanField(
        label="Text message notifications",
        required=False,
        help_text="Optional. Text message frequency varies based on assignments and schedule changes.",
    )
    phone = forms.CharField(
        label="Mobile phone number",
        max_length=30,
        required=False,
        help_text="A United States mobile number capable of receiving text messages.",
    )
    assignment_updates = forms.BooleanField(
        label="Assignment updates",
        required=False,
        help_text="Notify me when I am assigned to or removed from a course unit.",
    )
    schedule_updates = forms.BooleanField(
        label="Schedule and cancellation updates",
        required=False,
        help_text="Notify me when dates, times, locations, or course status change.",
    )
    sms_consent = forms.BooleanField(
        label="I agree to receive operational text messages from NYSFIRETOOLS.com",
        required=False,
        help_text="Message and data rates may apply. Message frequency varies. Reply STOP to opt out when provider-managed opt-out is enabled, or turn texts off here at any time.",
    )

    def __init__(self, *args, instructor, **kwargs):
        self.instructor = instructor
        self.preference, _ = NotificationPreference.objects.get_or_create(
            instructor=instructor
        )
        initial = kwargs.setdefault("initial", {})
        initial.update(
            {
                "email_enabled": self.preference.email_enabled,
                "sms_enabled": self.preference.sms_enabled,
                "phone": instructor.phone,
                "assignment_updates": self.preference.assignment_updates,
                "schedule_updates": self.preference.schedule_updates,
                "sms_consent": self.preference.sms_enabled,
            }
        )
        super().__init__(*args, **kwargs)

    def clean_phone(self):
        value = self.cleaned_data.get("phone", "").strip()
        if not value:
            return ""
        digits = "".join(character for character in value if character.isdigit())
        if len(digits) == 10:
            return f"+1{digits}"
        if len(digits) == 11 and digits.startswith("1"):
            return f"+{digits}"
        if value.startswith("+") and 8 <= len(digits) <= 15:
            return f"+{digits}"
        raise forms.ValidationError("Enter a valid mobile number, including area code.")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("sms_enabled"):
            if not cleaned.get("phone"):
                self.add_error("phone", "A mobile phone number is required for text notifications.")
            if not cleaned.get("sms_consent"):
                self.add_error("sms_consent", "Confirm text-message consent to opt in.")
        return cleaned

    def save(self):
        now = timezone.now()
        previously_enabled = self.preference.sms_enabled
        self.instructor.phone = self.cleaned_data["phone"]
        self.instructor.save(update_fields=("phone",))
        self.preference.email_enabled = self.cleaned_data["email_enabled"]
        self.preference.sms_enabled = self.cleaned_data["sms_enabled"]
        self.preference.assignment_updates = self.cleaned_data["assignment_updates"]
        self.preference.schedule_updates = self.cleaned_data["schedule_updates"]
        if self.preference.sms_enabled and not previously_enabled:
            self.preference.sms_consented_at = now
            self.preference.sms_opted_out_at = None
        elif previously_enabled and not self.preference.sms_enabled:
            self.preference.sms_opted_out_at = now
        self.preference.save()
        return self.preference
