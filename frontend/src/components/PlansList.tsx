import { useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { AsyncResource } from "../hooks/useAsyncResource";
import { EVENT_TYPE_LABELS, formatDateRange, formatEur } from "../lib/format";
import type { EventType, EventsResponse, PlannedEvent } from "../types/api";
import { ConfirmDialog } from "./ConfirmDialog";
import { EmptyState, ErrorNotice, Skeleton } from "./Feedback";
import { Icon, type IconName } from "./Icon";

const EVENT_ICONS: Record<EventType, IconName> = {
  closure: "calendar",
  expense: "arrowDown",
  inflow: "arrowUp",
  payment_deferral: "clock",
  credit_line: "bank",
};

interface Props {
  events: AsyncResource<EventsResponse>;
  onRemoved: () => void;
}

interface PlanRowProps {
  event: PlannedEvent;
  removing: boolean;
  /** True while any removal is in flight: one at a time, so their state cannot clobber each other. */
  disabled: boolean;
  onRemove: () => void;
}

function PlanRow({ event, removing, disabled, onRemove }: PlanRowProps) {
  // A measure of an approved option cannot be removed alone: the whole option is withdrawn.
  const withdrawsOption = event.source === "agent";
  return (
    <li className="plan">
      <span className={`plan__icon plan__icon--${event.event_type}`} aria-hidden="true">
        <Icon name={EVENT_ICONS[event.event_type]} size={18} />
      </span>
      <div className="plan__body">
        <p className="plan__title">
          {event.description}
          <span className={`badge badge--source-${event.source}`}>
            {event.source === "chat" ? "From chat" : "From an approved option"}
          </span>
        </p>
        <p className="muted">
          {EVENT_TYPE_LABELS[event.event_type]} · {formatDateRange(event.date_from, event.date_to)}
          {event.amount_eur !== null ? ` · ${formatEur(event.amount_eur)}` : ""}
        </p>
        {event.original_utterance !== null ? (
          <details className="disclosure disclosure--inline">
            <summary>What you said</summary>
            <blockquote>{event.original_utterance}</blockquote>
          </details>
        ) : null}
      </div>
      <button
        type="button"
        className="btn btn--ghost btn--small"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`${withdrawsOption ? "Withdraw option" : "Remove plan"}: ${event.description}`}
      >
        <Icon name="trash" size={14} />
        {removing ? "Removing…" : withdrawsOption ? "Withdraw option" : "Remove"}
      </button>
    </li>
  );
}

/** "My plans": every planned event, removable. */
export function PlansList({ events, onRemoved }: Props) {
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [removeError, setRemoveError] = useState<ApiError | null>(null);
  // Measures created by an approved option are removed only after an explicit confirmation.
  const [awaitingConfirmationId, setAwaitingConfirmationId] = useState<number | null>(null);

  async function remove(id: number) {
    if (removingId !== null) return;
    setRemovingId(id);
    setRemoveError(null);
    try {
      await api.deleteEvent(id);
      setAwaitingConfirmationId(null);
      onRemoved();
    } catch (error) {
      setRemoveError(toApiError(error));
    } finally {
      setRemovingId(null);
    }
  }

  function requestRemoval(event: PlannedEvent) {
    if (event.source === "agent") {
      setRemoveError(null);
      setAwaitingConfirmationId(event.id);
    } else {
      void remove(event.id);
    }
  }

  const list = events.data?.events ?? null;
  const awaitingConfirmation = list?.find((event) => event.id === awaitingConfirmationId) ?? null;
  // The backend withdraws an approved option as a whole: every measure of the same decision goes with it.
  const withdrawnTogether =
    awaitingConfirmation === null
      ? []
      : (list ?? []).filter(
          (event) => event.decision_id !== null && event.decision_id === awaitingConfirmation.decision_id,
        );

  return (
    <section className="card" aria-labelledby="plans-title">
      <div className="card__head">
        <h2 id="plans-title" className="card__title">
          My plans
        </h2>
      </div>
      {removeError !== null && awaitingConfirmation === null ? (
        <ErrorNotice title="The plan was not removed" error={removeError} onDismiss={() => setRemoveError(null)} />
      ) : null}
      {events.error ? (
        <ErrorNotice title="Your plans could not be loaded" error={events.error} onRetry={events.reload} />
      ) : null}
      {list === null && events.loading ? <Skeleton label="Loading your plans" lines={2} /> : null}
      {list !== null && list.length === 0 ? (
        <EmptyState>No plans yet. Tell the assistant about a closure, a purchase or an expected payment.</EmptyState>
      ) : null}
      {list !== null && list.length > 0 ? (
        <ul className="plans">
          {list.map((event) => (
            <PlanRow
              key={event.id}
              event={event}
              removing={removingId === event.id}
              disabled={removingId !== null}
              onRemove={() => requestRemoval(event)}
            />
          ))}
        </ul>
      ) : null}
      <ConfirmDialog
        open={awaitingConfirmation !== null}
        title="Withdraw this approved option?"
        confirmLabel="Withdraw option"
        busyLabel="Withdrawing…"
        busy={removingId !== null}
        error={removeError}
        danger
        onConfirm={() => {
          if (awaitingConfirmation !== null) void remove(awaitingConfirmation.id);
        }}
        onCancel={() => {
          setAwaitingConfirmationId(null);
          setRemoveError(null);
        }}
      >
        {awaitingConfirmation !== null ? (
          <>
            <p>
              <strong>{awaitingConfirmation.description}</strong> was put in place by an option you approved. An
              option is withdrawn as a whole, so this removes {withdrawnTogether.length > 1 ? "all of" : ""} its
              measures from your forecast:
            </p>
            <ul>
              {withdrawnTogether.map((event) => (
                <li key={event.id}>{event.description}</li>
              ))}
            </ul>
            <p>
              The shortfall the option covered may return. The approval itself stays in your decision history.
            </p>
          </>
        ) : null}
      </ConfirmDialog>
    </section>
  );
}
