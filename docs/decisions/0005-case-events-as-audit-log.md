# ADR-0005 — Case events are the audit log; Redis delivers, Postgres records

**Status:** accepted, 2026-08-15

## Context

Texas permits the insurance commissioner to audit and inspect an automated decision system at
any time. "Why did it decide this?" needs an answer that reproduces. Separately, the reviewer
console should show the pipeline working step by step rather than presenting a verdict.

Both needs point at the same structure: a record of what happened, in order.

## Decision

Every pipeline stage appends a row to `case_events`, which is append-only — never updated,
never deleted. The same event is published to Redis Pub/Sub for live SSE fan-out to the
console. Redis Streams carry work between the API and its workers.

State is **not** derived by replaying events. Postgres rows remain authoritative; the event
log is a record alongside them, not a substitute for them.

## Consequences

The audit trail and the live step view are the same data, so the thing regulators inspect is
the thing reviewers watch. Neither is a bolt-on.

Event-driven here is justified by a regulatory requirement rather than by fashion, which
keeps it consistent with the principle of adding no infrastructure the system has no use for.

Full event sourcing was rejected: deriving all state by replay is significant complexity for
a seven-step linear pipeline, and would buy nothing the append-only log does not already
provide.
