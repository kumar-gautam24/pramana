/**
 * Where the session token lives in the browser.
 *
 * `sessionStorage`, not `localStorage`: the token dies with the tab, which is the right
 * default for a console used on a shared clinical workstation. It is not
 * `document.cookie` either -- see `gateway.ts` on why this console sends a bearer header.
 *
 * This is not a defence against script injection; nothing readable by the page is. The
 * defences that matter are elsewhere and are real: the gateway validates every request
 * against `auth`, sessions expire, and `auth` stores only a SHA-256 of the token, so the
 * database cannot hand anyone a usable one.
 */

import type { Session } from "@/lib/types";

const KEY = "pramana.session";

/** Storage may be unavailable (server render, private-mode quirks). A console that
 * throws on a missing storage API is worse than one that treats it as signed out. */
function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

export function loadSession(): Session | null {
  const store = storage();
  if (!store) return null;

  const raw = store.getItem(KEY);
  if (!raw) return null;

  let session: Session;
  try {
    session = JSON.parse(raw) as Session;
  } catch {
    store.removeItem(KEY);
    return null;
  }

  // A token past its expiry is already worthless to the gateway; dropping it here means
  // the reviewer lands on the login screen rather than on a page that 401s as it loads.
  if (!session.token || Date.parse(session.expiresAt) <= Date.now()) {
    store.removeItem(KEY);
    return null;
  }
  return session;
}

export function saveSession(session: Session): void {
  storage()?.setItem(KEY, JSON.stringify(session));
}

export function clearSession(): void {
  storage()?.removeItem(KEY);
}

/**
 * Whether a role may record a review, mirroring the gateway's own `SATISFIES` table
 * (`services/gateway/routes.py`): clinician and admin, and specifically not reviewer.
 *
 * That is a legal distinction rather than a hierarchy -- Illinois permits only a
 * clinical peer to issue an adverse determination. The gateway is the enforcement point;
 * this exists only so the console does not render a form whose submission it knows will
 * be refused.
 */
export function mayReview(role: Session["user"]["role"]): boolean {
  return role === "clinician" || role === "admin";
}

/**
 * Whether a role may reach the eval harness, mirroring the gateway's `SATISFIES["operator"]`
 * for every `/api/golden-cases` and `/api/eval-runs` route: operator and admin.
 *
 * Not a clinician, and that is the same kind of distinction as `mayReview` rather than a
 * seniority ordering. Starting a run spends model tokens and publishes a figure about the
 * system's own accuracy; recording a determination on a member's case is a licensed clinical
 * act. Neither role should be able to do the other's job by accident.
 */
export function mayRunEvals(role: Session["user"]["role"]): boolean {
  return role === "operator" || role === "admin";
}
