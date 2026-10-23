/**
 * Centralized, strongly-typed API client for ActionBridge AI (M1-owned).
 *
 * Design goals
 * ------------
 * - One place that knows how to talk to the FastAPI backend (M2).
 * - Validates HTTP status, parses JSON defensively, and turns the backend's
 *   `{ detail: "..." }` error body into a readable `ApiError`.
 * - Enforces a timeout and supports caller cancellation via AbortSignal.
 * - No secrets, no fake responses, no swallowed errors.
 *
 * Base URL resolution (no secrets, no backend changes):
 *   1. `VITE_API_BASE_URL` if the runtime env provides it (set it to "" to force
 *      same-origin requests through the Vite `/api` proxy), else
 *   2. `http://<page host>:8000` — the documented FastAPI dev origin. A browser
 *      can resolve "localhost" to ::1 while uvicorn listens on IPv4 127.0.0.1,
 *      which fails to connect, so localhost is pinned to 127.0.0.1 here.
 *      Backend CORS is `*`, so the direct call works without any proxy.
 */

import type {
  ApplyChangeResponse,
  CancelChangeResponse,
  ChangeDetail,
  ChatRequest,
  ChatResponse,
  Commitment,
  CommitmentsQuery,
  CommitmentsResponse,
  Conflict,
  ConflictsQuery,
  ConflictsResponse,
  CommitmentStatus,
  CommitmentStatusResponse,
  DeleteCommitmentResponse,
  MovedItem,
  PlanItem,
  PlanResponse,
  ToolCall,
} from "./types";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

function readEnvBase(): string | undefined {
  // Access defensively so this module compiles even without vite/client types.
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  const value = env?.VITE_API_BASE_URL;
  return typeof value === "string" ? value : undefined;
}

/** Host of the page we are running in, or null outside a browser. */
function pageHost(): string | null {
  const loc = (globalThis as { location?: { hostname?: string } }).location;
  return typeof loc?.hostname === "string" && loc.hostname ? loc.hostname : null;
}

function fallbackBase(): string {
  const host = pageHost();
  if (!host) return "http://localhost:8000";
  // Pin the loopback name to IPv4: uvicorn's documented bind is 127.0.0.1:8000.
  const pinned = host === "localhost" || host === "::1" || host === "[::1]" ? "127.0.0.1" : host;
  return `http://${pinned}:8000`;
}

const RAW_BASE = readEnvBase();
const API_BASE: string = RAW_BASE !== undefined ? stripTrailingSlash(RAW_BASE) : fallbackBase();

function stripTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

/** Default per-request timeout. Chat runs a multi-step agent loop, so it is
 *  given a longer budget than the read endpoints. */
const DEFAULT_TIMEOUT_MS = 15_000;
const CHAT_TIMEOUT_MS = 60_000;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/** Normalized transport/HTTP error. Safe to render; never leaks internals. */
export class ApiError extends Error {
  readonly status: number;
  readonly isNetworkError: boolean;
  readonly isTimeout: boolean;
  readonly isCanceled: boolean;

  constructor(
    message: string,
    opts: {
      status?: number;
      isNetworkError?: boolean;
      isTimeout?: boolean;
      isCanceled?: boolean;
    } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status ?? 0;
    this.isNetworkError = opts.isNetworkError ?? false;
    this.isTimeout = opts.isTimeout ?? false;
    this.isCanceled = opts.isCanceled ?? false;
  }
}

// ---------------------------------------------------------------------------
// Core request helper
// ---------------------------------------------------------------------------

interface RequestOptions {
  method: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** Total timeout in ms. */
  timeoutMs?: number;
  /** Optional external abort signal (e.g. React unmount). */
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const url = `${API_BASE}${path}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

/** Combine a timeout with any caller signal into one AbortSignal. */
function makeSignal(timeoutMs: number, external?: AbortSignal): {
  signal: AbortSignal;
  cleanup: () => void;
  didTimeOut: () => boolean;
} {
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const onExternalAbort = () => controller.abort();
  if (external) {
    if (external.aborted) controller.abort();
    else external.addEventListener("abort", onExternalAbort, { once: true });
  }

  return {
    signal: controller.signal,
    cleanup: () => {
      clearTimeout(timer);
      external?.removeEventListener("abort", onExternalAbort);
    },
    didTimeOut: () => timedOut,
  };
}

async function request<T>(path: string, opts: RequestOptions, query?: Record<string, string | number | undefined>): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const { signal, cleanup, didTimeOut } = makeSignal(timeoutMs, opts.signal);

  let response: Response;
  try {
    const init: RequestInit = {
      method: opts.method,
      signal,
      headers: { Accept: "application/json" },
    };
    if (opts.body !== undefined) {
      (init.headers as Record<string, string>)["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }
    response = await fetch(buildUrl(path, query), init);
  } catch (err) {
    if (didTimeOut()) {
      throw new ApiError("The request timed out. Please try again.", { isTimeout: true });
    }
    if (opts.signal?.aborted) {
      throw new ApiError("Request canceled.", { isCanceled: true });
    }
    // fetch rejects on network/CORS/failure. Give a clear, honest message.
    throw new ApiError(
      "Cannot reach the ActionBridge backend. Check that the API server is running.",
      { isNetworkError: true },
    );
  } finally {
    cleanup();
  }

  if (!response.ok) {
    const detail = await extractErrorDetail(response);
    throw new ApiError(detail, { status: response.status });
  }

  // 204 or empty body -> undefined (all current endpoints return JSON, but be safe).
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      `Server returned a malformed response (expected JSON, got ${response.status}).`,
      { status: response.status },
    );
  }
}

async function extractErrorDetail(response: Response): Promise<string> {
  const fallback = `Request failed with status ${response.status}.`;
  let text = "";
  try {
    text = await response.text();
  } catch {
    return fallback;
  }
  if (!text) return fallback;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    // FastAPI answers request-validation failures with detail: [{loc, msg, ...}]
    // instead of a sentence, so it must be flattened before it reaches the UI.
    if (Array.isArray(parsed.detail)) return formatValidationIssues(parsed.detail);
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
    if (parsed.detail !== undefined) return JSON.stringify(parsed.detail);
  } catch {
    // Not JSON; use a truncated raw body so we never lose the server's message.
  }
  return text.slice(0, 300);
}

/**
 * Turn FastAPI's structured 422 entries into one readable sentence.
 * Raw JSON is never shown to a user, and nothing is invented: only the
 * server's own field name and message are re-used.
 */
function formatValidationIssues(items: unknown[]): string {
  const parts = items.map((raw) => {
    const item = asRecord(raw);
    const loc = Array.isArray(item.loc) ? item.loc.map(String) : [];
    const field = loc.filter((p) => p !== "body" && p !== "query").slice(-1)[0] ?? "request";
    const msg = asString(item.msg, "was not accepted").toLowerCase();
    return `${field} ${msg}`;
  });
  return parts.length
    ? `The backend rejected the request (${parts.join("; ")}).`
    : "The backend rejected the request.";
}

// ---------------------------------------------------------------------------
// Defensive response shaping
// ---------------------------------------------------------------------------

/**
 * The types above describe the contract the current backend really implements.
 * A half-deployed or changed server must never be able to crash a render, so
 * every response is shaped at runtime: arrays stay arrays, strings stay
 * strings, and an absent optional value becomes an honest empty value rather
 * than a fabricated one. Shaping never invents success.
 */
function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asArray<T>(value: unknown, shape: (item: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(shape) : [];
}

function shapeToolCall(raw: unknown): ToolCall {
  const r = asRecord(raw);
  return { name: asString(r.name, "unknown_tool"), input: asRecord(r.input), status: asString(r.status, "error") };
}

function shapeCommitment(raw: unknown): Commitment | null {
  if (raw === null || raw === undefined) return null;
  const r = asRecord(raw);
  if (typeof r.title !== "string" && r.id === undefined) return null;
  return {
    id: asNumber(r.id),
    title: asString(r.title),
    type: asString(r.type),
    due_date: asString(r.due_date),
    priority: typeof r.priority === "string" ? r.priority : null,
    status: asString(r.status),
    created_at: asString(r.created_at),
  };
}

function shapePlanItem(raw: unknown): PlanItem {
  const r = asRecord(raw);
  // Backend `/api/plan` returns `estimated_hours`; older/demo fixtures use
  // `hours`. Accept either so the dashboard's estimated-hours badge renders.
  const hoursRaw = r.estimated_hours ?? r.hours;
  return {
    id: asNumber(r.id),
    commitment_id: asNumber(r.commitment_id),
    title: asString(r.title),
    planned_date: asString(r.planned_date),
    status: asString(r.status),
    created_at: asString(r.created_at),
    // Absent or non-numeric stays null; the UI must not show a made-up estimate.
    hours: typeof hoursRaw === "number" && Number.isFinite(hoursRaw) ? hoursRaw : null,
  };
}

function shapeConflict(raw: unknown): Conflict {
  const r = asRecord(raw);
  return {
    type: asString(r.type, "unknown"),
    commitment_id: asNumber(r.commitment_id),
    title: asString(r.title),
    due_date: asString(r.due_date),
    reason: asString(r.reason),
  };
}

function shapeMovedItem(raw: unknown): MovedItem {
  const r = asRecord(raw);
  return {
    commitment_id: asNumber(r.commitment_id),
    title: asString(r.title),
    from: asString(r.from),
    to: asString(r.to),
    reason: asString(r.reason),
  };
}

function shapeChat(raw: unknown): ChatResponse {
  const r = asRecord(raw);
  const pending = r.pending_change_id;
  return {
    reply: asString(r.reply),
    pending_change_id: typeof pending === "string" && pending.length > 0 ? pending : null,
    tool_calls: asArray(r.tool_calls, shapeToolCall),
  };
}

function shapeChangeDetail(raw: unknown): ChangeDetail {
  const r = asRecord(raw);
  return {
    change_id: asString(r.change_id),
    status: asString(r.status, "unknown"),
    summary: asString(r.summary),
    commitment_id: asNumber(r.commitment_id),
    old_due_date: asString(r.old_due_date),
    new_due_date: asString(r.new_due_date),
    moved: asArray(r.moved, shapeMovedItem),
    added: Array.isArray(r.added) ? r.added : [],
    removed: Array.isArray(r.removed) ? r.removed : [],
  };
}

function shapeApplyResponse(raw: unknown): ApplyChangeResponse {
  const r = asRecord(raw);
  return {
    change_id: asString(r.change_id),
    status: asString(r.status, "unknown"),
    summary: asString(r.summary),
    commitment: shapeCommitment(r.commitment),
    plan_items: asArray(r.plan_items, shapePlanItem),
  };
}

function shapeCommitmentStatusResponse(raw: unknown): CommitmentStatusResponse {
  const r = asRecord(raw);
  return {
    commitment: shapeCommitment(r.commitment),
    plan_items: asArray(r.plan_items, shapePlanItem),
  };
}

function shapeDeleteCommitmentResponse(raw: unknown, fallbackId: number): DeleteCommitmentResponse {
  const r = asRecord(raw);
  return {
    deleted_id: asNumber(r.deleted_id, fallbackId),
    plan_items: asArray(r.plan_items, shapePlanItem),
  };
}

// ---------------------------------------------------------------------------
// Public, typed endpoints (1:1 with backend/main.py)
// ---------------------------------------------------------------------------

export const api = {
  /** POST /api/chat */
  chat(payload: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
    return request<unknown>("/api/chat", {
      method: "POST",
      body: payload,
      timeoutMs: CHAT_TIMEOUT_MS,
      signal,
    }).then(shapeChat);
  },

  /** GET /api/commitments */
  getCommitments(query: CommitmentsQuery = {}, signal?: AbortSignal): Promise<CommitmentsResponse> {
    return request<unknown>(
      "/api/commitments",
      { method: "GET", signal },
      { status: query.status, title_query: query.title_query },
    ).then((raw) => ({
      commitments: asArray(asRecord(raw).commitments, (item) => shapeCommitment(item)).filter(
        (c): c is Commitment => c !== null,
      ),
    }));
  },

  /** GET /api/plan */
  getPlan(signal?: AbortSignal): Promise<PlanResponse> {
    return request<unknown>("/api/plan", { method: "GET", signal }).then((raw) => {
      const r = asRecord(raw);
      const today = r.today;
      return {
        plan_items: asArray(r.plan_items, shapePlanItem),
        ...(typeof today === "string" && /^\d{4}-\d{2}-\d{2}$/.test(today) ? { today } : {}),
      };
    });
  },

  /** GET /api/conflicts — contextual "what-if" check (both params required). */
  getConflicts(params: ConflictsQuery, signal?: AbortSignal): Promise<ConflictsResponse> {
    return request<unknown>(
      "/api/conflicts",
      { method: "GET", signal },
      { commitment_id: params.commitment_id, new_due_date: params.new_due_date },
    ).then((raw) => ({ conflicts: asArray(asRecord(raw).conflicts, shapeConflict) }));
  },

  /** GET /api/changes/{id} */
  getChange(changeId: string, signal?: AbortSignal): Promise<ChangeDetail> {
    return request<unknown>(`/api/changes/${encodeURIComponent(changeId)}`, {
      method: "GET",
      signal,
    }).then(shapeChangeDetail);
  },

  /** POST /api/changes/{id}/apply — HUMAN confirmation boundary. */
  applyChange(changeId: string, signal?: AbortSignal): Promise<ApplyChangeResponse> {
    return request<unknown>(
      `/api/changes/${encodeURIComponent(changeId)}/apply`,
      { method: "POST", signal },
    ).then((raw) => shapeApplyResponse({ change_id: changeId, ...asRecord(raw) }));
  },

  /** POST /api/changes/{id}/cancel — HUMAN confirmation boundary. */
  cancelChange(changeId: string, signal?: AbortSignal): Promise<CancelChangeResponse> {
    return request<unknown>(
      `/api/changes/${encodeURIComponent(changeId)}/cancel`,
      { method: "POST", signal },
    ).then((raw) => {
      const r = asRecord(raw);
      return { change_id: asString(r.change_id, changeId), status: asString(r.status, "unknown") };
    });
  },

  /** PATCH /api/commitments/{id} — check a commitment off ('completed') or back
   *  on ('active'). A server-side dashboard control, never reachable from the
   *  agent/MCP path. */
  setCommitmentStatus(
    commitmentId: number,
    status: CommitmentStatus,
    signal?: AbortSignal,
  ): Promise<CommitmentStatusResponse> {
    return request<unknown>(`/api/commitments/${commitmentId}`, {
      method: "PATCH",
      body: { status },
      signal,
    }).then(shapeCommitmentStatusResponse);
  },

  /** DELETE /api/commitments/{id} — remove a commitment and its plan steps.
   *  Server-side dashboard control, never reachable from the agent/MCP path. */
  deleteCommitment(
    commitmentId: number,
    signal?: AbortSignal,
  ): Promise<DeleteCommitmentResponse> {
    return request<unknown>(`/api/commitments/${commitmentId}`, {
      method: "DELETE",
      signal,
    }).then((raw) => shapeDeleteCommitmentResponse(raw, commitmentId));
  },
};

/** True when an error means the backend could not be contacted at all. */
export function isConnectionError(err: unknown): boolean {
  return err instanceof ApiError && (err.isNetworkError || err.isTimeout);
}
