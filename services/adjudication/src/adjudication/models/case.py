"""A prior-authorization request moving through the pipeline."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class RunMode(StrEnum):
    """Which arithmetic decided this case (ADR-0021).

    `DETERMINISTIC` is the system as designed: threshold, enum and temporal comparisons are
    performed in Python against facts fetched from `member` (ADR-0003, invariant 2).
    `MODEL_ARITHMETIC` is the ablation that argues for that design empirically -- the same
    pipeline, the same fetches, the same evidence, with the comparisons handed to the model
    so the error rate of doing it the wrong way can be published rather than asserted.

    A closed enum and a CHECK constraint (migrations/0005_cases_run_mode.sql) rather than a
    bool, because this is the column that says whether a determination came from the system
    or from the experiment that exists to argue against it, and "true"/"false" would not say
    which experiment."""

    DETERMINISTIC = "deterministic"
    MODEL_ARITHMETIC = "model_arithmetic"


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
    #: A caller-supplied key that makes a retried POST /cases return this same row
    #: instead of creating a second one (migrations/0003_cases_idempotency_key.sql,
    #: task-8 brief decision 1). None for every case with no idempotency concern of its
    #: own -- the column's UNIQUE constraint permits any number of NULLs.
    idempotency_key: str | None = None
    #: Which arithmetic verifies this case's deterministic criteria -- see `RunMode`. Read
    #: by `services/verify` and recorded on the `started` event, so the audit trail says
    #: which arm decided the case rather than leaving it to be inferred from the tools.
    #: Defaulted so every existing construction of a `Case` keeps working, and defaulted to
    #: the *shipped* behaviour rather than to the experiment.
    run_mode: RunMode = RunMode.DETERMINISTIC
