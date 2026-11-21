import type { AdviceFlow } from "../hooks/useAdviceFlow";
import { type AdviceCard, advicePreview, changesForecast } from "../lib/advice";
import { formatDate, formatEur } from "../lib/format";
import type { BalancePoint } from "../types/api";
import { ConfirmDialog } from "./ConfirmDialog";

interface Props {
  flow: AdviceFlow;
  /** The forecast on screen, on top of which the selected proposals are combined. */
  baseline: BalancePoint[];
}

function dialogTitle(selected: AdviceCard[]): string {
  const [only] = selected;
  return selected.length === 1 && only !== undefined
    ? `Approve recommendation ${only.proposal.id}: ${only.title}?`
    : `Approve ${selected.length} recommendations?`;
}

/** Explicit confirmation before the selected recommendations are approved; makes clear that execution is simulated. */
export function AdviceApprovalDialog({ flow, baseline }: Props) {
  const { selected } = flow;
  const combined = advicePreview(
    baseline,
    selected.filter((card) => changesForecast(card.proposal)),
  );
  const simulatedActions = selected.flatMap((card) => card.proposal.simulated_actions);

  return (
    <ConfirmDialog
      open={flow.awaitingApproval}
      title={dialogTitle(selected)}
      confirmLabel="Approve (simulated)"
      busyLabel="Approving…"
      busy={flow.busyStatus !== null}
      blockedLabel={flow.canDecide || flow.busyStatus !== null ? undefined : "Recalculating…"}
      error={flow.error}
      onConfirm={flow.confirmApproval}
      onCancel={flow.cancelApproval}
    >
      {flow.awaitingApproval ? (
        <>
          <p className="simulated-banner">
            <span className="badge badge--simulated">SIMULATED</span>
            This is a demonstration. No real payment, transfer or deposit will be made.
          </p>
          <p className="dialog__label">You are approving</p>
          <ul className="dialog__list">
            {selected.map(({ proposal, title }) => (
              <li key={proposal.id}>
                {title}{" "}
                {proposal.amount_eur !== null ? (
                  <span className="dialog__amount">
                    Amount: <strong>{formatEur(proposal.amount_eur)}</strong>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          {combined !== null ? (
            <p>
              {selected.length > 1 ? "Together, your" : "Your"} lowest projected balance becomes{" "}
              <strong>{formatEur(combined.minBalanceEur)}</strong> on {formatDate(combined.minBalanceDate)}.
            </p>
          ) : null}
          {simulatedActions.length > 0 ? (
            <>
              <p className="dialog__label">What will be simulated</p>
              <ul className="dialog__list">
                {simulatedActions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
              </ul>
            </>
          ) : null}
          <p className="muted">
            Each decision is recorded in your decision history and the forecast is recalculated.
          </p>
        </>
      ) : null}
    </ConfirmDialog>
  );
}
