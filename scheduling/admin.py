from django.contrib import admin

from .models import (
    AssistanceRequest,
    AuditEvent,
    AvailabilityBlock,
    Course,
    CourseAuthorization,
    Instructor,
    InstructorAssignment,
    Organization,
    TrainingEvent,
    TrainingSession,
)


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "active", "display_order")
    list_editable = ("active", "display_order")
    list_filter = ("kind", "active")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "record_number",
        "name",
        "minimum_instructors",
        "recommended_instructors",
        "instructor_intensive",
        "active",
    )
    list_filter = ("active", "instructor_intensive")
    search_fields = ("record_number", "name")


class CourseAuthorizationInline(admin.TabularInline):
    model = CourseAuthorization
    extra = 0


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "home_organization", "travel_preference", "active")
    list_filter = ("home_organization", "travel_preference", "active")
    search_fields = ("first_name", "last_name", "email")
    inlines = (CourseAuthorizationInline,)


class TrainingSessionInline(admin.StackedInline):
    model = TrainingSession
    extra = 1


@admin.register(TrainingEvent)
class TrainingEventAdmin(admin.ModelAdmin):
    list_display = (
        "course",
        "offering_number",
        "host_organization",
        "status",
        "location_name",
        "updated_at",
    )
    list_filter = ("status", "host_organization", "course")
    search_fields = (
        "course__record_number",
        "course__name",
        "offering_number",
        "location_name",
    )
    inlines = (TrainingSessionInline,)


@admin.register(TrainingSession)
class TrainingSessionAdmin(admin.ModelAdmin):
    list_display = ("event", "starts_at", "ends_at")
    list_filter = ("event__host_organization", "event__status")


@admin.register(InstructorAssignment)
class InstructorAssignmentAdmin(admin.ModelAdmin):
    list_display = ("instructor", "session", "role", "confirmed")
    list_filter = ("role", "confirmed", "session__event__host_organization")
    search_fields = ("instructor__first_name", "instructor__last_name", "session__event__course__name")


admin.site.register(CourseAuthorization)
admin.site.register(AvailabilityBlock)
admin.site.register(AssistanceRequest)
admin.site.register(AuditEvent)

# Register your models here.
