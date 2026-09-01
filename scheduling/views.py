import calendar
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Min, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

from .forms import (
    AvailabilityBlockForm,
    CourseForm,
    InstructorForm,
    TrainingEventForm,
    TrainingSessionFormSet,
)
from .models import (
    AvailabilityBlock,
    Course,
    Instructor,
    InstructorAssignment,
    Organization,
    TrainingEvent,
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


def health(request):
    return JsonResponse({"status": "ok"})


@login_required_unless_debug
def dashboard(request):
    now = timezone.now()
    upcoming = list(
        TrainingEvent.objects.filter(
            sessions__ends_at__gte=now,
        )
        .exclude(status__in=(TrainingEvent.Status.CANCELED, TrainingEvent.Status.COMPLETED))
        .select_related("course", "host_organization")
        .annotate(
            next_session=Min("sessions__starts_at"),
            instructor_count=Count("sessions__instructor_assignments__instructor", distinct=True),
        )
        .prefetch_related("sessions__instructor_assignments__instructor")
        .order_by("next_session")[:8]
    )
    for event in upcoming:
        event.staffing_gap = max(
            1 - event.instructor_count,
            0,
        )

    confirmed_count = sum(
        event.status == TrainingEvent.Status.CONFIRMED for event in upcoming
    )
    proposed_count = sum(event.status == TrainingEvent.Status.PROPOSED for event in upcoming)
    needs_instructors = sum(event.staffing_gap > 0 for event in upcoming)

    context = {
        "upcoming": upcoming,
        "confirmed_count": confirmed_count,
        "proposed_count": proposed_count,
        "needs_instructors": needs_instructors,
        "active_instructors": Instructor.objects.filter(active=True).count(),
        "today": timezone.localdate(),
    }
    return render(request, "scheduling/dashboard.html", context)


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
            form.save()
            messages.success(request, "Course was created.")
            return redirect("course_list")
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
        if form.is_valid():
            form.save()
            messages.success(request, "Course was updated.")
            return redirect("course_list")
    else:
        form = CourseForm(instance=course)
    return render(
        request,
        "scheduling/course_form.html",
        {"form": form, "page_heading": f"Edit {course.record_number}"},
    )


@login_required_unless_debug
def instructor_list(request):
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
    )
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
        },
    )


@login_required_unless_debug
def training_detail(request, pk):
    event = get_object_or_404(
        TrainingEvent.objects.select_related("course", "host_organization").prefetch_related(
            "sessions__instructor_assignments__instructor__home_organization"
        ),
        pk=pk,
    )
    session_rows = []
    for session in event.sessions.all():
        session_rows.append(
            {
                "session": session,
                "assignments": session.instructor_assignments.all(),
                "eligible_leads": eligible_instructors_for_session(
                    session, InstructorAssignment.Role.LEAD
                )[:8],
                "eligible_assistants": eligible_instructors_for_session(
                    session, InstructorAssignment.Role.ASSISTANT
                )[:8],
            }
        )
    return render(
        request,
        "scheduling/training_detail.html",
        {
            "event": event,
            "session_rows": session_rows,
            "can_manage": managed_organizations(request.user).filter(pk=event.host_organization_id).exists(),
        },
    )


@login_required_unless_debug
def training_create(request):
    organizations = managed_organizations(request.user)
    if not organizations.exists():
        raise PermissionDenied("No managed organization is assigned to this account.")
    event = TrainingEvent(created_by=request.user if request.user.is_authenticated else None)
    if request.method == "POST":
        form = TrainingEventForm(request.POST, instance=event, managed_organizations=organizations)
        formset = TrainingSessionFormSet(request.POST, instance=event)
        if form.is_valid() and formset.is_valid():
            event = form.save()
            formset.instance = event
            formset.save()
            messages.success(request, "Training was created.")
            return redirect("training_detail", pk=event.pk)
    else:
        form = TrainingEventForm(instance=event, managed_organizations=organizations)
        formset = TrainingSessionFormSet(instance=event)
    return render(
        request,
        "scheduling/training_form.html",
        {"form": form, "formset": formset, "page_heading": "Propose training"},
    )


@login_required_unless_debug
def training_edit(request, pk):
    event = get_object_or_404(TrainingEvent, pk=pk)
    require_organization_manager(request.user, event.host_organization)
    organizations = managed_organizations(request.user)
    if request.method == "POST":
        form = TrainingEventForm(request.POST, instance=event, managed_organizations=organizations)
        formset = TrainingSessionFormSet(request.POST, instance=event)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, "Training was updated.")
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
        form = InstructorForm(request.POST, managed_organizations=organizations)
        if form.is_valid():
            instructor = form.save()
            messages.success(request, "Instructor was created.")
            return redirect("instructor_list")
    else:
        form = InstructorForm(managed_organizations=organizations)
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
        )
        if form.is_valid():
            form.save()
            messages.success(request, "Instructor was updated.")
            return redirect("instructor_list")
    else:
        form = InstructorForm(instance=instructor, managed_organizations=organizations)
    return render(
        request,
        "scheduling/instructor_form.html",
        {"form": form, "page_heading": f"Edit {instructor.full_name}"},
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
            month_value = request.POST.get("month", "")
            availability_url = reverse("instructor_availability", args=(instructor.pk,))
            if month_value:
                availability_url = f"{availability_url}?month={month_value}"
            return redirect(availability_url)
    requested_month = request.POST.get("month", "") or request.GET.get("month", "")
    try:
        month_start = datetime.strptime(requested_month, "%Y-%m").date()
    except ValueError:
        month_start = timezone.localdate().replace(day=1)
    month_start = month_start.replace(day=1)
    next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    previous_month = (month_start - timedelta(days=1)).replace(day=1)
    current_timezone = timezone.get_current_timezone()
    month_start_at = timezone.make_aware(
        datetime.combine(month_start, time.min),
        current_timezone,
    )
    next_month_at = timezone.make_aware(
        datetime.combine(next_month, time.min),
        current_timezone,
    )
    month_entries = list(
        instructor.availability_blocks.filter(
            starts_at__lt=next_month_at,
            ends_at__gt=month_start_at,
        )
    )
    calendar_weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(
        month_start.year,
        month_start.month,
    ):
        calendar_week = []
        for calendar_date in week:
            day_start = timezone.make_aware(
                datetime.combine(calendar_date, time.min),
                current_timezone,
            )
            day_end = day_start + timedelta(days=1)
            calendar_week.append(
                {
                    "date": calendar_date,
                    "in_month": calendar_date.month == month_start.month,
                    "is_today": calendar_date == timezone.localdate(),
                    "entries": [
                        entry
                        for entry in month_entries
                        if entry.starts_at < day_end and entry.ends_at > day_start
                    ],
                }
            )
        calendar_weeks.append(calendar_week)
    entries = instructor.availability_blocks.filter(ends_at__gte=timezone.now())
    return render(
        request,
        "scheduling/availability_list.html",
        {
            "instructor": instructor,
            "entries": entries,
            "calendar_weeks": calendar_weeks,
            "month_start": month_start,
            "previous_month": previous_month,
            "next_month": next_month,
            "can_manage": can_manage,
            "quick_form": quick_form,
        },
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
