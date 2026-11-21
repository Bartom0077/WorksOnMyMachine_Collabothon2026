import type { AsyncResource } from "../hooks/useAsyncResource";
import type { PlanRemoval } from "../hooks/usePlanRemoval";
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
  term_deposit: "lock",
  fx_prebuy: "exchange",
};

/** Measures only an approved CFO recommendation creates; every other agent measure belongs to a rescue option. */
const RECOMMENDATION_EVENT_TYPES: ReadonlySet<EventType> = new Set(["term_deposit", "fx_prebuy"]);

/** What the owner approved to put an agent measure in place. */
function approvedKind(event: PlannedEvent): "option" | "recommendation" {
  return RECOMMENDATION_EVENT_TYPES.has(event.event_type) ? "recommendation" : "option";
}

interface Props {
  events: AsyncResource<EventsResponse>;
  removal: PlanRemoval;
}

interface PlanRowProps {
  event: PlannedEvent;
  removing: boolean;
  /** True while any removal is in flight. */
  disabled: boolean;
  onRemove: () => void;
}

function PlanRow({ event, removing, disabled, onRemove }: PlanRowProps) {
  // A measure of an approved option or recommendation cannot be removed alone: the whole approval is withdrawn.
  const withdrawLabel = event.source === "agent" ? `Withdraw ${approvedKind(event)}` : null;
  return (
    <li className="plan">
      <span className={`plan__icon plan__icon--${event.event_type}`} aria-hidden="true">
        <Icon name={EVENT_ICONS[event.event_type]} size={16} />
      </span>
      <div className="plan__body">
        <p className="plan__title">
          {event.description}
          <span className={`badge badge--source-${event.source}`}>
            {event.source === "chat" ? "From chat" : `From an approved ${approvedKind(event)}`}
          </span>
        </p>
        <div className="plan__meta">
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
      </div>
      <button
        type="button"
        className="btn btn--ghost btn--small"
        onClick={onRemove}
        disabled={disabled}
        aria-label={`${withdrawLabel ?? "Remove plan"}: ${event.description}`}
      >
        <Icon name="trash" size={14} />
        {removing ? "Removing…" : (withdrawLabel ?? "Remove")}
      </button>
    </li>
  );
}

/** "My plans" panel: every planned event, removable. */
export function PlansList({ events, removal }: Props) {
  const { removingId, awaitingConfirmationId, error: removeError } = removal;

  const list = events.data?.events ?? null;
  const awaitingConfirmation = list?.find((event) => event.id === awaitingConfirmationId) ?? null;
  // The backend withdraws an approval as a whole: every measure of the same decision goes with it.
  const withdrawnKind = awaitingConfirmation === null ? "option" : approvedKind(awaitingConfirmation);
  const withdrawnTogether =
    awaitingConfirmation === null
      ? []
      : (list ?? []).filter(
          (event) => event.decision_id !== null && event.decision_id === awaitingConfirmation.decision_id,
        );

  return (
    <section aria-labelledby="plans-title">
      <h2 id="plans-title" className="visually-hidden">
        My plans
      </h2>
      {removeError !== null && awaitingConfirmation === null ? (
        <ErrorNotice title="The plan was not removed" error={removeError} onDismiss={removal.dismissError} />
      ) : null}
      {events.error ? (
        <ErrorNotice title="Your plans could not be loaded" error={events.error} onRetry={events.reload} />
      ) : null}
      {list === null && events.loading ? <Skeleton label="Loading your plans" lines={2} /> : null}
      {list !== null && list.length === 0 ? (
        <EmptyState>No plans yet. Tell the assistant what is coming up.</EmptyState>
      ) : null}
      {list !== null && list.length > 0 ? (
        <ul className="plans">
          {list.map((event) => (
            <PlanRow
              key={event.id}
              event={event}
              removing={removingId === event.id}
              disabled={removingId !== null}
              onRemove={() => removal.request(event)}
            />
          ))}
        </ul>
      ) : null}
      <ConfirmDialog
        open={awaitingConfirmation !== null}
        title={`Withdraw this approved ${withdrawnKind}?`}
        confirmLabel={`Withdraw ${withdrawnKind}`}
        busyLabel="Withdrawing…"
        busy={removingId !== null}
        error={removeError}
        danger
        onConfirm={removal.confirm}
        onCancel={removal.cancel}
      >
        {awaitingConfirmation !== null ? (
          <>
            <p>
              <strong>{awaitingConfirmation.description}</strong> was put in place by{" "}
              {withdrawnKind === "option" ? "an option" : "a recommendation"} you approved. It is withdrawn as a
              whole, so this removes {withdrawnTogether.length > 1 ? "all of" : ""} its measures from your forecast:
            </p>
            <ul>
              {withdrawnTogether.map((event) => (
                <li key={event.id}>{event.description}</li>
              ))}
            </ul>
            <p>
              {withdrawnKind === "option" ? "The shortfall the option covered may return. " : ""}
              The approval itself stays in your decision history.
            </p>
          </>
        ) : null}
      </ConfirmDialog>
    </section>
  );
}
