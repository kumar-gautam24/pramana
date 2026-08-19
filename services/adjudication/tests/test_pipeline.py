"""Tests for `services/pipeline.py` -- see the task-7 brief and its four resolved
short-circuit/reason mappings.

Unlike every other test file in this service, these run against `db_pool` directly,
not `db_session`: the pipeline under test acquires its own connections from the pool
for `case_events` (deliberately, see `repositories/case_events.py`) and for its own
transactions, so wrapping the *test* in one rolled-back transaction would not roll
back anything the pipeline itself did. The cost is that rows these tests insert are
not cleaned up afterwards -- and, for `case_events`, cannot be (the append-only
trigger forbids it, by design). That is an accepted, permanent cost of testing at this
level against `pramana_adjudication_test`: every test creates its own case under a
fresh `gen_random_uuid()`, so accumulated rows from previous runs never leak into a
later test's assertions, which all scope their reads to `case.id`.

Stub `PolicyClient`, `MemberClient` and `LLMProvider` only -- no live upstream, no live
model. The extraction fixture (`fixtures/ncd_240_4_extraction.py`) is reused as-is: it
already models NCD 240.4's real disjunctive structure (set 1: valid test + AHI >= 15;
sets 2-3: the 5-14 band with symptoms or a comorbidity; set 4: continuation/adherence),
which is exactly the shape needed to prove `winning_set` and "closest set" behavior
against something more than a synthetic one-criterion fixture."""

import json
from datetime import date

import pytest
from fixtures.ncd_240_4_extraction import HITS, RAW_RESPONSE
from pramana_common.criteria import GateReason, Outcome
from pramana_common.gate import GateThresholds

from adjudication.repositories import cases as cases_repo
from adjudication.services.member_client import Adherence, CoverageStatus, SleepStudy
from adjudication.services.pipeline import adjudicate
from adjudication.services.upstream import UpstreamUnavailable

MEMBER_ID = "m-1"
DATE_OF_SERVICE = date(2026, 1, 15)

#: RAW_RESPONSE decomposes into four sets of [2, 4, 4, 2] criteria -- 12 total. Every
#: test that runs the full pipeline against it (rather than short-circuiting before
#: extraction) expects exactly this many "criterion" events.
CRITERION_COUNT = 12


# --- test doubles ------------------------------------------------------------------


class StubPolicyClient:
    def __init__(self, hits=None):
        self._hits = [] if hits is None else hits
        self.calls: list[tuple] = []

    async def search(self, query, date_of_service, limit):
        self.calls.append((query, date_of_service, limit))
        if isinstance(self._hits, Exception):
            raise self._hits
        return self._hits


class StubMemberClient:
    """Matches `test_verify.py`'s stub, plus a default `adherence` that reports "no
    data uploaded" rather than `None` -- `verify.deterministic` reads `.nights` off
    whatever `adherence()` returns, and every pipeline test below exercises set 4's
    adherence criterion whether or not that test cares about its verdict."""

    def __init__(
        self, *, sleep_studies=None, conditions=None, coverage=None, adherence=None, notes=None
    ):
        self._sleep_studies = [] if sleep_studies is None else sleep_studies
        self._conditions = [] if conditions is None else conditions
        self._coverage = CoverageStatus.ACTIVE if coverage is None else coverage
        self._adherence = (
            Adherence(nights=0, qualifying_nights=0, fraction=0.0)
            if adherence is None
            else adherence
        )
        self._notes = [] if notes is None else notes
        self.calls: list[tuple] = []

    async def sleep_studies(self, member_id, before):
        self.calls.append(("sleep_studies", member_id, before))
        if isinstance(self._sleep_studies, Exception):
            raise self._sleep_studies
        return self._sleep_studies

    async def conditions(self, member_id, before, codes):
        self.calls.append(("conditions", member_id, before, codes))
        if isinstance(self._conditions, Exception):
            raise self._conditions
        return self._conditions

    async def coverage(self, member_id, on):
        self.calls.append(("coverage", member_id, on))
        if isinstance(self._coverage, Exception):
            raise self._coverage
        return self._coverage

    async def adherence(self, member_id, start, end, min_hours):
        self.calls.append(("adherence", member_id, start, end, min_hours))
        if isinstance(self._adherence, Exception):
            raise self._adherence
        return self._adherence

    async def notes(self, member_id, before):
        self.calls.append(("notes", member_id, before))
        if isinstance(self._notes, Exception):
            raise self._notes
        return self._notes


class StubLLM:
    """Records how many times it was asked, not just the last call -- the short-circuit
    tests need to tell "never called" apart from "called once, for extraction, and
    never again for a judgment verification"."""

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def chat(self, messages, schema):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


# --- fixtures ------------------------------------------------------------------------


async def _insert_case(pool, **overrides):
    values = dict(
        member_id=MEMBER_ID,
        requested_code="95810",
        icd10="G47.33",
        date_of_service=DATE_OF_SERVICE,
        kind="initial",
    )
    values.update(overrides)
    return await cases_repo.insert(pool, **values)


async def _fetch_events(pool, case_id) -> list[dict]:
    rows = await pool.fetch(
        "SELECT seq, type, payload FROM case_events WHERE case_id = $1 ORDER BY seq", case_id
    )
    return [{"seq": r["seq"], "type": r["type"], "payload": json.loads(r["payload"])} for r in rows]


def _study(**overrides) -> SleepStudy:
    fields = dict(
        id=1,
        date=date(2026, 1, 10),
        test_type="home_type_ii",
        channels=4,
        apnea_events=90,
        recorded_hours=6.0,
        ahi=20.0,
    )
    fields.update(overrides)
    return SleepStudy(**fields)


# === the full pipeline: happy path and near miss =====================================


async def test_happy_path_approves_with_winning_set_and_complete_event_sequence(db_pool):
    """Set 1's own two criteria (valid test, AHI >= 15) are both met; nothing about
    sets 2-4 changes that, once any set approves -- but every criterion in every set
    is still verified (decision 6), so the event count and seq run reflect all 12."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(sleep_studies=[_study(ahi=20.0)])
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.APPROVE
    assert determination.winning_set == 1
    assert determination.reason is None
    assert determination.blocking == []

    events = await _fetch_events(db_pool, case.id)
    types = [e["type"] for e in events]
    assert types[:4] == ["started", "eligibility", "policy", "criteria"]
    assert types[4:-1] == ["criterion"] * CRITERION_COUNT
    assert types[-1] == "decision"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    assert events[1]["payload"]["coverage_status"] == "active"
    assert events[-1]["payload"] == {
        "outcome": "approve",
        "reason": None,
        "winning_set": 1,
        "blocking": [],
    }

    stored = await cases_repo.get(db_pool, case.id)
    assert stored.status == "decided"


async def test_near_miss_escalates_naming_blocking_criterion(db_pool):
    """AHI 14.4 misses set 1's >= 15 bar by the least of any set's miss margin (one
    unmet criterion, versus two for sets 2-4 -- see the module docstring's fixture
    walkthrough in the task-7 report), so set 1 is the closest set and its AHI
    criterion, not any other set's, is what gets named."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(sleep_studies=[_study(ahi=14.4)])
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.winning_set is None
    assert determination.reason is GateReason.CRITERION_NOT_MET
    assert len(determination.blocking) == 1

    blocking_id = int(determination.blocking[0])
    row = await db_pool.fetchrow(
        "SELECT set_ordinal, params FROM criteria WHERE id = $1", blocking_id
    )
    assert row["set_ordinal"] == 1
    assert json.loads(row["params"]) == {"fact": "ahi", "operator": ">=", "value": 15}

    events = await _fetch_events(db_pool, case.id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert len(events) == 4 + CRITERION_COUNT + 1
    assert events[-1]["payload"]["outcome"] == "escalate"
    assert events[-1]["payload"]["blocking"] == determination.blocking


# === short-circuits: never reach the model, never approve ============================


async def test_not_eligible_no_record_escalates_and_never_reaches_the_model(db_pool):
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(coverage=CoverageStatus.NO_RECORD)
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.INSUFFICIENT_EVIDENCE
    assert determination.blocking == ["not_eligible"]
    assert llm.calls == 0
    assert policy_client.calls == []


async def test_not_eligible_inactive_escalates_with_criterion_not_met_reason(db_pool):
    """The NO_RECORD/INACTIVE split (task-7 brief, decision 2): a record that says
    "not covered" gets the reason the coverage_active verifier itself would give it,
    not the same reason as "no record at all" -- eligibility must not contradict the
    verifier about the identical fact."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(coverage=CoverageStatus.INACTIVE)
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.CRITERION_NOT_MET
    assert determination.blocking == ["not_eligible"]
    assert llm.calls == 0
    assert policy_client.calls == []


async def test_no_governing_policy_escalates_when_search_returns_no_hits(db_pool):
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=[])
    member_client = StubMemberClient()
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.NO_CRITERIA
    assert determination.blocking == ["no_governing_policy"]
    assert llm.calls == 0


async def test_no_criteria_escalates_when_extraction_is_invalid(db_pool):
    """`extract()` necessarily calls the model once to attempt decomposition -- that
    call is what "extraction failed" means. What must never happen is a *second* call,
    which would mean a judgment criterion reached verification despite there being no
    valid criteria at all."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient()
    llm = StubLLM({"sets": []})

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.NO_CRITERIA
    assert determination.blocking == ["no_criteria"]
    assert llm.calls == 1


async def test_upstream_unavailable_from_eligibility_escalates_and_never_reaches_the_model(
    db_pool,
):
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(coverage=UpstreamUnavailable("member", "timed out"))
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.INSUFFICIENT_EVIDENCE
    assert determination.blocking == ["upstream_unavailable"]
    assert llm.calls == 0
    assert policy_client.calls == []


async def test_upstream_unavailable_from_policy_search_escalates(db_pool):
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=UpstreamUnavailable("policy", "timed out"))
    member_client = StubMemberClient()
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.INSUFFICIENT_EVIDENCE
    assert determination.blocking == ["upstream_unavailable"]
    assert llm.calls == 0


async def test_upstream_unavailable_from_extraction_escalates(db_pool):
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient()
    llm = StubLLM(UpstreamUnavailable("llm", "timed out"))

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.INSUFFICIENT_EVIDENCE
    assert determination.blocking == ["upstream_unavailable"]


async def test_upstream_unavailable_during_verification_escalates(db_pool):
    """One failing task among the twelve concurrent verifications is still enough to
    short-circuit the whole case -- proving `return_exceptions=True` is actually being
    used to detect this, not merely to avoid crashing on it."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(sleep_studies=UpstreamUnavailable("member", "timed out"))
    llm = StubLLM(RAW_RESPONSE)

    determination = await adjudicate(
        case.id, db_pool, policy_client, member_client, llm, GateThresholds()
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.reason is GateReason.INSUFFICIENT_EVIDENCE
    assert determination.blocking == ["upstream_unavailable"]


async def test_non_upstream_exception_during_verification_propagates(db_pool):
    """The other half of decision 6: an unexpected exception is a bug, not a routine
    upstream failure, and must not be swallowed into a routine-looking escalation."""
    case = await _insert_case(db_pool)
    policy_client = StubPolicyClient(hits=HITS)
    member_client = StubMemberClient(sleep_studies=RuntimeError("boom"))
    llm = StubLLM(RAW_RESPONSE)

    with pytest.raises(RuntimeError, match="boom"):
        await adjudicate(case.id, db_pool, policy_client, member_client, llm, GateThresholds())
