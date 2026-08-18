"""Tests for the `params` contract each `CriterionType` must satisfy -- see
`domain/params.py`'s module docstring and Task 6's brief, which reads this module as
its input contract. Pure module, pure tests: nothing here touches `db_session`."""

import pytest
from pramana_common.criteria import CriterionType

from adjudication.domain.params import ExtractionInvalid, validate_params

# --- threshold -----------------------------------------------------------------------


def test_threshold_accepts_a_well_formed_params():
    validate_params(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": 15})


def test_threshold_missing_value_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">="})


def test_threshold_unknown_operator_is_rejected():
    """The >=-vs-> distinction has caused two defects in this project already, so
    the operator vocabulary is closed -- "at least" spelled some other way must not
    silently pass through."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD, {"fact": "ahi", "operator": "at_least", "value": 15}
        )


def test_threshold_non_numeric_value_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": "15"})


def test_threshold_bool_value_is_rejected():
    """bool is a subclass of int in Python; a criterion parameterised with
    True/False is a modelling mistake this validator must catch, not silently accept
    as 1/0."""
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.THRESHOLD, {"fact": "ahi", "operator": ">=", "value": True})


def test_threshold_fact_outside_vocabulary_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD, {"fact": "blood_pressure", "operator": ">=", "value": 140}
        )


def test_threshold_rejects_unexpected_keys():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {"fact": "ahi", "operator": ">=", "value": 15, "unit": "events/hour"},
        )


# --- enum ------------------------------------------------------------------------------


def test_enum_accepts_a_well_formed_params():
    validate_params(CriterionType.ENUM, {"fact": "test_type", "allowed": ["home_type_iv"]})


def test_enum_empty_allowed_list_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.ENUM, {"fact": "test_type", "allowed": []})


def test_enum_non_string_members_are_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.ENUM, {"fact": "test_type", "allowed": [1, 2]})


def test_enum_fact_outside_vocabulary_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.ENUM, {"fact": "diagnosis_confidence", "allowed": ["high"]})


def test_enum_missing_allowed_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.ENUM, {"fact": "test_type"})


# --- temporal --------------------------------------------------------------------------


def test_temporal_accepts_a_well_formed_params():
    validate_params(
        CriterionType.TEMPORAL,
        {"fact": "study_date", "operator": "within_days_before", "value": 365},
    )


def test_temporal_unknown_operator_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL, {"fact": "study_date", "operator": "recently", "value": 365}
        )


def test_temporal_non_positive_value_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {"fact": "study_date", "operator": "within_days_before", "value": 0},
        )


def test_temporal_non_integer_value_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {"fact": "study_date", "operator": "within_days_before", "value": 365.5},
        )


def test_temporal_fact_outside_vocabulary_is_rejected():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {"fact": "diagnosis_date", "operator": "within_days_before", "value": 365},
        )


# --- judgment ----------------------------------------------------------------------------


def test_judgment_accepts_empty_params():
    validate_params(CriterionType.JUDGMENT, {})


def test_judgment_rejects_any_params():
    """No key is defined for `judgment` yet -- see the reasoning in
    `domain/params.py`. Rejecting keeps a future key a deliberate contract change."""
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.JUDGMENT, {"fact": "ahi"})


# --- the vocabulary itself, against member_client -------------------------------------


def test_facts_vocabulary_matches_member_client_surface():
    """`FACTS` must name exactly the facts `MemberClient` can answer -- see that
    module's dataclasses. This is not a per-policy vocabulary (CLAUDE.md invariant 3);
    it is the API surface of the member service, and this test is what keeps the two
    from drifting apart silently if a field is renamed on one side only."""
    from adjudication.domain.params import FACTS

    assert FACTS == {
        "ahi",
        "channels",
        "apnea_events",
        "recorded_hours",
        "test_type",
        "study_date",
        "condition_codes",
        "adherence_fraction",
        "adherence_nights",
        "coverage_active",
    }
