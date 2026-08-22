"""Verifiers for the three `DETERMINISTIC_TYPES` -- `threshold`, `enum`, and `temporal`.

"Deterministic" names the *criterion types*, not the arithmetic: these are the criteria whose
answer is a comparison against a fact rather than a reading of clinical narrative. Who
performs that comparison is injected as an `Arithmetic` (see `arithmetic.py`). In every run
except the `model_arithmetic` ablation it is `PythonArithmetic`, and nothing here is ever
handed to a model. See the package docstring (`adjudication.services.verify`) for the
member-exists invariant every function below relies on.

Everything in this module is on the *shared* side of that seam, and deliberately so: the
fetches, the missing-versus-contradicted rules, the "any study satisfies it" semantics and
the evidence are identical in both arms, so a run and its ablated twin differ in exactly one
thing (ADR-0021).

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

Every verdict here carries confidence exactly `1.0`, in both arms. A comparison the Python
arm performs is exact; anything less would misrepresent certainty the code actually has, and
the gate thresholds on confidence, so a value below `1.0` could suppress a true `MET`. The
ablated arm keeps the same value on purpose -- a model's self-reported confidence would be a
second difference between the arms and would move the ablated run along the threshold sweep
for a reason unrelated to whether its arithmetic was right (see `arithmetic.py`).

Pure apart from the injected `member_client` calls and, in the ablated arm, the injected
`Arithmetic`'s model calls; no database.
"""

from datetime import timedelta

from pramana_common.criteria import CriterionResult, CriterionType, Verdict

from adjudication.models.case import Case
from adjudication.models.criterion import Criterion
from adjudication.services.member_client import CoverageStatus, MemberClient
from adjudication.services.verify import Verification
from adjudication.services.verify.arithmetic import Arithmetic

#: `adherence_fraction`/`adherence_nights` come from one `MemberClient.adherence` call
#: rather than the sleep-study endpoint; this is the field each reads off `Adherence`.
#: `adherence_nights` reads `qualifying_nights` (nights meeting the policy's
#: `min_hours` bar), not `nights` (nights any usage was logged at all) -- `nights` is
#: instead the signal for the INSUFFICIENT_EVIDENCE row below: zero of *those* means no
#: usage data was ever uploaded, which is a different fact than "the member used the
#: device on zero qualifying nights".
_ADHERENCE_FIELDS = {"adherence_fraction": "fraction", "adherence_nights": "qualifying_nights"}


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
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    threshold = criterion.params["value"]
    tool = arithmetic.tool(f"threshold:{fact}")

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
    # A loop rather than `next(... if compare(...))`: the comparison is awaited now, because
    # in the ablated arm it is a network call. The semantics are the generator's -- the first
    # study that satisfies it wins, and the rest are never compared.
    matched = None
    for study in studies:
        if await arithmetic.compare(operator_name, getattr(study, fact), threshold):
            matched = study
            break

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
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    threshold = criterion.params["value"]
    tool = arithmetic.tool(f"threshold:{fact}")

    fact_args = criterion.params["fact_args"]
    min_hours = fact_args["min_hours"]
    window_days = fact_args["window_days"]
    # The window is the `window_days` calendar days ending on (and including) the
    # case's date of service -- the only anchor a case has. `member`'s own adherence
    # docstring records that the caller (this service) computes the window; there is
    # no second source for it.
    #
    # Computed in Python in both arms, ablation included. It is an argument to the *fetch*,
    # not the comparison that produces the verdict, and ablating it would change what
    # evidence the two arms saw -- a second difference, and the one that would make the
    # comparison between them meaningless (ADR-0021).
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
    met = await arithmetic.compare(operator_name, observed, threshold)
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
    return _build(criterion, Verdict.MET if met else Verdict.NOT_MET, 1.0, tool, evidence)


async def _verify_threshold(
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    if criterion.params["fact"] in _ADHERENCE_FIELDS:
        return await _verify_adherence_threshold(criterion, case, member_client, arithmetic)
    return await _verify_sleep_study_threshold(criterion, case, member_client, arithmetic)


# --- enum ------------------------------------------------------------------------------


async def _verify_condition_codes(
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    allowed = criterion.params["allowed"]
    # Deliberately not `arithmetic.tool(...)`: this verifier performs no comparison, so
    # there is nothing here for the ablation to ablate and labelling it as ablated would
    # overstate a run's coverage. `MemberClient.conditions` takes the codes as a query
    # parameter and `member` filters in SQL, so the fetch *is* the membership test -- and it
    # cannot be moved to the model, because there is no way to ask that endpoint for every
    # condition a member has. `evals` reports how many criteria fell back this way rather
    # than letting a partial ablation read as a whole one (ADR-0021).
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
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    allowed = criterion.params["allowed"]
    tool = arithmetic.tool("enum:coverage_active")

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

    met = await arithmetic.member_of(status.value, allowed)
    evidence = {"fact": "coverage_active", "allowed": allowed, "observed": status.value}
    return _build(criterion, Verdict.MET if met else Verdict.NOT_MET, 1.0, tool, evidence)


async def _verify_test_type(
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    allowed = criterion.params["allowed"]
    tool = arithmetic.tool("enum:test_type")

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
    matched = None
    for study in studies:
        if await arithmetic.member_of(study.test_type, allowed):
            matched = study
            break

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
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    verifier = _ENUM_VERIFIERS[criterion.params["fact"]]
    return await verifier(criterion, case, member_client, arithmetic)


# --- temporal --------------------------------------------------------------------------


async def _verify_temporal(
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    # `study_date` is the only temporal fact `domain.params.FACTS` defines today.
    fact = criterion.params["fact"]
    operator_name = criterion.params["operator"]
    window_days = criterion.params["value"]
    tool = arithmetic.tool(f"temporal:{fact}")

    studies = await member_client.sleep_studies(case.member_id, case.date_of_service)
    if not studies:
        return _insufficient(criterion, tool, {
            "fact": fact,
            "operator": operator_name,
            "window_days": window_days,
            "date_of_service": case.date_of_service.isoformat(),
            "reason": "no sleep studies on record before the date of service",
        })

    # Computed here in both arms and put in the evidence, not used to decide anything --
    # `Arithmetic.within` receives the two dates precisely so that subtracting them is the
    # thing being ablated. In an ablated run this number is therefore the *correct* delta
    # sitting beside a verdict the model reached without it, which is exactly what a reader
    # comparing the two arms needs.
    def days_before_service(study_date) -> int:
        return (case.date_of_service - study_date).days

    checked = [
        {
            "study_id": s.id,
            "date": s.date.isoformat(),
            "days_before_service": days_before_service(s.date),
        }
        for s in studies
    ]
    matched = None
    for study in studies:
        if await arithmetic.within(
            operator_name, study.date, case.date_of_service, window_days
        ):
            matched = study
            break

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


async def verify(
    criterion: Criterion, case: Case, member_client: MemberClient, arithmetic: Arithmetic
) -> Verification:
    """Dispatch to the comparison for `criterion.type`. `criterion.type` must be one
    of `DETERMINISTIC_TYPES` -- `services/verify/__init__.py` routes `judgment`
    elsewhere and never reaches this function, and is also where `arithmetic` is chosen
    from the case's `run_mode`."""
    return await _VERIFIERS[criterion.type](criterion, case, member_client, arithmetic)
