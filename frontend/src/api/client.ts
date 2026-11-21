import type {
  ApiErrorBody,
  ChatRequest,
  ChatResponse,
  Dashboard,
  DecisionRequest,
  DecisionResponse,
  DecisionsResponse,
  EventsResponse,
  Recommendation,
  ScenarioParams,
} from "../types/api";

/** nginx (production) and the Vite dev proxy both strip this prefix before forwarding to the backend. */
const API_PREFIX = "/api";

/** A failed API call: either the contract's error body, or a transport/protocol failure. */
export class ApiError extends Error {
  readonly code: string;
  /** HTTP status, or 0 when no response was received. */
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isApiErrorBody(body: unknown): body is ApiErrorBody {
  if (!isRecord(body) || !isRecord(body.error)) return false;
  return typeof body.error.code === "string" && typeof body.error.message === "string";
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  const detail = error instanceof Error ? error.message : String(error);
  return new ApiError("network_error", `Could not reach the server (${detail}).`, 0);
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text === "") return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new ApiError(
      "invalid_response",
      `The server returned an unreadable response (HTTP ${response.status}).`,
      response.status,
    );
  }
}

async function send(path: string, init: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, init);
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw toApiError(error);
  }
  const body = await readJson(response);
  if (response.ok) return body;
  if (isApiErrorBody(body)) {
    throw new ApiError(body.error.code, body.error.message, response.status);
  }
  throw new ApiError("http_error", `The server responded with HTTP ${response.status}.`, response.status);
}

/** The single trust boundary: successful responses are assumed to follow the REST contract. */
async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await send(path, { signal })) as T;
}

async function postJson<T>(path: string, payload: object, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  };
  return (await send(path, init)) as T;
}

function scenarioQuery(params: ScenarioParams): string {
  return new URLSearchParams({
    as_of_date: params.as_of_date,
    horizon_days: String(params.horizon_days),
    buffer_eur: String(params.buffer_eur),
  }).toString();
}

export const api = {
  getDashboard: (params: ScenarioParams, signal?: AbortSignal) =>
    getJson<Dashboard>(`/dashboard?${scenarioQuery(params)}`, signal),

  getEvents: (signal?: AbortSignal) => getJson<EventsResponse>("/events", signal),

  deleteEvent: async (id: number): Promise<void> => {
    await send(`/events/${id}`, { method: "DELETE" });
  },

  getDecisions: (signal?: AbortSignal) => getJson<DecisionsResponse>("/decisions", signal),

  postDecision: (request: DecisionRequest) => postJson<DecisionResponse>("/decisions", request),

  postRecommendation: (params: ScenarioParams, signal?: AbortSignal) =>
    postJson<Recommendation>("/recommendation", params, signal),

  postChat: (request: ChatRequest) => postJson<ChatResponse>("/chat", request),

  resetDemo: async (): Promise<void> => {
    await send("/demo/reset", { method: "POST" });
  },
};
