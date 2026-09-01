from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0005_expand_instructor_availability")]

    operations = [
        migrations.AddField(
            model_name="availabilityblock",
            name="all_day",
            field=models.BooleanField(default=False),
        ),
    ]
