from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pramana_common.criteria import Outcome


class Ablation(StrEnum):
    """Which arithmetic the run used.

    `MODEL_ARITHMETIC` is the ablation ADR-0003 is an argument about: it has the model
    perform the threshold comparisons and date maths that deterministic code otherwise
    does, so the cost of doing it the wrong way can be published rather than asserted."""

    NONE = "none"
    MODEL_ARITHMETIC = "model_arithmetic"


@dataclass(frozen=True)
class EvalRun:
    id: int
    model: str
    prompt_version: str
    thresholds: dict[str, Any]
    git_sha: str
    ablation: Ablation
    status: str
    started_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class EvalResult:
    id: int
    run_id: int
    golden_case_id: int
    case_id: str | None
    #: None when the case never reached a determination -- see migration 0001 for why
    #: that is not collapsed into `escalate`.
    outcome: Outcome | None
    reason: str | None
    criterion_scores: dict[str, Any]
    error: str | None
