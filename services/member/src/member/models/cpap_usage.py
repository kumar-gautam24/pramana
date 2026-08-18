"""One night of recorded CPAP usage."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CpapUsage:
    id: int
    member_id: str
    night: date
    hours: float
