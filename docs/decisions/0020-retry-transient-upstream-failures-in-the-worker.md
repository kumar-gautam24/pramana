# ADR-0020 — A transient upstream failure is retried in the worker, and recorded

**Status:** accepted, 2026-08-22

## Context

On the first live end-to-end run, four of five cases escalated with
`blocking: ["upstream_unavailable"]`. All four were the model provider answering 429: the
free tier meters 8000 tokens a minute and one extraction costs about 2800.

The pipeline behaved exactly as designed. `UpstreamUnavailable` was caught, the case
short-circuited, the reason was recorded as `insufficient_evidence`, the audit trail was
intact, nothing crashed and nothing was approved on missing evidence. That part is right and
is not what this ADR changes.

What is wrong is what those four cases became. Each is now **permanently decided as
escalated because of our own rate limit** — a fact about our infrastructure, not about the
member's record — and it is sitting on a clinician's queue. The whole argument of this
project is that an escalation means *a human should look at this evidence*. There is no
evidence here for a human to look at, and nothing a clinician can do resolves it. An
escalation that a clinician cannot act on is the review-time cost the eval harness measures
in dollars, spent on nothing.

`UpstreamUnavailable` could not express the difference. A 429 and a schema mismatch produced
the same exception and therefore the same permanent determination, even though one clears on
its own in under a minute and the other never does.

Where the retry goes was already half-decided. `policy_client`'s docstring has said since
plan 04 task 4 why a retry hidden in a client is wrong: it would triple a case's latency with
nothing in the audit trail to say why. That argument still holds — and its converse names the
right layer. The worker is the only place that can wait, run the case again, and *write each
attempt into `case_events`*, which is what keeps the audit claim true: everything the system
did in reaching a determination is in this log.

## Decision

**`UpstreamUnavailable` carries `transient: bool`**, set explicitly at every raise site. A
timeout and a connection error are transient; a non-2xx is transient exactly when it is 429
or 5xx; a body the client cannot parse never is, because schema drift does not heal on a
second attempt. It defaults to `False` — a failure nobody has classified must not be retried,
since the cost of not retrying a transient failure is one avoidable escalation and the cost
of retrying a permanent one is the case timeout plus the tokens. It also carries
`retry_after`, read from the server's own header when it sent one.

**The pipeline re-raises a transient failure instead of short-circuiting it.** A permanent one
still becomes the `upstream_unavailable` determination exactly as before. Both paths funnel
through one function, `_upstream_stopped`, so the four call sites cannot diverge.

**The worker retries with backoff and records every attempt.** Four attempts, waiting 5s, 20s
then 60s between them. The rungs are sized against the failure that was measured rather than
chosen for roundness: a token-per-minute bucket refills over a minute, so the ladder must
contain at least one wait longer than that window or every attempt lands inside the same
exhausted minute. The two short rungs cover the other transient shapes — a restarting
container, a reset connection, a momentary 503 — which clear in seconds. A server's own
`Retry-After` lengthens a rung but never shortens one, and is capped at 90 seconds.

Each wait appends a `retry` event *before* sleeping, carrying the service, the detail, the
attempt number and the delay. Exhaustion appends `upstream_exhausted` and then records the
ordinary `upstream_unavailable` determination through `pipeline.record_upstream_exhausted`,
so a case that never got its evidence still ends as a determination rather than as silence.

**Total waiting is 85 seconds, and that number is bounded from outside.** `evals` gives a case
`case_timeout_seconds` (240s) to settle before recording it as unfinished. A retried case has
to fit inside that or the retries produce nothing measurable, so the two constants are
cross-referenced in both files and move together.

## Consequences

A rate-limited case now takes up to a minute and a half longer and then gets a real
determination, instead of taking two seconds and getting a fake one. That is the trade, and
it is obviously the right one: the slow path ends with an answer about the member.

A retry re-runs `adjudicate` from the top, so the case pays for its extraction again. This is
deliberate rather than an oversight. `criteria.insert_many` already delete-then-inserts
precisely so a second `adjudicate(case_id)` is well-defined — the at-least-once Redis stream
guarantees one will happen anyway — and a partial-resume path would be a second, less-tested
route to the gate. The audit log gains a second `started`/`eligibility`/`policy` sequence per
attempt, which is correct: those stages really did run again.

The `retry` events are not decoration. Without them a case sits `running` for ninety seconds
with nothing distinguishing "waiting out a rate limit" from "the worker has hung", and the
system would be doing work on a member's case that its own audit trail does not mention. The
console renders both new event types by name.

An exhausted retry still produces `blocking: ["upstream_unavailable"]`, which the console
already explains as "a service this decision depends on could not be reached". A clinician
still cannot act on it — but now it means the system tried for a minute and a half, which is
a different claim from the one those four cases were making before.

What this does **not** fix: a provider whose limits are structurally below one case's cost.
The measured free tier cannot serve a single case's extraction plus judgment round inside its
window; no retry ladder fixes that, and the ways out are a paid tier or fewer calls per case
(the latter already done — [ADR-0015](0015-batched-judgment-verification.md)).

Related: [ADR-0005](0005-case-events-as-audit-log.md),
[ADR-0015](0015-batched-judgment-verification.md)
