from django.contrib import admin

from .models import AccountProfile, InstructorApplication, UserOrganizationRole


@admin.register(AccountProfile)
class AccountProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "access_status", "scheduler_status", "organization", "signup_source")
    list_filter = ("access_status", "scheduler_status", "signup_source")
    search_fields = ("user__email", "user__first_name", "user__last_name", "requested_organization_name")


@admin.register(UserOrganizationRole)
class UserOrganizationRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role")
    list_filter = ("role", "organization")
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(InstructorApplication)
class InstructorApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "sfi_number", "home_organization", "status", "applied_at", "reviewed_by")
    list_filter = ("status", "home_organization")
    search_fields = ("user__first_name", "user__last_name", "user__email", "sfi_number")

# Register your models here.
