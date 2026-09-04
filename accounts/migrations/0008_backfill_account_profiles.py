from django.db import migrations


def backfill_account_profiles(apps, schema_editor):
    User = apps.get_model("auth", "User")
    AccountProfile = apps.get_model("accounts", "AccountProfile")
    Instructor = apps.get_model("scheduling", "Instructor")
    InstructorApplication = apps.get_model("accounts", "InstructorApplication")

    instructors = {
        instructor.user_id: instructor
        for instructor in Instructor.objects.exclude(user_id=None)
    }
    applications = {
        application.user_id: application
        for application in InstructorApplication.objects.all()
    }
    profiles = []
    for user in User.objects.all():
        instructor = instructors.get(user.pk)
        application = applications.get(user.pk)
        if instructor:
            scheduler_status = "active"
            organization_id = instructor.home_organization_id
        elif application:
            scheduler_status = {
                "pending": "pending",
                "approved": "active",
                "rejected": "rejected",
            }.get(application.status, "pending")
            organization_id = application.home_organization_id
        else:
            scheduler_status = "not_enrolled"
            organization_id = None
        profiles.append(
            AccountProfile(
                user_id=user.pk,
                access_status="active" if user.is_active else "pending",
                signup_source="legacy",
                scheduler_status=scheduler_status,
                organization_id=organization_id,
            )
        )
    AccountProfile.objects.bulk_create(profiles, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0007_accountprofile"),
    ]

    operations = [
        migrations.RunPython(backfill_account_profiles, migrations.RunPython.noop),
    ]
