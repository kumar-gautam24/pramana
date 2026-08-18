"""Tests for the `params` contract each `CriterionType` must satisfy -- see
`domain/params.py`'s module docstring and Task 6's brief, which reads this module as
its input contract. Pure module, pure tests: nothing here touches `db_session`."""

import dataclasses

import pytest
from pramana_common.criteria import CriterionType

from adjudication.domain.params import FACTS, ExtractionInvalid, FactDataType, validate_params

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


def test_enum_rejects_unexpected_keys():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.ENUM, {"fact": "test_type", "allowed": ["psg"], "confidence": "high"}
        )


def test_enum_channels_is_rejected_because_channels_is_not_enum_permitted():
    """Fix round 2. `{"fact": "channels", "allowed": [3, 4]}` used to be rejected
    for a datatype mismatch the prompt never explained (it promised `allowed` could
    hold "any value"). `channels` is `threshold`-only now -- see the `permitted_types`
    matrix test below -- so this is rejected for a single, prompt-consistent reason:
    the fact isn't usable under `enum` at all, independent of what `allowed` holds."""
    with pytest.raises(ExtractionInvalid):
        validate_params(CriterionType.ENUM, {"fact": "channels", "allowed": ["3", "4"]})


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


def test_temporal_rejects_unexpected_keys():
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {
                "fact": "study_date",
                "operator": "within_days_before",
                "value": 365,
                "inclusive": True,
            },
        )


def test_temporal_condition_codes_is_rejected():
    """Fix round 2 Critical: `condition_codes` under `temporal` used to validate --
    `FACTS` was a flat set with no declared permitted types -- even though nothing
    supplies `MemberClient.conditions`' required `codes` argument under `temporal`.
    `condition_codes` is `enum`-only now."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {"fact": "condition_codes", "operator": "within_days_before", "value": 365},
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


def test_temporal_fact_args_for_a_fact_that_takes_none_is_rejected():
    """Fix round 2. `_validate_fact_args` used to only be exercised through
    `threshold`; nothing proved `temporal` also calls it. `study_date` has no
    `fetch_args`, so attaching them must still be rejected here exactly as it is
    for `threshold`."""
    with pytest.raises(ExtractionInvalid):
        validate_params(
            CriterionType.TEMPORAL,
            {
                "fact": "study_date",
                "operator": "within_days_before",
                "value": 365,
                "fact_args": {"min_hours": 4.0, "window_days": 30},
            },
        )


# --- fix round 2: a fact may only be named under its permitted CriterionTypes -------
#
# General, not case by case: every (fact, CriterionType) combination is checked, so
# closing one nonsense combination (e.g. `condition_codes` under `threshold`) can't
# leave a sibling one (`ahi` under `enum`, `test_type` under `temporal`, ...) open by
# accident. 10 facts x 3 deterministic types = 30 combinations.


def _minimal_params(fact: str, criterion_type: CriterionType) -> dict:
    """The smallest params dict that would validate for `(fact, criterion_type)` if
    that combination is permitted -- used only to isolate the permitted-types check
    from every other rule this module enforces."""
    spec = FACTS[fact]
    params: dict = {"fact": fact}
    if criterion_type is CriterionType.THRESHOLD:
        params["operator"] = ">="
        params["value"] = 1
    elif criterion_type is CriterionType.ENUM:
        params["allowed"] = ["x"]
    elif criterion_type is CriterionType.TEMPORAL:
        params["operator"] = "within_days_before"
        params["value"] = 1
    if spec.fetch_args:
        params["fact_args"] = {
            key: (1.0 if expected is float else 1) for key, expected in spec.fetch_args.items()
        }
    return params


@pytest.mark.parametrize(
    "fact,criterion_type",
    [
        (fact, criterion_type)
        for fact in sorted(FACTS)
        for criterion_type in (CriterionType.THRESHOLD, CriterionType.ENUM, CriterionType.TEMPORAL)
    ],
)
def test_fact_validates_only_under_its_permitted_types(fact, criterion_type):
    params = _minimal_params(fact, criterion_type)

    if criterion_type in FACTS[fact].permitted_types:
        validate_params(criterion_type, params)  # must not raise
    else:
        with pytest.raises(ExtractionInvalid):
            validate_params(criterion_type, params)


# --- fix round 2: omitting a required key is rejected, systematically --------------
#
# Every existing test supplied a complete dict and varied one value; none omitted a
# key with a plausible default, so a validator quietly defaulting a missing `fact`
# or `operator` (`params.get("operator", ">=")`) passed every test unnoticed. This
# parametrizes over every required key of every type instead of one test per key.

_WELL_FORMED_PARAMS = {
    CriterionType.THRESHOLD: {"fact": "ahi", "operator": ">=", "value": 15},
    CriterionType.ENUM: {"fact": "test_type", "allowed": ["psg"]},
    CriterionType.TEMPORAL: {"fact": "study_date", "operator": "within_days_before", "value": 365},
}


@pytest.mark.parametrize(
    "criterion_type,missing_key",
    [
        (criterion_type, key)
        for criterion_type, params in _WELL_FORMED_PARAMS.items()
        for key in params
    ],
)
def test_omitting_a_required_key_is_rejected(criterion_type, missing_key):
    params = {k: v for k, v in _WELL_FORMED_PARAMS[criterion_type].items() if k != missing_key}

    with pytest.raises(ExtractionInvalid):
        validate_params(criterion_type, params)


# --- the vocabulary itself, against member_client -------------------------------------


def test_facts_vocabulary_matches_member_client_surface():
    """Genuinely pins `FACTS` to `MemberClient`'s wire dataclasses via
    `dataclasses.fields`, not a repeated literal set. Fix round 2: the previous
    version of this test asserted `FACTS == {a literal set}` without importing
    `member_client` at all, so its own claim -- that a rename on either side breaks
    it -- was false; renaming `SleepStudy.ahi` to `ahi_index` left all 28 tests
    passing. This version imports the real dataclasses and checks the field names
    `FACTS`' comment claims exist, so that rename now fails here."""
    from adjudication.services.member_client import Adherence, Condition, SleepStudy

    assert set(FACTS) == {
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

    sleep_study_fields = {f.name for f in dataclasses.fields(SleepStudy)}
    condition_fields = {f.name for f in dataclasses.fields(Condition)}
    adherence_fields = {f.name for f in dataclasses.fields(Adherence)}

    assert {"ahi", "channels", "apnea_events", "recorded_hours", "test_type"} <= sleep_study_fields
    assert "date" in sleep_study_fields  # study_date
    assert "code" in condition_fields  # condition_codes
    assert {"fraction", "nights"} <= adherence_fields  # adherence_fraction, adherence_nights


def test_facts_declare_a_datatype_consistent_with_their_permitted_types():
    """Every fact permitted under `threshold` is `NUMBER`-typed and every fact
    permitted under `enum` is `STRING`-typed -- the property that makes `enum`'s
    "allowed must be strings" rule correct for every fact that can actually reach
    it, rather than an assumption that happened to hold before fix round 2."""
    for fact, spec in FACTS.items():
        if CriterionType.THRESHOLD in spec.permitted_types:
            assert spec.datatype is FactDataType.NUMBER, fact
        if CriterionType.ENUM in spec.permitted_types:
            assert spec.datatype is FactDataType.STRING, fact
