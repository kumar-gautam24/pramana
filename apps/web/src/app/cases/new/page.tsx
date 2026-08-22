"use client";

/**
 * Intake: submitting a prior-authorization request.
 *
 * Open to any session, unlike the review form and the eval screens. Submitting a request is
 * the ordinary use of this system — in production the submitter is a provider's billing
 * system speaking X12 278, and this form is the human equivalent. The gateway gates
 * `POST /api/cases` on `session` and nothing narrower, so this screen offers exactly what
 * that route allows.
 *
 * There is no free-text-to-code step. A submitter's billing system has already assigned the
 * procedure and diagnosis codes, and asking a model to derive them would put a model-produced
 * fact at the one point in the pipeline nothing downstream re-checks — see
 * [ADR-0018](../../../../../docs/decisions/0018-no-normalize-stage.md), which struck the
 * design's `normalize` stage for that reason. What the narrative field *is* for is retrieval,
 * and the form says so where the field is, with the measurement behind it.
 *
 * The case is submitted with an idempotency key this form mints, so pressing the button twice
 * cannot buy a second adjudication of the same request.
 */

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { AppShell } from "@/components/AppShell";
import { useSession } from "@/components/SessionProvider";
import * as api from "@/lib/api";
import { GatewayError } from "@/lib/gateway";
import type { Case } from "@/lib/types";

const KINDS: { value: Case["kind"]; label: string }[] = [
  { value: "initial", label: "Initial authorization" },
  { value: "continuation", label: "Continuation of therapy" },
];

/**
 * 128 bits of hex.
 *
 * Not `crypto.randomUUID`, which exists only in a secure context: this console is served
 * over plain HTTP against a local gateway during development, so on any host that is not
 * `localhost` that function is simply undefined. `crypto.getRandomValues` has no such
 * restriction and the key needs no structure — the server treats it as an opaque string
 * under a UNIQUE constraint.
 */
function randomKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export default function NewCasePage() {
  const router = useRouter();
  const { session } = useSession();

  const [memberId, setMemberId] = useState("");
  const [requestedCode, setRequestedCode] = useState("");
  const [icd10, setIcd10] = useState("");
  const [dateOfService, setDateOfService] = useState("");
  const [kind, setKind] = useState<Case["kind"]>("initial");
  const [requestText, setRequestText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  /**
   * The key that makes this submission retryable, and the form contents it belongs to.
   *
   * Minted at submit time rather than on mount, so nothing random happens during a render
   * that also runs on the server. Reused while the contents are unchanged — which is what
   * makes a double-click, or a retry after a dropped response, return the case the first
   * attempt created instead of adjudicating the same request twice. Reminted the moment any
   * field changes, because an edited submission is a *different* submission and must not be
   * answered with the previous one's case.
   */
  const submission = useRef<{ fields: string; key: string } | null>(null);

  function idempotencyKeyFor(fields: string): string {
    if (submission.current?.fields !== fields) {
      submission.current = { fields, key: randomKey() };
    }
    return submission.current.key;
  }

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;

    const narrative = requestText.trim();
    const request = {
      member_id: memberId.trim(),
      requested_code: requestedCode.trim(),
      icd10: icd10.trim(),
      date_of_service: dateOfService,
      kind,
      // Empty is sent as null, not as "": the column is nullable precisely so the pipeline
      // can tell "no narrative was supplied" from one, and an empty string would make a
      // blank field look like a narrative that happened to say nothing.
      request_text: narrative === "" ? null : narrative,
    };

    setError(null);
    setSubmitting(true);
    try {
      const { case_id } = await api.createCase(session.token, {
        ...request,
        idempotency_key: idempotencyKeyFor(JSON.stringify(request)),
      });
      // Straight to the case. It is `queued` at this point, so the detail screen opens its
      // live stream and the submitter watches the pipeline work rather than being told
      // "submitted" and left to go looking.
      router.push(`/cases/${case_id}`);
    } catch (cause) {
      setError(
        cause instanceof GatewayError ? cause.detail : "The case could not be submitted.",
      );
      setSubmitting(false);
    }
  }

  const ready =
    memberId.trim() !== "" &&
    requestedCode.trim() !== "" &&
    icd10.trim() !== "" &&
    dateOfService !== "" &&
    !submitting;

  return (
    <AppShell>
      <div className="stack">
        <h1>Submit a case</h1>

        <form className="card stack stack--tight" onSubmit={onSubmit}>
          <div className="fields">
            <div className="field">
              <label htmlFor="member-id">Member ID</label>
              <input
                id="member-id"
                type="text"
                required
                autoComplete="off"
                value={memberId}
                onChange={(event) => setMemberId(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="date-of-service">Date of service</label>
              <input
                id="date-of-service"
                type="date"
                required
                value={dateOfService}
                onChange={(event) => setDateOfService(event.target.value)}
              />
              {/* Not decoration: `policy` resolves the coverage determination in force on
                  this date, not the current one. A case dated last year is adjudicated
                  against last year's rule. */}
              <p className="hint">
                The coverage determination in force on this date is the one the case is
                judged against.
              </p>
            </div>

            <div className="field">
              <label htmlFor="requested-code">Requested procedure code</label>
              <input
                id="requested-code"
                type="text"
                required
                autoComplete="off"
                placeholder="E0601"
                value={requestedCode}
                onChange={(event) => setRequestedCode(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="icd10">Diagnosis code (ICD-10)</label>
              <input
                id="icd10"
                type="text"
                required
                autoComplete="off"
                placeholder="G47.33"
                value={icd10}
                onChange={(event) => setIcd10(event.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="kind">Request type</label>
              <select
                id="kind"
                value={kind}
                onChange={(event) => setKind(event.target.value as Case["kind"])}
              >
                {KINDS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="field fields--wide">
            <label htmlFor="request-text">Clinical narrative</label>
            <textarea
              id="request-text"
              value={requestText}
              onChange={(event) => setRequestText(event.target.value)}
              placeholder="Why this is being requested, in the submitting clinician's own words."
            />
            {/*
              The measurement, at the field, because this is the one input whose absence
              silently degrades the determination rather than failing. The retrieval step
              ranks policy passages with a cross-encoder, and a bare code is out of
              distribution for a model trained on question/passage pairs -- codes-only
              queries score in a flat band that means "nothing here matches".
            */}
            <p className="hint">
              This text is what the coverage policy is searched with, and it changes what the
              system finds. Measured on this corpus: a search built from the codes alone
              reached <strong>one</strong> of the five passages that decide these cases; the
              same search with a narrative reached <strong>four</strong>. Leaving this blank
              still produces a determination — from a worse search.
            </p>
          </div>

          {error ? <p className="error">{error}</p> : null}

          <div className="row">
            <button type="submit" disabled={!ready}>
              {submitting ? "Submitting…" : "Submit for adjudication"}
            </button>
            <span className="hint">
              Submitting twice cannot create two cases: this form carries a key that makes a
              repeated submission return the case the first one created.
            </span>
          </div>
        </form>
      </div>
    </AppShell>
  );
}
