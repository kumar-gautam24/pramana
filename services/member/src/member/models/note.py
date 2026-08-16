"""A clinical note -- what the judgment criteria read."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Note:
    id: int
    member_id: str
    #: None: not every note is tied to a specific encounter.
    encounter_id: int | None
    date: date
    text: str
