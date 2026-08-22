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
    #: One of approve/deny/pend -- ADR-0019, enforced by `reviews_outcome_check`. Typed
    #: `str` rather than a Literal because this is the row as read back, and a value the
    #: database holds is a fact about the record whatever this process believes the
    #: vocabulary to be; `routers/cases.py::ReviewOutcome` is where the set is enforced on
    #: the way in.
    outcome: str
    rationale: str
    #: Whether the clinician's outcome matches what the system would have produced --
    #: the signal the eval harness reads to measure the gate against real judgment.
    agreed_with_system: bool
    created_at: datetime
