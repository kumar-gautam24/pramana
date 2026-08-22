"""Comparing a run against its ablated twin — pure, no I/O.

[ADR-0021](../../../../../docs/decisions/0021-model-arithmetic-as-a-run-mode.md) built the
ablation as one column and one module so that *a run and its twin differ in one thing and
nothing else*. That discipline is enforced inside a case. This module enforces it between two
runs, which is where it can actually be violated by accident: nothing stops an operator
opening a run made on one model beside a run made on another and reading the difference as
the cost of model arithmetic.

So the comparison is refused rather than produced when the two runs differ in anything but
their ablation. Not refused as an error — both runs' own figures are still returned, because
each is individually valid — but the **delta is withheld**, because a delta is a claim about
causation and there is no causal claim to make between two runs that differ in three ways.

The same argument, one level down, decides the denominator. Two runs may have scored different
subsets of the golden set: one timed out on a case, one was capped, one was resumed. Comparing
their headline figures directly would let a run that failed on its two hardest cases look
cheaper than the run that finished them. Everything here is therefore computed over the
**intersection** — the golden cases both runs actually decided — and the cases only one of them
reached are listed separately rather than dropped silently.
"""

from dataclasses import dataclass

from pramana_common.criteria import Outcome

from evals.domain.scoring import CaseOutcome, CasePoint
from evals.models.run import Ablation, EvalRun

#: Everything a run records about the conditions it ran under, except the ablation itself.
#: Two runs whose values here are identical differ in exactly one thing, which is what makes
#: their difference attributable. `status` and the timestamps are deliberately absent: when a
#: run happened says nothing about what it measured, and a still-running twin is a partial
#: comparison rather than an invalid one.
COMPARABLE_FIELDS = ("model", "prompt_version", "git_sha", "thresholds")


@dataclass(frozen=True)
class Pairing:
    """Which of two runs is the baseline, and whether they are a pair at all."""

    baseline: EvalRun
    ablated: EvalRun
    #: Fields other than `ablation` on which the two disagree. Empty means comparable.
    differs_in: tuple[str, ...]
    #: Set when the two are not an ablation pair at all — both ablated, or neither.
    not_a_pair: str | None

    @property
    def comparable(self) -> bool:
        return not self.differs_in and self.not_a_pair is None


def pair(first: EvalRun, second: EvalRun) -> Pairing:
    """Orient two runs as (baseline, ablated) and say what stands between them.

    Orientation is read off `ablation`, never off argument order: a caller asking "compare
    run 7 against run 4" should get the same answer whichever way round they name them, and
    a comparison whose sign depended on the URL would be a trap.
    """
    if first.ablation is second.ablation:
        # Both `none` or both `model_arithmetic`. Two runs of the same arrangement are a
        # useful thing to look at -- run-to-run variance is real -- but they are not an
        # ablation, and calling their difference one would be the whole error this module
        # exists to prevent.
        baseline, ablated = first, second
        not_a_pair = (
            f"both runs have ablation {first.ablation.value!r}; an ablation compares a run "
            "against one that differs in that column and nothing else"
        )
    else:
        baseline, ablated = (
            (first, second) if first.ablation is Ablation.NONE else (second, first)
        )
        not_a_pair = None

    differs = tuple(
        field
        for field in COMPARABLE_FIELDS
        if getattr(baseline, field) != getattr(ablated, field)
    )
    return Pairing(baseline=baseline, ablated=ablated, differs_in=differs, not_a_pair=not_a_pair)


@dataclass(frozen=True)
class Delta:
    """Ablated minus baseline, over the cases both runs decided.

    Signed, and the sign means something: a positive `total_cost` is what the ablation cost,
    which is the number ADR-0003 has been promising since the first week. A negative one would
    be the ablation *winning*, and the report must be as able to say that as the reverse — an
    experiment whose result shape only admits one answer is not an experiment.
    """

    correct_approve: int
    correct_escalate: int
    wrongly_approved: int
    wrongly_escalated: int
    unfinished: int
    auto_approval_rate: float
    wrongly_approved_cost: float
    wrongly_escalated_cost: float
    total_cost: float


def delta(baseline: CasePoint, ablated: CasePoint) -> Delta:
    return Delta(
        correct_approve=ablated.counts.correct_approve - baseline.counts.correct_approve,
        correct_escalate=ablated.counts.correct_escalate - baseline.counts.correct_escalate,
        wrongly_approved=ablated.counts.wrongly_approved - baseline.counts.wrongly_approved,
        wrongly_escalated=ablated.counts.wrongly_escalated - baseline.counts.wrongly_escalated,
        unfinished=ablated.counts.unfinished - baseline.counts.unfinished,
        auto_approval_rate=ablated.auto_approval_rate - baseline.auto_approval_rate,
        wrongly_approved_cost=ablated.wrongly_approved_cost - baseline.wrongly_approved_cost,
        wrongly_escalated_cost=ablated.wrongly_escalated_cost - baseline.wrongly_escalated_cost,
        total_cost=ablated.total_cost - baseline.total_cost,
    )


@dataclass(frozen=True)
class Disagreement:
    """One golden case the two arms decided differently.

    For a golden set of the size this project has, these *are* the finding: a delta of one
    wrongful approval is a statistic, and "on case 7 the model said 14.446 was at least 15" is
    the thing a reader can check. `expected` is carried so a reader can see which arm was
    wrong without opening a third page — and both arms can be wrong at once, which is why this
    is not a boolean.
    """

    golden_case_id: int
    expected: Outcome
    baseline: Outcome | None
    ablated: Outcome | None


def disagreements(
    expected: dict[int, Outcome],
    baseline: dict[int, CaseOutcome],
    ablated: dict[int, CaseOutcome],
) -> list[Disagreement]:
    """Cases both runs reached and decided differently, in golden-case order.

    Only over the intersection. A case one run never finished is not a disagreement between
    the arms; it is a hole in one of them, and it is reported as such elsewhere."""
    shared = sorted(set(baseline) & set(ablated) & set(expected))
    return [
        Disagreement(
            golden_case_id=case_id,
            expected=expected[case_id],
            baseline=baseline[case_id].actual,
            ablated=ablated[case_id].actual,
        )
        for case_id in shared
        if baseline[case_id].actual is not ablated[case_id].actual
    ]
