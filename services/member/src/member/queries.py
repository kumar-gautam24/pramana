"""Factual questions the deterministic criterion verifiers ask against NCD 240.4.

Each function answers exactly one question about the record -- coverage, history,
adherence -- and returns a fact, never a verdict. ADR-0003 draws the line between what
code decides and what a model decides at "facts vs. judgment"; a function here that
folded eligibility logic in (an "is_eligible" instead of "coverage_active") would move
that line into the one service that isn't supposed to hold it.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from member.models import Condition, CpapUsage, Member, Note, SleepStudy


@dataclass(frozen=True)
class Adherence:
    #: Nights with any usage row in the window, qualifying or not -- the denominator a
    #: caller needs to see the 0/0 case for what it is rather than infer it from fraction.
    nights: int
    #: Counted against the caller's `min_hours`, not a threshold this service holds.
    qualifying_nights: int
    fraction: float


async def coverage_active(session: AsyncSession, member_id: str, on: date) -> bool:
    """Whether `member_id` had active coverage on `on`.

    Both bounds are inclusive: a member whose coverage ends on the date of service was
    still covered that day, so `coverage_end < on` (not `<=`) is what excludes them.
    `coverage_end IS NULL` is open-ended and never excludes.
    """
    member = await session.get(Member, member_id)
    if member is None:
        return False
    if member.coverage_start > on:
        return False
    if member.coverage_end is not None and member.coverage_end < on:
        return False
    return True


async def sleep_studies_before(
    session: AsyncSession, member_id: str, on: date
) -> list[SleepStudy]:
    """Studies on or before `on`. A study performed after the date of service cannot
    justify coverage on it, so `on` is inclusive but nothing later is."""
    result = await session.execute(
        select(SleepStudy)
        .where(SleepStudy.member_id == member_id, SleepStudy.date <= on)
        .order_by(SleepStudy.date)
    )
    return list(result.scalars().all())


async def conditions_before(
    session: AsyncSession, member_id: str, on: date, codes: list[str]
) -> list[Condition]:
    """Conditions among `codes` with onset on or before `on`. Same "before the date of
    service" boundary as sleep studies, restricted to the codes a criterion names so an
    unrelated comorbidity in the record can't be mistaken for a relevant one."""
    result = await session.execute(
        select(Condition)
        .where(
            Condition.member_id == member_id,
            Condition.onset_date <= on,
            Condition.code.in_(codes),
        )
        .order_by(Condition.onset_date)
    )
    return list(result.scalars().all())


async def adherence(
    session: AsyncSession, member_id: str, start: date, end: date, min_hours: float
) -> Adherence:
    """Nights of CPAP usage within `[start, end]`, both bounds inclusive, and how many
    of them logged at least `min_hours`.

    `min_hours` is required rather than defaulted for the same reason `codes` is on
    `conditions_before`: four hours a night is NCD 240.4's number, and a threshold
    written here would be this service deciding what the policy says. A default would
    read as the answer and be used as one -- so the caller that owns the policy states
    it (ADR-0003).

    The caller supplies the window too (adjudication computes the consecutive 30-day
    span from the date of service); this only counts what falls inside the bounds it's
    given, so usage outside the window can't inflate a member's qualifying-night count.
    """
    result = await session.execute(
        select(CpapUsage).where(
            CpapUsage.member_id == member_id,
            CpapUsage.night >= start,
            CpapUsage.night <= end,
        )
    )
    usages = list(result.scalars().all())
    nights = len(usages)
    qualifying_nights = sum(1 for usage in usages if usage.hours >= min_hours)
    # A member who never used the device must fail the criterion cleanly -- 0/0 is a
    # fact about the record, not a program error, so this returns 0.0 rather than
    # raising ZeroDivisionError and turning a refusal into a 500.
    fraction = qualifying_nights / nights if nights else 0.0
    return Adherence(nights=nights, qualifying_nights=qualifying_nights, fraction=fraction)


async def notes_before(session: AsyncSession, member_id: str, on: date) -> list[Note]:
    """Notes on or before `on`, for the symptom-documentation judgment call the model
    still has to make (see notes.py) -- this only bounds which notes are in scope."""
    result = await session.execute(
        select(Note).where(Note.member_id == member_id, Note.date <= on).order_by(Note.date)
    )
    return list(result.scalars().all())
