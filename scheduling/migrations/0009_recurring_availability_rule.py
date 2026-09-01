import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0008_seed_state_course_matrix"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecurringAvailabilityRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("available", "Available (preferred time)"), ("tentative", "Tentative"), ("unavailable", "Unavailable")], max_length=16)),
                ("weekdays", models.CharField(help_text="Comma-separated weekday numbers where Monday is 0.", max_length=20)),
                ("all_day", models.BooleanField(default=False)),
                ("start_time", models.TimeField(blank=True, null=True)),
                ("end_time", models.TimeField(blank=True, null=True)),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField(blank=True, null=True)),
                ("notes", models.CharField(blank=True, max_length=250)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("instructor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recurring_availability_rules", to="scheduling.instructor")),
            ],
            options={"ordering": ("starts_on", "start_time", "pk")},
        ),
    ]
