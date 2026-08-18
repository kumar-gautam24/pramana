"""The `params` contract for each `CriterionType` -- the seam between extraction
(`services/extract.py`, populating `params` from a model's output) and verification
(Task 6, reading this module's validated shape to know what to compare and how).

`validate_params` is total: it raises `ExtractionInvalid` on anything that does not
match a type's exact shape, and never coerces or drops a field. A `params` dict this
module cannot validate is one a deterministic verifier cannot safely act on, so the
criterion it belongs to must be rejected, not passed through in a guessed-at shape.

Pure: no I/O, no imports of `db`, `asyncpg`, `httpx`, or `services/`."""

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


#: The complete set of facts the member service can answer a question about -- the
#: API surface of `MemberClient`, not a per-policy vocabulary (see CLAUDE.md
#: invariant 3: this hardcodes nothing about NCD 240.4 or any other policy). A
#: criterion naming a fact outside this set is one the system has no way to check,
#: and that must be rejected here rather than guessed at by a verifier later.
#:
#:   ahi, channels, apnea_events, recorded_hours, test_type -- MemberClient.sleep_studies
#:   study_date               -- MemberClient.sleep_studies (SleepStudy.date)
#:   condition_codes          -- MemberClient.conditions
#:   adherence_fraction, adherence_nights -- MemberClient.adherence
#:   coverage_active          -- MemberClient.coverage
FACTS = frozenset(
    {
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
)

#: Carried explicitly rather than parsed out of prose -- the >=-vs-> distinction has
#: caused two defects in this project already.
THRESHOLD_OPERATORS = frozenset({">=", ">", "<=", "<", "=="})

#: Direction relative to the case's date of service, the only temporal anchor a case
#: has. Carried explicitly for the same reason as THRESHOLD_OPERATORS: a bare "within
#: N days" is ambiguous about which side of the anchor it means, and that ambiguity
#: is exactly the kind this project has already been burned by once.
TEMPORAL_OPERATORS = frozenset({"within_days_before", "within_days_after"})

#: Facts the member service cannot answer without extra arguments, and the type each
#: argument must have. Every fact *not* listed here is answerable from the case's
#: date of service alone (a single `before`/`on` cutoff `extract.py`'s caller
#: supplies, not something a criterion needs to carry).
#:
#: `MemberClient.adherence(member_id, start, end, min_hours)` needs a window and a
#: nightly-hours bar before it will answer at all, and `min_hours` has no default on
#: that endpoint deliberately -- the nightly-hours bar is the *policy's* number, not
#: the member service's. Without a place to carry it, a verifier would have to
#: hardcode it, which is exactly the per-policy hardcoding invariant 3 forbids (see
#: ADR-0003) -- and it is NCD 240.4's actual continuation criterion ("CPAP used >= 4
#: hours per night on 70% of nights in a 30-day period"), not a hypothetical case.
FACT_ARGS: dict[str, dict[str, type]] = {
    "adherence_fraction": {"min_hours": float, "window_days": int},
    "adherence_nights": {"min_hours": float, "window_days": int},
}


def _is_number(value: Any) -> bool:
    # bool is a subclass of int; a criterion parameterised with True/False would be a
    # modelling mistake this validator should catch, not silently accept as 1/0.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_positive_of_type(value: Any, expected_type: type) -> bool:
    if expected_type is int:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    return _is_number(value) and value > 0


def _require_fact(params: dict[str, Any], criterion_type: CriterionType) -> str:
    fact = params.get("fact")
    if fact not in FACTS:
        raise ExtractionInvalid(
            f"{criterion_type} criterion names fact {fact!r}, outside the vocabulary the "
            f"member service can answer: {sorted(FACTS)}"
        )
    return fact


def _validate_fact_args(params: dict[str, Any], fact: str, criterion_type: CriterionType) -> None:
    """Both directions matter, and the first is the one that closes the hardcoding
    hole: a fact in `FACT_ARGS` is unusable without its arguments, so omitting them
    must be rejected exactly as firmly as inventing arguments for a fact that takes
    none -- the latter would let the model attach arbitrary keys nothing reads."""
    fact_args = params.get("fact_args")
    required = FACT_ARGS.get(fact)

    if required is None:
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
    if not _is_number(value):
        raise ExtractionInvalid(f"threshold criterion is missing a numeric 'value', got {value!r}")
    _validate_fact_args(params, fact, CriterionType.THRESHOLD)
    _reject_unexpected_keys(
        params, CriterionType.THRESHOLD, {"fact", "operator", "value", "fact_args"}
    )


def _validate_enum(params: dict[str, Any]) -> None:
    fact = _require_fact(params, CriterionType.ENUM)
    allowed = params.get("allowed")
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(member, str) for member in allowed)
    ):
        raise ExtractionInvalid(
            f"enum criterion needs a non-empty list of allowed strings, got {allowed!r}"
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
