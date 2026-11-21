import { useCallback, useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { PlannedEvent } from "../types/api";

export interface PlanRemoval {
  /** The plan whose removal is in flight; one at a time, so their state cannot clobber each other. */
  removingId: number | null;
  /** The measure of an approved option or recommendation waiting for the owner's explicit confirmation. */
  awaitingConfirmationId: number | null;
  error: ApiError | null;
  /** Removes a chat plan at once; a measure the agent put in place asks for a confirmation first. */
  request: (event: PlannedEvent) => void;
  confirm: () => void;
  cancel: () => void;
  dismissError: () => void;
}

/**
 * Removes planned events via DELETE /events.
 *
 * The state lives above the "My plans" panel, which is unmounted while another tab is open: a removal that
 * is still in flight keeps its busy state and still reports its failure when the owner comes back.
 */
export function usePlanRemoval(onRemoved: () => void): PlanRemoval {
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [awaitingConfirmationId, setAwaitingConfirmationId] = useState<number | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const remove = useCallback(
    async (id: number) => {
      if (removingId !== null) return;
      setRemovingId(id);
      setError(null);
      try {
        await api.deleteEvent(id);
        setAwaitingConfirmationId(null);
        onRemoved();
      } catch (failure) {
        setError(toApiError(failure));
      } finally {
        setRemovingId(null);
      }
    },
    [removingId, onRemoved],
  );

  const request = useCallback(
    (event: PlannedEvent) => {
      if (event.source === "agent") {
        setError(null);
        setAwaitingConfirmationId(event.id);
      } else {
        void remove(event.id);
      }
    },
    [remove],
  );

  const confirm = useCallback(() => {
    if (awaitingConfirmationId !== null) void remove(awaitingConfirmationId);
  }, [awaitingConfirmationId, remove]);

  const cancel = useCallback(() => {
    setAwaitingConfirmationId(null);
    setError(null);
  }, []);

  const dismissError = useCallback(() => setError(null), []);

  return { removingId, awaitingConfirmationId, error, request, confirm, cancel, dismissError };
}
