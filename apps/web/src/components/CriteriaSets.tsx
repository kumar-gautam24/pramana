import { CriterionRow } from "@/components/CriterionRow";
import type { CaseCriteria, Determination } from "@/lib/types";

/**
 * The policy as the system decomposed it: one panel per alternative set.
 *
 * A coverage determination is usually a disjunction -- several independent routes to the
 * same approval -- and a reviewer reading a flat list of criteria would draw the wrong
 * conclusion from it, because a criterion that fails in one set may be irrelevant in
 * another (ADR-0011). The grouping comes from the server, which knows which criteria
 * belong together; this component only renders it.
 *
 * `blocking` on the determination names the criteria of the *closest* set -- the one
 * nearest to being satisfied -- which is what a reviewer should look at first. Those rows
 * are marked. `winning_set` names the set that approved an approved case.
 */
export function CriteriaSets({
  criteria,
  determination,
}: {
  criteria: CaseCriteria;
  determination: Determination | null;
}) {
  if (criteria.sets.length === 0) {
    return (
      <p className="notice">
        No criteria were established for this case. The determination above says at which
        stage it stopped.
      </p>
    );
  }

  const blocking = new Set(determination?.blocking ?? []);

  return (
    <div className="stack">
      {criteria.sets.map((set) => {
        const won = determination?.winning_set === set.set_ordinal;
        const hasBlocking = set.criteria.some((criterion) => blocking.has(criterion.id));
        return (
          <section className="set" key={set.set_ordinal}>
            <header className="set__header">
              <h3>
                Alternative route {set.set_ordinal} of {criteria.sets.length}
              </h3>
              {won ? <span className="badge badge--approve">Satisfied</span> : null}
              {hasBlocking ? (
                <span className="badge badge--escalate">Closest to satisfied</span>
              ) : null}
              <div className="shell__spacer" />
              <span className="small muted">
                {set.criteria.length} criteri{set.criteria.length === 1 ? "on" : "a"}
              </span>
            </header>
            {set.criteria.map((criterion) => (
              <CriterionRow
                key={criterion.id}
                criterion={criterion}
                blocking={blocking.has(criterion.id)}
              />
            ))}
          </section>
        );
      })}
    </div>
  );
}
