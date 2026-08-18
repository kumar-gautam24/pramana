"""A polysomnography or home sleep study result."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SleepStudy:
    id: int
    member_id: str
    date: date
    #: Attended PSG or a Type II/III/IV home study; which one governs which channel
    #: threshold applies.
    test_type: str
    #: Type IV needs at least 3 channels -- the policy's own cutoff.
    channels: int
    #: Stored alongside the derived index, not instead of it: NCD 240.4 states its
    #: criteria both as a raw event/hour count and as AHI, and only keeping AHI would
    #: make the first form unanswerable.
    apnea_events: int
    recorded_hours: float
    ahi: float
