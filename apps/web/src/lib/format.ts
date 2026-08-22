/** Presentation helpers. Nothing here decides anything; it only chooses words. */

import type { GateReason, ReviewOutcome, Verdict } from "@/lib/types";

export function formatDate(iso: string): string {
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? iso : value.toLocaleDateString();
}

export function formatDateTime(iso: string): string {
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? iso : value.toLocaleString();
}

export function formatTime(iso: string): string {
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? iso : value.toLocaleTimeString();
}

export function formatConfidence(confidence: number | null): string {
  return confidence === null ? "--" : `${Math.round(confidence * 100)}%`;
}

/**
 * Money, with its currency symbol and cents.
 *
 * Every figure the eval harness produces is a count multiplied by a rate, and it is rendered
 * that way on the screen -- this only formats the product. Cents are kept even though the
 * rates are round numbers: a rate of $180/h over 12 minutes is $36.00 exactly, and rounding
 * to whole dollars somewhere else in the chain is how a figure stops reconciling with the
 * arithmetic printed beside it.
 */
const MONEY = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });

export function formatMoney(amount: number): string {
  return MONEY.format(amount);
}

/** A rate as a fraction, for auto-approval and for precision/recall. */
export function formatRate(value: number | null): string {
  // Null is "nobody measured this", which is a different statement from 0% and must not be
  // rendered as one -- the eval report returns null for extraction scores when no case in
  // the run carried a human-authored criteria list.
  return value === null ? "not measured" : `${(value * 100).toFixed(1)}%`;
}

const VERDICT_LABELS: Record<Verdict, string> = {
  met: "Met",
  not_met: "Not met",
  // Never "unknown" or "error". This verdict is the system working: it is what sends a
  // case to a human instead of guessing, and the wording has to say so.
  insufficient_evidence: "Insufficient evidence",
};

export function verdictLabel(verdict: Verdict | null): string {
  return verdict === null ? "Not verified" : VERDICT_LABELS[verdict];
}

/**
 * The gate's reason, in words a reviewer can act on. Each of the four says what to do
 * next: read the conflicting record, go find the missing document, or re-read what the
 * system was unsure about.
 *
 * None of them is a denial, and none may be phrased as one. A reason this map does not
 * know is shown verbatim rather than being given a sentence nobody wrote.
 */
const REASON_LABELS: Record<GateReason, string> = {
  no_criteria: "No criteria could be established from the governing policy",
  criterion_not_met: "The member's record contradicts a criterion",
  insufficient_evidence: "The record does not say enough to decide",
  low_confidence: "The evidence was read with too little confidence to rely on",
};

export function reasonLabel(reason: string | null): string {
  if (reason === null) return "";
  return REASON_LABELS[reason as GateReason] ?? reason;
}

/**
 * Short-circuit markers the pipeline writes into `determination.blocking` when it never
 * reached the criteria -- `services/pipeline.py::_short_circuit`. They are the only
 * `blocking` entries that do not name a criterion, so the detail screen needs them to
 * explain a case that has no criteria to show.
 */
const SHORT_CIRCUIT_LABELS: Record<string, string> = {
  not_eligible: "Coverage was not active for the member on the date of service",
  no_governing_policy: "No coverage determination in force governs this request",
  no_criteria: "The governing policy could not be decomposed into checkable criteria",
  upstream_unavailable: "A service this decision depends on could not be reached",
};

export function shortCircuitLabel(marker: string): string | null {
  return SHORT_CIRCUIT_LABELS[marker] ?? null;
}

/**
 * A clinician's recorded determination, in words. The vocabulary is closed
 * (`reviews_outcome_check`, ADR-0019), so an unrecognised value means the schema moved and
 * this map did not -- shown verbatim rather than given a sentence nobody wrote, the same rule
 * `OutcomeBadge` follows and for the same reason: this column records who issued an adverse
 * determination, and mislabelling one is a legal problem rather than a cosmetic one.
 */
const REVIEW_OUTCOME_LABELS: Record<ReviewOutcome, string> = {
  approve: "Approved",
  deny: "Denied",
  pend: "Pended for more information",
};

export function reviewOutcomeLabel(outcome: string): string {
  return REVIEW_OUTCOME_LABELS[outcome as ReviewOutcome] ?? outcome;
}

const STAGE_LABELS: Record<string, string> = {
  started: "Case picked up",
  eligibility: "Eligibility checked",
  policy: "Governing policy retrieved",
  criteria: "Policy decomposed into criteria",
  criterion: "Criterion verified",
  // Not a pipeline stage: the worker waiting out a transient upstream failure before
  // running the case again (ADR-0020). Named as what it is rather than hidden, because the
  // reason the retry is in the worker at all is that only the worker can put it in this log
  // -- a reviewer looking at a case that took two minutes is owed the reason.
  retry: "Upstream unavailable, retrying",
  upstream_exhausted: "Gave up waiting on an upstream",
  decision: "Determination recorded",
};

export function stageLabel(type: string): string {
  return STAGE_LABELS[type] ?? type;
}
