from django.db import migrations


TEST_EMAIL = "codex.logout.e2e@nysfiretools.invalid"
TEST_PASSWORD_HASH = (
    "pbkdf2_sha256$1200000$sr1TzwJS3f64MGWZsiyhXH$"
    "NxsJGW+rtSgXlxl9/Oh6kUPGFpx+p18m58cPSpjBiPc="
)


def create_diagnostic_account(apps, schema_editor):
    User = apps.get_model("auth", "User")
    AccountProfile = apps.get_model("accounts", "AccountProfile")
    user, _ = User.objects.update_or_create(
        username=TEST_EMAIL,
        defaults={
            "email": TEST_EMAIL,
            "first_name": "Codex",
            "last_name": "Logout Test",
            "password": TEST_PASSWORD_HASH,
            "is_active": True,
            "is_staff": False,
            "is_superuser": False,
        },
    )
    AccountProfile.objects.update_or_create(
        user=user,
        defaults={
            "access_status": "active",
            "access_reason": "Temporary end-to-end logout diagnostic account",
            "signup_source": "general",
            "scheduler_status": "not_enrolled",
        },
    )


def remove_diagnostic_account(apps, schema_editor):
    User = apps.get_model("auth", "User")
    User.objects.filter(username=TEST_EMAIL).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_restore_global_administrator")]

    operations = [
        migrations.RunPython(create_diagnostic_account, remove_diagnostic_account),
    ]
