from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from scheduling.models import Course, CourseAuthorization, Instructor, Organization

from .models import InstructorApplication, UserOrganizationRole


User = get_user_model()


class InstructorAccountWorkflowTests(TestCase):
    password = "TestAccess!23456"

    def setUp(self):
        self.jefferson = Organization.objects.get(name="Jefferson County")
        self.academy = Organization.objects.get(name="New York State Academy of Fire Science")
        self.course = Course.objects.create(
            record_number="99-99-9001",
            name="Test Fire Course",
        )
        self.second_course = Course.objects.create(
            record_number="99-99-9002",
            name="Second Fire Course",
        )
        self.state_admin = User.objects.create_superuser(
            username="state.admin@example.com",
            email="state.admin@example.com",
            password=self.password,
            first_name="State",
            last_name="Admin",
        )

    def registration_payload(self, email="new.instructor@example.com"):
        return {
            "first_name": "New",
            "last_name": "Instructor",
            "email": email,
            "phone": "315-555-0123",
            "home_organization": self.jefferson.pk,
            "travel_preference": Instructor.TravelPreference.LIMITED,
            "travel_notes": "Available across the north country with notice.",
            "requested_courses": [self.course.pk, self.second_course.pk],
            "password1": self.password,
            "password2": self.password,
        }

    def create_pending_application(self, email="new.instructor@example.com"):
        response = self.client.post(reverse("instructor_register"), self.registration_payload(email))
        self.assertRedirects(response, reverse("registration_received"))
        return InstructorApplication.objects.select_related("user").get(user__email=email)

    def test_registration_creates_inactive_account_with_course_claims(self):
        application = self.create_pending_application()

        self.assertEqual(application.status, InstructorApplication.Status.PENDING)
        self.assertEqual(application.home_organization, self.jefferson)
        self.assertEqual(application.get_travel_preference_display(), "Limited travel")
        self.assertEqual(
            set(application.requested_courses.all()),
            {self.course, self.second_course},
        )
        self.assertFalse(application.user.is_active)
        self.assertFalse(Instructor.objects.filter(user=application.user).exists())
        self.assertFalse(self.client.login(username=application.user.email, password=self.password))

    def test_registration_rejects_duplicate_email(self):
        User.objects.create_user(
            username="existing@example.com",
            email="existing@example.com",
            password=self.password,
        )

        response = self.client.post(
            reverse("instructor_register"),
            self.registration_payload("existing@example.com"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account already exists for this email address.")
        self.assertFalse(InstructorApplication.objects.exists())

    def test_database_rejects_case_insensitive_duplicate_email(self):
        User.objects.create_user(
            username="unique-one@example.com",
            email="Unique.Email@example.com",
            password=self.password,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(
                username="unique-two@example.com",
                email="unique.email@EXAMPLE.COM",
                password=self.password,
            )

    def test_email_sign_in_is_case_insensitive(self):
        user = User.objects.create_user(
            username="case.login@example.com",
            email="case.login@example.com",
            password=self.password,
        )

        response = self.client.post(
            reverse("login"),
            {"username": "CASE.LOGIN@EXAMPLE.COM", "password": self.password},
        )

        self.assertRedirects(response, reverse("dashboard"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_state_admin_reviews_application_and_approves_selected_course_claims(self):
        application = self.create_pending_application()
        applicant = application.user
        self.client.force_login(self.state_admin)

        review_page = self.client.get(reverse("application_review", args=[application.pk]))
        self.assertContains(review_page, self.course.name)
        response = self.client.post(
            reverse("application_review", args=[application.pk]),
            {"approved_courses": [self.course.pk]},
        )

        self.assertRedirects(response, reverse("user_list"))
        application.refresh_from_db()
        applicant.refresh_from_db()
        instructor = Instructor.objects.get(user=applicant)
        self.assertEqual(application.status, InstructorApplication.Status.APPROVED)
        self.assertEqual(application.reviewed_by, self.state_admin)
        self.assertEqual(application.instructor, instructor)
        self.assertTrue(applicant.is_active)
        approved = CourseAuthorization.objects.get(instructor=instructor, course=self.course)
        self.assertEqual(approved.status, CourseAuthorization.Status.ACTIVE)
        self.assertEqual(approved.verified_by, self.state_admin)
        self.assertFalse(
            CourseAuthorization.objects.filter(instructor=instructor, course=self.second_course).exists()
        )
        self.client.logout()
        self.assertTrue(self.client.login(username=applicant.email, password=self.password))

    def test_approval_links_a_matching_unclaimed_instructor_profile(self):
        existing = Instructor.objects.create(
            first_name="Existing",
            last_name="Record",
            email="new.instructor@example.com",
            home_organization=self.academy,
        )
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        self.client.post(
            reverse("application_review", args=[application.pk]),
            {"approved_courses": [self.course.pk]},
        )

        existing.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(application.instructor_id, existing.pk)
        self.assertEqual(existing.user_id, application.user_id)
        self.assertEqual(existing.first_name, "New")
        self.assertEqual(existing.home_organization, self.jefferson)

    def test_non_state_admin_cannot_review_applications(self):
        application = self.create_pending_application()
        county_admin = User.objects.create_user(
            username="county.admin@example.com",
            email="county.admin@example.com",
            password=self.password,
        )
        UserOrganizationRole.objects.create(
            user=county_admin,
            organization=self.jefferson,
            role=UserOrganizationRole.Role.ADMINISTRATOR,
        )
        self.client.force_login(county_admin)

        response = self.client.post(
            reverse("application_review", args=[application.pk]),
            {"approved_courses": [self.course.pk]},
        )

        self.assertEqual(response.status_code, 403)
        application.refresh_from_db()
        self.assertEqual(application.status, InstructorApplication.Status.PENDING)

    def test_state_admin_can_reject_application(self):
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        response = self.client.post(reverse("application_reject", args=[application.pk]))

        self.assertRedirects(response, reverse("user_list"))
        application.refresh_from_db()
        application.user.refresh_from_db()
        self.assertEqual(application.status, InstructorApplication.Status.REJECTED)
        self.assertFalse(application.user.is_active)
        self.assertFalse(Instructor.objects.filter(user=application.user).exists())

    def test_state_admin_can_approve_pending_course_authorization(self):
        instructor = Instructor.objects.create(
            first_name="Approved",
            last_name="Instructor",
            email="approved@example.com",
            home_organization=self.jefferson,
        )
        authorization = CourseAuthorization.objects.create(
            instructor=instructor,
            course=self.course,
            status=CourseAuthorization.Status.PENDING,
        )
        self.client.force_login(self.state_admin)

        response = self.client.post(reverse("authorization_approve", args=[authorization.pk]))

        self.assertRedirects(response, reverse("user_list"))
        authorization.refresh_from_db()
        self.assertEqual(authorization.status, CourseAuthorization.Status.ACTIVE)
        self.assertEqual(authorization.verified_by, self.state_admin)
        self.assertIsNotNone(authorization.verified_at)

    def test_state_admin_can_manage_user_and_county_admin_assignments(self):
        managed_user = User.objects.create_user(
            username="county.user@example.com",
            email="county.user@example.com",
            password=self.password,
            first_name="County",
            last_name="User",
        )
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_edit", args=[managed_user.pk]),
            {
                "first_name": "Updated",
                "last_name": "Administrator",
                "email": "updated.admin@example.com",
                "is_active": "on",
                "organization_admins": [self.jefferson.pk],
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        managed_user.refresh_from_db()
        self.assertEqual(managed_user.username, "updated.admin@example.com")
        self.assertEqual(managed_user.email, "updated.admin@example.com")
        self.assertTrue(managed_user.is_active)
        self.assertFalse(managed_user.is_superuser)
        self.assertTrue(
            UserOrganizationRole.objects.filter(
                user=managed_user,
                organization=self.jefferson,
                role=UserOrganizationRole.Role.ADMINISTRATOR,
            ).exists()
        )

    def test_state_admin_can_reset_user_password(self):
        managed_user = User.objects.create_user(
            username="reset.user@example.com",
            email="reset.user@example.com",
            password=self.password,
        )
        self.client.force_login(self.state_admin)
        new_password = "NewSecureAccess!45678"

        response = self.client.post(
            reverse("user_password_reset", args=[managed_user.pk]),
            {"new_password1": new_password, "new_password2": new_password},
        )

        self.assertRedirects(response, reverse("user_list"))
        managed_user.refresh_from_db()
        self.assertTrue(managed_user.check_password(new_password))

    def test_non_state_admin_cannot_reset_user_password(self):
        county_admin = User.objects.create_user(
            username="reset.county@example.com",
            email="reset.county@example.com",
            password=self.password,
        )
        self.client.force_login(county_admin)

        response = self.client.get(
            reverse("user_password_reset", args=[self.state_admin.pk])
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_user_can_request_password_reset_email(self):
        User.objects.create_user(
            username="forgot.password@example.com",
            email="forgot.password@example.com",
            password=self.password,
            is_active=True,
        )

        response = self.client.post(
            reverse("password_reset"),
            {"email": "forgot.password@example.com"},
        )

        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["forgot.password@example.com"])
        self.assertIn("/accounts/reset/", mail.outbox[0].body)

    def test_state_admin_cannot_disable_or_demote_their_own_account(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_edit", args=[self.state_admin.pk]),
            {
                "first_name": self.state_admin.first_name,
                "last_name": self.state_admin.last_name,
                "email": self.state_admin.email,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You cannot disable your own account.")
        self.assertContains(response, "You cannot remove your own State administrator access.")
        self.state_admin.refresh_from_db()
        self.assertTrue(self.state_admin.is_active)
        self.assertTrue(self.state_admin.is_superuser)

    def test_pending_application_cannot_be_manually_enabled(self):
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_edit", args=[application.user_id]),
            {
                "first_name": application.user.first_name,
                "last_name": application.user.last_name,
                "email": application.user.email,
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Approve this instructor application before enabling the account.")
        application.user.refresh_from_db()
        self.assertFalse(application.user.is_active)

    def test_sign_out_ends_the_authenticated_session(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(reverse("logout"))

        self.assertRedirects(response, reverse("login"))
        response = self.client.get(reverse("user_list"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('user_list')}")
