"""Aggregate page and audience counts, shared with the main admin dashboard."""
import hashlib
import hmac
import logging
import re
import time
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import F, Min
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import UsageDaily
from .usage_reach import USAGE_COOKIE, USAGE_LIFETIME, merge_reach, usage_cookie_domain, usage_identity


logger = logging.getLogger(__name__)
TOOL_ROUTES = {
    "dashboard": "dashboard",
    "schedule": "schedule",
    "training_detail": "schedule",
    "training_create": "schedule",
    "training_edit": "schedule",
    "course_list": "courses",
    "instructor_list": "instructors",
    "instructor_availability": "availability",
    "availability_create": "availability",
    "availability_edit": "availability",
    "recurring_availability_create": "availability",
    "recurring_availability_edit": "availability",
    "instructor_authorizations": "authorizations",
    "instructor_notifications": "notifications",
}


class UsageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.last_pruned = None

    def __call__(self, request):
        response = self.get_response(request)
        route = getattr(getattr(request, "resolver_match", None), "url_name", None)
        tool = TOOL_ROUTES.get(route)
        if (
            tool and request.method == "GET" and response.status_code == 200
            and response.get("Content-Type", "").startswith("text/html")
            and not re.search(r"bot|crawler|spider|slurp|headless|healthcheck|uptime|monitor|preview", request.headers.get("User-Agent", ""), re.I)
            and not re.search(r"prefetch|prerender", request.headers.get("Purpose", "") + request.headers.get("Sec-Purpose", ""), re.I)
        ):
            try:
                day = timezone.localdate()
                if self.last_pruned != day:
                    UsageDaily.objects.filter(day__lt=day - timedelta(days=89), reach__isnull=False).update(reach=None)
                    self.last_pruned = day
                reach, cookie = usage_identity(request, settings.NYSFIRETOOLS_SSO_CLIENT_SECRET or settings.SECRET_KEY)
                # get_or_create is race-safe under the unique constraint; F()
                # increments atomically. The savepoint keeps failures isolated.
                with transaction.atomic():
                    row, _ = UsageDaily.objects.get_or_create(day=day, tool=tool)
                    row = UsageDaily.objects.select_for_update().get(pk=row.pk)
                    UsageDaily.objects.filter(pk=row.pk).update(views=F("views") + 1, reach=merge_reach(row.reach, reach))
                if cookie:
                    hostname = request.get_host().split(":")[0]
                    response.set_cookie(USAGE_COOKIE, cookie, max_age=USAGE_LIFETIME, httponly=True,
                                        secure=not settings.DEBUG, samesite="Lax", path="/",
                                        domain=usage_cookie_domain(hostname))
            except DatabaseError:
                logger.warning("Aggregate usage count could not be recorded.")
        return response


@require_GET
def usage_summary(request):
    """Server-to-server only; no browser token, raw events or account data."""
    secret = settings.NYSFIRETOOLS_SSO_CLIENT_SECRET
    stamp = request.headers.get("X-Usage-Timestamp", "")
    signature = request.headers.get("X-Usage-Signature", "")
    days = request.GET.get("days", "30")
    valid = bool(secret) and days in {"7", "30", "90"} and stamp.isascii() and stamp.isdigit() and len(stamp) <= 12 and bool(re.fullmatch(r"[0-9a-f]{64}", signature))
    if valid:
        expected = hmac.new(secret.encode(), f"usage:{stamp}:{days}".encode(), hashlib.sha256).hexdigest()
        valid = abs(time.time() - int(stamp)) <= 60 and hmac.compare_digest(expected, signature)
    if not valid:
        response = JsonResponse({"error": "forbidden"}, status=403)
    else:
        end = timezone.localdate()
        start = end - timedelta(days=int(days) - 1)
        rows = list(UsageDaily.objects.filter(day__range=(start, end), tool__in=set(TOOL_ROUTES.values())).values("day", "tool", "views", "reach"))
        first = UsageDaily.objects.aggregate(day=Min("day"))["day"]
        first_reach = UsageDaily.objects.filter(day__gte=end - timedelta(days=89), reach__isnull=False).aggregate(day=Min("day"))["day"]
        response = JsonResponse({"rows": rows, "firstDay": first, "reachVersion": 1, "firstReachDay": first_reach})
    response["Cache-Control"] = "private, no-store"
    return response
