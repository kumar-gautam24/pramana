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


async def test_well_formed_fixture_extracts_three_sets():
    sets = await extract(StubLLM(RAW_RESPONSE), HITS)

    assert [s.ordinal for s in sets] == [1, 2, 3]
    assert len(sets[0].criteria) == 2
    assert len(sets[1].criteria) == 4
    assert len(sets[2].criteria) == 4


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


async def test_extract_sends_the_json_schema_and_cites_retrieved_chunks_in_the_prompt():
    stub = StubLLM(RAW_RESPONSE)

    await extract(stub, HITS)

    assert stub.last_schema == extract_module._RAW_EXTRACTION_SCHEMA
    user_message = stub.last_messages[-1]["content"]
    assert "chunk 57" in user_message
    assert "chunk 58" in user_message


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
    """61 is a plausible neighbouring chunk id from the same policy as the two ids
    actually retrieved here (57 and 58) -- not a value chosen to look obviously
    wrong. The point: membership must be checked against *this call's* `hits`, never
    against "is this a chunk id that could exist somewhere in the corpus"."""
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
    fully represent, and must escalate rather than being partially evaluated."""
    one_set = {"criteria": [_threshold_criterion()]}
    response = {"sets": [one_set] * (extract_module.MAX_SETS + 1)}

    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM(response), HITS)


async def test_response_not_matching_the_extraction_schema_is_rejected():
    """A model response that isn't even shaped like `{"sets": [...]}` must raise
    `ExtractionInvalid`, not an uncaught `pydantic.ValidationError`."""
    with pytest.raises(ExtractionInvalid):
        await extract(StubLLM([{"not": "the expected shape"}]), HITS)
