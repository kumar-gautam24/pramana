from datetime import date

import pytest

from member.repositories import conditions as conditions_repo
from member.repositories import cpap_usage as cpap_usage_repo
from member.repositories import members as members_repo
from member.repositories import notes as notes_repo
from member.repositories import sleep_studies as sleep_studies_repo


async def _insert_member(
    db_session, member_id: str, coverage_start: date, coverage_end: date | None
) -> None:
    await members_repo.insert(
        db_session,
        id=member_id,
        birth_date=date(1970, 1, 1),
        sex="F",
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )


async def test_coverage_is_inclusive_of_both_bounds(db_session):
    """A member whose coverage ends on the date of service is covered that day. Treating
    the end as exclusive denies a member a day of coverage they had."""
    await _insert_member(db_session, "m-coverage-end", date(2020, 1, 1), date(2026, 6, 30))

    m = "m-coverage-end"
    assert await members_repo.coverage_active(db_session, m, date(2026, 6, 30)) is True
    assert await members_repo.coverage_active(db_session, m, date(2026, 7, 1)) is False
    # The start bound is inclusive in the same direction: coverage begins on its own date.
    assert await members_repo.coverage_active(db_session, m, date(2020, 1, 1)) is True
    assert await members_repo.coverage_active(db_session, m, date(2019, 12, 31)) is False


async def test_open_ended_coverage_covers_the_future(db_session):
    await _insert_member(db_session, "m-open-ended", date(2020, 1, 1), None)

    assert await members_repo.coverage_active(db_session, "m-open-ended", date(2099, 1, 1)) is True


async def _insert_three_nights(db_session, member_id: str) -> None:
    await _insert_member(db_session, member_id, date(2020, 1, 1), None)
    await cpap_usage_repo.insert_many(
        db_session,
        member_id,
        [
            (date(2026, 1, 1), 4.0),
            (date(2026, 1, 2), 3.99),
            (date(2026, 1, 3), 5.0),
        ],
    )


async def test_adherence_counts_nights_at_or_above_the_callers_threshold(db_session):
    """The caller passing NCD 240.4's four hours gets 'greater than or equal to 4 hours
    per night' -- exactly 4.0 counts."""
    await _insert_three_nights(db_session, "m-threshold")

    result = await cpap_usage_repo.adherence(
        db_session, "m-threshold", date(2026, 1, 1), date(2026, 1, 3), min_hours=4.0
    )

    assert result.nights == 3
    assert result.qualifying_nights == 2
    assert result.fraction == pytest.approx(2 / 3)


async def test_the_nightly_hours_bar_is_the_callers_and_not_this_services(db_session):
    """Four hours is NCD 240.4's number, not this service's. A threshold hardcoded here
    would be the facts service holding a policy value (ADR-0003) -- and would answer the
    same for every policy, including one that asks a different question of the same rows.
    The same rows must therefore count differently under a different bar."""
    await _insert_three_nights(db_session, "m-callers-bar")

    strict = await cpap_usage_repo.adherence(
        db_session, "m-callers-bar", date(2026, 1, 1), date(2026, 1, 3), min_hours=5.0
    )
    lenient = await cpap_usage_repo.adherence(
        db_session, "m-callers-bar", date(2026, 1, 1), date(2026, 1, 3), min_hours=3.0
    )

    assert strict.qualifying_nights == 1
    assert lenient.qualifying_nights == 3


async def test_adherence_window_excludes_nights_outside_it(db_session):
    """The rule is 70% within a consecutive 30-day window. Counting nights outside the
    window would approve a member on usage the policy does not consider."""
    await _insert_member(db_session, "m-window", date(2020, 1, 1), None)
    await cpap_usage_repo.insert_many(
        db_session,
        "m-window",
        [
            # Before the window: a qualifying night the query must still ignore.
            (date(2025, 12, 31), 8.0),
            (date(2026, 1, 1), 5.0),
            (date(2026, 1, 30), 5.0),
            # After the window: same, must still be ignored.
            (date(2026, 1, 31), 8.0),
        ],
    )

    result = await cpap_usage_repo.adherence(
        db_session, "m-window", date(2026, 1, 1), date(2026, 1, 30), min_hours=4.0
    )

    assert result.nights == 2
    assert result.qualifying_nights == 2


async def test_adherence_with_no_nights_is_zero_not_an_error(db_session):
    """A member who never used the device has 0/0. Dividing would raise; reporting
    fraction 0.0 lets the criterion fail cleanly rather than crashing the case."""
    await _insert_member(db_session, "m-no-usage", date(2020, 1, 1), None)

    result = await cpap_usage_repo.adherence(
        db_session, "m-no-usage", date(2026, 1, 1), date(2026, 1, 30), min_hours=4.0
    )

    assert result.nights == 0
    assert result.qualifying_nights == 0
    assert result.fraction == 0.0


async def test_conditions_before_excludes_later_onsets(db_session):
    """A comorbidity diagnosed after the date of service cannot justify coverage on it."""
    await _insert_member(db_session, "m-onset", date(2020, 1, 1), None)
    # Onset exactly on the date of service counts.
    await conditions_repo.insert(
        db_session,
        member_id="m-onset",
        code="E66.9",
        description="Obesity",
        onset_date=date(2026, 1, 5),
    )
    await conditions_repo.insert(
        db_session,
        member_id="m-onset",
        code="I10",
        description="Essential hypertension",
        onset_date=date(2026, 1, 6),
    )

    result = await conditions_repo.conditions_before(
        db_session, "m-onset", date(2026, 1, 5), codes=["E66.9", "I10"]
    )

    assert [c.code for c in result] == ["E66.9"]


async def test_conditions_filters_by_code(db_session):
    await _insert_member(db_session, "m-codes", date(2020, 1, 1), None)
    await conditions_repo.insert(
        db_session,
        member_id="m-codes",
        code="E66.9",
        description="Obesity",
        onset_date=date(2020, 1, 1),
    )
    await conditions_repo.insert(
        db_session,
        member_id="m-codes",
        code="J45.909",
        description="Asthma",
        onset_date=date(2020, 1, 1),
    )

    result = await conditions_repo.conditions_before(
        db_session, "m-codes", date(2026, 1, 1), codes=["E66.9"]
    )

    assert [c.code for c in result] == ["E66.9"]


async def test_sleep_studies_before_excludes_later_studies(db_session):
    await _insert_member(db_session, "m-studies", date(2020, 1, 1), None)
    await sleep_studies_repo.insert(
        db_session,
        member_id="m-studies",
        date=date(2026, 1, 5),
        test_type="attended_psg",
        channels=10,
        apnea_events=100,
        recorded_hours=6.0,
        ahi=16.7,
    )
    await sleep_studies_repo.insert(
        db_session,
        member_id="m-studies",
        date=date(2026, 1, 6),
        test_type="attended_psg",
        channels=10,
        apnea_events=110,
        recorded_hours=6.0,
        ahi=18.3,
    )

    result = await sleep_studies_repo.sleep_studies_before(
        db_session, "m-studies", date(2026, 1, 5)
    )

    assert len(result) == 1
    assert result[0].date == date(2026, 1, 5)


async def test_notes_before_excludes_later_notes(db_session):
    await _insert_member(db_session, "m-notes", date(2020, 1, 1), None)
    await notes_repo.insert(
        db_session, member_id="m-notes", date=date(2026, 1, 5), text="Reports insomnia."
    )
    await notes_repo.insert(
        db_session, member_id="m-notes", date=date(2026, 1, 6), text="Follow-up visit."
    )

    result = await notes_repo.notes_before(db_session, "m-notes", date(2026, 1, 5))

    assert len(result) == 1
    assert result[0].text == "Reports insomnia."
