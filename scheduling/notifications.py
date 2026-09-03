import base64
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import NotificationDelivery, NotificationPreference


def _is_notification_participant(instructor):
    """Limit operational messages to active instructors with active login accounts."""

    return bool(
        instructor
        and instructor.active
        and instructor.user_id
        and instructor.user.is_active
    )


def _event_url(event):
    return f"{settings.SITE_BASE_URL.rstrip('/')}{reverse('training_detail', args=(event.pk,))}"


def _session_description(session):
    unit = session.course_unit.display_name if session.course_unit else "Training session"
    if not session.is_scheduled:
        return f"{unit} — date and time not yet scheduled"
    starts_at = timezone.localtime(session.starts_at)
    ends_at = timezone.localtime(session.ends_at)
    start_time = starts_at.strftime("%I:%M %p").lstrip("0")
    end_time = ends_at.strftime("%I:%M %p").lstrip("0")
    return (
        f"{unit} — {starts_at:%A, %B} {starts_at.day}, {starts_at.year} at {start_time}"
        f" to {end_time}"
    )


def _queue_delivery(instructor, channel, kind, destination, subject, body, event=None, session=None):
    delivery = NotificationDelivery.objects.create(
        instructor=instructor,
        event=event,
        session=session,
        channel=channel,
        kind=kind,
        destination=destination,
        subject=subject,
        body=body,
    )
    transaction.on_commit(lambda delivery_id=delivery.pk: deliver_notification(delivery_id))
    return delivery


def queue_instructor_notification(instructor, kind, subject, body, event=None, session=None):
    if not _is_notification_participant(instructor):
        return []

    preferences, _ = NotificationPreference.objects.get_or_create(instructor=instructor)
    is_assignment = kind in (
        NotificationDelivery.Kind.ASSIGNMENT,
        NotificationDelivery.Kind.ASSIGNMENT_REMOVED,
    )
    is_schedule = kind in (
        NotificationDelivery.Kind.SCHEDULE_UPDATE,
        NotificationDelivery.Kind.CANCELLATION,
    )
    if is_assignment and not preferences.assignment_updates:
        return []
    if is_schedule and not preferences.schedule_updates:
        return []

    deliveries = []
    email = (instructor.user.email or instructor.email).strip()
    if preferences.email_enabled and email:
        deliveries.append(
            _queue_delivery(
                instructor,
                NotificationDelivery.Channel.EMAIL,
                kind,
                email,
                subject,
                body,
                event,
                session,
            )
        )
    if preferences.sms_enabled and preferences.sms_consented_at and instructor.phone:
        sms_body = body
        if len(sms_body) > 1500:
            sms_body = f"{sms_body[:1497]}..."
        deliveries.append(
            _queue_delivery(
                instructor,
                NotificationDelivery.Channel.SMS,
                kind,
                instructor.phone,
                "",
                sms_body,
                event,
                session,
            )
        )
    return deliveries


def notify_assignment(assignment, removed=False):
    event = assignment.session.event
    action = "removed from" if removed else "assigned to"
    kind = (
        NotificationDelivery.Kind.ASSIGNMENT_REMOVED
        if removed
        else NotificationDelivery.Kind.ASSIGNMENT
    )
    subject = f"NYSFIRETOOLS: {'Assignment removed' if removed else 'New instructor assignment'}"
    body = (
        f"Hello {assignment.instructor.first_name},\n\n"
        f"You were {action} {event.course.name}.\n"
        f"{_session_description(assignment.session)}\n"
        f"Role: {assignment.get_role_display()}\n"
        f"Location: {assignment.session.location_override or event.location_name}\n\n"
        f"View training: {_event_url(event)}\n\n"
        "NYSFIRETOOLS Fire Training Scheduler"
    )
    return queue_instructor_notification(
        assignment.instructor,
        kind,
        subject,
        body,
        event=event,
        session=assignment.session,
    )


def notify_event_update(event):
    kind = (
        NotificationDelivery.Kind.CANCELLATION
        if event.status == event.Status.CANCELED
        else NotificationDelivery.Kind.SCHEDULE_UPDATE
    )
    subject = (
        f"NYSFIRETOOLS: {event.course.name} cancelled"
        if kind == NotificationDelivery.Kind.CANCELLATION
        else f"NYSFIRETOOLS: {event.course.name} schedule updated"
    )
    body = (
        f"The training details for {event.course.name} have changed.\n"
        f"Status: {event.get_status_display()}\n"
        f"Host: {event.host_organization.name}\n"
        f"Location: {event.location_name}\n\n"
        f"Review the current unit schedule: {_event_url(event)}\n\n"
        "NYSFIRETOOLS Fire Training Scheduler"
    )
    instructors = {
        assignment.instructor_id: assignment.instructor
        for session in event.sessions.all()
        for assignment in session.instructor_assignments.all()
    }
    deliveries = []
    for instructor in instructors.values():
        deliveries.extend(
            queue_instructor_notification(
                instructor,
                kind,
                subject,
                body,
                event=event,
            )
        )
    return deliveries


def notify_account_approved(instructor):
    subject = "Your NYSFIRETOOLS Fire Training Scheduler account was approved"
    body = (
        f"Hello {instructor.first_name},\n\n"
        "Your instructor account has been approved. You can now sign in and manage "
        "your availability and notification preferences.\n\n"
        f"Sign in: {settings.SITE_BASE_URL.rstrip('/')}{reverse('login')}\n\n"
        "NYSFIRETOOLS Fire Training Scheduler"
    )
    return queue_instructor_notification(
        instructor,
        NotificationDelivery.Kind.ACCOUNT_APPROVED,
        subject,
        body,
    )


def notify_authorization_approved(authorization):
    subject = f"NYSFIRETOOLS: {authorization.course.name} authorization approved"
    body = (
        f"Hello {authorization.instructor.first_name},\n\n"
        f"Your authorization to teach {authorization.course.name} "
        f"({authorization.course.record_number}) was approved.\n\n"
        "NYSFIRETOOLS Fire Training Scheduler"
    )
    return queue_instructor_notification(
        authorization.instructor,
        NotificationDelivery.Kind.AUTHORIZATION_APPROVED,
        subject,
        body,
    )


def deliver_notification(delivery_id):
    delivery = NotificationDelivery.objects.select_related("instructor__user").get(pk=delivery_id)
    if not _is_notification_participant(delivery.instructor):
        delivery.status = NotificationDelivery.Status.SKIPPED
        delivery.error_message = "Instructor does not have an active linked account."
        delivery.save(update_fields=("status", "error_message"))
        return delivery

    try:
        if delivery.channel == NotificationDelivery.Channel.EMAIL:
            if not settings.NOTIFICATION_EMAIL_ENABLED:
                delivery.status = NotificationDelivery.Status.SKIPPED
                delivery.error_message = "Email delivery is not configured."
            else:
                sent = send_mail(
                    delivery.subject,
                    delivery.body,
                    settings.DEFAULT_FROM_EMAIL,
                    [delivery.destination],
                    fail_silently=False,
                )
                if not sent:
                    raise RuntimeError("The email backend did not accept the message.")
                delivery.status = NotificationDelivery.Status.SENT
                delivery.sent_at = timezone.now()
        else:
            sid = settings.TWILIO_ACCOUNT_SID
            token = settings.TWILIO_AUTH_TOKEN
            messaging_service_sid = settings.TWILIO_MESSAGING_SERVICE_SID
            from_number = settings.TWILIO_FROM_NUMBER
            if not sid or not token or (not messaging_service_sid and not from_number):
                delivery.status = NotificationDelivery.Status.SKIPPED
                delivery.error_message = "Text-message delivery is not configured."
            else:
                payload = {"To": delivery.destination, "Body": delivery.body}
                if messaging_service_sid:
                    payload["MessagingServiceSid"] = messaging_service_sid
                else:
                    payload["From"] = from_number
                request = Request(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                    data=urlencode(payload).encode("utf-8"),
                    method="POST",
                )
                credentials = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
                request.add_header("Authorization", f"Basic {credentials}")
                request.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urlopen(request, timeout=10) as response:
                    result = json.loads(response.read().decode("utf-8"))
                delivery.provider_message_id = result.get("sid", "")
                delivery.status = NotificationDelivery.Status.SENT
                delivery.sent_at = timezone.now()
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as error:
        delivery.status = NotificationDelivery.Status.FAILED
        delivery.error_message = str(error)[:1000]
    delivery.save(
        update_fields=(
            "status",
            "provider_message_id",
            "error_message",
            "sent_at",
        )
    )
    return delivery
