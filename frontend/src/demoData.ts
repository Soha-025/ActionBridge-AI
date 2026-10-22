/**
 * M5 demo data source — this is MOCK DATA generated in the browser.
 *
 * It exists only so the dashboard can be built and QA'd without a running
 * FastAPI server. It is NEVER silent: `isDemoMode()` is false unless
 * VITE_DEMO_MODE is explicitly truthy, App renders a persistent "Demo data"
 * banner whenever this module is active, and the banner states that details
 * such as estimated hours are invented here and do not come from the backend.
 *
 * Shapes mirror the real service layer exactly, so swapping back to the live
 * API changes nothing but the numbers:
 *   commitments -> services.get_commitments  (ORDER BY due_date ASC, id ASC)
 *   plan_items  -> services._rebuild_plan    (one step per ACTIVE commitment,
 *                 planned_date = that commitment's due_date, status 'planned')
 *   conflicts   -> services.detect_conflicts (collision / tight_week rules)
 */

import { ApiError } from "./api";
import { addDaysIso, dayDiff } from "./format";
import type {
  Commitment,
  CommitmentsQuery,
  CommitmentsResponse,
  ConflictsQuery,
  ConflictsResponse,
  Conflict,
  PlanItem,
  PlanResponse,
} from "./types";

/** Text the UI shows whenever this source is serving data. */
export const DEMO_BANNER_TEXT =
  "Demo data — generated in your browser, not by the backend. Titles, dates and estimated hours here are invented for layout testing.";

const CREATED_AT = "2026-10-20T09:00:00";

/** Simulated latency, so loading states are actually observable offline. */
const LATENCY_MS = 350;

export function isDemoMode(): boolean {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  const value = (env?.VITE_DEMO_MODE ?? "").trim().toLowerCase();
  return value === "1" || value === "true" || value === "yes" || value === "on";
}

/** The three read endpoints the dashboard needs, from either source. */
export interface DashboardSource {
  getCommitments(query?: CommitmentsQuery, signal?: AbortSignal): Promise<CommitmentsResponse>;
  getPlan(signal?: AbortSignal): Promise<PlanResponse>;
  getConflicts(params: ConflictsQuery, signal?: AbortSignal): Promise<ConflictsResponse>;
}

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError("Request canceled.", { isCanceled: true }));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new ApiError("Request canceled.", { isCanceled: true }));
      },
      { once: true },
    );
  });
}

type DemoSeed = {
  id: number;
  title: string;
  type: string;
  dueOffset: number;
  priority: string | null;
  status: string;
  hours: number;
  stepStatus: string;
};

/**
 * Offsets are relative to the reference date so "Today" is never an empty
 * panel. `1 Lab safety briefing` and `7 Statistics revision set` both land on
 * the reference date — one pending step, one completed step — which is what
 * lets the pending/completed styling be checked without a backend.
 */
const DEMO_SEED: DemoSeed[] = [
  { id: 1, title: "Lab safety briefing", type: "presentation", dueOffset: 0, priority: "high", status: "active", hours: 1.5, stepStatus: "planned" },
  { id: 7, title: "Statistics revision set", type: "quiz", dueOffset: 0, priority: "medium", status: "active", hours: 1, stepStatus: "completed" },
  { id: 2, title: "Statistics quiz", type: "quiz", dueOffset: 3, priority: "high", status: "active", hours: 2.5, stepStatus: "planned" },
  { id: 3, title: "AI assignment", type: "assignment", dueOffset: 3, priority: "medium", status: "active", hours: 3, stepStatus: "planned" },
  { id: 4, title: "Presentation", type: "presentation", dueOffset: 5, priority: "medium", status: "active", hours: 2, stepStatus: "planned" },
  { id: 5, title: "Literature essay", type: "assignment", dueOffset: -2, priority: "low", status: "active", hours: 4, stepStatus: "planned" },
  // Completed commitments get no plan step, exactly like _rebuild_plan().
  { id: 6, title: "Weekly reading review", type: "other", dueOffset: 1, priority: "high", status: "completed", hours: 0.5, stepStatus: "" },
];

function buildCommitments(today: string): Commitment[] {
  return DEMO_SEED.map((seed) => ({
    id: seed.id,
    title: seed.title,
    type: seed.type,
    due_date: addDaysIso(today, seed.dueOffset),
    priority: seed.priority,
    status: seed.status,
    created_at: CREATED_AT,
  })).sort((a, b) => a.due_date.localeCompare(b.due_date) || a.id - b.id);
}

function buildPlanItems(today: string): PlanItem[] {
  return DEMO_SEED.filter((seed) => seed.status === "active")
    .map((seed) => ({
      id: seed.id,
      commitment_id: seed.id,
      title: `Work on ${seed.title.toLowerCase()}`,
      planned_date: addDaysIso(today, seed.dueOffset),
      status: seed.stepStatus || "planned",
      created_at: CREATED_AT,
      hours: seed.hours,
    }))
    .sort((a, b) => a.planned_date.localeCompare(b.planned_date) || a.id - b.id);
}

/** Port of services.detect_conflicts: same-day collision, 1-day tight week. */
function detectDemoConflicts(commitments: Commitment[], params: ConflictsQuery): Conflict[] {
  const moved = commitments.find((item) => item.id === params.commitment_id);
  const conflicts: Conflict[] = [];
  for (const other of commitments) {
    if (other.id === params.commitment_id || other.status !== "active") continue;
    const diff = dayDiff(params.new_due_date, other.due_date);
    if (diff === null) continue;
    if (diff === 0) {
      conflicts.push({
        type: "collision",
        commitment_id: other.id,
        title: other.title,
        due_date: other.due_date,
        reason:
          `'${other.title}' is already due on ${params.new_due_date}; moving ` +
          `'${moved?.title ?? `commitment #${params.commitment_id}`}' there puts two deadlines on the same day.`,
      });
    } else if (diff > 0 && diff <= 1) {
      conflicts.push({
        type: "tight_week",
        commitment_id: other.id,
        title: other.title,
        due_date: other.due_date,
        reason:
          `'${other.title}' is due ${other.due_date}, right after the new date ` +
          `${params.new_due_date}; work for '${moved?.title ?? `commitment #${params.commitment_id}`}' ` +
          "must move earlier to avoid back-to-back deadlines.",
      });
    }
  }
  return conflicts;
}

export function createDemoSource(today: string): DashboardSource {
  const commitments = buildCommitments(today);
  const planItems = buildPlanItems(today);

  return {
    async getCommitments(query: CommitmentsQuery = {}, signal?: AbortSignal): Promise<CommitmentsResponse> {
      await wait(LATENCY_MS, signal);
      const needle = (query.title_query ?? "").trim().toLowerCase();
      return {
        commitments: commitments.filter(
          (item) =>
            (!query.status || item.status === query.status) &&
            (!needle || item.title.toLowerCase().includes(needle)),
        ),
      };
    },

    async getPlan(signal?: AbortSignal): Promise<PlanResponse> {
      await wait(LATENCY_MS, signal);
      // The real /api/plan does not send `today`; the demo mirrors that, so the
      // reference date still comes from VITE_DEMO_TODAY.
      return { plan_items: planItems };
    },

    async getConflicts(params: ConflictsQuery, signal?: AbortSignal): Promise<ConflictsResponse> {
      await wait(LATENCY_MS, signal);
      if (!commitments.some((item) => item.id === params.commitment_id)) {
        throw new ApiError(`Unknown commitment id: ${params.commitment_id}`, { status: 404 });
      }
      if (!/^\d{4}-\d{2}-\d{2}$/.test(params.new_due_date)) {
        throw new ApiError("new_due_date must be an ISO date in YYYY-MM-DD form", { status: 400 });
      }
      return { conflicts: detectDemoConflicts(commitments, params) };
    },
  };
}
