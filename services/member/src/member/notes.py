"""Generates the clinical notes NCD 240.4's judgment criteria read.

The 5-14 AHI band only qualifies with documented symptoms, and whether a note
documents one is the one call in this whole eval a model has to make rather than a
query. That only holds if a note *describes* a symptom -- the way a clinician
actually writes -- rather than naming the criterion itself. A note containing the
criterion's own wording ("excessive daytime sleepiness") turns the judgment call
into a substring check, which would flatter every number this project publishes.

Pure by construction, same reasoning as generate.py: seeded only from arguments
callers already have, so a golden case reproduces from its own row.
"""

import random
from datetime import date

#: The four NCD 240.4 symptoms that qualify a 5-14 AHI member. Exposed so a caller
#: (or a test) can enumerate the contract without hardcoding a second copy of it.
SYMPTOMS = (
    "excessive daytime sleepiness",
    "impaired cognition",
    "mood disorder",
    "insomnia",
)

#: Two or three clinician-voiced descriptions per symptom, none of which contain the
#: symptom's own name -- see the module docstring for why that's load-bearing. Held
#: as a tuple per symptom so a population varies in phrasing without the variation
#: itself being unpredictable (selection is seeded, see _rng).
_PHRASINGS: dict[str, tuple[str, ...]] = {
    "excessive daytime sleepiness": (
        "Reports falling asleep watching television most evenings, and once while "
        "stopped in traffic.",
        "Family notes patient nodding off during conversations and after meals "
        "despite adequate time in bed.",
        "Describes an overwhelming urge to nap most afternoons, occasionally dozing "
        "at the desk.",
    ),
    "insomnia": (
        "Describes lying awake for hours most nights before finally drifting off, "
        "then waking repeatedly until morning.",
        "Reports going to bed but rarely sleeping through the night, up and "
        "checking the clock several times before dawn.",
        "Notes needing over an hour to fall asleep most nights and waking three or "
        "four times before sunrise.",
    ),
    "impaired cognition": (
        "Spouse reports patient losing track mid-sentence and rereading the same "
        "paragraph several times before it registers.",
        "Describes trouble concentrating at work, misplacing items and forgetting "
        "recent conversations more than before.",
        "Notes difficulty following multi-step instructions and needing to double "
        "back partway through routine tasks.",
    ),
    "mood disorder": (
        "Reports feeling increasingly irritable with family since the sleep "
        "problems began, with little patience for minor frustrations.",
        "Describes feeling flat and unmotivated most days, a change the partner "
        "attributes to the poor sleep.",
        "Notes a short temper and bouts of tearfulness that started around the "
        "same time as the sleep complaints.",
    ),
}

#: A visit still needs an opening sentence when no symptoms are documented at all --
#: otherwise the empty-symptom case would be distinguishable from a real note by
#: length alone, which is its own kind of leak.
_OPENER = "Follow-up visit for evaluation of sleep complaints."

#: Kept generic on purpose: this sentence exists to vary a note around a benefit
#: discussion, not to assert anything a criterion could key on.
_BENEFIT_PHRASINGS = (
    "Discussed options related to {benefit} and answered questions about next steps.",
    "Reviewed {benefit} with the patient and confirmed understanding of the plan.",
)


def _rng(member_id: str, seed: int, note_date: date, concern: str) -> random.Random:
    # Same rationale as generate.py's _rng: one stream per concern (per symptom, plus
    # one for the benefit sentence) so a change to one symptom's phrasing bank can
    # never shift which phrasing another symptom draws.
    return random.Random(f"{member_id}:{seed}:{note_date.isoformat()}:{concern}")


def generate_note(
    member_id: str,
    seed: int,
    note_date: date,
    symptoms: list[str],
    benefit: str | None = None,
) -> str:
    unknown = [s for s in symptoms if s not in _PHRASINGS]
    if unknown:
        # Silently documenting nothing for a misspelled symptom would produce a
        # golden case that doesn't test what its name claims -- fail loudly instead.
        raise ValueError(f"unknown symptom(s): {unknown!r}")

    sentences = [_OPENER]
    for symptom in symptoms:
        sentences.append(_rng(member_id, seed, note_date, symptom).choice(_PHRASINGS[symptom]))

    if benefit is not None:
        rng = _rng(member_id, seed, note_date, f"benefit:{benefit}")
        sentences.append(rng.choice(_BENEFIT_PHRASINGS).format(benefit=benefit))

    return " ".join(sentences)
