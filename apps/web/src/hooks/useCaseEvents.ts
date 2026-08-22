"use client";

/**
 * A case's audit trail, replayed and then followed live.
 *
 * Both sources are always used, and that is the design rather than a fallback. The stored
 * log is the record -- append-only, and what an auditor reads (ADR-0005) -- so it is
 * fetched for every case; a console that only followed the live stream would have nothing
 * to show for the ninety-nine cases in a hundred that were decided before anyone opened
 * them. The stream is subscribed to only while the case is still moving, because holding
 * an SSE connection open on a decided case occupies it for events that will never come.
 *
 * The two merge on `seq`. The database allocates it inside the INSERT under a UNIQUE
 * constraint and the Pub/Sub publish happens inside that same `append` call, so a
 * replayed event and a streamed one are the same identity and the two views cannot render
 * a different sequence -- see `repositories/case_events.py`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { useResource } from "@/hooks/useResource";
import * as api from "@/lib/api";
import type { CaseEvent } from "@/lib/types";

export interface CaseEvents {
  events: CaseEvent[];
  /** The stored log could not be read. The screen has no audit trail to show. */
  error: string | null;
  /** The live connection dropped. The stored log is still the record; this is weaker. */
  streamError: string | null;
}

export function useCaseEvents(caseId: string, live: boolean): CaseEvents {
  const { session } = useSession();
  const token = session?.token ?? null;

  const load = useCallback(
    (authToken: string, signal: AbortSignal) => api.listCaseEvents(authToken, caseId, signal),
    [caseId],
  );
  const { data: replayed, error } = useResource<CaseEvent[]>(load);

  const [streamed, setStreamed] = useState<CaseEvent[]>([]);
  const [streamError, setStreamError] = useState<string | null>(null);

  useEffect(() => {
    if (!live || !token) return;

    const controller = new AbortController();
    api
      .streamCaseEvents(
        token,
        caseId,
        (event) => setStreamed((current) => [...current, event]),
        controller.signal,
      )
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setStreamError(`Live updates stopped: ${String(cause)}`);
      });

    return () => controller.abort();
  }, [live, token, caseId]);

  const events = useMemo(() => {
    const bySeq = new Map<number, CaseEvent>();
    for (const event of [...(replayed ?? []), ...streamed]) bySeq.set(event.seq, event);
    return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  }, [replayed, streamed]);

  return { events, error, streamError };
}
