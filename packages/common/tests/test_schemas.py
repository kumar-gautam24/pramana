from datetime import date

import pytest
from pydantic import ValidationError

from pramana_common.criteria import CriterionType, Outcome, Verdict
from pramana_common.schemas import (
    CaseRequest,
    Criterion,
    CriterionOutcome,
    Determination,
    EvidenceSpan,
)


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
        blocking=["C2"],
        reason="insufficient_evidence",
        criteria=[
            CriterionOutcome(
                criterion_id="C2",
                verdict=Verdict.INSUFFICIENT_EVIDENCE,
                confidence=0.5,
                tool="retrieval+llm",
                evidence=[EvidenceSpan(source="note:41", locator="p3", text="no PT documented")],
            )
        ],
    )

    assert determination.criteria[0].evidence[0].source == "note:41"


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
