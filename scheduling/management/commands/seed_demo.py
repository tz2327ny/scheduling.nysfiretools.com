from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from scheduling.models import (
    Course,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
    Organization,
    TrainingEvent,
    TrainingSession,
)


class Command(BaseCommand):
    help = "Create a small, repeatable demonstration dataset."

    def handle(self, *args, **options):
        organizations = {}
        organization_rows = (
            ("Jefferson County", "Jefferson", Organization.Kind.COUNTY),
            ("Lewis County", "Lewis", Organization.Kind.COUNTY),
            ("St. Lawrence County", "St. Lawrence", Organization.Kind.COUNTY),
            ("Oswego County", "Oswego", Organization.Kind.COUNTY),
            (
                "New York State Academy of Fire Science",
                "NYS Academy",
                Organization.Kind.ACADEMY,
            ),
        )
        for order, (name, short_name, kind) in enumerate(organization_rows, start=1):
            organizations[short_name], _ = Organization.objects.update_or_create(
                name=name,
                defaults={
                    "short_name": short_name,
                    "kind": kind,
                    "active": True,
                    "display_order": order,
                },
            )

        course_rows = (
            ("01-05-0050", "Firefighter Survival", 4, 6, True),
            ("01-05-0005", "Basic Exterior Firefighting Operations", 2, 3, False),
            ("01-05-0006", "Interior Firefighting Operations", 3, 4, True),
            ("01-06-0007", "Pump Operations", 2, 2, False),
        )
        courses = {}
        for record_number, name, minimum, recommended, intensive in course_rows:
            courses[record_number], _ = Course.objects.update_or_create(
                record_number=record_number,
                defaults={
                    "name": name,
                    "minimum_instructors": minimum,
                    "recommended_instructors": recommended,
                    "instructor_intensive": intensive,
                    "active": True,
                },
            )

        instructor_rows = (
            ("Mara", "Chen", "Jefferson", Instructor.TravelPreference.CONTACT_ME),
            ("Daniel", "Ortiz", "Jefferson", Instructor.TravelPreference.CONTACT_ME),
            ("Elena", "Brooks", "Lewis", Instructor.TravelPreference.LIMITED),
            ("Marcus", "Reed", "St. Lawrence", Instructor.TravelPreference.CONTACT_ME),
            ("Tessa", "Morgan", "Oswego", Instructor.TravelPreference.CONTACT_ME),
            ("Aaron", "Bell", "NYS Academy", Instructor.TravelPreference.CONTACT_ME),
            ("Nina", "Patel", "NYS Academy", Instructor.TravelPreference.CONTACT_ME),
            ("James", "Walsh", "Lewis", Instructor.TravelPreference.LOCAL_ONLY),
        )
        instructors = []
        for first_name, last_name, organization, travel in instructor_rows:
            instructor, _ = Instructor.objects.update_or_create(
                first_name=first_name,
                last_name=last_name,
                defaults={
                    "home_organization": organizations[organization],
                    "email": f"{first_name}.{last_name}@example.test".lower(),
                    "travel_preference": travel,
                    "active": True,
                },
            )
            instructors.append(instructor)
            for course in courses.values():
                CourseAuthorization.objects.update_or_create(
                    instructor=instructor,
                    course=course,
                    defaults={
                        "status": CourseAuthorization.Status.ACTIVE,
                        "can_lead": True,
                        "can_assist": True,
                    },
                )

        TrainingEvent.objects.filter(notes="Demo schedule record").delete()
        today = timezone.localdate()
        event_rows = (
            (
                "01-05-0050", "01-01-03-048", "Jefferson", 5,
                TrainingEvent.Status.CONFIRMED, "Watertown Fire Training Center", 3,
            ),
            (
                "01-05-0005", None, "Lewis", 7,
                TrainingEvent.Status.PROPOSED, "Lewis County Public Safety Building", 2,
            ),
            (
                "01-05-0006", None, "Oswego", 8,
                TrainingEvent.Status.PROPOSED, "Oswego County Training Grounds", 2,
            ),
            (
                "01-06-0007", "01-01-03-049", "St. Lawrence", 12,
                TrainingEvent.Status.CONFIRMED, "Canton Fire Training Center", 2,
            ),
            (
                "01-05-0050", None, "Oswego", 18,
                TrainingEvent.Status.PROPOSED, "Mexico Fire Department", 1,
            ),
        )
        for index, row in enumerate(event_rows):
            (
                record_number,
                offering_number,
                host,
                offset,
                status,
                location,
                assignment_count,
            ) = row
            event = TrainingEvent.objects.create(
                course=courses[record_number],
                host_organization=organizations[host],
                status=status,
                offering_number=offering_number,
                location_name=location,
                notes="Demo schedule record",
                acadis_registration_url="https://www.dhses.ny.gov/academy-fire-science",
            )
            starts_at = timezone.make_aware(
                datetime.combine(today + timedelta(days=offset), time(hour=8, minute=0))
            )
            session = TrainingSession.objects.create(
                event=event,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(hours=8),
            )
            start_index = index % len(instructors)
            for position in range(assignment_count):
                instructor = instructors[(start_index + position) % len(instructors)]
                InstructorAssignment.objects.create(
                    session=session,
                    instructor=instructor,
                    role=(
                        InstructorAssignment.Role.LEAD
                        if position == 0
                        else InstructorAssignment.Role.ASSISTANT
                    ),
                )

        self.stdout.write(self.style.SUCCESS("Demo organizations, courses, instructors, and training created."))
