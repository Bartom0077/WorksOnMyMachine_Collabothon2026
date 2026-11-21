import { useEffect, useState } from "react";
import { useDebouncedCallback } from "../hooks/useDebouncedCallback";
import type { Dashboard, HorizonDays, ScenarioParams } from "../types/api";
import { Icon } from "./Icon";

const HORIZONS: HorizonDays[] = [30, 60, 90];
const BUFFER_DEBOUNCE_MS = 500;

interface Props {
  params: ScenarioParams;
  dataRange: Dashboard["data_range"] | null;
  onChange: (patch: Partial<ScenarioParams>) => void;
  onResetDemo: () => void;
}

function parseBuffer(text: string): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

/** Brand plus the three scenario controls: "today", forecast horizon and safety buffer. */
export function TopBar({ params, dataRange, onChange, onResetDemo }: Props) {
  const [bufferText, setBufferText] = useState(String(params.buffer_eur));
  const bufferInvalid = parseBuffer(bufferText) === null;

  useEffect(() => setBufferText(String(params.buffer_eur)), [params.buffer_eur]);

  const commitBuffer = useDebouncedCallback((text: string) => {
    const value = parseBuffer(text);
    if (value !== null) onChange({ buffer_eur: value });
  }, BUFFER_DEBOUNCE_MS);

  return (
    <header className="topbar">
      <div className="stripe" aria-hidden="true" />
      <div className="topbar__inner">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true" />
          <div>
            <p className="brand__wordmark">Commerzbank</p>
            <h1 className="brand__product">Autonomous CFO</h1>
          </div>
        </div>

        <form className="controls" aria-label="Forecast settings" onSubmit={(event) => event.preventDefault()}>
          <label className="field">
            <span className="field__label">Today</span>
            <input
              type="date"
              className="field__input"
              value={params.as_of_date}
              min={dataRange?.first_date}
              max={dataRange?.last_date}
              required
              onChange={(event) => {
                if (event.target.value !== "") onChange({ as_of_date: event.target.value });
              }}
            />
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
                Enter 0 or more
              </span>
            ) : null}
          </label>

          <button type="button" className="btn btn--on-dark" onClick={onResetDemo}>
            <Icon name="refresh" size={14} />
            Reset demo
          </button>
        </form>
      </div>
    </header>
  );
}
