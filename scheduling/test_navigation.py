import re
from django.test import override_settings
from django.urls import reverse

from .tests import SchedulingTestCase


@override_settings(DEBUG=True)
class SchedulingNavigationTests(SchedulingTestCase):
    def test_all_primary_destinations_match_the_main_site_in_the_same_tab(self):
        for route in ("dashboard", "schedule", "course_list", "instructor_list", "login", "account_access_start", "general_register", "instructor_register", "password_reset"):
            with self.subTest(route=route):
                response = self.client.get(reverse(route))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                navigation = re.search(r'<nav class="nav".*?</nav>', html, re.S).group()
                links = re.findall(r'href="([^"]+)"', navigation)
                self.assertEqual(links, [
                    "https://www.nysfiretools.com/", "https://www.nysfiretools.com/weather",
                    "https://www.nysfiretools.com/forms", "https://www.nysfiretools.com/burn-plans",
                    "https://www.nysfiretools.com/site-plan-builder", "https://www.nysfiretools.com/who-uses-nysfiretools",
                    "/", "https://www.nysfiretools.com/#feedback",
                ])
                self.assertNotIn('target="_blank"', navigation)
                self.assertEqual(navigation.count('aria-current="page"'), 1)
                self.assertIn("site-navigation.css", html)
                self.assertIn("site-navigation.js", html)

    def test_return_from_training_preserves_filters_and_clear_resets_them(self):
        self.client.get(reverse("schedule"), {"status": "confirmed", "organization": self.jefferson.pk})
        detail = self.client.get(reverse("training_detail", args=[self.event.pk]))
        expected = f"/schedule/?status=confirmed&organization={self.jefferson.pk}"
        self.assertEqual(detail.context["schedule_return_url"], expected)
        self.assertContains(detail, f'href="{expected.replace("&", "&amp;")}"')
        self.client.get(reverse("schedule"))
        detail = self.client.get(reverse("training_detail", args=[self.event.pk]))
        self.assertEqual(detail.context["schedule_return_url"], reverse("schedule"))

    def test_assignment_returns_to_the_unit(self):
        instructor = self.make_instructor("Navigation", self.jefferson)
        response = self.client.post(reverse("session_assignment_add", args=[self.event.pk, self.session.pk]), {
            "instructor": instructor.pk, "role": "lead", "confirmed": "on"
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("training_detail", args=[self.event.pk]) + f"#unit-{self.session.pk}")
        self.assertTrue(self.session.instructor_assignments.filter(instructor=instructor).exists())
