/** Presentation helpers. Nothing here decides anything; it only chooses words. */

import type { GateReason, Verdict } from "@/lib/types";

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

const STAGE_LABELS: Record<string, string> = {
  started: "Case picked up",
  eligibility: "Eligibility checked",
  policy: "Governing policy retrieved",
  criteria: "Policy decomposed into criteria",
  criterion: "Criterion verified",
  decision: "Determination recorded",
};

export function stageLabel(type: string): string {
  return STAGE_LABELS[type] ?? type;
}
