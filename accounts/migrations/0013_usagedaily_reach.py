from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_usagedaily")]
    operations = [
        migrations.AddField(
            model_name="usagedaily",
            name="reach",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
