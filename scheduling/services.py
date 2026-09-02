from datetime import timedelta

from django.db.models import Case, CharField, IntegerField, Q, Value, When
from django.utils import timezone

from .models import (
    AvailabilityBlock,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
    RecurringAvailabilityRule,
)


def eligible_instructors_for_session(session, role):
    """Return qualified instructors who are not unavailable or double-booked."""

    if not session.is_scheduled:
        return Instructor.objects.none()

    today = timezone.localdate()
    role_requirement = (
        Q(course_authorizations__can_lead=True)
        if role == InstructorAssignment.Role.LEAD
        else Q(course_authorizations__can_assist=True)
    )
    authorization_requirement = (
        Q(course_authorizations__course=session.event.course)
        & Q(course_authorizations__status=CourseAuthorization.Status.ACTIVE)
        & role_requirement
        & (
            Q(course_authorizations__effective_date__isnull=True)
            | Q(course_authorizations__effective_date__lte=today)
        )
        & (
            Q(course_authorizations__expiration_date__isnull=True)
            | Q(course_authorizations__expiration_date__gte=today)
        )
    )
    instructors = Instructor.objects.filter(
        Q(active=True) & authorization_requirement
    )

    unavailable_ids = set(AvailabilityBlock.objects.filter(
        status=AvailabilityBlock.Status.UNAVAILABLE,
        starts_at__lt=session.ends_at,
        ends_at__gt=session.starts_at,
    ).values_list("instructor_id", flat=True))
    preferred_ids = set(AvailabilityBlock.objects.filter(
        status=AvailabilityBlock.Status.AVAILABLE,
        starts_at__lt=session.ends_at,
        ends_at__gt=session.starts_at,
    ).values_list("instructor_id", flat=True))
    tentative_ids = set(AvailabilityBlock.objects.filter(
        status=AvailabilityBlock.Status.TENTATIVE,
        starts_at__lt=session.ends_at,
        ends_at__gt=session.starts_at,
    ).values_list("instructor_id", flat=True))
    local_start = timezone.localtime(session.starts_at)
    local_end = timezone.localtime(session.ends_at)
    final_date = (local_end - timedelta(microseconds=1)).date()
    recurring_rules = RecurringAvailabilityRule.objects.filter(
        instructor_id__in=instructors.values("pk"),
        starts_on__lte=final_date,
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=local_start.date()))
    for rule in recurring_rules:
        if not rule.overlaps(session.starts_at, session.ends_at):
            continue
        if rule.status == AvailabilityBlock.Status.UNAVAILABLE:
            unavailable_ids.add(rule.instructor_id)
        elif rule.status == AvailabilityBlock.Status.AVAILABLE:
            preferred_ids.add(rule.instructor_id)
        elif rule.status == AvailabilityBlock.Status.TENTATIVE:
            tentative_ids.add(rule.instructor_id)
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
    return (
        instructors.select_related("home_organization")
        .distinct()
        .annotate(
            availability_rank=Case(
                When(pk__in=preferred_ids, then=Value(0)),
                When(pk__in=tentative_ids, then=Value(2)),
                default=Value(1),
                output_field=IntegerField(),
            ),
            session_availability=Case(
                When(pk__in=preferred_ids, then=Value("available")),
                When(pk__in=tentative_ids, then=Value("tentative")),
                default=Value("unspecified"),
                output_field=CharField(),
            ),
        )
        .order_by("availability_rank", "last_name", "first_name")
    )


def sync_verified_course_authorizations(instructor, courses, verified_by):
    """Replace an instructor's active course authorizations with a verified selection."""

    selected_courses = list(courses)
    selected_ids = {course.pk for course in selected_courses}
    verified_at = timezone.now()

    instructor.course_authorizations.filter(
        status=CourseAuthorization.Status.ACTIVE,
    ).exclude(course_id__in=selected_ids).update(
        status=CourseAuthorization.Status.SUSPENDED,
        verified_by=verified_by,
        verified_at=verified_at,
    )

    for course in selected_courses:
        authorization, created = CourseAuthorization.objects.get_or_create(
            instructor=instructor,
            course=course,
            defaults={
                "status": CourseAuthorization.Status.ACTIVE,
                "can_lead": True,
                "can_assist": True,
                "verified_by": verified_by,
                "verified_at": verified_at,
            },
        )
        if created:
            continue
        update_fields = ["status", "verified_by", "verified_at"]
        if authorization.status != CourseAuthorization.Status.ACTIVE:
            authorization.effective_date = None
            authorization.expiration_date = None
            update_fields.extend(("effective_date", "expiration_date"))
        authorization.status = CourseAuthorization.Status.ACTIVE
        authorization.verified_by = verified_by
        authorization.verified_at = verified_at
        authorization.save(update_fields=update_fields)
