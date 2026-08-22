"""Tests for criteria extraction -- see the task-5 brief, ADR-0003, and ADR-0011.

Stub `LLMProvider` only: no live model (see fixtures/ncd_240_4_extraction.py's
docstring for why there cannot be one on this machine), no network. Each test is
named for the rule it would catch a regression in."""

import pytest
from fixtures.ncd_240_4_extraction import HITS, RAW_RESPONSE
from pramana_common.criteria import CriterionResult, CriterionType, Outcome, Verdict
from pramana_common.gate import GateThresholds

from adjudication.domain import params as params_module
from adjudication.domain.criteria_sets import CriteriaSet, aggregate
from adjudication.domain.params import ExtractionInvalid
from adjudication.models.criterion import Criterion
from adjudication.services import extract as extract_module
from adjudication.services.extract import extract


class StubLLM:
    """Records the last `chat` call it received and returns a fixed response -- the
    stand-in for a model this suite never talks to."""

    def __init__(self, response):
        self.response = response
        self.last_messages = None
        self.last_schema = None

    async def chat(self, messages, schema):
        self.last_messages = messages
        self.last_schema = schema
        return self.response


def _threshold_criterion(chunk_id: int = 58) -> dict:
    return {
        "text": "AHI or RDI greater than or equal to 15 events per hour",
        "type": "threshold",
        "params": {"fact": "ahi", "operator": ">=", "value": 15},
        "source_chunk_id": chunk_id,
    }


# --- the well-formed fixture ----------------------------------------------------------


async def test_well_formed_fixture_extracts_four_sets():
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    assert [s.ordinal for s in sets] == [1, 2, 3, 4]
    assert len(sets[0].criteria) == 2
    assert len(sets[1].criteria) == 4
    assert len(sets[2].criteria) == 4
    assert len(sets[3].criteria) == 2


async def test_well_formed_fixture_assigns_zero_indexed_ordinals_within_a_set():
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    assert [c.ordinal for c in sets[0].criteria] == [0, 1]
    assert [c.ordinal for c in sets[1].criteria] == [0, 1, 2, 3]


async def test_well_formed_fixture_resolves_source_display_id_from_hits():
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    for criteria_set in sets:
        for criterion in criteria_set.criteria:
            assert criterion.source_display_id == "240.4"


async def test_well_formed_fixture_covers_threshold_enum_and_judgment_types():
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    types = {c.type for s in sets for c in s.criteria}
    assert types == {CriterionType.ENUM, CriterionType.THRESHOLD, CriterionType.JUDGMENT}


async def test_well_formed_fixture_carries_fact_args_for_the_adherence_criterion():
    """Fix round 1: the continuation set's adherence criterion is the thing that
    proved the params contract couldn't represent NCD 240.4's own headline
    continuation rule without `fact_args` -- this pins the fixed shape down."""
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    continuation_set = sets[3]
    adherence_criterion = next(
        c for c in continuation_set.criteria if c.type is CriterionType.THRESHOLD
    )

    assert adherence_criterion.params["fact"] == "adherence_fraction"
    assert adherence_criterion.params["fact_args"] == {"min_hours": 4.0, "window_days": 30}


async def test_well_formed_fixture_parses_into_what_criteria_sets_aggregate_consumes():
    """The point of this test is agreement between the two tasks, not assumption:
    `extract`'s output must convert -- with only the glue a persistence layer would
    add, an `id` and `case_id` -- into the exact `CriteriaSet`/`Criterion` shape
    `criteria_sets.aggregate` already consumes, proven by actually running it, not by
    the two shapes merely looking alike."""
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    criteria_sets = []
    results = {}
    next_id = 1
    for extracted_set in sets:
        criteria = []
        for extracted_criterion in extracted_set.criteria:
            criterion = Criterion(
                id=next_id,
                case_id="case-1",
                set_ordinal=extracted_criterion.set_ordinal,
                ordinal=extracted_criterion.ordinal,
                text=extracted_criterion.text,
                type=extracted_criterion.type,
                params=extracted_criterion.params,
                source_chunk_id=extracted_criterion.source_chunk_id,
                source_display_id=extracted_criterion.source_display_id,
            )
            criteria.append(criterion)
            # Every criterion met, so set 1 (AHI >= 15) approves -- proof the
            # structure is not merely shaped right but usable end to end.
            results[str(next_id)] = CriterionResult(
                criterion_id=str(next_id), verdict=Verdict.MET, confidence=1.0
            )
            next_id += 1
        criteria_sets.append(CriteriaSet(ordinal=extracted_set.ordinal, criteria=tuple(criteria)))

    decision = aggregate(criteria_sets, results, GateThresholds())

    assert decision.outcome is Outcome.APPROVE
    assert decision.winning_set == 1


async def test_extract_sends_a_schema_constraining_source_chunk_id_to_retrieved_ids():
    """Fix round 2: asserting the schema equals a module constant proved nothing --
    a mutant that replaced the constant with `{}` still passed, because the test
    compared the sent value against itself. This asserts the schema's actual
    content: `source_chunk_id` is a closed string enum of exactly this call's
    retrieved chunk ids (see `_build_schema`'s docstring for why -- Deflect's answer
    service asked for a bare integer and once got a concatenated, malformed one
    back)."""
    stub = StubLLM(RAW_RESPONSE)

    await extract(stub, HITS)

    criterion_schema = stub.last_schema["$defs"]["_RawCriterion"]
    assert criterion_schema["properties"]["source_chunk_id"] == {
        "type": "string",
        "enum": ["56", "57", "58", "59"],
    }
    assert criterion_schema["additionalProperties"] is False
    assert set(criterion_schema["required"]) == {"text", "type", "source_chunk_id"}


async def test_extract_cites_retrieved_chunks_in_the_user_prompt():
    stub = StubLLM(RAW_RESPONSE)

    await extract(stub, HITS)

    user_message = stub.last_messages[-1]["content"]
    for chunk_id in (56, 57, 58, 59):
        assert f"chunk {chunk_id}" in user_message


async def test_a_string_source_chunk_id_is_accepted_like_a_real_provider_would_send():
    """The schema constrains `source_chunk_id` to a *string* enum (see
    `_build_schema`); a well-behaved provider following it sends `"58"`, not `58`.
    pydantic's lax `int` coercion is what lets that and this module's own
    int-literal fixtures both validate without a second field or a manual parse."""
    response = {
        "sets": [{"criteria": [{**_threshold_criterion(), "source_chunk_id": "58"}]}]
    }

    sets = await extract(StubLLM(response), HITS)

    assert sets[0].criteria[0].source_chunk_id == 58


# --- an unknown type must never coerce to judgment --------------------------------------


async def test_unknown_type_raises_extraction_invalid():
    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "some condition",
                        "type": "not_a_real_type",
                        "params": {},
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_unknown_type_never_reaches_params_validation(monkeypatch):
    """`{}` is deliberately exactly the params `judgment` itself accepts. A
    regression that silently coerced an unrecognised `type` to `judgment` and then
    relied on params validation to catch the mistake would sail straight through --
    `{}` validates fine for `judgment` -- so the test above alone would not catch it.
    This test instead proves `validate_params` is never even called for this
    criterion: the only code path that could produce a `judgment`-typed
    `ExtractedCriterion` from an unrecognised `type` never runs."""
    calls = []
    original = params_module.validate_params

    def spy(criterion_type, params):
        calls.append(criterion_type)
        return original(criterion_type, params)

    monkeypatch.setattr(extract_module, "validate_params", spy)

    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "some condition",
                        "type": "not_a_real_type",
                        "params": {},
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)

    assert calls == []


# --- source_chunk_id must be among the ids retrieved for this call ----------------------


async def test_source_chunk_id_not_in_the_corpus_at_all_is_rejected():
    response = {"sets": [{"criteria": [_threshold_criterion(chunk_id=999999)]}]}

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_source_chunk_id_valid_elsewhere_but_not_retrieved_for_this_call_is_rejected():
    """61 is a plausible neighbouring chunk id from the same policy as the four ids
    actually retrieved here (`HITS` -- 56, 57, 58, 59) -- not a value chosen to look
    obviously wrong. The point: membership must be checked against *this call's*
    `hits`, never against "is this a chunk id that could exist somewhere in the
    corpus"."""
    response = {"sets": [{"criteria": [_threshold_criterion(chunk_id=61)]}]}

    with pytest.raises(ExtractionInvalid) as exc_info:
        await extract(StubLLM(response), HITS)

    assert "61" in str(exc_info.value)


# --- params validation is enforced per type ----------------------------------------------


async def test_criterion_with_invalid_params_is_rejected():
    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "AHI at least fifteen",
                        "type": "threshold",
                        "params": {"fact": "ahi", "operator": "at_least", "value": 15},
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


# --- structural rejections -----------------------------------------------------------


async def test_empty_criteria_set_is_rejected():
    response = {"sets": [{"criteria": []}]}

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_no_sets_is_rejected():
    response = {"sets": []}

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_more_sets_than_the_cap_is_rejected():
    """ADR-0011: a policy that expands beyond the cap is one this service could not
    fully represent, and must escalate rather than being partially evaluated.

    9, not `MAX_SETS + 1`: deriving the count from the constant under test means a
    mutation to `MAX_SETS` itself (8 -> 64) silently keeps this test passing, since
    both the response and the check move together. `MAX_SETS` is 8 today -- if that
    ever changes deliberately, this literal has to be updated by hand, which is the
    point."""
    assert extract_module.MAX_SETS == 8
    one_set = {"criteria": [_threshold_criterion()]}
    response = {"sets": [one_set] * 9}

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_response_not_matching_the_extraction_schema_is_rejected():
    """A model response that isn't even shaped like `{"sets": [...]}` must raise
    `ExtractionInvalid`, not an uncaught `pydantic.ValidationError`."""
    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM([{"not": "the expected shape"}]), HITS)


async def test_criterion_with_an_unexpected_top_level_key_is_rejected():
    """Fix round 2: `_RawCriterion` used to accept `extra="ignore"` (pydantic's
    default), so a key like `negated` was silently dropped rather than validated --
    contradicting this module's own docstring, which promises every field is
    checked. `extra="forbid"` turns a dropped key into a rejected criterion."""
    response = {
        "sets": [
            {
                "criteria": [
                    {**_threshold_criterion(), "negated": True},
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


# --- fix round 2: a fact may only be named under the CriterionTypes it permits ------


async def test_condition_codes_under_threshold_is_rejected():
    """The Critical from fix round 2's review: `MemberClient.conditions` needs a
    `codes` argument the same way `adherence` needs `min_hours`/`window_days`, but
    nothing supplied it for `threshold`/`temporal` and `FACTS` didn't say they
    couldn't be used -- so a `threshold` on `condition_codes` validated even though
    no verifier could ever fetch it. `condition_codes` is now `enum`-only, because
    `enum`'s `allowed` list already doubles as the codes to look for."""
    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "At least one documented comorbidity",
                        "type": "threshold",
                        "params": {"fact": "condition_codes", "operator": ">=", "value": 1},
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_condition_codes_under_temporal_is_rejected():
    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "Comorbidity documented recently",
                        "type": "temporal",
                        "params": {
                            "fact": "condition_codes",
                            "operator": "within_days_before",
                            "value": 365,
                        },
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_channels_under_enum_is_rejected():
    """The other half of the same Critical: the prompt used to say `allowed` could
    hold "any value", but the validator only ever accepted strings, so
    `{"fact": "channels", "allowed": [3, 4]}` was rejected for a reason the prompt
    never explained. `channels` is `threshold`-only now, so this is rejected because
    the fact isn't usable under `enum` at all -- a clearer, single reason instead of
    a datatype mismatch the model had no way to predict."""
    response = {
        "sets": [
            {
                "criteria": [
                    {
                        "text": "Type III or Type IV test",
                        "type": "enum",
                        "params": {"fact": "channels", "allowed": ["3", "4"]},
                        "source_chunk_id": 58,
                    }
                ]
            }
        ]
    }

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


# --- fix round 2: params values survive extraction unchanged -----------------------
#
# Systematic, not one per bullet: a validator that approves a value must never be
# the thing that also silently rewrites it (the surviving mutant was an operator
# quietly normalised from `>` to `>=` -- still a valid operator, so nothing that
# only checks "did it raise" would ever notice). One case per `CriterionType`,
# built directly rather than drawn from the fixture, so `temporal` -- which the
# fixture never uses -- is covered too.

_VALID_CRITERIA_BY_TYPE = {
    CriterionType.THRESHOLD: {
        "text": "AHI greater than fifteen",
        "type": "threshold",
        "params": {"fact": "ahi", "operator": ">", "value": 15},
        "source_chunk_id": 58,
    },
    CriterionType.ENUM: {
        "text": "Positive PSG",
        "type": "enum",
        "params": {"fact": "test_type", "allowed": ["attended_psg", "home_type_ii"]},
        "source_chunk_id": 57,
    },
    CriterionType.TEMPORAL: {
        "text": "Study within the last year",
        "type": "temporal",
        "params": {"fact": "study_date", "operator": "within_days_before", "value": 365},
        "source_chunk_id": 58,
    },
    CriterionType.JUDGMENT: {
        "text": "Documented benefit",
        "type": "judgment",
        "params": {},
        "source_chunk_id": 56,
    },
}


@pytest.mark.parametrize("criterion_type", sorted(_VALID_CRITERIA_BY_TYPE, key=str))
async def test_params_are_preserved_verbatim_by_type(criterion_type):
    raw_criterion = _VALID_CRITERIA_BY_TYPE[criterion_type]
    response = {"sets": [{"criteria": [raw_criterion]}]}

    sets = await extract(StubLLM(response), HITS)

    assert sets[0].criteria[0].type is criterion_type
    assert sets[0].criteria[0].params == raw_criterion["params"]


async def test_well_formed_fixture_preserves_every_param_value_verbatim():
    """The same guarantee, exercised end to end against the realistic fixture
    (every criterion across all four sets), not just one example per type."""
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    raw_criteria = [c for s in RAW_RESPONSE["sets"] for c in s["criteria"]]
    extracted_criteria = [c for s in sets for c in s.criteria]
    assert len(raw_criteria) == len(extracted_criteria)
    for raw, extracted in zip(raw_criteria, extracted_criteria, strict=True):
        assert extracted.params == raw["params"]
