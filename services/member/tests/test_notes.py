from datetime import date

import pytest

from member.notes import SYMPTOMS, generate_note

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
