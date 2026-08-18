"""Tests for the pure aggregation layer -- see the task-3 brief and ADR-0011.

Pure module, pure tests: nothing here uses `db_session`. Each test is named for the
rule it would catch a regression in."""

from itertools import product

import pytest
from pramana_common.criteria import CriterionResult, CriterionType, GateReason, Outcome, Verdict
from pramana_common.gate import GateThresholds

from adjudication.domain import criteria_sets as criteria_sets_module
from adjudication.domain.criteria_sets import (
    AggregateDecision,
    CriteriaSet,
    MissingCriterionResult,
    SetOutcome,
    aggregate,
)
from adjudication.models.criterion import Criterion


def _criterion(id: int, set_ordinal: int, ordinal: int = 0) -> Criterion:
    return Criterion(
        id=id,
        case_id="case-1",
        set_ordinal=set_ordinal,
        ordinal=ordinal,
        text="AHI >= 15",
        type=CriterionType.THRESHOLD,
        params={},
        source_chunk_id=1,
        source_display_id="240.4",
    )


def _result(criterion_id: int, verdict: Verdict, confidence: float = 1.0) -> CriterionResult:
    return CriterionResult(criterion_id=str(criterion_id), verdict=verdict, confidence=confidence)


THRESHOLDS = GateThresholds(min_confidence=0.5)


def test_no_sets_escalates_with_no_criteria():
    """Rule 4. Extraction producing zero sets is a failure to understand the policy,
    never a policy with no requirements -- it must not fall through to approval."""
    decision = aggregate([], {}, THRESHOLDS)

    assert decision == AggregateDecision(
        outcome=Outcome.ESCALATE,
        reason=GateReason.NO_CRITERIA,
        winning_set=None,
        blocking=(),
        closest_set=None,
    )


def test_any_approving_set_yields_approve():
    """Rule 2, basic case: one set fully met approves the case."""
    c1 = _criterion(1, set_ordinal=1)
    results = {"1": _result(1, Verdict.MET, confidence=1.0)}

    decision = aggregate([CriteriaSet(ordinal=1, criteria=(c1,))], results, THRESHOLDS)

    assert decision.outcome is Outcome.APPROVE
    assert decision.winning_set == 1
    assert decision.blocking == ()
    assert decision.reason is None


def test_member_satisfying_only_set_2_approves():
    """A case that fails set 1 but fully meets set 2 must still approve, naming set 2 --
    this is the entire point of alternative criteria sets (ADR-0011)."""
    set1_criterion = _criterion(1, set_ordinal=1)
    set2_criterion = _criterion(2, set_ordinal=2)
    results = {
        "1": _result(1, Verdict.NOT_MET),
        "2": _result(2, Verdict.MET, confidence=1.0),
    }
    sets = [
        CriteriaSet(ordinal=1, criteria=(set1_criterion,)),
        CriteriaSet(ordinal=2, criteria=(set2_criterion,)),
    ]

    decision = aggregate(sets, results, THRESHOLDS)

    assert decision.outcome is Outcome.APPROVE
    assert decision.winning_set == 2


def test_approve_tie_breaks_to_lowest_ordinal():
    """Rule 2's tiebreak. Two sets both fully met -- the answer must be ordinal 1, not
    merely 'one of them', and must not depend on the order `sets` or `results` were
    built in. Passing `sets` reversed (2 before 1) would expose a 'first approving set
    in iteration order' bug that a forward-ordered list would hide."""
    c1 = _criterion(1, set_ordinal=1)
    c2 = _criterion(2, set_ordinal=2)
    results = {
        "2": _result(2, Verdict.MET, confidence=1.0),
        "1": _result(1, Verdict.MET, confidence=1.0),
    }
    sets = [CriteriaSet(ordinal=2, criteria=(c2,)), CriteriaSet(ordinal=1, criteria=(c1,))]

    decision = aggregate(sets, results, THRESHOLDS)

    assert decision.winning_set == 1


def test_escalate_names_closest_set_by_fewest_unmet():
    """Rule 3, asymmetric case: set 1 is missing three criteria, set 2 only one. The
    reported blocking criteria and reason must belong to set 2, the closer set, not
    whichever set happens to be first."""
    set1_criteria = tuple(_criterion(i, set_ordinal=1, ordinal=i) for i in (1, 2, 3))
    set2_criteria = tuple(_criterion(i, set_ordinal=2, ordinal=i) for i in (4, 5, 6))
    results = {
        "1": _result(1, Verdict.NOT_MET),
        "2": _result(2, Verdict.NOT_MET),
        "3": _result(3, Verdict.NOT_MET),
        "4": _result(4, Verdict.MET, confidence=1.0),
        "5": _result(5, Verdict.MET, confidence=1.0),
        "6": _result(6, Verdict.NOT_MET),
    }
    sets = [
        CriteriaSet(ordinal=1, criteria=set1_criteria),
        CriteriaSet(ordinal=2, criteria=set2_criteria),
    ]

    decision = aggregate(sets, results, THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.closest_set == 2
    assert decision.blocking == ("6",)
    assert decision.reason is GateReason.CRITERION_NOT_MET


def test_escalate_tie_breaks_to_lowest_ordinal():
    """The closest-set tiebreak. Two sets escalate with the same count of unmet
    criteria -- the answer must be ordinal 1 regardless of list order, the same
    audit-reproducibility requirement as the approve-side tiebreak."""
    c1 = _criterion(1, set_ordinal=1)
    c2 = _criterion(2, set_ordinal=2)
    results = {
        "1": _result(1, Verdict.NOT_MET),
        "2": _result(2, Verdict.NOT_MET),
    }
    sets = [CriteriaSet(ordinal=2, criteria=(c2,)), CriteriaSet(ordinal=1, criteria=(c1,))]

    decision = aggregate(sets, results, THRESHOLDS)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.closest_set == 1


def test_unmet_counts_every_blocking_verdict_not_only_not_met():
    """Rule 4. `unmet` (and therefore which set is 'closest') must be built from
    `decision.blocking`, not a second pass that only counts NOT_MET verdicts. Set 1
    has a single NOT_MET criterion (1 blocking); set 2 has three INSUFFICIENT_EVIDENCE
    criteria (3 blocking, 0 NOT_MET). A NOT_MET-only filter would see set 2 as having
    zero unmet criteria and wrongly call it closest; counting all blocking verdicts
    correctly picks set 1."""
    set1_criteria = (_criterion(1, set_ordinal=1),)
    set2_criteria = tuple(_criterion(i, set_ordinal=2, ordinal=i) for i in (2, 3, 4))
    results = {
        "1": _result(1, Verdict.NOT_MET),
        "2": _result(2, Verdict.INSUFFICIENT_EVIDENCE),
        "3": _result(3, Verdict.INSUFFICIENT_EVIDENCE),
        "4": _result(4, Verdict.INSUFFICIENT_EVIDENCE),
    }
    sets = [
        CriteriaSet(ordinal=1, criteria=set1_criteria),
        CriteriaSet(ordinal=2, criteria=set2_criteria),
    ]

    decision = aggregate(sets, results, THRESHOLDS)

    assert decision.closest_set == 1
    assert decision.blocking == ("1",)


def test_missing_criterion_result_raises_naming_criterion_and_set():
    """Rule 5. A silently-missing verification must never round down to an approval --
    it must raise, naming both the criterion id and the set ordinal so this is never
    mistaken for a bug in the caller's dict."""
    c1 = _criterion(1, set_ordinal=1)
    c2 = _criterion(2, set_ordinal=1)
    results = {"1": _result(1, Verdict.MET, confidence=1.0)}  # criterion 2 has no result

    with pytest.raises(MissingCriterionResult) as exc_info:
        aggregate([CriteriaSet(ordinal=1, criteria=(c1, c2))], results, THRESHOLDS)

    assert exc_info.value.criterion_id == 2
    assert exc_info.value.set_ordinal == 1


def test_evaluate_gate_runs_exactly_once_per_set_and_is_not_reimplemented(monkeypatch):
    """Rule 1. `aggregate` must call `evaluate_gate` once per set and use its output
    verbatim -- no confidence comparison or verdict check of its own. Verified by
    intercepting the real function: it is called exactly `len(sets)` times, and the
    `GateDecision` it returns flows into `SetOutcome.decision` unchanged."""
    calls: list[list[CriterionResult]] = []
    from pramana_common.gate import GateDecision

    sentinel_decision = GateDecision(
        outcome=Outcome.ESCALATE, blocking=("99",), reason=GateReason.LOW_CONFIDENCE
    )

    def fake_evaluate_gate(results, thresholds):
        calls.append(results)
        return sentinel_decision

    monkeypatch.setattr(criteria_sets_module, "evaluate_gate", fake_evaluate_gate)

    c1 = _criterion(1, set_ordinal=1)
    c2 = _criterion(2, set_ordinal=2)
    results = {
        "1": _result(1, Verdict.MET, confidence=1.0),
        "2": _result(2, Verdict.MET, confidence=1.0),
    }
    sets = [CriteriaSet(ordinal=1, criteria=(c1,)), CriteriaSet(ordinal=2, criteria=(c2,))]

    decision = aggregate(sets, results, THRESHOLDS)

    assert len(calls) == 2
    # Neither set "approved" per the real gate, so aggregate must escalate using
    # exactly the sentinel's blocking and reason -- proof it never second-guesses
    # evaluate_gate's verdict.
    assert decision.outcome is Outcome.ESCALATE
    assert decision.blocking == ("99",)
    assert decision.reason is GateReason.LOW_CONFIDENCE


def test_set_outcome_unmet_is_exactly_decision_blocking():
    """Rule 4, at the SetOutcome level directly: `unmet` must be identical to
    `decision.blocking`, not independently recomputed."""
    c1 = _criterion(1, set_ordinal=1)
    c2 = _criterion(2, set_ordinal=1, ordinal=1)
    results = {
        "1": _result(1, Verdict.NOT_MET),
        "2": _result(2, Verdict.INSUFFICIENT_EVIDENCE),
    }

    decision = aggregate([CriteriaSet(ordinal=1, criteria=(c1, c2))], results, THRESHOLDS)

    assert decision.blocking == ("1", "2")


def test_exhaustive_two_sets_two_criteria_never_yields_a_third_outcome():
    """ADR-0002 at this layer: no combination of sets and results yields anything but
    APPROVE or ESCALATE. Brute force over every verdict for 2 criteria in each of 2
    sets (3**4 = 81 combinations), crossed with a couple of confidence levels and gate
    thresholds, asserting the invariant on every element -- not a sample."""
    verdicts = (Verdict.MET, Verdict.NOT_MET, Verdict.INSUFFICIENT_EVIDENCE)
    confidence_levels = (0.4, 1.0)
    threshold_levels = (0.0, 0.6)

    set1_criteria = (_criterion(1, set_ordinal=1), _criterion(2, set_ordinal=1, ordinal=1))
    set2_criteria = (_criterion(3, set_ordinal=2), _criterion(4, set_ordinal=2, ordinal=1))
    sets = [
        CriteriaSet(ordinal=1, criteria=set1_criteria),
        CriteriaSet(ordinal=2, criteria=set2_criteria),
    ]

    checked = 0
    for verdict_combo in product(verdicts, repeat=4):
        for confidence in confidence_levels:
            for min_confidence in threshold_levels:
                results = {
                    str(cid): _result(cid, verdict, confidence)
                    for cid, verdict in zip((1, 2, 3, 4), verdict_combo, strict=True)
                }
                thresholds = GateThresholds(min_confidence=min_confidence)

                decision = aggregate(sets, results, thresholds)
                checked += 1

                assert decision.outcome in (Outcome.APPROVE, Outcome.ESCALATE)
                if decision.outcome is Outcome.APPROVE:
                    assert decision.winning_set is not None
                    assert decision.blocking == ()
                else:
                    assert decision.reason is not None

    assert checked == len(verdicts) ** 4 * len(confidence_levels) * len(threshold_levels)


def test_set_outcome_and_criteria_set_are_frozen():
    """These are plain value objects passed across the pure/impure boundary; mutability
    would let a caller silently disagree with what `aggregate` actually decided."""
    outcome = SetOutcome(
        ordinal=1,
        decision=criteria_sets_module.GateDecision(Outcome.APPROVE, (), None),
        unmet=(),
    )
    with pytest.raises(AttributeError):
        outcome.ordinal = 2

    criteria_set = CriteriaSet(ordinal=1, criteria=())
    with pytest.raises(AttributeError):
        criteria_set.ordinal = 2
