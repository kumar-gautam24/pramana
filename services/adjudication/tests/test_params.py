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


# --- fact_args: facts the member service cannot answer without extra arguments --------
#
# Fix round 1. `MemberClient.adherence(member_id, start, end, min_hours)` needs a
# window and a nightly-hours bar before it will answer at all, and `min_hours` has
# no default on that endpoint deliberately -- the bar is the policy's number, not
# the member service's. Without `fact_args`, a verifier would have had to hardcode
# it, which is exactly the per-policy hardcoding invariant 3 forbids. Both rejection
# directions matter equally: a fact that needs args and doesn't get them is
# unusable, and a fact that doesn't need args but gets them anyway means the model
# can attach keys nothing reads.


def test_threshold_adherence_fact_accepts_well_formed_fact_args():
    validate_params(
        CriterionType.THRESHOLD,
        {
            "fact": "adherence_fraction",
            "operator": ">=",
            "value": 0.7,
            "fact_args": {"min_hours": 4.0, "window_days": 30},
        },
    )


def test_threshold_adherence_fact_without_fact_args_is_rejected():
    """The hardcoding hole this fix closes: a criterion naming `adherence_fraction`
    (or `adherence_nights`) has no way to say which nightly-hours bar or window it
    means without `fact_args` -- omitting it must not silently pass, or Task 6's
    verifier would be forced to invent a default the policy never stated."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {"fact": "adherence_fraction", "operator": ">=", "value": 0.7},
        )


def test_threshold_non_adherence_fact_with_fact_args_is_rejected():
    """The other direction: a fact that doesn't need arguments must not be allowed
    to carry them anyway, or the model could attach arbitrary keys nothing reads."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {
                "fact": "ahi",
                "operator": ">=",
                "value": 15,
                "fact_args": {"min_hours": 4.0, "window_days": 30},
            },
        )


def test_adherence_fact_args_rejects_missing_key():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {
                "fact": "adherence_nights",
                "operator": ">=",
                "value": 21,
                "fact_args": {"min_hours": 4.0},  # window_days missing
            },
        )


def test_adherence_fact_args_rejects_extra_key():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {
                "fact": "adherence_nights",
                "operator": ">=",
                "value": 21,
                "fact_args": {"min_hours": 4.0, "window_days": 30, "unit": "hours"},
            },
        )


def test_adherence_fact_args_rejects_non_positive_min_hours():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {
                "fact": "adherence_fraction",
                "operator": ">=",
                "value": 0.7,
                "fact_args": {"min_hours": 0, "window_days": 30},
            },
        )


def test_adherence_fact_args_rejects_non_integer_window_days():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.THRESHOLD,
            {
                "fact": "adherence_fraction",
                "operator": ">=",
                "value": 0.7,
                "fact_args": {"min_hours": 4.0, "window_days": 30.5},
            },
        )


def test_enum_fact_with_fact_args_is_rejected():
    """Same rule, checked against a different `CriterionType` -- `fact_args`
    validity is driven by the fact named, not by which type carries it."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.ENUM,
            {
                "fact": "test_type",
                "allowed": ["home_type_iv"],
                "fact_args": {"min_hours": 4.0, "window_days": 30},
            },
        )


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
