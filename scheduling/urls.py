from django.urls import path

from . import views


urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.dashboard, name="dashboard"),
    path("schedule/", views.schedule, name="schedule"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/new/", views.course_create, name="course_create"),
    path("courses/<int:pk>/edit/", views.course_edit, name="course_edit"),
    path("instructors/", views.instructor_list, name="instructor_list"),
    path("instructors/new/", views.instructor_create, name="instructor_create"),
    path("instructors/<int:pk>/edit/", views.instructor_edit, name="instructor_edit"),
    path("training/new/", views.training_create, name="training_create"),
    path("training/<int:pk>/", views.training_detail, name="training_detail"),
    path("training/<int:pk>/edit/", views.training_edit, name="training_edit"),
]
