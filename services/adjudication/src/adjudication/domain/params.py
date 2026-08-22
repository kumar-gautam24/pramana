"""The `params` contract for each `CriterionType` -- the seam between extraction
(`services/extract.py`, populating `params` from a model's output) and verification
(Task 6, reading this module's validated shape to know what to compare and how).

`validate_params` is total: it raises `ExtractionInvalid` on anything that does not
match a type's exact shape, and never coerces or drops a field. A `params` dict this
module cannot validate is one a deterministic verifier cannot safely act on, so the
criterion it belongs to must be rejected, not passed through in a guessed-at shape.

Pure: no I/O, no imports of `db`, `asyncpg`, `httpx`, or `services/`."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pramana_common.criteria import CriterionType


class ExtractionInvalid(Exception):
    """A model's extraction output does not match what this service can safely act
    on: an unrecognised `type`, a `source_chunk_id` outside the chunks retrieved for
    this case, a `params` shape that does not validate for its `type`, an empty
    criteria set, or more sets than ADR-0011's cap allows.

    Raised rather than coerced. In particular, a criterion whose `type` is not a
    `CriterionType` member must never fall back to `judgment` -- that would hand a
    deterministic check to the model, the ADR-0003 violation that is hardest to see
    afterwards, because the case still gets an answer and the answer still looks
    reasoned. See `services/extract.py`."""


class FactDataType(StrEnum):
    """What a fact's value looks like -- drives the shape check on `threshold`'s
    `value` and `enum`'s `allowed` members, so that check is decided once per fact
    rather than hardcoded once per validator and left to drift out of sync with the
    facts it's supposed to cover (fix round 2: `{"fact": "channels", "allowed": [3, 4]}`
    used to be rejected because `enum` hardcoded a string check regardless of which
    fact was named, and the prompt promised `allowed` could hold any `<value>`)."""

    NUMBER = "number"
    STRING = "string"
    #: Declared for documentation -- `study_date` *is* a date -- but not currently
    #: enforced against any criterion's own params: `temporal`'s `value` is always a
    #: day count (see TEMPORAL_OPERATORS), never the date itself, regardless of which
    #: date fact is named. No current fact needs a literal date value validated.
    DATE = "date"


@dataclass(frozen=True)
class FactSpec:
    datatype: FactDataType
    #: Which `CriterionType`s may name this fact. A `threshold` naming `test_type`
    #: doesn't type-check (there is no numeric "value" a category name could be
    #: compared against); a `threshold` naming `condition_codes` type-checks but
    #: can't be fetched (nothing supplies which codes to count, unlike `enum`, where
    #: `allowed` doubles as that list). Closing this off here, once per fact, is what
    #: rejects every such combination in one place instead of ad hoc per validator.
    permitted_types: frozenset[CriterionType]
    #: Extra arguments `MemberClient` needs before this fact can be fetched at all,
    #: beyond the case's date of service. Empty for every fact answerable from a
    #: single `before`/`on` cutoff.
    fetch_args: dict[str, type] = field(default_factory=dict)
    #: The closed set of values this fact can actually hold in the member record, for
    #: facts whose vocabulary is closed. `None` means genuinely open -- see
    #: `condition_codes`.
    #:
    #: This exists because of a defect a real model produced on the real corpus. Asked
    #: to decompose NCD 240.4, it emitted a correct `test_type` criterion whose `allowed`
    #: list was written in the *policy's* words -- "attended PSG", "Type II HST" -- while
    #: the member service stores `home_type_ii`. Both sides were individually right and
    #: shared not one value, so the enum verifier returned NOT_MET for every member and
    #: every case escalated. No test saw it: the hand-authored fixture happened to use
    #: the verifier's vocabulary.
    #:
    #: Naming the vocabulary here lets the prompt show the model exactly which strings
    #: exist and lets `_validate_enum` reject anything else, so a model writing policy
    #: prose produces a loud `ExtractionInvalid` -- and an escalation that says the
    #: system could not read the policy -- instead of a silent, confident "not met"
    #: about a study that was in fact valid. Same discipline as `source_chunk_id`'s
    #: closed enum in `services.extract`: constrain the answer, then check it anyway.
    permitted_values: frozenset[str] | None = None


#: The complete map of facts the member service can answer a question about -- the
#: API surface of `MemberClient`, not a per-policy vocabulary (see CLAUDE.md
#: invariant 3: this hardcodes nothing about NCD 240.4 or any other policy). A
#: criterion naming a fact outside this map, or naming one under a `CriterionType`
#: its `permitted_types` doesn't allow, is one the system has no way to check, and
#: that must be rejected here rather than guessed at by a verifier later.
#:
#: Source, one row per fact:
#:   ahi, channels, apnea_events, recorded_hours -- MemberClient.sleep_studies (SleepStudy)
#:   test_type                -- MemberClient.sleep_studies (SleepStudy.test_type)
#:   study_date                -- MemberClient.sleep_studies (SleepStudy.date)
#:   condition_codes           -- MemberClient.conditions (Condition.code)
#:   adherence_fraction, adherence_nights -- MemberClient.adherence (Adherence)
#:   coverage_active            -- MemberClient.coverage (CoverageStatus)
#:
#: `MemberClient.adherence(member_id, start, end, min_hours)` needs a window and a
#: nightly-hours bar before it will answer at all, and `min_hours` has no default on
#: that endpoint deliberately -- the nightly-hours bar is the *policy's* number, not
#: the member service's. Without `fetch_args` to carry it, a verifier would have to
#: hardcode it, exactly the per-policy hardcoding invariant 3 forbids -- and it is
#: NCD 240.4's actual continuation criterion, not a hypothetical case.
#:
#: `condition_codes` is `enum`-only, not `threshold`/`temporal`: `MemberClient.conditions`
#: needs a `codes` argument the same way `adherence` needs `min_hours`/`window_days`,
#: but `enum`'s `allowed` list already supplies exactly that -- "the fact is one of
#: these codes" and "these are the codes to look for" are the same list. A `threshold`
#: or `temporal` naming `condition_codes` has no such list to reuse and nowhere else to
#: put one, so it is unfetchable and closed off via `permitted_types` rather than given
#: a second, redundant `fetch_args` shape.
FACTS: dict[str, FactSpec] = {
    "ahi": FactSpec(FactDataType.NUMBER, frozenset({CriterionType.THRESHOLD})),
    "channels": FactSpec(FactDataType.NUMBER, frozenset({CriterionType.THRESHOLD})),
    "apnea_events": FactSpec(FactDataType.NUMBER, frozenset({CriterionType.THRESHOLD})),
    "recorded_hours": FactSpec(FactDataType.NUMBER, frozenset({CriterionType.THRESHOLD})),
    # Source of truth is `member`'s `domain/generate.py`: the four types in `_TEST_TYPES`
    # plus `actigraphy`, which that module emits deliberately for its `invalid_test_type`
    # target. Adjudication already depends on that service's fact *names*; depending on
    # this one fact's values is the same wire contract, and `test_type` is the one enum
    # fact whose vocabulary is closed but not derivable from a shared type (it is a bare
    # `str` on `SleepStudy`).
    #
    # `actigraphy` is listed even though no policy would ever accept such a study,
    # because this table describes what the member record can *hold*, not what a policy
    # may approve. Which study types qualify is the extracted criteria's business
    # (invariant 3: nothing here branches on a policy). Pruning it to the "acceptable"
    # four would be a policy judgment smuggled into a fact declaration -- and it was
    # exactly that kind of quiet assumption, made from the seeded rows rather than from
    # the generator, that made the first draft of this line wrong.
    "test_type": FactSpec(
        FactDataType.STRING,
        frozenset({CriterionType.ENUM}),
        permitted_values=frozenset(
            {"attended_psg", "home_type_ii", "home_type_iii", "home_type_iv", "actigraphy"}
        ),
    ),
    "study_date": FactSpec(FactDataType.DATE, frozenset({CriterionType.TEMPORAL})),
    # Deliberately open: SNOMED is an unbounded vocabulary, and enumerating only the
    # codes that happen to be in the seeded corpus would reject a correct criterion about
    # any condition no test member has -- turning a coverage question into an artefact of
    # the fixture data. The `allowed` list here is a query, not a claim about what exists.
    "condition_codes": FactSpec(FactDataType.STRING, frozenset({CriterionType.ENUM})),
    "adherence_fraction": FactSpec(
        FactDataType.NUMBER,
        frozenset({CriterionType.THRESHOLD}),
        fetch_args={"min_hours": float, "window_days": int},
    ),
    "adherence_nights": FactSpec(
        FactDataType.NUMBER,
        frozenset({CriterionType.THRESHOLD}),
        fetch_args={"min_hours": float, "window_days": int},
    ),
    # `CoverageStatus`'s three members. Not imported from `services.member_client`:
    # `domain/` is pure and must not depend on a service module. `tests/test_params.py`
    # asserts the two agree, which is where that import is legitimate.
    "coverage_active": FactSpec(
        FactDataType.STRING,
        frozenset({CriterionType.ENUM}),
        permitted_values=frozenset({"active", "inactive", "no_record"}),
    ),
}

#: Carried explicitly rather than parsed out of prose -- the >=-vs-> distinction has
#: caused two defects in this project already.
THRESHOLD_OPERATORS = frozenset({">=", ">", "<=", "<", "=="})

#: Direction relative to the case's date of service, the only temporal anchor a case
#: has. Carried explicitly for the same reason as THRESHOLD_OPERATORS: a bare "within
#: N days" is ambiguous about which side of the anchor it means, and that ambiguity
#: is exactly the kind this project has already been burned by once.
TEMPORAL_OPERATORS = frozenset({"within_days_before", "within_days_after"})


def _is_number(value: Any) -> bool:
    # bool is a subclass of int; a criterion parameterised with True/False would be a
    # modelling mistake this validator should catch, not silently accept as 1/0.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_datatype(value: Any, datatype: FactDataType) -> bool:
    if datatype is FactDataType.NUMBER:
        return _is_number(value)
    if datatype is FactDataType.STRING:
        return isinstance(value, str)
    return False


def _is_positive_of_type(value: Any, expected_type: type) -> bool:
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    return _is_number(value) and value > 0


def _require_fact(params: dict[str, Any], criterion_type: CriterionType) -> str:
    fact = params.get("fact")
    spec = FACTS.get(fact)
    if spec is None:
        raise ExtractionInvalid(
            f"{criterion_type} criterion names fact {fact!r}, outside the vocabulary the "
            f"member service can answer: {sorted(FACTS)}"
        )
    if criterion_type not in spec.permitted_types:
        raise ExtractionInvalid(
            f"{criterion_type} criterion names fact {fact!r}, which may only be used "
            f"with {sorted(t.value for t in spec.permitted_types)} criteria"
        )
    return fact


def _validate_fact_args(params: dict[str, Any], fact: str, criterion_type: CriterionType) -> None:
    """Both directions matter, and the first is the one that closes the hardcoding
    hole: a fact with `fetch_args` is unusable without them, so omitting them must be
    rejected exactly as firmly as inventing arguments for a fact that needs none --
    the latter would let the model attach arbitrary keys nothing reads."""
    fact_args = params.get("fact_args")
    required = FACTS[fact].fetch_args

    if not required:
        if fact_args is not None:
            raise ExtractionInvalid(
                f"{criterion_type} criterion names fact {fact!r}, which takes no fact_args, "
                f"but got {fact_args!r}"
            )
        return

    if not isinstance(fact_args, dict) or set(fact_args) != set(required):
        raise ExtractionInvalid(
            f"{criterion_type} criterion names fact {fact!r}, which requires fact_args "
            f"{sorted(required)} exactly, got {fact_args!r}"
        )

    for key, expected_type in required.items():
        value = fact_args[key]
        if not _is_positive_of_type(value, expected_type):
            raise ExtractionInvalid(
                f"{criterion_type} criterion's fact_args.{key} for {fact!r} must be a "
                f"positive {expected_type.__name__}, got {value!r}"
            )


def _reject_unexpected_keys(
    params: dict[str, Any], criterion_type: CriterionType, expected: set[str]
) -> None:
    extra = set(params) - expected
    if extra:
        raise ExtractionInvalid(
            f"{criterion_type} criterion has unexpected params keys: {sorted(extra)}"
        )


def _validate_threshold(params: dict[str, Any]) -> None:
    fact = _require_fact(params, CriterionType.THRESHOLD)
    operator = params.get("operator")
    if operator not in THRESHOLD_OPERATORS:
        raise ExtractionInvalid(
            f"threshold criterion has operator {operator!r}, not one of "
            f"{sorted(THRESHOLD_OPERATORS)}"
        )
    value = params.get("value")
    datatype = FACTS[fact].datatype
    if not _matches_datatype(value, datatype):
        raise ExtractionInvalid(
            f"threshold criterion's value for {fact!r} must be a {datatype.value}, got {value!r}"
        )
    _validate_fact_args(params, fact, CriterionType.THRESHOLD)
    _reject_unexpected_keys(
        params, CriterionType.THRESHOLD, {"fact", "operator", "value", "fact_args"}
    )


def _validate_enum(params: dict[str, Any]) -> None:
    fact = _require_fact(params, CriterionType.ENUM)
    allowed = params.get("allowed")
    datatype = FACTS[fact].datatype
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(_matches_datatype(member, datatype) for member in allowed)
    ):
        raise ExtractionInvalid(
            f"enum criterion needs a non-empty list of {datatype.value} values, got {allowed!r}"
        )
    permitted = FACTS[fact].permitted_values
    if permitted is not None:
        unknown = [member for member in allowed if member not in permitted]
        if unknown:
            raise ExtractionInvalid(
                f"enum criterion on {fact!r} allows {unknown!r}, which the member record "
                f"cannot hold -- permitted values are {sorted(permitted)}"
            )

    _validate_fact_args(params, fact, CriterionType.ENUM)
    _reject_unexpected_keys(params, CriterionType.ENUM, {"fact", "allowed", "fact_args"})


def _validate_temporal(params: dict[str, Any]) -> None:
    fact = _require_fact(params, CriterionType.TEMPORAL)
    operator = params.get("operator")
    if operator not in TEMPORAL_OPERATORS:
        raise ExtractionInvalid(
            f"temporal criterion has operator {operator!r}, not one of "
            f"{sorted(TEMPORAL_OPERATORS)}"
        )
    value = params.get("value")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ExtractionInvalid(
            f"temporal criterion needs a positive integer day count, got {value!r}"
        )
    _validate_fact_args(params, fact, CriterionType.TEMPORAL)
    _reject_unexpected_keys(
        params, CriterionType.TEMPORAL, {"fact", "operator", "value", "fact_args"}
    )


def _validate_judgment(params: dict[str, Any]) -> None:
    # Judgment criteria are decided from the criterion's `text` against retrieved
    # clinical narrative, not a fact -- "which fact" is exactly the deterministic
    # question ADR-0003 keeps out of judgment's hands. No params are defined for this
    # type yet; kept strict (any key rejected) rather than permissive, so a key added
    # here later is a deliberate contract change Task 6 has to notice, not a silent
    # pass-through. This also closes off the fallback this module must never offer:
    # an unrecognised `type` cannot be quietly reinterpreted as `judgment` and then
    # slip past validation just because `{}` happens to be a valid judgment params.
    if params:
        raise ExtractionInvalid(f"judgment criterion takes no params, got keys {sorted(params)}")


_VALIDATORS = {
    CriterionType.THRESHOLD: _validate_threshold,
    CriterionType.ENUM: _validate_enum,
    CriterionType.TEMPORAL: _validate_temporal,
    CriterionType.JUDGMENT: _validate_judgment,
}


def validate_params(criterion_type: CriterionType, params: dict[str, Any]) -> None:
    """Raise `ExtractionInvalid` if `params` does not match `criterion_type`'s shape.

    `criterion_type` must already be a validated `CriterionType` member -- callers
    receiving raw model output validate `type` first (see `services/extract.py`)."""
    _VALIDATORS[criterion_type](params)
