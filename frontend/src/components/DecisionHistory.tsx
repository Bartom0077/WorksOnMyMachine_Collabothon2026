import type { AsyncResource } from "../hooks/useAsyncResource";
import { isProposalId } from "../lib/advice";
import { creditLineNote, formatDate, formatDateTime, formatEur } from "../lib/format";
import type { Decision, DecisionsResponse } from "../types/api";
import { EmptyState, ErrorNotice, Skeleton } from "./Feedback";
import { Icon } from "./Icon";
import { StatusChip } from "./StatusChip";

interface Props {
  decisions: AsyncResource<DecisionsResponse>;
}

type DecisionOutcome = "approved" | "rejected" | "withdrawn";

const OUTCOME_LABEL: Record<DecisionOutcome, string> = {
  approved: "Approved",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
};

function decisionOutcome(decision: Decision): DecisionOutcome {
  return decision.withdrawn_at !== null ? "withdrawn" : decision.status;
}

/** Outcome badge with what was executed; a withdrawn approval also says when it was reversed. */
function OutcomeCell({ decision, outcome }: { decision: Decision; outcome: DecisionOutcome }) {
  return (
    <>
      <div className="history__outcome">
        <span className={`badge badge--${outcome}`}>
          <Icon name={outcome === "approved" ? "check" : "close"} size={14} />
          {OUTCOME_LABEL[outcome]}
        </span>
        {outcome === "rejected" ? (
          <span className="muted">Not executed</span>
        ) : (
          <span className="badge badge--simulated">{decision.execution}</span>
        )}
      </div>
      {decision.withdrawn_at !== null ? (
        <p className="muted">
          Approved, then withdrawn on {formatDateTime(decision.withdrawn_at)}. Reversed: no longer in the forecast
        </p>
      ) : null}
    </>
  );
}

function DecisionRow({ decision }: { decision: Decision }) {
  const { snapshot } = decision;
  const outcome = decisionOutcome(decision);
  const creditNote = creditLineNote(snapshot.credit_limit_after_eur, snapshot.min_balance_after_eur);
  return (
    <tr>
      <td className="history__when">
        {formatDateTime(decision.decided_at)}
        <p className="muted">“Today” was {formatDate(decision.as_of_date)}</p>
      </td>
      <th scope="row">
        {isProposalId(decision.option_id) ? "Recommendation " : ""}
        {decision.option_id}: {decision.option_title}
      </th>
      <td>
        <OutcomeCell decision={decision} outcome={outcome} />
      </td>
      <td>
        {/* Stands in for the column header, which the stacked mobile layout hides. */}
        <span className="history__label" aria-hidden="true">
          Lowest balance
        </span>
        <div className="before-after">
          <span>
            {formatEur(snapshot.min_balance_before_eur)} <StatusChip status={snapshot.status_before} />
          </span>
          {/* The arrow stays with the "after" figure when the pair wraps onto two lines. */}
          <span>
            <span aria-hidden="true">→ </span>
            <span className="visually-hidden">changes to </span>
            {formatEur(snapshot.min_balance_after_eur)} <StatusChip status={snapshot.status_after} />
          </span>
        </div>
        {creditNote !== null ? <p className="muted">{creditNote}</p> : null}
      </td>
    </tr>
  );
}

/** "History" panel: audit trail of approved, rejected and later withdrawn options and CFO recommendations. */
export function DecisionHistory({ decisions }: Props) {
  const list = decisions.data?.decisions ?? null;

  return (
    <section className="history" aria-labelledby="decisions-title">
      <h2 id="decisions-title" className="visually-hidden">
        Decision history
      </h2>
      {decisions.error ? (
        <ErrorNotice title="The decision history could not be loaded" error={decisions.error} onRetry={decisions.reload} />
      ) : null}
      {list === null && decisions.loading ? <Skeleton label="Loading the decision history" lines={2} /> : null}
      {list !== null && list.length === 0 ? (
        <EmptyState>No decisions yet. Approvals and rejections are recorded here.</EmptyState>
      ) : null}
      {list !== null && list.length > 0 ? (
        <div className="table-scroll history__scroll" tabIndex={0} role="region" aria-labelledby="decisions-title">
          <table className="table history__table">
            <thead>
              <tr>
                <th scope="col">Decided</th>
                <th scope="col">Option or recommendation</th>
                <th scope="col">Decision</th>
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
