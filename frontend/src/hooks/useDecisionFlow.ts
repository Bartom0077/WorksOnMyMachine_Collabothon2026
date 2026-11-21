import { useCallback, useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { DecisionStatus, RescueOption, ScenarioParams } from "../types/api";

export interface DecisionOutcome {
  optionId: string;
  optionTitle: string;
  messages: string[];
}

export interface DecisionFlow {
  /** The option waiting for the owner's explicit confirmation. */
  awaitingApproval: RescueOption | null;
  busyOptionId: string | null;
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

/** Approve (with a confirmation step) or reject a rescue option via POST /decisions. */
export function useDecisionFlow(
  params: ScenarioParams,
  llmExplanation: string | null,
  onDecided: () => void,
): DecisionFlow {
  const [awaitingApproval, setAwaitingApproval] = useState<RescueOption | null>(null);
  const [busyOptionId, setBusyOptionId] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<DecisionOutcome | null>(null);

  const decide = useCallback(
    async (option: RescueOption, status: DecisionStatus) => {
      setBusyOptionId(option.id);
      setError(null);
      try {
        const response = await api.postDecision({
          ...params,
          option_id: option.id,
          status,
          llm_explanation: llmExplanation,
        });
        setAwaitingApproval(null);
        setOutcome(
          status === "approved"
            ? { optionId: option.id, optionTitle: option.title, messages: response.execution_messages }
            : null,
        );
        onDecided();
      } catch (caught) {
        setError(toApiError(caught));
      } finally {
        setBusyOptionId(null);
      }
    },
    [params, llmExplanation, onDecided],
  );

  const requestApproval = useCallback((option: RescueOption) => {
    setError(null);
    setAwaitingApproval(option);
  }, []);

  const cancelApproval = useCallback(() => {
    setError(null);
    setAwaitingApproval(null);
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
