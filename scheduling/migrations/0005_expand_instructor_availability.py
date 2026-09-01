from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0004_course_record_and_offering_numbers")]

    operations = [
        migrations.AlterField(
            model_name="availabilityblock",
            name="status",
            field=models.CharField(
                choices=[
                    ("available", "Available (preferred time)"),
                    ("tentative", "Tentative"),
                    ("unavailable", "Unavailable"),
                ],
                max_length=16,
            ),
        ),
    ]
