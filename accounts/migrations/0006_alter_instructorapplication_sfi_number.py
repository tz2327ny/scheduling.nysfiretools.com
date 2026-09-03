from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0005_externalaccesscode")]

    operations = [
        migrations.AlterField(
            model_name="instructorapplication",
            name="sfi_number",
            field=models.CharField(blank=True, max_length=30, verbose_name="SFI, CFI, or MFI number"),
        ),
    ]
