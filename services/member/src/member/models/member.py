"""A covered plan member: the Synthea patient identity plus the coverage window
criteria are judged against."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Member:
    #: The Synthea patient UUID, reused rather than surrogate-keyed: a regenerated
    #: population maps onto the same rows.
    id: str
    birth_date: date
    sex: str
    coverage_start: date
    #: None means open-ended coverage. A far-future sentinel date would silently expire
    #: a member's coverage on that day.
    coverage_end: date | None
