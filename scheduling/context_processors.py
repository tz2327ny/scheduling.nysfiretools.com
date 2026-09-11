from urllib.parse import urlencode

from django.conf import settings

from django.urls import reverse

from .permissions import has_administration_access, has_scheduler_access


def access_context(request):
    return {
        "main_site_origin": settings.NYSFIRETOOLS_MAIN_ORIGIN,
        "has_scheduler_access": has_scheduler_access(request.user),
        "has_administration_access": has_administration_access(request.user),
    }


def navigation(request):
    """Shared section identity and a predictable parent for every scheduler page."""
    match = request.resolver_match
    name = (match.url_name or "") if match else ""
    section = "dashboard"
    if name == "administration":
        section = "administration"
    elif name in ("scheduler_join", "scheduler_join_pending"):
        section = "enrollment"
    elif name.startswith(("training_", "session_assignment_")) or name == "schedule":
        section = "schedule"
    elif name.startswith("course_"):
        section = "courses"
    elif name.startswith("organization_"):
        section = "organizations"
    elif name.startswith(("user_", "application_", "authorization_")):
        section = "users"
    elif name.startswith(("instructor_", "availability_", "recurring_")):
        section = "instructors"

    schedule_url = reverse("schedule")
    filters = request.session.get("schedule_filters", {})
    if isinstance(filters, dict):
        query = urlencode({key: filters[key] for key in ("status", "organization") if filters.get(key)})
        if query:
            schedule_url += "?" + query

    roots = {
        "administration": ("Administration", "administration"),
        "enrollment": ("Join Scheduler", "scheduler_join"),
        "dashboard": ("Dashboard", "dashboard"),
        "schedule": ("Schedule", "schedule"),
        "courses": ("Course library", "course_list"),
        "instructors": ("Instructors", "instructor_list"),
        "organizations": ("Organizations", "organization_list"),
        "users": ("Users", "user_list"),
    }
    label, route = roots[section]
    parent_url = schedule_url if section == "schedule" else reverse(route)
    back_url, back_label = parent_url, label
    if match and name == "training_edit":
        back_url = reverse("training_detail", args=[match.kwargs["pk"]])
        back_label = "Training details"
    elif match and name.startswith(("availability_", "recurring_")):
        back_url = reverse("instructor_availability", args=[match.kwargs["pk"]])
        back_label = "Availability"
    elif match and name == "user_password_reset":
        back_url = reverse("user_edit", args=[match.kwargs["pk"]])
        back_label = "User details"
    page_labels = {
        "instructor_availability": "Availability",
        "instructor_authorizations": "Course authorizations",
        "instructor_notifications": "Notification preferences",
        "instructor_edit": "Edit instructor",
        "instructor_create": "Add instructor",
        "instructor_delete": "Delete instructor",
        "availability_create": "Add availability",
        "availability_edit": "Edit availability",
        "availability_delete": "Remove availability",
        "recurring_availability_create": "Add recurring availability",
        "recurring_availability_edit": "Edit recurring availability",
        "recurring_availability_delete": "Remove recurring availability",
        "course_create": "Add course",
        "course_edit": "Edit course",
        "organization_create": "Add organization",
        "organization_edit": "Edit organization",
        "organization_merge": "Merge organizations",
        "scheduler_join_pending": "Enrollment pending",
        "user_create": "Create account",
        "user_edit": "Manage user",
        "user_password_reset": "Reset password",
        "application_review": "Review application",
    }
    return {
        "nav_section": section,
        "nav_section_label": label,
        "nav_parent_url": parent_url,
        "nav_back_url": back_url,
        "nav_back_label": back_label,
        "nav_page_label": page_labels.get(name, "Details"),
        "nav_is_section_root": name == route,
        "schedule_return_url": schedule_url,
    }
