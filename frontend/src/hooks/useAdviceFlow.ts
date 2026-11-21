import { useCallback, useEffect, useMemo, useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { AdviceCard } from "../lib/advice";
import type { AdviceResponse, DecisionStatus } from "../types/api";

/** The server no longer offers one of the proposals: the recommendations on screen are out of date. */
const PROPOSAL_NOT_AVAILABLE = "proposal_not_available";

export interface AdviceOutcome {
  title: string;
  messages: string[];
}

export interface AdviceFlow {
  /** The ticked proposals, as offered by the recommendations on screen. */
  selected: AdviceCard[];
  /** True while the owner is asked to confirm the approval of the selection. */
  awaitingApproval: boolean;
  /** The decision being saved, if any. */
  busyStatus: DecisionStatus | null;
  /** False while the forecast or the recommendations on screen are being recalculated or failed to reload. */
  canDecide: boolean;
  error: ApiError | null;
  /** Execution messages of the most recent approval. */
  outcome: AdviceOutcome | null;
  /**
   * How many selections the flow itself has taken away: saved as a decision, or gone stale during the
   * confirmation. Each time the control that held the keyboard focus is disabled along with the selection.
   */
  settledSelections: number;
  toggle: (proposalId: string) => void;
  clearSelection: () => void;
  requestApproval: () => void;
  cancelApproval: () => void;
  confirmApproval: () => void;
  rejectSelected: () => void;
  dismissOutcome: () => void;
  dismissError: () => void;
}

function approvalTitle(approved: AdviceCard[]): string {
  const [only] = approved;
  return approved.length === 1 && only !== undefined
    ? `Recommendation ${only.proposal.id} approved: ${only.title}`
    : `${approved.length} recommendations approved`;
}

/**
 * Select CFO recommendations and approve (with a confirmation step) or reject them together via
 * POST /advice/decisions.
 *
 * A decision always refers to `advice`, the recommendations the owner is looking at: its scenario and the
 * texts on its cards are what gets posted.
 */
export function useAdviceFlow(
  advice: AdviceResponse | null,
  cards: AdviceCard[],
  adviceIsCurrent: boolean,
  onDecided: () => void,
): AdviceFlow {
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [approvalRequested, setApprovalRequested] = useState(false);
  const [busyStatus, setBusyStatus] = useState<DecisionStatus | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [outcome, setOutcome] = useState<AdviceOutcome | null>(null);
  const [settledSelections, setSettledSelections] = useState(0);

  // Looked up in the recommendations on screen. A selection with a proposal that is no longer offered is
  // dropped as a whole: the owner chose a combination, and that combination no longer exists.
  const selected = useMemo(() => {
    const offered = cards.filter((card) => selectedIds.includes(card.proposal.id));
    return offered.length === selectedIds.length ? offered : [];
  }, [cards, selectedIds]);
  const selectionIsStale = advice !== null && selectedIds.length > 0 && selected.length === 0;
  const awaitingApproval = approvalRequested && selected.length > 0;
  const canDecide = advice !== null && adviceIsCurrent && busyStatus === null;

  // Forget a stale selection, so it cannot come back if later recommendations offer those ids again.
  useEffect(() => {
    if (!selectionIsStale) return;
    setSelectedIds([]);
    if (!approvalRequested) return;
    setApprovalRequested(false);
    setSettledSelections((count) => count + 1);
  }, [selectionIsStale, approvalRequested]);

  const decide = useCallback(
    async (status: DecisionStatus) => {
      if (advice === null || !canDecide || selected.length === 0) return;
      const { as_of_date, horizon_days, buffer_eur } = advice;
      setBusyStatus(status);
      setError(null);
      try {
        const response = await api.postAdviceDecisions({
          as_of_date,
          horizon_days,
          buffer_eur,
          proposal_ids: selected.map((card) => card.proposal.id),
          status,
          explanations: Object.fromEntries(selected.map((card) => [card.proposal.id, card.explanation])),
          fingerprints: Object.fromEntries(selected.map((card) => [card.proposal.id, card.proposal.fingerprint])),
        });
        setSelectedIds([]);
        setApprovalRequested(false);
        setSettledSelections((count) => count + 1);
        setOutcome(
          status === "approved" ? { title: approvalTitle(selected), messages: response.execution_messages } : null,
        );
        onDecided();
      } catch (caught) {
        const apiError = toApiError(caught);
        setError(apiError);
        if (apiError.code === PROPOSAL_NOT_AVAILABLE) onDecided();
      } finally {
        setBusyStatus(null);
      }
    },
    [advice, canDecide, selected, onDecided],
  );

  const toggle = useCallback((proposalId: string) => {
    setSelectedIds((current) =>
      current.includes(proposalId) ? current.filter((id) => id !== proposalId) : [...current, proposalId],
    );
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedIds([]);
    setApprovalRequested(false);
  }, []);

  const requestApproval = useCallback(() => {
    setError(null);
    setApprovalRequested(true);
  }, []);

  const cancelApproval = useCallback(() => {
    setError(null);
    setApprovalRequested(false);
  }, []);

  const confirmApproval = useCallback(() => void decide("approved"), [decide]);
  const rejectSelected = useCallback(() => void decide("rejected"), [decide]);
  const dismissOutcome = useCallback(() => setOutcome(null), []);
  const dismissError = useCallback(() => setError(null), []);

  return {
    selected,
    awaitingApproval,
    busyStatus,
    canDecide,
    error,
    outcome,
    settledSelections,
    toggle,
    clearSelection,
    requestApproval,
    cancelApproval,
    confirmApproval,
    rejectSelected,
    dismissOutcome,
    dismissError,
  };
}
