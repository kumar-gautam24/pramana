"""Assembles Synthea patients, the generated sleep/CPAP data, and clinical notes into
the seeded population plan 04's adjudication pipeline and plan 06's golden set run
against.

Idempotent by member id, matching plan 02's ingest rule (`policy.ingest.ingest_ncd`):
seeding runs after failures and on a schedule, so re-seeding a member already present
must be a no-op rather than a way to double the population. `SeedResult` reports rows
added *by this call*, the same delta convention `IngestResult` uses, so a no-op run
reports zeros rather than restating the population's total size.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from member.generate import generate_sleep_profile, generate_usage_nights
from member.models import Condition, CpapUsage, Encounter, Member, Note, SleepStudy
from member.notes import generate_note
from member.synthea import SyntheaCondition, SyntheaEncounter, SyntheaPatient

#: Every seeded member gets the same wide-open window: this population exists to be
#: adjudicated against dates the generators below choose, not to exercise coverage
#: gaps -- a member whose coverage window excluded their own sleep study would make
#: every other criterion moot before it was ever reached.
_COVERAGE_START = date(2020, 1, 1)

#: One sleep study and one note per plan member, dated together: the note is the
#: same visit's documentation of the symptoms (or absence of them) that AHI 5-14
#: judgment reads.
_STUDY_DATE = date(2026, 1, 15)

#: Matches the window the brief's own verification command queries
#: (`?start=2026-02-01&end=2026-03-02`) -- 30 consecutive nights from Feb 1 lands on
#: Mar 2, not a round-number end date, so picking dates independently here would have
#: silently produced a window nobody could query against by eye.
_USAGE_START = date(2026, 2, 1)
_USAGE_NIGHTS = 30


@dataclass(frozen=True)
class MemberPlan:
    """The case shape one seeded member is built to. Naming a `member_id` alongside
    the targets that produced it is what lets a golden case (plan 06) ask for exactly
    this member instead of re-deriving which id happens to satisfy a criterion."""

    member_id: str
    study_target: str
    usage_target: str
    symptoms: list[str]


@dataclass(frozen=True)
class SeedResult:
    members: int
    studies: int
    usage_nights: int
    notes: int


#: The committed fixture has three Synthea patients (p1, p2, p3); this is the case
#: each is built to. p2 is ADR-0009's near-miss: AHI genuinely below 15, the member
#: this population exists to have refused rather than approved, with no documented
#: symptoms so the "note documents no symptom" shape exists too. p1 and p3 cover the
#: qualifying and 5-14-judgment-call shapes, and between them exercise both CPAP
#: usage targets.
FIXTURE_PLAN = [
    MemberPlan(
        member_id="p1",
        study_target="qualifying",
        usage_target="adherent",
        symptoms=["excessive daytime sleepiness", "insomnia"],
    ),
    MemberPlan(
        member_id="p2",
        study_target="near_miss_high",
        usage_target="near_miss_adherence",
        symptoms=[],
    ),
    MemberPlan(
        member_id="p3",
        study_target="mild_range",
        usage_target="adherent",
        symptoms=["mood disorder"],
    ),
]


async def seed_population(
    session: AsyncSession,
    patients: list[SyntheaPatient],
    conditions: list[SyntheaCondition],
    encounters: list[SyntheaEncounter],
    seed: int,
    plan: list[MemberPlan],
) -> SeedResult:
    plan_by_id = {p.member_id: p for p in plan}

    existing_ids = set(
        (
            await session.execute(
                select(Member.id).where(Member.id.in_(p.id for p in patients))
            )
        )
        .scalars()
        .all()
    )
    new_patients = [p for p in patients if p.id not in existing_ids]
    new_ids = {p.id for p in new_patients}

    # Every Member row is added and flushed here, before any Condition/Encounter/
    # SleepStudy/CpapUsage/Note is even constructed, let alone added. These models
    # declare raw FK columns with no relationship(), so the unit of work has no
    # inter-mapper dependency to sort a mixed flush by -- adding a child alongside its
    # parent in the same flush() intermittently inserts the child first and violates
    # the FK. Flushing all parents first, in their own flush() call, is the only fix
    # that doesn't require a schema change (see tests/test_queries.py's
    # `_insert_member` helper, which establishes the same pattern at unit scale).
    for patient in new_patients:
        session.add(
            Member(
                id=patient.id,
                birth_date=patient.birth_date,
                sex=patient.sex,
                coverage_start=_COVERAGE_START,
                coverage_end=None,
            )
        )
    await session.flush()

    for condition in conditions:
        if condition.patient_id in new_ids:
            session.add(
                Condition(
                    member_id=condition.patient_id,
                    code=condition.code,
                    description=condition.description,
                    onset_date=condition.onset_date,
                )
            )

    for encounter in encounters:
        if encounter.patient_id in new_ids:
            session.add(
                Encounter(
                    member_id=encounter.patient_id,
                    date=encounter.date,
                    description=encounter.description,
                )
            )

    studies = usage_nights = notes = 0

    for patient in new_patients:
        member_plan = plan_by_id.get(patient.id)
        if member_plan is None:
            # A patient with no named case still gets a Member row (above) and a
            # coverage window, but nothing to adjudicate -- there is no target to
            # generate a study, usage, or note from.
            continue

        profile = generate_sleep_profile(
            patient.id, seed, _STUDY_DATE, target=member_plan.study_target
        )
        session.add(
            SleepStudy(
                member_id=patient.id,
                date=profile.study_date,
                test_type=profile.test_type,
                channels=profile.channels,
                apnea_events=profile.apnea_events,
                recorded_hours=profile.recorded_hours,
                ahi=profile.ahi,
            )
        )
        studies += 1

        nights = generate_usage_nights(
            patient.id, seed, _USAGE_START, _USAGE_NIGHTS, target=member_plan.usage_target
        )
        for night, hours in nights:
            session.add(CpapUsage(member_id=patient.id, night=night, hours=hours))
        usage_nights += len(nights)

        note_text = generate_note(patient.id, seed, _STUDY_DATE, member_plan.symptoms)
        session.add(Note(member_id=patient.id, date=_STUDY_DATE, text=note_text))
        notes += 1

    await session.flush()

    return SeedResult(
        members=len(new_patients),
        studies=studies,
        usage_nights=usage_nights,
        notes=notes,
    )
