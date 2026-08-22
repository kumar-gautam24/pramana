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

/**
 * What `POST /api/cases` takes. Deliberately not `Partial<Case>`: a case as read back
 * carries an id, a status and a created_at that no submitter provides, and typing the
 * request as a loosened version of the response is how a client ends up sending fields the
 * server will ignore.
 *
 * `run_mode` is absent on purpose. It exists (ADR-0021) and the gateway would forward it,
 * but it is an operator's experiment and this console never submits one -- a control the
 * console does not offer is a control it cannot offer by accident.
 */
export interface CaseSubmission {
  member_id: string;
  requested_code: string;
  icd10: string;
  /** ISO `YYYY-MM-DD`, which is what `<input type="date">` produces. */
  date_of_service: string;
  kind: Case["kind"];
  request_text: string | null;
  idempotency_key: string;
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

/* --- evals -------------------------------------------------------------------------
 *
 * The operator-only surface. `evals` is the service that answers "is this system accurate
 * enough to be allowed to decide anything", which California requires be assessed
 * periodically -- so these shapes are the ones a regulator's question is answered in.
 */

/** A human-authored label (ADR-0008). `fixture` is forwarded verbatim to `POST /cases`. */
export interface GoldenCase {
  id: number;
  fixture: Record<string, unknown>;
  expected_outcome: Outcome;
  expected_criteria: string[];
  author: string;
  notes: string | null;
  created_at: string;
}

export interface GoldenCaseSubmission {
  fixture: Record<string, unknown>;
  expected_outcome: Outcome;
  expected_criteria: string[];
  author: string;
  notes: string | null;
}

/** `evals.models.run.Ablation`. `model_arithmetic` is the ADR-0021 run mode. */
export type Ablation = "none" | "model_arithmetic";

export interface EvalRun {
  id: number;
  model: string;
  prompt_version: string;
  git_sha: string;
  ablation: string;
  status: "running" | "complete" | "failed";
  thresholds: Record<string, number>;
  started_at: string;
  finished_at: string | null;
}

export interface EvalRunStart {
  min_confidence: number;
  ablation: Ablation;
  /** Cap the cases run, for proving the harness without waiting for the whole set. */
  limit: number | null;
}

/**
 * One point on the threshold sweep. Every money field is a count multiplied by a rate from
 * `EvalRunReport.costs` -- never a bare score, which is why the rates travel with the report
 * (`evals/domain/scoring.py::CasePoint`).
 */
export interface CasePoint {
  min_confidence: number;
  auto_approval_rate: number;
  correct_approve: number;
  correct_escalate: number;
  wrongly_approved: number;
  wrongly_escalated: number;
  unfinished: number;
  wrongly_approved_cost: number;
  wrongly_escalated_cost: number;
  total_cost: number;
}

export interface EvalRunReport {
  run: EvalRun;
  /** The rates the money figures are multiples of. Published so a reader can check them. */
  costs: {
    average_claim_amount: number;
    review_minutes: number;
    clinician_hourly_rate: number;
    review_cost: number;
  };
  /**
   * How much of the run was really ablated. `condition_codes` criteria have no comparison
   * step to hand to a model, so even a `model_arithmetic` run reports fewer ablated criteria
   * than it has comparisons (ADR-0021).
   */
  ablation_coverage: {
    comparison_criteria: number;
    by_model_arithmetic: number;
  };
  cases_scored: number;
  case_level: {
    at_threshold_zero: CasePoint;
    /** The cheapest point on the sweep. Null only when the run scored nothing. */
    best: CasePoint | null;
    sweep: CasePoint[];
  };
  criterion_level: {
    cases_with_expected_criteria: number;
    /** Null when no case in the run carried an expected-criteria list. Never render 0. */
    mean_precision: number | null;
    mean_recall: number | null;
    mean_f1: number | null;
  };
  /** Cases the harness could not decide. Not errors of the system -- gaps in the measurement. */
  unfinished: { golden_case_id: number; error: string | null }[];
}

export interface ReviewSubmission {
  /** Narrowed on the way *out*: the console must not offer a value the CHECK would reject. */
  outcome: ReviewOutcome;
  rationale: string;
  agreed_with_system: boolean;
}
