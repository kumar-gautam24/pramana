"""Invariants over the population this service actually ships: `seed.FIXTURE_PLAN`
composed with `tests/fixtures/synthea/*.csv`.

Every other seed test builds its own two-member plan, so until this file existed nothing
tested the artifact `POST /seed` produces. That matters because every Critical found in
this project has been a property of *composed* data rather than of any one module: a
generator band and a Synthea comorbidity, each correct alone and wrong together. No
module can see it. `generate.py` knows nothing about conditions, `synthea.py` knows
nothing about targets, and `seed.py` composes the two without looking at either. The
invariant therefore has to live above all three, which is here.

The bug this file was written for: `near_miss_high` used to be able to draw an AHI of
exactly 14.0, the mild band's ceiling. Member p2 is the population's designated
near-miss *and* carries coronary artery disease, which is one of NCD 240.4's alternate
qualifying paths at AHI 5-14. So on those draws the one member the population exists to
have refused became a legitimate approval, and the band test passed because it asserted
`14.0 <= ahi`, encoding the overlap instead of catching it.

This module models NCD 240.4's qualifying paths, which no route or query in this service
may do (ADR-0003) -- it is test apparatus, and the asymmetry is deliberate: knowing what
the policy would conclude is how a test can assert that a case the population calls a
refusal is not quietly an approval.
"""

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from member.generate import (
    TARGETS,
    TEST_TYPES,
    SleepProfile,
    generate_sleep_profile,
    generate_usage_nights,
)
from member.seed import (
    FIXTURE_PLAN,
    STUDY_DATE,
    USAGE_NIGHTS,
    USAGE_START,
    MemberPlan,
)
from member.synthea import parse_conditions, parse_patients

_FIXTURES = Path(__file__).parent / "fixtures" / "synthea"


def _rows(name: str) -> list[dict]:
    with (_FIXTURES / name).open() as handle:
        return list(csv.DictReader(handle))


PATIENTS = parse_patients(_rows("patients.csv"))
CONDITIONS = parse_conditions(_rows("conditions.csv"))

# ---------------------------------------------------------------------------
# NCD 240.4, as this test reads it. Nothing in src/ may hold any of this.
# ---------------------------------------------------------------------------

#: The comorbidities that qualify a member whose AHI sits in the 5-14 band: hypertension,
#: ischemic heart disease, history of stroke.
QUALIFYING_COMORBIDITIES = frozenset({"59621000", "53741008", "230690007"})

AHI_THRESHOLD = 15.0
#: Inclusive. An AHI above this and below the threshold qualifies under neither path --
#: which is the only reason a near-miss can be a near-miss at all.
MILD_BAND = (5.0, 14.0)
ADHERENCE_FRACTION = 0.70
MIN_NIGHTLY_HOURS = 4.0
TYPE_IV_MIN_CHANNELS = 3

#: Study targets built to be refused. `mild_range` is deliberately absent: 5-14 *is* a
#: qualifying path once a symptom or comorbidity is documented, so approving a
#: mild_range member is correct rather than a leak.
REFUSAL_TARGETS = ("near_miss_high", "below_threshold", "near_miss_channels", "invalid_test_type")

#: Wide enough that a rare draw cannot hide. The 14.0 collision was reachable on roughly
#: one profile in 400, so a handful of seeds would have passed by luck -- and did, for
#: eight commits. Against the pre-fix bands the sweeps below find it.
SEEDS = range(200)
PROBE_MEMBERS = [f"probe-{i}" for i in range(20)]


def _study_is_valid(profile: SleepProfile) -> bool:
    """NCD 240.4.1's study-validity criterion: an accepted test type, and at least three
    channels if it is Type IV."""
    if profile.test_type not in TEST_TYPES:
        return False
    return profile.test_type != "home_type_iv" or profile.channels >= TYPE_IV_MIN_CHANNELS


def _qualifies_for_initiation(
    profile: SleepProfile, symptoms: list[str], codes: frozenset[str]
) -> bool:
    """Whether NCD 240.4 would cover initial CPAP for this composed record -- across
    *both* qualifying paths, which is the point. Checking only the path a target was
    named for is what let a refusal walk in through the other one."""
    if not _study_is_valid(profile):
        return False
    if profile.ahi >= AHI_THRESHOLD:
        return True
    low, high = MILD_BAND
    if not low <= profile.ahi <= high:
        return False
    return bool(symptoms) or bool(codes & QUALIFYING_COMORBIDITIES)


def _codes_for(member_id: str) -> frozenset[str]:
    return frozenset(c.code for c in CONDITIONS if c.patient_id == member_id)


def _adherent_fraction(plan: MemberPlan, seed: int) -> float:
    nights = generate_usage_nights(
        plan.member_id, seed, USAGE_START, USAGE_NIGHTS, target=plan.usage_target
    )
    return sum(1 for _, hours in nights if hours >= MIN_NIGHTLY_HOURS) / len(nights)


def test_every_planned_member_exists_and_names_a_known_target():
    """The composition `POST /seed` performs, checked before it can silently drop a case:
    a plan id with no patient, or a patient with no plan, means a named case does not
    exist -- and the seed result would report success either way."""
    patient_ids = {p.id for p in PATIENTS}

    for plan in FIXTURE_PLAN:
        assert plan.member_id in patient_ids, plan.member_id
        assert plan.study_target in TARGETS, plan.study_target
        assert plan.usage_target in TARGETS, plan.usage_target

    assert patient_ids == {p.member_id for p in FIXTURE_PLAN}


def test_the_near_miss_band_cannot_touch_the_mild_band():
    """The direct statement of the bug: at exactly 14.0 a `near_miss_high` profile is
    simultaneously below the AHI threshold and inside the 5-14 band, so a documented
    symptom or comorbidity approves it. The bands must not share an endpoint."""
    ceiling = MILD_BAND[1]

    for seed in SEEDS:
        for member in PROBE_MEMBERS:
            profile = generate_sleep_profile(
                member, seed, STUDY_DATE, target="near_miss_high"
            )
            assert ceiling < profile.ahi < AHI_THRESHOLD, (
                f"{member} at seed {seed} drew ahi={profile.ahi}, inside the mild band"
            )


@pytest.mark.parametrize("target", REFUSAL_TARGETS)
def test_a_refusal_target_is_unapprovable_under_every_comorbidity_combination(target):
    """The generalised invariant: a target built to be refused must stay refused against
    any record it could be composed with, not merely against the criterion it was named
    for. Crossing the target with the symptom and comorbidity combinations a Synthea
    patient can carry is what makes that a property rather than a hope."""
    for seed in SEEDS:
        for member in PROBE_MEMBERS:
            profile = generate_sleep_profile(member, seed, STUDY_DATE, target=target)
            for symptoms in ([], ["insomnia"]):
                for codes in (frozenset(), QUALIFYING_COMORBIDITIES):
                    assert not _qualifies_for_initiation(profile, symptoms, codes), (
                        f"{target} member {member} at seed {seed} qualifies: "
                        f"ahi={profile.ahi}, type={profile.test_type}, "
                        f"channels={profile.channels}, symptoms={symptoms}, "
                        f"codes={sorted(codes)}"
                    )


def test_no_shipped_near_miss_is_approvable_by_its_own_record():
    """The same invariant against the real composition rather than a probe: each refusal
    member's own plan symptoms and its own rows in conditions.csv. p2 is why this is not
    redundant -- its Synthea record carries coronary artery disease, so 'AHI below 15' is
    not on its own enough to keep it refused."""
    for plan in FIXTURE_PLAN:
        if plan.study_target not in REFUSAL_TARGETS:
            continue
        codes = _codes_for(plan.member_id)
        for seed in SEEDS:
            profile = generate_sleep_profile(
                plan.member_id, seed, STUDY_DATE, target=plan.study_target
            )
            assert not _qualifies_for_initiation(profile, plan.symptoms, codes), (
                f"{plan.member_id} ({plan.study_target}) is approvable at seed {seed}: "
                f"ahi={profile.ahi}, symptoms={plan.symptoms}, codes={sorted(codes)}"
            )


#: Every criterion NCD 240.4 gates on that this service is the system of record for,
#: as a predicate over one seeded member. Kept as data so the coverage assertion below
#: names the criterion that lost a side rather than reporting a bare False.
CRITERIA = {
    "ahi at or above 15": lambda plan, profile, fraction, codes: profile.ahi >= AHI_THRESHOLD,
    "ahi within the 5-14 band": (
        lambda plan, profile, fraction, codes: MILD_BAND[0] <= profile.ahi <= MILD_BAND[1]
    ),
    "study type and channel count accepted": (
        lambda plan, profile, fraction, codes: _study_is_valid(profile)
    ),
    "symptoms documented": lambda plan, profile, fraction, codes: bool(plan.symptoms),
    "qualifying comorbidity documented": (
        lambda plan, profile, fraction, codes: bool(codes & QUALIFYING_COMORBIDITIES)
    ),
    "adherent on 70% of nights": (
        lambda plan, profile, fraction, codes: fraction >= ADHERENCE_FRACTION
    ),
    "benefit documented": lambda plan, profile, fraction, codes: bool(plan.benefits),
}


def test_every_criterion_has_a_qualifying_and_a_non_qualifying_member():
    """A criterion with only satisfying members can never be observed being refused on,
    and one with only failing members can never be observed being met -- either way the
    eval reports a number it did not measure. `benefit documented` had no satisfying
    member at all until the follow-up note existed, and `ahi at or above 15` had no
    member anywhere near the line until `just_qualifying` did."""
    for seed in SEEDS:
        sides = defaultdict(lambda: {True: [], False: []})

        for plan in FIXTURE_PLAN:
            profile = generate_sleep_profile(
                plan.member_id, seed, STUDY_DATE, target=plan.study_target
            )
            fraction = _adherent_fraction(plan, seed)
            codes = _codes_for(plan.member_id)
            for name, holds in CRITERIA.items():
                sides[name][bool(holds(plan, profile, fraction, codes))].append(plan.member_id)

        for name in CRITERIA:
            assert sides[name][True], f"no seeded member satisfies {name!r} at seed {seed}"
            assert sides[name][False], f"no seeded member fails {name!r} at seed {seed}"
