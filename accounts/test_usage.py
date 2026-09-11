import hashlib
import hmac
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import DatabaseError
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import UsageDaily
from .usage import UsageMiddleware


@override_settings(NYSFIRETOOLS_SSO_CLIENT_SECRET="test-aggregate-usage-secret")
class UsageTests(TestCase):
    def page(self, route="schedule", method="get", status=200, content_type="text/html", **headers):
        request = getattr(RequestFactory(), method)("/?private-search=value", **headers)
        request.resolver_match = SimpleNamespace(url_name=route)
        return UsageMiddleware(lambda request: HttpResponse("page", status=status, content_type=content_type))(request)

    def signed_headers(self, days="7", stamp=None):
        stamp = str(int(time.time()) if stamp is None else stamp)
        signature = hmac.new(b"test-aggregate-usage-secret", f"usage:{stamp}:{days}".encode(), hashlib.sha256).hexdigest()
        return {"HTTP_X_USAGE_TIMESTAMP": stamp, "HTTP_X_USAGE_SIGNATURE": signature}

    def test_successful_pages_are_aggregated_without_request_data(self):
        self.page()
        self.page()
        self.page(route="instructor_availability")
        self.assertEqual(UsageDaily.objects.get(tool="schedule").views, 2)
        self.assertEqual(UsageDaily.objects.count(), 2)
        self.assertEqual({f.name for f in UsageDaily._meta.fields}, {"id", "day", "tool", "views", "reach"})

    def test_auth_admin_assets_redirects_bots_and_prefetches_are_excluded(self):
        for route in ("login", "administration", "user_list", "organization_list", "health", "usage_summary", None):
            self.page(route=route)
        for status in (302, 403, 404, 500):
            self.page(status=status)
        self.page(method="post")
        self.page(method="head")
        self.page(content_type="application/json")
        self.page(HTTP_USER_AGENT="Googlebot")
        self.page(HTTP_SEC_PURPOSE="prefetch;prerender")
        self.assertFalse(UsageDaily.objects.exists())

    def test_analytics_failure_does_not_break_the_page(self):
        with patch("accounts.usage.UsageDaily.objects.get_or_create", side_effect=DatabaseError):
            with self.assertLogs("accounts.usage", level="WARNING"):
                self.assertEqual(self.page().status_code, 200)

    def test_signed_summary_is_read_only_and_range_limited(self):
        today = timezone.localdate()
        UsageDaily.objects.create(day=today, tool="schedule", views=7)
        UsageDaily.objects.create(day=today - timedelta(days=15), tool="dashboard", views=2)
        response = self.client.get(reverse("usage_summary"), {"days": 7}, **self.signed_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response.json(), {"firstDay": str(today - timedelta(days=15)), "reachVersion": 1, "firstReachDay": None, "rows": [{"day": str(today), "tool": "schedule", "views": 7, "reach": None}]})
        self.assertEqual(UsageDaily.objects.count(), 2)

    def test_unsigned_expired_wrong_scope_and_invalid_range_are_denied(self):
        endpoint = reverse("usage_summary")
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.assertEqual(self.client.get(endpoint, {"days": 7}, **self.signed_headers(stamp=int(time.time()) - 120)).status_code, 403)
        self.assertEqual(self.client.get(endpoint, {"days": 30}, **self.signed_headers(days="7")).status_code, 403)
        self.assertEqual(self.client.get(endpoint, {"days": 999}, **self.signed_headers(days="999")).status_code, 403)
        self.assertEqual(self.client.get(endpoint, {"days": 7}, HTTP_X_USAGE_TIMESTAMP="9" * 4000).status_code, 403)
        self.assertEqual(self.client.post(endpoint, {"days": 7}, **self.signed_headers()).status_code, 405)
        with override_settings(NYSFIRETOOLS_SSO_CLIENT_SECRET=""):
            self.assertEqual(self.client.get(endpoint, {"days": 7}, **self.signed_headers()).status_code, 403)
