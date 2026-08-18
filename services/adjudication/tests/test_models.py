"""Schema guarantees for migrations/0001_cases_and_determinations.sql, verified by
executing them against a real connection rather than by reading the DDL -- see the
brief's note on the append-only trigger and the cascade needing proof by execution.

This task adds no repositories, so these tests speak SQL directly against `db_session`
(a rolled-back asyncpg connection, see conftest.py) rather than through a query layer
that does not exist yet."""

from datetime import date

import asyncpg
import pytest


async def _insert_case(db_session, **overrides) -> str:
    values = dict(
        member_id="m-1",
        requested_code="95810",
        icd10="G47.33",
        date_of_service=date(2026, 1, 1),
        kind="initial",
    )
    values.update(overrides)
    row = await db_session.fetchrow(
        """
        INSERT INTO cases (member_id, requested_code, icd10, date_of_service, kind)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        values["member_id"],
        values["requested_code"],
        values["icd10"],
        values["date_of_service"],
        values["kind"],
    )
    return row["id"]


async def _insert_criterion(
    db_session, case_id: str, set_ordinal: int = 0, ordinal: int = 0
) -> int:
    row = await db_session.fetchrow(
        """
        INSERT INTO criteria
            (case_id, set_ordinal, ordinal, text, type, params, source_chunk_id, source_display_id)
        VALUES ($1, $2, $3, 'AHI >= 15', 'threshold', '{}'::jsonb, 1, '240.4')
        RETURNING id
        """,
        case_id,
        set_ordinal,
        ordinal,
    )
    return row["id"]


async def test_append_only_trigger_rejects_update(db_session):
    """case_events is the audit trail -- a commissioner's audit rests on no row ever
    being mutated in place. Enforced by a database trigger, not application discipline,
    so it must survive even a caller with a raw connection."""
    case_id = await _insert_case(db_session)
    event = await db_session.fetchrow(
        "INSERT INTO case_events (case_id, seq, type, payload) "
        "VALUES ($1, 1, 'case_created', '{}'::jsonb) RETURNING id",
        case_id,
    )

    with pytest.raises(asyncpg.RaiseError):
        async with db_session.transaction():
            await db_session.execute(
                "UPDATE case_events SET type = 'tampered' WHERE id = $1", event["id"]
            )


async def test_append_only_trigger_rejects_delete(db_session):
    case_id = await _insert_case(db_session)
    event = await db_session.fetchrow(
        "INSERT INTO case_events (case_id, seq, type, payload) "
        "VALUES ($1, 1, 'case_created', '{}'::jsonb) RETURNING id",
        case_id,
    )

    with pytest.raises(asyncpg.RaiseError):
        async with db_session.transaction():
            await db_session.execute("DELETE FROM case_events WHERE id = $1", event["id"])


async def test_append_only_trigger_rejects_truncate(db_session):
    """TRUNCATE bypasses row-level triggers entirely, so this needs its own statement-
    level BEFORE TRUNCATE trigger -- a test that only covered UPDATE/DELETE would pass
    over an audit log anyone could still empty in one statement."""
    case_id = await _insert_case(db_session)
    await db_session.execute(
        "INSERT INTO case_events (case_id, seq, type, payload) "
        "VALUES ($1, 1, 'case_created', '{}'::jsonb)",
        case_id,
    )

    with pytest.raises(asyncpg.RaiseError):
        async with db_session.transaction():
            await db_session.execute("TRUNCATE case_events")


async def test_case_events_seq_is_unique_per_case(db_session):
    """seq is assigned by the writer, per case, not from a global sequence -- that is
    what makes this constraint meaningful: a gap or duplicate is a violation, not a
    silently accepted reorder."""
    case_id = await _insert_case(db_session)
    await db_session.execute(
        "INSERT INTO case_events (case_id, seq, type, payload) "
        "VALUES ($1, 1, 'case_created', '{}'::jsonb)",
        case_id,
    )

    with pytest.raises(asyncpg.UniqueViolationError):
        await db_session.execute(
            "INSERT INTO case_events (case_id, seq, type, payload) "
            "VALUES ($1, 1, 'duplicate_seq', '{}'::jsonb)",
            case_id,
        )


async def test_a_determination_cannot_be_a_denial(db_session):
    """ADR-0002 is the project's central claim, and this is the line that makes it
    structural: the database itself has no room for a denial. Every other guarantee in
    this file protects an audit trail; this one protects a member.

    Worth stating why it is a test and not just a CHECK constraint. The constraint is
    the enforcement, but a constraint nobody asserts on is one a later migration widens
    without anything going red -- and `outcome` is exactly the column a well-meaning
    change would widen, because a clinician's review really can be a denial. It just
    does not live here."""
    case_id = await _insert_case(db_session)

    with pytest.raises(asyncpg.CheckViolationError):
        await db_session.execute(
            """
            INSERT INTO determinations (case_id, outcome, blocking, thresholds)
            VALUES ($1, 'deny', '[]'::jsonb, '{}'::jsonb)
            """,
            case_id,
        )


async def test_a_determination_reason_is_a_closed_set(db_session):
    """GateReason exists so that only four sentences may be put in front of the
    clinician who picks the case up (see packages/common). Free text here would let a
    caller write "denied by policy" into a field a reviewer reads -- a denial in
    everything but the outcome column."""
    case_id = await _insert_case(db_session)

    with pytest.raises(asyncpg.CheckViolationError):
        await db_session.execute(
            """
            INSERT INTO determinations (case_id, outcome, reason, blocking, thresholds)
            VALUES ($1, 'escalate', 'denied by policy', '[]'::jsonb, '{}'::jsonb)
            """,
            case_id,
        )


async def test_determination_winning_set_accepts_null(db_session):
    """NULL means escalation -- there is no winning set to name."""
    case_id = await _insert_case(db_session)

    row = await db_session.fetchrow(
        """
        INSERT INTO determinations (case_id, outcome, reason, blocking, thresholds, winning_set)
        VALUES ($1, 'escalate', 'criterion_not_met', '[]'::jsonb, '{}'::jsonb, NULL)
        RETURNING winning_set
        """,
        case_id,
    )

    assert row["winning_set"] is None


async def test_deleting_a_case_cascades_its_child_tables(db_session):
    """A case with no case_events rows can be deleted outright, and the delete must
    reach every other child table -- criteria, criterion_results, determinations and
    reviews all empty out. Kept as its own case (no case_events) because the refusal
    tested below blocks the cascade entirely, per the brief."""
    case_id = await _insert_case(db_session)
    criterion_id = await _insert_criterion(db_session, case_id)
    await db_session.execute(
        """
        INSERT INTO criterion_results (criterion_id, verdict, confidence, tool, evidence)
        VALUES ($1, 'met', 0.9, 'ahi_threshold', '{}'::jsonb)
        """,
        criterion_id,
    )
    await db_session.execute(
        """
        INSERT INTO determinations (case_id, outcome, reason, blocking, thresholds, winning_set)
        VALUES ($1, 'approve', NULL, '[]'::jsonb, '{}'::jsonb, 0)
        """,
        case_id,
    )
    await db_session.execute(
        """
        INSERT INTO reviews (case_id, clinician_id, outcome, rationale, agreed_with_system)
        VALUES ($1, 'clin-1', 'approve', 'looks right', true)
        """,
        case_id,
    )

    await db_session.execute("DELETE FROM cases WHERE id = $1", case_id)

    remaining_criteria = await db_session.fetchval(
        "SELECT count(*) FROM criteria WHERE case_id = $1", case_id
    )
    remaining_results = await db_session.fetchval(
        "SELECT count(*) FROM criterion_results WHERE criterion_id = $1", criterion_id
    )
    remaining_determinations = await db_session.fetchval(
        "SELECT count(*) FROM determinations WHERE case_id = $1", case_id
    )
    remaining_reviews = await db_session.fetchval(
        "SELECT count(*) FROM reviews WHERE case_id = $1", case_id
    )

    assert remaining_criteria == 0
    assert remaining_results == 0
    assert remaining_determinations == 0
    assert remaining_reviews == 0


async def test_deleting_a_case_with_events_is_refused(db_session):
    """case_events is ON DELETE RESTRICT, not CASCADE like the other four child
    tables -- a case whose audit trail exists cannot be deleted. Exercised as a
    separate case from the cascade test above because the restriction blocks the
    cascade before it reaches any other table."""
    case_id = await _insert_case(db_session)
    await db_session.execute(
        "INSERT INTO case_events (case_id, seq, type, payload) "
        "VALUES ($1, 1, 'case_created', '{}'::jsonb)",
        case_id,
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_session.execute("DELETE FROM cases WHERE id = $1", case_id)
