"""Tests for `services/verify` -- see the task-6 brief and ADR-0003.

Stub `MemberClient` and `LLMProvider` only: no live member service, no live model, no
database. Tests are named for the rule they would catch a regression in, and the
boundary tests are deliberately paired (value == threshold vs. value == threshold + 1,
or the `>=`/`>` operator swapped) because that exact confusion has caused two defects
in this project already."""

from datetime import date, datetime, timedelta

import pytest
from pramana_common.criteria import CriterionType, Verdict

from adjudication.domain.params import FACTS, THRESHOLD_OPERATORS, FactDataType
from adjudication.models.case import Case
from adjudication.models.criterion import Criterion
from adjudication.services.member_client import (
    Adherence,
    Condition,
    CoverageStatus,
    Note,
    SleepStudy,
)
from adjudication.services.upstream import UpstreamUnavailable
from adjudication.services.verify import deterministic as deterministic_module
from adjudication.services.verify import verify
from adjudication.services.verify.deterministic import verify as verify_deterministic
from adjudication.services.verify.judgment import verify as verify_judgment

DATE_OF_SERVICE = date(2026, 1, 15)
MEMBER_ID = "p1"


# --- test doubles ----------------------------------------------------------------------


class StubMemberClient:
    """Each factual method returns a preset value, or raises if the preset is an
    exception instance -- matching `test_clients.py`'s httpx-mock convention one layer
    up, without needing a real transport. `calls` records what was asked for, so a
    test can assert this module fetched with the arguments it claims to (e.g. the
    adherence window it computed)."""

    def __init__(self, *, sleep_studies=None, conditions=None, coverage=None, adherence=None,
                 notes=None):
        self._sleep_studies = [] if sleep_studies is None else sleep_studies
        self._conditions = [] if conditions is None else conditions
        self._coverage = CoverageStatus.ACTIVE if coverage is None else coverage
        self._adherence = adherence
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
    """Records the last `chat` call and returns a fixed response -- the stand-in for a
    model this suite never talks to. `response` may be an exception instance, raised
    instead of returned."""

    def __init__(self, response):
        self.response = response
        self.last_messages = None
        self.last_schema = None

    async def chat(self, messages, schema):
        self.last_messages = messages
        self.last_schema = schema
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class NeverCalledLLM:
    """A deterministic criterion must never reach the model -- passed to
    `verify_deterministic`-shaped calls so any accidental `llm.chat` call fails loudly
    rather than silently returning a canned answer."""

    async def chat(self, messages, schema):
        raise AssertionError("llm.chat must not be called for a deterministic criterion")


# --- fixtures ----------------------------------------------------------------------


def _case(**overrides) -> Case:
    defaults = dict(
        id="case-1",
        member_id=MEMBER_ID,
        requested_code="E0601",
        icd10="G47.33",
        date_of_service=DATE_OF_SERVICE,
        kind="initial",
        status="running",
        created_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    defaults.update(overrides)
    return Case(**defaults)


def _criterion(criterion_type: CriterionType, params: dict, criterion_id: int = 1) -> Criterion:
    return Criterion(
        id=criterion_id,
        case_id="case-1",
        set_ordinal=1,
        ordinal=0,
        text="test criterion",
        type=criterion_type,
        params=params,
        source_chunk_id=1,
        source_display_id="240.4",
    )


def _study(study_id: int = 1, days_before: int = 0, **overrides) -> SleepStudy:
    fields = dict(
        id=study_id,
        date=DATE_OF_SERVICE - timedelta(days=days_before),
        test_type="home_type_iv",
        channels=4,
        apnea_events=100,
        recorded_hours=6.0,
        ahi=20.0,
    )
    fields.update(overrides)
    return SleepStudy(**fields)


CASE = _case()


# === threshold: >=/> boundary, both directions =========================================


async def test_threshold_ge_met_at_exact_boundary():
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=15.0)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_threshold_gt_not_met_at_exact_boundary():
    """The paired case for the test above: same value, `>` instead of `>=`, opposite
    verdict. A mutation swapping `>=` for `>` (or vice versa) fails one of this pair."""
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=15.0)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_threshold_gt_met_just_above_boundary():
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=15.1)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_threshold_le_met_at_exact_boundary():
    criterion = _criterion(
        CriterionType.THRESHOLD, {"fact": "channels", "operator": "<=", "value": 3}
    )
    client = StubMemberClient(sleep_studies=[_study(channels=3)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_threshold_lt_not_met_at_exact_boundary():
    criterion = _criterion(
        CriterionType.THRESHOLD, {"fact": "channels", "operator": "<", "value": 3}
    )
    client = StubMemberClient(sleep_studies=[_study(channels=3)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_threshold_eq_met_when_equal():
    criterion = _criterion(
        CriterionType.THRESHOLD, {"fact": "apnea_events", "operator": "==", "value": 30}
    )
    client = StubMemberClient(sleep_studies=[_study(apnea_events=30)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_threshold_eq_not_met_when_unequal():
    criterion = _criterion(
        CriterionType.THRESHOLD, {"fact": "apnea_events", "operator": "==", "value": 30}
    )
    client = StubMemberClient(sleep_studies=[_study(apnea_events=31)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


# === threshold: any qualifying study, not "most recent" =================================


async def test_threshold_met_by_any_study_regardless_of_recency():
    """The most recent study fails; an older one passes. MET, and the evidence names
    the older, passing study -- there is no "most recent" rule."""
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    older_passing = _study(study_id=1, days_before=200, ahi=20.0)
    newer_failing = _study(study_id=2, days_before=10, ahi=5.0)
    client = StubMemberClient(sleep_studies=[older_passing, newer_failing])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET
    assert result.evidence["matched_study"]["study_id"] == 1


async def test_threshold_not_met_when_no_study_qualifies():
    """Decision-3 row 2: studies exist, none satisfies -- NOT_MET, not
    INSUFFICIENT_EVIDENCE, because the record has an answer and the answer is no."""
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=5.0), _study(study_id=2, ahi=8.0)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET
    assert result.evidence["matched_study"] is None
    assert len(result.evidence["studies_checked"]) == 2


async def test_threshold_insufficient_evidence_when_no_sleep_studies():
    """Decision-3 row 1: no sleep studies at all -- there is no document to read."""
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    client = StubMemberClient(sleep_studies=[])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


# === threshold: confidence is exactly 1.0, not merely high =============================


async def test_threshold_confidence_is_exactly_one_not_merely_high():
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=20.0)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.confidence == 1.0
    assert not (result.result.confidence >= 0.9 and result.result.confidence != 1.0)


# === threshold: adherence facts =========================================================


async def test_adherence_fraction_ge_met_at_boundary():
    criterion = _criterion(
        CriterionType.THRESHOLD,
        {
            "fact": "adherence_fraction", "operator": ">=", "value": 0.7,
            "fact_args": {"min_hours": 4.0, "window_days": 30},
        },
    )
    client = StubMemberClient(adherence=Adherence(nights=30, qualifying_nights=21, fraction=0.7))

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET
    # The window this module computed is itself part of the evidence a reviewer needs.
    assert client.calls[0][0] == "adherence"
    _, member_id, start, end, min_hours = client.calls[0]
    assert member_id == MEMBER_ID
    assert (start, end, min_hours) == (date(2025, 12, 17), DATE_OF_SERVICE, 4.0)


async def test_adherence_fraction_not_met_below_boundary():
    criterion = _criterion(
        CriterionType.THRESHOLD,
        {
            "fact": "adherence_fraction", "operator": ">=", "value": 0.7,
            "fact_args": {"min_hours": 4.0, "window_days": 30},
        },
    )
    client = StubMemberClient(
        adherence=Adherence(nights=30, qualifying_nights=20, fraction=20 / 30)
    )

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_adherence_nights_uses_qualifying_nights_not_total_nights():
    criterion = _criterion(
        CriterionType.THRESHOLD,
        {
            "fact": "adherence_nights", "operator": ">=", "value": 21,
            "fact_args": {"min_hours": 4.0, "window_days": 30},
        },
    )
    client = StubMemberClient(adherence=Adherence(nights=30, qualifying_nights=21, fraction=0.7))

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET
    assert result.evidence["observed"] == 21


async def test_adherence_insufficient_evidence_when_zero_nights_observed():
    """Decision-3 row 6: `nights == 0` means no usage data was ever uploaded, which is
    not the same fact as "the member used the device on zero qualifying nights"."""
    criterion = _criterion(
        CriterionType.THRESHOLD,
        {
            "fact": "adherence_fraction", "operator": ">=", "value": 0.7,
            "fact_args": {"min_hours": 4.0, "window_days": 30},
        },
    )
    client = StubMemberClient(adherence=Adherence(nights=0, qualifying_nights=0, fraction=0.0))

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


# === enum: condition_codes ==============================================================


async def test_enum_condition_codes_met_when_present():
    criterion = _criterion(
        CriterionType.ENUM, {"fact": "condition_codes", "allowed": ["53741008"]}
    )
    client = StubMemberClient(
        conditions=[
            Condition(id=1, code="53741008", description="CAD", onset_date=date(2020, 1, 1))
        ]
    )

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_enum_condition_codes_not_met_when_list_empty():
    """Decision-3 row 5, the one that looks wrong and is right: an empty list from
    `member` is a true fact (no comorbidity on record), not a missing document."""
    criterion = _criterion(
        CriterionType.ENUM, {"fact": "condition_codes", "allowed": ["53741008"]}
    )
    client = StubMemberClient(conditions=[])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET
    assert result.result.verdict is not Verdict.INSUFFICIENT_EVIDENCE


async def test_enum_condition_codes_forwards_allowed_as_the_codes_argument():
    criterion = _criterion(
        CriterionType.ENUM, {"fact": "condition_codes", "allowed": ["a", "b"]}
    )
    client = StubMemberClient(conditions=[])

    await verify_deterministic(criterion, CASE, client)

    assert client.calls[0] == ("conditions", MEMBER_ID, DATE_OF_SERVICE, ["a", "b"])


# === enum: coverage_active ===============================================================


async def test_enum_coverage_active_met_when_active_and_allowed():
    criterion = _criterion(CriterionType.ENUM, {"fact": "coverage_active", "allowed": ["active"]})
    client = StubMemberClient(coverage=CoverageStatus.ACTIVE)

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_enum_coverage_active_not_met_when_inactive():
    """Decision-3 row 4: a record exists and says not covered -- NOT_MET, because the
    record has an answer."""
    criterion = _criterion(CriterionType.ENUM, {"fact": "coverage_active", "allowed": ["active"]})
    client = StubMemberClient(coverage=CoverageStatus.INACTIVE)

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_enum_coverage_active_insufficient_evidence_when_no_record():
    """Decision-3 row 3: NO_RECORD means no record of this member at all."""
    criterion = _criterion(CriterionType.ENUM, {"fact": "coverage_active", "allowed": ["active"]})
    client = StubMemberClient(coverage=CoverageStatus.NO_RECORD)

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


# === enum: test_type =====================================================================


async def test_enum_test_type_met_by_any_study():
    criterion = _criterion(
        CriterionType.ENUM, {"fact": "test_type", "allowed": ["home_type_ii", "home_type_iii"]}
    )
    client = StubMemberClient(
        sleep_studies=[_study(study_id=1, test_type="home_type_iv"),
                       _study(study_id=2, test_type="home_type_iii")]
    )

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET
    assert result.evidence["matched_study"]["study_id"] == 2


async def test_enum_test_type_not_met_when_no_study_matches():
    criterion = _criterion(CriterionType.ENUM, {"fact": "test_type", "allowed": ["home_type_i"]})
    client = StubMemberClient(sleep_studies=[_study(test_type="home_type_iv")])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_enum_test_type_insufficient_evidence_when_no_studies():
    criterion = _criterion(CriterionType.ENUM, {"fact": "test_type", "allowed": ["home_type_i"]})
    client = StubMemberClient(sleep_studies=[])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


# === enum: no numeric-datatype fact exists yet (brief's conditional test) ===============


def test_no_enum_permitted_fact_has_a_numeric_datatype_yet():
    """The brief asks for an `enum` test against a numeric-datatype fact "if your
    reading of FactSpec permits one". It doesn't, today: every fact whose
    `permitted_types` includes `ENUM` (`test_type`, `condition_codes`,
    `coverage_active`) has `FactDataType.STRING`. Pinned as an executable check rather
    than left as a comment, so a future fact that adds `ENUM` to its `permitted_types`
    with a `NUMBER` datatype is caught here -- and is the reminder that
    `deterministic._verify_enum`'s dispatch table needs a new branch, not a silent gap.
    """
    numeric_enum_facts = [
        name
        for name, spec in FACTS.items()
        if CriterionType.ENUM in spec.permitted_types and spec.datatype is FactDataType.NUMBER
    ]
    assert numeric_enum_facts == []


def test_comparator_table_covers_every_threshold_operator():
    """Pins `deterministic._COMPARATORS` against `domain.params.THRESHOLD_OPERATORS`,
    the same closed set `validate_params` already checks a criterion's `operator`
    against. A sixth operator added to one without the other would otherwise fail
    silently -- either with a `KeyError` at verify time, or with an operator this
    module could never be asked to compare."""
    assert set(deterministic_module._COMPARATORS) == THRESHOLD_OPERATORS


# === temporal: window boundary, both directions =========================================


async def test_temporal_within_days_before_met_at_exact_window_boundary():
    criterion = _criterion(
        CriterionType.TEMPORAL,
        {"fact": "study_date", "operator": "within_days_before", "value": 365},
    )
    client = StubMemberClient(sleep_studies=[_study(days_before=365)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET


async def test_temporal_within_days_before_not_met_one_day_past_window():
    """The paired case: one day further back than the window allows. A mutation that
    changes the boundary's `<=` to `<` (or the reverse) fails one of this pair."""
    criterion = _criterion(
        CriterionType.TEMPORAL,
        {"fact": "study_date", "operator": "within_days_before", "value": 365},
    )
    client = StubMemberClient(sleep_studies=[_study(days_before=366)])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.NOT_MET


async def test_temporal_insufficient_evidence_when_no_studies():
    criterion = _criterion(
        CriterionType.TEMPORAL,
        {"fact": "study_date", "operator": "within_days_before", "value": 365},
    )
    client = StubMemberClient(sleep_studies=[])

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


async def test_temporal_met_by_any_qualifying_study():
    criterion = _criterion(
        CriterionType.TEMPORAL,
        {"fact": "study_date", "operator": "within_days_before", "value": 30},
    )
    client = StubMemberClient(
        sleep_studies=[_study(study_id=1, days_before=400), _study(study_id=2, days_before=10)]
    )

    result = await verify_deterministic(criterion, CASE, client)

    assert result.result.verdict is Verdict.MET
    assert result.evidence["matched_study"]["study_id"] == 2


# === the dispatcher: verify() ============================================================


async def test_verify_dispatches_deterministic_types_without_calling_the_model():
    criterion = _criterion(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})
    client = StubMemberClient(sleep_studies=[_study(ahi=20.0)])

    result = await verify(criterion, CASE, client, NeverCalledLLM())

    assert result.result.verdict is Verdict.MET


async def test_verify_dispatches_judgment_to_the_model():
    criterion = _criterion(CriterionType.JUDGMENT, {}, criterion_id=7)
    note_text = "Patient reports excessive daytime sleepiness most days."
    client = StubMemberClient(
        notes=[Note(id=1, encounter_id=None, date=DATE_OF_SERVICE, text=note_text)]
    )
    llm = StubLLM({
        "verdict": "met",
        "confidence": 0.9,
        "evidence_spans": ["excessive daytime sleepiness"],
    })

    result = await verify(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.MET
    assert llm.last_messages is not None


# === judgment ============================================================================


NOTE_TEXT = "Patient reports loud snoring and witnessed apneas per spouse."
NOTES = [Note(id=1, encounter_id=10, date=DATE_OF_SERVICE, text=NOTE_TEXT)]


async def test_judgment_met_with_a_grounded_span_carries_the_models_own_confidence():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "met",
        "confidence": 0.82,
        "evidence_spans": ["loud snoring and witnessed apneas"],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.MET
    # Judgment confidence is the model's own -- not forced to 1.0 like a deterministic
    # comparison, and not rounded or clamped away from what the model actually said.
    assert result.result.confidence == 0.82
    assert result.evidence["quoted_spans"] == ["loud snoring and witnessed apneas"]


async def test_judgment_not_met_with_a_grounded_span():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "not_met",
        "confidence": 0.7,
        "evidence_spans": ["loud snoring and witnessed apneas"],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.NOT_MET


async def test_judgment_insufficient_evidence_when_no_notes():
    """No notes is the judgment analogue of decision-3 row 1: no document to read, so
    the model is never even asked."""
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=[])
    llm = StubLLM({"verdict": "met", "confidence": 0.9, "evidence_spans": []})

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert result.result.confidence == 1.0
    assert llm.last_messages is None


async def test_judgment_malformed_response_is_insufficient_evidence_not_a_crash():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({"unexpected": "shape"})

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


async def test_judgment_out_of_range_confidence_is_insufficient_evidence():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "met", "confidence": 1.5,
        "evidence_spans": ["loud snoring and witnessed apneas"],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


async def test_judgment_unrecognised_verdict_string_is_insufficient_evidence():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "probably", "confidence": 0.9,
        "evidence_spans": ["loud snoring and witnessed apneas"],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE


async def test_judgment_ungrounded_span_does_not_support_a_verdict():
    """A quoted span that does not appear verbatim in the notes it was given is not
    evidence -- it is dropped, and since nothing grounds the model's claimed MET, the
    verdict is downgraded to INSUFFICIENT_EVIDENCE rather than trusted at face value."""
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "met", "confidence": 0.95,
        "evidence_spans": ["the patient underwent bariatric surgery in 2019"],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert result.evidence["ungrounded_spans"] == [
        "the patient underwent bariatric surgery in 2019"
    ]


async def test_judgment_keeps_grounded_spans_and_records_ungrounded_ones_separately():
    """A mix of one grounded and one fabricated span: the verdict stands (at least one
    real citation exists), the grounded span is the evidence, and the ungrounded one is
    kept alongside for a reviewer to see, not silently discarded."""
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({
        "verdict": "met", "confidence": 0.88,
        "evidence_spans": [
            "loud snoring and witnessed apneas",
            "the patient underwent bariatric surgery in 2019",
        ],
    })

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.MET
    assert result.evidence["quoted_spans"] == ["loud snoring and witnessed apneas"]
    assert result.evidence["ungrounded_spans"] == [
        "the patient underwent bariatric surgery in 2019"
    ]


async def test_judgment_model_reported_insufficient_evidence_is_kept_as_is():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=NOTES)
    llm = StubLLM({"verdict": "insufficient_evidence", "confidence": 0.4, "evidence_spans": []})

    result = await verify_judgment(criterion, CASE, client, llm)

    assert result.result.verdict is Verdict.INSUFFICIENT_EVIDENCE
    assert result.result.confidence == 0.4


async def test_judgment_upstream_unavailable_from_notes_propagates():
    """An upstream failure fetching notes must reach the pipeline as
    `UpstreamUnavailable`, not become a fabricated verdict."""
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=UpstreamUnavailable("member", "timed out"))
    llm = StubLLM({"verdict": "met", "confidence": 0.9, "evidence_spans": []})

    with pytest.raises(UpstreamUnavailable):
        await verify_judgment(criterion, CASE, client, llm)


async def test_judgment_upstream_unavailable_propagates_through_the_dispatcher_too():
    criterion = _criterion(CriterionType.JUDGMENT, {})
    client = StubMemberClient(notes=UpstreamUnavailable("member", "timed out"))
    llm = StubLLM({"verdict": "met", "confidence": 0.9, "evidence_spans": []})

    with pytest.raises(UpstreamUnavailable):
        await verify(criterion, CASE, client, llm)
