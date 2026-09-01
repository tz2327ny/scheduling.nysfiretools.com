from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0001_initial"),
        ("scheduling", "0008_seed_state_course_matrix"),
    ]

    operations = [
        migrations.CreateModel(
            name="InstructorApplication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("travel_preference", models.CharField(choices=[("contact", "Contact me as needed"), ("local", "Home organization only"), ("limited", "Limited travel")], max_length=16)),
                ("travel_notes", models.CharField(blank=True, max_length=250)),
                ("status", models.CharField(choices=[("pending", "Pending State approval"), ("approved", "Approved"), ("rejected", "Not approved")], default="pending", max_length=16)),
                ("applied_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("home_organization", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="instructor_applications", to="scheduling.organization")),
                ("instructor", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="registration_application", to="scheduling.instructor")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_instructor_applications", to=settings.AUTH_USER_MODEL)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="instructor_application", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-applied_at",)},
        ),
        migrations.AddField(
            model_name="instructorapplication",
            name="requested_courses",
            field=models.ManyToManyField(blank=True, related_name="instructor_applications", to="scheduling.course"),
        ),
    ]
