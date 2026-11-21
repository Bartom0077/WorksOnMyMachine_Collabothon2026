import type {
  AdviceActionType,
  AdviceAgent,
  AdviceProposal,
  AdviceRecommendation,
  BalancePoint,
} from "../types/api";
import type { ChartPreview } from "./chartModel";

/** The agents in the order their groups are shown, with the group headings. */
export const ADVICE_GROUPS: readonly { agent: AdviceAgent; heading: string }[] = [
  { agent: "tax", heading: "Tax" },
  { agent: "fx", heading: "Currency (CHF transfers)" },
  { agent: "investment", heading: "Investments" },
];

/** Actions that move cash and therefore change the forecast; the others are recorded as a decision only. */
const FORECAST_CHANGING_ACTIONS: ReadonlySet<AdviceActionType> = new Set(["term_deposit", "tax_reserve", "fx_prebuy"]);

/** Ids of CFO recommendations (T1, F2, I1, …), as opposed to the rescue options A–E. */
const PROPOSAL_ID_PATTERN = /^[TFI][0-9]$/;

/** A proposal as shown to the owner: the language model's wording when it arrived, the code texts otherwise. */
export interface AdviceCard {
  proposal: AdviceProposal;
  title: string;
  explanation: string;
  recommended: boolean;
}

export function isProposalId(id: string): boolean {
  return PROPOSAL_ID_PATTERN.test(id);
}

export function changesForecast(proposal: AdviceProposal): boolean {
  return FORECAST_CHANGING_ACTIONS.has(proposal.action_type);
}

export function buildAdviceCards(
  proposals: AdviceProposal[],
  recommendation: AdviceRecommendation | null,
): AdviceCard[] {
  return proposals.map((proposal) => {
    const wording = recommendation?.proposals[proposal.id];
    return {
      proposal,
      title: wording?.title ?? proposal.title,
      explanation: wording?.explanation ?? proposal.description,
      recommended: wording?.recommended ?? false,
    };
  });
}

function roundToCents(amount: number): number {
  return Math.round(amount * 100) / 100;
}

/**
 * The forecast with several proposals at once. Their effects are additive, so each day is the baseline plus
 * every proposal's own difference from the baseline. A day a proposal has no figure for counts as unchanged.
 */
export function combinedForecast(baseline: BalancePoint[], forecasts: BalancePoint[][]): BalancePoint[] {
  const balancesByDate = forecasts.map(
    (forecast) => new Map(forecast.map((point) => [point.date, point.balance_eur])),
  );
  return baseline.map((point) => {
    const change = balancesByDate.reduce(
      (sum, balances) => sum + (balances.get(point.date) ?? point.balance_eur) - point.balance_eur,
      0,
    );
    return { date: point.date, balance_eur: roundToCents(point.balance_eur + change) };
  });
}

/** The first day with the lowest balance, or null for an empty forecast. */
export function lowestPoint(points: BalancePoint[]): BalancePoint | null {
  return points.reduce<BalancePoint | null>(
    (lowest, point) => (lowest === null || point.balance_eur < lowest.balance_eur ? point : lowest),
    null,
  );
}

function singlePreview({ proposal, title }: AdviceCard): ChartPreview {
  return {
    name: `recommendation ${proposal.id}`,
    title,
    forecast: proposal.forecast,
    minBalanceEur: proposal.new_min_balance_eur,
    minBalanceDate: proposal.new_min_balance_date,
    creditLimitEur: null,
  };
}

/**
 * The chart scenario for the given forecast-changing cards together, or null when there are none.
 * One card is drawn with the server's own figures; several are combined on top of `baseline`.
 */
export function advicePreview(baseline: BalancePoint[], drawn: AdviceCard[]): ChartPreview | null {
  const [first] = drawn;
  if (first === undefined) return null;
  if (drawn.length === 1) return singlePreview(first);

  const forecast = combinedForecast(
    baseline,
    drawn.map((card) => card.proposal.forecast),
  );
  const lowest = lowestPoint(forecast);
  if (lowest === null) return null;
  return {
    name: `recommendations ${drawn.map((card) => card.proposal.id).join(" + ")}`,
    title: drawn.map((card) => card.title).join(" + "),
    forecast,
    minBalanceEur: lowest.balance_eur,
    minBalanceDate: lowest.date,
    creditLimitEur: null,
  };
}
