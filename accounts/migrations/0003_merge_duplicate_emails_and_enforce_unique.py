from django.db import migrations


def merge_duplicate_email_accounts(apps, schema_editor):
    User = apps.get_model("auth", "User")
    InstructorApplication = apps.get_model("accounts", "InstructorApplication")
    UserOrganizationRole = apps.get_model("accounts", "UserOrganizationRole")
    Instructor = apps.get_model("scheduling", "Instructor")
    CourseAuthorization = apps.get_model("scheduling", "CourseAuthorization")
    TrainingEvent = apps.get_model("scheduling", "TrainingEvent")
    AssistanceRequest = apps.get_model("scheduling", "AssistanceRequest")
    AuditEvent = apps.get_model("scheduling", "AuditEvent")

    groups = {}
    for user in User.objects.exclude(email="").order_by("pk"):
        normalized = user.email.strip().lower()
        if normalized:
            groups.setdefault(normalized, []).append(user.pk)

    for email, user_ids in groups.items():
        users = list(User.objects.filter(pk__in=user_ids))
        users.sort(
            key=lambda user: (
                not user.is_superuser,
                not user.is_active,
                not Instructor.objects.filter(user_id=user.pk).exists(),
                not UserOrganizationRole.objects.filter(user_id=user.pk).exists(),
                not InstructorApplication.objects.filter(user_id=user.pk).exists(),
                user.pk,
            )
        )
        primary = users[0]
        for duplicate in users[1:]:
            primary.groups.add(*duplicate.groups.all())
            primary.user_permissions.add(*duplicate.user_permissions.all())

            for role in UserOrganizationRole.objects.filter(user_id=duplicate.pk):
                UserOrganizationRole.objects.get_or_create(
                    user_id=primary.pk,
                    organization_id=role.organization_id,
                    role=role.role,
                )
            UserOrganizationRole.objects.filter(user_id=duplicate.pk).delete()

            primary_application = InstructorApplication.objects.filter(
                user_id=primary.pk
            ).first()
            duplicate_application = InstructorApplication.objects.filter(
                user_id=duplicate.pk
            ).first()
            if duplicate_application and primary_application is None:
                duplicate_application.user_id = primary.pk
                duplicate_application.save(update_fields=("user",))
            elif duplicate_application and primary_application:
                primary_application.requested_courses.add(
                    *duplicate_application.requested_courses.all()
                )
                duplicate_application.delete()

            primary_instructor = Instructor.objects.filter(user_id=primary.pk).first()
            duplicate_instructor = Instructor.objects.filter(user_id=duplicate.pk).first()
            if duplicate_instructor and primary_instructor is None:
                duplicate_instructor.user_id = primary.pk
                duplicate_instructor.save(update_fields=("user",))
            elif duplicate_instructor:
                duplicate_instructor.user_id = None
                duplicate_instructor.save(update_fields=("user",))

            CourseAuthorization.objects.filter(verified_by_id=duplicate.pk).update(
                verified_by_id=primary.pk
            )
            TrainingEvent.objects.filter(created_by_id=duplicate.pk).update(
                created_by_id=primary.pk
            )
            AssistanceRequest.objects.filter(created_by_id=duplicate.pk).update(
                created_by_id=primary.pk
            )
            AuditEvent.objects.filter(actor_id=duplicate.pk).update(actor_id=primary.pk)
            InstructorApplication.objects.filter(reviewed_by_id=duplicate.pk).update(
                reviewed_by_id=primary.pk
            )
            duplicate.delete()

        primary.email = email
        if not User.objects.exclude(pk=primary.pk).filter(username=email).exists():
            primary.username = email
        primary.save(update_fields=("email", "username"))


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("accounts", "0002_instructor_application"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("scheduling", "0010_course_units_and_staffing"),
    ]

    operations = [
        migrations.RunPython(merge_duplicate_email_accounts, migrations.RunPython.noop),
        migrations.RunSQL(
            sql=(
                "CREATE UNIQUE INDEX auth_user_email_ci_unique "
                "ON auth_user (LOWER(email)) WHERE email <> ''"
            ),
            reverse_sql="DROP INDEX IF EXISTS auth_user_email_ci_unique",
        ),
    ]
