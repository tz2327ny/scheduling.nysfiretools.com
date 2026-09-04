from django import forms


class OrganizationOptionMixin:
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name,
            value,
            label,
            selected,
            index,
            subindex=subindex,
            attrs=attrs,
        )
        organization = getattr(value, "instance", None)
        if organization is not None:
            option["attrs"]["data-county"] = organization.county_name
            option["attrs"]["data-kind"] = organization.kind
            option["attrs"]["data-search"] = organization.fdid_code
        return option

    def __init__(self, attrs=None, choices=()):
        attrs = {
            **(attrs or {}),
            "class": f"{(attrs or {}).get('class', '')} organization-picker-select".strip(),
            "data-organization-picker": "",
        }
        super().__init__(attrs=attrs, choices=choices)


class OrganizationSelect(OrganizationOptionMixin, forms.Select):
    pass


class OrganizationSelectMultiple(OrganizationOptionMixin, forms.SelectMultiple):
    pass
