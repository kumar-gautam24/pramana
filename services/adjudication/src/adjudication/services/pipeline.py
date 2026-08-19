"""The seven-stage pipeline: `started -> eligibility -> policy -> criteria ->
criterion (one per criterion) -> decision`.

Per the task-7 brief's own resolution of the design spec's stage list: the spec's
`normalize` stage (free text -> codes) has no home here -- `cases.requested_code` and
`cases.icd10` already arrive set and NOT NULL (migrations/0001), so there is no free
text for a `normalize` stage to act on within this task's schema.

Each stage appends exactly one `case_events` row, via `repositories.case_events.append`,
naming itself with the stage's own string as `type`. A stage that never completes --
because it short-circuited the case -- never gets its own row; the pipeline jumps
straight from whichever stage it reached to `decision`, which is why a short-circuited
case's event log is short rather than padded with rows for stages that never ran.

Events commit independently of the pipeline's own transactions (see
`repositories.case_events`'s module docstring): a stage that ran must stay in the audit
trail even if a later stage fails. The determination and its criterion results, by
contrast, are the one thing this module writes inside a single transaction -- see
`_persist` and `_short_circuit` below -- because a reviewer must never be able to see a
determination with no criterion results behind it, or criterion results with no
determination.

**Short-circuits.** Four situations end the case before it ever reaches a verifier or
`domain.criteria_sets.aggregate` (which is to say, before the gate). Each is recorded
as a `determinations` row exactly like any other decision -- `status` moves to
`decided`, never `failed` (`failed` is reserved for a genuine worker crash, Task 8's
concern) -- so a short-circuited case is never invisible to the reviewer queue. Because
`determinations.reason` is a closed four-value CHECK matching `GateReason`, the
short-circuit's own name is not a fifth reason; it is recorded as a single-element JSON
array in `blocking` instead (see `repositories.determinations`'s module docstring for
the full account of why that column holds two different shapes):

| short-circuit         | trigger                                    | reason                 |
|------------------------|---------------------------------------------|-------------------------|
| `not_eligible`         | `CoverageStatus.NO_RECORD`                  | `insufficient_evidence` |
| `not_eligible`         | `CoverageStatus.INACTIVE`                   | `criterion_not_met`     |
| `no_governing_policy`  | `PolicyClient.search` returns `[]`          | `no_criteria`           |
| `no_criteria`          | `extract` raises `ExtractionInvalid`        | `no_criteria`           |
| `upstream_unavailable` | `UpstreamUnavailable` from any upstream call | `insufficient_evidence` |

The `not_eligible` split is deliberate, not incidental: Task 6's `coverage_active`
verifier already maps this identical fact the same way (no record is a missing
document; a record that says inactive is a contradicting one), and eligibility must
not contradict the verifier about the same fact by choosing differently here.

**Stage ordering is load-bearing**, not merely the order these checks happen to be
written in. `services/verify/__init__.py`'s own docstring records that every verifier
in `deterministic.py` assumes the member already exists -- in particular, that an empty
list from `member` (no conditions, no sleep studies) means a true `NOT_MET`, not a
missing document -- and that assumption is only safe because eligibility is checked and
short-circuited on `CoverageStatus.NO_RECORD` **before any verifier runs**. Moving
eligibility after the policy search or after verification would let the pipeline
produce denial-shaped `NOT_MET` answers about members it has never heard of.

**Concurrency.** Every criterion across every set is verified in one
`asyncio.gather(..., return_exceptions=True)` -- not one gather per set -- because each
criterion row belongs to exactly one set and sets are independent of each other.
`return_exceptions=True` is what keeps one criterion's failure from leaving its sibling
tasks running against a pool this function has already moved on from; an
`UpstreamUnavailable` among the results short-circuits the case, and anything else
(a bug, not an expected failure mode) is re-raised rather than swallowed.
"""

import asyncio

import asyncpg
from pramana_common.criteria import CriterionResult, GateReason, Outcome
from pramana_common.gate import GateThresholds

from adjudication.domain.criteria_sets import aggregate
from adjudication.domain.params import ExtractionInvalid
from adjudication.models.determination import Determination
from adjudication.repositories import case_events as case_events_repo
from adjudication.repositories import cases as cases_repo
from adjudication.repositories import criteria as criteria_repo
from adjudication.repositories import criterion_results as criterion_results_repo
from adjudication.repositories import determinations as determinations_repo
from adjudication.services.extract import extract
from adjudication.services.llm import LLMProvider
from adjudication.services.member_client import CoverageStatus, MemberClient
from adjudication.services.policy_client import PolicyClient
from adjudication.services.upstream import UpstreamUnavailable
from adjudication.services.verify import Verification, verify

#: Retrieval width for the policy search that opens the `policy` stage. Twice what
#: NCD 240.4's worked example needs (chunks 56-59, four chunks -- see
#: tests/fixtures/ncd_240_4_extraction.py) so a policy that spans a couple more
#: sections still fits, without pulling in enough unrelated chunks to blow out the
#: extraction prompt built from them (see services/extract.py's MAX_SETS for the
#: matching cap on the other side of that call).
POLICY_SEARCH_LIMIT = 8


def _thresholds_payload(thresholds: GateThresholds) -> dict:
    return {"min_confidence": thresholds.min_confidence}


async def _short_circuit(
    pool: asyncpg.Pool, case_id: str, name: str, reason: GateReason, thresholds: GateThresholds
) -> Determination:
    """Record `name` as both the sole entry of `determinations.blocking` and the
    `decision` event's `blocking` field -- the two places a reader needs it, since
    `determinations.reason`'s closed set has no room for a fifth value naming the
    short-circuit itself (see this module's docstring table)."""
    blocking = [name]
    await case_events_repo.append(
        pool,
        case_id,
        "decision",
        {
            "outcome": Outcome.ESCALATE.value,
            "reason": reason.value,
            "winning_set": None,
            "blocking": blocking,
        },
    )
    async with pool.acquire() as conn, conn.transaction():
        determination = await determinations_repo.insert(
            conn,
            case_id=case_id,
            outcome=Outcome.ESCALATE,
            reason=reason,
            blocking=blocking,
            thresholds=_thresholds_payload(thresholds),
            winning_set=None,
        )
        # Every short-circuit still leaves the case `decided`, never `failed`:
        # `failed` means the worker crashed (Task 8's concern), and a short-circuited
        # case with no determination -- or one stuck in `running` -- is invisible to
        # the reviewer queue, exactly the failure this system exists to prevent.
        await cases_repo.update_status(conn, case_id, "decided")
    return determination


async def _persist_decision(
    pool: asyncpg.Pool,
    case_id: str,
    thresholds: GateThresholds,
    outcome: Outcome,
    reason: GateReason | None,
    blocking: list[str],
    winning_set: int | None,
    verifications: list[tuple[int, Verification]],
) -> Determination:
    """The determination and its criterion results, in one transaction: a reviewer
    must never be able to load a determination with no results behind it, or results
    with no determination naming the case decided. The `case_events` `decision` row is
    written first and separately, per this module's docstring on why events never
    share a transaction with anything else."""
    await case_events_repo.append(
        pool,
        case_id,
        "decision",
        {
            "outcome": outcome.value,
            "reason": reason.value if reason is not None else None,
            "winning_set": winning_set,
            "blocking": list(blocking),
        },
    )
    async with pool.acquire() as conn, conn.transaction():
        await criterion_results_repo.insert_many(conn, verifications)
        determination = await determinations_repo.insert(
            conn,
            case_id=case_id,
            outcome=outcome,
            reason=reason,
            blocking=list(blocking),
            thresholds=_thresholds_payload(thresholds),
            winning_set=winning_set,
        )
        await cases_repo.update_status(conn, case_id, "decided")
    return determination


async def adjudicate(
    case_id: str,
    pool: asyncpg.Pool,
    policy_client: PolicyClient,
    member_client: MemberClient,
    llm: LLMProvider,
    thresholds: GateThresholds,
) -> Determination:
    """Run the full pipeline for an already-persisted case and return its
    `Determination`. Loading the case by id is this function's own first step
    (rather than accepting a `Case` object) because Task 8's worker receives only an
    id off a Redis stream -- this is the seam that lets it call in with nothing else.
    """
    case = await cases_repo.get(pool, case_id)
    if case is None:
        raise LookupError(f"no case {case_id!r} to adjudicate")

    await cases_repo.update_status(pool, case_id, "running")
    await case_events_repo.append(pool, case_id, "started", {})

    # --- eligibility ------------------------------------------------------------
    try:
        coverage = await member_client.coverage(case.member_id, case.date_of_service)
    except UpstreamUnavailable:
        return await _short_circuit(
            pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
        )

    if coverage is CoverageStatus.NO_RECORD:
        return await _short_circuit(
            pool, case_id, "not_eligible", GateReason.INSUFFICIENT_EVIDENCE, thresholds
        )
    if coverage is CoverageStatus.INACTIVE:
        return await _short_circuit(
            pool, case_id, "not_eligible", GateReason.CRITERION_NOT_MET, thresholds
        )

    await case_events_repo.append(
        pool, case_id, "eligibility", {"coverage_status": coverage.value}
    )

    # --- policy: find the governing policy --------------------------------------
    # Built from the case's own codes and nothing else -- no policy names, no disease
    # terms, no per-code branching (task-7 brief, decision 5). Adding anything else
    # here would be exactly the per-policy hardcoding CLAUDE.md invariant 3 forbids.
    query = f"{case.requested_code} {case.icd10}"
    try:
        hits = await policy_client.search(query, case.date_of_service, POLICY_SEARCH_LIMIT)
    except UpstreamUnavailable:
        return await _short_circuit(
            pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
        )

    if not hits:
        return await _short_circuit(
            pool, case_id, "no_governing_policy", GateReason.NO_CRITERIA, thresholds
        )

    await case_events_repo.append(
        pool,
        case_id,
        "policy",
        {"hit_chunk_ids": [hit.chunk_id for hit in hits], "hit_count": len(hits)},
    )

    # --- criteria: decompose the policy into alternative criteria sets ----------
    try:
        extracted_sets = await extract(llm, hits)
    except ExtractionInvalid:
        return await _short_circuit(
            pool, case_id, "no_criteria", GateReason.NO_CRITERIA, thresholds
        )
    except UpstreamUnavailable:
        return await _short_circuit(
            pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
        )

    criteria_sets = await criteria_repo.insert_many(pool, case_id, extracted_sets)

    await case_events_repo.append(
        pool,
        case_id,
        "criteria",
        {
            "set_count": len(criteria_sets),
            "criterion_count": sum(len(s.criteria) for s in criteria_sets),
        },
    )

    # --- criterion: verify every criterion across every set, concurrently -------
    # One gather over the flattened list, not one per set: sets are independent, and
    # a criterion belongs to exactly one of them, so there is nothing set boundaries
    # would add to how these run (task-7 brief, decision 6).
    all_criteria = [
        criterion for criteria_set in criteria_sets for criterion in criteria_set.criteria
    ]
    raw_results = await asyncio.gather(
        *(verify(criterion, case, member_client, llm) for criterion in all_criteria),
        return_exceptions=True,
    )

    upstream_failure = next(
        (r for r in raw_results if isinstance(r, UpstreamUnavailable)), None
    )
    if upstream_failure is not None:
        return await _short_circuit(
            pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
        )

    # Anything left that is an exception is a real bug, not an expected failure mode
    # -- re-raised rather than folded into an escalation the caller could mistake for
    # a routine one (task-7 brief, decision 6: "re-raise anything else").
    other_failure = next((r for r in raw_results if isinstance(r, BaseException)), None)
    if other_failure is not None:
        raise other_failure

    verifications: list[Verification] = raw_results

    for criterion, verification in zip(all_criteria, verifications, strict=True):
        await case_events_repo.append(
            pool,
            case_id,
            "criterion",
            {
                "criterion_id": str(criterion.id),
                "set_ordinal": criterion.set_ordinal,
                "ordinal": criterion.ordinal,
                "verdict": verification.result.verdict.value,
                "confidence": verification.result.confidence,
                "tool": verification.tool,
            },
        )

    # --- decision: aggregate across sets and gate ---------------------------------
    results_by_id: dict[str, CriterionResult] = {
        verification.result.criterion_id: verification.result for verification in verifications
    }
    decision = aggregate(criteria_sets, results_by_id, thresholds)

    return await _persist_decision(
        pool,
        case_id,
        thresholds,
        decision.outcome,
        decision.reason,
        list(decision.blocking),
        decision.winning_set,
        list(zip((c.id for c in all_criteria), verifications, strict=True)),
    )
