"""The system's decision on a case -- approve or escalate, never deny.

A case may be adjudicated more than once, so nothing here is unique per case_id; the
current determination is the newest by created_at, then id. A superseded row is never
deleted -- it is what the previous decision was."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pramana_common.criteria import GateReason, Outcome


@dataclass(frozen=True)
class Determination:
    id: int
    case_id: str
    #: Structurally incapable of holding a denial -- see the CHECK constraint in
    #: migrations/0001_cases_and_determinations.sql and ADR-0002.
    outcome: Outcome
    #: None on approve; the gate's closed-set reason on escalate.
    reason: GateReason | None
    #: The unmet or insufficient-evidence criteria closest to satisfying the policy --
    #: see ADR-0011 on why the *closest* set, not every set's failures.
    blocking: list[Any]
    #: The gate's configuration at decision time (e.g. min_confidence), so a
    #: determination stays explainable after the configuration later changes.
    thresholds: dict[str, Any]
    #: None on escalation. On approval, names the satisfied set (criteria.set_ordinal)
    #: -- the audit answer to "which path approved this".
    winning_set: int | None
    created_at: datetime
