import { EvidenceView } from "@/components/EvidenceView";
import { VerdictBadge } from "@/components/VerdictBadge";
import { formatConfidence } from "@/lib/format";
import type { Criterion } from "@/lib/types";

/**
 * One criterion: what the policy requires, what the system found, and how it decided.
 *
 * The type tag is not decoration. It is the disclosure that matters most on this screen:
 * `judgment` means a language model read the notes and formed an opinion, and the other
 * three mean a query and a comparison in code (ADR-0003). A reviewer weighs those
 * differently, so the distinction is on the row rather than in a legend.
 *
 * Confidence is shown only for judgment criteria. A deterministic verifier records 1.0
 * because arithmetic is not uncertain, and printing "100%" next to a SQL comparison would
 * suggest a probability was estimated when none was.
 *
 * Evidence is inside a `<details>`: collapsed, the screen shows a whole policy at once;
 * expanded, it shows the working. The reviewer chooses. It is never absent -- a verdict a
 * reviewer cannot check is the thing this project exists not to produce.
 */
export function CriterionRow({
  criterion,
  blocking,
}: {
  criterion: Criterion;
  blocking: boolean;
}) {
  const isJudgment = criterion.type === "judgment";

  return (
    <div className={blocking ? "criterion criterion--blocking" : "criterion"}>
      <div className="row">
        <span className="mono muted small">#{criterion.ordinal}</span>
        <span className="tag">{criterion.type}</span>
        <VerdictBadge verdict={criterion.verdict} />
        {blocking ? <span className="badge badge--escalate">Blocking</span> : null}
        <div className="shell__spacer" />
        {isJudgment && criterion.confidence !== null ? (
          <span className="small muted">
            model confidence {formatConfidence(criterion.confidence)}
          </span>
        ) : null}
      </div>

      <p className="criterion__text">{criterion.text}</p>

      <div className="row small muted">
        <span>
          Source: {criterion.source_display_id}{" "}
          <span className="mono">(chunk {criterion.source_chunk_id})</span>
        </span>
        {criterion.tool ? <span className="mono">{criterion.tool}</span> : null}
      </div>

      <details>
        <summary className="small muted" style={{ cursor: "pointer" }}>
          Evidence
        </summary>
        <EvidenceView evidence={criterion.evidence} />
      </details>
    </div>
  );
}
