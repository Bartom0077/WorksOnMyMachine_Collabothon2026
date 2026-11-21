import type { BalancePoint, Dashboard, PlannedEvent, RescueOption } from "../types/api";
import { dateToTime } from "./format";

const DAY_MS = 86_400_000;

export interface ChartRow {
  time: number;
  date: string;
  history?: number;
  baseline?: number;
  option?: number;
}

export interface ClosureBand {
  id: number;
  start: number;
  end: number;
  description: string;
}

export interface EventMarker {
  id: number;
  time: number;
  balance: number;
  eventType: "expense" | "inflow";
  description: string;
}

export interface ChartModel {
  rows: ChartRow[];
  todayTime: number;
  ticks: number[];
  /** Round-numbered balance axis that contains every line and reference level. */
  balanceTicks: number[];
  closures: ClosureBand[];
  markers: EventMarker[];
}

function upsert(rows: Map<string, ChartRow>, date: string, patch: Partial<ChartRow>): void {
  const existing = rows.get(date) ?? { time: dateToTime(date), date };
  rows.set(date, { ...existing, ...patch });
}

function addSeries(rows: Map<string, ChartRow>, key: "history" | "baseline" | "option", points: BalancePoint[]): void {
  for (const point of points) upsert(rows, point.date, { [key]: point.balance_eur });
}

/** One tick per week, anchored on "today" so the today marker always sits on a tick. */
function weeklyTicks(first: number, last: number, anchor: number, everyWeeks: number): number[] {
  const step = 7 * DAY_MS * everyWeeks;
  const start = anchor - Math.floor((anchor - first) / step) * step;
  const ticks: number[] = [];
  for (let time = start; time <= last; time += step) ticks.push(time);
  return ticks;
}

const TARGET_BALANCE_TICKS = 5;

/** Smallest "nice" step (1, 2, 2.5 or 5 × a power of ten) that needs at most the target number of ticks. */
function niceStep(range: number): number {
  const rough = Math.max(range, 1) / TARGET_BALANCE_TICKS;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const factor = [1, 2, 2.5, 5, 10].find((candidate) => candidate * magnitude >= rough) ?? 10;
  return factor * magnitude;
}

function balanceTicks(values: number[]): number[] {
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  const step = niceStep(high - low);
  const ticks: number[] = [];
  for (let tick = Math.floor(low / step) * step; tick < high + step; tick += step) {
    ticks.push(tick === 0 ? 0 : tick); // normalises -0, which would be labelled "-€0"
  }
  return ticks;
}

function closureBands(events: PlannedEvent[], first: number, last: number): ClosureBand[] {
  return events
    .filter((event) => event.event_type === "closure")
    .map((event) => ({
      id: event.id,
      start: Math.max(dateToTime(event.date_from), first),
      end: Math.min(dateToTime(event.date_to), last),
      description: event.description,
    }))
    .filter((band) => band.start <= band.end);
}

function eventMarkers(events: PlannedEvent[], rows: Map<string, ChartRow>): EventMarker[] {
  const markers: EventMarker[] = [];
  for (const event of events) {
    if (event.event_type !== "expense" && event.event_type !== "inflow") continue;
    const balance = rows.get(event.date_from)?.baseline;
    if (balance === undefined) continue;
    markers.push({
      id: event.id,
      time: dateToTime(event.date_from),
      balance,
      eventType: event.event_type,
      description: event.description,
    });
  }
  return markers;
}

/** Merges history, the no-action forecast and the previewed option into one time-indexed row set. */
export function buildChartModel(dashboard: Dashboard, previewOption: RescueOption | null): ChartModel {
  const rows = new Map<string, ChartRow>();
  addSeries(rows, "history", dashboard.history);
  // Anchor both forecast lines on today's balance so they visibly continue the history line.
  upsert(rows, dashboard.as_of_date, { baseline: dashboard.current_balance_eur });
  addSeries(rows, "baseline", dashboard.forecast.days);
  if (previewOption !== null) {
    upsert(rows, dashboard.as_of_date, { option: dashboard.current_balance_eur });
    addSeries(rows, "option", previewOption.forecast);
  }

  const sorted = [...rows.values()].sort((a, b) => a.time - b.time);
  const todayTime = dateToTime(dashboard.as_of_date);
  const first = sorted[0]?.time ?? todayTime;
  const last = sorted[sorted.length - 1]?.time ?? todayTime;
  const spanWeeks = (last - first) / (7 * DAY_MS);
  const levels = [
    dashboard.risk.floor_eur + dashboard.buffer_eur,
    -(dashboard.risk.credit_limit_eur ?? 0),
    -(previewOption?.credit_limit_eur ?? 0),
  ];
  const balances = sorted.flatMap((row) =>
    [row.history, row.baseline, row.option].filter((value) => value !== undefined),
  );

  return {
    rows: sorted,
    todayTime,
    ticks: weeklyTicks(first, last, todayTime, spanWeeks > 16 ? 4 : 2),
    balanceTicks: balanceTicks([...balances, ...levels]),
    closures: closureBands(dashboard.events, first, last),
    markers: eventMarkers(dashboard.events, rows),
  };
}
