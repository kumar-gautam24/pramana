/**
 * What a run measured.
 *
 * California requires an AI tool used in coverage decisions to be periodically assessed for
 * accuracy. This screen is where that assessment is read, so its job is to make every figure
 * on it checkable rather than impressive.
 *
 * Two rules run through the whole component. **Every money figure is shown as a count times a
 * rate**, with the rate named — never a bare total, because a total nobody can decompose is a
 * number to be believed rather than checked, and the rates are configuration precisely so a
 * reader who disagrees can change them and re-run. **A number nobody measured is never shown
 * as zero** — extraction scores are null when no case in the run carried a human-authored
 * criteria list, and rendering that as 0% would report a failure where there was no
 * measurement.
 */

import { ThresholdSweep } from "@/components/ThresholdSweep";
import { formatDateTime, formatMoney, formatRate } from "@/lib/format";
import type { CasePoint, EvalRunReport } from "@/lib/types";

/** A money figure, decomposed into the count and the rate it is a product of. */
function Cost({
  count,
  rate,
  rateLabel,
  total,
}: {
  count: number;
  rate: string;
  rateLabel: string;
  total: number;
}) {
  return (
    <span>
      <strong>{formatMoney(total)}</strong>{" "}
      <span className="muted small">
        = {count} × {rate} ({rateLabel})
      </span>
    </span>
  );
}

function PointDetail({
  point,
  costs,
}: {
  point: CasePoint;
  costs: EvalRunReport["costs"];
}) {
  return (
    <dl className="evidence">
      <dt>Threshold</dt>
      <dd className="mono">{point.min_confidence.toFixed(2)}</dd>

      <dt>Auto-approved</dt>
      <dd>
        {formatRate(point.auto_approval_rate)}{" "}
        <span className="muted small">
          of the {point.correct_approve + point.correct_escalate + point.wrongly_approved +
            point.wrongly_escalated}{" "}
          cases that reached a determination
        </span>
      </dd>

      <dt>Correct</dt>
      <dd>
        {point.correct_approve} approved, {point.correct_escalate} referred
      </dd>

      <dt>Wrongly approved</dt>
      <dd>
        <Cost
          count={point.wrongly_approved}
          rate={formatMoney(costs.average_claim_amount)}
          rateLabel="average claim"
          total={point.wrongly_approved_cost}
        />
      </dd>

      <dt>Wrongly escalated</dt>
      <dd>
        <Cost
          count={point.wrongly_escalated}
          rate={formatMoney(costs.review_cost)}
          rateLabel={`${costs.review_minutes} min at ${formatMoney(
            costs.clinician_hourly_rate,
          )}/h`}
          total={point.wrongly_escalated_cost}
        />
      </dd>

      <dt>Total</dt>
      <dd>
        <strong>{formatMoney(point.total_cost)}</strong>
      </dd>
    </dl>
  );
}

export function RunReport({ report }: { report: EvalRunReport }) {
  const { run, costs, case_level, criterion_level, ablation_coverage } = report;
  const ablated = run.ablation !== "none";

  return (
    <div className="stack">
      {ablated ? (
        <p className="experiment">
          <strong>Ablation: {run.ablation}.</strong> The cases in this run were adjudicated
          with a language model performing the threshold, category and date comparisons that
          deterministic code otherwise does. Its numbers describe that arrangement, not the
          system as shipped — they are the argument against it.
        </p>
      ) : null}

      <section className="card stack stack--tight">
        <h2>Run {run.id}</h2>
        <dl className="evidence">
          <dt>Model</dt>
          <dd className="mono">{run.model}</dd>
          <dt>Prompt</dt>
          <dd className="mono">{run.prompt_version}</dd>
          <dt>Commit</dt>
          <dd className="mono">{run.git_sha}</dd>
          <dt>Ablation</dt>
          <dd className="mono">{run.ablation}</dd>
          <dt>Status</dt>
          <dd>{run.status}</dd>
          <dt>Started</dt>
          <dd>{formatDateTime(run.started_at)}</dd>
          <dt>Finished</dt>
          <dd>{run.finished_at ? formatDateTime(run.finished_at) : "still running"}</dd>
          <dt>Cases scored</dt>
          <dd>{report.cases_scored}</dd>
        </dl>
        {run.status === "running" ? (
          <p className="notice">
            This run is still going. Everything below is scored from the cases finished so far
            and will change; reload to see it move.
          </p>
        ) : null}
      </section>

      <section className="card stack stack--tight">
        <h2>What being wrong cost</h2>
        {/*
          The two failure directions are not symmetric and the report must not average them
          away: there is no third kind of error because there is no deny path (ADR-0002), so
          one of these costs money and the other costs a clinician's afternoon.
        */}
        <p className="small muted">
          At the threshold the run was configured with, and at the cheapest threshold the
          sweep found. Both are counts multiplied by the rates below, which come from this
          service&rsquo;s configuration and are published here so they can be argued with.
        </p>

        <h3>As configured (threshold 0.00)</h3>
        <PointDetail point={case_level.at_threshold_zero} costs={costs} />

        {case_level.best ? (
          <>
            <h3>Cheapest threshold on the sweep</h3>
            <PointDetail point={case_level.best} costs={costs} />
          </>
        ) : null}
      </section>

      <section className="card stack stack--tight">
        <h2>Total cost against the confidence threshold</h2>
        <ThresholdSweep sweep={case_level.sweep} best={case_level.best} />
      </section>

      <section className="card stack stack--tight">
        <h2>Did it read the policy correctly?</h2>
        {/*
          Precision and recall rather than one accuracy figure, because the two failures
          differ in kind: missing a criterion the policy contains can produce a wrongful
          approval, and inventing one can only produce a wrongful escalation.
        */}
        <dl className="evidence">
          <dt>Cases labelled</dt>
          <dd>
            {criterion_level.cases_with_expected_criteria}{" "}
            <span className="muted small">
              of {report.cases_scored} carried a human-authored criteria list; the rest are
              not scored for extraction at all
            </span>
          </dd>
          <dt>Precision</dt>
          <dd>{formatRate(criterion_level.mean_precision)}</dd>
          <dt>Recall</dt>
          <dd>{formatRate(criterion_level.mean_recall)}</dd>
          <dt>F1</dt>
          <dd>{formatRate(criterion_level.mean_f1)}</dd>
        </dl>
        <p className="hint">
          Matched by token overlap against the human-authored wording, which is a blunt proxy
          for &ldquo;these describe the same requirement&rdquo;. It is here to tell you when a
          person should go and read both lists, not to stand in for having done so.
        </p>
      </section>

      <section className="card stack stack--tight">
        <h2>How much of this run was ablated</h2>
        <p>
          <strong>
            {ablation_coverage.by_model_arithmetic} of {ablation_coverage.comparison_criteria}
          </strong>{" "}
          comparison-bearing criteria were decided by the model.
        </p>
        <p className="hint">
          {/*
            Reported rather than assumed. Stating it costs one line; a reader discovering an
            unmarked partial ablation would have reason to distrust every other figure here.
          */}
          Not every comparison can be moved to the model. A condition-code criterion has no
          comparison step — the member service filters by code in SQL, so the fetch <em>is</em>{" "}
          the membership test — and those criteria fall back to deterministic verification in
          both arms. A run with no ablation reports zero here, which is the honest reading of
          it too.
        </p>
      </section>

      {report.unfinished.length > 0 ? (
        <section className="card stack stack--tight">
          <h2>Cases the harness could not decide</h2>
          {/*
            Deliberately not folded into the escalate count. A case that never reached a
            determination is a gap in the measurement, not a refusal by the system, and
            counting it as one would let an outage read as caution.
          */}
          <p className="small muted">
            These are gaps in the measurement, not determinations. They are excluded from every
            figure above except the case count.
          </p>
          <table className="table">
            <thead>
              <tr>
                <th>Golden case</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {report.unfinished.map((item) => (
                <tr key={item.golden_case_id}>
                  <td className="mono">{item.golden_case_id}</td>
                  <td className="small">{item.error ?? "no reason recorded"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  );
}
