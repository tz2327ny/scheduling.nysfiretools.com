from django.urls import path

from . import views


urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.dashboard, name="dashboard"),
    path("schedule/", views.schedule, name="schedule"),
    path("organizations/", views.organization_list, name="organization_list"),
    path("organizations/new/", views.organization_create, name="organization_create"),
    path("organizations/<int:pk>/edit/", views.organization_edit, name="organization_edit"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/new/", views.course_create, name="course_create"),
    path("courses/<int:pk>/edit/", views.course_edit, name="course_edit"),
    path("instructors/", views.instructor_list, name="instructor_list"),
    path("instructors/new/", views.instructor_create, name="instructor_create"),
    path("instructors/<int:pk>/edit/", views.instructor_edit, name="instructor_edit"),
    path("instructors/<int:pk>/delete/", views.instructor_delete, name="instructor_delete"),
    path(
        "instructors/<int:pk>/authorizations/",
        views.instructor_authorizations,
        name="instructor_authorizations",
    ),
    path(
        "instructors/<int:pk>/notifications/",
        views.instructor_notifications,
        name="instructor_notifications",
    ),
    path(
        "instructors/<int:pk>/availability/",
        views.instructor_availability,
        name="instructor_availability",
    ),
    path(
        "instructors/<int:pk>/availability/new/",
        views.availability_create,
        name="availability_create",
    ),
    path(
        "instructors/<int:pk>/availability/weekly/new/",
        views.recurring_availability_create,
        name="recurring_availability_create",
    ),
    path(
        "instructors/<int:pk>/availability/weekly/<int:rule_pk>/edit/",
        views.recurring_availability_edit,
        name="recurring_availability_edit",
    ),
    path(
        "instructors/<int:pk>/availability/weekly/<int:rule_pk>/delete/",
        views.recurring_availability_delete,
        name="recurring_availability_delete",
    ),
    path(
        "instructors/<int:pk>/availability/<int:entry_pk>/edit/",
        views.availability_edit,
        name="availability_edit",
    ),
    path(
        "instructors/<int:pk>/availability/<int:entry_pk>/delete/",
        views.availability_delete,
        name="availability_delete",
    ),
    path("training/new/", views.training_create, name="training_create"),
    path("training/<int:pk>/", views.training_detail, name="training_detail"),
    path("training/<int:pk>/edit/", views.training_edit, name="training_edit"),
    path(
        "training/<int:pk>/units/<int:session_pk>/assign/",
        views.session_assignment_add,
        name="session_assignment_add",
    ),
    path(
        "training/<int:pk>/units/<int:session_pk>/assignments/<int:assignment_pk>/remove/",
        views.session_assignment_remove,
        name="session_assignment_remove",
    ),
]
