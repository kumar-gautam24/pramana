"""A single testable condition extracted from a coverage policy."""

from dataclasses import dataclass
from typing import Any

from pramana_common.criteria import CriterionType


@dataclass(frozen=True)
class Criterion:
    id: int
    case_id: str
    #: Criteria sharing set_ordinal belong to the same alternative set in the
    #: extracted disjunctive-normal-form policy -- see ADR-0011.
    set_ordinal: int
    #: Position within the set.
    ordinal: int
    text: str
    type: CriterionType
    #: Parameters a deterministic tool compares against member facts (a threshold
    #: value, an enum's allowed members, a temporal window) -- shaped by `type`.
    params: dict[str, Any]
    #: policy's chunk id and human-readable citation, recorded as values -- no
    #: foreign key crosses a service boundary.
    source_chunk_id: int
    source_display_id: str
