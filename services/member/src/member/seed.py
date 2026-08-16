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
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from member.generate import (
    STUDY_TARGETS,
    USAGE_TARGETS,
    generate_sleep_profile,
    generate_usage_nights,
)
from member.models import Condition, CpapUsage, Encounter, Member, Note, SleepStudy
from member.notes import generate_note
from member.synthea import SyntheaCondition, SyntheaEncounter, SyntheaPatient

#: Every seeded member gets the same wide-open window: this population exists to be
#: adjudicated against dates the generators below choose, not to exercise coverage
#: gaps -- a member whose coverage window excluded their own sleep study would make
#: every other criterion moot before it was ever reached.
_COVERAGE_START = date(2020, 1, 1)

# The dates below are public because a golden case (plan 06) has to name the same ones
# to build a request this population can answer -- a case that guessed the study date
# would query a window the seeder never wrote to and read an empty record as a refusal.

#: The sleep study and the visit note that documents the symptoms (or their absence)
#: the AHI 5-14 judgment reads -- one visit, so both carry the same date.
STUDY_DATE = date(2026, 1, 15)

#: Matches the window the brief's own verification command queries
#: (`?start=2026-02-01&end=2026-03-02`) -- 30 consecutive nights from Feb 1 lands on
#: Mar 2, not a round-number end date, so picking dates independently here would have
#: silently produced a window nobody could query against by eye.
USAGE_START = date(2026, 2, 1)
USAGE_NIGHTS = 30

#: The continuation visit, deliberately *after* the last night the usage window covers.
#: NCD 240.4 continues coverage on adherence plus documented benefit, and a note dated
#: before the therapy it reports on cannot be evidence of that therapy working -- which
#: is what every note in this population used to be, leaving the criterion representable
#: (notes.BENEFIT_INDICATORS) and unreachable.
FOLLOW_UP_DATE = USAGE_START + timedelta(days=USAGE_NIGHTS + 7)


@dataclass(frozen=True)
class MemberPlan:
    """The case shape one seeded member is built to. Naming a `member_id` alongside
    the targets that produced it is what lets a golden case (plan 06) ask for exactly
    this member instead of re-deriving which id happens to satisfy a criterion."""

    member_id: str
    study_target: str
    usage_target: str
    #: notes.SYMPTOMS names documented at the initial visit; empty means the visit
    #: documented none, which is a fact the 5-14 judgment needs as much as its opposite.
    symptoms: list[str]
    #: notes.BENEFIT_INDICATORS documented at the follow-up visit, for the continuation
    #: criterion. Empty means the follow-up found no improvement to report -- a note
    #: showing none, not a missing note, so the criterion has a real negative case.
    benefits: list[str]


@dataclass(frozen=True)
class SeedResult:
    members: int
    studies: int
    usage_nights: int
    notes: int


#: The case each committed Synthea patient is built to. Chosen so that every criterion
#: NCD 240.4 gates on has a member on both sides of it -- a criterion with only
#: satisfying members is one the eval can never observe being refused on, and one with
#: only failing members can never be observed being met. tests/test_fixture_population.py
#: asserts exactly that over this list and the committed CSVs, so a plan that drops a
#: side fails rather than quietly narrowing what plan 06 can build.
#:
#: p2 is ADR-0009's near-miss and the reason that test exists: its Synthea record carries
#: coronary artery disease, which is itself a qualifying path at AHI 5-14, so "below 15"
#: is not on its own enough to keep it refused -- its AHI must also stay clear of the
#: mild band.
FIXTURE_PLAN = [
    MemberPlan(
        member_id="p1",
        study_target="qualifying",
        usage_target="adherent",
        symptoms=["excessive daytime sleepiness", "insomnia"],
        benefits=["improved daytime alertness", "partner reporting less snoring"],
    ),
    MemberPlan(
        member_id="p2",
        study_target="near_miss_high",
        usage_target="near_miss_adherence",
        symptoms=[],
        benefits=[],
    ),
    MemberPlan(
        member_id="p3",
        study_target="mild_range",
        usage_target="adherent",
        symptoms=["mood disorder"],
        benefits=[],
    ),
    MemberPlan(
        member_id="p4",
        study_target="just_qualifying",
        usage_target="adherent",
        # No symptoms and no qualifying comorbidity in the CSVs: this member approves on
        # AHI alone, just above the line, so nothing else can be carrying the approval.
        symptoms=[],
        benefits=[],
    ),
    MemberPlan(
        member_id="p5",
        study_target="near_miss_channels",
        usage_target="adherent",
        # Comfortably qualifying AHI, one channel short of the Type IV minimum -- the
        # negative case for study validity, failing on that criterion and no other.
        symptoms=[],
        benefits=[],
    ),
]


def _validate_plan(patients: list[SyntheaPatient], plan: list[MemberPlan]) -> None:
    """Raises unless every patient has a case shape and every case shape has a patient.

    A typo'd member id used to be dropped silently and a patient without a plan used to
    get a bare Member row with no study, usage or note. Either way a named case simply
    would not exist, and the seed result would still report success -- which is precisely
    the failure ADR-0009 is written against: the near-miss you believe you have is the
    one you never built.
    """
    patient_ids = {p.id for p in patients}
    planned_ids = {p.member_id for p in plan}

    unknown = sorted(planned_ids - patient_ids)
    if unknown:
        raise ValueError(f"plan names member ids with no Synthea patient: {unknown}")

    unplanned = sorted(patient_ids - planned_ids)
    if unplanned:
        raise ValueError(f"patients have no case shape in the plan: {unplanned}")

    for member_plan in plan:
        if member_plan.study_target not in STUDY_TARGETS:
            raise ValueError(
                f"{member_plan.member_id}: unknown sleep-study target "
                f"{member_plan.study_target!r}"
            )
        if member_plan.usage_target not in USAGE_TARGETS:
            raise ValueError(
                f"{member_plan.member_id}: unknown CPAP-usage target "
                f"{member_plan.usage_target!r}"
            )


async def seed_population(
    session: AsyncSession,
    patients: list[SyntheaPatient],
    conditions: list[SyntheaCondition],
    encounters: list[SyntheaEncounter],
    seed: int,
    plan: list[MemberPlan],
) -> SeedResult:
    # Before any row is written, so a bad plan fails as a bad plan rather than as a
    # population that is quietly missing the case it was supposed to contain.
    _validate_plan(patients, plan)
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
        member_plan = plan_by_id[patient.id]

        profile = generate_sleep_profile(
            patient.id, seed, STUDY_DATE, target=member_plan.study_target
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
            patient.id, seed, USAGE_START, USAGE_NIGHTS, target=member_plan.usage_target
        )
        for night, hours in nights:
            session.add(CpapUsage(member_id=patient.id, night=night, hours=hours))
        usage_nights += len(nights)

        initial_note = generate_note(patient.id, seed, STUDY_DATE, member_plan.symptoms)
        session.add(Note(member_id=patient.id, date=STUDY_DATE, text=initial_note))

        # Every member gets a follow-up note, including those with no benefits to
        # report. An absent note and a note that documents no improvement are different
        # facts, and only the second gives the continuation criterion something to be
        # refused on rather than merely unanswerable.
        follow_up_note = generate_note(
            patient.id, seed, FOLLOW_UP_DATE, symptoms=[], benefits=member_plan.benefits
        )
        session.add(Note(member_id=patient.id, date=FOLLOW_UP_DATE, text=follow_up_note))
        notes += 2

    await session.flush()

    return SeedResult(
        members=len(new_patients),
        studies=studies,
        usage_nights=usage_nights,
        notes=notes,
    )
