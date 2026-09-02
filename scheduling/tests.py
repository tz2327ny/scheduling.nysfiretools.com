from datetime import date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserOrganizationRole
from scheduling.forms import InstructorAssignmentForm
from scheduling.models import (
    AvailabilityBlock,
    Course,
    CourseAuthorization,
    CourseUnit,
    Instructor,
    InstructorAssignment,
    NotificationDelivery,
    NotificationPreference,
    Organization,
    RecurringAvailabilityRule,
    TrainingEvent,
    TrainingSession,
)
from scheduling.permissions import can_manage_organization
from scheduling.services import eligible_instructors_for_session
from scheduling.notifications import notify_assignment
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
    def test_assignment_dropdown_lists_each_authorized_instructor_once(self):
        instructor = self.make_instructor("Pete", self.jefferson)
        for index in range(3):
            other_course = Course.objects.create(
                record_number=f"99-88-100{index}",
                name=f"Additional Authorization {index}",
            )
            CourseAuthorization.objects.create(
                instructor=instructor,
                course=other_course,
                status=CourseAuthorization.Status.ACTIVE,
                can_lead=True,
                can_assist=True,
            )

        form = InstructorAssignmentForm(session=self.session)
        instructor_ids = list(
            form.fields["instructor"].queryset.values_list("pk", flat=True)
        )

        self.assertEqual(instructor_ids.count(instructor.pk), 1)

    def test_role_permission_must_belong_to_the_scheduled_course(self):
        instructor = self.make_instructor("CourseSpecific", self.jefferson)
        scheduled_course_authorization = instructor.course_authorizations.get(
            course=self.course
        )
        scheduled_course_authorization.can_assist = False
        scheduled_course_authorization.save(update_fields=("can_assist",))
        other_course = Course.objects.create(
            record_number="99-88-2000",
            name="Unrelated Assistant Authorization",
        )
        CourseAuthorization.objects.create(
            instructor=instructor,
            course=other_course,
            status=CourseAuthorization.Status.ACTIVE,
            can_lead=True,
            can_assist=True,
        )

        eligible = eligible_instructors_for_session(
            self.session,
            InstructorAssignment.Role.ASSISTANT,
        )

        self.assertNotIn(instructor, eligible)

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

    @override_settings(DEBUG=False)
    def test_admin_can_create_scheduling_only_instructor_without_sfi_number(self):
        response = self.client.post(
            reverse("instructor_create"),
            {
                "first_name": "Schedule",
                "last_name": "Only",
                "sfi_number": "",
                "email": "schedule.only@example.com",
                "phone": "",
                "home_organization": self.jefferson.pk,
                "travel_preference": Instructor.TravelPreference.CONTACT_ME,
                "travel_notes": "",
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("instructor_list"))
        instructor = Instructor.objects.get(email="schedule.only@example.com")
        self.assertEqual(instructor.sfi_number, "")
        self.assertIsNone(instructor.user_id)

    @override_settings(DEBUG=False)
    def test_site_admin_can_create_scheduling_only_instructor_with_authorization(self):
        site_admin = get_user_model().objects.create_superuser(
            username="site.admin@example.com",
            email="site.admin@example.com",
            password="test-password",
        )
        self.client.force_login(site_admin)

        response = self.client.post(
            reverse("instructor_create"),
            {
                "first_name": "Qualified",
                "last_name": "Instructor",
                "sfi_number": "SFI-2048",
                "email": "qualified@example.com",
                "phone": "",
                "home_organization": self.jefferson.pk,
                "travel_preference": Instructor.TravelPreference.CONTACT_ME,
                "travel_notes": "",
                "active": "on",
                "verified_courses": [self.course.pk],
            },
        )

        self.assertRedirects(response, reverse("instructor_list"))
        instructor = Instructor.objects.get(email="qualified@example.com")
        authorization = CourseAuthorization.objects.get(
            instructor=instructor,
            course=self.course,
        )
        self.assertEqual(authorization.status, CourseAuthorization.Status.ACTIVE)
        self.assertEqual(authorization.verified_by, site_admin)
        self.assertIsNotNone(authorization.verified_at)
        self.assertIn(
            instructor,
            eligible_instructors_for_session(
                self.session,
                InstructorAssignment.Role.ASSISTANT,
            ),
        )

    @override_settings(DEBUG=False)
    def test_county_admin_cannot_self_verify_course_authorizations(self):
        response = self.client.post(
            reverse("instructor_create"),
            {
                "first_name": "County",
                "last_name": "Created",
                "sfi_number": "SFI-4096",
                "email": "county.created@example.com",
                "phone": "",
                "home_organization": self.jefferson.pk,
                "travel_preference": Instructor.TravelPreference.CONTACT_ME,
                "travel_notes": "",
                "active": "on",
                "verified_courses": [self.course.pk],
            },
        )

        self.assertRedirects(response, reverse("instructor_list"))
        instructor = Instructor.objects.get(email="county.created@example.com")
        self.assertFalse(instructor.course_authorizations.exists())


class AuthenticationTests(TestCase):
    @override_settings(DEBUG=False)
    def test_anonymous_dashboard_redirects_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")


class DashboardOrganizationFilterTests(SchedulingTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="dashboard@example.com",
            password="test-password",
        )
        self.client.force_login(self.user)
        self.lewis_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.lewis,
            status=TrainingEvent.Status.PROPOSED,
            location_name="Lowville",
        )
        TrainingSession.objects.create(
            event=self.lewis_event,
            starts_at=self.starts_at + timedelta(days=1),
            ends_at=self.starts_at + timedelta(days=1, hours=4),
        )
        self.academy_event = TrainingEvent.objects.create(
            course=self.course,
            host_organization=self.academy,
            status=TrainingEvent.Status.PROPOSED,
            location_name="Montour Falls",
        )
        TrainingSession.objects.create(
            event=self.academy_event,
            starts_at=self.starts_at + timedelta(days=2),
            ends_at=self.starts_at + timedelta(days=2, hours=4),
        )

    @override_settings(DEBUG=False)
    def test_dashboard_defaults_to_all_scheduled_courses(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["show_all_organizations"])
        self.assertEqual(response.context["scope_label"], "All scheduled courses")
        self.assertEqual(response.context["upcoming_total_count"], 3)
        self.assertEqual(response.context["confirmed_count"], 1)
        self.assertEqual(response.context["proposed_count"], 2)

    @override_settings(DEBUG=False)
    def test_dashboard_can_show_one_county(self):
        response = self.client.get(
            reverse("dashboard"),
            {"organization": [self.jefferson.pk]},
        )

        self.assertFalse(response.context["show_all_organizations"])
        self.assertEqual(
            response.context["selected_organization_ids"],
            {self.jefferson.pk},
        )
        self.assertEqual(response.context["scope_label"], self.jefferson.short_name)
        self.assertEqual(
            [event.pk for event in response.context["upcoming"]],
            [self.event.pk],
        )
        self.assertEqual(response.context["confirmed_count"], 1)
        self.assertEqual(response.context["proposed_count"], 0)

    @override_settings(DEBUG=False)
    def test_dashboard_can_combine_two_counties(self):
        response = self.client.get(
            reverse("dashboard"),
            {"organization": [self.jefferson.pk, self.lewis.pk]},
        )

        self.assertEqual(
            response.context["selected_organization_ids"],
            {self.jefferson.pk, self.lewis.pk},
        )
        self.assertEqual(response.context["scope_label"], "2 organizations selected")
        self.assertEqual(
            {event.pk for event in response.context["upcoming"]},
            {self.event.pk, self.lewis_event.pk},
        )
        self.assertNotContains(response, "Montour Falls")


class NotificationPreferenceTests(SchedulingTestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(
            username="notices@example.com",
            email="notices@example.com",
            password="test-password",
        )
        self.instructor = self.make_instructor("Notice", self.jefferson)
        self.instructor.user = self.user
        self.instructor.email = self.user.email
        self.instructor.save(update_fields=("user", "email"))
        self.client.force_login(self.user)

    @override_settings(DEBUG=False)
    def test_instructor_must_confirm_consent_and_phone_to_enable_texts(self):
        response = self.client.post(
            reverse("instructor_notifications", args=(self.instructor.pk,)),
            {
                "email_enabled": "on",
                "sms_enabled": "on",
                "assignment_updates": "on",
                "schedule_updates": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "phone",
            "A mobile phone number is required for text notifications.",
        )
        self.assertFormError(
            response.context["form"],
            "sms_consent",
            "Confirm text-message consent to opt in.",
        )

    @override_settings(DEBUG=False)
    def test_instructor_can_opt_in_and_phone_is_normalized(self):
        response = self.client.post(
            reverse("instructor_notifications", args=(self.instructor.pk,)),
            {
                "email_enabled": "on",
                "sms_enabled": "on",
                "phone": "315-555-0199",
                "assignment_updates": "on",
                "schedule_updates": "on",
                "sms_consent": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("instructor_notifications", args=(self.instructor.pk,)),
        )
        self.instructor.refresh_from_db()
        preference = NotificationPreference.objects.get(instructor=self.instructor)
        self.assertEqual(self.instructor.phone, "+13155550199")
        self.assertTrue(preference.sms_enabled)
        self.assertIsNotNone(preference.sms_consented_at)

    @override_settings(DEBUG=False)
    def test_another_instructor_cannot_change_text_consent(self):
        other_user = get_user_model().objects.create_user(
            username="other.notices@example.com",
            password="test-password",
        )
        other = self.make_instructor("OtherNotice", self.lewis)
        other.user = other_user
        other.save(update_fields=("user",))

        response = self.client.post(
            reverse("instructor_notifications", args=(other.pk,)),
            {"sms_enabled": "on", "phone": "3155550199", "sms_consent": "on"},
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        NOTIFICATION_EMAIL_ENABLED=True,
    )
    def test_assignment_notification_is_emailed_and_logged(self):
        assignment = InstructorAssignment.objects.create(
            session=self.session,
            instructor=self.instructor,
            role=InstructorAssignment.Role.ASSISTANT,
        )

        with self.captureOnCommitCallbacks(execute=True):
            notify_assignment(assignment)

        delivery = NotificationDelivery.objects.get(
            instructor=self.instructor,
            channel=NotificationDelivery.Channel.EMAIL,
        )
        self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.course.name, mail.outbox[0].body)

    @override_settings(
        NOTIFICATION_EMAIL_ENABLED=False,
        TWILIO_ACCOUNT_SID="AC00000000000000000000000000000000",
        TWILIO_AUTH_TOKEN="test-token",
        TWILIO_MESSAGING_SERVICE_SID="MG00000000000000000000000000000000",
        TWILIO_FROM_NUMBER="",
    )
    def test_opted_in_assignment_notification_uses_sms_provider(self):
        self.instructor.phone = "+13155550199"
        self.instructor.save(update_fields=("phone",))
        NotificationPreference.objects.create(
            instructor=self.instructor,
            email_enabled=False,
            sms_enabled=True,
            assignment_updates=True,
            sms_consented_at=timezone.now(),
        )
        assignment = InstructorAssignment.objects.create(
            session=self.session,
            instructor=self.instructor,
            role=InstructorAssignment.Role.ASSISTANT,
        )
        provider_response = MagicMock()
        provider_response.__enter__.return_value = provider_response
        provider_response.read.return_value = b'{"sid":"SM123"}'

        with patch("scheduling.notifications.urlopen", return_value=provider_response), self.captureOnCommitCallbacks(execute=True):
            notify_assignment(assignment)

        delivery = NotificationDelivery.objects.get(
            instructor=self.instructor,
            channel=NotificationDelivery.Channel.SMS,
        )
        self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)
        self.assertEqual(delivery.provider_message_id, "SM123")


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

        dashboard_response = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard_response.context["open_staffing_positions"], 49)
        self.assertContains(dashboard_response, "Open staffing positions")

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


class OrganizationManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.state_admin = User.objects.create_superuser(
            username="state-organizations@example.com",
            email="state-organizations@example.com",
            password="test-password",
        )
        self.jefferson = Organization.objects.get(name="Jefferson County")

    @override_settings(DEBUG=False)
    def test_state_administrator_can_add_county(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("organization_create"),
            {
                "name": "Albany County",
                "short_name": "Albany",
                "kind": Organization.Kind.COUNTY,
                "display_order": 20,
                "active": "on",
            },
        )

        self.assertRedirects(response, reverse("organization_list"))
        organization = Organization.objects.get(name="Albany County")
        self.assertEqual(organization.short_name, "Albany")
        self.assertTrue(organization.active)

    @override_settings(DEBUG=False)
    def test_county_administrator_cannot_manage_organizations(self):
        county_admin = get_user_model().objects.create_user(
            username="county-organizations@example.com",
            password="test-password",
        )
        UserOrganizationRole.objects.create(
            user=county_admin,
            organization=self.jefferson,
            role=UserOrganizationRole.Role.ADMINISTRATOR,
        )
        self.client.force_login(county_admin)

        response = self.client.get(reverse("organization_list"))

        self.assertEqual(response.status_code, 403)

    @override_settings(DEBUG=False)
    def test_organization_names_and_short_names_are_case_insensitive_unique(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("organization_create"),
            {
                "name": "jefferson county",
                "short_name": "JEFFERSON",
                "kind": Organization.Kind.COUNTY,
                "display_order": 0,
                "active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "name", "An organization with this name already exists.")
        self.assertFormError(response.context["form"], "short_name", "An organization with this short name already exists.")
