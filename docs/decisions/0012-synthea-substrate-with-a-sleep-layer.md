# ADR-0012 — Synthea provides the patient substrate; the sleep domain is generated on top

**Status:** accepted, 2026-08-16. Amends [ADR-0006](0006-hand-rolled-tool-loop.md)'s sibling
decision recorded in the design spec ("Synthea + layered clinical notes").

## Context

The design chose Synthea for synthetic members, on the reasonable assumption that the
industry-standard generator would cover the clinical facts this system adjudicates.

Checking its module gallery before writing the member service showed it does not. Synthea
ships modules for allergies, asthma, COPD, dementia, epilepsy, metabolic syndrome,
osteoarthritis, lung cancer, opioid addiction and roughly thirty others — **and none for
obstructive sleep apnea.** There is no polysomnography, no AHI or RDI, no sleep-test type, and
no CPAP therapy or adherence data anywhere in the distribution.

Those are precisely the facts NCD 240.4 turns on. A member service built on Synthea alone
could not answer a single deterministic criterion in the v1 corpus.

## Decision

Split the generation by what each source is actually good for.

**Synthea provides the substrate** — demographics, encounters, and the comorbidities the
policy's third criteria set depends on: hypertension, ischemic heart disease and history of
stroke are all modelled, and their co-occurrence is realistic in a way hand-authored data is
not. Use Synthea's **CSV export**, not FHIR: this service needs patients, conditions,
encounters and observations as tables, and parsing FHIR bundles would be plumbing that proves
nothing about the system.

**The sleep domain is generated on top**, seeded from each Synthea patient so the two stay
consistent: sleep studies with a test type, apnea event count, recorded hours and AHI or RDI;
CPAP usage nights for the adherence criteria; and narrative notes carrying the documented
symptoms — daytime sleepiness, impaired cognition, mood disorders, insomnia — that the
judgment criteria read.

## Consequences

Every criterion in NCD 240.4 becomes answerable, which was not true of either source alone.
Comorbidity realism is inherited rather than invented, and the sleep layer is small enough to
control precisely — which matters, because the golden set needs *near-miss* cases (ADR-0009):
a study with 29 apnea events, or adherence at 3.9 hours a night, cannot be produced by
sampling a real-world distribution and hoping.

The honest cost: the clinically decisive facts in this system are authored by us, not by
Synthea. The generator must therefore be treated as part of the test apparatus and reviewed
with the same care as the eval harness — a generator that quietly cannot express a case is a
gap in coverage that no test will report.

Rejected: writing a custom Synthea OSA module. Synthea modules are JSON state machines and the
work is feasible, but it buys realism in the one area where the golden set needs deliberate
control, at the price of a Java toolchain detour that demonstrates nothing about this system.

Also rejected: dropping Synthea entirely. Hand-authored comorbidity co-occurrence would be
guesswork, and criteria set 3 in [ADR-0011](0011-alternative-criteria-sets.md) rests on it.

Related: [ADR-0008](0008-human-authored-golden-labels.md) — the labels stay human-authored
regardless of how the underlying members are produced.
