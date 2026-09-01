from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.models import UserOrganizationRole
from scheduling.models import Organization


def login_required_unless_debug(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if request.user.is_authenticated or settings.DEBUG:
            return view_func(request, *args, **kwargs)
        return redirect_to_login(request.get_full_path())

    return wrapped


def managed_organizations(user):
    if not user.is_authenticated:
        return Organization.objects.filter(active=True) if settings.DEBUG else Organization.objects.none()
    if user.is_superuser:
        return Organization.objects.filter(active=True)
    return Organization.objects.filter(
        active=True,
        user_roles__user=user,
        user_roles__role=UserOrganizationRole.Role.ADMINISTRATOR,
    ).distinct()


def can_manage_organization(user, organization):
    return managed_organizations(user).filter(pk=organization.pk).exists()


def require_organization_manager(user, organization):
    if not can_manage_organization(user, organization):
        raise PermissionDenied("You do not manage this organization.")
