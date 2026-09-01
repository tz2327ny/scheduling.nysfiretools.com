from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0006_availability_all_day")]

    operations = [
        migrations.AddField(model_name="course", name="number_of_units", field=models.CharField(blank=True, max_length=30, verbose_name="Number of units")),
        migrations.AddField(model_name="course", name="student_contact_hours", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="course", name="instructor_requirements", field=models.TextField(blank=True, help_text="Primary and additional instructor requirements by unit.")),
        migrations.AddField(model_name="course", name="safety_officer_requirements", field=models.TextField(blank=True)),
        migrations.AddField(model_name="course", name="ems_requirements", field=models.TextField(blank=True, verbose_name="EMS requirements")),
        migrations.AddField(model_name="course", name="admin_time", field=models.TextField(blank=True)),
        migrations.AddField(model_name="course", name="county_hours_or_program_charge", field=models.TextField(blank=True)),
        migrations.AddField(model_name="course", name="completion_type", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="course", name="instructional_method", field=models.TextField(blank=True)),
        migrations.AddField(model_name="course", name="class_size", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="course", name="prerequisites", field=models.TextField(blank=True)),
        migrations.AddField(model_name="course", name="in_service_hours", field=models.CharField(blank=True, max_length=60, verbose_name="In-service hours / CEU credit")),
        migrations.AddField(model_name="course", name="national_certification", field=models.CharField(blank=True, max_length=60)),
        migrations.AddField(model_name="course", name="course_version", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="course", name="template_start_date", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="course", name="template_end_date", field=models.CharField(blank=True, max_length=40)),
        migrations.AddField(model_name="course", name="matrix_source", field=models.CharField(blank=True, max_length=120)),
    ]
