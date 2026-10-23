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
  /**
   * Estimated effort in hours. The backend `GET /api/plan` returns this as
   * `estimated_hours`; `api.ts` shapes it into `hours`. When the field is
   * absent or non-numeric it stays null and the UI shows no estimate (it is
   * never fabricated client-side).
   */
  hours?: number | null;
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
  /**
   * NOT SENT by the current backend (`/api/plan` returns only `plan_items`).
   * Declared optional so the UI can prefer an authoritative server-side
   * reference date the moment M2 exposes one; until then the client falls back
   * to VITE_DEMO_TODAY and then to the browser clock. Never assumed present.
   */
  today?: string;
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
  /** The backend returns the updated commitment row; null only if the reply is
   *  malformed, so callers must handle it instead of assuming a shape. */
  commitment: Commitment | null;
  plan_items: PlanItem[];
}

/** Response for POST /api/changes/{id}/cancel. */
export interface CancelChangeResponse {
  change_id: string;
  status: string;
}

// ---------------------------------------------------------------------------
// Commitment check-off / delete (server-side dashboard controls)
// ---------------------------------------------------------------------------

/**
 * The only statuses a user may set directly from a dashboard row. A "tick"
 * toggles between them: completing hides the step from the plan, reactivating
 * brings it back. Deleting a commitment removes it entirely.
 */
export type CommitmentStatus = "active" | "completed";

/** Response for PATCH /api/commitments/{id}. */
export interface CommitmentStatusResponse {
  commitment: Commitment | null;
  plan_items: PlanItem[];
}

/** Response for DELETE /api/commitments/{id}. */
export interface DeleteCommitmentResponse {
  deleted_id: number;
  plan_items: PlanItem[];
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
 * (422 validation failures carry `detail` as an ARRAY; api.ts flattens it.)
 */
export interface ApiErrorBody {
  detail?: string;
}

// ---------------------------------------------------------------------------
// M5 dashboard-panel integration contract
// ---------------------------------------------------------------------------

/**
 * Props M1 will pass when mounting the M5-owned panels
 * (`TodayPlan.tsx`, `CommitmentsPanel.tsx`, `ConflictsPanel.tsx`). They are
 * declared here because M1 owns this type file and M5 owns the component
 * internals: the panels must not fetch, derive or mutate anything themselves,
 * and M1 supplies already-loaded, already-filtered data from the real API.
 *
 * STATUS: those three files are currently 0 bytes, so App.tsx renders its own
 * equivalent views; importing the empty modules would break the build. Once M5
 * lands them, App swaps its views for these components with no API changes.
 */
export interface TodayPlanProps {
  /** ISO date the backend plans against (see `resolveReferenceDate` in App). */
  today: string;
  /** Plan items already filtered to `planned_date === today`. */
  items: PlanItem[];
}

export interface CommitmentsPanelProps {
  /** Commitments already filtered to `status === "active"`. */
  commitments: Commitment[];
}

export interface ConflictsPanelProps {
  /** Conflicts from a real `GET /api/conflicts` call for one proposed move. */
  conflicts: Conflict[];
}
