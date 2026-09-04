from django.db import migrations
from django.db.models import Q


GLOBAL_ADMIN_EMAIL = "tom.zecher@gmail.com"


def restore_global_administrator(apps, schema_editor):
    User = apps.get_model("auth", "User")
    AccountProfile = apps.get_model("accounts", "AccountProfile")
    users = User.objects.filter(
        Q(email__iexact=GLOBAL_ADMIN_EMAIL)
        | Q(username__iexact=GLOBAL_ADMIN_EMAIL)
    )
    user_ids = list(users.values_list("pk", flat=True))
    users.update(is_active=True, is_staff=True, is_superuser=True)
    AccountProfile.objects.filter(user_id__in=user_ids).update(access_status="active")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0008_backfill_account_profiles"),
    ]

    operations = [
        migrations.RunPython(
            restore_global_administrator,
            migrations.RunPython.noop,
        ),
    ]
