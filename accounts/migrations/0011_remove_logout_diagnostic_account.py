from django.db import migrations


TEST_EMAIL = "codex.logout.e2e@nysfiretools.invalid"


def remove_diagnostic_account(apps, schema_editor):
    User = apps.get_model("auth", "User")
    User.objects.filter(username=TEST_EMAIL).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_create_logout_diagnostic_account")]

    operations = [
        migrations.RunPython(remove_diagnostic_account, migrations.RunPython.noop),
    ]
