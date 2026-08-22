/**
 * The case's current determination, read out of its audit log.
 *
 * `GET /api/cases/{id}` returns the case row alone -- status, codes, dates -- and there
 * is no route that returns the determination on its own. The `decision` event carries
 * exactly the four fields a determination has (`services/pipeline.py::_persist_decision`),
 * so this reads it from there.
 *
 * That is safe by construction rather than by luck: the `decision` event is appended
 * *after* the transaction holding the determination and its criterion results commits.
 * The ordering was chosen for this consumer -- so a console can never render an APPROVE
 * for a case that has no determination behind it. Read the other way round, an event
 * present here always has a committed row behind it.
 *
 * The last one wins. A case can be adjudicated again (an at-least-once redelivery does
 * exactly that), each run appends its own `decision`, and the current determination is
 * the newest -- the same rule the database read would apply.
 *
 * This is the audit log's copy, not the `determinations` row. It is sufficient for
 * everything this console renders and it is what an auditor would read; if a screen ever
 * needs `thresholds` -- the gate's configuration at decision time, which the event does
 * not carry -- that needs a route, not a wider event.
 */

import type { CaseEvent, Determination } from "@/lib/types";

export function determinationFrom(events: CaseEvent[]): Determination | null {
  const decision = [...events].reverse().find((event) => event.type === "decision");
  if (!decision) return null;

  const payload = decision.payload;
  return {
    outcome: String(payload.outcome),
    reason: typeof payload.reason === "string" ? payload.reason : null,
    blocking: Array.isArray(payload.blocking) ? payload.blocking.map(String) : [],
    winning_set: typeof payload.winning_set === "number" ? payload.winning_set : null,
    decided_at: decision.created_at,
  };
}
