"""The verdict recorded for one criterion, with the evidence and tool that produced it.

Distinct from `pramana_common.criteria.CriterionResult`: that one is the lightweight
wire shape the gate evaluates (criterion_id, verdict, confidence). This is the full
persisted row -- it adds the id, which tool produced the verdict, and the evidence a
reviewer would need to check the tool's work."""

from dataclasses import dataclass
from typing import Any

from pramana_common.criteria import Verdict


@dataclass(frozen=True)
class CriterionResult:
    id: int
    criterion_id: int
    verdict: Verdict
    confidence: float
    #: Which deterministic tool (or, for judgment criteria, which model call)
    #: produced this verdict. Free text: the set of tools grows without a migration.
    tool: str
    evidence: dict[str, Any]
