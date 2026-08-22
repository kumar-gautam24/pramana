/**
 * The threshold sweep, drawn as the argument it is.
 *
 * The system ships with a confidence threshold. The honest defence of that number is not
 * "we picked 0.7" but *here is total cost against every threshold, and the one we ship is the
 * minimum of the curve* — a claim a reader can check and disagree with. So the curve is the
 * point of this component, and the table beneath it is the evidence for the curve.
 *
 * Two component series and their sum, because the shape only argues if you can see why it
 * bends: raising the bar converts wrongful approvals into wrongful escalations, one costs a
 * claim and the other costs a clinician's time, and the minimum is where the two trade evenly.
 * Every one of those figures is a count multiplied by a published rate — `EvalRunReport.costs`
 * travels with the report for exactly this reason — and never a bare score.
 *
 * The palette is two categorical hues plus text ink for the total, validated with the dataviz
 * skill's checker at both surfaces (all pairs, both modes: lightness band, chroma floor, CVD
 * separation, normal-vision separation, contrast). Blue and amber rather than the console's
 * own green/amber/red: those are reserved for outcome and verdict, and reusing them here would
 * suggest a wrongful-approval cost is an "approve" the way a badge is. The total is ink rather
 * than a third hue because it is an aggregate of the other two, not a peer of them.
 *
 * Identity is never colour alone: both series are direct-labelled at their right-hand end and
 * the table repeats every number.
 */

import { formatMoney, formatRate } from "@/lib/format";
import type { CasePoint } from "@/lib/types";

const WIDTH = 640;
const HEIGHT = 240;
const PADDING = { top: 16, right: 132, bottom: 34, left: 68 };

const PLOT_WIDTH = WIDTH - PADDING.left - PADDING.right;
const PLOT_HEIGHT = HEIGHT - PADDING.top - PADDING.bottom;

/** Enough vertical room for one 11px label without it touching its neighbour. */
const LABEL_PITCH = 14;

/**
 * Push overlapping direct labels apart, keeping their order.
 *
 * Not cosmetic. The expected shape of a good run is zero wrongful approvals at every
 * threshold, which puts that series flat on the floor and makes the total identical to the
 * wrongly-escalated series -- so two of the three labels land on exactly the same pixel and
 * one is unreadable. Since the labels are what carries identity for a reader who cannot use
 * the colours, losing one is losing the legend.
 */
function spread(positions: number[]): number[] {
  const order = positions.map((y, index) => ({ y, index })).sort((a, b) => a.y - b.y);
  let previous = -Infinity;
  const resolved = positions.slice();
  for (const entry of order) {
    const y = Math.max(entry.y, previous + LABEL_PITCH);
    resolved[entry.index] = y;
    previous = y;
  }
  return resolved;
}

interface Series {
  label: string;
  /** A CSS custom property defined in globals.css, so light and dark are separate choices. */
  stroke: string;
  width: number;
  value: (point: CasePoint) => number;
}

const SERIES: Series[] = [
  { label: "Total", stroke: "var(--series-total)", width: 2.5, value: (p) => p.total_cost },
  {
    label: "Wrongly approved",
    stroke: "var(--series-a)",
    width: 2,
    value: (p) => p.wrongly_approved_cost,
  },
  {
    label: "Wrongly escalated",
    stroke: "var(--series-b)",
    width: 2,
    value: (p) => p.wrongly_escalated_cost,
  },
];

export function ThresholdSweep({
  sweep,
  best,
}: {
  sweep: CasePoint[];
  best: CasePoint | null;
}) {
  // `at` rather than indexing, and both ends bound before anything else: the tsconfig has
  // `noUncheckedIndexedAccess`, so this guard is also what proves to the compiler that the
  // first and last points exist.
  const first = sweep.at(0);
  const last = sweep.at(-1);
  if (sweep.length < 2 || first === undefined || last === undefined) {
    return <p className="notice">Not enough scored cases to sweep a threshold.</p>;
  }

  const ceiling = Math.max(...sweep.map((point) => point.total_cost));
  // A run in which nothing was ever wrong has a flat zero curve. Dividing by that ceiling
  // would put every point at the top of the plot, which reads as "cost is maximal
  // everywhere" -- the exact opposite of what happened. Scale to 1 and let the line sit on
  // the floor, where it belongs.
  const scale = ceiling > 0 ? ceiling : 1;

  const x = (confidence: number) => PADDING.left + confidence * PLOT_WIDTH;
  const y = (cost: number) => PADDING.top + PLOT_HEIGHT - (cost / scale) * PLOT_HEIGHT;

  const points = (series: Series) =>
    sweep.map((point) => `${x(point.min_confidence)},${y(series.value(point))}`).join(" ");

  const flat = sweep.every((point) => point.total_cost === first.total_cost);
  const labelPositions = spread(SERIES.map((series) => y(series.value(last)) + 4));

  return (
    <figure className="sweep">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={
          `Total cost against the confidence threshold, over ${sweep.length} thresholds` +
          (best ? `; cheapest at ${best.min_confidence.toFixed(2)}` : "")
        }
        className="sweep__plot"
      >
        {/* Grid and axes, recessive: they orient the eye and are not the content. */}
        {[0, 0.5, 1].map((fraction) => (
          <g key={fraction}>
            <line
              x1={PADDING.left}
              x2={PADDING.left + PLOT_WIDTH}
              y1={y(scale * fraction)}
              y2={y(scale * fraction)}
              className="sweep__grid"
            />
            <text x={PADDING.left - 8} y={y(scale * fraction) + 4} className="sweep__tick-y">
              {formatMoney(scale * fraction)}
            </text>
          </g>
        ))}

        {[0, 0.25, 0.5, 0.75, 1].map((confidence) => (
          <text
            key={confidence}
            x={x(confidence)}
            y={HEIGHT - 14}
            className="sweep__tick-x"
          >
            {confidence.toFixed(2)}
          </text>
        ))}
        <text x={PADDING.left + PLOT_WIDTH / 2} y={HEIGHT - 1} className="sweep__axis-title">
          minimum confidence required to approve
        </text>

        {/* The chosen operating point, drawn under the lines so it never hides one. */}
        {best ? (
          <g>
            <line
              x1={x(best.min_confidence)}
              x2={x(best.min_confidence)}
              y1={PADDING.top}
              y2={PADDING.top + PLOT_HEIGHT}
              className="sweep__best-rule"
            />
            <circle
              cx={x(best.min_confidence)}
              cy={y(best.total_cost)}
              r={5}
              className="sweep__best-dot"
            />
            <text
              x={x(best.min_confidence)}
              y={PADDING.top - 4}
              className="sweep__best-label"
            >
              cheapest at {best.min_confidence.toFixed(2)}
            </text>
          </g>
        ) : null}

        {SERIES.map((series, index) => {
          const end = y(series.value(last));
          // `spread` returns one position per series, so the fallback is unreachable; it is
          // here because `noUncheckedIndexedAccess` is on and falling back to the line's own
          // end is the only answer that cannot silently draw a label at the top of the plot.
          const labelY = labelPositions[index] ?? end + 4;
          return (
            <g key={series.label}>
              <polyline
                points={points(series)}
                fill="none"
                stroke={series.stroke}
                strokeWidth={series.width}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
              {/* Direct label at the right-hand end, so identity survives without colour. A
                  short leader in the series' own colour keeps the pairing legible after
                  `spread` has moved a label off its line. */}
              <line
                x1={PADDING.left + PLOT_WIDTH}
                x2={PADDING.left + PLOT_WIDTH + 6}
                y1={end}
                y2={labelY - 4}
                stroke={series.stroke}
                strokeWidth={1}
              />
              <text
                x={PADDING.left + PLOT_WIDTH + 8}
                y={labelY}
                className="sweep__series-label"
              >
                {series.label}
              </text>
            </g>
          );
        })}
      </svg>

      <figcaption className="hint">
        {flat ? (
          <>
            The curve is flat: every threshold costs the same on this run, so the sweep cannot
            recommend one. The point marked is the <em>highest</em> threshold among the tied —
            two thresholds costing the same are not equally good, and the stricter one approves
            less on the same evidence.
          </>
        ) : (
          <>
            Total cost is the sum of the two below it. Raising the bar converts wrongful
            approvals into wrongful escalations; the minimum is where that trade stops paying.
            The threshold this system should ship with is that minimum — which is a claim you
            can check against the table.
          </>
        )}
      </figcaption>

      <details className="sweep__table">
        <summary>Every point, as numbers</summary>
        <table className="table">
          <thead>
            <tr>
              <th>Threshold</th>
              <th>Auto-approved</th>
              <th>Wrongly approved</th>
              <th>Wrongly escalated</th>
              <th>Cost of each</th>
              <th>Total</th>
            </tr>
          </thead>
          <tbody>
            {sweep.map((point) => (
              <tr
                key={point.min_confidence}
                className={point === best ? "row--best" : undefined}
              >
                <td className="mono">{point.min_confidence.toFixed(2)}</td>
                <td>{formatRate(point.auto_approval_rate)}</td>
                <td>{point.wrongly_approved}</td>
                <td>{point.wrongly_escalated}</td>
                <td className="small muted">
                  {formatMoney(point.wrongly_approved_cost)} +{" "}
                  {formatMoney(point.wrongly_escalated_cost)}
                </td>
                <td>{formatMoney(point.total_cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
