"use client";

/**
 * Starting an eval run.
 *
 * Three inputs, each of which changes what the resulting number means, so each says so.
 *
 * The 501 branch is the reason this component has its own error handling rather than
 * reusing the generic one. `evals` answers 501 for an ablation it has no way to actually
 * perform, and that refusal is the system behaving well — it declines to publish a figure
 * labelled with an experiment it did not run. Rendered as an error it would look like a
 * malfunction; rendered as a zero it would be a lie. So it gets its own, calmer treatment:
 * not built yet.
 */

import { useState } from "react";

import { useSession } from "@/components/SessionProvider";
import * as api from "@/lib/api";
import { GatewayError } from "@/lib/gateway";
import type { Ablation } from "@/lib/types";

const ABLATIONS: { value: Ablation; label: string; note: string }[] = [
  {
    value: "none",
    label: "None — the system as shipped",
    note: "Threshold, category and date comparisons are performed in code.",
  },
  {
    value: "model_arithmetic",
    label: "Model arithmetic",
    note:
      "The same pipeline with the model performing those comparisons instead. This is the " +
      "experiment the design's central claim is argued from, and it costs one model call " +
      "per comparison.",
  },
];

export function StartRunForm({ onStarted }: { onStarted: (runId: number) => void }) {
  const { session } = useSession();

  const [minConfidence, setMinConfidence] = useState("0");
  const [ablation, setAblation] = useState<Ablation>("none");
  const [limit, setLimit] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notBuilt, setNotBuilt] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;

    setError(null);
    setNotBuilt(null);
    setSubmitting(true);
    try {
      const { run_id } = await api.startEvalRun(session.token, {
        min_confidence: Number(minConfidence),
        ablation,
        limit: limit.trim() === "" ? null : Number(limit),
      });
      onStarted(run_id);
    } catch (cause) {
      if (cause instanceof GatewayError && cause.isNotImplemented) {
        // Not an error state and not a zero: the harness is telling us it will not report a
        // measurement it cannot take. Carry the server's own sentence, which names what is
        // missing.
        setNotBuilt(cause.detail);
      } else {
        setError(
          cause instanceof GatewayError ? cause.detail : "The run could not be started.",
        );
      }
    } finally {
      setSubmitting(false);
    }
  }

  const selected = ABLATIONS.find((option) => option.value === ablation);

  return (
    <form className="card stack stack--tight" onSubmit={onSubmit}>
      <h2>Start a run</h2>

      <div className="fields">
        <div className="field">
          <label htmlFor="min-confidence">Confidence threshold</label>
          <input
            id="min-confidence"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={minConfidence}
            onChange={(event) => setMinConfidence(event.target.value)}
          />
          <p className="hint">
            Recorded on the run, so it can be reproduced from its own row. The report sweeps
            every threshold regardless — scoring a run at many thresholds costs nothing,
            because it re-scores rather than re-adjudicating.
          </p>
        </div>

        <div className="field">
          <label htmlFor="limit">Cap on cases</label>
          <input
            id="limit"
            type="number"
            min={1}
            placeholder="all"
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
          />
          <p className="hint">
            A full run is tens of minutes of deliberately paced model calls. A capped run
            proves the harness works without that wait, and is recorded as capped so it cannot
            be mistaken for a complete one.
          </p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="ablation">Ablation</label>
        <select
          id="ablation"
          value={ablation}
          onChange={(event) => setAblation(event.target.value as Ablation)}
        >
          {ABLATIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <p className="hint">{selected?.note}</p>
      </div>

      {notBuilt ? (
        <p className="notice">
          <strong>Not built yet.</strong> {notBuilt}
        </p>
      ) : null}
      {error ? <p className="error">{error}</p> : null}

      <div className="row">
        <button type="submit" disabled={submitting}>
          {submitting ? "Starting…" : "Start run"}
        </button>
        <span className="hint">
          Two runs an hour, rate-limited at the gateway: a run spends real model tokens.
        </span>
      </div>
    </form>
  );
}
