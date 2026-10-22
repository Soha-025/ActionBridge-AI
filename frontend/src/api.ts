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
 * Base URL resolution (no config files are modified):
 *   1. `VITE_API_BASE_URL` if the runtime env provides it, else
 *   2. `http://localhost:8000` (the documented FastAPI dev origin; the backend
 *      enables CORS for `*`). Endpoints are appended as `/api/...`, so if a
 *      Vite dev proxy is added later it works simply by setting
 *      `VITE_API_BASE_URL=""` (same-origin) without touching this file.
 */

import type {
  ApplyChangeResponse,
  CancelChangeResponse,
  ChangeDetail,
  ChatRequest,
  ChatResponse,
  CommitmentsQuery,
  CommitmentsResponse,
  ConflictsQuery,
  ConflictsResponse,
  PlanResponse,
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

const RAW_BASE = readEnvBase();
const API_BASE: string =
  RAW_BASE !== undefined ? stripTrailingSlash(RAW_BASE) : "http://localhost:8000";

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
  method: "GET" | "POST";
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
    if (typeof parsed.detail === "string" && parsed.detail.trim()) return parsed.detail;
    if (parsed.detail !== undefined) return JSON.stringify(parsed.detail);
  } catch {
    // Not JSON; use a truncated raw body so we never lose the server's message.
  }
  return text.slice(0, 300);
}

// ---------------------------------------------------------------------------
// Public, typed endpoints (1:1 with backend/main.py)
// ---------------------------------------------------------------------------

export const api = {
  /** POST /api/chat */
  chat(payload: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
    return request<ChatResponse>("/api/chat", {
      method: "POST",
      body: payload,
      timeoutMs: CHAT_TIMEOUT_MS,
      signal,
    });
  },

  /** GET /api/commitments */
  getCommitments(query: CommitmentsQuery = {}, signal?: AbortSignal): Promise<CommitmentsResponse> {
    return request<CommitmentsResponse>(
      "/api/commitments",
      { method: "GET", signal },
      { status: query.status, title_query: query.title_query },
    );
  },

  /** GET /api/plan */
  getPlan(signal?: AbortSignal): Promise<PlanResponse> {
    return request<PlanResponse>("/api/plan", { method: "GET", signal });
  },

  /** GET /api/conflicts — contextual "what-if" check (both params required). */
  getConflicts(params: ConflictsQuery, signal?: AbortSignal): Promise<ConflictsResponse> {
    return request<ConflictsResponse>(
      "/api/conflicts",
      { method: "GET", signal },
      { commitment_id: params.commitment_id, new_due_date: params.new_due_date },
    );
  },

  /** GET /api/changes/{id} */
  getChange(changeId: string, signal?: AbortSignal): Promise<ChangeDetail> {
    return request<ChangeDetail>(`/api/changes/${encodeURIComponent(changeId)}`, {
      method: "GET",
      signal,
    });
  },

  /** POST /api/changes/{id}/apply — HUMAN confirmation boundary. */
  applyChange(changeId: string, signal?: AbortSignal): Promise<ApplyChangeResponse> {
    return request<ApplyChangeResponse>(
      `/api/changes/${encodeURIComponent(changeId)}/apply`,
      { method: "POST", signal },
    );
  },

  /** POST /api/changes/{id}/cancel — HUMAN confirmation boundary. */
  cancelChange(changeId: string, signal?: AbortSignal): Promise<CancelChangeResponse> {
    return request<CancelChangeResponse>(
      `/api/changes/${encodeURIComponent(changeId)}/cancel`,
      { method: "POST", signal },
    );
  },
};

/** True when an error means the backend could not be contacted at all. */
export function isConnectionError(err: unknown): boolean {
  return err instanceof ApiError && (err.isNetworkError || err.isTimeout);
}
