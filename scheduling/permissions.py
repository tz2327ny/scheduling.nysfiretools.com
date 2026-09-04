from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from accounts.models import AccountProfile, UserOrganizationRole
from scheduling.models import Organization


def login_required_unless_debug(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated and not has_scheduler_access(request.user):
            return redirect_to_login(
                request.get_full_path(),
                login_url="scheduler_join",
                redirect_field_name=None,
            )
        if request.user.is_authenticated or settings.DEBUG:
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path())

    return wrapped


def has_scheduler_access(user):
    if not user.is_authenticated:
        return settings.DEBUG
    if user.is_superuser or user.organization_roles.filter(
        role=UserOrganizationRole.Role.ADMINISTRATOR,
    ).exists():
        return True
    try:
        if user.nysfiretools_profile.scheduler_status == AccountProfile.SchedulerStatus.ACTIVE:
            return True
    except AccountProfile.DoesNotExist:
        # Every production account receives a profile during migration. Treat a
        # profile-less account as a legacy Scheduler login for compatibility.
        return True
    return hasattr(user, "instructor_profile")


def has_administration_access(user):
    return bool(
        user.is_authenticated
        and (
            user.is_superuser
            or user.organization_roles.filter(
                role=UserOrganizationRole.Role.ADMINISTRATOR,
            ).exists()
        )
    )


def managed_organizations(user):
    if not user.is_authenticated:
        return Organization.objects.filter(
            active=True,
            lifecycle_status=Organization.LifecycleStatus.ACTIVE,
        ) if settings.DEBUG else Organization.objects.none()
    if user.is_superuser:
        return Organization.objects.filter(
            active=True,
            lifecycle_status=Organization.LifecycleStatus.ACTIVE,
        )
    direct = Organization.objects.filter(
        active=True,
        lifecycle_status=Organization.LifecycleStatus.ACTIVE,
        user_roles__user=user,
        user_roles__role=UserOrganizationRole.Role.ADMINISTRATOR,
    ).distinct()
    direct_ids = list(direct.values_list("pk", flat=True))
    if not direct_ids:
        return Organization.objects.none()
    if direct.filter(kind__in=(Organization.Kind.STATE, Organization.Kind.ACADEMY)).exists():
        return Organization.objects.filter(
            active=True,
            lifecycle_status=Organization.LifecycleStatus.ACTIVE,
        )
    county_ids = list(direct.filter(kind=Organization.Kind.COUNTY).values_list("pk", flat=True))
    county_names = list(
        direct.filter(kind=Organization.Kind.COUNTY).values_list("county_name", flat=True)
    )
    return Organization.objects.filter(
        Q(pk__in=direct_ids)
        | Q(parent_id__in=county_ids)
        | Q(kind=Organization.Kind.AGENCY, county_name__in=county_names),
        active=True,
        lifecycle_status=Organization.LifecycleStatus.ACTIVE,
    ).distinct()


def can_manage_organization(user, organization):
    return managed_organizations(user).filter(pk=organization.pk).exists()


def require_organization_manager(user, organization):
    if not can_manage_organization(user, organization):
        raise PermissionDenied("You do not manage this organization.")


def can_manage_courses(user):
    return bool(
        (user.is_authenticated and user.is_superuser)
        or (settings.DEBUG and not user.is_authenticated)
    )


def require_course_manager(user):
    if not can_manage_courses(user):
        raise PermissionDenied("Only a system administrator can manage the course library.")


def can_manage_instructor_availability(user, instructor):
    return bool(
        (user.is_authenticated and instructor.user_id == user.id)
        or can_manage_organization(user, instructor.home_organization)
    )


def require_instructor_availability_manager(user, instructor):
    if not can_manage_instructor_availability(user, instructor):
        raise PermissionDenied("You cannot manage this instructor's availability.")
