import { useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { AsyncResource } from "../hooks/useAsyncResource";
import { EVENT_TYPE_LABELS, formatDateRange, formatEur } from "../lib/format";
import type { EventType, EventsResponse, PlannedEvent } from "../types/api";
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
  onRemove: () => void;
}

function PlanRow({ event, removing, onRemove }: PlanRowProps) {
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
        disabled={removing}
        aria-label={`Remove plan: ${event.description}`}
      >
        <Icon name="trash" size={14} />
        {removing ? "Removing…" : "Remove"}
      </button>
    </li>
  );
}

/** "My plans": every planned event, removable. */
export function PlansList({ events, onRemoved }: Props) {
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [removeError, setRemoveError] = useState<ApiError | null>(null);

  async function remove(id: number) {
    setRemovingId(id);
    setRemoveError(null);
    try {
      await api.deleteEvent(id);
      onRemoved();
    } catch (error) {
      setRemoveError(toApiError(error));
    } finally {
      setRemovingId(null);
    }
  }

  const list = events.data?.events ?? null;

  return (
    <section className="card" aria-labelledby="plans-title">
      <div className="card__head">
        <h2 id="plans-title" className="card__title">
          My plans
        </h2>
      </div>
      {removeError ? (
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
              onRemove={() => void remove(event.id)}
            />
          ))}
        </ul>
      ) : null}
    </section>
  );
}
