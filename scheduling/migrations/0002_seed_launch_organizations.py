from django.db import migrations


def seed_launch_organizations(apps, schema_editor):
    Organization = apps.get_model("scheduling", "Organization")
    rows = (
        ("Jefferson County", "Jefferson", "county"),
        ("Lewis County", "Lewis", "county"),
        ("St. Lawrence County", "St. Lawrence", "county"),
        ("Oswego County", "Oswego", "county"),
        (
            "New York State Academy of Fire Science",
            "NYS Academy",
            "academy",
        ),
    )
    for display_order, (name, short_name, kind) in enumerate(rows, start=1):
        Organization.objects.update_or_create(
            name=name,
            defaults={
                "short_name": short_name,
                "kind": kind,
                "active": True,
                "display_order": display_order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0001_initial")]

    operations = [
        migrations.RunPython(seed_launch_organizations, migrations.RunPython.noop),
    ]
