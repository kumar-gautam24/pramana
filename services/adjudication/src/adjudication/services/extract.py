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

from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from typing import Any

from pramana_common.criteria import CriterionType
from pramana_common.schemas import Hit
from pydantic import BaseModel, ConfigDict, ValidationError

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
    # `extra="forbid"`, not the pydantic default `"ignore"`: this module's own
    # docstring promises every field the model produces is validated, and a key like
    # `"negated": true` silently dropped by the default would be validating nothing
    # -- the case would proceed as if the model had never said it.
    model_config = ConfigDict(extra="forbid")

    text: str
    #: Kept as `str`, not `CriterionType`, deliberately: this module decides what
    #: happens to an unrecognised value, explicitly and visibly below, rather than
    #: letting pydantic's enum coercion make that call implicitly.
    type: str
    params: dict[str, Any] = {}
    #: The schema built for a given call constrains this to an enum of the retrieved
    #: chunk ids encoded as *strings* (see `_build_schema`); kept `int` here anyway
    #: because pydantic's lax int validation already accepts a numeral string and
    #: coerces it ("58" -> 58), so a well-behaved provider's string answer and this
    #: module's own hand-authored (int-literal) test fixtures both validate without
    #: a second field or a manual parse step. A value that isn't a clean integer at
    #: all (not on the schema's enum, or mangled) fails here as a `ValidationError`,
    #: caught below and re-raised as `ExtractionInvalid`.
    source_chunk_id: int


class _RawSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criteria: list[_RawCriterion]


class _RawExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sets: list[_RawSet]


_BASE_SCHEMA = _RawExtraction.model_json_schema()


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


def _describe_fact_args(criterion_type: CriterionType) -> str:
    """The facts a given `CriterionType` may name, inline with any `fact_args` that
    fact requires -- shown at the point the model decides `type`, not introduced
    separately afterwards, so a model following the `threshold` line literally does
    not omit `fact_args` on an adherence criterion."""
    parts = []
    for fact_name, spec in sorted(FACTS.items()):
        if criterion_type not in spec.permitted_types:
            continue
        qualifiers = []
        if spec.fetch_args:
            shape = ", ".join(f'"{key}": <{typ.__name__}>' for key, typ in spec.fetch_args.items())
            qualifiers.append(f'requires `"fact_args": {{{shape}}}`')
        if spec.permitted_values is not None:
            # Shown inline at the point the model picks the fact, for the same reason
            # fetch_args is: a vocabulary introduced separately gets skipped. Without
            # this the model writes the policy's words ("attended PSG") where the record
            # holds `home_type_ii`, and every case escalates on a criterion that was met.
            listed = ", ".join(f"`{value}`" for value in sorted(spec.permitted_values))
            qualifiers.append(f"`allowed` must be drawn from exactly: {listed}")
        if qualifiers:
            parts.append(f"`{fact_name}` ({'; '.join(qualifiers)})")
        else:
            parts.append(f"`{fact_name}`")
    return ", ".join(parts)


def _build_system_prompt() -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(
        criterion_types=[t.value for t in CriterionType],
        threshold_operators=sorted(THRESHOLD_OPERATORS),
        threshold_facts=_describe_fact_args(CriterionType.THRESHOLD),
        enum_facts=_describe_fact_args(CriterionType.ENUM),
        temporal_operators=sorted(TEMPORAL_OPERATORS),
        temporal_facts=_describe_fact_args(CriterionType.TEMPORAL),
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


def _build_schema(retrieved_ids: list[int]) -> dict[str, Any]:
    """Built per call, not once at import: `source_chunk_id` is constrained to an
    enum of the ids retrieved for *this* case, encoded as strings, rather than a bare
    `integer`.

    This is the predecessor project's own lesson, carried forward deliberately (the
    brief points at it directly): Deflect's answer service once asked a local model
    for a bare integer array of citation ids and got `[4161, 2750, 4179]` back
    concatenated into a single malformed value, `4161275041794180` -- a
    well-typed-looking but wrong answer. A closed string enum leaves the model
    nothing to format: it can only select one of the ids actually shown to it.
    `extract()` below still validates membership after the fact (a schema is a
    request, not a guarantee against every provider), but constraining the schema is
    what stops a malformed id from being emitted in the first place, which is
    strictly better than catching it afterwards as an escalation."""
    schema = deepcopy(_BASE_SCHEMA)
    schema["$defs"]["_RawCriterion"]["properties"]["source_chunk_id"] = {
        "type": "string",
        "enum": [str(chunk_id) for chunk_id in retrieved_ids],
    }
    return schema


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

    schema = _build_schema(sorted(retrieved))
    response = await llm.chat(_build_messages(hits), schema)
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

            # The schema already constrained this to a string enum of retrieved ids
            # (see `_build_schema`); this membership check is what makes rejection of
            # anything else certain rather than merely likely, and is what a
            # provider that ignores `format` (or a hand-authored test fixture) still
            # goes through.
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
