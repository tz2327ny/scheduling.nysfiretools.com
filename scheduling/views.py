from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone

from .forms import (
    AvailabilityBlockForm,
    CourseForm,
    CourseUnitFormSet,
    InstructorAuthorizationRequestForm,
    InstructorAssignmentForm,
    InstructorForm,
    NotificationPreferenceForm,
    OrganizationForm,
    RecurringAvailabilityRuleForm,
    TrainingEventForm,
    TrainingSessionFormSet,
)
from .models import (
    AvailabilityBlock,
    Course,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
    NotificationDelivery,
    Organization,
    RecurringAvailabilityRule,
    TrainingEvent,
    TrainingSession,
)
from .permissions import (
    can_manage_courses,
    can_manage_instructor_availability,
    login_required_unless_debug,
    managed_organizations,
    require_course_manager,
    require_instructor_availability_manager,
    require_organization_manager,
)
from .services import eligible_instructors_for_session
from .notifications import notify_assignment, notify_event_update
from .unit_staffing import sync_course_units, sync_event_units


def health(request):
    return JsonResponse({"status": "ok"})


@login_required_unless_debug
def dashboard(request):
    now = timezone.now()
    organizations = Organization.objects.filter(active=True)
    selected_values = request.GET.getlist("organization")
    show_all_organizations = request.GET.get("all") == "1" or not selected_values
    selected_organization_ids = set()
    if not show_all_organizations:
        requested_ids = {
            int(value) for value in selected_values if value.isdigit()
        }
        selected_organization_ids = set(
            organizations.filter(pk__in=requested_ids).values_list("pk", flat=True)
        )
        if not selected_organization_ids:
            show_all_organizations = True

    upcoming_query = (
        TrainingEvent.objects.filter(
            Q(sessions__ends_at__gte=now) | Q(sessions__starts_at__isnull=True),
        )
        .exclude(status__in=(TrainingEvent.Status.CANCELED, TrainingEvent.Status.COMPLETED))
        .select_related("course", "host_organization")
        .annotate(
            next_session=Min("sessions__starts_at"),
            instructor_count=Count("sessions__instructor_assignments__instructor", distinct=True),
        )
        .prefetch_related(
            "sessions__course_unit",
            "sessions__instructor_assignments__instructor",
        )
        .order_by("next_session")
    )
    if not show_all_organizations:
        upcoming_query = upcoming_query.filter(
            host_organization_id__in=selected_organization_ids
        )
    scoped_upcoming = list(upcoming_query)
    for event in scoped_upcoming:
        event.staffing_gap = 0
        event.required_positions = 0
        event.filled_positions = 0
        event.unscheduled_units = 0
        for session in event.sessions.all():
            unit = session.course_unit
            required_instructors = unit.required_instructors if unit else 1
            requires_safety = bool(unit and unit.requires_safety_officer)
            lead_count = sum(
                assignment.role == InstructorAssignment.Role.LEAD
                for assignment in session.instructor_assignments.all()
            )
            assistant_count = sum(
                assignment.role == InstructorAssignment.Role.ASSISTANT
                for assignment in session.instructor_assignments.all()
            )
            safety_count = sum(
                assignment.role == InstructorAssignment.Role.SAFETY_OFFICER
                for assignment in session.instructor_assignments.all()
            )
            additional_required = max(required_instructors - 1, 0)
            event.required_positions += required_instructors + int(requires_safety)
            event.filled_positions += (
                min(lead_count, 1)
                + min(assistant_count, additional_required)
                + min(safety_count, int(requires_safety))
            )
            event.staffing_gap += max(1 - lead_count, 0) + max(
                additional_required - assistant_count, 0
            )
            if requires_safety and not safety_count:
                event.staffing_gap += 1
            if unit and not session.is_scheduled:
                event.unscheduled_units += 1

    confirmed_count = sum(
        event.status == TrainingEvent.Status.CONFIRMED for event in scoped_upcoming
    )
    proposed_count = sum(
        event.status == TrainingEvent.Status.PROPOSED for event in scoped_upcoming
    )
    open_staffing_positions = sum(event.staffing_gap for event in scoped_upcoming)
    upcoming = scoped_upcoming[:8]
    if show_all_organizations:
        scope_label = "All scheduled courses"
    elif len(selected_organization_ids) == 1:
        scope_label = organizations.get(pk=next(iter(selected_organization_ids))).short_name
    else:
        scope_label = f"{len(selected_organization_ids)} organizations selected"

    context = {
        "upcoming": upcoming,
        "upcoming_total_count": len(scoped_upcoming),
        "confirmed_count": confirmed_count,
        "proposed_count": proposed_count,
        "open_staffing_positions": open_staffing_positions,
        "active_instructors": Instructor.objects.filter(active=True).count(),
        "organizations": organizations,
        "selected_organization_ids": selected_organization_ids,
        "show_all_organizations": show_all_organizations,
        "scope_label": scope_label,
        "today": timezone.localdate(),
    }
    return render(request, "scheduling/dashboard.html", context)


@login_required_unless_debug
def organization_list(request):
    require_course_manager(request.user)
    organizations = Organization.objects.annotate(
        instructor_count=Count("instructors", distinct=True),
        training_count=Count("training_events", distinct=True),
        administrator_count=Count(
            "user_roles",
            filter=Q(user_roles__role="administrator"),
            distinct=True,
        ),
    )
    return render(
        request,
        "scheduling/organization_list.html",
        {"organizations": organizations},
    )


@login_required_unless_debug
def organization_create(request):
    require_course_manager(request.user)
    form = OrganizationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        organization = form.save()
        messages.success(request, f"{organization.name} was added.")
        return redirect("organization_list")
    return render(
        request,
        "scheduling/organization_form.html",
        {"form": form, "page_heading": "Add organization"},
    )


@login_required_unless_debug
def organization_edit(request, pk):
    require_course_manager(request.user)
    organization = get_object_or_404(Organization, pk=pk)
    form = OrganizationForm(request.POST or None, instance=organization)
    if request.method == "POST" and form.is_valid():
        organization = form.save()
        messages.success(request, f"{organization.name} was updated.")
        return redirect("organization_list")
    return render(
        request,
        "scheduling/organization_form.html",
        {
            "form": form,
            "organization": organization,
            "page_heading": f"Edit {organization.short_name}",
        },
    )


@login_required_unless_debug
def schedule(request):
    events = (
        TrainingEvent.objects.select_related("course", "host_organization")
        .annotate(next_session=Min("sessions__starts_at"))
        .prefetch_related("sessions")
        .order_by("next_session")
    )
    status = request.GET.get("status")
    organization = request.GET.get("organization")
    if status:
        events = events.filter(status=status)
    if organization:
        events = events.filter(host_organization_id=organization)
    return render(
        request,
        "scheduling/schedule.html",
        {
            "events": events,
            "organizations": Organization.objects.filter(active=True),
            "status_choices": TrainingEvent.Status.choices,
            "selected_status": status or "",
            "selected_organization": organization or "",
        },
    )


@login_required_unless_debug
def course_list(request):
    course_manager = can_manage_courses(request.user)
    courses = (Course.objects.all() if course_manager else Course.objects.filter(active=True)).annotate(
        authorized_count=Count(
            "instructor_authorizations",
            filter=Q(instructor_authorizations__status="active"),
            distinct=True,
        ),
        upcoming_count=Count(
            "events",
            filter=Q(
                events__sessions__ends_at__gte=timezone.now(),
                events__status__in=(
                    TrainingEvent.Status.PROPOSED,
                    TrainingEvent.Status.CONFIRMED,
                ),
            ),
            distinct=True,
        ),
    )
    query = request.GET.get("q", "").strip()
    if query:
        courses = courses.filter(
            Q(record_number__icontains=query)
            | Q(name__icontains=query)
            | Q(instructor_requirements__icontains=query)
        )
    courses = courses.order_by("name")
    page_obj = Paginator(courses, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "scheduling/course_list.html",
        {
            "courses": page_obj,
            "page_obj": page_obj,
            "course_count": page_obj.paginator.count,
            "query": query,
            "can_manage_courses": course_manager,
        },
    )


@login_required_unless_debug
def course_create(request):
    require_course_manager(request.user)
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            sync_course_units(course)
            messages.success(request, "Course was created. Review its unit staffing below.")
            return redirect("course_edit", pk=course.pk)
    else:
        form = CourseForm()
    return render(
        request,
        "scheduling/course_form.html",
        {"form": form, "page_heading": "Add course"},
    )


@login_required_unless_debug
def course_edit(request, pk):
    require_course_manager(request.user)
    course = get_object_or_404(Course, pk=pk)
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        unit_formset = CourseUnitFormSet(request.POST, instance=course)
        if form.is_valid() and unit_formset.is_valid():
            with transaction.atomic():
                form.save()
                unit_formset.save()
            messages.success(request, "Course was updated.")
            return redirect("course_list")
    else:
        form = CourseForm(instance=course)
        if not course.units.exists():
            sync_course_units(course)
        unit_formset = CourseUnitFormSet(instance=course)
    return render(
        request,
        "scheduling/course_form.html",
        {
            "form": form,
            "unit_formset": unit_formset,
            "page_heading": f"Edit {course.record_number}",
        },
    )


@login_required_unless_debug
def instructor_list(request):
    include_inactive = request.GET.get("inactive") == "1"
    instructors = Instructor.objects.select_related("home_organization").annotate(
        authorization_count=Count(
            "course_authorizations",
            filter=Q(course_authorizations__status="active"),
            distinct=True,
        ),
        upcoming_assignment_count=Count(
            "assignments",
            filter=Q(assignments__session__ends_at__gte=timezone.now()),
            distinct=True,
        ),
        availability_count=Count(
            "availability_blocks",
            filter=Q(availability_blocks__ends_at__gte=timezone.now()),
            distinct=True,
        ),
        recurring_availability_count=Count(
            "recurring_availability_rules",
            distinct=True,
        ),
    )
    if not include_inactive:
        instructors = instructors.filter(active=True)
    organization = request.GET.get("organization")
    if organization:
        instructors = instructors.filter(home_organization_id=organization)
    managed_ids = set(managed_organizations(request.user).values_list("id", flat=True))
    return render(
        request,
        "scheduling/instructor_list.html",
        {
            "instructors": instructors,
            "organizations": Organization.objects.filter(active=True),
            "selected_organization": organization or "",
            "managed_ids": managed_ids,
            "can_create": bool(managed_ids),
            "include_inactive": include_inactive,
        },
    )


@login_required_unless_debug
def training_detail(request, pk):
    event = get_object_or_404(
        TrainingEvent.objects.select_related("course", "host_organization").prefetch_related(
            "sessions__course_unit",
            "sessions__instructor_assignments__instructor__home_organization",
        ),
        pk=pk,
    )
    can_manage = managed_organizations(request.user).filter(
        pk=event.host_organization_id
    ).exists()
    session_rows = []
    total_required = 0
    total_filled = 0
    unscheduled_units = 0
    for session in event.sessions.all():
        assignments = list(session.instructor_assignments.all())
        required_instructors = (
            session.course_unit.required_instructors if session.course_unit else 1
        )
        requires_safety = bool(
            session.course_unit and session.course_unit.requires_safety_officer
        )
        lead_assignments = [
            assignment
            for assignment in assignments
            if assignment.role == InstructorAssignment.Role.LEAD
        ]
        assistant_assignments = [
            assignment
            for assignment in assignments
            if assignment.role == InstructorAssignment.Role.ASSISTANT
        ]
        safety_assignments = [
            assignment
            for assignment in assignments
            if assignment.role == InstructorAssignment.Role.SAFETY_OFFICER
        ]
        additional_required = max(required_instructors - 1, 0)
        instructional_count = min(len(lead_assignments), 1) + min(
            len(assistant_assignments), additional_required
        )
        safety_count = len(safety_assignments)
        instructional_gap = max(1 - len(lead_assignments), 0) + max(
            additional_required - len(assistant_assignments), 0
        )
        safety_gap = int(requires_safety and not safety_count)
        total_required += required_instructors + int(requires_safety)
        total_filled += min(instructional_count, required_instructors) + min(
            safety_count, int(requires_safety)
        )
        if session.course_unit and not session.is_scheduled:
            unscheduled_units += 1
        staffing_slots = []
        lead_assignment = lead_assignments[0] if lead_assignments else None
        staffing_slots.append(
            {
                "label": "Lead instructor",
                "role": InstructorAssignment.Role.LEAD,
                "assignment": lead_assignment,
                "assignment_form": (
                    InstructorAssignmentForm(
                        session=session,
                        role=InstructorAssignment.Role.LEAD,
                        auto_id=False,
                    )
                    if can_manage and session.is_scheduled and not lead_assignment
                    else None
                ),
            }
        )
        for position in range(1, additional_required + 1):
            assistant_assignment = (
                assistant_assignments[position - 1]
                if len(assistant_assignments) >= position
                else None
            )
            staffing_slots.append(
                {
                    "label": f"Additional instructor {position}",
                    "role": InstructorAssignment.Role.ASSISTANT,
                    "assignment": assistant_assignment,
                    "assignment_form": (
                        InstructorAssignmentForm(
                            session=session,
                            role=InstructorAssignment.Role.ASSISTANT,
                            auto_id=False,
                        )
                        if can_manage and session.is_scheduled and not assistant_assignment
                        else None
                    ),
                }
            )
        safety_assignment = (
            safety_assignments[0] if requires_safety and safety_assignments else None
        )
        if requires_safety:
            staffing_slots.append(
                {
                    "label": "Safety officer",
                    "role": InstructorAssignment.Role.SAFETY_OFFICER,
                    "assignment": safety_assignment,
                    "assignment_form": (
                        InstructorAssignmentForm(
                            session=session,
                            role=InstructorAssignment.Role.SAFETY_OFFICER,
                            auto_id=False,
                        )
                        if can_manage and session.is_scheduled and not safety_assignment
                        else None
                    ),
                }
            )
        used_assignment_ids = {
            assignment.pk
            for assignment in [
                lead_assignment,
                *assistant_assignments[:additional_required],
                safety_assignment,
            ]
            if assignment
        }
        session_rows.append(
            {
                "session": session,
                "assignments": assignments,
                "required_instructors": required_instructors,
                "requires_safety": requires_safety,
                "instructional_count": instructional_count,
                "instructional_gap": instructional_gap,
                "safety_gap": safety_gap,
                "staffing_slots": staffing_slots,
                "extra_assignments": [
                    assignment
                    for assignment in assignments
                    if assignment.pk not in used_assignment_ids
                ],
            }
        )
    return render(
        request,
        "scheduling/training_detail.html",
        {
            "event": event,
            "session_rows": session_rows,
            "can_manage": can_manage,
            "total_required": total_required,
            "total_filled": total_filled,
            "total_open": max(total_required - total_filled, 0),
            "unscheduled_units": unscheduled_units,
        },
    )


@login_required_unless_debug
@require_POST
def session_assignment_add(request, pk, session_pk):
    event = get_object_or_404(TrainingEvent, pk=pk)
    require_organization_manager(request.user, event.host_organization)
    session = get_object_or_404(TrainingSession, pk=session_pk, event=event)
    form = InstructorAssignmentForm(request.POST, session=session)
    if form.is_valid():
        assignment = form.save()
        notify_assignment(assignment)
        messages.success(request, "Instructor was assigned to this course unit.")
    else:
        error_text = " ".join(
            error for errors in form.errors.values() for error in errors
        )
        messages.error(request, error_text or "The instructor could not be assigned.")
    return redirect("training_detail", pk=event.pk)


@login_required_unless_debug
@require_POST
def session_assignment_remove(request, pk, session_pk, assignment_pk):
    event = get_object_or_404(TrainingEvent, pk=pk)
    require_organization_manager(request.user, event.host_organization)
    assignment = get_object_or_404(
        InstructorAssignment,
        pk=assignment_pk,
        session_id=session_pk,
        session__event=event,
    )
    instructor_name = assignment.instructor.full_name
    assignment.session
    assignment.delete()
    notify_assignment(assignment, removed=True)
    messages.success(request, f"{instructor_name} was removed from this course unit.")
    return redirect("training_detail", pk=event.pk)


@login_required_unless_debug
def training_create(request):
    organizations = managed_organizations(request.user)
    if not organizations.exists():
        raise PermissionDenied("No managed organization is assigned to this account.")
    event = TrainingEvent(created_by=request.user if request.user.is_authenticated else None)
    if request.method == "POST":
        form = TrainingEventForm(request.POST, instance=event, managed_organizations=organizations)
        if form.is_valid():
            if form.cleaned_data["status"] != TrainingEvent.Status.PROPOSED:
                form.add_error("status", "Create the training as Purposed, schedule every unit, then confirm it.")
            else:
                event = form.save()
                if not sync_event_units(event):
                    TrainingSession.objects.create(event=event)
                messages.success(request, "Training was created. Set a date and time for every course unit.")
                return redirect("training_edit", pk=event.pk)
    else:
        form = TrainingEventForm(instance=event, managed_organizations=organizations)
    return render(
        request,
        "scheduling/training_form.html",
        {"form": form, "page_heading": "Propose training", "is_create": True},
    )


@login_required_unless_debug
def training_edit(request, pk):
    event = get_object_or_404(TrainingEvent, pk=pk)
    require_organization_manager(request.user, event.host_organization)
    organizations = managed_organizations(request.user)
    sync_event_units(event)
    if request.method == "POST":
        form = TrainingEventForm(request.POST, instance=event, managed_organizations=organizations)
        formset = TrainingSessionFormSet(request.POST, instance=event)
        if form.is_valid() and formset.is_valid():
            details_changed = form.has_changed() or any(
                session_form.has_changed() for session_form in formset.forms
            )
            requires_complete_schedule = form.cleaned_data["status"] in (
                TrainingEvent.Status.CONFIRMED,
                TrainingEvent.Status.COMPLETED,
            )
            missing_units = [
                session_form.instance.course_unit
                for session_form in formset.forms
                if session_form.instance.course_unit_id
                and session_form.instance.course_unit.active
                and not (
                    session_form.cleaned_data.get("starts_at")
                    and session_form.cleaned_data.get("ends_at")
                )
            ]
            if requires_complete_schedule and missing_units:
                form.add_error(
                    "status",
                    f"Schedule every course unit before confirming. {len(missing_units)} unit(s) still need dates.",
                )
            else:
                with transaction.atomic():
                    form.save()
                    formset.save()
                    if details_changed:
                        transaction.on_commit(lambda: notify_event_update(event))
                messages.success(request, "Training and unit schedule were updated.")
                return redirect("training_detail", pk=event.pk)
    else:
        form = TrainingEventForm(instance=event, managed_organizations=organizations)
        formset = TrainingSessionFormSet(instance=event)
    return render(
        request,
        "scheduling/training_form.html",
        {"form": form, "formset": formset, "page_heading": "Edit training"},
    )


@login_required_unless_debug
def instructor_create(request):
    organizations = managed_organizations(request.user)
    if not organizations.exists():
        raise PermissionDenied("No managed organization is assigned to this account.")
    if request.method == "POST":
        form = InstructorForm(
            request.POST,
            managed_organizations=organizations,
            authorization_verifier=request.user if request.user.is_superuser else None,
        )
        if form.is_valid():
            instructor = form.save()
            messages.success(request, "Instructor was created.")
            return redirect("instructor_list")
    else:
        form = InstructorForm(
            managed_organizations=organizations,
            authorization_verifier=request.user if request.user.is_superuser else None,
        )
    return render(
        request,
        "scheduling/instructor_form.html",
        {"form": form, "page_heading": "Add instructor"},
    )


@login_required_unless_debug
def instructor_edit(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_organization_manager(request.user, instructor.home_organization)
    organizations = managed_organizations(request.user)
    if request.method == "POST":
        form = InstructorForm(
            request.POST,
            instance=instructor,
            managed_organizations=organizations,
            authorization_verifier=request.user if request.user.is_superuser else None,
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Instructor was updated.")
            return redirect("instructor_list")
    else:
        form = InstructorForm(
            instance=instructor,
            managed_organizations=organizations,
            authorization_verifier=request.user if request.user.is_superuser else None,
        )
    return render(
        request,
        "scheduling/instructor_form.html",
        {
            "form": form,
            "page_heading": f"Edit {instructor.full_name}",
            "instructor": instructor,
        },
    )


@login_required_unless_debug
def instructor_delete(request, pk):
    instructor = get_object_or_404(
        Instructor.objects.select_related("home_organization", "user").annotate(
            assignment_count=Count("assignments", distinct=True),
        ),
        pk=pk,
    )
    require_organization_manager(request.user, instructor.home_organization)
    will_deactivate = instructor.assignment_count > 0
    if request.method == "POST":
        instructor_name = instructor.full_name
        if will_deactivate:
            instructor.active = False
            instructor.save(update_fields=("active",))
            messages.success(
                request,
                f"{instructor_name} was removed from active scheduling. Existing course history was preserved.",
            )
        else:
            instructor.delete()
            messages.success(request, f"{instructor_name} was deleted.")
        return redirect("instructor_list")
    return render(
        request,
        "scheduling/instructor_confirm_delete.html",
        {
            "instructor": instructor,
            "will_deactivate": will_deactivate,
        },
    )


@login_required_unless_debug
def instructor_authorizations(request, pk):
    instructor = get_object_or_404(
        Instructor.objects.select_related("home_organization", "user"),
        pk=pk,
    )
    if not request.user.is_authenticated or (
        not request.user.is_superuser and instructor.user_id != request.user.id
    ):
        raise PermissionDenied("Only the instructor or a Site Administrator can view these authorizations.")
    form = InstructorAuthorizationRequestForm(
        request.POST or None,
        instructor=instructor,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Your course authorization selections were submitted for State approval.")
        return redirect("instructor_authorizations", pk=instructor.pk)
    authorizations = instructor.course_authorizations.select_related("course", "verified_by").order_by(
        "course__name", "course__record_number"
    )
    return render(
        request,
        "scheduling/instructor_authorizations.html",
        {
            "instructor": instructor,
            "form": form,
            "authorizations": authorizations,
        },
    )


@login_required_unless_debug
def instructor_notifications(request, pk):
    instructor = get_object_or_404(
        Instructor.objects.select_related("user", "home_organization"),
        pk=pk,
    )
    can_edit = bool(
        request.user.is_authenticated and instructor.user_id == request.user.id
    )
    can_view = can_edit or bool(
        request.user.is_authenticated and request.user.is_superuser
    )
    if not can_view:
        raise PermissionDenied(
            "Only the instructor or a Site Administrator can view notification preferences."
        )
    if request.method == "POST" and not can_edit:
        raise PermissionDenied("Only the instructor can change text-message consent.")
    form = NotificationPreferenceForm(
        request.POST or None,
        instructor=instructor,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Notification preferences were updated.")
        return redirect("instructor_notifications", pk=instructor.pk)
    deliveries = NotificationDelivery.objects.filter(instructor=instructor)[:20]
    return render(
        request,
        "scheduling/instructor_notifications.html",
        {
            "instructor": instructor,
            "form": form,
            "can_edit": can_edit,
            "deliveries": deliveries,
            "preferences": form.preference,
            "email_configured": settings.NOTIFICATION_EMAIL_ENABLED,
            "sms_configured": bool(
                settings.TWILIO_ACCOUNT_SID
                and settings.TWILIO_AUTH_TOKEN
                and (
                    settings.TWILIO_MESSAGING_SERVICE_SID
                    or settings.TWILIO_FROM_NUMBER
                )
            ),
        },
    )


@login_required_unless_debug
def instructor_availability(request, pk):
    instructor = get_object_or_404(
        Instructor.objects.select_related("home_organization"),
        pk=pk,
    )
    can_manage = can_manage_instructor_availability(request.user, instructor)
    quick_entry = AvailabilityBlock(instructor=instructor)
    quick_form = AvailabilityBlockForm(
        request.POST if request.method == "POST" else None,
        instance=quick_entry,
        initial={
            "status": AvailabilityBlock.Status.AVAILABLE,
            "all_day": True,
        },
    )
    if request.method == "POST":
        require_instructor_availability_manager(request.user, instructor)
        if quick_form.is_valid():
            quick_form.save()
            messages.success(request, "Availability was added.")
            week_value = request.POST.get("week", "")
            availability_url = reverse("instructor_availability", args=(instructor.pk,))
            if week_value:
                availability_url = f"{availability_url}?week={week_value}"
            return redirect(availability_url)
    requested_week = request.POST.get("week", "") or request.GET.get("week", "")
    try:
        selected_date = date.fromisoformat(requested_week)
    except ValueError:
        selected_date = timezone.localdate()
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_end = week_start + timedelta(days=7)
    previous_week = week_start - timedelta(days=7)
    next_week = week_start + timedelta(days=7)
    current_timezone = timezone.get_current_timezone()
    week_start_at = timezone.make_aware(
        datetime.combine(week_start, time.min),
        current_timezone,
    )
    week_end_at = timezone.make_aware(
        datetime.combine(week_end, time.min),
        current_timezone,
    )
    week_entries = list(
        instructor.availability_blocks.filter(
            starts_at__lt=week_end_at,
            ends_at__gt=week_start_at,
        )
    )
    week_rules = list(
        instructor.recurring_availability_rules.filter(
            starts_on__lt=week_end,
        ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=week_start))
    )
    week_days = []
    for day_offset in range(7):
        calendar_date = week_start + timedelta(days=day_offset)
        day_start = timezone.make_aware(
            datetime.combine(calendar_date, time.min),
            current_timezone,
        )
        day_end = day_start + timedelta(days=1)
        week_days.append(
            {
                "date": calendar_date,
                "is_today": calendar_date == timezone.localdate(),
                "entries": [
                    entry
                    for entry in week_entries
                    if entry.starts_at < day_end and entry.ends_at > day_start
                ],
                "recurring_rules": [
                    rule for rule in week_rules if rule.occurs_on(calendar_date)
                ],
            }
        )
    entries = instructor.availability_blocks.filter(ends_at__gte=timezone.now())
    recurring_rules = instructor.recurring_availability_rules.all()
    recurring_form = RecurringAvailabilityRuleForm(
        initial={
            "status": AvailabilityBlock.Status.UNAVAILABLE,
            "weekdays": ("0", "1", "2", "3", "4"),
            "all_day": False,
            "start_time": time(hour=8),
            "end_time": time(hour=17),
            "starts_on": max(week_start, timezone.localdate()),
        }
    )
    return render(
        request,
        "scheduling/availability_list.html",
        {
            "instructor": instructor,
            "entries": entries,
            "week_days": week_days,
            "week_start": week_start,
            "week_last_day": week_end - timedelta(days=1),
            "previous_week": previous_week,
            "next_week": next_week,
            "can_manage": can_manage,
            "quick_form": quick_form,
            "recurring_form": recurring_form,
            "recurring_rules": recurring_rules,
        },
    )


@login_required_unless_debug
def recurring_availability_create(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    if request.method != "POST":
        return redirect("instructor_availability", pk=instructor.pk)
    rule = RecurringAvailabilityRule(instructor=instructor)
    form = RecurringAvailabilityRuleForm(request.POST, instance=rule)
    if form.is_valid():
        form.save()
        messages.success(request, "Weekly availability schedule was created.")
        availability_url = reverse("instructor_availability", args=(instructor.pk,))
        return_week = request.POST.get("return_week", "")
        if return_week:
            availability_url = f"{availability_url}?week={return_week}"
        return redirect(availability_url)
    messages.error(request, "Review the weekly schedule fields and try again.")
    return render(
        request,
        "scheduling/recurring_availability_form.html",
        {
            "form": form,
            "instructor": instructor,
            "page_heading": "Add weekly schedule",
        },
    )


@login_required_unless_debug
def recurring_availability_edit(request, pk, rule_pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    rule = get_object_or_404(
        RecurringAvailabilityRule,
        pk=rule_pk,
        instructor=instructor,
    )
    form = RecurringAvailabilityRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Weekly availability schedule was updated.")
        return redirect("instructor_availability", pk=instructor.pk)
    return render(
        request,
        "scheduling/recurring_availability_form.html",
        {
            "form": form,
            "instructor": instructor,
            "rule": rule,
            "page_heading": "Edit weekly schedule",
        },
    )


@login_required_unless_debug
def recurring_availability_delete(request, pk, rule_pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    rule = get_object_or_404(
        RecurringAvailabilityRule,
        pk=rule_pk,
        instructor=instructor,
    )
    if request.method == "POST":
        rule.delete()
        messages.success(request, "Weekly availability schedule was removed.")
        return redirect("instructor_availability", pk=instructor.pk)
    return render(
        request,
        "scheduling/recurring_availability_confirm_delete.html",
        {"instructor": instructor, "rule": rule},
    )


@login_required_unless_debug
def availability_create(request, pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    entry = AvailabilityBlock(instructor=instructor)
    selected_start = request.GET.get("start") or request.GET.get("date")
    selected_end = request.GET.get("end") or selected_start
    if selected_start:
        try:
            start_date = date.fromisoformat(selected_start)
            end_date = date.fromisoformat(selected_end)
            if end_date < start_date:
                start_date, end_date = end_date, start_date
            current_timezone = timezone.get_current_timezone()
            entry.starts_at = timezone.make_aware(
                datetime.combine(start_date, time(hour=8)),
                current_timezone,
            )
            entry.ends_at = timezone.make_aware(
                datetime.combine(end_date, time(hour=17)),
                current_timezone,
            )
        except (TypeError, ValueError):
            pass
    if request.method == "POST":
        form = AvailabilityBlockForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Availability was added.")
            return redirect("instructor_availability", pk=instructor.pk)
    else:
        form = AvailabilityBlockForm(instance=entry)
    return render(
        request,
        "scheduling/availability_form.html",
        {
            "form": form,
            "instructor": instructor,
            "page_heading": "Add availability",
        },
    )


@login_required_unless_debug
def availability_edit(request, pk, entry_pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    entry = get_object_or_404(
        AvailabilityBlock,
        pk=entry_pk,
        instructor=instructor,
    )
    if request.method == "POST":
        form = AvailabilityBlockForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Availability was updated.")
            return redirect("instructor_availability", pk=instructor.pk)
    else:
        form = AvailabilityBlockForm(instance=entry)
    return render(
        request,
        "scheduling/availability_form.html",
        {
            "form": form,
            "instructor": instructor,
            "entry": entry,
            "page_heading": "Edit availability",
        },
    )


@login_required_unless_debug
def availability_delete(request, pk, entry_pk):
    instructor = get_object_or_404(Instructor, pk=pk)
    require_instructor_availability_manager(request.user, instructor)
    entry = get_object_or_404(
        AvailabilityBlock,
        pk=entry_pk,
        instructor=instructor,
    )
    if request.method == "POST":
        entry.delete()
        messages.success(request, "Availability was removed.")
        return redirect("instructor_availability", pk=instructor.pk)
    return render(
        request,
        "scheduling/availability_confirm_delete.html",
        {"instructor": instructor, "entry": entry},
    )

# Create your views here.
