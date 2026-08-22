import Link from "next/link";

import { OutcomeBadge } from "@/components/OutcomeBadge";
import { formatDate, formatDateTime, reasonLabel } from "@/lib/format";
import type { QueuedCase } from "@/lib/types";

/**
 * The queue, as a table. Presentational only -- it is handed rows and renders them.
 *
 * The reason column carries the gate's sentence rather than its enum value, because that
 * sentence is what tells a reviewer whether to open the case now: "the record does not
 * say enough" is a different afternoon from "the record contradicts a criterion".
 *
 * A case with no determination is shown, not filtered out. One sitting in `running`
 * because a worker died is precisely the case nobody would otherwise notice.
 */
export function CaseQueueTable({ cases }: { cases: QueuedCase[] }) {
  if (cases.length === 0) {
    return <p className="notice">No cases match this filter.</p>;
  }

  return (
    <table className="table">
      <thead>
        <tr>
          <th>Case</th>
          <th>Member</th>
          <th>Request</th>
          <th>Date of service</th>
          <th>Outcome</th>
          <th>Why</th>
          <th>Decided</th>
        </tr>
      </thead>
      <tbody>
        {cases.map((item) => (
          <tr key={item.id}>
            <td>
              <Link href={`/cases/${item.id}`} className="mono">
                {item.id.slice(0, 8)}
              </Link>
            </td>
            <td className="mono">{item.member_id.slice(0, 8)}</td>
            <td>
              <span className="mono">{item.requested_code}</span>{" "}
              <span className="muted mono">{item.icd10}</span>
              <div className="small muted">{item.kind}</div>
            </td>
            <td>{formatDate(item.date_of_service)}</td>
            <td>
              <OutcomeBadge outcome={item.determination?.outcome ?? null} />
              {item.determination === null ? (
                <div className="small muted">{item.status}</div>
              ) : null}
            </td>
            <td className="small">{reasonLabel(item.determination?.reason ?? null)}</td>
            <td className="small muted">
              {item.determination ? formatDateTime(item.determination.decided_at) : "--"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
