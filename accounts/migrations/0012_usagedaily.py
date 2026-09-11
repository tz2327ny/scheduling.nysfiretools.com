from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0011_remove_logout_diagnostic_account")]

    operations = [
        migrations.CreateModel(
            name="UsageDaily",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("day", models.DateField()),
                ("tool", models.CharField(max_length=32)),
                ("views", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "ordering": ("day", "tool"),
                "constraints": [models.UniqueConstraint(fields=("day", "tool"), name="unique_usage_day_tool")],
            },
        ),
    ]
