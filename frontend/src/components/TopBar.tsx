import { useEffect, useState } from "react";
import type { AssistantPanel } from "../hooks/useAssistantPanel";
import { useDebouncedCallback } from "../hooks/useDebouncedCallback";
import { formatDateRange } from "../lib/format";
import { ASSISTANT_PANEL_ID } from "../lib/layout";
import type { Dashboard, HorizonDays, ScenarioParams } from "../types/api";
import { Icon } from "./Icon";

const HORIZONS: HorizonDays[] = [30, 60, 90];
/** Typed values are committed once typing pauses, so intermediate keystrokes never reach the API. */
const COMMIT_DEBOUNCE_MS = 500;
/** Upper bound the API accepts for the safety buffer. */
const MAX_BUFFER_EUR = 1_000_000;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

type DataRange = Dashboard["data_range"];

interface Props {
  params: ScenarioParams;
  dataRange: DataRange | null;
  onChange: (patch: Partial<ScenarioParams>) => void;
  onResetDemo: () => void;
  assistant: AssistantPanel;
  /** What the closed assistant is waiting for or busy with ("needs your confirmation"); null when nothing is. */
  assistantAttention: string | null;
}

function parseBuffer(text: string): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 && value <= MAX_BUFFER_EUR ? value : null;
}

/** True for a complete date inside the bank data range (any complete date while the range is unknown). */
function isSelectableDate(text: string, dataRange: DataRange | null): boolean {
  if (!ISO_DATE.test(text)) return false;
  // ISO dates order lexicographically.
  return dataRange === null || (text >= dataRange.first_date && text <= dataRange.last_date);
}

/** Brand, the three scenario controls ("today", forecast horizon, safety buffer) and the assistant toggle. */
export function TopBar({ params, dataRange, onChange, onResetDemo, assistant, assistantAttention }: Props) {
  // Both typed fields keep a local draft: only a complete, valid value is passed on.
  const [dateText, setDateText] = useState(params.as_of_date);
  const [bufferText, setBufferText] = useState(String(params.buffer_eur));
  // A date input reports "" while a date is only partly typed; that is not an error yet.
  const dateInvalid = dateText !== "" && !isSelectableDate(dateText, dataRange);
  const bufferInvalid = parseBuffer(bufferText) === null;

  useEffect(() => setDateText(params.as_of_date), [params.as_of_date]);
  useEffect(() => setBufferText(String(params.buffer_eur)), [params.buffer_eur]);

  const commitDate = useDebouncedCallback((text: string) => {
    if (isSelectableDate(text, dataRange) && text !== params.as_of_date) onChange({ as_of_date: text });
  }, COMMIT_DEBOUNCE_MS);

  const commitBuffer = useDebouncedCallback((text: string) => {
    const value = parseBuffer(text);
    if (value !== null) onChange({ buffer_eur: value });
  }, COMMIT_DEBOUNCE_MS);

  return (
    <header className="topbar">
      <div className="stripe" aria-hidden="true" />
      <div className="topbar__inner">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <div>
            <p className="brand__wordmark">Commerzbank</p>
            <h1 className="brand__product">FinGuard</h1>
          </div>
        </div>

        <form className="controls" aria-label="Forecast settings" onSubmit={(event) => event.preventDefault()}>
          <label className="field">
            <span className="field__label">Today</span>
            <input
              type="date"
              className="field__input"
              value={dateText}
              min={dataRange?.first_date}
              max={dataRange?.last_date}
              required
              aria-invalid={dateInvalid}
              aria-describedby={dateInvalid ? "date-error" : undefined}
              onChange={(event) => {
                setDateText(event.target.value);
                commitDate(event.target.value);
              }}
              onBlur={() => {
                // An abandoned, half-typed date falls back to the date the forecast is showing.
                if (dateText === "") setDateText(params.as_of_date);
              }}
            />
            {dateInvalid && dataRange !== null ? (
              <span id="date-error" className="field__error" role="alert">
                Choose a date within {formatDateRange(dataRange.first_date, dataRange.last_date)}
              </span>
            ) : null}
          </label>

          <fieldset className="field">
            <legend className="field__label">Look ahead</legend>
            <div className="segmented">
              {HORIZONS.map((days) => (
                <label key={days} className="segmented__option">
                  <input
                    type="radio"
                    name="horizon"
                    value={days}
                    checked={params.horizon_days === days}
                    onChange={() => onChange({ horizon_days: days })}
                  />
                  <span>
                    {days}
                    <span className="segmented__unit"> days</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <label className="field">
            <span className="field__label">Safety buffer (€)</span>
            <input
              type="number"
              className="field__input field__input--number"
              inputMode="decimal"
              min={0}
              max={MAX_BUFFER_EUR}
              step={500}
              value={bufferText}
              aria-invalid={bufferInvalid}
              aria-describedby={bufferInvalid ? "buffer-error" : undefined}
              onChange={(event) => {
                setBufferText(event.target.value);
                commitBuffer(event.target.value);
              }}
            />
            {bufferInvalid ? (
              <span id="buffer-error" className="field__error" role="alert">
                Enter 0 to 1,000,000
              </span>
            ) : null}
          </label>

          <button type="button" className="btn btn--on-dark" onClick={onResetDemo}>
            <Icon name="refresh" size={14} />
            Reset demo
          </button>
        </form>

        <button
          type="button"
          ref={assistant.toggleRef}
          className="assistant-toggle"
          aria-expanded={assistant.open}
          aria-controls={ASSISTANT_PANEL_ID}
          onClick={assistant.toggle}
        >
          <Icon name="chat" size={18} />
          Assistant
          {assistantAttention !== null ? (
            <span className="assistant-toggle__dot">
              <span className="visually-hidden">{assistantAttention}</span>
            </span>
          ) : null}
        </button>
      </div>
    </header>
  );
}
