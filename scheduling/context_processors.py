from .permissions import has_administration_access, has_scheduler_access


def access_context(request):
    return {
        "has_scheduler_access": has_scheduler_access(request.user),
        "has_administration_access": has_administration_access(request.user),
    }
