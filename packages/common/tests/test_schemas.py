from datetime import date

import pytest
from pydantic import ValidationError

from pramana_common.criteria import CriterionResult, CriterionType, GateReason, Outcome, Verdict
from pramana_common.gate import GateThresholds, evaluate_gate
from pramana_common.schemas import (
    CaseRequest,
    Criterion,
    CriterionOutcome,
    Determination,
    EvidenceSpan,
    Hit,
)


def _hit(**overrides) -> Hit:
    fields = {
        "chunk_id": 1902,
        "policy_id": 7,
        "display_id": "240.4",
        "heading_path": "Indications and Limitations > B. Nationally Covered Indications",
        "text": "AHI greater than or equal to 15 events per hour",
        "score": 4.2,
    }
    return Hit(**(fields | overrides))


def test_case_request_round_trips_through_json():
    request = CaseRequest(
        member_id="M1",
        requested_code="E0601",
        icd10="G47.33",
        date_of_service=date(2026, 3, 1),
        kind="initial",
    )

    assert CaseRequest.model_validate_json(request.model_dump_json()) == request


def test_case_request_rejects_an_unknown_kind():
    with pytest.raises(ValidationError):
        CaseRequest(
            member_id="M1",
            requested_code="E0601",
            icd10="G47.33",
            date_of_service=date(2026, 3, 1),
            kind="appeal",
        )


def test_determination_cannot_carry_a_denial():
    """Outcome is the shared enum, so the wire format inherits the two-outcome
    guarantee rather than restating it."""
    with pytest.raises(ValidationError):
        Determination(case_id=1, outcome="deny", blocking=[], reason=None, criteria=[])


def test_determination_accepts_escalation_with_blocking_criteria():
    determination = Determination(
        case_id=1,
        outcome=Outcome.ESCALATE,
        blocking=("C2",),
        reason="insufficient_evidence",
        criteria=(
            CriterionOutcome(
                criterion_id="C2",
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                confidence=0.5,
                tool="retrieval+llm",
                evidence=(EvidenceSpan(source="note:41", locator="p3", text="no PT documented"),),
            ),
        ),
    )

    assert determination.criteria[0].evidence[0].source == "note:41"


def test_determination_reason_cannot_read_as_a_denial():
    """The reason is what a clinician sees next to the case. A free-text field would let
    a caller put a denial in front of a reviewer without touching Outcome at all."""
    with pytest.raises(ValidationError):
        Determination(case_id=1, outcome=Outcome.ESCALATE, reason="denied_by_policy")


def test_an_approval_cannot_carry_blocking_criteria():
    """The gate has no branch that approves while something blocks. The wire format must
    not be able to express one, or a service could hand-build an approval that never
    passed the gate."""
    with pytest.raises(ValidationError):
        Determination(
            case_id=1,
            outcome=Outcome.APPROVE,
            blocking=("C2",),
            reason=GateReason.CRITERION_NOT_MET,
        )


def test_an_escalation_must_carry_a_reason():
    with pytest.raises(ValidationError):
        Determination(case_id=1, outcome=Outcome.ESCALATE, blocking=("C2",))


def test_from_gate_decision_round_trips_an_approval():
    decision = evaluate_gate(
        [CriterionResult("C1", Verdict.MET, 0.9)], GateThresholds(min_confidence=0.7)
    )

    determination = Determination.from_gate_decision(
        case_id=7,
        decision=decision,
        criteria=[
            CriterionOutcome(criterion_id="C1", verdict=Verdict.MET, confidence=0.9, tool="sql")
        ],
    )

    assert determination.case_id == 7
    assert determination.outcome is Outcome.APPROVE
    assert determination.blocking == ()
    assert determination.reason is None
    assert determination.criteria[0].criterion_id == "C1"


def test_from_gate_decision_round_trips_an_escalation():
    decision = evaluate_gate(
        [
            CriterionResult("C1", Verdict.MET, 0.9),
            CriterionResult("C2", Verdict.NOT_MET, 0.9),
        ],
        GateThresholds(min_confidence=0.7),
    )

    determination = Determination.from_gate_decision(
        case_id=7,
        decision=decision,
        criteria=[
            CriterionOutcome(criterion_id="C1", verdict=Verdict.MET, confidence=0.9, tool="sql"),
            CriterionOutcome(
                criterion_id="C2", verdict=Verdict.NOT_MET, confidence=0.9, tool="sql"
            ),
        ],
    )

    assert determination.outcome is Outcome.ESCALATE
    assert determination.blocking == ("C2",)
    assert determination.reason is GateReason.CRITERION_NOT_MET


def test_determination_blocking_cannot_be_mutated_in_place():
    """Determinations are recorded on the audit trail. `frozen=True` alone would leave a
    list field appendable, so the sequence fields are tuples."""
    determination = Determination(
        case_id=1,
        outcome=Outcome.ESCALATE,
        blocking=("C2",),
        reason=GateReason.CRITERION_NOT_MET,
    )

    with pytest.raises(AttributeError):
        determination.blocking.append("C9")


def test_hit_round_trips_through_json():
    """A Hit crosses the wire from policy to adjudication, so JSON is its real form."""
    hit = _hit()

    assert Hit.model_validate_json(hit.model_dump_json()) == hit


def test_hit_cannot_be_mutated_after_construction():
    """A hit is the cited evidence behind a determination and is recorded as such.
    Rewriting its text or its score after the fact would rewrite the audit trail."""
    hit = _hit()

    with pytest.raises(ValidationError):
        hit.score = 9.9


@pytest.mark.parametrize("score", [-11.5, -1.0, 0.0, 1.0, 8.75])
def test_hit_score_is_unbounded_in_both_directions(score):
    """The cross-encoder emits a raw logit, not a probability: negative scores are the
    normal case for a poor match, and strong matches run well above 1. Constraining this
    to [0, 1] later would reject valid hits from a service that never changed -- so the
    absence of a bound is asserted here rather than left to be discovered."""
    assert _hit(score=score).score == pytest.approx(score)


def test_criterion_carries_the_chunk_it_came_from():
    """A criterion with no source chunk cannot be shown to a reviewer as policy text,
    so the field is required rather than optional."""
    with pytest.raises(ValidationError):
        Criterion(
            id="C1",
            ordinal=1,
            text="at least 30 apnea events",
            type=CriterionType.THRESHOLD,
            params={"field": "apnea_events", "op": ">=", "value": 30},
        )
