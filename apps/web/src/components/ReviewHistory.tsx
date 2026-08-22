import { formatDateTime } from "@/lib/format";
import type { Review } from "@/lib/types";

/**
 * Determinations already recorded on this case.
 *
 * Shown above the form, not below it: a case may be reviewed more than once, and a
 * clinician about to record a decision needs to know a colleague has already made one.
 * The clinician's id is rendered rather than hidden -- who issued an adverse
 * determination is the fact Illinois law is about, and it is not an implementation
 * detail.
 */
export function ReviewHistory({ reviews }: { reviews: Review[] }) {
  if (reviews.length === 0) return null;

  return (
    <section className="card stack stack--tight">
      <h2>Recorded determinations</h2>
      {reviews.map((review) => (
        <article key={review.id} className="evidence stack stack--tight">
          <div className="row small">
            <strong>{review.outcome}</strong>
            <span className="muted mono">{review.clinician_id.slice(0, 8)}</span>
            <span className="muted">{formatDateTime(review.created_at)}</span>
            <div className="shell__spacer" />
            <span className="badge badge--neutral">
              {review.agreed_with_system ? "Agreed with the system" : "Disagreed with the system"}
            </span>
          </div>
          <p className="small">{review.rationale}</p>
        </article>
      ))}
    </section>
  );
}
