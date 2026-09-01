from .course_matrix import parse_unit_count, parse_unit_staffing
from .models import CourseUnit, TrainingSession


def sync_course_units(course, overwrite=False):
    count = parse_unit_count(course.number_of_units)
    if not count:
        return 0
    staffing = parse_unit_staffing(
        count,
        course.instructor_requirements,
        course.safety_officer_requirements,
    )
    for unit_number, requirements in staffing.items():
        CourseUnit.objects.get_or_create(
            course=course,
            unit_number=unit_number,
            defaults=requirements,
        )
        if overwrite:
            CourseUnit.objects.filter(course=course, unit_number=unit_number).update(
                **requirements
            )
    return count


def sync_event_units(event):
    units = list(event.course.units.filter(active=True).order_by("unit_number"))
    if not units:
        return 0
    assigned_unit_ids = set(
        event.sessions.exclude(course_unit__isnull=True).values_list("course_unit_id", flat=True)
    )
    unassigned_sessions = list(
        event.sessions.filter(course_unit__isnull=True).order_by("starts_at", "pk")
    )
    available_units = [unit for unit in units if unit.pk not in assigned_unit_ids]
    for session, unit in zip(unassigned_sessions, available_units):
        session.course_unit = unit
        session.save(update_fields=("course_unit",))
        assigned_unit_ids.add(unit.pk)
    TrainingSession.objects.bulk_create(
        [
            TrainingSession(event=event, course_unit=unit)
            for unit in units
            if unit.pk not in assigned_unit_ids
        ],
        ignore_conflicts=True,
    )
    return len(units)
