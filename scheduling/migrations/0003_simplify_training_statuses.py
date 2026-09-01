from django.db import migrations, models


def convert_tentative_events(apps, schema_editor):
    TrainingEvent = apps.get_model("scheduling", "TrainingEvent")
    TrainingEvent.objects.filter(status="tentative").update(status="proposed")


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0002_seed_launch_organizations")]

    operations = [
        migrations.RunPython(convert_tentative_events, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="trainingevent",
            name="status",
            field=models.CharField(
                choices=[
                    ("proposed", "Purposed"),
                    ("confirmed", "Confirmed"),
                    ("completed", "Completed"),
                    ("canceled", "Cancelled"),
                ],
                default="proposed",
                max_length=16,
            ),
        ),
    ]
