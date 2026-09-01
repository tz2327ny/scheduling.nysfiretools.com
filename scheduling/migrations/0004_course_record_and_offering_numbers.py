from django.db import migrations, models


def return_unidentified_trainings_to_purposed(apps, schema_editor):
    TrainingEvent = apps.get_model("scheduling", "TrainingEvent")
    TrainingEvent.objects.filter(
        status__in=("confirmed", "completed"),
        offering_number__isnull=True,
    ).update(status="proposed")


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0003_simplify_training_statuses")]

    operations = [
        migrations.RenameField(
            model_name="course",
            old_name="code",
            new_name="record_number",
        ),
        migrations.AlterField(
            model_name="course",
            name="record_number",
            field=models.CharField(
                max_length=30,
                unique=True,
                verbose_name="Course record number",
            ),
        ),
        migrations.AddField(
            model_name="trainingevent",
            name="offering_number",
            field=models.CharField(
                blank=True,
                help_text="Optional while Purposed; required before the training can be Confirmed.",
                max_length=30,
                null=True,
                unique=True,
                verbose_name="Course offering number",
            ),
        ),
        migrations.RunPython(
            return_unidentified_trainings_to_purposed,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="trainingevent",
            constraint=models.CheckConstraint(
                condition=(
                    ~models.Q(status__in=("confirmed", "completed"))
                    | (
                        models.Q(offering_number__isnull=False)
                        & ~models.Q(offering_number="")
                    )
                ),
                name="confirmed_training_requires_offering_number",
            ),
        ),
    ]
