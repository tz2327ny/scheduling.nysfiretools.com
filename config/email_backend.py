import json
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class CloudflareEmailError(RuntimeError):
    pass


class CloudflareEmailBackend(BaseEmailBackend):
    """Django email backend for Cloudflare Email Sending's HTTPS API."""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        account_id = settings.CLOUDFLARE_ACCOUNT_ID
        api_token = settings.CLOUDFLARE_EMAIL_API_TOKEN
        if not account_id or not api_token:
            if self.fail_silently:
                return 0
            raise CloudflareEmailError("Cloudflare Email Sending is not configured.")

        sent_count = 0
        for message in email_messages:
            try:
                self._send_message(message, account_id, api_token)
            except (CloudflareEmailError, HTTPError, URLError, OSError, ValueError):
                if not self.fail_silently:
                    raise
            else:
                sent_count += 1
        return sent_count

    def _send_message(self, message, account_id, api_token):
        recipients = message.recipients()
        if not recipients:
            return

        from_email = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)[1]
        if not from_email:
            raise CloudflareEmailError("A valid sender email address is required.")

        html_body = ""
        for alternative in getattr(message, "alternatives", ()):
            if alternative.mimetype == "text/html":
                html_body = alternative.content
                break

        endpoint = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/email/sending/send"
        )
        for recipient in recipients:
            payload = {
                "to": recipient,
                "from": from_email,
                "subject": message.subject,
                "text": message.body,
            }
            if html_body:
                payload["html"] = html_body
            request = Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(
                    request,
                    timeout=settings.CLOUDFLARE_EMAIL_TIMEOUT,
                ) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                details = error.read().decode("utf-8", errors="replace")[:1000]
                raise CloudflareEmailError(
                    f"Cloudflare Email Sending returned HTTP {error.code}: {details}"
                ) from error

            if not result.get("success"):
                errors = result.get("errors") or []
                detail = "; ".join(error.get("message", "Unknown error") for error in errors)
                raise CloudflareEmailError(
                    f"Cloudflare Email Sending rejected the message: {detail or 'Unknown error'}"
                )
            delivery = result.get("result") or {}
            accepted = set(delivery.get("delivered", ())) | set(delivery.get("queued", ()))
            if recipient not in accepted:
                raise CloudflareEmailError(
                    f"Cloudflare Email Sending did not accept delivery to {recipient}."
                )
