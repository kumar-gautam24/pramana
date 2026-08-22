"""One entry in a case's audit trail. Append-only, enforced by the database -- see the
triggers in migrations/0001_cases_and_determinations.sql and ADR-0005."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CaseEvent:
    id: int
    case_id: str
    #: Per-case and assigned by the writer, not a global sequence -- what makes
    #: UNIQUE (case_id, seq) mean anything: a gap or a duplicate is a constraint
    #: violation rather than a silent reordering.
    seq: int
    type: str
    payload: dict[str, Any]
    created_at: datetime
