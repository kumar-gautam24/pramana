"""Turning a policy's retrieved chunks into the disjunctive-normal-form criteria sets
ADR-0011 describes. This is the one place in the pipeline where a model's output
drives control flow (ADR-0003): the model decides what the rules are -- which
criteria exist, how each classifies, and what its parameters are -- and this module
is the deterministic gate on that decision. Every field the model produces is
validated before anything downstream ever sees it, and nothing is coerced: an
extraction this module cannot make sense of raises `ExtractionInvalid`, and the
pipeline escalates the case exactly as it would for any other criterion it could not
verify.

Given the same `hits` and the same model response, extraction is reproducible --
there is no I/O here beyond the single injected `LLMProvider.chat` call."""

from dataclasses import dataclass
from importlib import resources
from typing import Any

from pramana_common.criteria import CriterionType
from pramana_common.schemas import Hit
from pydantic import BaseModel, ValidationError

from adjudication.domain.params import (
    FACTS,
    TEMPORAL_OPERATORS,
    THRESHOLD_OPERATORS,
    ExtractionInvalid,
    validate_params,
)
from adjudication.services.llm import LLMProvider

#: ADR-0011: a policy that decomposes into more paths than this is one this service
#: could not fully represent, and a determination it could not fully represent must
#: escalate rather than be partially evaluated. NCD 240.4's initial-authorisation
#: path needs 3; this leaves headroom without letting a pathological extraction
#: multiply out unbounded.
MAX_SETS = 8

_SYSTEM_PROMPT_TEMPLATE = (
    resources.files("adjudication.prompts").joinpath("extract_system.md").read_text()
)


# --- the raw shape the model must produce, before this module's own validation -----


class _RawCriterion(BaseModel):
    text: str
    #: Kept as `str`, not `CriterionType`, deliberately: this module decides what
    #: happens to an unrecognised value, explicitly and visibly below, rather than
    #: letting pydantic's enum coercion make that call implicitly.
    type: str
    params: dict[str, Any] = {}
    source_chunk_id: int


class _RawSet(BaseModel):
    criteria: list[_RawCriterion]


class _RawExtraction(BaseModel):
    sets: list[_RawSet]


_RAW_EXTRACTION_SCHEMA = _RawExtraction.model_json_schema()


# --- this module's own output shape -------------------------------------------------


@dataclass(frozen=True)
class ExtractedCriterion:
    text: str
    type: CriterionType
    params: dict[str, Any]
    source_chunk_id: int
    #: Carried alongside `source_chunk_id` because it is already known at extraction
    #: time (from the `Hit` the id resolved against) and `models.criterion.Criterion`
    #: needs it -- resolving it again downstream would be a second lookup that could
    #: disagree with this one.
    source_display_id: str
    set_ordinal: int
    ordinal: int


@dataclass(frozen=True)
class ExtractedSet:
    ordinal: int
    criteria: tuple[ExtractedCriterion, ...]


def _build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        criterion_types=[t.value for t in CriterionType],
        threshold_operators=sorted(THRESHOLD_OPERATORS),
        temporal_operators=sorted(TEMPORAL_OPERATORS),
        facts=", ".join(sorted(FACTS)),
    )


def _build_user_prompt(hits: list[Hit]) -> str:
    chunks = "\n\n".join(
        f"[chunk {hit.chunk_id}] ({hit.heading_path})\n{hit.text}" for hit in hits
    )
    return (
        "Decompose the following policy excerpts into criteria sets. Cite only the "
        "chunk ids shown here.\n\n" + chunks
    )


def _build_messages(hits: list[Hit]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": _build_user_prompt(hits)},
    ]


async def extract(llm: LLMProvider, hits: list[Hit]) -> list[ExtractedSet]:
    """Ask the model to decompose `hits` -- the governing policy's retrieved chunks
    -- into alternative criteria sets, validate every field of the answer, and return
    the DNF structure ADR-0011 describes.

    Raises `ExtractionInvalid` -- never coerces -- when: a `type` is not a
    `CriterionType` member; a `source_chunk_id` names a chunk not among `hits`; a
    criterion's `params` do not validate against its `type` (see `domain.params`); a
    set is empty; there are no sets; or there are more than `MAX_SETS` sets.

    Raises `UpstreamUnavailable` if the model does not answer -- see `services.llm`
    and `services.upstream`."""
    retrieved = {hit.chunk_id: hit.display_id for hit in hits}

    response = await llm.chat(_build_messages(hits), _RAW_EXTRACTION_SCHEMA)
    try:
        raw = _RawExtraction.model_validate(response)
    except ValidationError as exc:
        raise ExtractionInvalid(
            f"model response did not match the extraction schema: {exc}"
        ) from exc

    if not raw.sets:
        raise ExtractionInvalid("model returned no criteria sets")
    if len(raw.sets) > MAX_SETS:
        raise ExtractionInvalid(
            f"model returned {len(raw.sets)} criteria sets, more than the cap of {MAX_SETS}"
        )

    sets = []
    for set_ordinal, raw_set in enumerate(raw.sets, start=1):
        if not raw_set.criteria:
            raise ExtractionInvalid(f"set {set_ordinal} has no criteria")

        criteria = []
        for ordinal, raw_criterion in enumerate(raw_set.criteria):
            # No fallback exists on this path: an unrecognised `type` raises before
            # `validate_params` is ever called, so it can never be reinterpreted as
            # `judgment` even in the case where `params` happens to be `{}` -- the one
            # shape `judgment` itself accepts. See ExtractionInvalid's docstring.
            try:
                criterion_type = CriterionType(raw_criterion.type)
            except ValueError:
                raise ExtractionInvalid(
                    f"set {set_ordinal} criterion {ordinal} has type "
                    f"{raw_criterion.type!r}, not one of {[t.value for t in CriterionType]}"
                ) from None

            if raw_criterion.source_chunk_id not in retrieved:
                raise ExtractionInvalid(
                    f"set {set_ordinal} criterion {ordinal} cites chunk "
                    f"{raw_criterion.source_chunk_id}, not among the chunks retrieved "
                    f"for this case: {sorted(retrieved)}"
                )

            validate_params(criterion_type, raw_criterion.params)

            criteria.append(
                ExtractedCriterion(
                    text=raw_criterion.text,
                    type=criterion_type,
                    params=raw_criterion.params,
                    source_chunk_id=raw_criterion.source_chunk_id,
                    source_display_id=retrieved[raw_criterion.source_chunk_id],
                    set_ordinal=set_ordinal,
                    ordinal=ordinal,
                )
            )

        sets.append(ExtractedSet(ordinal=set_ordinal, criteria=tuple(criteria)))

    return sets
