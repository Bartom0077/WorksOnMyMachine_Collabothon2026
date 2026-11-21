import { type FormEvent, useEffect, useRef, useState } from "react";
import type { AssistantPanel } from "../hooks/useAssistantPanel";
import type { Chat, ChatEntry } from "../hooks/useChat";
import { EVENT_TYPE_LABELS, formatDateRange, formatEur, humanizeToolName } from "../lib/format";
import { ASSISTANT_PANEL_ID } from "../lib/layout";
import type { PlannedEvent, ToolCall } from "../types/api";
import { ErrorNotice } from "./Feedback";
import { Icon } from "./Icon";

const SUGGESTED_PROMPTS = [
  "We are closing for holidays from 1 to 21 June",
  "I am buying an oven for €8,000 in July",
  "Why is my balance dropping?",
];
const CONFIRM_MESSAGE = "Yes, please include it in the forecast.";
const REMOVE_MESSAGE = "Yes, please remove it from my plans.";
const REMOVE_ALL_MESSAGE = "Yes, please remove them from my plans.";
const KEEP_MESSAGE = "No, keep it.";
const KEEP_ALL_MESSAGE = "No, keep them.";
const DEFAULT_PLACEHOLDER = "Describe a plan or ask a question…";
const CORRECTION_PLACEHOLDER = "Tell me what to change, e.g. “from 2 to 15 June”";

interface Props {
  chat: Chat;
  /** Open/closed state of the panel; a closed panel stays mounted, so the conversation and the draft survive. */
  panel: AssistantPanel;
}

/** What a plan card shows: a checked proposal and a saved plan both have these fields. */
type PlanSummary = Pick<PlannedEvent, "event_type" | "date_from" | "date_to" | "amount_eur" | "description">;

function PlanCard({ plan: draft, removal = false }: { plan: PlanSummary; removal?: boolean }) {
  return (
    <div className={removal ? "draft draft--removal" : "draft"}>
      <p className="draft__type">{EVENT_TYPE_LABELS[draft.event_type]}</p>
      <dl className="draft__facts">
        <div>
          <dt>When</dt>
          <dd>{formatDateRange(draft.date_from, draft.date_to)}</dd>
        </div>
        {draft.amount_eur !== null ? (
          <div>
            <dt>Amount</dt>
            <dd>{formatEur(draft.amount_eur)}</dd>
          </div>
        ) : null}
        <div>
          <dt>What</dt>
          <dd>{draft.description}</dd>
        </div>
      </dl>
    </div>
  );
}

function DataUsed({ toolCalls }: { toolCalls: ToolCall[] }) {
  if (toolCalls.length === 0) return null;
  const names = [...new Set(toolCalls.map((call) => humanizeToolName(call.name) + (call.ok ? "" : " (failed)")))];
  return (
    <p className="message__meta">
      <Icon name="database" size={12} />
      Data used: {names.join(", ")}
    </p>
  );
}

interface PlanConfirmationProps {
  /** Accessible name of the group. */
  label: string;
  /** Visible question above the plans; the add flow asks it in the assistant's own message instead. */
  title?: string;
  plans: PlanSummary[];
  removal?: boolean;
  /** False once the owner has answered or a newer message exists, so a card cannot be answered twice. */
  actionable: boolean;
  acceptLabel: string;
  declineLabel: string;
  onAccept: () => void;
  onDecline: () => void;
}

/** Plans the assistant asks about (include them? remove them?) with the two answers as buttons. */
function PlanConfirmation(props: PlanConfirmationProps) {
  const { label, title, plans, removal = false, actionable, acceptLabel, declineLabel, onAccept, onDecline } = props;
  return (
    <div className="confirmation" role="group" aria-label={label}>
      {title !== undefined ? <p className="confirmation__title">{title}</p> : null}
      {plans.map((plan, index) => (
        <PlanCard key={index} plan={plan} removal={removal} />
      ))}
      <div className="confirmation__actions">
        <button
          type="button"
          className={`btn btn--small ${removal ? "btn--danger" : "btn--primary"}`}
          onClick={onAccept}
          disabled={!actionable}
        >
          <Icon name={removal ? "trash" : "check"} size={14} />
          {acceptLabel}
        </button>
        <button type="button" className="btn btn--small" onClick={onDecline} disabled={!actionable}>
          {declineLabel}
        </button>
      </div>
    </div>
  );
}

interface EntryProps {
  entry: ChatEntry;
  actionable: boolean;
  onSend: (message: string) => void;
  onCorrect: () => void;
}

function Entry({ entry, actionable, onSend, onCorrect }: EntryProps) {
  if (entry.role === "system") {
    return (
      <li className="message message--system">
        <Icon name="refresh" size={12} />
        {entry.text}
      </li>
    );
  }
  if (entry.role === "user") {
    return (
      <li className="message message--user">
        <span className="visually-hidden">You said: </span>
        {entry.text}
      </li>
    );
  }
  const many = entry.pendingEvents.length > 1;
  const manyRemovals = entry.pendingRemovals.length > 1;
  return (
    <li className="message message--assistant">
      <span className="visually-hidden">Assistant said: </span>
      <p className="message__text">{entry.text}</p>
      {entry.pendingEvents.length > 0 ? (
        <PlanConfirmation
          label="Plan awaiting your confirmation"
          plans={entry.pendingEvents}
          actionable={actionable}
          acceptLabel={many ? "Yes, include them" : "Yes, include it"}
          declineLabel="Correct it"
          onAccept={() => onSend(CONFIRM_MESSAGE)}
          onDecline={onCorrect}
        />
      ) : null}
      {entry.pendingRemovals.length > 0 ? (
        <PlanConfirmation
          label="Plan awaiting removal"
          title={manyRemovals ? "Remove these plans?" : "Remove this plan?"}
          plans={entry.pendingRemovals}
          removal
          actionable={actionable}
          acceptLabel={manyRemovals ? "Yes, remove them" : "Yes, remove it"}
          declineLabel={manyRemovals ? "Keep them" : "Keep it"}
          onAccept={() => onSend(manyRemovals ? REMOVE_ALL_MESSAGE : REMOVE_MESSAGE)}
          onDecline={() => onSend(manyRemovals ? KEEP_ALL_MESSAGE : KEEP_MESSAGE)}
        />
      ) : null}
      {entry.savedEvents.map((event) => (
        <p key={event.id} className="message__saved">
          <Icon name="check" size={12} />
          Added to your plans: {event.description}
        </p>
      ))}
      {entry.removedEvents.map((event) => (
        <p key={event.id} className="message__saved">
          <Icon name="trash" size={12} />
          Removed from your plans: {event.description}
        </p>
      ))}
      <DataUsed toolCalls={entry.toolCalls} />
    </li>
  );
}

/** Conversation with the assistant: describe plans, confirm recognised events, ask "why?". */
export function ChatPanel({ chat, panel }: Props) {
  const [draft, setDraft] = useState("");
  const [placeholder, setPlaceholder] = useState(DEFAULT_PLACEHOLDER);
  const { open, inputRef } = panel;
  const logRef = useRef<HTMLDivElement>(null);
  const lastEntryId = chat.entries[chat.entries.length - 1]?.id;

  // Also on opening: a hidden log cannot be scrolled, so messages that arrived meanwhile are caught up with here.
  useEffect(() => {
    const log = logRef.current;
    if (log !== null) log.scrollTop = log.scrollHeight;
  }, [lastEntryId, chat.sending, chat.failed, open]);

  /** Sends a ready-made message (suggested prompt, confirmation) without touching what the owner is typing. */
  function sendPreset(text: string) {
    if (chat.send(text)) setPlaceholder(DEFAULT_PLACEHOLDER);
  }

  /** Sends the typed message; the draft is kept when it was not accepted (a reply is still pending). */
  function submitDraft() {
    if (!chat.send(draft)) return;
    setDraft("");
    setPlaceholder(DEFAULT_PLACEHOLDER);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    submitDraft();
  }

  function startCorrection() {
    setPlaceholder(CORRECTION_PLACEHOLDER);
    inputRef.current?.focus();
  }

  return (
    <aside id={ASSISTANT_PANEL_ID} ref={panel.panelRef} className="chat" aria-labelledby="chat-title" hidden={!open}>
      <header className="chat__head">
        <span className="chat__avatar" aria-hidden="true">
          <Icon name="chat" size={18} />
        </span>
        <div>
          <h2 id="chat-title" className="chat__title">
            Assistant
          </h2>
          <p className="chat__subtitle">I ask before saving anything.</p>
        </div>
        <button type="button" className="chat__close" aria-label="Close assistant" onClick={panel.close}>
          <Icon name="close" size={18} />
        </button>
      </header>

      <div ref={logRef} className="chat__log" role="log" aria-live="polite" aria-label="Conversation" tabIndex={0}>
        {chat.entries.length === 0 ? (
          <p className="chat__intro">Tell me a plan and I will show how it changes your forecast.</p>
        ) : null}
        <ol className="chat__messages">
          {chat.entries.map((entry) => (
            <Entry
              key={entry.id}
              entry={entry}
              actionable={entry.id === lastEntryId && !chat.sending}
              onSend={sendPreset}
              onCorrect={startCorrection}
            />
          ))}
          {chat.sending ? (
            <li className="message message--assistant message--typing">
              <span className="typing" aria-hidden="true">
                <span />
                <span />
                <span />
              </span>
              <span className="visually-hidden">The assistant is typing…</span>
            </li>
          ) : null}
        </ol>
      </div>

      {chat.failed !== null ? (
        <div className="chat__error">
          <ErrorNotice
            title={
              chat.failed.error.code === "assistant_unavailable"
                ? "The assistant is unavailable right now"
                : "Your message was not delivered"
            }
            error={chat.failed.error}
            onRetry={chat.retry}
          />
        </div>
      ) : null}

      <ul className="prompts" aria-label="Suggested messages">
        {SUGGESTED_PROMPTS.map((prompt) => (
          <li key={prompt}>
            <button type="button" className="prompt" onClick={() => sendPreset(prompt)} disabled={chat.sending}>
              {prompt}
            </button>
          </li>
        ))}
      </ul>

      <form className="chat__form" onSubmit={handleSubmit}>
        <label className="visually-hidden" htmlFor="chat-input">
          Message to the assistant
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          className="chat__input"
          rows={2}
          value={draft}
          placeholder={placeholder}
          aria-describedby={chat.sending && draft.trim() !== "" ? "chat-wait-hint" : undefined}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter confirms an IME composition; only a plain Enter outside one sends the message.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submitDraft();
            }
          }}
        />
        <button
          type="submit"
          className="btn btn--primary btn--icon"
          disabled={chat.sending || draft.trim() === ""}
          aria-label="Send message"
        >
          <Icon name="send" size={18} />
        </button>
        {chat.sending && draft.trim() !== "" ? (
          <p id="chat-wait-hint" className="chat__hint">
            Waiting for the assistant’s reply. You can send this as soon as it arrives.
          </p>
        ) : null}
      </form>
    </aside>
  );
}
