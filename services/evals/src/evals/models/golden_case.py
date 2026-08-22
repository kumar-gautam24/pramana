from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pramana_common.criteria import Outcome


@dataclass(frozen=True)
class GoldenCase:
    id: int
    #: The adjudication request body, forwarded verbatim -- see the column comment in
    #: migration 0001 for why this service does not model it field by field.
    fixture: dict[str, Any]
    expected_outcome: Outcome
    expected_criteria: list[Any]
    author: str
    notes: str | None
    created_at: datetime
