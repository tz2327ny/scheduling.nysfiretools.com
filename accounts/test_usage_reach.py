from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import UsageDaily
from .usage import UsageMiddleware
from .usage_reach import (
    USAGE_COOKIE, USAGE_LIFETIME, reach_point, merge_reach, reach_estimate,
    read_usage_cookie, usage_identity, usage_cookie_domain,
)


SECRET = "reach-test-shared-secret"
NOW = datetime(2026, 9, 11, 16, tzinfo=dt_timezone.utc)
GOLDEN = "v1.1789142400.0123456789abcdef0123456789abcdef.4afa4c2b7bcc0eaacb003288c545853c0b0f177a853204e51263077d8137f83e"


@override_settings(NYSFIRETOOLS_SSO_CLIENT_SECRET=SECRET, ALLOWED_HOSTS=["testserver", "scheduling.nysfiretools.com"])
class UsageReachTests(TestCase):
    def request(self, cookie="", **headers):
        request = RequestFactory().get("/", HTTP_COOKIE=cookie, **headers)
        request.resolver_match = SimpleNamespace(url_name="dashboard")
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, pk=42)
        return request

    def test_python_matches_js_golden_protocol_and_domain(self):
        self.assertEqual(reach_point(SECRET, "browser", "0123456789abcdef0123456789abcdef"), {"678": 1})
        self.assertEqual(reach_point(SECRET, "account", "scheduler:42"), {"2694": 1})
        reach, cookie = usage_identity(self.request(f"{USAGE_COOKIE}={GOLDEN}"), SECRET, NOW)
        self.assertIsNone(cookie)
        self.assertEqual(reach, {"b": {"678": 1}, "n": {"678": 1}, "a": {"2694": 1}})
        self.assertEqual(usage_cookie_domain("scheduling.nysfiretools.com"), "nysfiretools.com")
        self.assertIsNone(usage_cookie_domain("nysfiretools.com.evil.test"))

    def test_cookie_tampering_expiry_and_privacy_controls(self):
        for secret, stamp, cookie in [
            ("wrong", NOW.timestamp(), GOLDEN),
            (SECRET, NOW.timestamp() + USAGE_LIFETIME, GOLDEN),
            (SECRET, NOW.timestamp() - 120, GOLDEN),
            (SECRET, NOW.timestamp(), GOLDEN + "bad"),
        ]:
            self.assertIsNone(read_usage_cookie(f"{USAGE_COOKIE}={cookie}", secret, stamp))
        reach, cookie = usage_identity(self.request(f"{USAGE_COOKIE}={GOLDEN}"), SECRET, NOW + timedelta(days=1))
        self.assertIsNone(cookie)
        self.assertIn("r", reach)
        self.assertNotIn("n", reach)
        for headers in ({"HTTP_DNT": "1"}, {"HTTP_SEC_GPC": "1"}):
            self.assertEqual(usage_identity(self.request(**headers), SECRET, NOW), ({}, None))

    def test_repeated_requests_and_different_devices_deduplicate_the_account(self):
        first, cookie = usage_identity(self.request(), SECRET, NOW)
        repeat, _ = usage_identity(self.request(f"{USAGE_COOKIE}={cookie}"), SECRET, NOW)
        self.assertEqual(reach_estimate(merge_reach(first, repeat)["b"]), 1)
        other_device, _ = usage_identity(self.request(), SECRET, NOW)
        self.assertEqual(reach_estimate(merge_reach(first, other_device)["a"]), 1)
        self.assertEqual(reach_estimate({}), 0)

    def test_response_uses_secure_shared_cookie_but_never_an_auth_cookie(self):
        response = UsageMiddleware(lambda request: HttpResponse("OK"))(self.request(HTTP_HOST="scheduling.nysfiretools.com"))
        cookie = response.cookies[USAGE_COOKIE]
        self.assertEqual(cookie["domain"], "nysfiretools.com")
        self.assertEqual(cookie["max-age"], USAGE_LIFETIME)
        self.assertTrue(cookie["httponly"])
        self.assertTrue(cookie["secure"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertNotIn("sessionid", response.cookies)
        row = UsageDaily.objects.get(tool="dashboard")
        self.assertEqual(reach_estimate(row.reach["a"]), 1)
        self.assertNotIn("scheduler:42", str(row.reach))
        self.assertNotIn(cookie.value, str(row.reach))

    def test_opt_out_still_counts_page_views_but_not_browsers_or_accounts(self):
        response = UsageMiddleware(lambda request: HttpResponse("OK"))(self.request(HTTP_DNT="1"))
        self.assertNotIn(USAGE_COOKIE, response.cookies)
        row = UsageDaily.objects.get(tool="dashboard")
        self.assertEqual(row.views, 1)
        self.assertEqual(row.reach, {})

    def test_retention_preserves_totals_and_only_clears_expired_sketches(self):
        today = timezone.localdate()
        expired = UsageDaily.objects.create(day=today - timedelta(days=90), tool="schedule", views=3, reach={"b": {"1": 1}})
        retained = UsageDaily.objects.create(day=today - timedelta(days=89), tool="schedule", views=7, reach={"b": {"2": 1}})
        UsageMiddleware(lambda request: HttpResponse("OK"))(self.request())
        expired.refresh_from_db()
        retained.refresh_from_db()
        self.assertEqual(expired.views, 3)
        self.assertIsNone(expired.reach)
        self.assertIsNotNone(retained.reach)

    def test_estimator_is_reasonable_for_realistic_traffic(self):
        for size in (1, 20, 200, 2000, 20000):
            registers = {}
            for i in range(size):
                for index, rank in reach_point(SECRET, "browser", f"sample-{i}").items():
                    registers[index] = max(registers.get(index, 0), rank)
            self.assertLessEqual(abs(reach_estimate(registers) - size), max(1, size * .05))
            self.assertLessEqual(len(registers), 4096)
