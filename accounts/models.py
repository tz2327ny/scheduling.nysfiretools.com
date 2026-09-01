from django.conf import settings
from django.db import models


class UserOrganizationRole(models.Model):
    """Scopes an administrator to the organization they are allowed to manage."""

    class Role(models.TextChoices):
        ADMINISTRATOR = "administrator", "Organization administrator"
        VIEWER = "viewer", "Read-only viewer"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organization_roles",
    )
    organization = models.ForeignKey(
        "scheduling.Organization",
        on_delete=models.CASCADE,
        related_name="user_roles",
    )
    role = models.CharField(max_length=24, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "organization", "role"),
                name="unique_user_organization_role",
            )
        ]
        ordering = ("organization__name", "user__last_name", "user__first_name")

    def __str__(self):
        return f"{self.user} — {self.organization} ({self.get_role_display()})"

# Create your models here.
