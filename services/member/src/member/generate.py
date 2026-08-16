"""Generates the sleep-domain facts Synthea has no module for (ADR-0012).

Synthea's synthetic patients carry no polysomnography, AHI, or CPAP data at all, so
every clinically decisive fact NCD 240.4 adjudicates on has to be authored here. That
makes this module test apparatus in its own right, not a convenience script: a target
this generator cannot actually produce is a gap in eval coverage that no test reports.

Pure by construction: only stdlib `random`, seeded from the arguments callers already
have, so a golden case is reproducible from its own row without a database round trip.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta

#: AHI-shaped cases. Each name is a claim about where the resulting profile sits
#: relative to the NCD 240.4 threshold (15 events/hour) -- see the module docstring.
STUDY_TARGETS = frozenset(
    {
        "qualifying",
        "borderline_high",
        "near_miss_high",
        "mild_range",
        "below_threshold",
    }
)

#: Adherence-shaped cases, judged against the 70%-of-nights threshold instead of AHI.
USAGE_TARGETS = frozenset({"adherent", "near_miss_adherence"})

#: The full contract callers can rely on. Kept as one set, not two, so
#: `target in TARGETS` answers "is this spelled right" while the generators
#: below separately enforce "is this the right kind of target".
TARGETS = STUDY_TARGETS | USAGE_TARGETS

#: (test_type, channel_count range). Channel count is what a real reviewer checks to
#: tell a Type IV home study (the policy's minimum) from an attended lab study; the
#: exact type doesn't gate any of the AHI targets above, so it's drawn independently.
_TEST_TYPES: dict[str, tuple[int, int]] = {
    "attended_psg": (8, 16),
    "home_type_ii": (5, 7),
    "home_type_iii": (3, 4),
    "home_type_iv": (1, 3),
}

#: (low, high) AHI bounds per study target, inclusive. `borderline_high` is handled
#: separately below because hitting exactly 15.0 needs integer hours, not a band.
_AHI_BANDS: dict[str, tuple[float, float]] = {
    "qualifying": (20.0, 60.0),
    "near_miss_high": (14.0, 14.99),
    "mild_range": (5.0, 14.0),
    "below_threshold": (1.0, 4.9),
}


@dataclass(frozen=True)
class SleepProfile:
    test_type: str
    channels: int
    apnea_events: int
    recorded_hours: float
    ahi: float
    study_date: date


def _rng(member_id: str, seed: int, target: str) -> random.Random:
    # Combining all three into the seed string is what makes the stream depend on
    # each of them: drop the target and every target would draw the same numbers;
    # drop the member and a population would be 500 copies of one case.
    return random.Random(f"{member_id}:{seed}:{target}")


def generate_sleep_profile(
    member_id: str, seed: int, study_date: date, target: str = "qualifying"
) -> SleepProfile:
    if target not in STUDY_TARGETS:
        # Covers both a misspelled target and a usage target handed to the wrong
        # generator -- either way the caller asked for a study shape that doesn't exist.
        raise ValueError(f"unknown sleep-study target: {target!r}")

    rng = _rng(member_id, seed, target)
    test_type = rng.choice(list(_TEST_TYPES))
    channels = rng.randint(*_TEST_TYPES[test_type])

    if target == "borderline_high":
        # Exact threshold equality can't survive rounding a fractional apnea-event
        # count, so pin recorded_hours to an integer and derive events as an exact
        # multiple of 15 -- the division then lands on 15.0 with no float drift.
        recorded_hours = float(rng.randint(5, 9))
        apnea_events = 15 * int(recorded_hours)
    else:
        recorded_hours = round(rng.uniform(5.0, 9.0), 2)
        low, high = _AHI_BANDS[target]
        # Derive the event count from the band and the hours actually drawn, rather
        # than drawing an AHI and rounding it into an event count: ceil/floor here
        # guarantee events / recorded_hours lands inside [low, high], where rounding
        # an independently-drawn AHI could push the ratio outside the named band.
        import math

        lo_events = math.ceil(low * recorded_hours)
        hi_events = math.floor(high * recorded_hours)
        apnea_events = rng.randint(lo_events, hi_events)

    ahi = apnea_events / recorded_hours

    return SleepProfile(
        test_type=test_type,
        channels=channels,
        apnea_events=apnea_events,
        recorded_hours=recorded_hours,
        ahi=ahi,
        study_date=study_date,
    )


def generate_usage_nights(
    member_id: str,
    seed: int,
    start: date,
    nights: int,
    target: str = "adherent",
) -> list[tuple[date, float]]:
    if target not in USAGE_TARGETS:
        raise ValueError(f"unknown CPAP-usage target: {target!r}")

    rng = _rng(member_id, seed, target)

    # As with the AHI bands, derive the *count* of qualifying nights from a fraction
    # range via floor/ceil instead of coin-flipping each night independently -- a coin
    # flip's qualifying fraction only converges to its probability in expectation,
    # which is exactly what a near-miss golden case can't rely on across 30 nights.
    if target == "adherent":
        lo_frac, hi_frac = 0.75, 0.95
    else:
        lo_frac, hi_frac = 0.60, 0.69

    lo_count = math.ceil(lo_frac * nights)
    hi_count = math.floor(hi_frac * nights)
    qualifying_count = rng.randint(lo_count, hi_count)

    qualifying_indices = set(rng.sample(range(nights), qualifying_count))

    result = []
    for i in range(nights):
        night = start + timedelta(days=i)
        if i in qualifying_indices:
            hours = round(rng.uniform(4.0, 8.0), 2)
        else:
            hours = round(rng.uniform(0.0, 3.9), 2)
        result.append((night, hours))

    return result
