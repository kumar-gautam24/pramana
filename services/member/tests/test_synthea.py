import csv
from datetime import date
from pathlib import Path

import pytest

from member.domain.synthea import parse_conditions, parse_encounters, parse_patients

FIXTURES = Path(__file__).parent / "fixtures" / "synthea"


def rows(name: str) -> list[dict]:
    with (FIXTURES / name).open() as handle:
        return list(csv.DictReader(handle))


def test_parses_patients():
    patients = parse_patients(rows("patients.csv"))

    assert len(patients) == 5
    assert all(isinstance(p.birth_date, date) for p in patients)


def test_encounter_timestamps_are_truncated_to_a_date():
    """Synthea writes encounter starts with a time component. Comparisons in this system
    are date-based, so a datetime would silently never equal a date."""
    encounters = parse_encounters(rows("encounters.csv"))

    assert all(isinstance(e.date, date) and not hasattr(e.date, "hour") for e in encounters)


def test_conditions_keep_their_snomed_code():
    """Criteria match on codes, not on prose. Dropping the code would force downstream
    matching on free text, which is exactly the guessing this system avoids."""
    conditions = parse_conditions(rows("conditions.csv"))

    assert all(c.code for c in conditions)


def test_a_row_missing_a_required_column_raises():
    """A silently skipped patient is a member who cannot be adjudicated and whose absence
    nothing reports."""
    with pytest.raises(KeyError):
        parse_patients([{"Id": "x"}])
