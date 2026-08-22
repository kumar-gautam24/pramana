"use client";

/**
 * The reviewer's queue.
 *
 * Defaults to `outcome=escalate`: the cases the gate declined to approve and referred to
 * a person. That is the work. The other two filters exist so a reviewer can check what
 * the system approved without being asked to -- an auto-approval nobody can audit is
 * worth less than one they can.
 */

import { useCallback, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { CaseQueueTable } from "@/components/CaseQueueTable";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import type { QueuedCase } from "@/lib/types";

const FILTERS = [
  { key: "escalate", label: "Referred to a clinician", outcome: "escalate" },
  { key: "approve", label: "Approved automatically", outcome: "approve" },
  { key: "all", label: "All cases", outcome: undefined },
] as const;

type FilterKey = (typeof FILTERS)[number]["key"];

export default function CasesPage() {
  const [filter, setFilter] = useState<FilterKey>("escalate");
  const outcome = FILTERS.find((option) => option.key === filter)?.outcome;

  const load = useCallback(
    (token: string, signal: AbortSignal) =>
      api.listCases(token, { outcome, limit: 100 }, signal),
    [outcome],
  );
  const { data, error, loading } = useResource<QueuedCase[]>(load);

  return (
    <AppShell>
      <div className="stack">
        <div className="row">
          <h1>Case queue</h1>
          <div className="shell__spacer" />
          <div className="row">
            {FILTERS.map((option) => (
              <button
                key={option.key}
                type="button"
                className={option.key === filter ? "" : "secondary"}
                onClick={() => setFilter(option.key)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        {error ? <p className="error">{error}</p> : null}

        <div className="card">
          {loading && data === null ? (
            <p className="notice">Loading cases&hellip;</p>
          ) : (
            <CaseQueueTable cases={data ?? []} />
          )}
        </div>
      </div>
    </AppShell>
  );
}
