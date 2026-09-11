"""Mergeable distinct-count counters; never store lists of user/browser IDs."""
import hashlib
import hmac
import math
import re
import secrets
from datetime import datetime

from django.utils import timezone


USAGE_COOKIE = "nysfiretools_usage_v1"
USAGE_LIFETIME = 90 * 86400
REACH_METRICS = ("b", "n", "r", "a")
REGISTERS = 4096


def reach_point(secret, kind, identity):
    # Sparse HLL p=12, mirrored exactly in main lib/usage-reach.js.
    digest = hmac.new(secret.encode(), f"usage-reach-v1:{kind}:{identity}".encode(), hashlib.sha256).digest()
    index = int.from_bytes(digest[:2], "big") >> 4
    rank = 1
    for byte in digest[2:]:
        if byte == 0:
            rank += 8
            continue
        rank += 8 - byte.bit_length()
        break
    return {str(index): rank}


def merge_reach(*values):
    merged = {}
    for value in values:
        for metric in REACH_METRICS:
            if not value or metric not in value:
                continue
            output = merged.setdefault(metric, {})
            for index, rank in value[metric].items():
                output[index] = max(output.get(index, 0), rank)
    return merged


def reach_estimate(registers):
    zeros = REGISTERS - len(registers)
    total = zeros + sum(2 ** -rank for rank in registers.values())
    estimate = (0.7213 / (1 + 1.079 / REGISTERS)) * REGISTERS ** 2 / total
    if estimate <= 2.5 * REGISTERS and zeros:
        estimate = REGISTERS * math.log(REGISTERS / zeros)
    return math.floor(estimate + 0.5)


def cookie_signature(payload, secret):
    return hmac.new(secret.encode(), f"usage-cookie-v1:{payload}".encode(), hashlib.sha256).hexdigest()


def read_usage_cookie(header, secret, now_seconds):
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name != USAGE_COOKIE or len(value) > 140:
            continue
        match = re.fullmatch(r"v1\.(\d{10})\.([0-9a-f]{32})\.([0-9a-f]{64})", value, flags=re.ASCII)
        if not match:
            continue
        born = int(match[1])
        if born > now_seconds + 60 or now_seconds - born >= USAGE_LIFETIME:
            continue
        payload = f"v1.{match[1]}.{match[2]}"
        if hmac.compare_digest(cookie_signature(payload, secret), match[3]):
            return {"id": match[2], "born": born, "value": value}
    return None


def usage_cookie_domain(hostname):
    return "nysfiretools.com" if hostname == "nysfiretools.com" or hostname.endswith(".nysfiretools.com") else None


def usage_identity(request, secret, now=None):
    if not secret or request.headers.get("Sec-GPC") == "1" or request.headers.get("DNT") == "1":
        return {}, None
    now = now or timezone.now()
    now_seconds = int(now.timestamp())
    browser = read_usage_cookie(request.headers.get("Cookie", ""), secret, now_seconds)
    cookie = None
    if not browser:
        identity = secrets.token_hex(16)
        payload = f"v1.{now_seconds}.{identity}"
        browser = {"id": identity, "born": now_seconds, "value": f"{payload}.{cookie_signature(payload, secret)}"}
        cookie = browser["value"]
    first_day = timezone.localdate(datetime.fromtimestamp(browser["born"], tz=now.tzinfo))
    point = reach_point(secret, "browser", browser["id"])
    reach = {"b": point, "n" if first_day == timezone.localdate(now) else "r": point}
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.is_active:
        reach["a"] = reach_point(secret, "account", f"scheduler:{user.pk}")
    return reach, cookie
