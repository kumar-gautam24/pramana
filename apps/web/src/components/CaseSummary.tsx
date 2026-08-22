import { OutcomeBadge } from "@/components/OutcomeBadge";
import { formatDate, formatDateTime, reasonLabel, shortCircuitLabel } from "@/lib/format";
import type { Case, Determination } from "@/lib/types";

/**
 * What was requested, and what the system did with it.
 *
 * `blocking` holds two different kinds of value and this is where they are told apart. On
 * a case that reached verification it names criteria, and those are marked in the criteria
 * panel below. On a case that stopped earlier it holds a single marker for the stage that
 * stopped it -- `not_eligible`, `no_governing_policy`, `no_criteria`,
 * `upstream_unavailable` -- and there are no criteria for the reviewer to read at all. A
 * screen that did not distinguish them would show an empty criteria panel and no
 * explanation, which is the one case where a reviewer would be right to distrust it.
 *
 * `upstream_unavailable` deserves the reviewer's attention for a different reason from
 * the others: it is a fact about our infrastructure, not about the member's record, and
 * nothing a clinician does can resolve it.
 *
 * A case whose `run_mode` is not `deterministic` gets a banner above everything else. Such a
 * case exists to measure what happens when the model does the arithmetic (ADR-0021), and its
 * determination is an experimental result rather than an adjudication -- the check is
 * `!== "deterministic"` rather than `=== "model_arithmetic"` so a mode this console has never
 * heard of is also flagged, which is the safe direction to be wrong in.
 */
export function CaseSummary({
  caseData,
  determination,
  criterionIds,
}: {
  caseData: Case;
  determination: Determination | null;
  criterionIds: Set<string>;
}) {
  const markers = (determination?.blocking ?? []).filter((entry) => !criterionIds.has(entry));

  return (
    <section className="card stack stack--tight">
      {caseData.run_mode !== "deterministic" ? (
        <p className="experiment">
          <strong>Experimental run mode: {caseData.run_mode}.</strong> The threshold, date and
          category comparisons on this case were performed by a language model instead of by
          code. This case exists to measure the error rate of doing that. Its determination is
          a measurement, not an adjudication, and must not be relied on for a member.
        </p>
      ) : null}

      <div className="row">
        <h1>
          <span className="mono">{caseData.requested_code}</span> for member{" "}
          <span className="mono">{caseData.member_id.slice(0, 8)}</span>
        </h1>
        <div className="shell__spacer" />
        <OutcomeBadge outcome={determination?.outcome ?? null} />
      </div>

      <dl className="evidence">
        <dt>Diagnosis</dt>
        <dd className="mono">{caseData.icd10}</dd>
        <dt>Date of service</dt>
        <dd>{formatDate(caseData.date_of_service)}</dd>
        <dt>Request</dt>
        <dd>{caseData.kind}</dd>
        <dt>Submitted</dt>
        <dd>{formatDateTime(caseData.created_at)}</dd>
        <dt>Pipeline</dt>
        <dd>{caseData.status}</dd>
        {determination ? (
          <>
            <dt>Decided</dt>
            <dd>{formatDateTime(determination.decided_at)}</dd>
          </>
        ) : null}
      </dl>

      {caseData.request_text ? (
        <div>
          <h3 className="muted">Submitted narrative</h3>
          <p className="small">{caseData.request_text}</p>
        </div>
      ) : null}

      {determination?.reason ? (
        <p>
          <strong>Why it came to you:</strong> {reasonLabel(determination.reason)}
        </p>
      ) : null}

      {markers.map((marker) => {
        const explanation = shortCircuitLabel(marker);
        return (
          <p className="notice" key={marker}>
            {/* An unknown marker is printed as itself. Inventing a sentence for a value
                this console has never seen would be putting words in the system's mouth
                on the one screen where that matters most. */}
            {explanation ?? <span className="mono">{marker}</span>}
          </p>
        );
      })}
    </section>
  );
}
