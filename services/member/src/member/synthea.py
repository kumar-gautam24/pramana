"""Parses Synthea's CSV export into member records.

Pure: this module is handed already-parsed CSV rows (e.g. from `csv.DictReader`) and
never opens a file itself, so it stays testable without a filesystem fixture and
reusable against rows sourced any other way.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SyntheaPatient:
    id: str
    birth_date: date
    sex: str


@dataclass(frozen=True)
class SyntheaCondition:
    patient_id: str
    code: str
    description: str
    onset_date: date


@dataclass(frozen=True)
class SyntheaEncounter:
    patient_id: str
    date: date
    description: str


def parse_patients(rows: Iterable[dict]) -> list[SyntheaPatient]:
    # `row["COLUMN"]` rather than `.get`: a missing column must raise, not silently
    # produce a patient who can never be adjudicated.
    return [
        SyntheaPatient(
            id=row["Id"],
            birth_date=date.fromisoformat(row["BIRTHDATE"]),
            sex=row["GENDER"],
        )
        for row in rows
    ]


def parse_conditions(rows: Iterable[dict]) -> list[SyntheaCondition]:
    return [
        SyntheaCondition(
            patient_id=row["PATIENT"],
            # Kept as a code, not just the DESCRIPTION prose: criteria match on SNOMED
            # codes, and matching on free text would be exactly the guessing this
            # system exists to avoid.
            code=row["CODE"],
            description=row["DESCRIPTION"],
            onset_date=date.fromisoformat(row["START"]),
        )
        for row in rows
    ]


def parse_encounters(rows: Iterable[dict]) -> list[SyntheaEncounter]:
    return [
        SyntheaEncounter(
            patient_id=row["PATIENT"],
            # Synthea writes encounter starts as timestamps (e.g. "2019-03-14T09:20:00Z").
            # Truncate to a date explicitly, rather than relying on a datetime happening
            # to compare equal to a date: every comparison in this system is date-based,
            # and a datetime would silently never match.
            date=date.fromisoformat(row["START"][:10]),
            description=row["DESCRIPTION"],
        )
        for row in rows
    ]
