import type { DecisionFlow } from "../hooks/useDecisionFlow";
import { creditLineNote, formatDate, formatEur } from "../lib/format";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusChip } from "./StatusChip";

interface Props {
  decisions: DecisionFlow;
}

/** Explicit confirmation before an option is approved; makes clear that execution is simulated. */
export function ApprovalDialog({ decisions }: Props) {
  const option = decisions.awaitingApproval;
  const creditNote = option ? creditLineNote(option.credit_limit_eur, option.new_min_balance_eur) : null;

  return (
    <ConfirmDialog
      open={option !== null}
      title={option ? `Approve option ${option.id}: ${option.title}?` : "Approve option"}
      confirmLabel="Approve (simulated)"
      busyLabel="Approving…"
      busy={decisions.busyOptionId !== null}
      blockedLabel={decisions.canDecide || decisions.busyOptionId !== null ? undefined : "Recalculating…"}
      error={decisions.error}
      onConfirm={decisions.confirmApproval}
      onCancel={decisions.cancelApproval}
    >
      {option ? (
        <>
          <p className="simulated-banner">
            <span className="badge badge--simulated">SIMULATED</span>
            This is a demonstration. No real payment, transfer or credit request will be made.
          </p>
          <p>
            Your lowest projected balance becomes <strong className="dialog__figure">{formatEur(option.new_min_balance_eur)}</strong> on{" "}
            {formatDate(option.new_min_balance_date)} <StatusChip status={option.new_status} />
          </p>
          {creditNote !== null ? <p className="muted">{creditNote}</p> : null}
          {option.simulated_actions.length > 0 ? (
            <>
              <p className="dialog__label">What will be simulated</p>
              <ul className="dialog__list">
                {option.simulated_actions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </>
          ) : null}
          <p className="muted">The decision is recorded in your decision history and the forecast is recalculated.</p>
        </>
      ) : null}
    </ConfirmDialog>
  );
}
