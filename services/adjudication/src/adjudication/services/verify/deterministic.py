"""Verifiers for `threshold`, `enum`, and `temporal` criteria -- the three
`DETERMINISTIC_TYPES`. Every comparison in this module is a plain Python `if`,
operator, or membership test; nothing here is ever handed to a model. See the
package docstring (`adjudication.services.verify`) for the member-exists invariant
every function below relies on.

Two rules apply everywhere a fact is fetched from `member`:

- A **missing fact** (no sleep studies at all, no coverage record, no adherence data
  uploaded) is `INSUFFICIENT_EVIDENCE`: there is no document to read. A fact that
  *exists and contradicts the criterion* (a study whose AHI falls short, a condition
  list that doesn't contain the code, coverage recorded as inactive) is `NOT_MET`: the
  record has an answer, and the answer is no. Conflating the two would tell a reviewer
  to go looking for a document that was never missing.
- When a fact can come from more than one sleep study, the criterion is `MET` if
  **any** study before the date of service satisfies the comparison. There is no
  "most recent study" rule -- recency, if a policy ever needs it, is its own
  `temporal` criterion on `study_date`, which is exactly why `temporal` exists as a
  separate type rather than being folded into `threshold`.

Every verdict here carries confidence exactly `1.0`. A comparison this module performs
is exact; anything less would misrepresent certainty the code actually has, and the
gate thresholds on confidence, so a value below `1.0` could suppress a true `MET`.

Pure apart from the two injected calls to `member_client`; no model, no database."""

import operator as _op
from collections.abc import Callable
from datetime import timedelta

from pramana_common.criteria import CriterionResult, CriterionType, Verdict

from adjudication.models.case import Case
from adjudication.models.criterion import Criterion
from adjudication.services.member_client import CoverageStatus, MemberClient
from adjudication.services.verify import Verification

#: `adherence_fraction`/`adherence_nights` come from one `MemberClient.adherence` call
#: rather than the sleep-study endpoint; this is the field each reads off `Adherence`.
#: `adherence_nights` reads `qualifying_nights` (nights meeting the policy's
#: `min_hours` bar), not `nights` (nights any usage was logged at all) -- `nights` is
#: instead the signal for the INSUFFICIENT_EVIDENCE row below: zero of *those* means no
#: usage data was ever uploaded, which is a different fact than "the member used the
#: device on zero qualifying nights".
_ADHERENCE_FIELDS = {"adherence_fraction": "fraction", "adherence_nights": "qualifying_nights"}

#: `THRESHOLD_OPERATORS` is the closed set `domain.params` already validated
#: `criterion.params["operator"]` against; mapping it to `operator` module callables
#: here, once, is what makes a `>=`-for-`>` typo in this module a one-line diff to
#: find rather than a bug hiding in a chain of `if`/`elif`.
_COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    ">=": _op.ge,
    ">": _op.gt,
    "<=": _op.le,
    "<": _op.lt,
    "==": _op.eq,
}


def _build(criterion: Criterion, verdict: Verdict, confidence: float, tool: str,
           evidence: dict) -> Verification:
    return Verification(
        result=CriterionResult(
            criterion_id=str(criterion.id), verdict=verdict, confidence=confidence
        ),
        tool=tool,
        evidence=evidence,
    )


def _insufficient(criterion: Criterion, tool: str, evidence: dict) -> Verification:
    return _build(criterion, Verdict.INSUFFICIENT_EVIDENCE, 1.0, tool, evidence)


# --- threshold -----------------------------------------------------------------------


async def _verify_sleep_study_threshold(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    threshold = criterion.params["value"]
    compare = _COMPARATORS[operator_name]
    tool = f"threshold:{fact}"

    studies = await member_client.sleep_studies(case.member_id, case.date_of_service)
    if not studies:
        return _insufficient(criterion, tool, {
            "fact": fact,
            "operator": operator_name,
            "threshold": threshold,
            "reason": "no sleep studies on record before the date of service",
        })

    checked = [
        {"study_id": s.id, "date": s.date.isoformat(), "value": getattr(s, fact)} for s in studies
    ]
    matched = next((s for s in studies if compare(getattr(s, fact), threshold)), None)
    verdict = Verdict.MET if matched is not None else Verdict.NOT_MET
    evidence = {
        "fact": fact,
        "operator": operator_name,
        "threshold": threshold,
        "matched_study": (
            {
                "study_id": matched.id,
                "date": matched.date.isoformat(),
                "value": getattr(matched, fact),
            }
            if matched is not None
            else None
        ),
        "studies_checked": checked,
    }
    return _build(criterion, verdict, 1.0, tool, evidence)


async def _verify_adherence_threshold(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    threshold = criterion.params["value"]
    compare = _COMPARATORS[operator_name]
    tool = f"threshold:{fact}"

    fact_args = criterion.params["fact_args"]
    min_hours = fact_args["min_hours"]
    window_days = fact_args["window_days"]
    # The window is the `window_days` calendar days ending on (and including) the
    # case's date of service -- the only anchor a case has. `member`'s own adherence
    # docstring records that the caller (this service) computes the window; there is
    # no second source for it.
    end = case.date_of_service
    start = end - timedelta(days=window_days - 1)
    window_evidence = {
        "start": start.isoformat(), "end": end.isoformat(),
        "min_hours": min_hours, "window_days": window_days,
    }

    adherence = await member_client.adherence(case.member_id, start, end, min_hours)

    if adherence.nights == 0:
        return _insufficient(criterion, tool, {
            "fact": fact,
            "operator": operator_name,
            "threshold": threshold,
            "reason": "no adherence data uploaded for this window (0 nights observed)",
            "window": window_evidence,
        })

    observed = getattr(adherence, _ADHERENCE_FIELDS[fact])
    verdict = Verdict.MET if compare(observed, threshold) else Verdict.NOT_MET
    evidence = {
        "fact": fact,
        "operator": operator_name,
        "threshold": threshold,
        "observed": observed,
        "nights": adherence.nights,
        "qualifying_nights": adherence.qualifying_nights,
        "fraction": adherence.fraction,
        "window": window_evidence,
    }
    return _build(criterion, verdict, 1.0, tool, evidence)


async def _verify_threshold(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    if criterion.params["fact"] in _ADHERENCE_FIELDS:
        return await _verify_adherence_threshold(criterion, case, member_client)
    return await _verify_sleep_study_threshold(criterion, case, member_client)


# --- enum ------------------------------------------------------------------------------


async def _verify_condition_codes(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    allowed = criterion.params["allowed"]
    tool = "enum:condition_codes"

    # `allowed` doubles as the `codes` argument `MemberClient.conditions` needs -- see
    # `domain/params.py`'s FACTS docstring: "the fact is one of these codes" and
    # "these are the codes to look for" are the same list.
    found = await member_client.conditions(case.member_id, case.date_of_service, allowed)

    # An empty list here is not a missing document -- `member`'s own router comment
    # says so directly: "an empty condition or note list for an unknown [to it] member
    # is still a true fact, since absence of conditions is a fact." A member with none
    # of the named codes on record has NOT_MET this criterion, not INSUFFICIENT_EVIDENCE.
    verdict = Verdict.MET if found else Verdict.NOT_MET
    evidence = {
        "fact": "condition_codes",
        "allowed": allowed,
        "matched_conditions": [
            {"code": c.code, "description": c.description, "onset_date": c.onset_date.isoformat()}
            for c in found
        ],
    }
    return _build(criterion, verdict, 1.0, tool, evidence)


async def _verify_coverage_active(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    allowed = criterion.params["allowed"]
    tool = "enum:coverage_active"

    status = await member_client.coverage(case.member_id, case.date_of_service)

    if status is CoverageStatus.NO_RECORD:
        # Per the package docstring, this should never actually happen -- task 7's
        # pipeline checks eligibility and short-circuits before any verifier runs.
        # Handled anyway rather than assumed away, because "the pipeline already
        # checked this" is exactly the kind of invariant that silently stops holding.
        return _insufficient(criterion, tool, {
            "fact": "coverage_active",
            "allowed": allowed,
            "reason": "no record of this member",
        })

    verdict = Verdict.MET if status.value in allowed else Verdict.NOT_MET
    evidence = {"fact": "coverage_active", "allowed": allowed, "observed": status.value}
    return _build(criterion, verdict, 1.0, tool, evidence)


async def _verify_test_type(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    allowed = criterion.params["allowed"]
    tool = "enum:test_type"

    studies = await member_client.sleep_studies(case.member_id, case.date_of_service)
    if not studies:
        return _insufficient(criterion, tool, {
            "fact": "test_type",
            "allowed": allowed,
            "reason": "no sleep studies on record before the date of service",
        })

    checked = [
        {"study_id": s.id, "date": s.date.isoformat(), "value": s.test_type} for s in studies
    ]
    matched = next((s for s in studies if s.test_type in allowed), None)
    verdict = Verdict.MET if matched is not None else Verdict.NOT_MET
    evidence = {
        "fact": "test_type",
        "allowed": allowed,
        "matched_study": (
            {"study_id": matched.id, "date": matched.date.isoformat(), "value": matched.test_type}
            if matched is not None
            else None
        ),
        "studies_checked": checked,
    }
    return _build(criterion, verdict, 1.0, tool, evidence)


_ENUM_VERIFIERS = {
    "condition_codes": _verify_condition_codes,
    "coverage_active": _verify_coverage_active,
    "test_type": _verify_test_type,
}


async def _verify_enum(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    return await _ENUM_VERIFIERS[criterion.params["fact"]](criterion, case, member_client)


# --- temporal --------------------------------------------------------------------------


async def _verify_temporal(
    criterion: Criterion, case: Case, member_client: MemberClient
) -> Verification:
    # `study_date` is the only temporal fact `domain.params.FACTS` defines today.
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    window_days = criterion.params["value"]
    tool = f"temporal:{fact}"

    studies = await member_client.sleep_studies(case.member_id, case.date_of_service)
    if not studies:
        return _insufficient(criterion, tool, {
            "fact": fact,
            "operator": operator_name,
            "window_days": window_days,
            "date_of_service": case.date_of_service.isoformat(),
            "reason": "no sleep studies on record before the date of service",
        })

    def days_before_service(study_date) -> int:
        return (case.date_of_service - study_date).days

    def satisfies(delta_days: int) -> bool:
        if operator_name == "within_days_before":
            # Boundary is inclusive both directions: a study exactly `window_days`
            # days before the date of service counts; one day further back does not.
            return 0 <= delta_days <= window_days
        # "within_days_after" would need a study *after* the date of service, which
        # `member_client.sleep_studies`'s "on or before" cutoff can never return --
        # see the package docstring. Written out explicitly (not assumed
        # unreachable) so a future fact with a real "after" fetch is covered for free.
        return -window_days <= delta_days < 0

    checked = [
        {
            "study_id": s.id,
            "date": s.date.isoformat(),
            "days_before_service": days_before_service(s.date),
        }
        for s in studies
    ]
    matched = next((s for s in studies if satisfies(days_before_service(s.date))), None)
    verdict = Verdict.MET if matched is not None else Verdict.NOT_MET
    evidence = {
        "fact": fact,
        "operator": operator_name,
        "window_days": window_days,
        "date_of_service": case.date_of_service.isoformat(),
        "matched_study": (
            {
                "study_id": matched.id,
                "date": matched.date.isoformat(),
                "days_before_service": days_before_service(matched.date),
            }
            if matched is not None
            else None
        ),
        "studies_checked": checked,
    }
    return _build(criterion, verdict, 1.0, tool, evidence)


_VERIFIERS = {
    CriterionType.THRESHOLD: _verify_threshold,
    CriterionType.ENUM: _verify_enum,
    CriterionType.TEMPORAL: _verify_temporal,
}


async def verify(criterion: Criterion, case: Case, member_client: MemberClient) -> Verification:
    """Dispatch to the comparison for `criterion.type`. `criterion.type` must be one
    of `DETERMINISTIC_TYPES` -- `services/verify/__init__.py` routes `judgment`
    elsewhere and never reaches this function."""
    return await _VERIFIERS[criterion.type](criterion, case, member_client)
