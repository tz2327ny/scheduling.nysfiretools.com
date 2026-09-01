from django import forms
from django.forms import inlineformset_factory

from .models import AvailabilityBlock, Course, Instructor, TrainingEvent, TrainingSession


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"


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
        if managed_organizations is not None:
            self.fields["host_organization"].queryset = managed_organizations


class TrainingSessionForm(forms.ModelForm):
    class Meta:
        model = TrainingSession
        fields = ("starts_at", "ends_at", "location_override")
        widgets = {
            "starts_at": DateTimeInput(format="%Y-%m-%dT%H:%M"),
            "ends_at": DateTimeInput(format="%Y-%m-%dT%H:%M"),
        }


TrainingSessionFormSet = inlineformset_factory(
    TrainingEvent,
    TrainingSession,
    form=TrainingSessionForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class InstructorForm(forms.ModelForm):
    class Meta:
        model = Instructor
        fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "home_organization",
            "travel_preference",
            "travel_notes",
            "active",
        )

    def __init__(self, *args, managed_organizations=None, **kwargs):
        super().__init__(*args, **kwargs)
        if managed_organizations is not None:
            self.fields["home_organization"].queryset = managed_organizations
