/**
 * The evidence behind one verdict, as the verifier recorded it.
 *
 * Rendered by the *shape* of what is there, never by which policy or which fact produced
 * it: no branch in this file may name an NCD, a procedure or a clinical concept
 * (CLAUDE.md invariant 3). What it does know about are the four structures the verifiers
 * emit -- quoted spans, a list of records that were checked, a reason sentence, and
 * scalars -- and each of those is a shape, not a subject.
 *
 * Quoted spans get their own treatment because they are the one part a reviewer is meant
 * to check by eye. Spans that the verifier could not find verbatim in the record are
 * shown too, marked, rather than dropped: the model claimed them, they played no part in
 * the verdict, and a reviewer weighing how much to trust a model's reading benefits from
 * seeing what it made up.
 */

interface EvidenceProps {
  evidence: Record<string, unknown> | null;
}

/** Keys with a rendering of their own; everything else falls through to the scalar list. */
const QUOTE_KEYS = ["quoted_spans", "ungrounded_spans"] as const;
const RECORD_LIST_KEYS = [
  "studies_checked",
  "matched_conditions",
  "notes_checked",
] as const;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function scalarText(value: unknown): string {
  if (value === null || value === undefined) return "--";
  if (Array.isArray(value)) return value.map(scalarText).join(", ");
  if (isRecord(value)) {
    return Object.entries(value)
      .map(([key, nested]) => `${key}: ${scalarText(nested)}`)
      .join(", ");
  }
  return String(value);
}

function Quotes({ spans, grounded }: { spans: string[]; grounded: boolean }) {
  return (
    <div>
      <div className="small muted">
        {grounded
          ? "Quoted from the member's record"
          : "Quoted by the model but not found in the record — disregarded"}
      </div>
      {spans.map((span, index) => (
        <blockquote
          key={`${index}-${span.slice(0, 24)}`}
          className={grounded ? "quote" : "quote quote--ungrounded"}
        >
          {span}
        </blockquote>
      ))}
    </div>
  );
}

function RecordList({ label, rows }: { label: string; rows: unknown[] }) {
  return (
    <div>
      <div className="small muted">
        {label.replace(/_/g, " ")} ({rows.length})
      </div>
      <ul className="small mono" style={{ margin: "0.2rem 0 0", paddingLeft: "1.1rem" }}>
        {rows.map((row, index) => (
          <li key={index}>{scalarText(row)}</li>
        ))}
      </ul>
    </div>
  );
}

export function EvidenceView({ evidence }: EvidenceProps) {
  if (!evidence || Object.keys(evidence).length === 0) {
    return <p className="notice">No evidence recorded.</p>;
  }

  const quotes = QUOTE_KEYS.filter(
    (key) => Array.isArray(evidence[key]) && (evidence[key] as unknown[]).length > 0,
  );
  const lists = RECORD_LIST_KEYS.filter(
    (key) => Array.isArray(evidence[key]) && (evidence[key] as unknown[]).length > 0,
  );
  const handled = new Set<string>([...quotes, ...lists, "reason", "criterion_text"]);
  const scalars = Object.entries(evidence).filter(([key]) => !handled.has(key));

  return (
    <div className="evidence stack stack--tight">
      {typeof evidence.reason === "string" ? <div>{evidence.reason}</div> : null}

      {quotes.map((key) => (
        <Quotes
          key={key}
          spans={evidence[key] as string[]}
          grounded={key === "quoted_spans"}
        />
      ))}

      {scalars.length > 0 ? (
        <dl>
          {scalars.map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt>{key}</dt>
              <dd>{scalarText(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {/* Last, and collapsed to one line each: what was checked and did not match is
          context for the verdict, not the verdict's argument. It matters most when it is
          empty -- "no studies on record" is a different finding from "three studies, none
          qualifying", and a reviewer must be able to tell them apart. */}
      {lists.map((key) => (
        <RecordList key={key} label={key} rows={evidence[key] as unknown[]} />
      ))}
    </div>
  );
}
