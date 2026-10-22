/**
 * Display helpers shared by the M5 dashboard panels.
 *
 * Formatting lives here so TodayPlan / CommitmentsPanel / ConflictsPanel and
 * App never diverge on how the same backend value looks on screen. Nothing in
 * this module invents data: an unknown or malformed value formats to an
 * explicit fallback rather than a plausible-looking one.
 */

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export function isIsoDate(value: string): boolean {
  return ISO_DATE_RE.test(value);
}

/** UTC milliseconds for a YYYY-MM-DD date, or null when it is not one. */
function toUtcDay(value: string): number | null {
  if (!isIsoDate(value)) return null;
  const [year, month, day] = value.split("-").map(Number);
  const ms = Date.UTC(year, month - 1, day);
  return Number.isNaN(ms) ? null : ms;
}

export function formatDateShort(iso: string): string {
  const day = toUtcDay(iso);
  if (day === null) return iso;
  return new Date(day).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatLongDate(iso: string): string {
  const day = toUtcDay(iso);
  if (day === null) return iso;
  return new Date(day).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

/** Whole days from `from` to `to`; null when either side is not a real date. */
export function dayDiff(from: string, to: string): number | null {
  const a = toUtcDay(from);
  const b = toUtcDay(to);
  if (a === null || b === null) return null;
  return Math.round((b - a) / 86_400_000);
}

/** `iso` shifted by `days`, staying in YYYY-MM-DD. Empty string if not a date. */
export function addDaysIso(iso: string, days: number): string {
  if (!isIsoDate(iso)) return "";
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}

/**
 * Deadline wording relative to the dashboard's reference date. Negative means
 * the date is already past, which the caller styles as overdue.
 */
export function describeDeadline(dueDate: string, today: string): string {
  const diff = dayDiff(today, dueDate);
  if (diff === null) return formatDateShort(dueDate);
  if (diff < 0) return `${formatDateShort(dueDate)} (overdue ${Math.abs(diff)}d)`;
  if (diff === 0) return `${formatDateShort(dueDate)} (today)`;
  if (diff === 1) return `${formatDateShort(dueDate)} (tomorrow)`;
  return `${formatDateShort(dueDate)} (in ${diff}d)`;
}

/**
 * Estimated effort. The backend does not return an `hours` field yet, so this
 * returns null unless a number actually arrives — the caller then shows a
 * visible "not provided" marker instead of a fabricated figure.
 */
export function formatHours(hours: number | null | undefined): string | null {
  if (typeof hours !== "number" || !Number.isFinite(hours) || hours <= 0) return null;
  return `${hours % 1 === 0 ? hours.toFixed(0) : hours.toFixed(1)} h`;
}

export function capitalize(value: string): string {
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1);
}

/** Statuses the service layer uses once work is finished. */
const COMPLETED_STATUSES = new Set(["completed", "done"]);

export function isCompletedStatus(status: string): boolean {
  return COMPLETED_STATUSES.has((status || "").trim().toLowerCase());
}

/** Badge tone for a plan/commitment status. Unknown statuses stay neutral. */
export function statusTone(status: string): "success" | "primary" | "" {
  const normalized = (status || "").trim().toLowerCase();
  if (isCompletedStatus(normalized)) return "success";
  if (normalized === "cancelled") return "";
  if (normalized === "planned" || normalized === "active" || normalized === "pending") {
    return "primary";
  }
  return "";
}

/** Badge tone for a priority. Missing priority is neutral, never "low". */
export function priorityTone(priority: string | null): "danger" | "warning" | "" {
  const normalized = (priority || "").trim().toLowerCase();
  if (normalized === "high" || normalized === "urgent" || normalized === "critical") {
    return "danger";
  }
  if (normalized === "medium" || normalized === "normal") return "warning";
  return "";
}

export function badgeClass(tone: "success" | "primary" | "warning" | "danger" | ""): string {
  if (!tone) return "ab-badge";
  return `ab-badge ab-badge--${tone}`;
}

/** Deadline-first ordering: soonest due date, then stable id. */
export function compareByDeadline(a: { due_date: string; id: number }, b: { due_date: string; id: number }): number {
  const byDate = (a.due_date || "").localeCompare(b.due_date || "");
  return byDate !== 0 ? byDate : a.id - b.id;
}
