"""Schema guarantees the models module used to state as ORM metadata
(Member.__table__.c.coverage_end.nullable, CpapUsage.__table__.constraints,
SleepStudy.__table__.c...). Frozen dataclasses carry no such metadata, so these are
rewritten as behavioural checks against the real schema in
migrations/0001_member_records.sql -- each one is strictly stronger than the assertion
it replaces, since it exercises the constraint instead of reading its declaration."""

from datetime import date

import asyncpg
import pytest

from member.repositories import cpap_usage as cpap_usage_repo
from member.repositories import members as members_repo
from member.repositories import sleep_studies as sleep_studies_repo


async def test_coverage_end_can_be_null(db_session):
    """NULL means open-ended coverage. A far-future sentinel date would silently expire
    a member's coverage on that day -- checked here by inserting NULL and confirming the
    column accepts it, rather than reading a `nullable=True` declaration."""
    member = await members_repo.insert(
        db_session,
        id="m-nullable-coverage-end",
        birth_date=date(1970, 1, 1),
        sex="F",
        coverage_start=date(2020, 1, 1),
        coverage_end=None,
    )

    assert member.coverage_end is None


async def test_one_usage_row_per_member_per_night(db_session):
    """Adherence is a count of qualifying nights. A duplicate night would inflate that
    count and approve a member who did not meet the threshold -- uq_cpap_usage_member_night
    is what makes that a database guarantee rather than a convention application code
    could violate."""
    await members_repo.insert(
        db_session,
        id="m-duplicate-night",
        birth_date=date(1970, 1, 1),
        sex="F",
        coverage_start=date(2020, 1, 1),
        coverage_end=None,
    )
    await cpap_usage_repo.insert_many(
        db_session, "m-duplicate-night", [(date(2026, 1, 1), 5.0)]
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await cpap_usage_repo.insert_many(
            db_session, "m-duplicate-night", [(date(2026, 1, 1), 6.0)]
        )


async def test_sleep_study_keeps_the_raw_counts_not_only_the_index(db_session):
    """NCD 240.4 states criteria in terms of apnea events over recorded hours as well as
    AHI. Storing only the derived index would make one of them unanswerable -- checked
    here by writing a study and reading the raw counts back, rather than confirming the
    columns are merely declared."""
    await members_repo.insert(
        db_session,
        id="m-raw-counts",
        birth_date=date(1970, 1, 1),
        sex="F",
        coverage_start=date(2020, 1, 1),
        coverage_end=None,
    )
    study = await sleep_studies_repo.insert(
        db_session,
        member_id="m-raw-counts",
        date=date(2026, 1, 15),
        test_type="attended_psg",
        channels=10,
        apnea_events=100,
        recorded_hours=6.0,
        ahi=16.7,
    )

    assert study.apnea_events == 100
    assert study.recorded_hours == 6.0
    assert study.ahi == 16.7
