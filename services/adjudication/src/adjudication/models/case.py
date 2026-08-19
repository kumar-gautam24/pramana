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
    #: The clinical narrative a real prior-authorization submission carries, if any --
    #: retrieval input for the policy search (services/pipeline.py falls back to the
    #: codes when this is None), never a second source of truth about what was
    #: requested (migrations/0002_cases_request_text.sql). Defaulted so every existing
    #: construction of a `Case` -- in tests and elsewhere -- keeps working unchanged.
    request_text: str | None = None
