"""A diagnosed comorbidity, one of NCD 240.4's alternate qualifying paths at AHI 5-14."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Condition:
    id: int
    member_id: str
    #: SNOMED. Criteria match on codes, not prose.
    code: str
    description: str
    onset_date: date
