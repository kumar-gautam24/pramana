"""The scoring maths: pure, no I/O, and the part of this service a reviewer should read
first.

Two levels, because one of them is not enough. Criterion-level numbers say whether the
system read the policy correctly. Case-level numbers say what being wrong costs. A
project that reports only the first is measuring its own machinery; one that reports only
the second cannot say why it scored as it did."""

from dataclasses import dataclass

from pramana_common.criteria import Outcome


@dataclass(frozen=True)
class CostModel:
    """What being wrong costs, in each direction.

    These come from configuration (see `config.py`) rather than being written here,
    because the operating point this module recommends is a function of them and a reader
    who disagrees must be able to change them and re-run."""

    average_claim_amount: float
    review_minutes: float
    clinician_hourly_rate: float

    @property
    def review_cost(self) -> float:
        return self.review_minutes / 60.0 * self.clinician_hourly_rate


@dataclass(frozen=True)
class CaseOutcome:
    """One scored case: what a person said should happen, and what happened.

    `confidence` is the *minimum* confidence across the case's criteria -- the weakest
    link, because the gate approves only when every criterion clears the bar, so that is
    the value a threshold sweep must vary against. None when the case never reached a
    determination."""

    expected: Outcome
    actual: Outcome | None
    confidence: float | None


@dataclass(frozen=True)
class ConfusionCounts:
    """Deliberately not a 2x2 confusion matrix.

    A 2x2 would imply four symmetric cells. These four are not symmetric: a wrong
    approval costs money, a wrong escalation costs time, and there is no third kind of
    error because there is no deny path (ADR-0002). `unfinished` is separate from all of
    them -- a case the harness could not decide is not a case the system got wrong, and
    folding it in would let an outage read as a refusal."""

    correct_approve: int = 0
    correct_escalate: int = 0
    wrongly_approved: int = 0
    wrongly_escalated: int = 0
    unfinished: int = 0

    @property
    def decided(self) -> int:
        return (
            self.correct_approve
            + self.correct_escalate
            + self.wrongly_approved
            + self.wrongly_escalated
        )


def confusion(outcomes: list[CaseOutcome], min_confidence: float = 0.0) -> ConfusionCounts:
    """Count outcomes at a given confidence threshold.

    The threshold is applied here rather than only inside the gate so a single run can be
    scored at many thresholds without re-adjudicating anything -- which is what makes the
    sweep below affordable. An approval whose weakest criterion falls below the threshold
    becomes an escalation, exactly as the gate would have decided it."""
    counts = {
        "correct_approve": 0,
        "correct_escalate": 0,
        "wrongly_approved": 0,
        "wrongly_escalated": 0,
        "unfinished": 0,
    }

    for case in outcomes:
        if case.actual is None:
            counts["unfinished"] += 1
            continue

        actual = case.actual
        if (
            actual is Outcome.APPROVE
            and case.confidence is not None
            and case.confidence < min_confidence
        ):
            actual = Outcome.ESCALATE

        if actual is Outcome.APPROVE:
            key = "correct_approve" if case.expected is Outcome.APPROVE else "wrongly_approved"
        else:
            key = "correct_escalate" if case.expected is Outcome.ESCALATE else "wrongly_escalated"
        counts[key] += 1

    return ConfusionCounts(**counts)


@dataclass(frozen=True)
class CasePoint:
    """The case-level report at one threshold. Every money figure carries its unit by
    construction: a count multiplied by a rate, never a bare score."""

    min_confidence: float
    counts: ConfusionCounts
    auto_approval_rate: float
    wrongly_approved_cost: float
    wrongly_escalated_cost: float
    total_cost: float


def score_at(outcomes: list[CaseOutcome], min_confidence: float, costs: CostModel) -> CasePoint:
    counts = confusion(outcomes, min_confidence)
    approved = counts.correct_approve + counts.wrongly_approved

    # Denominator is decided cases, not all cases. A rate computed over cases the harness
    # never finished would fall when the provider rate-limits us, which would read as the
    # system becoming more cautious when nothing about it changed.
    rate = approved / counts.decided if counts.decided else 0.0

    wrongly_approved_cost = counts.wrongly_approved * costs.average_claim_amount
    wrongly_escalated_cost = counts.wrongly_escalated * costs.review_cost

    return CasePoint(
        min_confidence=min_confidence,
        counts=counts,
        auto_approval_rate=rate,
        wrongly_approved_cost=wrongly_approved_cost,
        wrongly_escalated_cost=wrongly_escalated_cost,
        total_cost=wrongly_approved_cost + wrongly_escalated_cost,
    )


def sweep(
    outcomes: list[CaseOutcome], costs: CostModel, steps: int = 21
) -> tuple[list[CasePoint], CasePoint | None]:
    """Total cost against the confidence threshold, and the cheapest point on it.

    This is the whole argument for whatever threshold the system ships with: the
    operating point is the minimum of a curve, which is a claim someone can check, rather
    than a number someone preferred.

    Ties break toward the *higher* threshold. Two thresholds costing the same are not
    equally good -- the stricter one approves less on the same evidence, and when the
    error it avoids is a wrongful approval affecting someone's care, the tie should not be
    settled by list order."""
    points = [
        score_at(outcomes, step / (steps - 1) if steps > 1 else 0.0, costs)
        for step in range(steps)
    ]
    if not points:
        return [], None

    best = min(points, key=lambda point: (point.total_cost, -point.min_confidence))
    return points, best


@dataclass(frozen=True)
class CriterionScore:
    """Extraction quality for one case, against a human-authored criteria list.

    Precision and recall rather than an accuracy figure: the two failures are different.
    Missing a criterion the policy contains can produce a wrongful approval; inventing
    one can only produce a wrongful escalation. A single number would hide which
    direction the system errs in, and they do not cost the same."""

    expected_count: int
    extracted_count: int
    matched_count: int

    @property
    def precision(self) -> float:
        return self.matched_count / self.extracted_count if self.extracted_count else 0.0

    @property
    def recall(self) -> float:
        return self.matched_count / self.expected_count if self.expected_count else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _normalise(text: str) -> set[str]:
    return {word for word in "".join(c.lower() if c.isalnum() else " " for c in text).split()}


def match_criteria(
    expected: list[str], extracted: list[str], overlap_threshold: float = 0.5
) -> CriterionScore:
    """Match extracted criteria to human-authored ones by token overlap.

    Exact string equality would score nearly zero on correct output: the model writes the
    policy's requirement in its own words, which is what it should do. Overlap is a blunt
    proxy for "these describe the same requirement" and is honest about being one -- the
    right instrument is a human reading both lists, and this number exists to tell you
    when to go and do that.

    Greedy one-to-one matching, so two extracted criteria cannot both claim the same
    expected one and inflate recall past what was actually found."""
    remaining = list(range(len(expected)))
    expected_tokens = [_normalise(text) for text in expected]
    matched = 0

    for candidate in extracted:
        candidate_tokens = _normalise(candidate)
        if not candidate_tokens:
            continue

        best_index, best_overlap = None, 0.0
        for index in remaining:
            reference = expected_tokens[index]
            if not reference:
                continue
            # Overlap relative to the human-authored criterion, not to the union: a model
            # that pads its wording must not be penalised for it, but one that omits half
            # the requirement must be.
            overlap = len(candidate_tokens & reference) / len(reference)
            if overlap > best_overlap:
                best_index, best_overlap = index, overlap

        if best_index is not None and best_overlap >= overlap_threshold:
            remaining.remove(best_index)
            matched += 1

    return CriterionScore(
        expected_count=len(expected), extracted_count=len(extracted), matched_count=matched
    )
