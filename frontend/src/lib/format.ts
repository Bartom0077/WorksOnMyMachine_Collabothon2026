import type { EventType, RiskStatus } from "../types/api";

const eur = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const eurWhole = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  maximumFractionDigits: 0,
});
const eurCompact = new Intl.NumberFormat("en-IE", {
  style: "currency",
  currency: "EUR",
  notation: "compact",
  maximumFractionDigits: 1,
});
const longDate = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** "€22,097.60" / "-€22,097.60" */
export function formatEur(amount: number): string {
  return eur.format(amount);
}

/** "€22,098" — for prose and labels where cents are noise. */
export function formatEurWhole(amount: number): string {
  return eurWhole.format(amount);
}

/** "€22K" — for chart axes. */
export function formatEurCompact(amount: number): string {
  return eurCompact.format(amount);
}

/** Milliseconds since epoch for a YYYY-MM-DD date at UTC midnight. */
export function dateToTime(isoDate: string): number {
  return Date.parse(`${isoDate}T00:00:00Z`);
}

/** "3 Jul 2025" */
export function formatDate(isoDate: string): string {
  return longDate.format(dateToTime(isoDate));
}

/** "3 Jul" */
function formatShortDate(isoDate: string): string {
  return shortDate.format(dateToTime(isoDate));
}

export function formatTime(time: number, style: "short" | "long"): string {
  return (style === "short" ? shortDate : longDate).format(time);
}

/** "1 Jun – 21 Jun 2025", or a single date when both ends are equal. */
export function formatDateRange(from: string, to: string): string {
  return from === to ? formatDate(from) : `${formatShortDate(from)} – ${formatDate(to)}`;
}

/** Local date and time for an ISO timestamp. */
export function formatDateTime(isoTimestamp: string): string {
  return dateTime.format(new Date(isoTimestamp));
}

export function formatDaysFromNow(days: number): string {
  if (days <= 0) return "today";
  if (days === 1) return "in 1 day";
  if (days < 14) return `in ${days} days`;
  return `in about ${Math.round(days / 7)} weeks`;
}

/**
 * Explains a negative lowest balance that a credit line absorbs, e.g. why "-€15,728" can be "On track".
 * Null when there is no credit line or the balance never goes below zero.
 */
export function creditLineNote(creditLimit: number | null, lowestBalance: number): string | null {
  if (creditLimit === null || lowestBalance >= 0) return null;
  const unused = creditLimit + lowestBalance;
  return unused >= 0
    ? `Covered by the ${formatEurWhole(creditLimit)} credit line; ${formatEurWhole(unused)} of it stays unused at the lowest point.`
    : `${formatEurWhole(-unused)} more than the ${formatEurWhole(creditLimit)} credit line covers.`;
}

export const STATUS_LABELS: Record<RiskStatus, string> = {
  GREEN: "On track",
  YELLOW: "Tight",
  RED: "Shortfall ahead",
};

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  closure: "Closure",
  expense: "Planned expense",
  inflow: "Planned inflow",
  payment_deferral: "Deferred payment",
  credit_line: "Credit line",
};

/** "get_cash_kpis" → "cash kpis"; makes MCP tool names readable in the "data used" line. */
export function humanizeToolName(name: string): string {
  return name.replace(/^(get|list)_/, "").replaceAll("_", " ");
}
