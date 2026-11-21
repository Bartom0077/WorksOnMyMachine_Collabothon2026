import { useCallback, useRef, useState } from "react";
import { type ApiError, api, toApiError } from "../api/client";
import type { EventDraft, PlannedEvent, ScenarioParams, ToolCall } from "../types/api";

export type ChatEntry =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "system"; text: string }
  | {
      id: number;
      role: "assistant";
      text: string;
      pendingEvents: EventDraft[];
      savedEvents: PlannedEvent[];
      pendingRemovals: PlannedEvent[];
      removedEvents: PlannedEvent[];
      toolCalls: ToolCall[];
    };

type NewChatEntry = ChatEntry extends infer Entry ? (Entry extends ChatEntry ? Omit<Entry, "id"> : never) : never;

interface FailedMessage {
  message: string;
  error: ApiError;
}

export interface Chat {
  entries: ChatEntry[];
  sending: boolean;
  failed: FailedMessage | null;
  /** Returns false when the message was not accepted (blank, or a reply is still pending). */
  send: (message: string) => boolean;
  retry: () => void;
  /** Forgets the server-side conversation and clears the transcript, leaving a note that explains why. */
  startNewConversation: (note: string) => void;
}

/** Chat transcript and POST /chat round-trips with conversation_id continuity. */
export function useChat(params: ScenarioParams, onEventsChanged: () => void): Chat {
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [sending, setSending] = useState(false);
  const [failed, setFailed] = useState<FailedMessage | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  // Incremented when a new conversation starts, so replies to the old one are dropped.
  const generationRef = useRef(0);
  const nextIdRef = useRef(1);

  const append = useCallback((entry: NewChatEntry) => {
    const id = nextIdRef.current++;
    setEntries((current) => [...current, { ...entry, id }]);
  }, []);

  const deliver = useCallback(
    async (message: string) => {
      const generation = generationRef.current;
      setSending(true);
      setFailed(null);
      try {
        const response = await api.postChat({
          ...params,
          message,
          conversation_id: conversationIdRef.current,
        });
        // A plan saved or removed by a reply to an abandoned conversation still changed the forecast.
        if (generation !== generationRef.current) {
          if (response.events_changed) onEventsChanged();
          return;
        }
        conversationIdRef.current = response.conversation_id;
        append({
          role: "assistant",
          text: response.reply,
          pendingEvents: response.pending_events,
          savedEvents: response.saved_events,
          // A backend from before chat removals does not send these two fields.
          pendingRemovals: response.pending_removals ?? [],
          removedEvents: response.removed_events ?? [],
          toolCalls: response.tool_calls,
        });
        if (response.events_changed) {
          append({ role: "system", text: "Forecast updated with your plans." });
          onEventsChanged();
        }
      } catch (error) {
        if (generation === generationRef.current) setFailed({ message, error: toApiError(error) });
      } finally {
        if (generation === generationRef.current) setSending(false);
      }
    },
    [params, append, onEventsChanged],
  );

  const send = useCallback(
    (message: string) => {
      const text = message.trim();
      if (text === "" || sending) return false;
      append({ role: "user", text });
      void deliver(text);
      return true;
    },
    [sending, append, deliver],
  );

  const retry = useCallback(() => {
    if (failed !== null && !sending) void deliver(failed.message);
  }, [failed, sending, deliver]);

  const startNewConversation = useCallback((note: string) => {
    generationRef.current += 1;
    conversationIdRef.current = null;
    setSending(false);
    setFailed(null);
    setEntries([{ id: nextIdRef.current++, role: "system", text: note }]);
  }, []);

  return { entries, sending, failed, send, retry, startNewConversation };
}
