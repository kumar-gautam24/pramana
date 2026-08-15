from dataclasses import FrozenInstanceError

import pytest

from pramana_common.criteria import (
    DETERMINISTIC_TYPES,
    CriterionResult,
    CriterionType,
    Outcome,
    Verdict,
)


def test_outcome_has_exactly_two_members():
    """ADR-0002: the system approves or escalates. A third outcome is a denial by
    another name, so this test is the guard on that invariant."""
    assert set(Outcome) == {Outcome.APPROVE, Outcome.ESCALATE}


def test_no_outcome_resembles_a_denial():
    assert not any("deny" in o.value or "denial" in o.value or "reject" in o.value for o in Outcome)


def test_judgment_is_the_only_non_deterministic_type():
    """ADR-0003: everything except JUDGMENT is compared by code, never by a model."""
    assert DETERMINISTIC_TYPES == frozenset(
        {CriterionType.THRESHOLD, CriterionType.ENUM, CriterionType.TEMPORAL}
    )
    assert CriterionType.JUDGMENT not in DETERMINISTIC_TYPES


def test_criterion_result_is_frozen():
    """Results are recorded on the audit trail, so a caller must not be able to amend one
    after the gate has read it."""
    result = CriterionResult(criterion_id="C1", verdict=Verdict.MET, confidence=0.9)
    with pytest.raises(FrozenInstanceError):
        result.confidence = 0.1


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_outside_zero_to_one_is_rejected(confidence):
    with pytest.raises(ValueError, match="confidence"):
        CriterionResult(criterion_id="C1", verdict=Verdict.MET, confidence=confidence)


def test_nan_confidence_is_rejected():
    """NaN compares false against every threshold, so it would silently pass a gate
    check that was meant to fail. Reject it at construction."""
    with pytest.raises(ValueError, match="confidence"):
        CriterionResult(criterion_id="C1", verdict=Verdict.MET, confidence=float("nan"))
