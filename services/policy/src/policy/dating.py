"""Choosing the version of a policy that governs a given date of service.

A case is adjudicated against the policy in force on the date of service, not today's
policy. Coverage determinations are revised, and judging a 2015 claim by a 2020 rule is
wrong in the direction that harms the member."""

from datetime import date
from typing import Protocol


class Versioned(Protocol):
    effective_from: date
    effective_to: date | None


def in_force_on[V: Versioned](versions: list[V], on: date) -> V | None:
    """The version covering `on`, or None if no version does.

    Both bounds are inclusive: CMS states an end date as the last day the version applies.
    Returning None is a real answer -- a date before any version means the determination
    did not yet exist, which must escalate rather than fall back to the earliest rule."""
    covering = [
        v
        for v in versions
        if v.effective_from <= on and (v.effective_to is None or on <= v.effective_to)
    ]
    if not covering:
        return None
    # Overlapping ranges do occur. The later determination governs; without this the
    # result would depend on row order.
    return max(covering, key=lambda v: v.effective_from)
