"""A prior-authorization request moving through the pipeline."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Case:
    id: str
    #: member's own primary key, recorded as a value -- no foreign key crosses a
    #: service boundary.
    member_id: str
    #: CPT code, identifier only. Never paired with its description -- see ADR-0004.
    requested_code: str
    icd10: str
    date_of_service: date
    #: 'initial' or 'continuation'.
    kind: str
    #: Pipeline progress only ('queued', 'running', 'decided', 'failed'). The outcome
    #: lives on Determination and is never mirrored here.
    status: str
    created_at: datetime
