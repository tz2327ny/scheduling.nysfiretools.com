from datetime import timedelta
from functools import wraps
import hashlib
import hmac
import re
import secrets
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from scheduling.models import (
    AssistanceRequest,
    AuditEvent,
    CourseAuthorization,
    Instructor,
    TrainingEvent,
)
from scheduling.notifications import (
    notify_account_approved,
    notify_authorization_approved,
)
from scheduling.permissions import has_administration_access, managed_organizations

from .forms import (
    GeneralAccessRegistrationForm,
    InstructorApplicationReviewForm,
    InstructorRegistrationForm,
    SchedulerEnrollmentForm,
    StatePasswordResetForm,
    StateUserCreateForm,
    StateUserForm,
)
from .models import AccountProfile, ExternalAccessCode, InstructorApplication, UserOrganizationRole


def _safe_external_return_path(value):
    value = str(value or "")[:500]
    return value if value.startswith("/") and not value.startswith("//") else "/burn-plans"


def _external_user_agency(user):
    try:
        organization_name = user.nysfiretools_profile.organization_name
        if organization_name:
            return organization_name
    except (AttributeError, AccountProfile.DoesNotExist):
        pass
    try:
        return user.instructor_profile.home_organization.name
    except (AttributeError, Instructor.DoesNotExist):
        pass
    try:
        return user.instructor_application.home_organization.name
    except (AttributeError, InstructorApplication.DoesNotExist):
        return ""


@login_required
def nysfiretools_sso_authorize(request):
    """Issue a short-lived code after Scheduler authentication."""
    if not settings.NYSFIRETOOLS_SSO_CLIENT_SECRET:
        return JsonResponse({"error": "single_sign_on_unavailable"}, status=503)

    state = str(request.GET.get("state", ""))[:200]
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", state):
        return JsonResponse({"error": "invalid_state"}, status=400)

    now = timezone.now()
    ExternalAccessCode.objects.filter(expires_at__lte=now).delete()
    raw_code = secrets.token_urlsafe(32)
    ExternalAccessCode.objects.create(
        token_hash=hashlib.sha256(raw_code.encode("utf-8")).hexdigest(),
        state=state,
        user=request.user,
        return_path=_safe_external_return_path(request.GET.get("return_to")),
        expires_at=now + timedelta(minutes=2),
    )
    query = urlencode({"code": raw_code, "state": state})
    return redirect(f"{settings.NYSFIRETOOLS_MAIN_ORIGIN}/burn-plans/sso/callback?{query}")


@csrf_exempt
@require_POST
def nysfiretools_sso_token(request):
    """Exchange a one-time code for the approved Scheduler identity."""
    configured_secret = settings.NYSFIRETOOLS_SSO_CLIENT_SECRET
    supplied_secret = request.headers.get("X-NYSFIRETOOLS-SSO-Secret", "")
    if not configured_secret:
        return JsonResponse({"error": "single_sign_on_unavailable"}, status=503)
    if not secrets.compare_digest(supplied_secret, configured_secret):
        return JsonResponse({"error": "invalid_client"}, status=401)

    token_hash = hashlib.sha256(str(request.POST.get("code", "")).encode("utf-8")).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        access_code = (
            ExternalAccessCode.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=token_hash, used_at__isnull=True, expires_at__gt=now)
            .first()
        )
        if not access_code or not access_code.user.is_active:
            return JsonResponse({"error": "invalid_or_expired_code"}, status=400)
        access_code.used_at = now
        access_code.save(update_fields=("used_at",))

    user = access_code.user
    email = (user.email or user.username).strip().lower()
    return JsonResponse(
        {
            "sub": str(user.pk),
            "email": email,
            "name": user.get_full_name().strip() or email,
            "agency": _external_user_agency(user),
            "role": "admin" if user.is_superuser else "viewer",
            "scheduler_status": getattr(
                getattr(user, "nysfiretools_profile", None),
                "scheduler_status",
                AccountProfile.SchedulerStatus.ACTIVE if hasattr(user, "instructor_profile") else AccountProfile.SchedulerStatus.NOT_ENROLLED,
            ),
            "admin_organizations": list(
                user.organization_roles.filter(
                    role=UserOrganizationRole.Role.ADMINISTRATOR,
                    organization__active=True,
                ).values_list("organization_id", flat=True)
            ),
            "return_to": access_code.return_path,
        }
    )


@require_GET
def nysfiretools_sso_sign_out(request):
    """End the central account session after a signed main-site sign-out."""
    configured_secret = settings.NYSFIRETOOLS_SSO_CLIENT_SECRET
    expires = str(request.GET.get("expires", ""))
    return_path = _safe_external_return_path(request.GET.get("return_to"))
    supplied_signature = str(request.GET.get("signature", ""))
    if not configured_secret or not expires.isdigit():
        return JsonResponse({"error": "invalid_sign_out_request"}, status=400)

    try:
        expires_at = int(expires)
    except ValueError:
        return JsonResponse({"error": "invalid_sign_out_request"}, status=400)
    if expires_at < int(timezone.now().timestamp()) or expires_at > int(timezone.now().timestamp()) + 300:
        return JsonResponse({"error": "expired_sign_out_request"}, status=400)

    expected_signature = hmac.new(
        configured_secret.encode("utf-8"),
        f"{expires}:{return_path}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(supplied_signature, expected_signature):
        return JsonResponse({"error": "invalid_sign_out_request"}, status=400)

    logout(request)
    return redirect(f"{settings.NYSFIRETOOLS_MAIN_ORIGIN}{return_path}")


@require_POST
def nysfiretools_full_sign_out(request):
    """End Scheduler access, then clear the main-site access layers."""
    logout(request)
    configured_secret = settings.NYSFIRETOOLS_SSO_CLIENT_SECRET
    if not configured_secret:
        return redirect("login")

    expires = str(int(timezone.now().timestamp()) + 60)
    return_path = "/login"
    signature = hmac.new(
        configured_secret.encode("utf-8"),
        f"{expires}:{return_path}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode(
        {"expires": expires, "return_to": return_path, "signature": signature}
    )
    return redirect(
        f"{settings.NYSFIRETOOLS_MAIN_ORIGIN}/burn-plans/sso/scheduler-sign-out?{query}"
    )


def state_admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only a Site Administrator can manage users.")
        return view_func(request, *args, **kwargs)

    return wrapped


@login_required
def administration(request):
    if not has_administration_access(request.user):
        raise PermissionDenied("You do not have an administrative role.")
    scope = () if request.user.is_superuser else managed_organizations(request.user)
    return render(
        request,
        "accounts/administration.html",
        {
            "managed_organization_count": 0 if request.user.is_superuser else scope.count(),
            "managed_organizations": scope[:12],
        },
    )


def merge_existing_login_into_applicant(existing_user, applicant_user, application):
    """Consolidate access/history into the newly registered login."""
    applicant_user.groups.add(*existing_user.groups.all())
    applicant_user.user_permissions.add(*existing_user.user_permissions.all())
    applicant_user.is_superuser = applicant_user.is_superuser or existing_user.is_superuser
    applicant_user.is_staff = applicant_user.is_superuser
    applicant_user.save(update_fields=("is_superuser", "is_staff"))

    for role in existing_user.organization_roles.all():
        UserOrganizationRole.objects.get_or_create(
            user=applicant_user,
            organization=role.organization,
            role=role.role,
        )

    existing_profile = AccountProfile.objects.filter(user=existing_user).first()
    applicant_profile, _ = AccountProfile.objects.get_or_create(
        user=applicant_user,
        defaults={"signup_source": AccountProfile.SignupSource.SCHEDULER},
    )
    if existing_profile:
        if existing_profile.access_status == AccountProfile.AccessStatus.ACTIVE:
            applicant_profile.access_status = AccountProfile.AccessStatus.ACTIVE
        if not applicant_profile.organization_id and existing_profile.organization_id:
            applicant_profile.organization = existing_profile.organization
        applicant_profile.access_reason = applicant_profile.access_reason or existing_profile.access_reason
        applicant_profile.save()
        existing_profile.delete()

    old_application = InstructorApplication.objects.filter(user=existing_user).first()
    if old_application and old_application.pk != application.pk:
        application.requested_courses.add(*old_application.requested_courses.all())
        old_application.delete()

    CourseAuthorization.objects.filter(verified_by=existing_user).update(
        verified_by=applicant_user
    )
    TrainingEvent.objects.filter(created_by=existing_user).update(
        created_by=applicant_user
    )
    AssistanceRequest.objects.filter(created_by=existing_user).update(
        created_by=applicant_user
    )
    AuditEvent.objects.filter(actor=existing_user).update(actor=applicant_user)
    InstructorApplication.objects.filter(reviewed_by=existing_user).update(
        reviewed_by=applicant_user
    )
    existing_user.delete()


def account_access_start(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "registration/access_choice.html")


def general_register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = GeneralAccessRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("email", "An account already exists for this email address.")
        else:
            return redirect("registration_received")
    return render(request, "registration/register_general.html", {"form": form})


def instructor_register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = InstructorRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
                application = InstructorApplication.objects.create(
                    user=user,
                    sfi_number=form.cleaned_data["sfi_number"],
                    phone=form.cleaned_data["phone"],
                    home_organization=form.cleaned_data["home_organization"],
                    travel_preference=form.cleaned_data["travel_preference"],
                    travel_notes=form.cleaned_data["travel_notes"],
                )
                application.requested_courses.set(form.cleaned_data["requested_courses"])
                AccountProfile.objects.create(
                    user=user,
                    access_status=AccountProfile.AccessStatus.PENDING,
                    signup_source=AccountProfile.SignupSource.SCHEDULER,
                    scheduler_status=AccountProfile.SchedulerStatus.PENDING,
                    organization=form.cleaned_data["home_organization"],
                    access_reason="Fire Training Scheduler enrollment",
                )
        except IntegrityError:
            form.add_error("email", "An account already exists for this email address.")
        else:
            return redirect("registration_received")
    return render(request, "registration/register.html", {"form": form})


def registration_received(request):
    return render(request, "registration/registration_received.html")


@login_required
def scheduler_join(request):
    profile, _ = AccountProfile.objects.get_or_create(
        user=request.user,
        defaults={
            "access_status": AccountProfile.AccessStatus.ACTIVE,
            "signup_source": AccountProfile.SignupSource.LEGACY,
        },
    )
    if hasattr(request.user, "instructor_profile"):
        if profile.scheduler_status != AccountProfile.SchedulerStatus.ACTIVE:
            profile.scheduler_status = AccountProfile.SchedulerStatus.ACTIVE
            profile.save(update_fields=("scheduler_status", "updated_at"))
        messages.info(request, "Your account is already enrolled in the Scheduler.")
        return redirect("dashboard")
    application = InstructorApplication.objects.filter(user=request.user).first()
    if application and application.status == InstructorApplication.Status.PENDING:
        return render(
            request,
            "registration/scheduler_join_pending.html",
            {"application": application},
        )
    form = SchedulerEnrollmentForm(
        request.POST or None,
        user=request.user,
        application=application,
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save()
        return redirect("scheduler_join")
    return render(request, "registration/scheduler_join.html", {"form": form})


@state_admin_required
def user_list(request):
    users = (
        request.user.__class__.objects.all()
        .select_related(
            "instructor_profile__home_organization",
            "instructor_application__home_organization",
        )
        .prefetch_related("organization_roles__organization")
        .order_by("last_name", "first_name", "email")
    )
    pending_applications = InstructorApplication.objects.filter(
        status=InstructorApplication.Status.PENDING
    ).select_related("user", "home_organization").prefetch_related("requested_courses")
    pending_authorizations = CourseAuthorization.objects.filter(
        status=CourseAuthorization.Status.PENDING
    ).select_related("instructor__home_organization", "course")
    pending_access_requests = AccountProfile.objects.filter(
        access_status=AccountProfile.AccessStatus.PENDING,
        user__instructor_application__isnull=True,
    ).select_related("user", "organization")
    return render(
        request,
        "accounts/user_list.html",
        {
            "users": users,
            "pending_applications": pending_applications,
            "pending_authorizations": pending_authorizations,
            "pending_access_requests": pending_access_requests,
        },
    )


@state_admin_required
def user_create(request):
    form = StateUserCreateForm(request.POST or None, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save_with_roles()
            AccountProfile.objects.get_or_create(
                user=user,
                defaults={
                    "access_status": AccountProfile.AccessStatus.ACTIVE if user.is_active else AccountProfile.AccessStatus.PENDING,
                    "signup_source": AccountProfile.SignupSource.ADMIN,
                    "scheduler_status": AccountProfile.SchedulerStatus.ACTIVE if hasattr(user, "instructor_profile") else AccountProfile.SchedulerStatus.NOT_ENROLLED,
                },
            )
        messages.success(
            request,
            f"{user.get_full_name() or user.email} can now sign in.",
        )
        return redirect("user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "is_create": True},
    )


@state_admin_required
def user_edit(request, pk):
    user = get_object_or_404(request.user.__class__, pk=pk)
    form = StateUserForm(request.POST or None, instance=user, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            updated_user = form.save_with_roles()
            profile, _ = AccountProfile.objects.get_or_create(
                user=updated_user,
                defaults={"signup_source": AccountProfile.SignupSource.LEGACY},
            )
            profile.access_status = (
                AccountProfile.AccessStatus.ACTIVE
                if updated_user.is_active
                else AccountProfile.AccessStatus.SUSPENDED
            )
            if hasattr(updated_user, "instructor_profile"):
                profile.scheduler_status = AccountProfile.SchedulerStatus.ACTIVE
                profile.organization = updated_user.instructor_profile.home_organization
            profile.save()
        messages.success(request, "User access was updated.")
        return redirect("user_list")
    return render(request, "accounts/user_form.html", {"form": form, "managed_user": user})


@state_admin_required
def user_password_reset(request, pk):
    user = get_object_or_404(request.user.__class__, pk=pk)
    form = StatePasswordResetForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        if user == request.user:
            update_session_auth_hash(request, user)
        messages.success(
            request,
            f"Password was reset for {user.get_full_name() or user.email}.",
        )
        return redirect("user_list")
    return render(
        request,
        "accounts/user_password_reset.html",
        {"form": form, "managed_user": user},
    )


@require_POST
@state_admin_required
def access_request_approve(request, pk):
    profile = get_object_or_404(
        AccountProfile.objects.select_related("user"),
        pk=pk,
        access_status=AccountProfile.AccessStatus.PENDING,
    )
    profile.access_status = AccountProfile.AccessStatus.ACTIVE
    profile.user.is_active = True
    profile.user.save(update_fields=("is_active",))
    profile.save(update_fields=("access_status", "updated_at"))
    messages.success(
        request,
        f"{profile.user.get_full_name() or profile.user.email} can now use the protected NYSFIRETOOLS tools.",
    )
    return redirect("user_list")


@require_POST
@state_admin_required
def access_request_reject(request, pk):
    profile = get_object_or_404(
        AccountProfile.objects.select_related("user"),
        pk=pk,
        access_status=AccountProfile.AccessStatus.PENDING,
    )
    profile.access_status = AccountProfile.AccessStatus.REJECTED
    profile.user.is_active = False
    profile.user.save(update_fields=("is_active",))
    profile.save(update_fields=("access_status", "updated_at"))
    messages.success(request, "The NYSFIRETOOLS access request was not approved.")
    return redirect("user_list")


@state_admin_required
@transaction.atomic
def application_review(request, pk):
    application = get_object_or_404(
        InstructorApplication.objects.select_related("user", "home_organization").prefetch_related(
            "requested_courses"
        ),
        pk=pk,
        status=InstructorApplication.Status.PENDING,
    )
    form = InstructorApplicationReviewForm(
        request.POST or None,
        application=application,
    )
    if request.method != "POST" or not form.is_valid():
        return render(
            request,
            "accounts/application_review.html",
            {
                "application": application,
                "form": form,
                "matching_instructors": form.matching_instructors,
            },
        )

    user = application.user
    instructor_match = form.cleaned_data["instructor_match"]
    if instructor_match == "new":
        instructor = Instructor()
    else:
        instructor = get_object_or_404(
            Instructor.objects.select_for_update(),
            pk=int(instructor_match),
        )
        if instructor.user_id and instructor.user_id != user.pk:
            existing_user = instructor.user
            instructor.user = None
            instructor.save(update_fields=("user",))
            merge_existing_login_into_applicant(existing_user, user, application)
    instructor.user = user
    instructor.first_name = user.first_name
    instructor.last_name = user.last_name
    instructor.sfi_number = application.sfi_number
    instructor.email = user.email
    instructor.phone = application.phone
    instructor.home_organization = application.home_organization
    instructor.travel_preference = application.travel_preference
    instructor.travel_notes = application.travel_notes
    instructor.active = True
    instructor.save()

    user.is_active = True
    user.save(update_fields=("is_active",))
    account_profile, _ = AccountProfile.objects.get_or_create(
        user=user,
        defaults={"signup_source": AccountProfile.SignupSource.SCHEDULER},
    )
    account_profile.access_status = AccountProfile.AccessStatus.ACTIVE
    account_profile.scheduler_status = AccountProfile.SchedulerStatus.ACTIVE
    account_profile.organization = application.home_organization
    account_profile.requested_organization_name = ""
    account_profile.save()
    application.status = InstructorApplication.Status.APPROVED
    application.instructor = instructor
    application.reviewed_at = timezone.now()
    application.reviewed_by = request.user
    application.save(update_fields=("status", "instructor", "reviewed_at", "reviewed_by"))
    verified_at = timezone.now()
    for course in form.cleaned_data["approved_courses"]:
        CourseAuthorization.objects.update_or_create(
            instructor=instructor,
            course=course,
            defaults={
                "status": CourseAuthorization.Status.ACTIVE,
                "verified_by": request.user,
                "verified_at": verified_at,
            },
        )
    notify_account_approved(instructor)
    messages.success(request, f"{user.get_full_name() or user.email} was approved and can now sign in.")
    return redirect("user_list")


@require_POST
@state_admin_required
def application_reject(request, pk):
    application = get_object_or_404(
        InstructorApplication.objects.select_related("user"),
        pk=pk,
        status=InstructorApplication.Status.PENDING,
    )
    application.status = InstructorApplication.Status.REJECTED
    application.reviewed_at = timezone.now()
    application.reviewed_by = request.user
    application.save(update_fields=("status", "reviewed_at", "reviewed_by"))
    profile, _ = AccountProfile.objects.get_or_create(
        user=application.user,
        defaults={"signup_source": AccountProfile.SignupSource.SCHEDULER},
    )
    profile.scheduler_status = AccountProfile.SchedulerStatus.REJECTED
    if profile.access_status == AccountProfile.AccessStatus.PENDING:
        profile.access_status = AccountProfile.AccessStatus.REJECTED
        application.user.is_active = False
        application.user.save(update_fields=("is_active",))
    profile.save()
    messages.success(request, "The instructor application was not approved.")
    return redirect("user_list")


@require_POST
@state_admin_required
def authorization_approve(request, pk):
    authorization = get_object_or_404(
        CourseAuthorization.objects.select_related("instructor", "course"),
        pk=pk,
        status=CourseAuthorization.Status.PENDING,
    )
    authorization.status = CourseAuthorization.Status.ACTIVE
    authorization.verified_by = request.user
    authorization.verified_at = timezone.now()
    authorization.save(update_fields=("status", "verified_by", "verified_at"))
    notify_authorization_approved(authorization)
    messages.success(
        request,
        f"{authorization.course.name} was approved for {authorization.instructor.full_name}.",
    )
    return redirect("user_list")


@require_POST
@state_admin_required
def authorization_reject(request, pk):
    authorization = get_object_or_404(
        CourseAuthorization,
        pk=pk,
        status=CourseAuthorization.Status.PENDING,
    )
    authorization.delete()
    messages.success(request, "The requested course authorization was not approved.")
    return redirect("user_list")
