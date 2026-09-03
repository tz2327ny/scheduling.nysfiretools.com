from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0013_seed_all_new_york_counties")]

    operations = [
        migrations.AlterField(
            model_name="instructor",
            name="sfi_number",
            field=models.CharField(blank=True, db_index=True, max_length=30, verbose_name="SFI, CFI, or MFI number"),
        ),
    ]
