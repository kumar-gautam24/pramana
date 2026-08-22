"use client";

/**
 * A run beside its ablated twin.
 *
 * This is the screen the ablation exists for. The design's central claim — the model decides
 * what the rules are, code checks the facts ([ADR-0003](../../../../docs/decisions/0003-ai-extracts-rules-code-checks-facts.md))
 * — has been argued from first principles since the first week, and its own consequences
 * section promises it will be *measured*. Two report pages diffed by eye is not a measurement.
 * A signed delta, over the cases both arms actually decided, with the conditions of both runs
 * printed above it, is.
 *
 * So the conditions come first on the page, not last. A reader has to be able to check that
 * the two runs differ in one thing before reading what that thing cost, and when they differ
 * in more, the delta is **absent rather than zero** and the reason names the fields.
 *
 * The per-case disagreements are last but are usually the finding. At the golden-set sizes
 * this project has, "the ablated arm approved case 7 and the baseline referred it" is a
 * checkable statement and a cost delta of one claim amount is a statistic.
 */

import { formatMoney, formatRate } from "@/lib/format";
import type { CasePoint, RunComparison, RunDelta } from "@/lib/types";

/** A signed number, with its sign shown even when positive: the sign is the result. */
function signed(value: number, render: (n: number) => string): string {
  return value > 0 ? `+${render(value)}` : render(value);
}

/**
 * Whether a delta is bad for the ablated arm.
 *
 * Only used to choose a colour, and only for cost — where "more expensive" is unambiguously
 * worse. Counts are deliberately left uncoloured: more wrongful escalations is worse than
 * fewer, but so is more wrongful approvals, and they are worse in different currencies. A
 * palette that flattened that would undo the asymmetry this whole project is about.
 */
function costClass(value: number): string {
  if (value === 0) return "muted";
  return value > 0 ? "delta delta--worse" : "delta delta--better";
}

function DeltaRow({
  label,
  baseline,
  ablated,
  delta,
  render,
  colour = false,
}: {
  label: string;
  baseline: number;
  ablated: number;
  delta: number | null;
  render: (n: number) => string;
  colour?: boolean;
}) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{render(baseline)}</td>
      <td>{render(ablated)}</td>
      <td className={delta !== null && colour ? costClass(delta) : "muted"}>
        {delta === null ? "—" : signed(delta, render)}
      </td>
    </tr>
  );
}

function whole(value: number): string {
  return String(value);
}

export function RunComparisonView({ comparison }: { comparison: RunComparison }) {
  const { baseline, ablated, case_level, costs } = comparison;
  const delta: RunDelta | null = case_level.delta;

  const rows: {
    label: string;
    pick: (point: CasePoint) => number;
    deltaOf: (d: RunDelta) => number;
    render: (n: number) => string;
    colour?: boolean;
  }[] = [
    {
      label: "Correctly approved",
      pick: (p) => p.correct_approve,
      deltaOf: (d) => d.correct_approve,
      render: whole,
    },
    {
      label: "Correctly referred",
      pick: (p) => p.correct_escalate,
      deltaOf: (d) => d.correct_escalate,
      render: whole,
    },
    {
      label: "Wrongly approved",
      pick: (p) => p.wrongly_approved,
      deltaOf: (d) => d.wrongly_approved,
      render: whole,
    },
    {
      label: "Wrongly referred",
      pick: (p) => p.wrongly_escalated,
      deltaOf: (d) => d.wrongly_escalated,
      render: whole,
    },
    {
      label: "Auto-approval rate",
      pick: (p) => p.auto_approval_rate,
      deltaOf: (d) => d.auto_approval_rate,
      render: formatRate,
    },
    {
      label: `Cost of wrong approvals (× ${formatMoney(costs.average_claim_amount)})`,
      pick: (p) => p.wrongly_approved_cost,
      deltaOf: (d) => d.wrongly_approved_cost,
      render: formatMoney,
      colour: true,
    },
    {
      label: `Cost of wrong referrals (× ${formatMoney(costs.review_cost)})`,
      pick: (p) => p.wrongly_escalated_cost,
      deltaOf: (d) => d.wrongly_escalated_cost,
      render: formatMoney,
      colour: true,
    },
    {
      label: "Total cost",
      pick: (p) => p.total_cost,
      deltaOf: (d) => d.total_cost,
      render: formatMoney,
      colour: true,
    },
  ];

  return (
    <div className="stack stack--tight">
      {/* The conditions, before the numbers. A reader cannot evaluate a delta without them,
          and a screen that put them below would be inviting the delta to be read alone. */}
      <table className="table">
        <thead>
          <tr>
            <th>Condition</th>
            <th>Baseline (run {baseline.id})</th>
            <th>Ablated (run {ablated.id})</th>
          </tr>
        </thead>
        <tbody>
          {(["model", "prompt_version", "git_sha", "ablation", "status"] as const).map(
            (field) => {
              const same = String(baseline[field]) === String(ablated[field]);
              return (
                <tr key={field}>
                  <th scope="row" className="mono small">
                    {field}
                  </th>
                  <td className="mono small">{String(baseline[field])}</td>
                  <td className={same ? "mono small" : "mono small delta delta--worse"}>
                    {String(ablated[field])}
                  </td>
                </tr>
              );
            },
          )}
        </tbody>
      </table>

      {comparison.comparable ? (
        <p className="notice">
          These two runs differ in their ablation and in nothing else, so the difference below
          is attributable to it. That is the whole reason the comparison is worth reading.
        </p>
      ) : (
        <p className="experiment">
          <strong>No delta, deliberately.</strong>{" "}
          {comparison.not_a_pair ??
            `These runs differ in ${comparison.differs_in.join(", ")} as well as in their ablation.`}{" "}
          Each run&rsquo;s own figures are below and are valid; their difference is not
          attributable to anything, so it is withheld rather than shown as a number that would
          be read as a result.
        </p>
      )}

      <p className="hint">
        Every figure below is over the <strong>{comparison.shared_cases}</strong> golden cases
        both runs decided, at threshold 0.00 — the one operating point both were actually run
        at. Cases only one arm reached are excluded, because a run that gave up on its two
        hardest cases would otherwise look cheaper than the run that finished them.
        {comparison.only_in_baseline.length > 0 || comparison.only_in_ablated.length > 0 ? (
          <>
            {" "}
            Excluded: {comparison.only_in_baseline.length} reached only by the baseline,{" "}
            {comparison.only_in_ablated.length} only by the ablated run.
          </>
        ) : null}
      </p>

      <table className="table">
        <thead>
          <tr>
            <th>Measure</th>
            <th>Baseline</th>
            <th>Ablated</th>
            <th>Difference</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <DeltaRow
              key={row.label}
              label={row.label}
              baseline={row.pick(case_level.baseline)}
              ablated={row.pick(case_level.ablated)}
              delta={delta === null ? null : row.deltaOf(delta)}
              render={row.render}
              colour={row.colour}
            />
          ))}
        </tbody>
      </table>

      <p className="hint">
        The ablated run handed{" "}
        <strong>
          {comparison.ablation_coverage.ablated.by_model_arithmetic} of{" "}
          {comparison.ablation_coverage.ablated.comparison_criteria}
        </strong>{" "}
        comparison-bearing criteria to the model over these cases; the baseline handed it{" "}
        {comparison.ablation_coverage.baseline.by_model_arithmetic}. The shortfall is not a
        bug: a condition-code criterion has no comparison step to move, because the member
        service filters by code in SQL.
      </p>

      <h3>Cases the two arms decided differently</h3>
      {comparison.disagreements.length === 0 ? (
        <p className="notice">
          None. Over {comparison.shared_cases} cases the two arms reached the same outcome
          every time — which is a result, not an absence of one, and the honest reading of it
          at this set size is that the set is too small to separate them.
        </p>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Golden case</th>
              <th>A person expected</th>
              <th>Baseline</th>
              <th>Ablated</th>
            </tr>
          </thead>
          <tbody>
            {comparison.disagreements.map((item) => (
              <tr key={item.golden_case_id}>
                <td className="mono">{item.golden_case_id}</td>
                <td>{item.expected}</td>
                {/* Marked against the human label, not against each other: both arms can be
                    wrong at once, and a rendering that only showed which one differed would
                    hide that. */}
                <td className={item.baseline === item.expected ? "" : "delta delta--worse"}>
                  {item.baseline ?? "no determination"}
                </td>
                <td className={item.ablated === item.expected ? "" : "delta delta--worse"}>
                  {item.ablated ?? "no determination"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
