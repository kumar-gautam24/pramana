"use client";

/**
 * Load one thing from the gateway with the signed-in reviewer's token.
 *
 * Four screens' worth of fetching had the same four concerns -- pass the token, abort on
 * unmount, turn a `GatewayError` into a sentence, and end the session when the gateway
 * says the token is no longer good. This exists because those four repeated, not to be
 * a data layer: there is no cache, no deduplication and no revalidation, because nothing
 * here needs them.
 *
 * `load` must be stable across renders (wrap it in `useCallback`); it is the dependency
 * that decides when a refetch happens.
 */

import { useCallback, useEffect, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { GatewayError } from "@/lib/gateway";

export interface Resource<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  /** Refetch on demand -- used after a review is recorded. */
  reload: () => void;
}

export function useResource<T>(
  load: (token: string, signal: AbortSignal) => Promise<T>,
): Resource<T> {
  const { session, signOut } = useSession();
  const token = session?.token ?? null;

  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!token) return;

    const controller = new AbortController();
    setLoading(true);

    load(token, controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return;
        setData(value);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        // An expired or revoked token has to end the session rather than render as an
        // error over a page the reviewer can no longer load.
        if (cause instanceof GatewayError && cause.isUnauthenticated) {
          void signOut();
          return;
        }
        setError(cause instanceof GatewayError ? cause.detail : String(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [token, load, attempt, signOut]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  return { data, error, loading, reload };
}
