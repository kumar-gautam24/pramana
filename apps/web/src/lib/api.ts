/**
 * Every gateway route this console calls, one function each.
 *
 * Kept separate from `gateway.ts` so that module stays about transport and this one
 * stays about the API surface: the list below is the honest answer to "what can the
 * console do", and it is deliberately short. There is no case-submission call -- intake
 * is not a reviewer's job, and the route exists for the eval harness and a payer's own
 * systems.
 */

import { readEventStream, request } from "@/lib/gateway";
import type {
  CaseCriteria,
  CaseEvent,
  Case,
  QueuedCase,
  Review,
  ReviewSubmission,
  Session,
  User,
} from "@/lib/types";

interface LoginResponse {
  token: string;
  expires_at: string;
  user: User;
}

export async function login(email: string, password: string): Promise<Session> {
  const body = await request<LoginResponse>("/api/auth/login", {
    method: "POST",
    body: { email, password },
  });
  return { token: body.token, expiresAt: body.expires_at, user: body.user };
}

/**
 * Logout failures are swallowed by the caller, not here: the token is destroyed locally
 * either way, and a reviewer who cannot reach the gateway must still be able to leave a
 * shared workstation signed out.
 */
export async function logout(token: string): Promise<void> {
  await request<{ status: string }>("/api/auth/logout", { method: "POST", token });
}

/**
 * The work queue. `outcome: "escalate"` is the reviewer's queue -- the cases the gate
 * declined to approve and handed to a human, which is the only kind of case this console
 * exists to show.
 */
export function listCases(
  token: string,
  params: { outcome?: string; status?: string; limit?: number },
  signal?: AbortSignal,
): Promise<QueuedCase[]> {
  const query = new URLSearchParams();
  if (params.outcome) query.set("outcome", params.outcome);
  if (params.status) query.set("status", params.status);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  return request<QueuedCase[]>(`/api/cases${suffix}`, { token, signal });
}

export function getCase(token: string, caseId: string, signal?: AbortSignal): Promise<Case> {
  return request<Case>(`/api/cases/${caseId}`, { token, signal });
}

export function getCaseCriteria(
  token: string,
  caseId: string,
  signal?: AbortSignal,
): Promise<CaseCriteria> {
  return request<CaseCriteria>(`/api/cases/${caseId}/criteria`, { token, signal });
}

/** The stored audit log. Replayed on every case, decided or not -- see `StepStream`. */
export function listCaseEvents(
  token: string,
  caseId: string,
  signal?: AbortSignal,
): Promise<CaseEvent[]> {
  return request<CaseEvent[]>(`/api/cases/${caseId}/events`, { token, signal });
}

/** The live view of the same log. `onEvent` fires once per appended event. */
export function streamCaseEvents(
  token: string,
  caseId: string,
  onEvent: (event: CaseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  return readEventStream(
    `/api/cases/${caseId}/stream`,
    token,
    (data) => onEvent(JSON.parse(data) as CaseEvent),
    signal,
  );
}

export function listCaseReviews(
  token: string,
  caseId: string,
  signal?: AbortSignal,
): Promise<Review[]> {
  return request<Review[]>(`/api/cases/${caseId}/reviews`, { token, signal });
}

/**
 * Record a clinician's decision. The clinician's identity is not in this payload and
 * must not be: `adjudication` takes it from the `X-Pramana-User-Id` header the gateway
 * writes after resolving the session, so a caller cannot attribute a decision to someone
 * else. This row is the record of who made an adverse determination.
 */
export function submitReview(
  token: string,
  caseId: string,
  review: ReviewSubmission,
): Promise<{ id: number; case_id: string; created_at: string }> {
  return request(`/api/cases/${caseId}/review`, { method: "POST", token, body: review });
}
