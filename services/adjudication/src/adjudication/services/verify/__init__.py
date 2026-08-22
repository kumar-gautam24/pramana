"""Verifying one criterion against a member's record -- ADR-0003's line runs through
this package. `deterministic.py` fetches the facts for `threshold`/`enum`/`temporal`
criteria and compares them through an injected `Arithmetic`; `judgment.py` asks a model
for `judgment` criteria and validates its answer before trusting it.

In every ordinary run the `Arithmetic` is `PythonArithmetic`, so no comparison of a date
or a number is delegated to a model -- which is the invariant, not an implementation
detail. The single exception is a case whose `run_mode` is `model_arithmetic`: the
ablation ADR-0003 promises, which exists to measure what the invariant is worth (ADR-0021).
`_arithmetic` below is the only place that choice is made, and `arithmetic.py` is the only
place the two arms differ.

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

import asyncio
from dataclasses import dataclass
from typing import Any

from pramana_common.criteria import DETERMINISTIC_TYPES, CriterionResult, Verdict

from adjudication.models.case import Case, RunMode
from adjudication.models.criterion import Criterion
from adjudication.services.llm import LLMProvider
from adjudication.services.member_client import MemberClient

# Safe at module scope, unlike the two verifier submodules: `arithmetic` imports nothing
# from this package, so there is no cycle to break.
from adjudication.services.verify.arithmetic import (
    PYTHON_ARITHMETIC,
    Arithmetic,
    ArithmeticUnusable,
    ModelArithmetic,
)


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


def _arithmetic(case: Case, llm: LLMProvider) -> Arithmetic:
    """Who performs this case's comparisons -- the whole of what `run_mode` controls.

    One function, one branch, and nothing else in the pipeline reads `run_mode`: that is
    what makes an ablated run and its twin differ in exactly one thing rather than in
    however many places happened to check a flag (ADR-0021)."""
    if case.run_mode is RunMode.MODEL_ARITHMETIC:
        return ModelArithmetic(llm)
    return PYTHON_ARITHMETIC


def _unusable_comparison(criterion: Criterion, exc: ArithmeticUnusable) -> Verification:
    """A model that was asked a comparison and did not answer one.

    Reachable only in the ablated arm. Resolved to `INSUFFICIENT_EVIDENCE` rather than to
    `NOT_MET`, the same way `judgment.py` resolves a verdict it cannot parse: a model that
    answered wrongly belongs in the ablation's error rate, and a model that did not answer
    is a different finding that must not be counted as strictness."""
    return Verification(
        result=CriterionResult(
            criterion_id=str(criterion.id),
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            confidence=1.0,
        ),
        tool="model_arithmetic:unusable",
        evidence={
            "reason": "the model did not answer the comparison it was asked",
            "question": exc.question,
            "raw_response": exc.raw,
        },
    )


async def verify(
    criterion: Criterion,
    case: Case,
    member_client: MemberClient,
    llm: LLMProvider,
    arithmetic: Arithmetic | None = None,
) -> Verification:
    """Verify one already-validated criterion. `threshold`/`enum`/`temporal` reach `llm`
    only through the ablation's `Arithmetic`; `judgment` never receives a bare fact value
    to compare -- see the two submodules' own docstrings for what each does and why.

    `arithmetic` is normally supplied by `verify_all`, which builds **one per case**: the
    ablated implementation serialises its own model calls, and a fresh instance per criterion
    would give each its own lock and serialise nothing. Defaulted for a caller verifying a
    single criterion on its own, where there is nothing to serialise against."""
    # Imported inside the function, not at module level: both submodules import
    # `Verification` from this package, so importing them here at module scope would
    # be circular. By the time `verify()` is actually called, this package has already
    # finished initialising, so the import below is safe.
    from adjudication.services.verify import deterministic, judgment

    if criterion.type in DETERMINISTIC_TYPES:
        try:
            return await deterministic.verify(
                criterion, case, member_client, arithmetic or _arithmetic(case, llm)
            )
        except ArithmeticUnusable as exc:
            # Caught here rather than inside `deterministic.py` so that module keeps no
            # branch for a failure only one arm can produce -- the shipped arm's code path
            # is byte-for-byte what it was before the ablation existed.
            return _unusable_comparison(criterion, exc)
    return await judgment.verify(criterion, case, member_client, llm)


async def verify_all(
    criteria: list[Criterion], case: Case, member_client: MemberClient, llm: LLMProvider
) -> list[Verification]:
    """Verify every criterion of a case, returned in the order given.

    Deterministic criteria stay one call each -- they are cheap SQL-shaped questions to
    `member` and there is nothing to save by combining them. (In an ablated run each also
    costs one model call per comparison; `ModelArithmetic` serialises those itself rather
    than letting this gather fire them all at once -- see its own comment.) Judgment
    criteria are sent
    to the model as a single batch, because each one otherwise re-sent the member's whole
    chart: a real case with seven judgment criteria spent seven model calls reading the
    same three sentences, which alone exceeded a rate-limited token budget and escalated
    the case for a reason that had nothing to do with the member.

    Each criterion is still judged and grounded independently -- see
    `judgment.verify_many`. This is a change in round trips, not in isolation.

    Exceptions propagate: the pipeline gathers these with `return_exceptions=True` and
    decides what an `UpstreamUnavailable` means for the case as a whole.
    """
    from adjudication.services.verify import judgment

    judgment_indices = [i for i, c in enumerate(criteria) if c.type not in DETERMINISTIC_TYPES]
    judgment_criteria = [criteria[i] for i in judgment_indices]

    # One instance for the whole case, not one per criterion. `ModelArithmetic` holds the
    # lock that keeps an ablated case from firing every comparison at the provider at once,
    # and a lock nobody else holds is not a lock.
    arithmetic = _arithmetic(case, llm)
    deterministic_calls = [
        verify(c, case, member_client, llm, arithmetic)
        for c in criteria
        if c.type in DETERMINISTIC_TYPES
    ]
    batched = judgment.verify_many(judgment_criteria, case, member_client, llm)

    # One gather over both, so a slow model call and the member-service calls overlap
    # rather than queueing behind each other.
    settled = await asyncio.gather(*deterministic_calls, batched, return_exceptions=True)
    *deterministic_results, judgment_results = settled

    # A failure inside the batch is a failure for every criterion it carried: the
    # exception is returned once by gather but the pipeline needs one entry per
    # criterion, and handing back fewer would misalign every index after it.
    if isinstance(judgment_results, BaseException):
        judgment_results = [judgment_results] * len(judgment_criteria)

    is_judgment = set(judgment_indices)
    deterministic_iter = iter(deterministic_results)
    judgment_iter = iter(judgment_results)
    merged: list[object] = [
        next(judgment_iter) if i in is_judgment else next(deterministic_iter)
        for i in range(len(criteria))
    ]
    return merged  # type: ignore[return-value]
