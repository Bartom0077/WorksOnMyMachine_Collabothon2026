import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { type ChartRow, buildChartModel } from "../lib/chartModel";
import { dateToTime, formatDate, formatEur, formatEurCompact, formatEurWhole, formatTime } from "../lib/format";
import type { Dashboard, ForecastDay, RescueOption } from "../types/api";

const COLORS = {
  // Validated as a categorical trio (colour-vision-deficiency separation and contrast on white).
  history: "#0a6c94",
  baseline: "#a81e1e",
  option: "#22a05c",
  axis: "#516870",
  grid: "#e3eaec",
  zero: "#0b2a33",
  buffer: "#8a6100",
  credit: "#5b4bb3",
  closure: "#7d8f96",
  expense: "#a81e1e",
  inflow: "#22a05c",
  surface: "#ffffff",
};
const BASELINE_DASH = "7 5";
const OPTION_DASH = "2 4";
const MAX_TOOLTIP_ITEMS = 4;

interface Props {
  dashboard: Dashboard;
  previewOption: RescueOption | null;
}

interface TooltipCardProps {
  row: ChartRow;
  baselineLabel: string;
  day: ForecastDay | undefined;
  optionId: string | null;
}

function largestItems(day: ForecastDay) {
  return [...day.items].sort((a, b) => Math.abs(b.amount_eur) - Math.abs(a.amount_eur)).slice(0, MAX_TOOLTIP_ITEMS);
}

function TooltipCard({ row, baselineLabel, day, optionId }: TooltipCardProps) {
  const isForecast = row.history === undefined;
  return (
    <div className="chart-tooltip">
      <p className="chart-tooltip__date">{formatDate(row.date)}</p>
      <dl>
        {row.history !== undefined ? (
          <div>
            <dt>Balance</dt>
            <dd>{formatEur(row.history)}</dd>
          </div>
        ) : null}
        {isForecast && row.baseline !== undefined ? (
          <div>
            <dt>{baselineLabel}</dt>
            <dd>{formatEur(row.baseline)}</dd>
          </div>
        ) : null}
        {isForecast && row.option !== undefined && optionId !== null ? (
          <div>
            <dt>With option {optionId}</dt>
            <dd>{formatEur(row.option)}</dd>
          </div>
        ) : null}
      </dl>
      {day && day.items.length > 0 ? (
        <ul className="chart-tooltip__items">
          {largestItems(day).map((item, index) => (
            <li key={`${item.category}-${index}`}>
              <span>{item.label}</span>
              <span>{formatEur(item.amount_eur)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function LegendItem({ color, dash, label }: { color: string; dash?: string; label: string }) {
  return (
    <li>
      <svg width="28" height="8" aria-hidden="true" focusable="false">
        <line x1="1" y1="4" x2="27" y2="4" stroke={color} strokeWidth="2.5" strokeDasharray={dash} strokeLinecap="round" />
      </svg>
      {label}
    </li>
  );
}

/**
 * "If you do nothing" only makes sense while there is something to do: once no options are offered
 * (nothing is wrong, or a measure was approved) the same line is simply the forecast.
 */
function needsAction(dashboard: Dashboard): boolean {
  return dashboard.options.length > 0;
}

function describeChart(dashboard: Dashboard, previewOption: RescueOption | null): string {
  const { risk } = dashboard;
  const forecastSubject = needsAction(dashboard) ? "If you do nothing, the forecast" : "The forecast";
  const parts = [
    `Balance today is ${formatEur(dashboard.current_balance_eur)}.`,
    `${forecastSubject} for the next ${dashboard.horizon_days} days reaches its lowest point of ` +
      `${formatEur(risk.min_balance_eur)} on ${formatDate(risk.min_balance_date)}.`,
  ];
  if (previewOption !== null) {
    parts.push(
      `With option ${previewOption.id} (${previewOption.title}) the lowest point is ` +
        `${formatEur(previewOption.new_min_balance_eur)} on ${formatDate(previewOption.new_min_balance_date)}.`,
    );
  }
  return parts.join(" ");
}

/** Balance history and forecast lines with event overlays, plus a table alternative. */
export default function BalanceChart({ dashboard, previewOption }: Props) {
  const model = useMemo(() => buildChartModel(dashboard, previewOption), [dashboard, previewOption]);
  const rowsByTime = useMemo(() => new Map(model.rows.map((row) => [row.time, row])), [model.rows]);
  const daysByDate = useMemo(
    () => new Map(dashboard.forecast.days.map((day) => [day.date, day])),
    [dashboard.forecast.days],
  );
  const optionByDate = useMemo(
    () => new Map(previewOption?.forecast.map((point) => [point.date, point.balance_eur])),
    [previewOption],
  );

  const { risk } = dashboard;
  const bufferLevel = risk.floor_eur + dashboard.buffer_eur;
  const minTime = dateToTime(risk.min_balance_date);
  const lastTime = model.rows[model.rows.length - 1]?.time ?? minTime;
  const firstTime = model.rows[0]?.time ?? minTime;
  const minLabelSide = minTime > firstTime + (lastTime - firstTime) * 0.8 ? "left" : "right";
  const optionCreditLimit = previewOption?.credit_limit_eur ?? null;
  const summary = describeChart(dashboard, previewOption);
  // A healthy forecast simply continues the history colour; red is reserved for a forecast that needs attention.
  const baselineColor = risk.status === "GREEN" ? COLORS.history : COLORS.baseline;
  const baselineLabel = needsAction(dashboard) ? "If you do nothing" : "Forecast";
  const baselineLegend = needsAction(dashboard) ? "Forecast if you do nothing" : "Forecast";
  // With a credit line the buffer is measured from the limit, so the line sits below zero: say what it is.
  const hasCreditLine = risk.credit_limit_eur !== null;
  const bufferLabel = hasCreditLine
    ? `Safety buffer (${formatEurWhole(dashboard.buffer_eur)} above the credit limit)`
    : "Safety buffer";
  // Labels sit above their line, away from the busiest part of the plot: without a credit line the buffer is
  // near the history line (left), with one it is near the forecast's low point (right).
  const bufferLabelPosition = hasCreditLine ? "insideBottomLeft" : "insideBottomRight";

  return (
    <section className="card" aria-labelledby="chart-title">
      <div className="card__head">
        <h2 id="chart-title" className="card__title">
          Balance: past {dashboard.history.length} days and next {dashboard.horizon_days} days
        </h2>
      </div>

      <ul className="legend" aria-label="Chart legend">
        <LegendItem color={COLORS.history} label="Past balance" />
        <LegendItem color={baselineColor} dash={BASELINE_DASH} label={baselineLegend} />
        {previewOption !== null ? (
          <LegendItem
            color={COLORS.option}
            dash={OPTION_DASH}
            label={`Forecast with option ${previewOption.id}: ${previewOption.title}`}
          />
        ) : null}
        {model.closures.length > 0 ? (
          <li>
            <span className="legend__band" aria-hidden="true" />
            Closed
          </li>
        ) : null}
      </ul>

      <figure className="chart">
        <div className="chart__plot">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={model.rows} margin={{ top: 24, right: 16, bottom: 4, left: 0 }} accessibilityLayer>
              <CartesianGrid vertical={false} stroke={COLORS.grid} />
              <XAxis
                dataKey="time"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                ticks={model.ticks}
                tickFormatter={(time: number) => formatTime(time, "short")}
                tick={{ fill: COLORS.axis, fontSize: 12 }}
                tickLine={false}
                axisLine={{ stroke: COLORS.grid }}
                minTickGap={24}
              />
              <YAxis
                width={52}
                type="number"
                ticks={model.balanceTicks}
                domain={[model.balanceTicks[0] ?? 0, model.balanceTicks[model.balanceTicks.length - 1] ?? 0]}
                tickFormatter={formatEurCompact}
                tick={{ fill: COLORS.axis, fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />

              {model.closures.map((band) => (
                <ReferenceArea
                  key={band.id}
                  x1={band.start}
                  x2={band.end}
                  fill={COLORS.closure}
                  fillOpacity={0.16}
                  stroke="none"
                  label={{ value: "Closed", position: "insideTop", fill: COLORS.axis, fontSize: 12 }}
                />
              ))}

              <ReferenceLine y={0} stroke={COLORS.zero} strokeWidth={1} />
              <ReferenceLine
                y={bufferLevel}
                stroke={COLORS.buffer}
                strokeDasharray="3 3"
               
                label={{ value: bufferLabel, position: bufferLabelPosition, fill: COLORS.buffer, fontSize: 12 }}
              />
              {risk.credit_limit_eur !== null ? (
                <ReferenceLine
                  y={-risk.credit_limit_eur}
                  stroke={COLORS.credit}
                  strokeDasharray="8 3 2 3"
                 
                  label={{ value: "Credit limit", position: "insideBottomLeft", fill: COLORS.credit, fontSize: 12 }}
                />
              ) : null}
              {optionCreditLimit !== null ? (
                <ReferenceLine
                  y={-optionCreditLimit}
                  stroke={COLORS.credit}
                  strokeDasharray="8 3 2 3"
                 
                  label={{
                    value: `Credit limit with option ${previewOption?.id ?? ""}`,
                    position: "insideBottomLeft",
                    fill: COLORS.credit,
                    fontSize: 12,
                  }}
                />
              ) : null}
              <ReferenceLine
                x={model.todayTime}
                stroke={COLORS.axis}
                strokeDasharray="2 2"
                label={{ value: "Today", position: "top", fill: COLORS.zero, fontSize: 12, fontWeight: 600 }}
              />

              <Tooltip
                isAnimationActive={false}
                cursor={{ stroke: COLORS.axis, strokeWidth: 1 }}
                content={({ active, label }) => {
                  const row = active && typeof label === "number" ? rowsByTime.get(label) : undefined;
                  if (row === undefined) return null;
                  return (
                    <TooltipCard
                      row={row}
                      baselineLabel={baselineLabel}
                      day={daysByDate.get(row.date)}
                      optionId={previewOption?.id ?? null}
                    />
                  );
                }}
              />

              <Line
                dataKey="history"
                name="Past balance"
                stroke={COLORS.history}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, stroke: COLORS.surface, strokeWidth: 2 }}
                isAnimationActive={false}
              />
              <Line
                dataKey="baseline"
                name={baselineLegend}
                stroke={baselineColor}
                strokeWidth={2}
                strokeDasharray={BASELINE_DASH}
                dot={false}
                activeDot={{ r: 4, stroke: COLORS.surface, strokeWidth: 2 }}
                isAnimationActive={false}
              />
              {previewOption !== null ? (
                <Line
                  dataKey="option"
                  name={`Forecast with option ${previewOption.id}`}
                  stroke={COLORS.option}
                  strokeWidth={2.5}
                  strokeDasharray={OPTION_DASH}
                  strokeLinecap="round"
                  dot={false}
                  activeDot={{ r: 4, stroke: COLORS.surface, strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              ) : null}

              {model.markers.map((marker) => (
                <ReferenceDot
                  key={marker.id}
                  x={marker.time}
                  y={marker.balance}
                  r={6}
                  fill={marker.eventType === "expense" ? COLORS.expense : COLORS.inflow}
                  stroke={COLORS.surface}
                  strokeWidth={2}
                  label={{
                    value: marker.eventType === "expense" ? "▼ Expense" : "▲ Inflow",
                    position: "top",
                    fill: COLORS.zero,
                    fontSize: 12,
                  }}
                />
              ))}
              <ReferenceDot
                x={minTime}
                y={risk.min_balance_eur}
                r={5}
                fill={baselineColor}
                stroke={COLORS.surface}
                strokeWidth={2}
                label={{
                  value: `Low ${formatEurWhole(risk.min_balance_eur)}`,
                  position: minLabelSide,
                  fill: COLORS.zero,
                  fontSize: 12,
                  fontWeight: 600,
                }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <figcaption className="chart__caption">{summary}</figcaption>
      </figure>
      <p className="muted">
        Day-to-day revenue and running costs are estimated from your average over the last{" "}
        {dashboard.forecast.daily_averages.lookback_days} days ({dashboard.forecast.daily_averages.open_days} of them
        open); fixed payments and your plans are added on their due dates.
      </p>

      <details className="disclosure">
        <summary>Show the forecast as a table</summary>
        <div className="table-scroll" tabIndex={0} role="region" aria-label="Forecast table">
          <table className="table">
            <caption className="visually-hidden">Projected end-of-day balance per day</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col" className="num">
                  {baselineLabel}
                </th>
                {previewOption !== null ? (
                  <th scope="col" className="num">
                    With option {previewOption.id}
                  </th>
                ) : null}
                <th scope="col" className="num">
                  Money in
                </th>
                <th scope="col" className="num">
                  Money out
                </th>
              </tr>
            </thead>
            <tbody>
              {dashboard.forecast.days.map((day) => {
                const optionBalance = optionByDate.get(day.date);
                return (
                  <tr key={day.date}>
                    <th scope="row">{formatDate(day.date)}</th>
                    <td className="num">{formatEur(day.balance_eur)}</td>
                    {previewOption !== null ? (
                      <td className="num">{optionBalance === undefined ? "–" : formatEur(optionBalance)}</td>
                    ) : null}
                    <td className="num">{formatEur(day.inflow_eur)}</td>
                    <td className="num">{formatEur(day.outflow_eur)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  );
}
