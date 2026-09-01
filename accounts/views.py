from functools import wraps

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from scheduling.models import CourseAuthorization, Instructor

from .forms import (
    InstructorApplicationReviewForm,
    InstructorRegistrationForm,
    StatePasswordResetForm,
    StateUserForm,
)
from .models import InstructorApplication


def state_admin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied("Only a State administrator can manage users.")
        return view_func(request, *args, **kwargs)

    return wrapped


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
                    phone=form.cleaned_data["phone"],
                    home_organization=form.cleaned_data["home_organization"],
                    travel_preference=form.cleaned_data["travel_preference"],
                    travel_notes=form.cleaned_data["travel_notes"],
                )
                application.requested_courses.set(form.cleaned_data["requested_courses"])
        except IntegrityError:
            form.add_error("email", "An account already exists for this email address.")
        else:
            return redirect("registration_received")
    return render(request, "registration/register.html", {"form": form})


def registration_received(request):
    return render(request, "registration/registration_received.html")


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
    return render(
        request,
        "accounts/user_list.html",
        {
            "users": users,
            "pending_applications": pending_applications,
            "pending_authorizations": pending_authorizations,
        },
    )


@state_admin_required
def user_edit(request, pk):
    user = get_object_or_404(request.user.__class__, pk=pk)
    form = StateUserForm(request.POST or None, instance=user, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.save_with_roles()
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
            {"application": application, "form": form},
        )

    user = application.user
    instructor = Instructor.objects.filter(email__iexact=user.email, user__isnull=True).first()
    if instructor is None:
        instructor = Instructor()
    instructor.user = user
    instructor.first_name = user.first_name
    instructor.last_name = user.last_name
    instructor.email = user.email
    instructor.phone = application.phone
    instructor.home_organization = application.home_organization
    instructor.travel_preference = application.travel_preference
    instructor.travel_notes = application.travel_notes
    instructor.active = True
    instructor.save()

    user.is_active = True
    user.save(update_fields=("is_active",))
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
    application.user.is_active = False
    application.user.save(update_fields=("is_active",))
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
