from django.contrib import admin

from .models import UserOrganizationRole


@admin.register(UserOrganizationRole)
class UserOrganizationRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role")
    list_filter = ("role", "organization")
    search_fields = ("user__username", "user__first_name", "user__last_name")

# Register your models here.
