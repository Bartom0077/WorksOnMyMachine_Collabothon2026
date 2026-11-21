import type { Page, Route } from "@playwright/test";
import type {
  ApiErrorBody,
  ChatRequest,
  ChatResponse,
  Dashboard,
  Decision,
  DecisionRequest,
  PlannedEvent,
  Recommendation,
} from "../../src/types/api";
import { greenRecommendation, llmRecommendation, redDashboard } from "./data";

export interface MockError {
  status: number;
  body: ApiErrorBody;
}

/** Mutable backend state; tests change it to script what the next responses look like. */
export interface MockState {
  dashboard: Dashboard | MockError;
  dashboardDelayMs: number;
  recommendation: Recommendation | MockError;
  recommendationDelayMs: number;
  events: PlannedEvent[];
  decisions: Decision[];
  /** Responses for successive POST /chat calls; an empty queue answers 503 assistant_unavailable. */
  chatQueue: (ChatResponse | MockError)[];
  chatDelayMs: number;
  /** Called for POST /decisions; returns the stored decision or an error and may mutate the state. */
  onDecision: (request: DecisionRequest, state: MockState) => Decision | MockError;
  /** Called after a successful chat response is chosen; lets a test change what later requests return. */
  afterChat: (response: ChatResponse, state: MockState) => void;
}

export interface MockApi {
  state: MockState;
  requests: {
    dashboard: URLSearchParams[];
    recommendation: number;
    chat: ChatRequest[];
    decisions: DecisionRequest[];
    deletedEventIds: number[];
    resets: number;
  };
}

export function apiError(status: number, code: string, message: string): MockError {
  return { status, body: { error: { code, message } } };
}

function isMockError(value: object): value is MockError {
  return "status" in value && "body" in value;
}

function isApiUrl(url: URL): boolean {
  return url.pathname.startsWith("/api/");
}

function fulfill(route: Route, value: object, successStatus = 200): Promise<void> {
  return isMockError(value)
    ? route.fulfill({ status: value.status, json: value.body })
    : route.fulfill({ status: successStatus, json: value });
}

function delay(ms: number): Promise<void> {
  return ms > 0 ? new Promise((resolve) => setTimeout(resolve, ms)) : Promise.resolve();
}

function defaultRecommendation(state: MockState): Recommendation | MockError {
  if (isMockError(state.recommendation) || isMockError(state.dashboard)) return state.recommendation;
  return state.dashboard.options.length === 0 ? greenRecommendation : state.recommendation;
}

/** Installs contract-shaped route mocks for every backend endpoint the UI calls. */
export async function mockApi(page: Page, overrides: Partial<MockState> = {}): Promise<MockApi> {
  const state: MockState = {
    dashboard: redDashboard,
    dashboardDelayMs: 0,
    recommendation: llmRecommendation,
    recommendationDelayMs: 0,
    events: isMockError(overrides.dashboard ?? redDashboard) ? [] : [...redDashboard.events],
    decisions: [],
    chatQueue: [],
    chatDelayMs: 0,
    onDecision: () => apiError(409, "option_not_available", "This option is no longer available."),
    afterChat: () => undefined,
    ...overrides,
  };
  const requests: MockApi["requests"] = {
    dashboard: [],
    recommendation: 0,
    chat: [],
    decisions: [],
    deletedEventIds: [],
    resets: 0,
  };

  // Match on the path prefix only: a glob like "**/api/**" would also swallow Vite's /src/api/* modules.
  await page.route(isApiUrl, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = `${request.method()} ${url.pathname.replace(/^\/api/, "")}`;

    switch (endpoint) {
      case "GET /dashboard":
        requests.dashboard.push(url.searchParams);
        await delay(state.dashboardDelayMs);
        return fulfill(route, state.dashboard);
      case "GET /events":
        return fulfill(route, { events: state.events });
      case "GET /decisions":
        return fulfill(route, { decisions: state.decisions });
      case "POST /recommendation":
        requests.recommendation += 1;
        await delay(state.recommendationDelayMs);
        return fulfill(route, defaultRecommendation(state));
      case "POST /decisions": {
        const body: DecisionRequest = request.postDataJSON();
        requests.decisions.push(body);
        const result = state.onDecision(body, state);
        if (isMockError(result)) return fulfill(route, result);
        state.decisions = [result, ...state.decisions];
        return fulfill(route, { decision: result, execution_messages: result.execution_messages }, 201);
      }
      case "POST /chat": {
        const body: ChatRequest = request.postDataJSON();
        requests.chat.push(body);
        const response =
          state.chatQueue.shift() ?? apiError(503, "assistant_unavailable", "The assistant is not reachable.");
        await delay(state.chatDelayMs);
        if (!isMockError(response)) {
          // Like the backend: a plan removed through the chat is gone from the stored plans.
          const removedIds = new Set(response.removed_events.map((event) => event.id));
          state.events = state.events.filter((event) => !removedIds.has(event.id));
          state.afterChat(response, state);
        }
        return fulfill(route, response);
      }
      case "POST /demo/reset":
        requests.resets += 1;
        state.events = [];
        state.decisions = [];
        return route.fulfill({ status: 204 });
      default: {
        const eventId = /^DELETE \/events\/(\d+)$/.exec(endpoint)?.[1];
        if (eventId !== undefined) {
          const id = Number(eventId);
          requests.deletedEventIds.push(id);
          // Like the backend: deleting a measure of an approved option withdraws all measures of that decision.
          const decisionId = state.events.find((event) => event.id === id)?.decision_id ?? null;
          state.events = state.events.filter(
            (event) => event.id !== id && (decisionId === null || event.decision_id !== decisionId),
          );
          state.decisions = state.decisions.map((decision) =>
            decision.id === decisionId ? { ...decision, withdrawn_at: "2025-05-10T10:00:00Z" } : decision,
          );
          return route.fulfill({ status: 204 });
        }
        return fulfill(route, apiError(404, "not_found", `No mock for ${endpoint}`));
      }
    }
  });

  return { state, requests };
}
