import type { AsyncResource } from "../hooks/useAsyncResource";
import { creditLineNote, formatDate, formatDateTime, formatEur } from "../lib/format";
import type { Decision, DecisionsResponse } from "../types/api";
import { EmptyState, ErrorNotice, Skeleton } from "./Feedback";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

interface Props {
  decisions: AsyncResource<DecisionsResponse>;
}

function DecisionRow({ decision }: { decision: Decision }) {
  const { snapshot } = decision;
  const approved = decision.status === "approved";
  const creditNote = creditLineNote(snapshot.credit_limit_after_eur, snapshot.min_balance_after_eur);
  return (
    <tr>
      <td>{formatDateTime(decision.decided_at)}</td>
      <td>{formatDate(decision.as_of_date)}</td>
      <th scope="row">
        {decision.option_id}: {decision.option_title}
      </th>
      <td>
        <span className={`badge badge--${decision.status}`}>
          <Icon name={approved ? "check" : "close"} size={14} />
          {approved ? "Approved" : "Rejected"}
        </span>
      </td>
      <td>
        {approved ? (
          <span className="badge badge--simulated">{decision.execution}</span>
        ) : (
          <span className="muted">Not executed</span>
        )}
      </td>
      <td>
        <div className="before-after">
          <span>
            {formatEur(snapshot.min_balance_before_eur)} <StatusChip status={snapshot.status_before} />
          </span>
          <span aria-hidden="true">→</span>
          <span className="visually-hidden">changes to</span>
          <span>
            {formatEur(snapshot.min_balance_after_eur)} <StatusChip status={snapshot.status_after} />
          </span>
        </div>
        {creditNote !== null ? <p className="muted">{creditNote}</p> : null}
      </td>
    </tr>
  );
}

/** Audit trail of approved and rejected options. */
export function DecisionHistory({ decisions }: Props) {
  const list = decisions.data?.decisions ?? null;

  return (
    <section className="card" aria-labelledby="decisions-title">
      <div className="card__head">
        <h2 id="decisions-title" className="card__title">
          Decision history
        </h2>
      </div>
      {decisions.error ? (
        <ErrorNotice title="The decision history could not be loaded" error={decisions.error} onRetry={decisions.reload} />
      ) : null}
      {list === null && decisions.loading ? <Skeleton label="Loading the decision history" lines={2} /> : null}
      {list !== null && list.length === 0 ? (
        <EmptyState>No decisions yet. Options you approve or reject are recorded here.</EmptyState>
      ) : null}
      {list !== null && list.length > 0 ? (
        <div className="table-scroll" tabIndex={0} role="region" aria-labelledby="decisions-title">
          <table className="table table--wide">
            <thead>
              <tr>
                <th scope="col">Decided</th>
                <th scope="col">“Today” was</th>
                <th scope="col">Option</th>
                <th scope="col">Decision</th>
                <th scope="col">Execution</th>
                <th scope="col">Lowest balance, before → after</th>
              </tr>
            </thead>
            <tbody>
              {list.map((decision) => (
                <DecisionRow key={decision.id} decision={decision} />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
