# ADR-0004 — CMS coverage determinations as the corpus; CPT descriptions never committed

**Status:** accepted, 2026-08-15

## Context

The corpus needs real, messy, multi-criteria coverage policy. Two candidates: commercial
payer medical policy bulletins (Aetna, UnitedHealthcare, Cigna) and CMS National and Local
Coverage Determinations.

Commercial bulletins are publicly readable but copyrighted, so redistributing them in a
public repository is legally murky. CMS determinations are public domain. However, the CMS
Medicare Coverage Database bulk download requires accepting ADA, AMA and NUBC licence terms,
because the data embeds CPT code descriptions, which the AMA holds copyright over.

## Decision

Use CMS NCD/LCD data as the corpus. Ingest CPT values as bare identifiers. Never commit CPT
descriptions to the repository. ICD-10 and HCPCS are free of such restrictions and may be
included in full.

The corpus directory is gitignored and reproduced by an ingestion script, so a clone fetches
its own copy under its own acceptance of the CMS terms.

## Consequences

The repository is safe to publish. Medicare Advantage is also exactly the population the
federal "no algorithm without human review" rule governs, so the corpus and the regulatory
argument reinforce each other.

Cost: a clone cannot run until the operator downloads the corpus and accepts CMS terms. The
README documents this as a deliberate constraint rather than an oversight.
