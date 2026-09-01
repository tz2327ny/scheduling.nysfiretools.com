from django.urls import path

from . import views


urlpatterns = [
    path("register/", views.instructor_register, name="instructor_register"),
    path("register/received/", views.registration_received, name="registration_received"),
    path("users/", views.user_list, name="user_list"),
    path("users/<int:pk>/edit/", views.user_edit, name="user_edit"),
    path("applications/<int:pk>/review/", views.application_review, name="application_review"),
    path("applications/<int:pk>/reject/", views.application_reject, name="application_reject"),
    path("authorizations/<int:pk>/approve/", views.authorization_approve, name="authorization_approve"),
    path("authorizations/<int:pk>/reject/", views.authorization_reject, name="authorization_reject"),
]
