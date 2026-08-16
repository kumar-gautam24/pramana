from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

import member.notes as notes_module
from member.models import CpapUsage, Member, Note, SleepStudy
from member.notes import BENEFIT_INDICATORS
from member.seed import (
    FOLLOW_UP_DATE,
    STUDY_DATE,
    USAGE_NIGHTS,
    USAGE_START,
    MemberPlan,
    seed_population,
)
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
        benefits=["improved daytime alertness"],
    ),
    MemberPlan(
        member_id="s2",
        study_target="near_miss_high",
        usage_target="near_miss_adherence",
        symptoms=[],
        benefits=[],
    ),
]


def _benefit_sentences_in(text: str) -> list[str]:
    """Which of notes.py's benefit narratives the text actually carries.

    Reaches into the phrasing bank rather than matching the indicator names, because the
    names are exactly what a well-formed note never contains -- searching for them would
    report "no benefit documented" for every note, benefit-carrying or not.
    """
    return [
        sentence
        for phrasings in notes_module._BENEFIT_PHRASINGS.values()
        for sentence in phrasings
        if sentence in text
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
    # Two notes per member: the initial visit and the continuation follow-up.
    assert first.notes == 4
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

    result = await db_session.execute(
        select(Note).where(Note.member_id == "s2", Note.date == STUDY_DATE)
    )
    note = result.scalar_one()

    assert not any(s in note.text.lower() for s in ("sleepiness", "insomnia", "mood", "cognition"))


async def test_documented_benefit_is_seeded_after_the_usage_window_closes(db_session):
    """NCD 240.4 continues coverage on adherence *plus* documented benefit. A note dated
    before the nights it reports on cannot evidence that therapy working, so every note
    landing on the study date left the criterion representable and unreachable: plan 04
    could query /notes and find nothing to judge."""
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    last_night = USAGE_START + timedelta(days=USAGE_NIGHTS - 1)
    result = await db_session.execute(
        select(Note).where(Note.member_id == "s1", Note.date == FOLLOW_UP_DATE)
    )
    note = result.scalar_one()

    assert note.date > last_night
    assert _benefit_sentences_in(note.text)
    # Described, never labelled -- same rule as the symptom phrasings (see notes.py),
    # so the judgment stays a judgment rather than a substring match.
    assert not any(indicator in note.text.lower() for indicator in BENEFIT_INDICATORS)


async def test_a_member_with_no_benefits_gets_a_follow_up_documenting_none(db_session):
    """The negative case the continuation criterion needs: a follow-up visit that
    happened and found nothing to report, not a missing note. An absent note is
    unanswerable; this one is answerable and answers no."""
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    result = await db_session.execute(
        select(Note).where(Note.member_id == "s2", Note.date == FOLLOW_UP_DATE)
    )
    note = result.scalar_one()

    assert not _benefit_sentences_in(note.text)


async def test_a_plan_naming_an_unknown_member_raises(db_session):
    """A typo'd id used to drop the case silently, and the seed result still reported
    success -- so the near-miss you believed you had was the one you never built."""
    plan = [*PLAN, MemberPlan("s-typo", "qualifying", "adherent", symptoms=[], benefits=[])]

    with pytest.raises(ValueError, match="s-typo"):
        await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, plan)


async def test_a_patient_with_no_case_shape_raises(db_session):
    """The mirror of the above: a patient the plan forgot used to get a bare Member row
    with no study, usage or note -- a member nothing can be adjudicated about."""
    patients = [*PATIENTS, SyntheaPatient(id="s3", birth_date=date(1980, 3, 3), sex="M")]

    with pytest.raises(ValueError, match="s3"):
        await seed_population(db_session, patients, CONDITIONS, ENCOUNTERS, 42, PLAN)


async def test_a_plan_naming_an_unknown_target_raises(db_session):
    """`near_miss` is not a target. Nothing validated this, so the case would fail deep
    inside the generator -- or, for a usage target handed to the study generator, name a
    shape the case was never built to."""
    plan = [MemberPlan("s1", "near_miss", "adherent", symptoms=[], benefits=[]), PLAN[1]]

    with pytest.raises(ValueError, match="near_miss"):
        await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, plan)


async def test_every_seeded_member_has_a_coverage_window(db_session):
    await seed_population(db_session, PATIENTS, CONDITIONS, ENCOUNTERS, 42, PLAN)
    await db_session.flush()

    result = await db_session.execute(select(Member))
    members = result.scalars().all()

    assert len(members) == 2
    for member in members:
        assert member.coverage_start is not None
