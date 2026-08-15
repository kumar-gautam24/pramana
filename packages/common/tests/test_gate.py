import itertools

import pytest

from pramana_common.criteria import CriterionResult, Outcome, Verdict
from pramana_common.gate import GateDecision, GateThresholds, evaluate_gate

THRESHOLDS = GateThresholds(min_confidence=0.7)


def met(criterion_id: str, confidence: float = 0.9) -> CriterionResult:
    return CriterionResult(criterion_id=criterion_id, verdict=Verdict.MET, confidence=confidence)


def test_all_criteria_met_and_confident_approves():
    decision = evaluate_gate([met("C1"), met("C2")], THRESHOLDS)

    assert decision == GateDecision(outcome=Outcome.APPROVE, blocking=(), reason=None)


def test_a_single_unmet_criterion_escalates():
    results = [met("C1"), CriterionResult("C2", Verdict.NOT_MET, 0.95)]

    decision = evaluate_gate(results, THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ("C2",)
    assert decision.reason == "criterion_not_met"


def test_insufficient_evidence_escalates():
    results = [met("C1"), CriterionResult("C2", Verdict.INSUFFICIENT_EVIDENCE, 0.9)]

    decision = evaluate_gate(results, THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ("C2",)
    assert decision.reason == "insufficient_evidence"


def test_met_but_below_confidence_threshold_escalates():
    """A criterion the model believes is met, but is not sure about, is not grounds to
    approve. Being unsure is exactly what a human is for."""
    decision = evaluate_gate([met("C1"), met("C2", confidence=0.4)], THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ("C2",)
    assert decision.reason == "low_confidence"


def test_no_criteria_escalates():
    """An empty criteria list means policy decomposition produced nothing. That is a
    failure to understand the policy, not a policy with no requirements."""
    decision = evaluate_gate([], THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ()
    assert decision.reason == "no_criteria"


def test_definitive_failure_is_reported_before_missing_evidence():
    """Ordering matters to the reviewer. NOT_MET means the record contradicts the policy
    and the case likely warrants denial; INSUFFICIENT_EVIDENCE means go find a document.
    Report the actionable one first."""
    results = [
        CriterionResult("C1", Verdict.INSUFFICIENT_EVIDENCE, 0.9),
        CriterionResult("C2", Verdict.NOT_MET, 0.9),
    ]

    decision = evaluate_gate(results, THRESHOLDS)

    assert decision.reason == "criterion_not_met"


def test_all_blocking_criteria_are_reported_in_input_order():
    results = [
        CriterionResult("C1", Verdict.NOT_MET, 0.9),
        met("C2"),
        CriterionResult("C3", Verdict.INSUFFICIENT_EVIDENCE, 0.9),
    ]

    decision = evaluate_gate(results, THRESHOLDS)

    assert decision.blocking == ("C1", "C3")


def test_no_combination_of_inputs_can_produce_anything_but_approve_or_escalate():
    """ADR-0002 as an executable guarantee. Exhaust every verdict and confidence
    combination for up to three criteria; the gate must never invent a third outcome."""
    confidences = (0.0, 0.5, 1.0)
    for length in (1, 2, 3):
        for verdicts in itertools.product(Verdict, repeat=length):
            for confs in itertools.product(confidences, repeat=length):
                results = [
                    CriterionResult(f"C{i}", verdict, conf)
                    for i, (verdict, conf) in enumerate(zip(verdicts, confs, strict=True))
                ]
                decision = evaluate_gate(results, THRESHOLDS)
                assert decision.outcome in (Outcome.APPROVE, Outcome.ESCALATE)


def test_approval_requires_every_criterion_to_be_met():
    """The converse of the invariant above: approval is only ever reachable when nothing
    blocks. This is what stops a future 'majority met' shortcut passing review."""
    for verdicts in itertools.product(Verdict, repeat=3):
        results = [CriterionResult(f"C{i}", v, 1.0) for i, v in enumerate(verdicts)]
        decision = evaluate_gate(results, THRESHOLDS)
        expected = Outcome.APPROVE if all(v is Verdict.MET for v in verdicts) else Outcome.ESCALATE
        assert decision.outcome is expected


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_threshold_is_rejected(bad):
    """Every eval run persists its thresholds as JSON so the run can be reproduced, and
    JSON has no infinity."""
    with pytest.raises(ValueError, match="min_confidence"):
        GateThresholds(min_confidence=bad)


@pytest.mark.parametrize("bad", [-0.5, 1.5])
def test_threshold_outside_zero_to_one_is_rejected(bad):
    with pytest.raises(ValueError, match="min_confidence"):
        GateThresholds(min_confidence=bad)


def test_confidence_exactly_at_threshold_approves():
    """The threshold is the minimum acceptable confidence, so meeting it exactly is
    meeting it. A confidence of 0.7 against min_confidence=0.7 should approve."""
    decision = evaluate_gate([met("C1", confidence=0.7)], THRESHOLDS)

    assert decision.outcome is Outcome.APPROVE
    assert decision.blocking == ()
    assert decision.reason is None


def test_confidence_just_below_threshold_escalates():
    """A confidence of 0.6999 against min_confidence=0.7 falls below the boundary
    and must escalate."""
    decision = evaluate_gate([met("C1", confidence=0.6999)], THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ("C1",)
    assert decision.reason == "low_confidence"


def test_default_thresholds_disable_confidence_check():
    """The default min_confidence=0.0 disables the confidence check by design. Any
    MET criterion with any valid confidence (including 0.0) will approve under
    default thresholds. This is fail-open behavior for decomposition failures."""
    default_thresholds = GateThresholds()
    decision = evaluate_gate([CriterionResult("C1", Verdict.MET, 0.0)], default_thresholds)

    assert decision.outcome is Outcome.APPROVE
    assert decision.blocking == ()
    assert decision.reason is None
