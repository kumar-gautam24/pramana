"use client";

/**
 * The eval harness: the golden set, and the runs measured against it.
 *
 * Operator-only, mirroring the gateway's `SATISFIES["operator"]` on every `/api/golden-cases`
 * and `/api/eval-runs` route. This is the surface California's periodic-accuracy-assessment
 * requirement is answered from, which is also why it is a screen rather than a script: an
 * assessment nobody can re-run is not an assessment.
 *
 * The golden set is shown first, and the count is shown against its target rather than on its
 * own. Three cases can prove the harness works end to end; they cannot support a claim about
 * accuracy, and a report rendered from them looks exactly as authoritative as one rendered
 * from sixty. Saying so here is cheaper than trusting a reader to notice.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { EvalRunTable } from "@/components/EvalRunTable";
import { GoldenCaseForm, GoldenCaseTable } from "@/components/GoldenCases";
import { OperatorOnly } from "@/components/OperatorOnly";
import { StartRunForm } from "@/components/StartRunForm";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import type { EvalRun, GoldenCase } from "@/lib/types";

/**
 * The size the design asks the golden set to reach, with at least 8 near-miss cases
 * (ADR-0009). Rendered as a denominator so the set's size is always read as a fraction of
 * what it needs to be.
 */
const GOLDEN_SET_TARGET = 60;

export default function EvalsPage() {
  const router = useRouter();

  const loadRuns = useCallback(
    (token: string, signal: AbortSignal) => api.listEvalRuns(token, signal),
    [],
  );
  const loadGolden = useCallback(
    (token: string, signal: AbortSignal) => api.listGoldenCases(token, signal),
    [],
  );

  const runs = useResource<EvalRun[]>(loadRuns);
  const golden = useResource<GoldenCase[]>(loadGolden);

  const goldenCount = golden.data?.length ?? 0;

  return (
    <OperatorOnly>
      <div className="stack">
        <h1>Evaluation</h1>

        <section className="card stack stack--tight">
          <div className="row">
            <h2>Golden set</h2>
            <div className="shell__spacer" />
            <span className="badge badge--neutral">
              {goldenCount} of {GOLDEN_SET_TARGET}
            </span>
          </div>
          {goldenCount < GOLDEN_SET_TARGET ? (
            <p className="notice">
              Below the target set size. A run over this set proves the harness works; it does
              not support a claim about how accurate the system is, and a report rendered from
              it looks exactly as authoritative as one rendered from sixty.
            </p>
          ) : null}
          {golden.error ? <p className="error">{golden.error}</p> : null}
          {golden.data === null ? (
            <p className="notice">Loading golden cases&hellip;</p>
          ) : (
            <GoldenCaseTable cases={golden.data} />
          )}
        </section>

        <GoldenCaseForm onCreated={golden.reload} />

        <StartRunForm onStarted={(runId) => router.push(`/evals/runs/${runId}`)} />

        <section className="card stack stack--tight">
          <div className="row">
            <h2>Runs</h2>
            <div className="shell__spacer" />
            <button type="button" className="secondary" onClick={runs.reload}>
              Refresh
            </button>
          </div>
          {runs.error ? <p className="error">{runs.error}</p> : null}
          {runs.data === null ? (
            <p className="notice">Loading runs&hellip;</p>
          ) : (
            <EvalRunTable runs={runs.data} />
          )}
        </section>

        <p className="hint">
          A run scores every golden case that has no outcome recorded against it yet, so
          starting one again resumes rather than repeats — see{" "}
          <Link href="/cases">the queue</Link> for the cases a run submits.
        </p>
      </div>
    </OperatorOnly>
  );
}
