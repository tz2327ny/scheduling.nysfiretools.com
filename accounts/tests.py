import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from scheduling.models import Course, CourseAuthorization, Instructor, Organization

from .models import ExternalAccessCode, InstructorApplication, UserOrganizationRole


User = get_user_model()


@override_settings(
    NYSFIRETOOLS_MAIN_ORIGIN="https://www.nysfiretools.com",
    NYSFIRETOOLS_SSO_CLIENT_SECRET="test-shared-secret-with-sufficient-length",
)
class NysfiretoolsSingleSignOnTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.get(name="Jefferson County")
        self.user = User.objects.create_user(
            username="linked.instructor@example.com",
            email="linked.instructor@example.com",
            password="TestAccess!23456",
            first_name="Linked",
            last_name="Instructor",
            is_active=True,
        )
        Instructor.objects.create(
            user=self.user,
            first_name="Linked",
            last_name="Instructor",
            email=self.user.email,
            home_organization=self.organization,
        )

    def issue_code(self, return_to="/site-plan-builder"):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("nysfiretools_sso_authorize"),
            {"return_to": return_to, "state": "browser-state-value-123456789"},
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(f"{parsed.scheme}://{parsed.netloc}", "https://www.nysfiretools.com")
        self.assertEqual(parsed.path, "/burn-plans/sso/callback")
        self.assertEqual(parse_qs(parsed.query)["state"][0], "browser-state-value-123456789")
        return parse_qs(parsed.query)["code"][0]

    def exchange_code(self, code, secret="test-shared-secret-with-sufficient-length"):
        return self.client.post(
            reverse("nysfiretools_sso_token"),
            {"code": code},
            HTTP_X_NYSFIRETOOLS_SSO_SECRET=secret,
        )

    def test_scheduler_login_is_required_before_issuing_code(self):
        response = self.client.get(
            reverse("nysfiretools_sso_authorize"),
            {"return_to": "/burn-plans", "state": "browser-state-value-123456789"},
        )
        self.assertEqual(response.status_code, 302)
        parsed = urlparse(response["Location"])
        self.assertEqual(parsed.path, reverse("login"))
        self.assertIn(reverse("nysfiretools_sso_authorize"), parse_qs(parsed.query)["next"][0])

    def test_authorize_rejects_missing_browser_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("nysfiretools_sso_authorize"))
        self.assertEqual(response.status_code, 400)

    def test_code_exchanges_once_for_active_scheduler_identity(self):
        code = self.issue_code()

        response = self.exchange_code(code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "sub": str(self.user.pk),
                "email": "linked.instructor@example.com",
                "name": "Linked Instructor",
                "agency": "Jefferson County",
                "role": "viewer",
                "return_to": "/site-plan-builder",
            },
        )
        self.assertEqual(self.exchange_code(code).status_code, 400)

    def test_invalid_client_does_not_consume_code_and_unsafe_return_is_rejected(self):
        code = self.issue_code("//untrusted.example/path")

        self.assertEqual(self.exchange_code(code, "wrong-secret").status_code, 401)
        response = self.exchange_code(code)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["return_to"], "/burn-plans")

    def test_expired_code_and_inactive_user_are_rejected(self):
        expired_code = self.issue_code()
        ExternalAccessCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange_code(expired_code).status_code, 400)

        active_code = self.issue_code()
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))
        self.assertEqual(self.exchange_code(active_code).status_code, 400)

    def test_scheduler_superuser_maps_to_nysfiretools_admin(self):
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(update_fields=("is_superuser", "is_staff"))
        response = self.exchange_code(self.issue_code("/supporters/admin"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["role"], "admin")


class CloudflareEmailBackendTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="config.email_backend.CloudflareEmailBackend",
        CLOUDFLARE_ACCOUNT_ID="account-id",
        CLOUDFLARE_EMAIL_API_TOKEN="restricted-token",
        CLOUDFLARE_EMAIL_TIMEOUT=5,
        DEFAULT_FROM_EMAIL="NYS Fire Scheduler <notifications@nysfiretools.com>",
    )
    @patch("config.email_backend.urlopen")
    def test_sends_email_through_cloudflare_https_api(self, mocked_urlopen):
        provider_response = MagicMock()
        provider_response.__enter__.return_value = provider_response
        provider_response.read.return_value = json.dumps(
            {
                "success": True,
                "errors": [],
                "result": {
                    "delivered": ["instructor@example.com"],
                    "queued": [],
                },
            }
        ).encode("utf-8")
        mocked_urlopen.return_value = provider_response

        sent = mail.send_mail(
            "Schedule updated",
            "Your schedule changed.",
            None,
            ["instructor@example.com"],
        )

        self.assertEqual(sent, 1)
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["from"], "notifications@nysfiretools.com")
        self.assertEqual(payload["to"], "instructor@example.com")
        self.assertEqual(payload["subject"], "Schedule updated")
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer restricted-token",
        )

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
            "sfi_number": "SFI-12345",
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

    def test_registration_offers_all_new_york_counties_and_state_academy(self):
        response = self.client.get(reverse("instructor_register"))

        organizations = response.context["form"].fields["home_organization"].queryset
        self.assertEqual(
            organizations.filter(kind=Organization.Kind.COUNTY).count(),
            62,
        )
        self.assertTrue(organizations.filter(name="Albany County").exists())
        self.assertTrue(organizations.filter(name="Yates County").exists())
        self.assertTrue(
            organizations.filter(
                name="New York State Academy of Fire Science",
                kind=Organization.Kind.ACADEMY,
            ).exists()
        )
        self.assertContains(response, "Administrator review required")
        self.assertContains(response, "Selecting a course submits it for verification")
        self.assertContains(response, "Selections are authorization claims only")

    def test_registration_creates_inactive_account_with_course_claims(self):
        application = self.create_pending_application()

        self.assertEqual(application.status, InstructorApplication.Status.PENDING)
        self.assertEqual(application.sfi_number, "SFI-12345")
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

    def test_registration_requires_sfi_number(self):
        payload = self.registration_payload()
        payload["sfi_number"] = ""

        response = self.client.post(reverse("instructor_register"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "sfi_number", "This field is required.")
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
            sfi_number="SFI-12345",
            email="new.instructor@example.com",
            home_organization=self.academy,
        )
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        self.client.post(
            reverse("application_review", args=[application.pk]),
            {
                "instructor_match": str(existing.pk),
                "approved_courses": [self.course.pk],
            },
        )

        existing.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(application.instructor_id, existing.pk)
        self.assertEqual(existing.user_id, application.user_id)
        self.assertEqual(existing.first_name, "New")
        self.assertEqual(existing.sfi_number, "SFI-12345")
        self.assertEqual(existing.home_organization, self.jefferson)

    def test_possible_match_requires_explicit_merge_decision(self):
        existing = Instructor.objects.create(
            first_name="New",
            last_name="Instructor",
            sfi_number="SFI-12345",
            home_organization=self.academy,
        )
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        review_page = self.client.get(reverse("application_review", args=[application.pk]))
        self.assertContains(review_page, "Possible existing instructor found")
        self.assertContains(review_page, existing.sfi_number)
        response = self.client.post(
            reverse("application_review", args=[application.pk]),
            {"approved_courses": [self.course.pk]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "instructor_match",
            "This field is required.",
        )
        application.refresh_from_db()
        self.assertEqual(application.status, InstructorApplication.Status.PENDING)
        self.assertIsNone(existing.user_id)

    def test_review_flags_a_similar_name_even_when_identifiers_differ(self):
        existing = Instructor.objects.create(
            first_name="New",
            last_name="Instructer",
            sfi_number="DIFFERENT-SFI",
            email="different@example.com",
            home_organization=self.academy,
        )
        application = self.create_pending_application()
        self.client.force_login(self.state_admin)

        response = self.client.get(reverse("application_review", args=[application.pk]))

        self.assertContains(response, "Possible existing instructor found")
        self.assertContains(response, existing.email)

    def test_approval_merges_an_existing_login_and_preserves_access(self):
        old_user = User.objects.create_user(
            username="old.instructor@example.com",
            email="old.instructor@example.com",
            password=self.password,
            first_name="New",
            last_name="Instructor",
        )
        existing = Instructor.objects.create(
            user=old_user,
            first_name="New",
            last_name="Instructor",
            sfi_number="SFI-12345",
            email=old_user.email,
            home_organization=self.academy,
        )
        UserOrganizationRole.objects.create(
            user=old_user,
            organization=self.academy,
            role=UserOrganizationRole.Role.ADMINISTRATOR,
        )
        application = self.create_pending_application()
        applicant_id = application.user_id
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("application_review", args=[application.pk]),
            {
                "instructor_match": str(existing.pk),
                "approved_courses": [self.course.pk],
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        existing.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(existing.user_id, applicant_id)
        self.assertEqual(application.instructor_id, existing.pk)
        self.assertFalse(User.objects.filter(pk=old_user.pk).exists())
        self.assertTrue(
            UserOrganizationRole.objects.filter(
                user_id=applicant_id,
                organization=self.academy,
                role=UserOrganizationRole.Role.ADMINISTRATOR,
            ).exists()
        )

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

    def test_state_admin_can_change_instructor_home_assignment(self):
        erie = Organization.objects.get(name="Erie County")
        managed_user = User.objects.create_user(
            username="david@example.com",
            email="david@example.com",
            password=self.password,
            first_name="David",
            last_name="Mastrella",
            is_active=True,
        )
        instructor = Instructor.objects.create(
            user=managed_user,
            first_name="David",
            last_name="Mastrella",
            email=managed_user.email,
            home_organization=self.academy,
        )
        application = InstructorApplication.objects.create(
            user=managed_user,
            sfi_number="SFI-DAVID",
            home_organization=self.academy,
            travel_preference=InstructorApplication.TravelPreference.CONTACT_ME,
            status=InstructorApplication.Status.APPROVED,
            instructor=instructor,
        )
        self.client.force_login(self.state_admin)

        edit_page = self.client.get(reverse("user_edit", args=[managed_user.pk]))
        self.assertContains(edit_page, "Instructor home assignment")
        self.assertContains(
            edit_page,
            "This is separate from county/organization administrator access.",
        )
        response = self.client.post(
            reverse("user_edit", args=[managed_user.pk]),
            {
                "first_name": managed_user.first_name,
                "last_name": managed_user.last_name,
                "email": managed_user.email,
                "is_active": "on",
                "instructor_profile": instructor.pk,
                "instructor_home_organization": erie.pk,
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        instructor.refresh_from_db()
        application.refresh_from_db()
        self.assertEqual(instructor.home_organization, erie)
        self.assertEqual(application.home_organization, erie)
        self.assertFalse(
            UserOrganizationRole.objects.filter(
                user=managed_user,
                organization=erie,
                role=UserOrganizationRole.Role.ADMINISTRATOR,
            ).exists()
        )

    def test_site_admin_can_unlink_own_instructor_profile_without_deleting_it(self):
        instructor = Instructor.objects.create(
            user=self.state_admin,
            first_name=self.state_admin.first_name,
            last_name=self.state_admin.last_name,
            sfi_number="SFI-ADMIN",
            email=self.state_admin.email,
            home_organization=self.jefferson,
        )
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_edit", args=[self.state_admin.pk]),
            {
                "first_name": self.state_admin.first_name,
                "last_name": self.state_admin.last_name,
                "email": self.state_admin.email,
                "is_active": "on",
                "is_superuser": "on",
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        instructor.refresh_from_db()
        self.assertIsNone(instructor.user_id)
        self.assertFalse(instructor.active)
        self.assertTrue(Instructor.objects.filter(pk=instructor.pk).exists())

    def test_site_admin_can_create_administrator_only_login(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_create"),
            {
                "first_name": "Login",
                "last_name": "Only",
                "email": "login.only@example.com",
                "password1": self.password,
                "password2": self.password,
                "is_active": "on",
                "is_superuser": "on",
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        created = User.objects.get(email="login.only@example.com")
        self.assertTrue(created.is_superuser)
        self.assertTrue(created.is_active)
        self.assertFalse(Instructor.objects.filter(user=created).exists())

    def test_site_admin_can_assign_authorizations_when_linking_login(self):
        instructor = Instructor.objects.create(
            first_name="Schedule",
            last_name="Only",
            email="schedule.only@example.com",
            home_organization=self.jefferson,
        )
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_create"),
            {
                "first_name": "Schedule",
                "last_name": "Only",
                "email": "schedule.only@example.com",
                "password1": self.password,
                "password2": self.password,
                "is_active": "on",
                "instructor_profile": instructor.pk,
                "verified_courses": [self.course.pk, self.second_course.pk],
            },
        )

        self.assertRedirects(response, reverse("user_list"))
        instructor.refresh_from_db()
        self.assertIsNotNone(instructor.user_id)
        authorizations = instructor.course_authorizations.filter(
            status=CourseAuthorization.Status.ACTIVE
        )
        self.assertEqual(
            set(authorizations.values_list("course_id", flat=True)),
            {self.course.pk, self.second_course.pk},
        )
        self.assertFalse(authorizations.exclude(verified_by=self.state_admin).exists())

    def test_site_admin_cannot_assign_authorizations_without_instructor_profile(self):
        self.client.force_login(self.state_admin)

        response = self.client.post(
            reverse("user_create"),
            {
                "first_name": "Admin",
                "last_name": "Only",
                "email": "admin.only@example.com",
                "password1": self.password,
                "password2": self.password,
                "is_active": "on",
                "verified_courses": [self.course.pk],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "verified_courses",
            "Link an instructor directory profile before assigning course authorizations.",
        )
        self.assertFalse(User.objects.filter(email="admin.only@example.com").exists())

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

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_successful_password_reset_signs_user_in(self):
        user = User.objects.create_user(
            username="reset.login@dhses.ny.gov",
            email="reset.login@dhses.ny.gov",
            password="OldPassword!23456",
            is_active=True,
        )
        self.client.post(reverse("password_reset"), {"email": user.email})
        reset_url = mail.outbox[0].body.split("Use this secure link to choose a new password:\n", 1)[1].splitlines()[0]
        response = self.client.get(reset_url)
        response = self.client.post(
            response["Location"],
            {
                "new_password1": "NewPassword!23456",
                "new_password2": "NewPassword!23456",
            },
        )

        self.assertRedirects(response, reverse("password_reset_complete"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(self.client.get(reverse("dashboard")).wsgi_request.user.is_authenticated)

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
        self.assertContains(response, "You cannot remove your own Site Administrator access.")
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
