from datetime import date

import pytest

from member.domain.notes import BENEFIT_INDICATORS, SYMPTOMS, generate_note

WHEN = date(2026, 1, 5)


def test_generation_is_deterministic():
    assert generate_note("M1", 4, WHEN, ["insomnia"]) == generate_note("M1", 4, WHEN, ["insomnia"])


def test_a_note_with_no_symptoms_documents_none_of_them():
    """The absence of documentation is the whole point of the insufficient-evidence
    verdict. A note that always mentions something makes that unreachable."""
    note = generate_note("M1", 4, WHEN, [])

    assert not any(s in note.lower() for s in ("sleepiness", "insomnia", "mood", "cognition"))


def test_a_documented_symptom_is_described_not_labelled():
    """Judgment criteria must require interpretation. A note containing the criterion's
    own wording is pattern matching, not comprehension, and would flatter the eval."""
    note = generate_note("M1", 4, WHEN, ["excessive daytime sleepiness"])

    assert "excessive daytime sleepiness" not in note.lower()
    assert len(note) > 40


def test_an_unknown_symptom_raises():
    with pytest.raises(ValueError, match="symptom"):
        generate_note("M1", 4, WHEN, ["hiccups"])


def test_every_known_symptom_produces_prose():
    for symptom in SYMPTOMS:
        note = generate_note("M1", 4, WHEN, [symptom])
        assert len(note) > 40


def test_a_note_with_no_benefits_documents_none_of_them():
    """The continuation criterion needs a negative case as much as the initial
    qualification does: a note showing no evidence of benefit."""
    note = generate_note("M1", 4, WHEN, [], benefits=None).lower()

    assert "benefiting" not in note
    assert "adherent" not in note
    assert not any(indicator in note for indicator in BENEFIT_INDICATORS)


def test_an_unknown_benefit_indicator_raises():
    with pytest.raises(ValueError, match="benefit"):
        generate_note("M1", 4, WHEN, [], benefits=["telepathy"])


def test_every_known_benefit_indicator_produces_prose():
    for indicator in BENEFIT_INDICATORS:
        note = generate_note("M1", 4, WHEN, [], benefits=[indicator])
        assert len(note) > 40


def test_no_note_labels_the_symptom_or_benefit_it_describes():
    """This is the guard on the single property this module exists for: a note
    must describe, never label. The brief's own test only exercises one symptom
    ("excessive daytime sleepiness"), so a regression in any of the other three
    phrasing banks -- or in any benefit indicator -- could reintroduce a label and
    leave the rest of the suite green. This test iterates every symptom and every
    benefit indicator across many seeds instead.

    The forbidden substrings are derived from SYMPTOMS/BENEFIT_INDICATORS (each
    name's last, most distinctive word -- "sleepiness", "cognition", "disorder",
    "insomnia", "alertness", "apnoeas", "snoring", "concentration") rather than
    hardcoded separately, so a newly added symptom or indicator extends the guard
    automatically instead of silently falling outside it.
    """
    canonical_names = (*SYMPTOMS, *BENEFIT_INDICATORS)
    distinctive_words = [name.split()[-1] for name in canonical_names]

    for symptom in SYMPTOMS:
        for seed in range(50):
            note = generate_note("M1", seed, WHEN, [symptom]).lower()
            assert symptom not in note
            assert not any(word in note for word in distinctive_words)

    for indicator in BENEFIT_INDICATORS:
        for seed in range(50):
            note = generate_note("M1", seed, WHEN, [], benefits=[indicator]).lower()
            assert indicator not in note
            assert not any(word in note for word in distinctive_words)
            assert "benefiting" not in note
            assert "adherent" not in note
