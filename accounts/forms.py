from difflib import SequenceMatcher

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, UserCreationForm
from django.db.models import Q

from scheduling.models import Course, CourseAuthorization, Instructor, Organization
from scheduling.services import sync_verified_course_authorizations

from .models import UserOrganizationRole


User = get_user_model()


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True}))

    def clean_username(self):
        return User.objects.normalize_email(self.cleaned_data["username"]).strip().lower()


class InstructorRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email address")
    sfi_number = forms.CharField(
        label="SFI number",
        max_length=30,
        help_text="Required for State verification and matching an existing instructor record.",
    )
    phone = forms.CharField(max_length=30, required=False)
    home_organization = forms.ModelChoiceField(
        label="County or State assignment",
        queryset=Organization.objects.none(),
        help_text="Choose the organization you are assigned to. Academy-assigned instructors should choose the New York State Academy of Fire Science.",
    )
    travel_preference = forms.ChoiceField(choices=Instructor.TravelPreference.choices)
    travel_notes = forms.CharField(
        required=False,
        max_length=250,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Optional travel limits, preferred areas, or other conditions.",
    )
    requested_courses = forms.ModelMultipleChoiceField(
        label="Courses you are authorized to teach — review required",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Selections are authorization claims only. A Site Administrator reviews and "
            "verifies each selected course before it becomes active or can be used for assignments."
        ),
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
                "sfi_number",
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

    def clean_sfi_number(self):
        return self.cleaned_data["sfi_number"].strip().upper()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
        return user


def _normalized_match_value(value):
    return "".join(character for character in (value or "").lower() if character.isalnum())


def matching_instructors_for_application(application):
    """Return directory profiles that may belong to an applicant."""
    applicant = application.user
    applicant_name = _normalized_match_value(
        f"{applicant.first_name} {applicant.last_name}"
    )
    applicant_email = _normalized_match_value(applicant.email)
    applicant_sfi = _normalized_match_value(application.sfi_number)
    scored_matches = []
    for instructor in Instructor.objects.all().select_related("home_organization", "user"):
        instructor_name = _normalized_match_value(
            f"{instructor.first_name} {instructor.last_name}"
        )
        instructor_email = _normalized_match_value(instructor.email)
        instructor_sfi = _normalized_match_value(instructor.sfi_number)
        exact_sfi = bool(applicant_sfi and applicant_sfi == instructor_sfi)
        exact_email = bool(applicant_email and applicant_email == instructor_email)
        name_score = SequenceMatcher(None, applicant_name, instructor_name).ratio()
        email_score = SequenceMatcher(None, applicant_email, instructor_email).ratio()
        same_last_name = bool(
            applicant.last_name
            and instructor.last_name
            and applicant.last_name.casefold() == instructor.last_name.casefold()
        )
        if exact_sfi or exact_email or name_score >= 0.82 or (
            same_last_name and name_score >= 0.7
        ) or (applicant_email and instructor_email and email_score >= 0.88):
            priority = int(exact_sfi) * 4 + int(exact_email) * 3 + name_score
            scored_matches.append((priority, instructor.pk))
    scored_matches.sort(reverse=True)
    ids = [pk for _, pk in scored_matches[:10]]
    profiles = {
        profile.pk: profile
        for profile in Instructor.objects.filter(pk__in=ids).select_related(
            "home_organization"
        )
    }
    return [profiles[pk] for pk in ids if pk in profiles]


class InstructorApplicationReviewForm(forms.Form):
    instructor_match = forms.ChoiceField(
        label="Is this an existing instructor?",
        required=False,
        widget=forms.RadioSelect,
    )
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
        self.matching_instructors = matching_instructors_for_application(application)
        if self.matching_instructors:
            self.fields["instructor_match"].required = True
            self.fields["instructor_match"].choices = [
                (
                    "new",
                    "No — this is a different person. Create a new instructor profile.",
                )
            ] + [
                (
                    str(instructor.pk),
                    f"Yes — {instructor.full_name} · {instructor.sfi_number or 'No SFI number'} · "
                    f"{instructor.email or 'No email'} · {instructor.home_organization.short_name}"
                    f"{' · Existing login will be merged' if instructor.user_id else ''}",
                )
                for instructor in self.matching_instructors
            ]
        else:
            self.fields["instructor_match"].widget = forms.HiddenInput()
            self.fields["instructor_match"].choices = [("new", "Create a new instructor profile")]
            self.fields["instructor_match"].initial = "new"
        requested = application.requested_courses.filter(active=True).order_by("name", "record_number")
        self.fields["approved_courses"].queryset = requested
        self.fields["approved_courses"].initial = requested

    def clean_instructor_match(self):
        selected = self.cleaned_data.get("instructor_match") or "new"
        valid_ids = {str(instructor.pk) for instructor in self.matching_instructors}
        if selected != "new" and selected not in valid_ids:
            raise forms.ValidationError("Choose one of the possible instructor matches shown.")
        return selected


class InstructorProfileChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, instructor):
        return (
            f"{instructor.full_name} — {instructor.home_organization.short_name} — "
            f"{instructor.sfi_number or instructor.email or 'No identifier entered'}"
        )


class StateUserForm(forms.ModelForm):
    organization_admins = forms.ModelMultipleChoiceField(
        label="County/organization administrator for",
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    instructor_profile = InstructorProfileChoiceField(
        label="Linked instructor directory profile",
        queryset=Instructor.objects.none(),
        required=False,
        help_text="Optional. Leave blank for an administrator-only account. Clearing an existing link deactivates that instructor profile but preserves its staffing history.",
    )
    verified_courses = forms.ModelMultipleChoiceField(
        label="Verified course authorizations",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the courses this linked instructor is currently authorized to teach.",
    )

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "is_active", "is_superuser")
        labels = {
            "is_active": "Account enabled",
            "is_superuser": "Site administrator",
        }
        help_texts = {
            "is_superuser": "Site administrators have statewide access and can approve instructors, manage users, organizations, and courses.",
        }

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.fields["organization_admins"].queryset = Organization.objects.filter(active=True)
        self.fields["verified_courses"].queryset = Course.objects.filter(
            active=True
        ).order_by("name", "record_number")
        if self.instance.pk:
            self.fields["organization_admins"].initial = Organization.objects.filter(
                user_roles__user=self.instance,
                user_roles__role=UserOrganizationRole.Role.ADMINISTRATOR,
            )
            self.fields["instructor_profile"].initial = getattr(
                self.instance, "instructor_profile", None
            )
            instructor = getattr(self.instance, "instructor_profile", None)
            if instructor:
                self.fields["verified_courses"].initial = (
                    instructor.course_authorizations.filter(
                        status=CourseAuthorization.Status.ACTIVE
                    ).values_list("course_id", flat=True)
                )
            self.fields["instructor_profile"].queryset = Instructor.objects.filter(
                Q(user__isnull=True) | Q(user=self.instance)
            ).select_related("home_organization")
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
                self.add_error("is_superuser", "You cannot remove your own Site Administrator access.")
        application = getattr(self.instance, "instructor_application", None)
        if application and application.status != application.Status.APPROVED and cleaned.get("is_active"):
            self.add_error("is_active", "Approve this instructor application before enabling the account.")
        if cleaned.get("verified_courses") and not cleaned.get("instructor_profile"):
            self.add_error(
                "verified_courses",
                "Link an instructor directory profile before assigning course authorizations.",
            )
        return cleaned

    def save_with_roles(self):
        previous_instructor = getattr(self.instance, "instructor_profile", None)
        selected_instructor = self.cleaned_data["instructor_profile"]
        if previous_instructor and previous_instructor != selected_instructor:
            previous_instructor.user = None
            previous_instructor.active = False
            previous_instructor.save(update_fields=("user", "active"))
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.is_staff = user.is_superuser
        user.save()
        if selected_instructor:
            selected_instructor.user = user
            selected_instructor.first_name = user.first_name
            selected_instructor.last_name = user.last_name
            selected_instructor.email = user.email
            selected_instructor.active = True
            selected_instructor.save(
                update_fields=("user", "first_name", "last_name", "email", "active")
            )
            sync_verified_course_authorizations(
                selected_instructor,
                self.cleaned_data["verified_courses"],
                self.acting_user,
            )
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


class StateUserCreateForm(UserCreationForm):
    email = forms.EmailField(label="Email address")
    is_active = forms.BooleanField(label="Account enabled", required=False, initial=True)
    is_superuser = forms.BooleanField(
        label="Site administrator",
        required=False,
        help_text="Site administrators have statewide access. Leave unchecked for instructors and organization administrators.",
    )
    organization_admins = forms.ModelMultipleChoiceField(
        label="County/organization administrator for",
        queryset=Organization.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    instructor_profile = InstructorProfileChoiceField(
        label="Linked instructor directory profile",
        queryset=Instructor.objects.none(),
        required=False,
        help_text="Optional. Link an instructor already added to the scheduling directory, or leave blank for an administrator-only account.",
    )
    verified_courses = forms.ModelMultipleChoiceField(
        label="Verified course authorizations",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select the courses this linked instructor is currently authorized to teach.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.fields["organization_admins"].queryset = Organization.objects.filter(active=True)
        self.fields["verified_courses"].queryset = Course.objects.filter(
            active=True
        ).order_by("name", "record_number")
        self.fields["instructor_profile"].queryset = Instructor.objects.filter(
            user__isnull=True
        ).select_related("home_organization")
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.order_fields(
            [
                "first_name",
                "last_name",
                "email",
                "password1",
                "password2",
                "is_active",
                "is_superuser",
                "organization_admins",
                "instructor_profile",
                "verified_courses",
            ]
        )

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        if User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            raise forms.ValidationError("An account already exists for this email address.")
        return email

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("verified_courses") and not cleaned.get("instructor_profile"):
            self.add_error(
                "verified_courses",
                "Link an instructor directory profile before assigning course authorizations.",
            )
        return cleaned

    def save_with_roles(self):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        user.is_active = self.cleaned_data["is_active"]
        user.is_superuser = self.cleaned_data["is_superuser"]
        user.is_staff = user.is_superuser
        user.save()
        selected_instructor = self.cleaned_data["instructor_profile"]
        if selected_instructor:
            selected_instructor.user = user
            selected_instructor.first_name = user.first_name
            selected_instructor.last_name = user.last_name
            selected_instructor.email = user.email
            selected_instructor.active = True
            selected_instructor.save(
                update_fields=("user", "first_name", "last_name", "email", "active")
            )
            sync_verified_course_authorizations(
                selected_instructor,
                self.cleaned_data["verified_courses"],
                self.acting_user,
            )
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


class StatePasswordResetForm(SetPasswordForm):
    """Allows a Site Administrator to set a new password for an account."""

    new_password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
