import re


ORDINAL_VALUES = {
    "1st": 1,
    "2nd": 2,
    "3rd": 3,
    "4th": 4,
    "5th": 5,
    "6th": 6,
}


def parse_unit_count(value):
    normalized = (value or "").strip().rstrip("`")
    if not re.fullmatch(r"\d+(?:\.0+)?", normalized):
        return None
    count = int(float(normalized))
    return count if count > 0 else None


def _expand_unit_numbers(text, unit_count):
    normalized = re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))
    unit_numbers = set()
    range_pattern = re.compile(r"(?<!\d)(\d+)\s*(?:-|thru|through|to)\s*(\d+)(?!\d)", re.I)
    for match in range_pattern.finditer(normalized):
        start, end = (int(match.group(1)), int(match.group(2)))
        if start > end:
            start, end = end, start
        unit_numbers.update(range(start, end + 1))
    normalized = range_pattern.sub(" ", normalized)
    unit_numbers.update(int(value) for value in re.findall(r"\d+", normalized))
    return {number for number in unit_numbers if 1 <= number <= unit_count}


def parse_unit_staffing(unit_count, instructor_requirements, safety_requirements=""):
    staffing = {
        number: {"required_instructors": 1, "requires_safety_officer": False}
        for number in range(1, unit_count + 1)
    }
    requirements = re.sub(r"\s+", " ", instructor_requirements or "").strip()
    ordinal = r"(?:SFI\s*)?(?:1st|2nd|3rd|4th|5th|6th)"
    rank_group = rf"{ordinal}(?:\s*(?:&|,|and)\s*{ordinal})*"
    clause_pattern = re.compile(
        rf"(?P<ranks>{rank_group})\s*-\s*Units?\s*(?P<units>.*?)(?={rank_group}\s*-\s*Units?|$)",
        re.I,
    )
    explicit_clauses = list(clause_pattern.finditer(requirements))
    for clause in explicit_clauses:
        ranks = [
            ORDINAL_VALUES[value.lower()]
            for value in re.findall(r"1st|2nd|3rd|4th|5th|6th", clause.group("ranks"), re.I)
        ]
        if not ranks:
            continue
        required = max(ranks)
        for unit_number in _expand_unit_numbers(clause.group("units"), unit_count):
            staffing[unit_number]["required_instructors"] = max(
                staffing[unit_number]["required_instructors"],
                required,
            )
    if not explicit_clauses:
        ranks = [
            ORDINAL_VALUES[value.lower()]
            for value in re.findall(r"1st|2nd|3rd|4th|5th|6th", requirements, re.I)
        ]
        if ranks:
            required = max(ranks)
            for unit in staffing.values():
                unit["required_instructors"] = required

    safety = re.sub(r"\s+", " ", safety_requirements or "").strip()
    if safety and safety.upper() not in {"N/A", "NA", "NONE"}:
        if re.search(r"all\s+units", safety, re.I):
            safety_units = set(staffing)
        elif re.search(r"units?", safety, re.I):
            unit_text = re.split(r"units?", safety, maxsplit=1, flags=re.I)[-1]
            safety_units = _expand_unit_numbers(unit_text, unit_count)
        else:
            safety_units = set()
        for unit_number in safety_units:
            staffing[unit_number]["requires_safety_officer"] = True
    return staffing
