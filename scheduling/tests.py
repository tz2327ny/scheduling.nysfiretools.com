from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserOrganizationRole
from scheduling.models import (
    AvailabilityBlock,
    Course,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
    Organization,
    TrainingEvent,
    TrainingSession,
)
from scheduling.permissions import can_manage_organization
from scheduling.services import eligible_instructors_for_session


class SchedulingTestCase(TestCase):
    def setUp(self):
        self.jefferson = Organization.objects.get(name="Jefferson County")
        self.lewis = Organization.objects.get(name="Lewis County")
        self.academy = Organization.objects.get(
            name="New York State Academy of Fire Science"
        )
        self.course = Course.objects.create(
            record_number="01-05-0050",
            name="Firefighter Survival",
            minimum_instructors=4,
            recommended_instructors=6,
            instructor_intensive=True,
        )
        self.event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.jefferson,
            status=TrainingEvent.Status.CONFIRMED,
            offering_number="01-01-03-048",
            location_name="Watertown",
        )
        self.starts_at = timezone.make_aware(datetime(2026, 9, 10, 8, 0))
        self.session = TrainingSession.objects.create(
            event=self.event,
            starts_at=self.starts_at,
            ends_at=self.starts_at + timedelta(hours=8),
        )

    def make_instructor(self, first_name, organization, travel=Instructor.TravelPreference.CONTACT_ME):
        instructor = Instructor.objects.create(
            first_name=first_name,
            last_name="Instructor",
            home_organization=organization,
            travel_preference=travel,
        )
        CourseAuthorization.objects.create(
            instructor=instructor,
            course=self.course,
            status=CourseAuthorization.Status.ACTIVE,
            can_lead=True,
            can_assist=True,
        )
        return instructor


class InstructorConflictTests(SchedulingTestCase):
    def test_overlapping_assignment_is_rejected(self):
        instructor = self.make_instructor("Mara", self.jefferson)
        InstructorAssignment.objects.create(
            session=self.session,
            instructor=instructor,
            role=InstructorAssignment.Role.LEAD,
        )
        second_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            status=TrainingEvent.Status.PROPOSED,
            location_name="Lowville",
        )
        overlapping = TrainingSession.objects.create(
            event=second_event,
            starts_at=self.starts_at + timedelta(hours=2),
            ends_at=self.starts_at + timedelta(hours=6),
        )

        with self.assertRaises(ValidationError):
            InstructorAssignment.objects.create(
                session=overlapping,
                instructor=instructor,
                role=InstructorAssignment.Role.ASSISTANT,
            )

    def test_non_overlapping_assignment_is_allowed(self):
        instructor = self.make_instructor("Mara", self.jefferson)
        InstructorAssignment.objects.create(
            session=self.session,
            instructor=instructor,
            role=InstructorAssignment.Role.LEAD,
        )
        second_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            location_name="Lowville",
        )
        later_session = TrainingSession.objects.create(
            event=second_event,
            starts_at=self.session.ends_at + timedelta(hours=1),
            ends_at=self.session.ends_at + timedelta(hours=5),
        )

        assignment = InstructorAssignment.objects.create(
            session=later_session,
            instructor=instructor,
            role=InstructorAssignment.Role.ASSISTANT,
        )
        self.assertIsNotNone(assignment.pk)


class EligibleInstructorTests(SchedulingTestCase):
    def test_matching_includes_travelers_and_excludes_local_only_outside_home(self):
        home_instructor = self.make_instructor("Home", self.jefferson)
        regional_instructor = self.make_instructor("Regional", self.academy)
        local_only = self.make_instructor(
            "Local",
            self.lewis,
            travel=Instructor.TravelPreference.LOCAL_ONLY,
        )

        eligible = list(
            eligible_instructors_for_session(
                self.session, InstructorAssignment.Role.ASSISTANT
            )
        )

        self.assertIn(home_instructor, eligible)
        self.assertIn(regional_instructor, eligible)
        self.assertNotIn(local_only, eligible)

    def test_matching_excludes_instructor_assigned_elsewhere(self):
        instructor = self.make_instructor("Busy", self.academy)
        other_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            location_name="Lowville",
        )
        other_session = TrainingSession.objects.create(
            event=other_event,
            starts_at=self.starts_at,
            ends_at=self.starts_at + timedelta(hours=3),
        )
        InstructorAssignment.objects.create(
            session=other_session,
            instructor=instructor,
            role=InstructorAssignment.Role.LEAD,
        )

        eligible = eligible_instructors_for_session(
            self.session, InstructorAssignment.Role.ASSISTANT
        )

        self.assertNotIn(instructor, eligible)

    def test_preferred_availability_is_ranked_first(self):
        default_instructor = self.make_instructor("Able", self.jefferson)
        preferred_instructor = self.make_instructor("Zulu", self.jefferson)
        AvailabilityBlock.objects.create(
            instructor=preferred_instructor,
            status=AvailabilityBlock.Status.AVAILABLE,
            starts_at=self.session.starts_at,
            ends_at=self.session.ends_at,
            notes="Preferred daytime assignment",
        )

        eligible = list(
            eligible_instructors_for_session(
                self.session,
                InstructorAssignment.Role.ASSISTANT,
            )
        )

        self.assertLess(
            eligible.index(preferred_instructor),
            eligible.index(default_instructor),
        )
        self.assertEqual(
            eligible[eligible.index(preferred_instructor)].session_availability,
            "available",
        )

    def test_unavailable_instructor_is_excluded(self):
        instructor = self.make_instructor("Unavailable", self.jefferson)
        AvailabilityBlock.objects.create(
            instructor=instructor,
            status=AvailabilityBlock.Status.UNAVAILABLE,
            starts_at=self.session.starts_at,
            ends_at=self.session.ends_at,
        )

        eligible = eligible_instructors_for_session(
            self.session,
            InstructorAssignment.Role.ASSISTANT,
        )

        self.assertNotIn(instructor, eligible)


class CountyPermissionTests(SchedulingTestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.admin = User.objects.create_user(username="jefferson-admin", password="test-password")
        UserOrganizationRole.objects.create(
            user=self.admin,
            organization=self.jefferson,
            role=UserOrganizationRole.Role.ADMINISTRATOR,
        )
        self.jefferson_instructor = self.make_instructor("Jefferson", self.jefferson)
        self.lewis_instructor = self.make_instructor("Lewis", self.lewis)
        self.client.force_login(self.admin)

    def test_admin_manages_only_assigned_organization(self):
        self.assertTrue(can_manage_organization(self.admin, self.jefferson))
        self.assertFalse(can_manage_organization(self.admin, self.lewis))

    @override_settings(DEBUG=False)
    def test_admin_can_edit_own_instructor_but_not_other_county(self):
        own_response = self.client.get(
            reverse("instructor_edit", args=(self.jefferson_instructor.pk,))
        )
        other_response = self.client.get(
            reverse("instructor_edit", args=(self.lewis_instructor.pk,))
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 403)

    @override_settings(DEBUG=False)
    def test_admin_cannot_edit_other_county_training(self):
        lewis_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            location_name="Lowville",
        )

        response = self.client.get(reverse("training_edit", args=(lewis_event.pk,)))

        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=False)
    def test_admin_manages_availability_only_for_assigned_organization(self):
        own_response = self.client.get(
            reverse("availability_create", args=(self.jefferson_instructor.pk,))
        )
        other_response = self.client.get(
            reverse("availability_create", args=(self.lewis_instructor.pk,))
        )

        self.assertEqual(own_response.status_code, 200)
        self.assertEqual(other_response.status_code, 403)


class AuthenticationTests(TestCase):
    @override_settings(DEBUG=False)
    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")


class TrainingStatusTests(TestCase):
    def test_training_statuses_match_operational_workflow(self):
        self.assertEqual(
            list(TrainingEvent.Status.choices),
            [
                ("proposed", "Purposed"),
                ("confirmed", "Confirmed"),
                ("completed", "Completed"),
                ("canceled", "Cancelled"),
            ],
        )


class CourseOfferingNumberTests(SchedulingTestCase):
    def test_purposed_training_can_be_saved_without_offering_number(self):
        event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            status=TrainingEvent.Status.PROPOSED,
            location_name="Lowville",
        )

        self.assertIsNone(event.offering_number)

    def test_training_cannot_be_confirmed_without_offering_number(self):
        event = TrainingEvent(
            course=self.course,
            host_organization=self.lewis,
            status=TrainingEvent.Status.CONFIRMED,
            location_name="Lowville",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Enter the Course Offering Number before confirming this training.",
        ):
            event.save()

    def test_confirmed_training_accepts_unique_offering_number(self):
        event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            status=TrainingEvent.Status.CONFIRMED,
            offering_number="01-01-03-049",
            location_name="Lowville",
        )

        self.assertEqual(event.offering_number, "01-01-03-049")


class AvailabilityTests(SchedulingTestCase):
    def test_operational_availability_choices(self):
        self.assertEqual(
            list(AvailabilityBlock.Status.choices),
            [
                ("available", "Available (preferred time)"),
                ("tentative", "Tentative"),
                ("unavailable", "Unavailable"),
            ],
        )

    def test_overlapping_entries_are_rejected(self):
        instructor = self.make_instructor("Available", self.jefferson)
        AvailabilityBlock.objects.create(
            instructor=instructor,
            status=AvailabilityBlock.Status.AVAILABLE,
            starts_at=self.session.starts_at,
            ends_at=self.session.ends_at,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "This entry overlaps another availability entry for this instructor.",
        ):
            AvailabilityBlock.objects.create(
                instructor=instructor,
                status=AvailabilityBlock.Status.TENTATIVE,
                starts_at=self.session.starts_at + timedelta(hours=1),
                ends_at=self.session.ends_at + timedelta(hours=1),
            )

    def test_all_day_entry_covers_each_selected_day(self):
        instructor = self.make_instructor("All Day", self.jefferson)
        entry = AvailabilityBlock.objects.create(
            instructor=instructor,
            status=AvailabilityBlock.Status.AVAILABLE,
            all_day=True,
            starts_at=timezone.make_aware(datetime(2026, 9, 14, 8, 0)),
            ends_at=timezone.make_aware(datetime(2026, 9, 16, 17, 0)),
        )

        self.assertEqual(timezone.localtime(entry.starts_at).isoformat(), "2026-09-14T00:00:00-04:00")
        self.assertEqual(timezone.localtime(entry.ends_at).isoformat(), "2026-09-17T00:00:00-04:00")
        self.assertEqual(entry.all_day_end_date.isoformat(), "2026-09-16")

    @override_settings(DEBUG=False)
    def test_linked_instructor_can_manage_own_availability(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="instructor@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Self", self.jefferson)
        instructor.user = user
        instructor.save()
        self.client.force_login(user)

        response = self.client.get(
            reverse("availability_create", args=(instructor.pk,))
        )

        self.assertEqual(response.status_code, 200)

    @override_settings(DEBUG=False)
    def test_availability_page_renders_selectable_month_calendar(self):
        User = get_user_model()
        administrator = User.objects.create_superuser(
            username="calendar-admin@example.com",
            email="calendar-admin@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Calendar", self.jefferson)
        self.client.force_login(administrator)

        response = self.client.get(
            reverse("instructor_availability", args=(instructor.pk,)),
            {"month": "2026-09"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "September 2026")
        self.assertContains(response, 'data-calendar-date="2026-09-14"')
        self.assertContains(response, 'data-selectable-date="2026-09-14"')

    @override_settings(DEBUG=False)
    def test_calendar_quick_form_creates_all_day_availability(self):
        User = get_user_model()
        administrator = User.objects.create_superuser(
            username="quick-admin@example.com",
            email="quick-admin@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Quick", self.jefferson)
        self.client.force_login(administrator)

        response = self.client.post(
            reverse("instructor_availability", args=(instructor.pk,)),
            {
                "month": "2026-09",
                "status": AvailabilityBlock.Status.AVAILABLE,
                "all_day": "on",
                "starts_at": "2026-09-14T00:00",
                "ends_at": "2026-09-17T00:00",
                "notes": "Preferred three-day window",
            },
        )

        self.assertRedirects(
            response,
            f'{reverse("instructor_availability", args=(instructor.pk,))}?month=2026-09',
        )
        entry = instructor.availability_blocks.get()
        self.assertTrue(entry.all_day)
        self.assertEqual(entry.all_day_end_date.isoformat(), "2026-09-16")

    @override_settings(DEBUG=False)
    def test_calendar_range_prefills_availability_form(self):
        User = get_user_model()
        administrator = User.objects.create_superuser(
            username="range-admin@example.com",
            email="range-admin@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Range", self.jefferson)
        self.client.force_login(administrator)

        response = self.client.get(
            reverse("availability_create", args=(instructor.pk,)),
            {"start": "2026-09-14", "end": "2026-09-16"},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(timezone.localtime(form.instance.starts_at).date().isoformat(), "2026-09-14")
        self.assertEqual(timezone.localtime(form.instance.ends_at).date().isoformat(), "2026-09-16")


class CourseManagementTests(TestCase):
    def test_state_course_matrix_is_prepopulated_and_editable(self):
        course = Course.objects.get(record_number="01-05-0101")

        self.assertEqual(
            course.name,
            "2021 BASIC EXTERIOR FIREFIGHTING OPERATIONS W/ HAZARDOUS MATERIALS FIRST RESPONDER OPERATIONS (BEFO W/ HMFRO)",
        )
        self.assertEqual(course.number_of_units, "25")
        self.assertIn("2nd - Units", course.instructor_requirements)
        self.assertEqual(course.matrix_source, "OFPC Course Matrix 5/7/2025")

        course.name = "Locally adjusted title"
        course.save()
        self.assertEqual(Course.objects.get(pk=course.pk).name, "Locally adjusted title")

    @override_settings(DEBUG=False)
    def test_system_administrator_can_create_course(self):
        User = get_user_model()
        administrator = User.objects.create_superuser(
            username="system@example.com",
            email="system@example.com",
            password="test-password",
        )
        self.client.force_login(administrator)

        response = self.client.post(
            reverse("course_create"),
            {
                "record_number": "99-99-9999",
                "name": "Test Course",
                "description": "Used to verify course management.",
                "instructor_intensive": "on",
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("course_list"))
        self.assertTrue(Course.objects.filter(record_number="99-99-9999").exists())

    @override_settings(DEBUG=False)
    def test_non_system_administrator_cannot_create_course(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="county@example.com",
            password="test-password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("course_create"))

        self.assertEqual(response.status_code, 403)
