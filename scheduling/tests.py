from datetime import date, datetime, time, timedelta

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
    CourseUnit,
    Instructor,
    InstructorAssignment,
    Organization,
    RecurringAvailabilityRule,
    TrainingEvent,
    TrainingSession,
)
from scheduling.permissions import can_manage_organization
from scheduling.services import eligible_instructors_for_session
from scheduling.unit_staffing import sync_event_units


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


class InstructorAuthorizationRequestTests(SchedulingTestCase):
    @override_settings(DEBUG=False)
    def test_linked_instructor_can_submit_course_claim_for_state_approval(self):
        user = get_user_model().objects.create_user(
            username="authorized.instructor@example.com",
            email="authorized.instructor@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Authorized", self.jefferson)
        instructor.user = user
        instructor.save(update_fields=("user",))
        requested_course = Course.objects.create(
            record_number="99-99-9100",
            name="Requested Authorization Course",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("instructor_authorizations", args=(instructor.pk,)),
            {"courses": [requested_course.pk]},
        )

        self.assertRedirects(
            response,
            reverse("instructor_authorizations", args=(instructor.pk,)),
        )
        authorization = CourseAuthorization.objects.get(
            instructor=instructor,
            course=requested_course,
        )
        self.assertEqual(authorization.status, CourseAuthorization.Status.PENDING)
        self.assertIsNone(authorization.verified_by)
        self.assertIsNone(authorization.verified_at)

    @override_settings(DEBUG=False)
    def test_instructor_cannot_manage_another_instructors_authorizations(self):
        user = get_user_model().objects.create_user(
            username="first.instructor@example.com",
            password="test-password",
        )
        own_profile = self.make_instructor("First", self.jefferson)
        own_profile.user = user
        own_profile.save(update_fields=("user",))
        other_profile = self.make_instructor("Other", self.lewis)
        self.client.force_login(user)

        response = self.client.get(
            reverse("instructor_authorizations", args=(other_profile.pk,))
        )

        self.assertEqual(response.status_code, 403)


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

    def test_recurring_work_schedule_excludes_instructor_during_that_time(self):
        instructor = self.make_instructor("Recurring", self.jefferson)
        RecurringAvailabilityRule.objects.create(
            instructor=instructor,
            status=AvailabilityBlock.Status.UNAVAILABLE,
            weekdays="0,1,2,3,4",
            start_time=time(8, 0),
            end_time=time(17, 0),
            starts_on=date(2026, 9, 1),
            notes="Regular work schedule",
        )

        eligible = eligible_instructors_for_session(
            self.session,
            InstructorAssignment.Role.ASSISTANT,
        )

        self.assertNotIn(instructor, eligible)

    def test_recurring_schedule_does_not_block_outside_its_hours(self):
        instructor = self.make_instructor("Evening", self.jefferson)
        RecurringAvailabilityRule.objects.create(
            instructor=instructor,
            status=AvailabilityBlock.Status.UNAVAILABLE,
            weekdays="0,1,2,3,4",
            start_time=time(17, 0),
            end_time=time(21, 0),
            starts_on=date(2026, 9, 1),
        )

        eligible = eligible_instructors_for_session(
            self.session,
            InstructorAssignment.Role.ASSISTANT,
        )

        self.assertIn(instructor, eligible)


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
    def test_availability_page_renders_one_selectable_week(self):
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
            {"week": "2026-09-10"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sep 7 – Sep 13, 2026")
        self.assertContains(response, 'data-calendar-date="2026-09-10"')
        self.assertContains(response, 'data-selectable-date="2026-09-10"')
        self.assertNotContains(response, 'data-calendar-date="2026-09-14"')
        self.assertContains(response, "Select one or more dates on the calendar below")
        self.assertContains(response, "data-save-availability")
        self.assertNotContains(response, "data-availability-selection method=\"post\" hidden")

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
                "week": "2026-09-07",
                "status": AvailabilityBlock.Status.AVAILABLE,
                "all_day": "on",
                "starts_at": "2026-09-14T00:00",
                "ends_at": "2026-09-17T00:00",
                "notes": "Preferred three-day window",
            },
        )

        self.assertRedirects(
            response,
            f'{reverse("instructor_availability", args=(instructor.pk,))}?week=2026-09-07',
        )
        entry = instructor.availability_blocks.get()
        self.assertTrue(entry.all_day)
        self.assertEqual(entry.all_day_end_date.isoformat(), "2026-09-16")

    @override_settings(DEBUG=False)
    def test_instructor_can_create_recurring_weekday_work_schedule(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="recurring@example.com",
            email="recurring@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Repeating", self.jefferson)
        instructor.user = user
        instructor.save(update_fields=("user",))
        self.client.force_login(user)

        response = self.client.post(
            reverse("recurring_availability_create", args=(instructor.pk,)),
            {
                "return_week": "2026-09-07",
                "status": AvailabilityBlock.Status.UNAVAILABLE,
                "weekdays": ["0", "1", "2", "3", "4"],
                "start_time": "08:00",
                "end_time": "17:00",
                "starts_on": "2026-09-01",
                "ends_on": "",
                "notes": "Regular work schedule",
            },
        )

        self.assertRedirects(
            response,
            f'{reverse("instructor_availability", args=(instructor.pk,))}?week=2026-09-07',
        )
        rule = instructor.recurring_availability_rules.get()
        self.assertEqual(rule.weekdays, "0,1,2,3,4")
        self.assertEqual(rule.weekday_summary, "Monday–Friday")
        self.assertEqual(rule.start_time, time(8, 0))
        self.assertIsNone(rule.ends_on)
        weekly_page = self.client.get(
            reverse("instructor_availability", args=(instructor.pk,)),
            {"week": "2026-09-07"},
        )
        self.assertContains(weekly_page, "Regular work schedule")
        self.assertContains(weekly_page, "8:00 AM–5:00 PM", count=7)

    @override_settings(DEBUG=False)
    def test_all_day_weekly_schedule_does_not_require_times(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="weekend@example.com",
            password="test-password",
        )
        instructor = self.make_instructor("Weekend", self.jefferson)
        instructor.user = user
        instructor.save(update_fields=("user",))
        self.client.force_login(user)

        response = self.client.post(
            reverse("recurring_availability_create", args=(instructor.pk,)),
            {
                "status": AvailabilityBlock.Status.AVAILABLE,
                "weekdays": ["5", "6"],
                "all_day": "on",
                "starts_on": "2026-09-01",
                "ends_on": "",
                "notes": "Preferred weekends",
            },
        )

        self.assertRedirects(
            response,
            reverse("instructor_availability", args=(instructor.pk,)),
        )
        rule = instructor.recurring_availability_rules.get()
        self.assertTrue(rule.all_day)
        self.assertEqual(rule.weekday_summary, "Saturday–Sunday")
        self.assertIsNone(rule.start_time)
        self.assertIsNone(rule.end_time)

    @override_settings(DEBUG=False)
    def test_instructor_cannot_edit_another_instructors_weekly_schedule(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="rule-owner@example.com",
            password="test-password",
        )
        own_instructor = self.make_instructor("Owner", self.jefferson)
        own_instructor.user = user
        own_instructor.save(update_fields=("user",))
        other_instructor = self.make_instructor("Other Rule", self.lewis)
        rule = RecurringAvailabilityRule.objects.create(
            instructor=other_instructor,
            status=AvailabilityBlock.Status.UNAVAILABLE,
            weekdays="0,1,2,3,4",
            start_time=time(8),
            end_time=time(17),
            starts_on=date(2026, 9, 1),
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse(
                "recurring_availability_edit",
                args=(other_instructor.pk, rule.pk),
            )
        )

        self.assertEqual(response.status_code, 403)

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
        self.assertEqual(course.units.count(), 25)
        self.assertEqual(course.units.get(unit_number=1).required_instructors, 2)
        self.assertEqual(course.units.get(unit_number=2).required_instructors, 1)
        self.assertEqual(course.units.get(unit_number=3).required_instructors, 3)
        self.assertEqual(course.units.get(unit_number=14).required_instructors, 4)
        self.assertEqual(
            sum(course.units.values_list("required_instructors", flat=True)),
            49,
        )

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

        course = Course.objects.get(record_number="99-99-9999")
        self.assertRedirects(response, reverse("course_edit", args=(course.pk,)))

    @override_settings(DEBUG=True)
    def test_befo_event_has_one_scheduling_row_per_unit_and_matrix_staffing(self):
        course = Course.objects.get(record_number="01-05-0101")
        jefferson = Organization.objects.get(name="Jefferson County")
        event = TrainingEvent.objects.create(
            course=course,
            host_organization=jefferson,
            status=TrainingEvent.Status.PROPOSED,
            location_name="Watertown",
        )

        self.assertEqual(sync_event_units(event), 25)
        self.assertEqual(event.sessions.count(), 25)
        self.assertEqual(
            event.sessions.get(course_unit__unit_number=14).course_unit.required_instructors,
            4,
        )

        response = self.client.get(reverse("training_detail", args=(event.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_required"], 49)
        self.assertEqual(response.context["total_open"], 49)
        self.assertEqual(response.context["unscheduled_units"], 25)
        self.assertContains(response, "Unit 14")
        self.assertContains(response, "4 instructors required")

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
