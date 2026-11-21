import { type KeyboardEvent, type ReactNode, useId, useRef } from "react";

export interface WorkspaceTab<Id extends string> {
  id: Id;
  label: string;
  /** Number shown next to the label; null while it is not known yet. */
  count: number | null;
  content: ReactNode;
}

interface Props<Id extends string> {
  /** Accessible name of the tab list. */
  label: string;
  tabs: WorkspaceTab<Id>[];
  selectedId: Id;
  onSelect: (id: Id) => void;
}

/** Index of the tab an arrow / Home / End key moves to, or null for any other key. */
function targetIndex(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
      return (current + 1) % count;
    case "ArrowLeft":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/**
 * One card with a WAI-ARIA tab list (roving tabindex, selection follows focus) and the panel of the
 * selected tab; the other panels are not rendered. The panel is a Tab stop of its own, so a panel without
 * controls (an empty list) can still be reached from the keyboard.
 */
export function WorkspaceTabs<Id extends string>({ label, tabs, selectedId, onSelect }: Props<Id>) {
  const baseId = useId();
  const tabRefs = useRef(new Map<Id, HTMLButtonElement>());
  const selected = tabs.find((tab) => tab.id === selectedId);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const current = tabs.findIndex((tab) => tab.id === selectedId);
    const target = targetIndex(event.key, current, tabs.length);
    const next = target === null ? undefined : tabs[target];
    if (next === undefined) return;
    event.preventDefault();
    onSelect(next.id);
    tabRefs.current.get(next.id)?.focus();
  }

  return (
    <div className="card tabs">
      <div className="tabs__list" role="tablist" aria-label={label} onKeyDown={onKeyDown}>
        {tabs.map((tab) => {
          const isSelected = tab.id === selectedId;
          return (
            <button
              key={tab.id}
              ref={(element) => {
                if (element === null) tabRefs.current.delete(tab.id);
                else tabRefs.current.set(tab.id, element);
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              className="tabs__tab"
              aria-selected={isSelected}
              aria-controls={isSelected ? `${baseId}-panel-${tab.id}` : undefined}
              tabIndex={isSelected ? 0 : -1}
              onClick={() => onSelect(tab.id)}
            >
              {tab.label}{" "}
              {tab.count !== null ? <span className="tabs__count">{tab.count}</span> : null}
            </button>
          );
        })}
      </div>
      {selected !== undefined ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${selected.id}`}
          className="tabs__panel"
          aria-labelledby={`${baseId}-tab-${selected.id}`}
          tabIndex={0}
        >
          {selected.content}
        </div>
      ) : null}
    </div>
  );
}
