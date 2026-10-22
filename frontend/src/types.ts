/**
 * Shared frontend types for ActionBridge AI (M1-owned).
 *
 * These models are derived directly from the ACTUAL backend implementation
 * (backend/main.py + backend/services.py + backend/agent.py), not from the
 * planning docs. Field names and optionality match the JSON the server really
 * returns, so adapters in api.ts stay honest.
 */

// ---------------------------------------------------------------------------
// Domain entities (rows surfaced verbatim by the service layer)
// ---------------------------------------------------------------------------

/** A single commitment row returned inside `{ commitments: [...] }`. */
export interface Commitment {
  id: number;
  title: string;
  type: string;
  /** ISO date string, YYYY-MM-DD. */
  due_date: string;
  priority: string | null;
  status: string;
  /** ISO timestamp string. */
  created_at: string;
}

/** A single plan step returned inside `{ plan_items: [...] }`. */
export interface PlanItem {
  id: number;
  commitment_id: number;
  title: string;
  /** ISO date string, YYYY-MM-DD. */
  planned_date: string;
  status: string;
  created_at: string;
}

/** A conflict produced by the contextual conflict checker. */
export type ConflictType = "collision" | "tight_week" | string;

export interface Conflict {
  type: ConflictType;
  commitment_id: number;
  title: string;
  /** ISO date string of the *other* commitment involved. */
  due_date: string;
  reason: string;
}

// ---------------------------------------------------------------------------
// Request bodies
// ---------------------------------------------------------------------------

/** Body for POST /api/chat. */
export interface ChatRequest {
  session_id: string;
  text: string;
}

// ---------------------------------------------------------------------------
// Response envelopes (the exact objects the backend serialises)
// ---------------------------------------------------------------------------

/** One executed agent tool call. `status` mirrors agent.py dispatch states. */
export type ToolCallStatus = "success" | "error" | "rejected" | string;

export interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  status: ToolCallStatus;
}

/** Response for POST /api/chat. */
export interface ChatResponse {
  reply: string;
  /** Hex change id when the agent proposed a replan, otherwise null/absent. */
  pending_change_id: string | null;
  tool_calls: ToolCall[];
}

/** Response for GET /api/commitments. */
export interface CommitmentsResponse {
  commitments: Commitment[];
}

/** Response for GET /api/plan. */
export interface PlanResponse {
  plan_items: PlanItem[];
}

/** Response for GET /api/conflicts (requires commitment_id + new_due_date). */
export interface ConflictsResponse {
  conflicts: Conflict[];
}

// ---------------------------------------------------------------------------
// Pending change lifecycle
// ---------------------------------------------------------------------------

/** A rebalancing move recorded inside a change diff. */
export interface MovedItem {
  commitment_id: number;
  title: string;
  from: string;
  to: string;
  reason: string;
}

/** Response for GET /api/changes/{id}. */
export interface ChangeDetail {
  change_id: string;
  /** pending | applied | cancelled */
  status: string;
  summary: string;
  commitment_id: number;
  old_due_date: string;
  new_due_date: string;
  moved: MovedItem[];
  added: unknown[];
  removed: unknown[];
}

/** Response for POST /api/changes/{id}/apply. */
export interface ApplyChangeResponse {
  change_id: string;
  status: string;
  summary: string;
  commitment: Commitment;
  plan_items: PlanItem[];
}

/** Response for POST /api/changes/{id}/cancel. */
export interface CancelChangeResponse {
  change_id: string;
  status: string;
}

// ---------------------------------------------------------------------------
// Query filters
// ---------------------------------------------------------------------------

export interface CommitmentsQuery {
  status?: string;
  title_query?: string;
}

export interface ConflictsQuery {
  commitment_id: number;
  new_due_date: string;
}

/**
 * FastAPI error body. Every non-2xx response from the backend is
 * `{ "detail": "<message>" }`, so api.ts surfaces `detail` as the message.
 */
export interface ApiErrorBody {
  detail?: string;
}
