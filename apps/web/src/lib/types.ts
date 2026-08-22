/**
 * The wire shapes this console reads, mirroring what `adjudication` actually returns
 * (`routers/cases.py`, `routers/events.py`) and what `auth` returns on login.
 *
 * These are hand-written rather than generated. The generator would be a build step and
 * a dependency, and this surface is eleven shapes that change when a route changes --
 * at which point a hand-written type fails to compile in the component that renders it,
 * which is the notification you want anyway.
 */

/** `auth`'s closed role vocabulary (services/auth/models/user.py). */
export type Role = "clinician" | "reviewer" | "operator" | "admin";

export interface User {
  id: string;
  email: string;
  role: Role;
}

export interface Session {
  token: string;
  expiresAt: string;
  user: User;
}

/** Pipeline progress only. The outcome lives on the determination and never here. */
export type CaseStatus = "queued" | "running" | "decided" | "failed";

/**
 * The two outcomes the machine can produce (`pramana_common.criteria.Outcome`).
 *
 * Wire fields below are typed `string`, not this union, deliberately. Narrowing at the
 * boundary would let an unrecognised value be coerced into one of these two by a
 * fallback somewhere downstream; instead the renderer tests membership explicitly and
 * refuses to label anything else. See `components/OutcomeBadge.tsx`.
 */
export type Outcome = "approve" | "escalate";

/** `pramana_common.criteria.GateReason` -- a closed set, each member actionable. */
export type GateReason =
  | "no_criteria"
  | "criterion_not_met"
  | "insufficient_evidence"
  | "low_confidence";

export type Verdict = "met" | "not_met" | "insufficient_evidence";

export type CriterionType = "threshold" | "enum" | "temporal" | "judgment";

export interface Determination {
  outcome: string;
  reason: string | null;
  /**
   * Criterion ids of the closest set's blockers, or -- for a short-circuited case -- a
   * single pseudo-reason such as `upstream_unavailable` that names the stage that
   * stopped (`services/pipeline.py::_short_circuit`). Both are strings on the wire, and
   * the detail screen tells them apart by whether the value resolves to a criterion.
   */
  blocking: string[];
  winning_set: number | null;
  decided_at: string;
}

/**
 * Which arithmetic decided a case (`cases.run_mode`, ADR-0021). `deterministic` is the
 * system as designed; `model_arithmetic` is the ablation, in which the model performed the
 * threshold and date comparisons SQL otherwise does.
 */
export type RunMode = "deterministic" | "model_arithmetic";

export interface Case {
  id: string;
  member_id: string;
  requested_code: string;
  icd10: string;
  date_of_service: string;
  kind: "initial" | "continuation";
  status: CaseStatus;
  created_at: string;
  request_text: string | null;
  /**
   * Typed `string`, like the other two vocabularies read back off the wire: a value this
   * console does not recognise must be shown, not coerced. `CaseSummary` treats anything
   * other than `deterministic` as an experimental determination, which is the safe
   * direction to be wrong in -- an unknown mode is certainly not the shipped one.
   */
  run_mode: string;
}

/** A row of `GET /api/cases` -- a case plus its determination, if it has one yet. */
export interface QueuedCase extends Case {
  determination: Determination | null;
}

/**
 * One criterion with the verdict recorded against it. `verdict`, `confidence`, `tool`
 * and `evidence` are null when verification never reached this criterion -- the row is
 * still returned so a reviewer sees the whole policy the case was judged against, not
 * an abridged one (`repositories/criteria.py::list_for_case_with_results`).
 */
export interface Criterion {
  id: string;
  set_ordinal: number;
  ordinal: number;
  text: string;
  type: CriterionType;
  params: Record<string, unknown>;
  source_chunk_id: number;
  source_display_id: string;
  verdict: Verdict | null;
  confidence: number | null;
  tool: string | null;
  evidence: Record<string, unknown> | null;
}

/** Criteria grouped into the alternative sets the policy decomposed into (ADR-0011). */
export interface CriteriaSet {
  set_ordinal: number;
  criteria: Criterion[];
}

export interface CaseCriteria {
  case_id: string;
  sets: CriteriaSet[];
}

/**
 * One row of the append-only audit log. `type` is the stage vocabulary fixed in the
 * task-7 brief: started, eligibility, policy, criteria, criterion, decision.
 */
export interface CaseEvent {
  id: number;
  case_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/**
 * What a clinician may record, mirroring `reviews_outcome_check`
 * (adjudication/migrations/0004_reviews_outcome_vocabulary.sql) and
 * `routers/cases.py::ReviewOutcome`. Three values, and a different three from `Outcome`
 * above: the machine has two outcomes and a clinician has three, the third being the denial
 * the machine may never issue (ADR-0002, ADR-0019).
 */
export type ReviewOutcome = "approve" | "deny" | "pend";

export interface Review {
  id: number;
  /**
   * Typed `string`, not `ReviewOutcome`, for the same reason `Determination.outcome` is:
   * this is a value read back from the database, and narrowing at the boundary would let an
   * unrecognised one be coerced into a plausible label. `reviewOutcomeLabel` tests
   * membership and shows anything else verbatim.
   */
  outcome: string;
  clinician_id: string;
  rationale: string;
  agreed_with_system: boolean;
  created_at: string;
}

export interface ReviewSubmission {
  /** Narrowed on the way *out*: the console must not offer a value the CHECK would reject. */
  outcome: ReviewOutcome;
  rationale: string;
  agreed_with_system: boolean;
}
