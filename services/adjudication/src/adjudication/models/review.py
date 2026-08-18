"""A licensed clinician's decision on an escalated case.

Separate from Determination, and deliberately not typed with `pramana_common.Outcome`:
a clinician may issue an adverse determination here (California SB 1120, the Medicare
Advantage rule), which is exactly what the system itself may never do -- see ADR-0002."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Review:
    id: int
    case_id: str
    #: auth's own primary key, recorded as a value -- no foreign key crosses a
    #: service boundary.
    clinician_id: str
    outcome: str
    rationale: str
    #: Whether the clinician's outcome matches what the system would have produced --
    #: the signal the eval harness reads to measure the gate against real judgment.
    agreed_with_system: bool
    created_at: datetime
