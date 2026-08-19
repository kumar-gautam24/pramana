"""Verifying one criterion against a member's record -- ADR-0003's line runs through
this package. `deterministic.py` performs `threshold`/`enum`/`temporal` comparisons as
plain Python `if`s; `judgment.py` asks a model for `judgment` criteria and validates
its answer before trusting it. Neither ever asks a model to compare a date or a
number, and no comparison in either submodule is delegated back to a model.

`Verification` is deliberately not `pramana_common.criteria.CriterionResult` widened.
That type is the coupling point the gate and the eval harness both consume, and this
service's own `tool`/`evidence` columns (see `models/criterion_result.py`) have no
reason to be visible there. `verify()` is the single entry point task 7 calls; it
dispatches on `criterion.type` and returns this local, richer shape. Converting
`Verification.result` into the wire `CriterionResult` the gate consumes is already
free -- `.result` *is* that type.

Cross-task invariant, load-bearing (see task-6 brief, decision 4): `member` returns an
empty list rather than a 404 from its four non-coverage endpoints, so for an *unknown*
member `conditions()`, `sleep_studies()` and `notes()` would all come back `[]` --
indistinguishable, to this package, from a real member who simply has none. Every
verifier below therefore assumes the member already exists. That is only safe because
the pipeline calls `MemberClient.coverage` and short-circuits the case on
`CoverageStatus.NO_RECORD` **before any verifier runs** (task 7 owns that ordering). If
that eligibility check is ever moved after verification, every "member has no X" row
in `deterministic.py`'s table starts silently producing denial-shaped answers
(`NOT_MET`) about people the system has no record of."""

from dataclasses import dataclass
from typing import Any

from pramana_common.criteria import DETERMINISTIC_TYPES, CriterionResult

from adjudication.models.case import Case
from adjudication.models.criterion import Criterion
from adjudication.services.llm import LLMProvider
from adjudication.services.member_client import MemberClient


@dataclass(frozen=True)
class Verification:
    #: The wire shape the gate and eval harness consume -- see this module's
    #: docstring for why it is carried whole rather than re-derived.
    result: CriterionResult
    #: Which deterministic tool, or which model call, produced `result`. Free text,
    #: matching `models/criterion_result.py.tool`.
    tool: str
    #: Enough for a reviewer to check the machine's arithmetic (or the model's
    #: citation) without opening the member service themselves. Stored as `jsonb`.
    evidence: dict[str, Any]


async def verify(
    criterion: Criterion, case: Case, member_client: MemberClient, llm: LLMProvider
) -> Verification:
    """Verify one already-validated criterion. `threshold`/`enum`/`temporal` never
    touch `llm`; `judgment` never receives a bare fact value to compare -- see the two
    submodules' own docstrings for what each does and why."""
    # Imported inside the function, not at module level: both submodules import
    # `Verification` from this package, so importing them here at module scope would
    # be circular. By the time `verify()` is actually called, this package has already
    # finished initialising, so the import below is safe.
    from adjudication.services.verify import deterministic, judgment

    if criterion.type in DETERMINISTIC_TYPES:
        return await deterministic.verify(criterion, case, member_client)
    return await judgment.verify(criterion, case, member_client, llm)
