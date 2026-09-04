from difflib import SequenceMatcher

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, SetPasswordForm, UserCreationForm
from django.db.models import Q

from scheduling.models import Course, CourseAuthorization, Instructor, Organization
from scheduling.services import sync_verified_course_authorizations
from scheduling.widgets import OrganizationSelect, OrganizationSelectMultiple

from .models import AccountProfile, InstructorApplication, UserOrganizationRole


User = get_user_model()


class OrganizationChoiceField(forms.ModelChoiceField):
    widget = OrganizationSelect

    def label_from_instance(self, organization):
        return organization.authority_label


def active_organizations():
    return Organization.objects.filter(
        active=True,
        lifecycle_status=Organization.LifecycleStatus.ACTIVE,
    ).select_related("parent").order_by("kind", "county_name", "name")


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"autofocus": True}))

    def clean_username(self):
        return User.objects.normalize_email(self.cleaned_data["username"]).strip().lower()


class InstructorRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email address")
    sfi_number = forms.CharField(
        label="SFI, CFI, or MFI number",
        max_length=30,
        help_text="Required for State verification and matching an existing instructor record.",
    )
    phone = forms.CharField(max_length=30, required=False)
    home_organization = OrganizationChoiceField(
        label="Home agency, county, or State assignment",
        queryset=Organization.objects.none(),
        help_text="Choose your normal fire department, county program, or State assignment.",
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
        self.fields["home_organization"].queryset = active_organizations()
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


class GeneralAccessRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email address")
    organization = OrganizationChoiceField(
        label="Department, agency, or organization",
        queryset=Organization.objects.none(),
        required=False,
        help_text="Choose the existing official entry whenever possible.",
    )
    requested_organization_name = forms.CharField(
        label="Organization not listed",
        max_length=180,
        required=False,
        help_text="Enter a name only when the correct organization is not available. An administrator will review it rather than creating a duplicate.",
    )
    access_reason = forms.CharField(
        label="Why are you requesting access?",
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="For example: Burn Plan Library, Site Plan Builder, or department-logo submission.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["organization"].queryset = active_organizations()
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True
        self.order_fields(
            [
                "first_name",
                "last_name",
                "email",
                "organization",
                "requested_organization_name",
                "access_reason",
                "password1",
                "password2",
            ]
        )

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).strip().lower()
        if User.objects.filter(Q(username__iexact=email) | Q(email__iexact=email)).exists():
            raise forms.ValidationError("An account already exists for this email address.")
        return email

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("organization") and not cleaned.get("requested_organization_name", "").strip():
            self.add_error("organization", "Choose your organization or enter its name below.")
        if cleaned.get("organization") and cleaned.get("requested_organization_name", "").strip():
            self.add_error("requested_organization_name", "Leave this blank when you selected an organization above.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.username = self.cleaned_data["email"]
        user.email = self.cleaned_data["email"]
        user.is_active = False
        if commit:
            user.save()
            AccountProfile.objects.create(
                user=user,
                access_status=AccountProfile.AccessStatus.PENDING,
                signup_source=AccountProfile.SignupSource.GENERAL,
                scheduler_status=AccountProfile.SchedulerStatus.NOT_ENROLLED,
                organization=self.cleaned_data.get("organization"),
                requested_organization_name=self.cleaned_data.get("requested_organization_name", "").strip(),
                access_reason=self.cleaned_data["access_reason"].strip(),
            )
        return user


class SchedulerEnrollmentForm(forms.Form):
    sfi_number = forms.CharField(
        label="SFI, CFI, or MFI number",
        max_length=30,
        help_text="Required for State verification and matching an existing instructor record.",
    )
    phone = forms.CharField(max_length=30, required=False)
    home_organization = OrganizationChoiceField(
        label="Home agency, county, or State assignment",
        queryset=Organization.objects.none(),
        help_text="Choose your normal fire department, county program, or State assignment.",
    )
    travel_preference = forms.ChoiceField(choices=Instructor.TravelPreference.choices)
    travel_notes = forms.CharField(
        required=False,
        max_length=250,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    requested_courses = forms.ModelMultipleChoiceField(
        label="Courses you are authorized to teach — review required",
        queryset=Course.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, user, application=None, **kwargs):
        self.user = user
        self.application = application
        super().__init__(*args, **kwargs)
        self.fields["home_organization"].queryset = active_organizations()
        self.fields["requested_courses"].queryset = Course.objects.filter(active=True).order_by(
            "name", "record_number"
        )
        if application and not self.is_bound:
            self.initial.update(
                {
                    "sfi_number": application.sfi_number,
                    "phone": application.phone,
                    "home_organization": application.home_organization_id,
                    "travel_preference": application.travel_preference,
                    "travel_notes": application.travel_notes,
                    "requested_courses": application.requested_courses.values_list("pk", flat=True),
                }
            )

    def clean_sfi_number(self):
        return self.cleaned_data["sfi_number"].strip().upper()

    def save(self):
        application = self.application or InstructorApplication(user=self.user)
        application.sfi_number = self.cleaned_data["sfi_number"]
        application.phone = self.cleaned_data["phone"]
        application.home_organization = self.cleaned_data["home_organization"]
        application.travel_preference = self.cleaned_data["travel_preference"]
        application.travel_notes = self.cleaned_data["travel_notes"]
        application.status = InstructorApplication.Status.PENDING
        application.reviewed_at = None
        application.reviewed_by = None
        application.save()
        application.requested_courses.set(self.cleaned_data["requested_courses"])
        profile, _ = AccountProfile.objects.get_or_create(
            user=self.user,
            defaults={
                "access_status": AccountProfile.AccessStatus.ACTIVE,
                "signup_source": AccountProfile.SignupSource.LEGACY,
            },
        )
        profile.scheduler_status = AccountProfile.SchedulerStatus.PENDING
        profile.organization = self.cleaned_data["home_organization"]
        profile.requested_organization_name = ""
        profile.save(
            update_fields=(
                "scheduler_status",
                "organization",
                "requested_organization_name",
                "updated_at",
            )
        )
        return application


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
                    f"Yes — {instructor.full_name} · {instructor.sfi_number or 'No SFI/CFI/MFI number'} · "
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
        widget=OrganizationSelectMultiple(attrs={"size": 12}),
    )
    instructor_profile = InstructorProfileChoiceField(
        label="Linked instructor directory profile",
        queryset=Instructor.objects.none(),
        required=False,
        help_text="Optional. Leave blank for an administrator-only account. Clearing an existing link deactivates that instructor profile but preserves its staffing history.",
    )
    instructor_home_organization = forms.ModelChoiceField(
        label="Instructor home assignment",
        queryset=Organization.objects.none(),
        required=False,
        widget=OrganizationSelect,
        help_text=(
            "The county or State Academy shown for this instructor throughout the scheduler. "
            "This is separate from county/organization administrator access."
        ),
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
            "is_superuser": "Global administrator",
        }
        help_texts = {
            "is_superuser": "Global administrators can approve accounts and manage every NYSFIRETOOLS organization, user, and Scheduler setting.",
        }

    def __init__(self, *args, acting_user=None, **kwargs):
        self.acting_user = acting_user
        super().__init__(*args, **kwargs)
        self.fields["organization_admins"].queryset = active_organizations()
        self.fields["instructor_home_organization"].queryset = active_organizations()
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
                self.fields["instructor_home_organization"].initial = (
                    instructor.home_organization
                )
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
                self.add_error("is_superuser", "You cannot remove your own Global Administrator access.")
        application = getattr(self.instance, "instructor_application", None)
        if application and application.status != application.Status.APPROVED and cleaned.get("is_active"):
            self.add_error("is_active", "Approve this instructor application before enabling the account.")
        if cleaned.get("verified_courses") and not cleaned.get("instructor_profile"):
            self.add_error(
                "verified_courses",
                "Link an instructor directory profile before assigning course authorizations.",
            )
        selected_instructor = cleaned.get("instructor_profile")
        if selected_instructor and not cleaned.get("instructor_home_organization"):
            cleaned["instructor_home_organization"] = selected_instructor.home_organization
        if not selected_instructor:
            cleaned["instructor_home_organization"] = None
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
            selected_instructor.home_organization = self.cleaned_data[
                "instructor_home_organization"
            ]
            selected_instructor.active = True
            selected_instructor.save(
                update_fields=(
                    "user",
                    "first_name",
                    "last_name",
                    "email",
                    "home_organization",
                    "active",
                )
            )
            application = getattr(user, "instructor_application", None)
            if (
                application
                and application.status == InstructorApplication.Status.APPROVED
                and application.home_organization_id
                != selected_instructor.home_organization_id
            ):
                application.home_organization = selected_instructor.home_organization
                application.save(update_fields=("home_organization",))
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
        label="Global administrator",
        required=False,
        help_text="Global administrators can manage all NYSFIRETOOLS accounts, organizations, and Scheduler settings. Leave unchecked for scoped administrators.",
    )
    organization_admins = forms.ModelMultipleChoiceField(
        label="County/organization administrator for",
        queryset=Organization.objects.none(),
        required=False,
        widget=OrganizationSelectMultiple(attrs={"size": 12}),
    )
    instructor_profile = InstructorProfileChoiceField(
        label="Linked instructor directory profile",
        queryset=Instructor.objects.none(),
        required=False,
        help_text="Optional. Link an instructor already added to the scheduling directory, or leave blank for an administrator-only account.",
    )
    instructor_home_organization = forms.ModelChoiceField(
        label="Instructor home assignment",
        queryset=Organization.objects.none(),
        required=False,
        widget=OrganizationSelect,
        help_text=(
            "The county or State Academy shown for this instructor throughout the scheduler. "
            "Leave blank to keep the linked directory profile's current assignment."
        ),
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
        self.fields["organization_admins"].queryset = active_organizations()
        self.fields["instructor_home_organization"].queryset = active_organizations()
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
                "instructor_home_organization",
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
        selected_instructor = cleaned.get("instructor_profile")
        if selected_instructor and not cleaned.get("instructor_home_organization"):
            cleaned["instructor_home_organization"] = selected_instructor.home_organization
        if not selected_instructor:
            cleaned["instructor_home_organization"] = None
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
            selected_instructor.home_organization = self.cleaned_data[
                "instructor_home_organization"
            ]
            selected_instructor.active = True
            selected_instructor.save(
                update_fields=(
                    "user",
                    "first_name",
                    "last_name",
                    "email",
                    "home_organization",
                    "active",
                )
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
