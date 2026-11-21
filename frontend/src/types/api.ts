/** API types mirroring the backend REST contract. Amounts are EUR, dates are YYYY-MM-DD strings. */

export type RiskStatus = "GREEN" | "YELLOW" | "RED";
export type HorizonDays = 30 | 60 | 90;
export type EventType = "closure" | "expense" | "inflow" | "payment_deferral" | "credit_line";
export type ChatEventType = "closure" | "expense" | "inflow";
export type EventSource = "chat" | "agent";
export type DecisionStatus = "approved" | "rejected";
export type ForecastItemKind = "fixed" | "variable_revenue" | "variable_cost" | "event";

export interface ScenarioParams {
  as_of_date: string;
  horizon_days: HorizonDays;
  buffer_eur: number;
}

export interface ApiErrorBody {
  error: { code: string; message: string };
}

export interface BalancePoint {
  date: string;
  balance_eur: number;
}

export interface ForecastItem {
  label: string;
  category: string;
  kind: ForecastItemKind;
  /** Signed: positive is an inflow, negative an outflow. */
  amount_eur: number;
}

export interface ForecastDay extends BalancePoint {
  inflow_eur: number;
  outflow_eur: number;
  items: ForecastItem[];
}

export interface DailyAverages {
  revenue_eur: number;
  variable_costs_eur: number;
  lookback_days: number;
  open_days: number;
}

export interface DateRange {
  start: string;
  end: string;
}

export interface Risk {
  status: RiskStatus;
  min_balance_eur: number;
  min_balance_date: string;
  floor_eur: number;
  credit_limit_eur: number | null;
  days_until_min: number;
  risk_window: DateRange | null;
}

export interface RiskDriver {
  category: string;
  label: string;
  total_eur: number;
  deferrable: boolean;
}

export interface PlannedEvent {
  id: number;
  event_type: EventType;
  date_from: string;
  date_to: string;
  amount_eur: number | null;
  category: string | null;
  description: string;
  original_utterance: string | null;
  source: EventSource;
  as_of_date: string;
  decision_id: number | null;
  created_at: string;
}

export interface RescueOption {
  id: string;
  title: string;
  description: string;
  new_min_balance_eur: number;
  new_min_balance_date: string;
  new_status: RiskStatus;
  cost_eur: number;
  cost_note: string | null;
  non_financial_cost: string | null;
  requires_approval: true;
  credit_limit_eur: number | null;
  simulated_actions: string[];
  forecast: BalancePoint[];
  rejected: boolean;
}

export interface Insight {
  kind: "surplus";
  title: string;
  detail: string;
  amount_eur: number;
}

export interface Dashboard extends ScenarioParams {
  data_range: { first_date: string; last_date: string };
  current_balance_eur: number;
  history: BalancePoint[];
  forecast: { days: ForecastDay[]; daily_averages: DailyAverages };
  risk: Risk;
  risk_drivers: RiskDriver[];
  headline: string;
  events: PlannedEvent[];
  options: RescueOption[];
  code_recommended_option_id: string | null;
  insights: Insight[];
}

export interface EventsResponse {
  events: PlannedEvent[];
}

export interface DecisionSnapshot {
  status_before: RiskStatus;
  min_balance_before_eur: number;
  min_balance_date_before: string;
  status_after: RiskStatus;
  min_balance_after_eur: number;
  /** The credit line the chosen option relies on, if any. */
  credit_limit_after_eur: number | null;
}

export interface Decision {
  id: number;
  as_of_date: string;
  horizon_days: number;
  buffer_eur: number;
  option_id: string;
  option_title: string;
  option_description: string;
  status: DecisionStatus;
  execution: "SIMULATED";
  llm_explanation: string | null;
  execution_messages: string[];
  decided_at: string;
  /** Set when the owner later withdrew an approved option; its measures are no longer in the forecast. */
  withdrawn_at: string | null;
  snapshot: DecisionSnapshot;
}

export interface DecisionsResponse {
  decisions: Decision[];
}

export interface DecisionRequest extends ScenarioParams {
  option_id: string;
  status: DecisionStatus;
  llm_explanation: string | null;
}

export interface DecisionResponse {
  decision: Decision;
  execution_messages: string[];
}

export interface Recommendation {
  source: "llm" | "fallback";
  fallback_reason: string | null;
  headline: string;
  explanation: string;
  recommended_option_id: string | null;
  rationale: string;
  option_comments: Record<string, string>;
}

export interface EventDraft {
  event_type: ChatEventType;
  date_from: string;
  date_to: string;
  amount_eur: number | null;
  description: string;
}

export interface ToolCall {
  name: string;
  ok: boolean;
}

export interface ChatRequest extends ScenarioParams {
  message: string;
  conversation_id: string | null;
}

export interface ChatResponse {
  conversation_id: string;
  reply: string;
  pending_events: EventDraft[];
  saved_events: PlannedEvent[];
  /** Saved plans the assistant asks the owner to confirm for removal; nothing is removed yet. */
  pending_removals: PlannedEvent[];
  /** Plans removed in this turn. */
  removed_events: PlannedEvent[];
  tool_calls: ToolCall[];
  events_changed: boolean;
}
