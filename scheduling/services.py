from django.db.models import Q
from django.utils import timezone

from .models import (
    AvailabilityBlock,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
)


def eligible_instructors_for_session(session, role):
    """Return qualified instructors who are not unavailable or double-booked."""

    today = timezone.localdate()
    role_requirement = (
        Q(course_authorizations__can_lead=True)
        if role == InstructorAssignment.Role.LEAD
        else Q(course_authorizations__can_assist=True)
    )
    instructors = Instructor.objects.filter(
        active=True,
        course_authorizations__course=session.event.course,
        course_authorizations__status=CourseAuthorization.Status.ACTIVE,
    ).filter(role_requirement)
    instructors = instructors.filter(
        Q(course_authorizations__effective_date__isnull=True)
        | Q(course_authorizations__effective_date__lte=today),
        Q(course_authorizations__expiration_date__isnull=True)
        | Q(course_authorizations__expiration_date__gte=today),
    )

    unavailable_ids = AvailabilityBlock.objects.filter(
        status=AvailabilityBlock.Status.UNAVAILABLE,
        starts_at__lt=session.ends_at,
        ends_at__gt=session.starts_at,
    ).values_list("instructor_id", flat=True)
    assigned_ids = InstructorAssignment.objects.filter(
        confirmed=True,
        session__starts_at__lt=session.ends_at,
        session__ends_at__gt=session.starts_at,
    ).exclude(session=session).values_list("instructor_id", flat=True)

    instructors = instructors.exclude(pk__in=unavailable_ids).exclude(pk__in=assigned_ids)
    instructors = instructors.filter(
        Q(home_organization=session.event.host_organization)
        | ~Q(travel_preference=Instructor.TravelPreference.LOCAL_ONLY)
    )
    return instructors.select_related("home_organization").order_by("last_name", "first_name")
