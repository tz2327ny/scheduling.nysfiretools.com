from django import forms
from django.forms import inlineformset_factory

from .models import Course, Instructor, TrainingEvent, TrainingSession


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = (
            "code",
            "name",
            "description",
            "minimum_instructors",
            "recommended_instructors",
            "instructor_intensive",
            "active",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
        }


class TrainingEventForm(forms.ModelForm):
    class Meta:
        model = TrainingEvent
        fields = (
            "course",
            "host_organization",
            "status",
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
