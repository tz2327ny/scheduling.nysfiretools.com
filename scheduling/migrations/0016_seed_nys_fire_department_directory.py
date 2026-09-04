import json
from pathlib import Path

from django.db import migrations


SOURCE = "NY Open Data qfsu-zcpv"


def normalize_name(value):
    return "".join(character for character in (value or "").casefold() if character.isalnum())


def preferred_record(current, candidate):
    current_matches = str(current.get("fdid_code", "")).startswith(str(current.get("county_code", "")))
    candidate_matches = str(candidate.get("fdid_code", "")).startswith(str(candidate.get("county_code", "")))
    if candidate_matches != current_matches:
        return candidate if candidate_matches else current
    return min((current, candidate), key=lambda item: str(item.get("fdid_code", "")))


def seed_directory(apps, schema_editor):
    Organization = apps.get_model("scheduling", "Organization")
    Organization.objects.update_or_create(
        kind="state",
        county_name="",
        normalized_name=normalize_name("New York State Office of Fire Prevention and Control"),
        defaults={
            "name": "New York State Office of Fire Prevention and Control",
            "short_name": "NYS OFPC",
            "lifecycle_status": "active",
            "active": True,
            "display_order": 0,
        },
    )
    data_path = Path(__file__).resolve().parents[1] / "data" / "nys_fire_departments.json"
    records = json.loads(data_path.read_text(encoding="utf-8"))
    deduplicated = {}
    for record in records:
        county = str(record.get("countyname", "")).strip()
        name = str(record.get("fire_department_name", "")).strip()
        if not county or not name:
            continue
        key = (county.casefold(), normalize_name(name))
        if key in deduplicated:
            deduplicated[key] = preferred_record(deduplicated[key], record)
        else:
            deduplicated[key] = record

    county_parents = {
        organization.county_name.casefold(): organization
        for organization in Organization.objects.filter(kind="county")
    }
    agencies = []
    for record in deduplicated.values():
        county = str(record["countyname"]).strip()
        name = str(record["fire_department_name"]).strip().title()
        parent = county_parents.get(county.casefold())
        agencies.append(
            Organization(
                name=name,
                short_name=name[:50],
                kind="agency",
                normalized_name=normalize_name(name),
                parent_id=parent.pk if parent else None,
                county_name=county,
                fdid_code=str(record.get("fdid_code", "")).strip().upper(),
                address=str(record.get("address", "")).strip().title(),
                city=str(record.get("city", "")).strip().title(),
                state=str(record.get("st", "NY")).strip().upper() or "NY",
                zip_code=str(record.get("zip_code", "")).strip(),
                phone_number=str(record.get("phone_number", "")).strip(),
                lifecycle_status="active",
                directory_source=SOURCE,
                active=True,
                display_order=100,
            )
        )
    Organization.objects.bulk_create(agencies, ignore_conflicts=True, batch_size=250)


def remove_seeded_directory(apps, schema_editor):
    Organization = apps.get_model("scheduling", "Organization")
    Organization.objects.filter(directory_source=SOURCE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0015_organizationalias_alter_organization_options_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_directory, remove_seeded_directory),
    ]
