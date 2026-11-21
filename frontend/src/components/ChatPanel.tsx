import { type FormEvent, useEffect, useRef, useState } from "react";
import type { Chat, ChatEntry } from "../hooks/useChat";
import { EVENT_TYPE_LABELS, formatDateRange, formatEur, humanizeToolName } from "../lib/format";
import type { EventDraft, ToolCall } from "../types/api";
import { ErrorNotice } from "./Feedback";
import { Icon } from "./Icon";

const SUGGESTED_PROMPTS = [
  "We are closing for holidays from 1 to 21 June",
  "I am buying an oven for €8,000 in July",
  "Why is my balance dropping?",
];
const CONFIRM_MESSAGE = "Yes, please include it in the forecast.";
const DEFAULT_PLACEHOLDER = "Describe a plan or ask a question…";
const CORRECTION_PLACEHOLDER = "Tell me what to change, e.g. “from 2 to 15 June”";

interface Props {
  chat: Chat;
}

function DraftCard({ draft }: { draft: EventDraft }) {
  return (
    <div className="draft">
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

interface EntryProps {
  entry: ChatEntry;
  actionable: boolean;
  onConfirm: () => void;
  onCorrect: () => void;
}

function Entry({ entry, actionable, onConfirm, onCorrect }: EntryProps) {
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
  return (
    <li className="message message--assistant">
      <span className="visually-hidden">Assistant said: </span>
      <p className="message__text">{entry.text}</p>
      {entry.pendingEvents.length > 0 ? (
        <div className="confirmation" role="group" aria-label="Plan awaiting your confirmation">
          {entry.pendingEvents.map((draft, index) => (
            <DraftCard key={index} draft={draft} />
          ))}
          <div className="confirmation__actions">
            <button type="button" className="btn btn--primary btn--small" onClick={onConfirm} disabled={!actionable}>
              <Icon name="check" size={14} />
              {many ? "Yes, include them" : "Yes, include it"}
            </button>
            <button type="button" className="btn btn--small" onClick={onCorrect} disabled={!actionable}>
              Correct it
            </button>
          </div>
        </div>
      ) : null}
      {entry.savedEvents.map((event) => (
        <p key={event.id} className="message__saved">
          <Icon name="check" size={12} />
          Added to your plans: {event.description}
        </p>
      ))}
      <DataUsed toolCalls={entry.toolCalls} />
    </li>
  );
}

/** Conversation with the assistant: describe plans, confirm recognised events, ask "why?". */
export function ChatPanel({ chat }: Props) {
  const [draft, setDraft] = useState("");
  const [placeholder, setPlaceholder] = useState(DEFAULT_PLACEHOLDER);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const lastEntryId = chat.entries[chat.entries.length - 1]?.id;

  useEffect(() => {
    const log = logRef.current;
    if (log !== null) log.scrollTop = log.scrollHeight;
  }, [lastEntryId, chat.sending, chat.failed]);

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
    <aside id="assistant" className="chat" aria-labelledby="chat-title">
      <header className="chat__head">
        <span className="chat__avatar" aria-hidden="true">
          <Icon name="chat" size={18} />
        </span>
        <div>
          <h2 id="chat-title" className="chat__title">
            Assistant
          </h2>
          <p className="chat__subtitle">Tell me your plans. I ask before saving anything.</p>
        </div>
        <a className="chat__back" href="#main">
          <Icon name="arrowUp" size={14} />
          Back to forecast
        </a>
      </header>

      <div ref={logRef} className="chat__log" role="log" aria-live="polite" aria-label="Conversation" tabIndex={0}>
        {chat.entries.length === 0 ? (
          <p className="chat__intro">
            Tell me what is coming up, such as a holiday closure, a big purchase or money you expect, and I will
            show how it changes your forecast.
          </p>
        ) : null}
        <ol className="chat__messages">
        {chat.entries.map((entry) => (
          <Entry
            key={entry.id}
            entry={entry}
            actionable={entry.id === lastEntryId && !chat.sending}
            onConfirm={() => sendPreset(CONFIRM_MESSAGE)}
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
