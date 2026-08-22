"use client";

/**
 * One run's measured result.
 *
 * A run started from this console lands here immediately, before any case has been submitted,
 * so the first thing this screen usually shows is a report of nothing — which is correct and
 * is labelled as such rather than hidden behind a spinner. A run takes tens of minutes of
 * deliberately paced model calls; pretending otherwise would leave an operator watching a
 * blank page and wondering whether it started.
 */

import Link from "next/link";
import { useCallback, useState } from "react";
import { useParams } from "next/navigation";

import { OperatorOnly } from "@/components/OperatorOnly";
import { RunComparisonView } from "@/components/RunComparisonView";
import { RunReport } from "@/components/RunReport";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import type { EvalRun, EvalRunReport, RunComparison } from "@/lib/types";

export default function EvalRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = Number(params.runId);

  const load = useCallback(
    (token: string, signal: AbortSignal) => api.getEvalRun(token, runId, signal),
    [runId],
  );
  const report = useResource<EvalRunReport>(load);

  // The other runs, for the comparison picker. Nothing is preselected and nothing is
  // guessed: choosing a twin by heuristic is exactly how a comparison ends up being made
  // between two runs that differ in three things (ADR-0021), so the pair is the operator's
  // deliberate claim and the service checks it.
  const [against, setAgainst] = useState<number | null>(null);
  const loadRuns = useCallback(
    (token: string, signal: AbortSignal) => api.listEvalRuns(token, signal),
    [],
  );
  const runs = useResource<EvalRun[]>(loadRuns);

  const loadComparison = useCallback(
    (token: string, signal: AbortSignal) =>
      against === null
        ? Promise.resolve(null)
        : api.compareEvalRuns(token, runId, against, signal),
    [runId, against],
  );
  const comparison = useResource<RunComparison | null>(loadComparison);

  const others = (runs.data ?? []).filter((run) => run.id !== runId);

  return (
    <OperatorOnly>
      <div className="stack">
        <div className="row">
          <Link href="/evals" className="small">
            &larr; Back to evaluation
          </Link>
          <div className="shell__spacer" />
          <button type="button" className="secondary" onClick={report.reload}>
            Reload
          </button>
        </div>

        {report.error ? <p className="error">{report.error}</p> : null}

        {report.data === null ? (
          <p className="notice">Loading run&hellip;</p>
        ) : (
          <RunReport report={report.data} />
        )}

        <section className="card stack stack--tight">
          <h2>Compare against another run</h2>
          <p className="small muted">
            The comparison the ablation exists for. It is produced only when the two runs
            differ in their ablation and in nothing else — otherwise both runs&rsquo; figures
            are shown and the difference between them is withheld, because a delta across
            several simultaneous changes is not attributable to any of them.
          </p>

          <div className="field">
            <label htmlFor="against">Other run</label>
            <select
              id="against"
              value={against ?? ""}
              onChange={(event) =>
                setAgainst(event.target.value === "" ? null : Number(event.target.value))
              }
            >
              <option value="">Choose a run&hellip;</option>
              {others.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.id} — {run.ablation} — {run.model} @ {run.git_sha}
                </option>
              ))}
            </select>
          </div>

          {comparison.error ? <p className="error">{comparison.error}</p> : null}
          {against !== null && comparison.data === null && !comparison.error ? (
            <p className="notice">Comparing&hellip;</p>
          ) : null}
          {comparison.data ? <RunComparisonView comparison={comparison.data} /> : null}
        </section>
      </div>
    </OperatorOnly>
  );
}
