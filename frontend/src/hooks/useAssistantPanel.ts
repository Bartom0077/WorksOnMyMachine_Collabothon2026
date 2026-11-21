import { type RefObject, useCallback, useEffect, useRef, useState } from "react";
import { isSingleColumn } from "../lib/layout";

/** Whether the assistant panel is open on a first visit; the first screen is meant to be calm. */
const ASSISTANT_OPEN_BY_DEFAULT = false;
const STORAGE_KEY = "autonomous-cfo.assistant-open";

/** The owner's last explicit choice, or the default when there is none or the storage cannot be read. */
function readStoredChoice(): boolean {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // Storage can be unavailable (private mode, blocked site data): the default applies.
  }
  return ASSISTANT_OPEN_BY_DEFAULT;
}

function storeChoice(open: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(open));
  } catch {
    // Storage can be unavailable: the choice then lasts for this visit only.
  }
}

/** True when no part of the element is inside the viewport (it was scrolled past, or not reached yet). */
function isOffScreen(element: HTMLElement | null): boolean {
  if (element === null) return false;
  const { top, bottom } = element.getBoundingClientRect();
  return bottom <= 0 || top >= window.innerHeight;
}

export interface AssistantPanel {
  open: boolean;
  /** The button in the top bar that shows and hides the panel. */
  toggleRef: RefObject<HTMLButtonElement>;
  panelRef: RefObject<HTMLElement>;
  /** The message box; it takes the focus when the panel is opened. */
  inputRef: RefObject<HTMLTextAreaElement>;
  /** Shows or hides the panel; an open panel that is scrolled out of sight is brought back instead of hidden. */
  toggle: () => void;
  /** Closes the panel from inside it and hands the focus back to the toggle. */
  close: () => void;
}

/** Open/closed state of the docked assistant panel: remembered between visits, with focus following the panel. */
export function useAssistantPanel(): AssistantPanel {
  const [open, setOpen] = useState(readStoredChoice);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Set when the owner opens the panel; a panel that is open on load must not take the focus.
  const focusOnOpenRef = useRef(false);

  /** Puts the cursor in the message box, with the whole panel on screen. */
  const reveal = useCallback(() => {
    // In the single-column layout the panel sits below everything else: bring all of it into view.
    const stacked = isSingleColumn();
    if (stacked) panelRef.current?.scrollIntoView();
    inputRef.current?.focus({ preventScroll: stacked });
  }, []);

  useEffect(() => {
    if (!open || !focusOnOpenRef.current) return;
    focusOnOpenRef.current = false;
    reveal();
  }, [open, reveal]);

  const toggle = useCallback(() => {
    // Stacked under the content, an open panel can be a long way off screen: hiding it then would look like
    // a dead button, so the toggle leads to it. The panel's own close button stays the way to hide it.
    if (open && isSingleColumn() && isOffScreen(panelRef.current)) {
      reveal();
      return;
    }
    focusOnOpenRef.current = !open;
    setOpen(!open);
    storeChoice(!open);
  }, [open, reveal]);

  const close = useCallback(() => {
    setOpen(false);
    storeChoice(false);
    toggleRef.current?.focus();
  }, []);

  return { open, toggleRef, panelRef, inputRef, toggle, close };
}
