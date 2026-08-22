/**
 * Every gateway route this console calls, one function each.
 *
 * Kept separate from `gateway.ts` so that module stays about transport and this one
 * stays about the API surface: the list below is the honest answer to "what can the
 * console do", and it is deliberately short.
 *
 * It grew a case-submission call and the eval routes on 2026-08-22. Plan 07 said intake was
 * not a reviewer's job and left it out; the design's own request-path diagram has
 * `web ──/api/cases──► gateway` and `web ──/eval-runs──► gateway`, so leaving them out made
 * the console a strict subset of the thing that was designed. Both are role-gated at the
 * gateway and mirrored in `lib/session.ts`, so the console offers only what a given account
 * can actually use.
 */

import { readEventStream, request } from "@/lib/gateway";
import type {
  CaseCriteria,
  CaseEvent,
  CaseSubmission,
  Case,
  EvalRun,
  EvalRunReport,
  EvalRunStart,
  GoldenCase,
  GoldenCaseSubmission,
  QueuedCase,
  Review,
  ReviewSubmission,
  RunComparison,
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

/**
 * Submit a prior-authorization request. Answers 202 with the case id before the worker has
 * touched it, so the caller's next move is to watch the case rather than to read a result.
 *
 * `idempotency_key` is not optional here even though the route allows it to be. The one
 * client this function has is a form with a button a person can press twice, and the second
 * press would otherwise buy a second adjudication of the same request -- another few model
 * calls, and a duplicate on someone's queue.
 */
export function createCase(
  token: string,
  submission: CaseSubmission,
): Promise<{ case_id: string }> {
  return request("/api/cases", { method: "POST", token, body: submission });
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

/* --- evals -------------------------------------------------------------------------
 *
 * Operator-only at the gateway (`SATISFIES["operator"]` -- operator and admin). The console
 * mirrors that in `lib/session.ts::mayRunEvals` and hides the screen rather than offering a
 * control it knows would be refused; the gateway is still the enforcement point.
 */

export function listGoldenCases(token: string, signal?: AbortSignal): Promise<GoldenCase[]> {
  return request<GoldenCase[]>("/api/golden-cases", { token, signal });
}

/**
 * Author a golden case. The label is the person's, not the model's -- `author` is required
 * by the service and rejected if empty, because a label a model wrote measures agreement
 * between two models rather than correctness (ADR-0008).
 */
export function createGoldenCase(
  token: string,
  submission: GoldenCaseSubmission,
): Promise<GoldenCase> {
  return request<GoldenCase>("/api/golden-cases", {
    method: "POST",
    token,
    body: submission,
  });
}

export function listEvalRuns(token: string, signal?: AbortSignal): Promise<EvalRun[]> {
  return request<EvalRun[]>("/api/eval-runs", { token, signal });
}

/**
 * Start a run. Answers 202 with the id before any case has been submitted -- a full run is
 * tens of minutes of paced model calls, so the result is read back from `getEvalRun`.
 *
 * Rate-limited to two an hour at the gateway, deliberately: a run costs real model tokens.
 * A 429 arrives here as a `GatewayError` carrying the gateway's own sentence.
 */
export function startEvalRun(
  token: string,
  start: EvalRunStart,
): Promise<{ run_id: number; status: string }> {
  return request("/api/eval-runs", { method: "POST", token, body: start });
}

/**
 * Two runs side by side. `against` is required by the route and has no default: picking a
 * twin by heuristic is how a comparison ends up being made between runs that differ in three
 * things, so naming both is what makes the pair a deliberate claim.
 */
export function compareEvalRuns(
  token: string,
  runId: number,
  againstId: number,
  signal?: AbortSignal,
): Promise<RunComparison> {
  return request<RunComparison>(
    `/api/eval-runs/${runId}/comparison?against=${againstId}`,
    { token, signal },
  );
}

export function getEvalRun(
  token: string,
  runId: number,
  signal?: AbortSignal,
): Promise<EvalRunReport> {
  return request<EvalRunReport>(`/api/eval-runs/${runId}`, { token, signal });
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
