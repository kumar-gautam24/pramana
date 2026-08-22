"use client";

/**
 * The clinician's own decision on a case the system referred to them.
 *
 * This form has a deny option and that is correct. The two rules are not in tension, they
 * are the same rule: an adverse determination must be made by a licensed clinician
 * (California SB 1120, the Medicare Advantage rule, Illinois on who may issue one), which
 * is exactly why the machine has none and this form does. The wording around the control
 * says so, because a reviewer must never be left with the impression that they are
 * confirming a denial the system already made.
 *
 * `agreed_with_system` has no default and the form will not submit until it is answered.
 * It is the one field that turns clinical work into eval data -- every review is a
 * human-authored label on a real case (ADR-0008) -- and a default would silently record
 * agreement nobody expressed. `adjudication`'s own request model requires it for the same
 * reason.
 */

import { useState } from "react";

import { useSession } from "@/components/SessionProvider";
import * as api from "@/lib/api";
import { GatewayError } from "@/lib/gateway";
import type { ReviewOutcome } from "@/lib/types";

/**
 * The vocabulary, settled in [ADR-0019] and enforced in three places that must agree:
 * `reviews_outcome_check` in adjudication/migrations/0004_reviews_outcome_vocabulary.sql,
 * `routers/cases.py::ReviewOutcome`, and this list. The `Record<ReviewOutcome, string>`
 * annotation is what makes the third copy checkable rather than hopeful -- adding a value to
 * `ReviewOutcome` without a label here, or a label for a value the type does not have, fails
 * `tsc`.
 *
 * Three values, not the machine's two: `deny` is the adverse determination a licensed
 * clinician may issue and the system may not (ADR-0002), which is the whole reason `reviews`
 * is a separate table. Partial approval is deliberately absent -- a case carries one code,
 * one date and no units, so there is nothing for a partial to be partial of.
 */
const OUTCOME_LABELS: Record<ReviewOutcome, string> = {
  approve: "Approve — the record supports the request",
  deny: "Deny — the record does not support the request",
  pend: "Pend — the record does not say enough; request more information",
};

const OUTCOMES = Object.keys(OUTCOME_LABELS) as ReviewOutcome[];

export function ReviewForm({ caseId, onRecorded }: { caseId: string; onRecorded: () => void }) {
  const { session } = useSession();

  const [outcome, setOutcome] = useState<ReviewOutcome>("approve");
  const [rationale, setRationale] = useState("");
  const [agreed, setAgreed] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const ready = rationale.trim().length > 0 && agreed !== null && !submitting;

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!session || agreed === null) return;

    setError(null);
    setSubmitting(true);
    try {
      await api.submitReview(session.token, caseId, {
        outcome,
        rationale: rationale.trim(),
        agreed_with_system: agreed,
      });
      // The whole form resets, the decision included. A case can be reviewed more than
      // once, and leaving `deny` selected after a recorded denial makes the next submission
      // one mis-click away from a second one.
      setRationale("");
      setAgreed(null);
      setOutcome("approve");
      onRecorded();
    } catch (cause) {
      setError(
        cause instanceof GatewayError ? cause.detail : "The review could not be recorded.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="card stack stack--tight" onSubmit={onSubmit}>
      <h2>Your determination</h2>
      <p className="small muted">
        This decision is yours. The system referred this case to you; it did not deny it,
        and it has no ability to.
      </p>

      <div className="field">
        <label htmlFor="review-outcome">Decision</label>
        <select
          id="review-outcome"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value as ReviewOutcome)}
        >
          {OUTCOMES.map((value) => (
            <option key={value} value={value}>
              {OUTCOME_LABELS[value]}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="review-rationale">Rationale</label>
        <textarea
          id="review-rationale"
          required
          value={rationale}
          onChange={(event) => setRationale(event.target.value)}
          placeholder="What in the record decided this, and which criterion it bears on."
        />
      </div>

      <fieldset className="field" style={{ border: "none", padding: 0, margin: 0 }}>
        <legend>
          <label>Did you reach the same conclusion the system did?</label>
        </legend>
        <div className="row">
          <label className="choice">
            <input
              type="radio"
              name="agreed"
              checked={agreed === true}
              onChange={() => setAgreed(true)}
            />
            Yes
          </label>
          <label className="choice">
            <input
              type="radio"
              name="agreed"
              checked={agreed === false}
              onChange={() => setAgreed(false)}
            />
            No
          </label>
        </div>
        <p className="small muted">
          Recorded as a label on this case and measured against the system&rsquo;s own
          determination. Neither answer is preselected.
        </p>
      </fieldset>

      {error ? <p className="error">{error}</p> : null}

      <div className="row">
        <button type="submit" disabled={!ready}>
          {submitting ? "Recording…" : "Record determination"}
        </button>
      </div>
    </form>
  );
}
