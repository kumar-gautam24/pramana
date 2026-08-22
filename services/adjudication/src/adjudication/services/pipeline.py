"""The six-stage pipeline: `started -> eligibility -> policy -> criteria ->
criterion (one per criterion) -> decision`.

Six, not the design's seven: the spec's `normalize` stage (free text -> codes) was
**struck**, not deferred -- see ADR-0018. `cases.requested_code` and `cases.icd10`
arrive set and NOT NULL (migrations/0001) because the submitter's billing system
already assigned them, and asking a model to produce them instead would place a
model-generated fact at the one point in this pipeline that nothing downstream
re-checks. The narrative a submission does carry is `request_text`, and its job is
retrieval (see the policy stage below), not identification.

Each stage appends exactly one `case_events` row, via `repositories.case_events.append`,
naming itself with the stage's own string as `type`. A stage that never completes --
because it short-circuited the case -- never gets its own row; the pipeline jumps
straight from whichever stage it reached to `decision`, which is why a short-circuited
case's event log is short rather than padded with rows for stages that never ran.

Events commit independently of the pipeline's own transactions (see
`repositories.case_events`'s module docstring): a stage that ran must stay in the audit
trail even if a later stage fails. The determination and its criterion results, by
contrast, are the one thing this module writes inside a single transaction -- see
`_persist_decision` and `_short_circuit` below -- because a reviewer must never be able
to see a determination with no criterion results behind it, or criterion results with no
determination.

**Exception: the `decision` event.** Every other event above describes a stage that has
already finished by the time it is appended, so appending it before whatever comes next
is safe -- that "next thing" cannot retroactively undo a stage that already ran.
`decision` is not like that: it *describes the outcome of the transaction that persists
it*, so appending it beforehand means describing a transaction that has not committed
yet. If that transaction then failed, the append-only log would permanently say a case
approved (or escalated) when no determination exists for it and the case is still
`running` -- exactly the corruption Task 8's SSE console would render as fact. Fix round
1 (finding 2) moved both `_short_circuit` and `_persist_decision` to append `decision`
only *after* their transaction commits, the one deliberate exception to "append, then
do the work" that the rest of this module follows.

Fix round 1 (finding 6) also changed when `criterion` events are appended: every
verification that actually completed gets its event before the pipeline checks whether
any sibling verification failed upstream, not after. A criterion is not itself
unverified just because `asyncio.gather` returned its result beside a failure -- see the
concurrency note below.

Fix round 1 (finding 1) wrapped the criteria-persistence step in a transaction that also
deletes the case's previous criteria first -- see `repositories.criteria.insert_many`'s
own docstring for why a second `adjudicate(case_id)` needs that to be well-defined rather
than a `UniqueViolationError`.

Fix round 1 (approved change) also gave the policy-search query a narrative-text input:
`case.request_text or f"{case.requested_code} {case.icd10}"`. The codes-only query the
brief originally specified retrieves poorly against the real corpus -- see
task-7-fixes.md's measured retrieval table -- and the fallback keeps a case with no
narrative adjudicating (just less well) rather than crashing on one that predates the
column, or was submitted without one.

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
| `upstream_unavailable` | a *permanent* `UpstreamUnavailable`, below   | `insufficient_evidence` |

**A transient `UpstreamUnavailable` is not a short-circuit** and is deliberately not handled
here (ADR-0020). A 429 or a 5xx is a fact about our own infrastructure, not about the
member's record, and recording it as a determination puts a case on a clinician's queue for
a reason no clinician can act on. Those propagate out of `adjudicate` so `worker.py` can
retry the case with backoff and record each attempt in `case_events` -- which is where a
retry belongs, because that is the only layer that can make it visible in the audit trail.
`policy_client`'s docstring says why the alternative, a retry hidden in the client, is wrong.
`record_upstream_exhausted` below is what the worker calls once the ladder runs out, so a
case that never got its evidence still ends as a determination rather than as silence.

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

**The `model_arithmetic` run mode changes nothing in this module** beyond the `started`
event's payload (ADR-0021). `services/verify` reads `case.run_mode` and swaps who performs
the threshold, enum and temporal comparisons; the fetches, the evidence, the gate, the
thresholds, the events and the persistence are the same code on both arms. That is what
makes a run and its ablated twin a comparison rather than an anecdote, and it is why
nothing here branches on the mode.

**Concurrency.** Every criterion across every set is verified through one
`verify_all` call -- one gather, not one per set, because each criterion row belongs to
exactly one set and sets are independent. That function returns exceptions positionally
rather than raising them, which is what keeps one criterion's failure from leaving its
sibling tasks running against a pool this function has already moved on from; an
`UpstreamUnavailable` among the results short-circuits the case, and anything else
(a bug, not an expected failure mode) is re-raised rather than swallowed. Every
verification that did complete is still appended as a `criterion` event first, before
either check runs -- a criterion that finished is evidence gathered, whether or not a
sibling's failure means the case will not use it to reach the gate (finding 6).
"""

import dataclasses

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
from adjudication.services.verify import Verification, verify_all

#: Retrieval width for the policy search that opens the `policy` stage. Twice what
#: NCD 240.4's worked example needs (chunks 56-59, four chunks -- see
#: tests/fixtures/ncd_240_4_extraction.py) so a policy that spans a couple more
#: sections still fits, without pulling in enough unrelated chunks to blow out the
#: extraction prompt built from them (see services/extract.py's MAX_SETS for the
#: matching cap on the other side of that call).
POLICY_SEARCH_LIMIT = 8


def _thresholds_payload(thresholds: GateThresholds) -> dict:
    # `dataclasses.asdict` rather than naming `min_confidence` by hand (finding 7):
    # `GateThresholds` is total today, but a hand-picked field list silently drops
    # anything a future field adds, and nothing here would fail loudly to say so.
    return dataclasses.asdict(thresholds)


async def _short_circuit(
    pool: asyncpg.Pool, case_id: str, name: str, reason: GateReason, thresholds: GateThresholds
) -> Determination:
    """Record `name` as both the sole entry of `determinations.blocking` and the
    `decision` event's `blocking` field -- the two places a reader needs it, since
    `determinations.reason`'s closed set has no room for a fifth value naming the
    short-circuit itself (see this module's docstring table).

    The `decision` event is appended only after the transaction below commits (finding
    2, fix round 1): it describes that transaction's own outcome, so appending it first
    would leave a `decision` event for a determination that, if the transaction then
    failed, never actually got written."""
    blocking = [name]
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
    return determination


async def _upstream_stopped(
    pool: asyncpg.Pool, case_id: str, exc: UpstreamUnavailable, thresholds: GateThresholds
) -> Determination:
    """What every upstream failure in this module funnels through.

    A permanent one (a schema mismatch, a 4xx) becomes the `upstream_unavailable`
    short-circuit, exactly as every upstream failure did before ADR-0020. A transient one
    (a 429, a 5xx, a timeout) is **re-raised instead**: it says nothing about the member's
    record, so it must not be written down as a determination about the member's record.
    `worker.py` catches it, backs off, and runs the case again."""
    if exc.transient:
        raise exc
    return await _short_circuit(
        pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
    )


async def record_upstream_exhausted(
    pool: asyncpg.Pool,
    case_id: str,
    thresholds: GateThresholds,
    *,
    service: str,
    detail: str,
    attempts: int,
) -> Determination:
    """End a case whose transient upstream failure outlived the worker's retry ladder.

    Called from `worker.py`, which owns the ladder; the recording lives here with the other
    two functions that write a determination, so all three keep the same ordering guarantee
    (transaction first, `decision` event after) and a reader looking for "where does a case
    stop" finds one file.

    The `upstream_exhausted` event is appended first and is not folded into the `decision`
    payload: how many times we tried and what failed each time is the difference between a
    reviewer reading "a service could not be reached" as a shrug and reading it as a
    measurement. The determination it precedes is the ordinary `upstream_unavailable`
    short-circuit -- the case really has no evidence behind it, however many attempts went
    into establishing that."""
    await case_events_repo.append(
        pool,
        case_id,
        "upstream_exhausted",
        {"service": service, "detail": detail, "attempts": attempts},
    )
    return await _short_circuit(
        pool, case_id, "upstream_unavailable", GateReason.INSUFFICIENT_EVIDENCE, thresholds
    )


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
    appended only after that transaction commits (finding 2, fix round 1) -- see
    `_short_circuit`'s docstring for why, which applies identically here."""
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
    # `run_mode` is on the very first event, not inferred from the tools further down: the
    # audit trail has to say which arithmetic decided a case in the one place a reader looks
    # first, and a retried attempt (ADR-0020) appends its own `started` so the answer is
    # per attempt rather than per case (ADR-0021).
    await case_events_repo.append(
        pool, case_id, "started", {"run_mode": case.run_mode.value}
    )

    # --- eligibility ------------------------------------------------------------
    try:
        coverage = await member_client.coverage(case.member_id, case.date_of_service)
    except UpstreamUnavailable as exc:
        return await _upstream_stopped(pool, case_id, exc, thresholds)

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
    # The case's own narrative if it has one, falling back to its codes -- never a
    # policy name, a disease term, or any other per-code branching (task-7 brief,
    # decision 5; CLAUDE.md invariant 3). The narrative comes from the caller, not from
    # anything this module invents: a cross-encoder ranks a bare-code query no better
    # than noise (codes are out of distribution for a model trained on question/passage
    # pairs -- see task-7-fixes.md's measured retrieval table), so a case submitted
    # with real clinical text retrieves the governing chunks and one submitted without
    # any still adjudicates, just less well (approved change, fix round 1).
    query = case.request_text or f"{case.requested_code} {case.icd10}"
    try:
        hits = await policy_client.search(query, case.date_of_service, POLICY_SEARCH_LIMIT)
    except UpstreamUnavailable as exc:
        return await _upstream_stopped(pool, case_id, exc, thresholds)

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
    except UpstreamUnavailable as exc:
        return await _upstream_stopped(pool, case_id, exc, thresholds)

    # One transaction for the delete-then-insert re-adjudication needs (finding 1, fix
    # round 1): a mid-loop failure here must roll back to the previous run's rows, not
    # leave a mix of old and half-written new ones -- see
    # `repositories.criteria.insert_many`'s docstring for the full account.
    async with pool.acquire() as conn, conn.transaction():
        criteria_sets = await criteria_repo.insert_many(conn, case_id, extracted_sets)

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
    # One flattened list, not one batch per set: sets are independent, and a criterion
    # belongs to exactly one of them, so set boundaries add nothing to how these run
    # (task-7 brief, decision 6). `verify_all` owns the concurrency and returns results
    # positionally, including exceptions -- see its docstring for why the judgment
    # criteria go to the model together while the deterministic ones do not.
    all_criteria = [
        criterion for criteria_set in criteria_sets for criterion in criteria_set.criteria
    ]
    raw_results = await verify_all(all_criteria, case, member_client, llm)

    # Every verification that actually completed gets its `criterion` event now, before
    # either check below runs (finding 6, fix round 1): a criterion is not itself
    # unverified just because a sibling task in the same gather() raised
    # `UpstreamUnavailable` -- its evidence must not be lost from the audit trail just
    # because the case goes on to short-circuit for an unrelated reason.
    for criterion, result in zip(all_criteria, raw_results, strict=True):
        if isinstance(result, BaseException):
            continue
        await case_events_repo.append(
            pool,
            case_id,
            "criterion",
            {
                "criterion_id": str(criterion.id),
                "set_ordinal": criterion.set_ordinal,
                "ordinal": criterion.ordinal,
                "verdict": result.result.verdict.value,
                "confidence": result.result.confidence,
                "tool": result.tool,
            },
        )

    upstream_failure = next(
        (r for r in raw_results if isinstance(r, UpstreamUnavailable)), None
    )
    if upstream_failure is not None:
        return await _upstream_stopped(pool, case_id, upstream_failure, thresholds)

    # Anything left that is an exception is a real bug, not an expected failure mode
    # -- re-raised rather than folded into an escalation the caller could mistake for
    # a routine one (task-7 brief, decision 6: "re-raise anything else").
    other_failure = next((r for r in raw_results if isinstance(r, BaseException)), None)
    if other_failure is not None:
        raise other_failure

    verifications: list[Verification] = raw_results

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
