import { useCallback, useEffect, useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { Dashboard, DecisionStatus, RescueOption } from "../types/api";

/** The server no longer offers the option: the board on screen is out of date. */
const OPTION_NOT_AVAILABLE = "option_not_available";

export interface DecisionOutcome {
  optionId: string;
  optionTitle: string;
  messages: string[];
}

export interface DecisionFlow {
  /** The option waiting for the owner's explicit confirmation, as offered by the dashboard on screen. */
  awaitingApproval: RescueOption | null;
  busyOptionId: string | null;
  /** False while the dashboard on screen is being recalculated or failed to reload; decisions wait until it is current. */
  canDecide: boolean;
  error: ApiError | null;
  /** Execution messages of the most recent approval. */
  outcome: DecisionOutcome | null;
  requestApproval: (option: RescueOption) => void;
  cancelApproval: () => void;
  confirmApproval: () => void;
  reject: (option: RescueOption) => void;
  dismissOutcome: () => void;
  dismissError: () => void;
}

/**
 * Approve (with a confirmation step) or reject a rescue option via POST /decisions.
 *
 * A decision always refers to `board`, the dashboard the owner is looking at: its scenario is what gets
 * posted, so the figures confirmed on screen are the ones the server records.
 */
export function useDecisionFlow(
  board: Dashboard | null,
  boardIsCurrent: boolean,
  llmExplanation: string | null,
  onDecided: () => void,
): DecisionFlow {
  const [awaitingOptionId, setAwaitingOptionId] = useState<string | null>(null);
  const [busyOptionId, setBusyOptionId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<DecisionOutcome | null>(null);

  // Looked up in the board on screen, so the dialog never shows figures of a dashboard that was replaced
  // and closes by itself when the option is no longer offered.
  const awaitingApproval = board?.options.find((option) => option.id === awaitingOptionId) ?? null;
  const canDecide = board !== null && boardIsCurrent && busyOptionId === null;

  // Forget a confirmation whose option disappeared, so it cannot reopen if a later board offers that id again.
  useEffect(() => {
    if (awaitingOptionId !== null && board !== null && awaitingApproval === null) setAwaitingOptionId(null);
  }, [awaitingOptionId, board, awaitingApproval]);

  const decide = useCallback(
    async (option: RescueOption, status: DecisionStatus) => {
      if (board === null || !canDecide) return;
      const { as_of_date, horizon_days, buffer_eur } = board;
      setBusyOptionId(option.id);
      setError(null);
      try {
        const response = await api.postDecision({
          as_of_date,
          horizon_days,
          buffer_eur,
          option_id: option.id,
          status,
          llm_explanation: llmExplanation,
        });
        setAwaitingOptionId(null);
        setOutcome(
          status === "approved"
            ? { optionId: option.id, optionTitle: option.title, messages: response.execution_messages }
            : null,
        );
        onDecided();
      } catch (caught) {
        const apiError = toApiError(caught);
        setError(apiError);
        if (apiError.code === OPTION_NOT_AVAILABLE) onDecided();
      } finally {
        setBusyOptionId(null);
      }
    },
    [board, canDecide, llmExplanation, onDecided],
  );

  const requestApproval = useCallback((option: RescueOption) => {
    setError(null);
    setAwaitingOptionId(option.id);
  }, []);

  const cancelApproval = useCallback(() => {
    setError(null);
    setAwaitingOptionId(null);
  }, []);

  const confirmApproval = useCallback(() => {
    if (awaitingApproval !== null) void decide(awaitingApproval, "approved");
  }, [awaitingApproval, decide]);

  const reject = useCallback((option: RescueOption) => void decide(option, "rejected"), [decide]);
  const dismissOutcome = useCallback(() => setOutcome(null), []);
  const dismissError = useCallback(() => setError(null), []);

  return {
    awaitingApproval,
    busyOptionId,
    canDecide,
    error,
    outcome,
    requestApproval,
    cancelApproval,
    confirmApproval,
    reject,
    dismissOutcome,
    dismissError,
  };
}
