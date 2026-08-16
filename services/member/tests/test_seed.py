from datetime import date

from sqlalchemy import func, select

from member.models import CpapUsage, Member, Note, SleepStudy
from member.seed import MemberPlan, seed_population
from member.synthea import SyntheaCondition, SyntheaEncounter, SyntheaPatient

PATIENTS = [
    SyntheaPatient(id="s1", birth_date=date(1970, 1, 1), sex="F"),
    SyntheaPatient(id="s2", birth_date=date(1965, 6, 15), sex="M"),
]
CONDITIONS = [
    SyntheaCondition(
        patient_id="s1", code="E66.9", description="Obesity", onset_date=date(2020, 1, 1)
    )
]
ENCOUNTERS = [
    SyntheaEncounter(patient_id="s1", date=date(2020, 1, 1), description="Checkup")
]
PLAN = [
    MemberPlan(
        member_id="s1",
        study_target="qualifying",
        usage_target="adherent",
        symptoms=["excessive daytime sleepiness"],
    ),
    MemberPlan(
        member_id="s2",
        study_target="near_miss_high",
        usage_target="near_miss_adherence",
        symptoms=[],
    ),
]


async def _table_count(session, model) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


async def test_seeding_twice_adds_nothing_the_second_time(db_session):
    """Idempotent by member id: seeding runs after failures and on a schedule, so a
    second run must be a no-op, not a way to double the population."""
    first = await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    assert first.members == 2
    assert first.studies == 2
    assert first.notes == 2
    members_after_first = await _table_count(db_session, Member)
    studies_after_first = await _table_count(db_session, SleepStudy)
    usage_after_first = await _table_count(db_session, CpapUsage)
    notes_after_first = await _table_count(db_session, Note)

    second = await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    assert second == type(second)(members=0, studies=0, usage_nights=0, notes=0)
    assert await _table_count(db_session, Member) == members_after_first
    assert await _table_count(db_session, SleepStudy) == studies_after_first
    assert await _table_count(db_session, CpapUsage) == usage_after_first
    assert await _table_count(db_session, Note) == notes_after_first


async def test_near_miss_high_member_genuinely_has_an_ahi_below_fifteen(db_session):
    """That member exists to be refused. A seeder that quietly produced a qualifying
    AHI would make the whole refusal half of the eval meaningless -- so this reads
    the row back from the database rather than trusting the generator."""
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    result = await db_session.execute(select(SleepStudy).where(SleepStudy.member_id == "s2"))
    study = result.scalar_one()

    assert study.ahi < 15.0


async def test_a_member_with_no_symptoms_gets_a_note_documenting_none(db_session):
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    result = await db_session.execute(select(Note).where(Note.member_id == "s2"))
    note = result.scalar_one()

    assert not any(s in note.text.lower() for s in ("sleepiness", "insomnia", "mood", "cognition"))


async def test_every_seeded_member_has_a_coverage_window(db_session):
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    result = await db_session.execute(select(Member))
    members = result.scalars().all()

    assert len(members) == 2
    for member in members:
        assert member.coverage_start is not None
