from django.contrib import admin

from .models import InstructorApplication, UserOrganizationRole


@admin.register(UserOrganizationRole)
class UserOrganizationRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role")
    list_filter = ("role", "organization")
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(InstructorApplication)
class InstructorApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "home_organization", "status", "applied_at", "reviewed_by")
    list_filter = ("status", "home_organization")
    search_fields = ("user__first_name", "user__last_name", "user__email")

# Register your models here.
