from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db.models import Q

from scheduling.models import Course, Instructor, Organization

from .models import UserOrganizationRole


User = get_user_model()


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True}))


class InstructorRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email address")
    phone = forms.CharField(max_length=30, required=False)
    home_organization = forms.ModelChoiceField(
        label="County or State assignment",
        queryset=Organization.objects.none(),
        help_text="Choose the organization you are assigned to. State/Regional instructors should choose the Academy.",
    )
    travel_preference = forms.ChoiceField(choices=Instructor.TravelPreference.choices)
    travel_notes = forms.CharField(
        required=False,
        max_length=250,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional travel limits, preferred areas, or other conditions.",
    )
    requested_courses = forms.ModelMultipleChoiceField(
        label="Courses you are authorized to teach",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select every current authorization. A State administrator will verify these before they become active.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["home_organization"].queryset = Organization.objects.filter(active=True)
        self.fields["requested_courses"].queryset = Course.objects.filter(active=True).order_by(
            "name", "record_number"
        )
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.order_fields(
            [
                "first_name",
                "last_name",
                "email",
                "phone",
                "home_organization",
                "travel_preference",
                "travel_notes",
                "requested_courses",
                "password1",
                "password2",
            ]
        )

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(username__iexact=email).exists() or User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already exists for this email address.")
        linked_profile = Instructor.objects.filter(email__iexact=email, user__isnull=False).first()
        if linked_profile:
            raise forms.ValidationError("This instructor profile is already linked to an account.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


class InstructorApplicationReviewForm(forms.Form):
    approved_courses = forms.ModelMultipleChoiceField(
        label="Verified course authorizations",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Only checked courses will become active authorizations when this account is approved.",
    )

    def __init__(self, *args, application, **kwargs):
        self.application = application
        super().__init__(*args, **kwargs)
        requested = application.requested_courses.filter(active=True).order_by("name", "record_number")
        self.fields["approved_courses"].queryset = requested
        self.fields["approved_courses"].initial = requested


class StateUserForm(forms.ModelForm):
    organization_admins = forms.ModelMultipleChoiceField(
        label="County/organization administrator for",
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "is_active", "is_superuser")
        labels = {
            "is_active": "Account enabled",
            "is_superuser": "State administrator",
        }
        help_texts = {
            "is_superuser": "State administrators can approve instructors, manage users, and edit the course library.",
        }

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.fields["organization_admins"].queryset = Organization.objects.filter(active=True)
        if self.instance.pk:
            self.fields["organization_admins"].initial = Organization.objects.filter(
                user_roles__user=self.instance,
                user_roles__role=UserOrganizationRole.Role.ADMINISTRATOR,
            )
        self.fields["email"].required = True

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.exclude(pk=self.instance.pk).filter(
            Q(username__iexact=email) | Q(email__iexact=email)
        ).exists():
            raise forms.ValidationError("Another account already uses this email address.")
        return email

    def clean(self):
        cleaned = super().clean()
        if self.acting_user == self.instance:
            if not cleaned.get("is_active"):
                self.add_error("is_active", "You cannot disable your own account.")
            if not cleaned.get("is_superuser"):
                self.add_error("is_superuser", "You cannot remove your own State administrator access.")
        application = getattr(self.instance, "instructor_application", None)
        if application and application.status != application.Status.APPROVED and cleaned.get("is_active"):
            self.add_error("is_active", "Approve this instructor application before enabling the account.")
        return cleaned

    def save_with_roles(self):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.is_staff = user.is_superuser
        user.save()
        instructor = getattr(user, "instructor_profile", None)
        if instructor:
            instructor.first_name = user.first_name
            instructor.last_name = user.last_name
            instructor.email = user.email
            instructor.save(update_fields=("first_name", "last_name", "email"))
        UserOrganizationRole.objects.filter(
            user=user,
            role=UserOrganizationRole.Role.ADMINISTRATOR,
        ).delete()
        UserOrganizationRole.objects.bulk_create(
            [
                UserOrganizationRole(
                    user=user,
                    organization=organization,
                    role=UserOrganizationRole.Role.ADMINISTRATOR,
                )
                for organization in self.cleaned_data["organization_admins"]
            ]
        )
        return user
