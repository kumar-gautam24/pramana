import Link from "next/link";

import { formatDateTime } from "@/lib/format";
import type { EvalRun } from "@/lib/types";

/**
 * Every run, newest first.
 *
 * The model, the prompt version and the commit are columns rather than detail-page fields
 * because they are what makes a run reproducible, and a list of runs whose conditions are
 * hidden invites comparing two numbers that were produced by different systems.
 *
 * `status` here is the *run's* status, not an outcome: `failed` means the harness stopped,
 * not that the system under test did badly. A run in which every case was wrong finishes
 * `complete`.
 */

const STATUS_CLASS: Record<EvalRun["status"], string> = {
  running: "badge badge--neutral",
  complete: "badge badge--approve",
  // Amber, not red. A failed run is a measurement that did not happen; nothing about the
  // system under test is being reported as bad.
  failed: "badge badge--escalate",
};

export function EvalRunTable({ runs }: { runs: EvalRun[] }) {
  if (runs.length === 0) {
    return <p className="notice">No runs yet.</p>;
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Run</th>
          <th>Status</th>
          <th>Model</th>
          <th>Prompt</th>
          <th>Commit</th>
          <th>Ablation</th>
          <th>Started</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => (
          <tr key={run.id}>
            <td>
              <Link href={`/evals/runs/${run.id}`} className="mono">
                {run.id}
              </Link>
            </td>
            <td>
              <span className={STATUS_CLASS[run.status] ?? "badge badge--neutral"}>
                {run.status}
              </span>
            </td>
            <td className="mono small">{run.model}</td>
            <td className="mono small">{run.prompt_version}</td>
            <td className="mono small">{run.git_sha}</td>
            <td className="small">
              {run.ablation === "none" ? (
                <span className="muted">none</span>
              ) : (
                // An ablated run's numbers describe an arrangement this system argues
                // against. Marked in the list so the two are never compared by accident.
                <span className="badge badge--negative">{run.ablation}</span>
              )}
            </td>
            <td className="small muted">{formatDateTime(run.started_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
