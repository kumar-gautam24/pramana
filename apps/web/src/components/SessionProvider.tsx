"use client";

/**
 * The signed-in session, held in React state and mirrored into `sessionStorage`.
 *
 * Every screen in this console renders on the client and fetches with the reviewer's own
 * token. The alternative -- server components fetching through the gateway -- would put
 * the credential on the Next.js server and make that server a second thing holding a
 * backend address, which is exactly the invariant `gateway.ts` exists to keep.
 *
 * `status` distinguishes "we have not read storage yet" from "there is no session".
 * Without it every guarded screen would flash the login redirect on first paint before
 * discovering the reviewer was signed in all along.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import * as api from "@/lib/api";
import { clearSession, loadSession, saveSession } from "@/lib/session";
import type { Session } from "@/lib/types";

interface SessionContextValue {
  status: "loading" | "signed-in" | "signed-out";
  session: Session | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  // Storage is only readable in the browser, so the first render is always "loading".
  useEffect(() => {
    setSession(loadSession());
    setReady(true);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const fresh = await api.login(email, password);
    saveSession(fresh);
    setSession(fresh);
  }, []);

  const signOut = useCallback(async () => {
    const token = session?.token;
    // Local state is cleared first and unconditionally. A reviewer leaving a shared
    // workstation must end up signed out here even if the gateway cannot be reached;
    // the server-side invalidation is the part that may fail.
    clearSession();
    setSession(null);
    if (token) {
      try {
        await api.logout(token);
      } catch {
        // The token expires on its own. Nothing here is worth blocking the sign-out on.
      }
    }
  }, [session]);

  const value = useMemo<SessionContextValue>(
    () => ({
      status: !ready ? "loading" : session ? "signed-in" : "signed-out",
      session,
      signIn,
      signOut,
    }),
    [ready, session, signIn, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside <SessionProvider>");
  return value;
}
