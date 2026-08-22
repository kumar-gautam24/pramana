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
import { useCallback } from "react";
import { useParams } from "next/navigation";

import { OperatorOnly } from "@/components/OperatorOnly";
import { RunReport } from "@/components/RunReport";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import type { EvalRunReport } from "@/lib/types";

export default function EvalRunPage() {
  const params = useParams<{ runId: string }>();
  const runId = Number(params.runId);

  const load = useCallback(
    (token: string, signal: AbortSignal) => api.getEvalRun(token, runId, signal),
    [runId],
  );
  const report = useResource<EvalRunReport>(load);

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
      </div>
    </OperatorOnly>
  );
}
