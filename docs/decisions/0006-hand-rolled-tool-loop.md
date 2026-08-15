# ADR-0006 — Hand-rolled tool loop rather than an agent framework

**Status:** accepted, 2026-08-15

## Context

The pipeline is seven fixed stages. Only one part is genuinely dynamic: which verification
tool each extracted criterion routes to, and with what parameters. LangGraph and Pydantic AI
were both considered; LangGraph in particular appears by name in many job descriptions.

## Decision

Write the tool loop by hand. Keep the provider abstraction and schema-constrained decoding
carried over from the predecessor project.

## Consequences

Every line is explicable in an interview, which matters more here than framework name
recognition. The workflow is linear and bounded; a graph runtime would obscure rather than
express it, and would contradict the principle of adding no infrastructure the system has no
use for.

Cost: no free checkpointing or graph visualisation, and one fewer keyword on the résumé. If
the pipeline ever grows genuine branching or long-running human-in-the-loop suspension across
process restarts, revisit this — that is the point at which a framework would start paying
for itself.
