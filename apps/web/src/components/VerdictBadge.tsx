import { verdictLabel } from "@/lib/format";
import type { Verdict } from "@/lib/types";

/**
 * One criterion's verdict.
 *
 * `not_met` is red here while an escalated *case* is amber, and the difference is
 * deliberate: a criterion the record contradicts is a genuine negative finding about the
 * evidence, whereas the case outcome it contributes to is a referral, not a refusal.
 * Colouring both the same would collapse the distinction the whole system rests on.
 *
 * `insufficient_evidence` is amber rather than grey. It is not a missing value or an
 * error -- it is the most useful thing the system produces, because it is what sends a
 * case to a human.
 */
const CLASSES: Record<Verdict, string> = {
  met: "badge badge--approve",
  not_met: "badge badge--negative",
  insufficient_evidence: "badge badge--escalate",
};

export function VerdictBadge({ verdict }: { verdict: Verdict | null }) {
  const className = verdict === null ? "badge badge--neutral" : CLASSES[verdict];
  return <span className={className}>{verdictLabel(verdict)}</span>;
}
