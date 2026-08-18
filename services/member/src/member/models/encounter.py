"""A clinical visit. Notes may reference one, but this service has no query that reads
encounters directly -- they exist so a note can be tied to the visit that produced it."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Encounter:
    id: int
    member_id: str
    date: date
    description: str
