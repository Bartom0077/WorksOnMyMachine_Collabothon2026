/** Contract-shaped fixtures for the mocked specs. Values follow the demo story (as of 2025-05-10). */
import type {
  BalancePoint,
  ChatResponse,
  Dashboard,
  Decision,
  ForecastDay,
  PlannedEvent,
  Recommendation,
  RescueOption,
} from "../../src/types/api";

const DAY_MS = 86_400_000;
export const AS_OF_DATE = "2025-05-10";
export const CURRENT_BALANCE = 3205.69;
export const MIN_BALANCE = -22097.6;
export const MIN_DATE = "2025-07-03";

type Anchor = [date: string, balance: number];

function toTime(date: string): number {
  return Date.parse(`${date}T00:00:00Z`);
}

function toDate(time: number): string {
  return new Date(time).toISOString().slice(0, 10);
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Daily points from the first to the last anchor, linearly interpolated between anchors. */
function interpolate(anchors: Anchor[]): BalancePoint[] {
  const points: BalancePoint[] = [];
  for (let index = 0; index < anchors.length - 1; index += 1) {
    const [fromDate, fromBalance] = anchors[index] as Anchor;
    const [toDateIso, toBalance] = anchors[index + 1] as Anchor;
    const days = Math.round((toTime(toDateIso) - toTime(fromDate)) / DAY_MS);
    for (let day = index === 0 ? 0 : 1; day <= days; day += 1) {
      points.push({
        date: toDate(toTime(fromDate) + day * DAY_MS),
        balance_eur: round2(fromBalance + ((toBalance - fromBalance) * day) / days),
      });
    }
  }
  return points;
}

function shift(points: BalancePoint[], fromDate: string, delta: number): BalancePoint[] {
  return points.map((point) =>
    point.date >= fromDate ? { ...point, balance_eur: round2(point.balance_eur + delta) } : point,
  );
}

function toForecastDays(points: BalancePoint[], startBalance: number): ForecastDay[] {
  let previous = startBalance;
  return points.map((point) => {
    const change = round2(point.balance_eur - previous);
    previous = point.balance_eur;
    const inflow = change > 0 ? change : 0;
    const outflow = change < 0 ? -change : 0;
    return {
      ...point,
      inflow_eur: inflow,
      outflow_eur: outflow,
      items:
        change === 0
          ? []
          : [
              {
                label: change > 0 ? "Average daily card and cash revenue" : "Salaries",
                category: change > 0 ? "revenue" : "salaries",
                kind: change > 0 ? "variable_revenue" : "fixed",
                amount_eur: change,
              },
            ],
    };
  });
}

const history = interpolate([
  ["2025-02-10", 9420.1],
  ["2025-03-01", 4310.55],
  ["2025-03-28", 11890.2],
  ["2025-04-02", 2950.4],
  ["2025-04-29", 10480.75],
  ["2025-05-05", 2610.3],
  [AS_OF_DATE, CURRENT_BALANCE],
]);

const redLine = interpolate([
  ["2025-05-11", 3890.1],
  ["2025-05-14", 6240.3],
  ["2025-05-15", 240.3],
  ["2025-05-28", 10890.5],
  ["2025-06-02", -3105.2],
  ["2025-06-15", -9480.4],
  ["2025-06-30", -12410.8],
  [MIN_DATE, MIN_BALANCE],
  ["2025-07-09", -17020.35],
]);

const greenLine = interpolate([
  ["2025-05-11", 3890.1],
  ["2025-05-14", 6240.3],
  ["2025-05-15", 240.3],
  ["2025-05-28", 10890.5],
  ["2025-06-02", 3105.2],
  ["2025-06-27", 14980.4],
  ["2025-07-03", 3480.15],
  ["2025-07-09", 8020.35],
]);

const deferredLine = shift(shift(redLine, "2025-05-15", 6000), "2025-06-15", 6000);

export const closureEvent: PlannedEvent = {
  id: 11,
  event_type: "closure",
  date_from: "2025-06-01",
  date_to: "2025-06-21",
  amount_eur: null,
  category: null,
  description: "Holiday closure",
  original_utterance: "We are closing for holidays from 1 to 21 June",
  source: "chat",
  as_of_date: AS_OF_DATE,
  decision_id: null,
  created_at: "2025-05-10T09:15:00Z",
};

export const ovenEvent: PlannedEvent = {
  id: 12,
  event_type: "expense",
  date_from: "2025-06-10",
  date_to: "2025-06-10",
  amount_eur: 8000,
  category: null,
  description: "New pizza oven",
  original_utterance: "I am buying an oven for €8,000 in June",
  source: "chat",
  as_of_date: AS_OF_DATE,
  decision_id: null,
  created_at: "2025-05-10T09:20:00Z",
};

export const agentEvents: PlannedEvent[] = [
  {
    id: 21,
    event_type: "payment_deferral",
    date_from: "2025-05-15",
    date_to: "2025-07-03",
    amount_eur: null,
    category: "investment_outflow",
    description: "Transfers to Zurich postponed by two months",
    original_utterance: null,
    source: "agent",
    as_of_date: AS_OF_DATE,
    decision_id: 1,
    created_at: "2025-05-10T09:30:00Z",
  },
  {
    id: 22,
    event_type: "credit_line",
    date_from: AS_OF_DATE,
    date_to: "2025-07-09",
    amount_eur: 15000,
    category: null,
    description: "Credit line of €15,000",
    original_utterance: null,
    source: "agent",
    as_of_date: AS_OF_DATE,
    decision_id: 1,
    created_at: "2025-05-10T09:30:00Z",
  },
];

export const options: RescueOption[] = [
  {
    id: "A",
    title: "Credit line",
    description: "Arrange a €30,000 credit line with the bank and use it only while the balance is negative.",
    new_min_balance_eur: MIN_BALANCE,
    new_min_balance_date: MIN_DATE,
    new_status: "GREEN",
    cost_eur: 212.4,
    cost_note: "interest at 9% p.a. on the amount used (assumption)",
    non_financial_cost: null,
    requires_approval: true,
    credit_limit_eur: 30000,
    simulated_actions: ["Credit line request for €30,000 sent to Commerzbank (simulated)."],
    forecast: redLine,
    rejected: false,
  },
  {
    id: "B",
    title: "Postpone the transfers to Zurich",
    description: "Hold the two €6,000 transfers due in the risk window and pay them two months later.",
    new_min_balance_eur: -10097.6,
    new_min_balance_date: MIN_DATE,
    new_status: "RED",
    cost_eur: 0,
    cost_note: null,
    non_financial_cost: "Requires a conversation with your brother",
    requires_approval: true,
    credit_limit_eur: null,
    simulated_actions: ["Transfers to Zurich on 15 May and 15 June postponed by two months (simulated)."],
    forecast: deferredLine,
    rejected: false,
  },
  {
    id: "C",
    title: "Shorter holiday",
    description: "Close for 7 days instead of 21.",
    new_min_balance_eur: -8420.1,
    new_min_balance_date: MIN_DATE,
    new_status: "RED",
    cost_eur: 0,
    cost_note: null,
    non_financial_cost: "Two weeks less holiday",
    requires_approval: true,
    credit_limit_eur: null,
    simulated_actions: ["Holiday closure shortened to 1–7 June (simulated)."],
    forecast: shift(redLine, "2025-06-08", 13677.5),
    rejected: false,
  },
  {
    id: "E",
    title: "Postpone transfers + smaller credit line",
    description: "Postpone the transfers to Zurich and cover the rest with a €15,000 credit line.",
    new_min_balance_eur: -10097.6,
    new_min_balance_date: MIN_DATE,
    new_status: "GREEN",
    cost_eur: 61.85,
    cost_note: "interest at 9% p.a. on the amount used (assumption)",
    non_financial_cost: "Requires a conversation with your brother",
    requires_approval: true,
    credit_limit_eur: 15000,
    simulated_actions: [
      "Credit line request for €15,000 sent to Commerzbank (simulated).",
      "Transfers to Zurich on 15 May and 15 June postponed by two months (simulated).",
    ],
    forecast: deferredLine,
    rejected: false,
  },
];

const base = {
  as_of_date: AS_OF_DATE,
  horizon_days: 60,
  buffer_eur: 3000,
  data_range: { first_date: "2025-01-01", last_date: "2025-12-31" },
  current_balance_eur: CURRENT_BALANCE,
  history,
} satisfies Partial<Dashboard>;

const dailyAverages = { revenue_eur: 1012.4, variable_costs_eur: 310.25, lookback_days: 28, open_days: 24 };

export const redDashboard: Dashboard = {
  ...base,
  forecast: { days: toForecastDays(redLine, CURRENT_BALANCE), daily_averages: dailyAverages },
  risk: {
    status: "RED",
    min_balance_eur: MIN_BALANCE,
    min_balance_date: MIN_DATE,
    floor_eur: 0,
    credit_limit_eur: null,
    days_until_min: 54,
    risk_window: { start: "2025-05-15", end: MIN_DATE },
  },
  risk_drivers: [
    { category: "salaries", label: "Salaries", total_eur: 16800, deferrable: false },
    { category: "investment_outflow", label: "Transfers to Zurich", total_eur: 12000, deferrable: true },
    { category: "rent", label: "Rent", total_eur: 7000, deferrable: false },
  ],
  headline: "In about 8 weeks you will be about €22,000 short.",
  events: [closureEvent, ovenEvent],
  options,
  code_recommended_option_id: "E",
  insights: [],
};

export const greenDashboard: Dashboard = {
  ...base,
  forecast: { days: toForecastDays(greenLine, CURRENT_BALANCE), daily_averages: dailyAverages },
  risk: {
    status: "GREEN",
    min_balance_eur: 3105.2,
    min_balance_date: "2025-06-02",
    floor_eur: 0,
    credit_limit_eur: null,
    days_until_min: 23,
    risk_window: null,
  },
  risk_drivers: [],
  headline: "You are on track: your balance stays above your safety buffer for the next 60 days.",
  events: [],
  options: [],
  code_recommended_option_id: null,
  insights: [
    {
      kind: "surplus",
      title: "You have money you could put to work",
      detail: "This amount stays above your safety buffer for the whole forecast. Consider a short-term deposit.",
      amount_eur: 105.2,
    },
  ],
};

/** The state after option E was approved: same balance line, but a credit line moves the floor. */
export const approvedDashboard: Dashboard = {
  ...base,
  forecast: { days: toForecastDays(deferredLine, CURRENT_BALANCE), daily_averages: dailyAverages },
  risk: {
    status: "GREEN",
    min_balance_eur: -10097.6,
    min_balance_date: MIN_DATE,
    floor_eur: -15000,
    credit_limit_eur: 15000,
    days_until_min: 54,
    risk_window: null,
  },
  risk_drivers: [],
  headline: "You are covered: the agreed credit line absorbs the dip in early July.",
  events: [closureEvent, ovenEvent, ...agentEvents],
  options: [],
  code_recommended_option_id: null,
  insights: [],
};

export const llmRecommendation: Recommendation = {
  source: "llm",
  fallback_reason: null,
  headline: "In about 8 weeks you will be roughly €22,000 short on your account.",
  explanation:
    "For three weeks in June there is no revenue, while salaries, rent and the transfers to Luigi still have to be paid.",
  recommended_option_id: "E",
  rationale: "It closes the gap at the lowest cost and keeps the credit line small.",
  option_comments: {
    A: "Safe, but you pay more interest than needed.",
    E: "Cheapest way to stay safe.",
  },
};

export const greenRecommendation: Recommendation = {
  source: "llm",
  fallback_reason: null,
  headline: "You are on track for the next 60 days.",
  explanation: "Revenue comfortably covers your fixed costs and no unusual payments are planned.",
  recommended_option_id: null,
  rationale: "",
  option_comments: {},
};

export const fallbackRecommendation: Recommendation = {
  source: "fallback",
  fallback_reason: "The language model did not respond in time.",
  headline: redDashboard.headline,
  explanation:
    "Your projected balance falls below zero because fixed costs continue while revenue stops during the closure.",
  recommended_option_id: "E",
  rationale: "Ranked first by the standard rules: it reaches a safe status at the lowest cost.",
  option_comments: {},
};

export const approvedDecision: Decision = {
  id: 1,
  as_of_date: AS_OF_DATE,
  horizon_days: 60,
  buffer_eur: 3000,
  option_id: "E",
  option_title: "Postpone transfers + smaller credit line",
  option_description: "Postpone the transfers to Zurich and cover the rest with a €15,000 credit line.",
  status: "approved",
  execution: "SIMULATED",
  llm_explanation: llmRecommendation.explanation,
  execution_messages: [
    "Credit line request for €15,000 sent to Commerzbank (simulated).",
    "Transfers to Zurich on 15 May and 15 June postponed by two months (simulated).",
  ],
  decided_at: "2025-05-10T09:30:00Z",
  snapshot: {
    status_before: "RED",
    min_balance_before_eur: MIN_BALANCE,
    min_balance_date_before: MIN_DATE,
    status_after: "GREEN",
    min_balance_after_eur: -10097.6,
  },
};

export const rejectedDecision: Decision = {
  ...approvedDecision,
  id: 2,
  option_id: "B",
  option_title: "Postpone the transfers to Zurich",
  option_description: "Hold the two €6,000 transfers due in the risk window and pay them two months later.",
  status: "rejected",
  execution_messages: [],
  snapshot: { ...approvedDecision.snapshot, status_after: "RED" },
};

export const chatProposal: ChatResponse = {
  conversation_id: "conv-1",
  reply: "I understand: the restaurant is closed from 1 June to 21 June 2025. Shall I include this in the forecast?",
  pending_events: [
    {
      event_type: "closure",
      date_from: "2025-06-01",
      date_to: "2025-06-21",
      amount_eur: null,
      description: "Holiday closure",
    },
  ],
  saved_events: [],
  tool_calls: [{ name: "propose_planned_event", ok: true }],
  events_changed: false,
};

export const chatSaved: ChatResponse = {
  conversation_id: "conv-1",
  reply: "Done. The closure is now part of your forecast, and it creates a shortfall in early July.",
  pending_events: [],
  saved_events: [closureEvent],
  tool_calls: [
    { name: "save_planned_event", ok: true },
    { name: "get_cash_kpis", ok: true },
  ],
  events_changed: true,
};
