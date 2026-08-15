# ADR-0009 — The golden set must contain near-miss cases

**Status:** accepted, 2026-08-15

## Context

The predecessor's refusal set contained fifteen questions, every one of them out-of-domain:
pricing, a competing framework, an account balance, a support phone number. Those are easy
refusals — embedding distance alone separates them, and no gate is needed.

Reviewing a live run exposed the gap. The most in-domain unanswerable question scored 1.175
against a 2.0 threshold, well above the −1.04 median of the rest, because the corpus genuinely
discussed the topic without answering the question. There was almost no coverage above that
line, so the headline "13% wrongly answered" was measured against the easy half of the
problem.

## Decision

At least 8 of the v1 golden cases must be **near-miss**: in-domain, partially satisfied,
where the policy nearly but does not quite support approval. A member meeting four of five
criteria; a sleep study of the right type but 29 apnea events; adherence documented but
clinical benefit not.

## Consequences

The reported wrongly-approved rate reflects the cases that actually matter. Numbers will look
worse than a set of easy refusals would produce, which is the point — a metric that flatters
the system is worth nothing.

Near-miss cases are also the ones where per-criterion refusal earns its keep: the system can
say which single criterion blocked approval, which is exactly what a reviewer needs.
