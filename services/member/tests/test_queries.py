from datetime import date

import pytest

from member.models import Condition, CpapUsage, Member, Note, SleepStudy
from member.queries import (
    adherence,
    conditions_before,
    coverage_active,
    notes_before,
    sleep_studies_before,
)


async def _insert_member(
    db_session, member_id: str, coverage_start: date, coverage_end: date | None
) -> None:
    # Flushed on its own, before any child row is added: these models have no
    # `relationship()` declared (see models.py), only raw FK columns, so the unit of
    # work has nothing to infer insert order from and won't reliably put the parent
    # row first if both are pending in the same flush.
    db_session.add(
        Member(
            id=member_id,
            birth_date=date(1970, 1, 1),
            sex="F",
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
    )
    await db_session.flush()


async def test_coverage_is_inclusive_of_both_bounds(db_session):
    """A member whose coverage ends on the date of service is covered that day. Treating
    the end as exclusive denies a member a day of coverage they had."""
    await _insert_member(db_session, "m-coverage-end", date(2020, 1, 1), date(2026, 6, 30))

    assert await coverage_active(db_session, "m-coverage-end", date(2026, 6, 30)) is True
    assert await coverage_active(db_session, "m-coverage-end", date(2026, 7, 1)) is False
    # The start bound is inclusive in the same direction: coverage begins on its own date.
    assert await coverage_active(db_session, "m-coverage-end", date(2020, 1, 1)) is True
    assert await coverage_active(db_session, "m-coverage-end", date(2019, 12, 31)) is False


async def test_open_ended_coverage_covers_the_future(db_session):
    await _insert_member(db_session, "m-open-ended", date(2020, 1, 1), None)

    assert await coverage_active(db_session, "m-open-ended", date(2099, 1, 1)) is True


async def test_adherence_counts_only_nights_at_or_above_four_hours(db_session):
    """NCD 240.4 says 'greater than or equal to 4 hours per night'. Exactly 4.0 counts."""
    await _insert_member(db_session, "m-threshold", date(2020, 1, 1), None)
    db_session.add_all(
        [
            CpapUsage(member_id="m-threshold", night=date(2026, 1, 1), hours=4.0),
            CpapUsage(member_id="m-threshold", night=date(2026, 1, 2), hours=3.99),
            CpapUsage(member_id="m-threshold", night=date(2026, 1, 3), hours=5.0),
        ]
    )
    await db_session.flush()

    result = await adherence(db_session, "m-threshold", date(2026, 1, 1), date(2026, 1, 3))

    assert result.nights == 3
    assert result.qualifying_nights == 2
    assert result.fraction == pytest.approx(2 / 3)


async def test_adherence_window_excludes_nights_outside_it(db_session):
    """The rule is 70% within a consecutive 30-day window. Counting nights outside the
    window would approve a member on usage the policy does not consider."""
    await _insert_member(db_session, "m-window", date(2020, 1, 1), None)
    db_session.add_all(
        [
            # Before the window: a qualifying night the query must still ignore.
            CpapUsage(member_id="m-window", night=date(2025, 12, 31), hours=8.0),
            CpapUsage(member_id="m-window", night=date(2026, 1, 1), hours=5.0),
            CpapUsage(member_id="m-window", night=date(2026, 1, 30), hours=5.0),
            # After the window: same, must still be ignored.
            CpapUsage(member_id="m-window", night=date(2026, 1, 31), hours=8.0),
        ]
    )
    await db_session.flush()

    result = await adherence(db_session, "m-window", date(2026, 1, 1), date(2026, 1, 30))

    assert result.nights == 2
    assert result.qualifying_nights == 2


async def test_adherence_with_no_nights_is_zero_not_an_error(db_session):
    """A member who never used the device has 0/0. Dividing would raise; reporting
    fraction 0.0 lets the criterion fail cleanly rather than crashing the case."""
    await _insert_member(db_session, "m-no-usage", date(2020, 1, 1), None)

    result = await adherence(db_session, "m-no-usage", date(2026, 1, 1), date(2026, 1, 30))

    assert result.nights == 0
    assert result.qualifying_nights == 0
    assert result.fraction == 0.0


async def test_conditions_before_excludes_later_onsets(db_session):
    """A comorbidity diagnosed after the date of service cannot justify coverage on it."""
    await _insert_member(db_session, "m-onset", date(2020, 1, 1), None)
    db_session.add_all(
        [
            # Onset exactly on the date of service counts.
            Condition(
                member_id="m-onset",
                code="E66.9",
                description="Obesity",
                onset_date=date(2026, 1, 5),
            ),
            Condition(
                member_id="m-onset",
                code="I10",
                description="Essential hypertension",
                onset_date=date(2026, 1, 6),
            ),
        ]
    )
    await db_session.flush()

    result = await conditions_before(
        db_session, "m-onset", date(2026, 1, 5), codes=["E66.9", "I10"]
    )

    assert [c.code for c in result] == ["E66.9"]


async def test_conditions_filters_by_code(db_session):
    await _insert_member(db_session, "m-codes", date(2020, 1, 1), None)
    db_session.add_all(
        [
            Condition(
                member_id="m-codes",
                code="E66.9",
                description="Obesity",
                onset_date=date(2020, 1, 1),
            ),
            Condition(
                member_id="m-codes",
                code="J45.909",
                description="Asthma",
                onset_date=date(2020, 1, 1),
            ),
        ]
    )
    await db_session.flush()

    result = await conditions_before(db_session, "m-codes", date(2026, 1, 1), codes=["E66.9"])

    assert [c.code for c in result] == ["E66.9"]


async def test_sleep_studies_before_excludes_later_studies(db_session):
    await _insert_member(db_session, "m-studies", date(2020, 1, 1), None)
    db_session.add_all(
        [
            SleepStudy(
                member_id="m-studies",
                date=date(2026, 1, 5),
                test_type="attended_psg",
                channels=10,
                apnea_events=100,
                recorded_hours=6.0,
                ahi=16.7,
            ),
            SleepStudy(
                member_id="m-studies",
                date=date(2026, 1, 6),
                test_type="attended_psg",
                channels=10,
                apnea_events=110,
                recorded_hours=6.0,
                ahi=18.3,
            ),
        ]
    )
    await db_session.flush()

    result = await sleep_studies_before(db_session, "m-studies", date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].date == date(2026, 1, 5)


async def test_notes_before_excludes_later_notes(db_session):
    await _insert_member(db_session, "m-notes", date(2020, 1, 1), None)
    db_session.add_all(
        [
            Note(member_id="m-notes", date=date(2026, 1, 5), text="Reports insomnia."),
            Note(member_id="m-notes", date=date(2026, 1, 6), text="Follow-up visit."),
        ]
    )
    await db_session.flush()

    result = await notes_before(db_session, "m-notes", date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].text == "Reports insomnia."
