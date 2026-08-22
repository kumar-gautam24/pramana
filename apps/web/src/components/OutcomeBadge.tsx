/**
 * The machine's outcome on a case.
 *
 * Exactly two values can be rendered as an outcome, and they are enumerated here rather
 * than derived from the string: `approve` and `escalate`. Anything else is labelled
 * "unrecognised" and shown verbatim.
 *
 * That guard looks redundant -- `determinations.outcome` is a CHECK constraint and
 * `Outcome` is a two-member enum -- and it is the point. The one thing this console must
 * never do is put a denial in front of a reviewer as though the system had issued one
 * (ADR-0002; California SB 1120 and the Medicare Advantage rule reserve an adverse
 * determination to a licensed clinician). A default branch that mapped an unknown value
 * to a plausible label is the shape that failure would take, so there is no default
 * branch.
 *
 * Escalate is amber, never red. Red reads as refusal; an escalation is the machine
 * handing a decision to a person, which is the system working as designed.
 */

const RENDERABLE = {
  approve: { label: "Approved", className: "badge badge--approve" },
  escalate: { label: "Referred to clinician", className: "badge badge--escalate" },
} as const;

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (outcome === null) {
    return <span className="badge badge--neutral">No determination yet</span>;
  }

  const known = RENDERABLE[outcome as keyof typeof RENDERABLE];
  if (!known) {
    return (
      <span className="badge badge--negative" title="This console renders only the two outcomes the system can produce.">
        Unrecognised outcome: {outcome}
      </span>
    );
  }

  return <span className={known.className}>{known.label}</span>;
}
