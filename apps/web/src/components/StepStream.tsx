import { OutcomeBadge } from "@/components/OutcomeBadge";
import { formatTime, stageLabel } from "@/lib/format";
import type { CaseEvent } from "@/lib/types";

/**
 * The pipeline showing its work: every stage it ran, in order, with what that stage
 * found.
 *
 * The reviewer watches the machine reason rather than receiving a verdict from it. That
 * is the audit surface Texas can demand of an automated decision system, and it is how
 * this kind of software earns the trust it is asking for.
 *
 * Presentational: `useCaseEvents` owns replay and the live subscription.
 */

/** A one-line summary of a stage's payload, in the stage's own terms. */
function stepDetail(event: CaseEvent): React.ReactNode {
  const payload = event.payload;
  switch (event.type) {
    case "eligibility":
      return `coverage ${String(payload.coverage_status)}`;
    case "policy":
      return `${String(payload.hit_count)} policy passages retrieved`;
    case "criteria":
      return `${String(payload.criterion_count)} criteria across ${String(
        payload.set_count,
      )} alternative routes`;
    case "criterion":
      return `route ${String(payload.set_ordinal)} #${String(payload.ordinal)} → ${String(
        payload.verdict,
      )} (${String(payload.tool)})`;
    case "retry":
      return `${String(payload.service)}: ${String(payload.detail)} — attempt ${String(
        payload.attempt,
      )} of ${String(payload.of)}, waiting ${String(payload.retrying_in_seconds)}s`;
    case "upstream_exhausted":
      return `${String(payload.service)} still unavailable after ${String(
        payload.attempts,
      )} attempts: ${String(payload.detail)}`;
    case "decision":
      return <OutcomeBadge outcome={String(payload.outcome)} />;
    default:
      return null;
  }
}

export function StepStream({
  events,
  live,
  error,
  streamError,
}: {
  events: CaseEvent[];
  live: boolean;
  error: string | null;
  streamError: string | null;
}) {
  return (
    <section className="card stack stack--tight">
      <div className="row">
        <h2>How this case was decided</h2>
        <div className="shell__spacer" />
        {live ? <span className="live">live</span> : null}
      </div>

      {error ? <p className="error">{error}</p> : null}
      {streamError ? <p className="notice">{streamError}</p> : null}

      {events.length === 0 ? (
        <p className="notice">No steps recorded yet.</p>
      ) : (
        <ol className="steps">
          {events.map((event) => (
            <li className="step" key={event.seq}>
              <span className="step__seq">{formatTime(event.created_at)}</span>
              <span>
                {stageLabel(event.type)}
                <span className="step__detail"> {stepDetail(event)}</span>
              </span>
              <span className="step__seq">#{event.seq}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
