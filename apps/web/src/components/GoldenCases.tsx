"use client";

/**
 * The golden set: the labelled cases every measurement in this service is measured against.
 *
 * **The labels are human-authored, and this form is the place that is enforced socially
 * rather than only technically.** A label a model wrote measures agreement between two
 * models, not correctness, and would void every number the harness produces
 * ([ADR-0008](../../../../docs/decisions/0008-human-authored-golden-labels.md)). `author` is
 * `NOT NULL` in the schema and rejected empty by the service; the wording here is the other
 * half of that, because the failure it guards against is a person pasting in a model's answer
 * rather than a client omitting a field.
 *
 * The form composes a `fixture` from the same fields the intake screen collects, because the
 * fixture is forwarded verbatim to `POST /cases` — the service does not model it field by
 * field, so mirroring the request schema is this form's job. It deliberately cannot set
 * `run_mode` or `idempotency_key`: both belong to the run that submits the case rather than
 * to the label, and the service rejects a fixture carrying either.
 */

import { useState } from "react";

import { useSession } from "@/components/SessionProvider";
import * as api from "@/lib/api";
import { GatewayError } from "@/lib/gateway";
import { formatDate } from "@/lib/format";
import type { Case, GoldenCase, Outcome } from "@/lib/types";

const EXPECTED: { value: Outcome; label: string }[] = [
  { value: "approve", label: "Approve — a person reading the policy would auto-approve this" },
  { value: "escalate", label: "Escalate — a person would refer this to a clinician" },
];

export function GoldenCaseTable({ cases }: { cases: GoldenCase[] }) {
  if (cases.length === 0) {
    return <p className="notice">No golden cases yet. Nothing can be measured without them.</p>;
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>#</th>
          <th>Member</th>
          <th>Request</th>
          <th>Date of service</th>
          <th>Expected</th>
          <th>Criteria labelled</th>
          <th>Author</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((item) => (
          <tr key={item.id}>
            <td className="mono">{item.id}</td>
            <td className="mono">{String(item.fixture.member_id ?? "--")}</td>
            <td className="mono">
              {String(item.fixture.requested_code ?? "--")}{" "}
              <span className="muted">{String(item.fixture.icd10 ?? "")}</span>
            </td>
            <td>{formatDate(String(item.fixture.date_of_service ?? ""))}</td>
            <td>{item.expected_outcome}</td>
            <td>
              {/* Zero is meaningful here and is not a gap: a case can be labelled at the
                  outcome level alone, and the report excludes such cases from extraction
                  scoring rather than counting them as a precision of zero. */}
              {item.expected_criteria.length === 0 ? (
                <span className="muted small">outcome only</span>
              ) : (
                item.expected_criteria.length
              )}
            </td>
            <td className="small">{item.author}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function GoldenCaseForm({ onCreated }: { onCreated: () => void }) {
  const { session } = useSession();

  const [memberId, setMemberId] = useState("");
  const [requestedCode, setRequestedCode] = useState("");
  const [icd10, setIcd10] = useState("");
  const [dateOfService, setDateOfService] = useState("");
  const [kind, setKind] = useState<Case["kind"]>("initial");
  const [requestText, setRequestText] = useState("");
  const [expected, setExpected] = useState<Outcome>("escalate");
  const [criteria, setCriteria] = useState("");
  const [author, setAuthor] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!session) return;

    const narrative = requestText.trim();
    setError(null);
    setSubmitting(true);
    try {
      await api.createGoldenCase(session.token, {
        fixture: {
          member_id: memberId.trim(),
          requested_code: requestedCode.trim(),
          icd10: icd10.trim(),
          date_of_service: dateOfService,
          kind,
          request_text: narrative === "" ? null : narrative,
        },
        expected_outcome: expected,
        // One criterion per line: the list is prose a person wrote reading the policy, and
        // any single-character separator would eventually appear inside one of them.
        expected_criteria: criteria
          .split("\n")
          .map((line) => line.trim())
          .filter((line) => line !== ""),
        author: author.trim(),
        notes: notes.trim() === "" ? null : notes.trim(),
      });
      setMemberId("");
      setRequestedCode("");
      setIcd10("");
      setDateOfService("");
      setRequestText("");
      setCriteria("");
      setNotes("");
      onCreated();
    } catch (cause) {
      setError(
        cause instanceof GatewayError ? cause.detail : "The golden case could not be saved.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const ready =
    memberId.trim() !== "" &&
    requestedCode.trim() !== "" &&
    icd10.trim() !== "" &&
    dateOfService !== "" &&
    author.trim() !== "" &&
    !submitting;

  return (
    <form className="card stack stack--tight" onSubmit={onSubmit}>
      <h2>Add a golden case</h2>
      <p className="small muted">
        The expected outcome below is <strong>your</strong> reading of the policy, written
        before you look at what the system did. A label produced by a model measures agreement
        between two models rather than correctness, and would void every number this harness
        reports.
      </p>

      <div className="fields">
        <div className="field">
          <label htmlFor="golden-member">Member ID</label>
          <input
            id="golden-member"
            type="text"
            required
            value={memberId}
            onChange={(event) => setMemberId(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="golden-dos">Date of service</label>
          <input
            id="golden-dos"
            type="date"
            required
            value={dateOfService}
            onChange={(event) => setDateOfService(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="golden-code">Requested procedure code</label>
          <input
            id="golden-code"
            type="text"
            required
            value={requestedCode}
            onChange={(event) => setRequestedCode(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="golden-icd10">Diagnosis code (ICD-10)</label>
          <input
            id="golden-icd10"
            type="text"
            required
            value={icd10}
            onChange={(event) => setIcd10(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="golden-kind">Request type</label>
          <select
            id="golden-kind"
            value={kind}
            onChange={(event) => setKind(event.target.value as Case["kind"])}
          >
            <option value="initial">Initial authorization</option>
            <option value="continuation">Continuation of therapy</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="golden-expected">Expected outcome</label>
          <select
            id="golden-expected"
            value={expected}
            onChange={(event) => setExpected(event.target.value as Outcome)}
          >
            {EXPECTED.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          {/* There is no third option and there must not be: the system has no deny path, so
              a golden case expecting a denial would be a test for behaviour that must never
              exist (ADR-0002). The schema's CHECK says the same thing. */}
          <p className="hint">
            Two values only. The system cannot deny, so the eval set must not be able to
            expect a denial.
          </p>
        </div>
      </div>

      <div className="field">
        <label htmlFor="golden-narrative">Clinical narrative</label>
        <textarea
          id="golden-narrative"
          value={requestText}
          onChange={(event) => setRequestText(event.target.value)}
        />
        <p className="hint">
          Submitted with the case, and what the policy search runs on. A golden case with no
          narrative measures the system against a worse retrieval than a real submission would
          give it.
        </p>
      </div>

      <div className="field">
        <label htmlFor="golden-criteria">Expected criteria, one per line</label>
        <textarea
          id="golden-criteria"
          value={criteria}
          onChange={(event) => setCriteria(event.target.value)}
          placeholder={"AHI of at least 15 events per hour\nStudy was ordered by the treating physician"}
        />
        <p className="hint">
          Optional. Without them the case still scores at the outcome level, but extraction
          precision and recall are not measurable for it — and the report says so rather than
          scoring it zero.
        </p>
      </div>

      <div className="fields">
        <div className="field">
          <label htmlFor="golden-author">Author</label>
          <input
            id="golden-author"
            type="text"
            required
            value={author}
            onChange={(event) => setAuthor(event.target.value)}
          />
          <p className="hint">
            A person. This is recorded so a reader of the numbers can ask them what they meant.
          </p>
        </div>
        <div className="field">
          <label htmlFor="golden-notes">Notes</label>
          <input
            id="golden-notes"
            type="text"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Why this case, and what makes it a near miss."
          />
        </div>
      </div>

      {error ? <p className="error">{error}</p> : null}

      <div className="row">
        <button type="submit" disabled={!ready}>
          {submitting ? "Saving…" : "Add golden case"}
        </button>
      </div>
    </form>
  );
}
