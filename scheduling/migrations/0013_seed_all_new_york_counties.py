from django.db import migrations


NEW_YORK_COUNTIES = (
    "Albany",
    "Allegany",
    "Bronx",
    "Broome",
    "Cattaraugus",
    "Cayuga",
    "Chautauqua",
    "Chemung",
    "Chenango",
    "Clinton",
    "Columbia",
    "Cortland",
    "Delaware",
    "Dutchess",
    "Erie",
    "Essex",
    "Franklin",
    "Fulton",
    "Genesee",
    "Greene",
    "Hamilton",
    "Herkimer",
    "Jefferson",
    "Kings",
    "Lewis",
    "Livingston",
    "Madison",
    "Monroe",
    "Montgomery",
    "Nassau",
    "New York",
    "Niagara",
    "Oneida",
    "Onondaga",
    "Ontario",
    "Orange",
    "Orleans",
    "Oswego",
    "Otsego",
    "Putnam",
    "Queens",
    "Rensselaer",
    "Richmond",
    "Rockland",
    "St. Lawrence",
    "Saratoga",
    "Schenectady",
    "Schoharie",
    "Schuyler",
    "Seneca",
    "Steuben",
    "Suffolk",
    "Sullivan",
    "Tioga",
    "Tompkins",
    "Ulster",
    "Warren",
    "Washington",
    "Wayne",
    "Westchester",
    "Wyoming",
    "Yates",
)


def seed_all_new_york_counties(apps, schema_editor):
    Organization = apps.get_model("scheduling", "Organization")
    for display_order, county in enumerate(NEW_YORK_COUNTIES, start=1):
        Organization.objects.update_or_create(
            name=f"{county} County",
            defaults={
                "short_name": county,
                "kind": "county",
                "active": True,
                "display_order": display_order,
            },
        )

    Organization.objects.update_or_create(
        name="New York State Academy of Fire Science",
        defaults={
            "short_name": "NYS Academy",
            "kind": "academy",
            "active": True,
            "display_order": len(NEW_YORK_COUNTIES) + 1,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("scheduling", "0012_notificationdelivery_notificationpreference")]

    operations = [
        migrations.RunPython(
            seed_all_new_york_counties,
            migrations.RunPython.noop,
        ),
    ]
