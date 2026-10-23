/**
 * TodayPlan — M5 dashboard panel for the plan steps scheduled on the
 * dashboard's reference date (or, with `groupByDate`, the whole plan).
 *
 * Data contract: GET /api/plan -> { plan_items: PlanItem[] } where the current
 * backend provides `title`, `planned_date` and `status` only. There is no
 * effort estimate in the real response, so `hours` renders only if the backend
 * starts sending it; otherwise the panel says so once instead of inventing a
 * number.
 */

import { useMemo } from "react";
import type { PlanItem } from "../types";
import {
  badgeClass,
  formatHours,
  formatDateShort,
  formatLongDate,
  isCompletedStatus,
  statusTone,
} from "../format";
import { EmptyState, ErrorState, ItemActions, LoadingRows } from "./PanelState";

export interface TodayPlanProps {
  /** Plan steps to display; already filtered by the caller for "today". */
  items: PlanItem[];
  /** Reference date the backend plans against (YYYY-MM-DD). */
  today: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  title?: string;
  /** Group rows under one heading per date — used by the full-plan view. */
  groupByDate?: boolean;
  /** Extra hint text for the empty state. */
  emptyHint?: string;
  /**
   * Check a plan step's underlying commitment off/reactivate, keyed by
   * commitment id. Omitted = the row shows no controls.
   */
  onToggleCommitment?: (commitmentId: number, done: boolean) => void;
  /** Delete a commitment from its plan row. Omitted = no delete control. */
  onDeleteCommitment?: (commitmentId: number) => void;
  /** Commitment id currently mid-request, so only that row's buttons disable. */
  busyCommitmentId?: number | null;
}

const COMPARABLE = (a: PlanItem, b: PlanItem): number => {
  const byDate = (a.planned_date || "").localeCompare(b.planned_date || "");
  return byDate !== 0 ? byDate : a.id - b.id;
};

interface RowActions {
  onToggleCommitment?: (commitmentId: number, done: boolean) => void;
  onDeleteCommitment?: (commitmentId: number) => void;
  busyCommitmentId?: number | null;
}

function PlanRow({
  item,
  showDate,
  onToggleCommitment,
  onDeleteCommitment,
  busyCommitmentId,
}: { item: PlanItem; showDate: boolean } & RowActions) {
  const done = isCompletedStatus(item.status);
  const hours = formatHours(item.hours);
  const showActions = !!onToggleCommitment || !!onDeleteCommitment;

  return (
    <div className="ab-row">
      <span className={`ab-dot ${done ? "ab-dot--done" : "ab-dot--pending"}`} aria-hidden />
      <div className="ab-row__main">
        <div className={`ab-row__title${done ? " ab-row__title--done" : ""}`} title={item.title}>
          {item.title || `Plan step #${item.id}`}
        </div>
        <div className="ab-row__meta">
          {showDate ? `Planned ${formatDateShort(item.planned_date)} · ` : ""}
          {item.status || "unknown"} · commitment #{item.commitment_id}
        </div>
      </div>
      <div className="ab-row__badges">
        {hours && <span className="ab-badge ab-mono">{hours}</span>}
        <span className={badgeClass(done ? "success" : statusTone(item.status))}>
          {done ? "Completed" : item.status || "Unknown"}
        </span>
      </div>
      {showActions && (
        <ItemActions
          done={done}
          busy={busyCommitmentId === item.commitment_id}
          onToggle={() => onToggleCommitment?.(item.commitment_id, done)}
          onDelete={() => onDeleteCommitment?.(item.commitment_id)}
        />
      )}
    </div>
  );
}

export default function TodayPlan({
  items,
  today,
  loading,
  error,
  onRetry,
  title = "Today's focus",
  groupByDate = false,
  emptyHint = "Ask the Assistant what to work on, or add a commitment — the plan is built from saved deadlines.",
  onToggleCommitment,
  onDeleteCommitment,
  busyCommitmentId,
}: TodayPlanProps) {
  const sorted = useMemo(() => [...items].sort(COMPARABLE), [items]);

  const groups = useMemo(() => {
    if (!groupByDate) return null;
    const map = new Map<string, PlanItem[]>();
    for (const item of sorted) {
      const key = item.planned_date || "";
      const bucket = map.get(key);
      if (bucket) bucket.push(item);
      else map.set(key, [item]);
    }
    return Array.from(map.entries());
  }, [sorted, groupByDate]);

  const completedCount = sorted.filter((item) => isCompletedStatus(item.status)).length;
  const totalHours = sorted.reduce((sum, item) => sum + (formatHours(item.hours) ? item.hours ?? 0 : 0), 0);

  const hint = loading
    ? "loading…"
    : `${sorted.length} scheduled${completedCount > 0 ? ` · ${completedCount} done` : ""}` +
      (totalHours > 0 ? ` · ${formatHours(totalHours)} planned` : "");

  return (
    <section className="ab-card" aria-label={title}>
      <div className="ab-card__head">
        <h2 className="ab-card__title">{title}</h2>
        <span className="ab-card__hint">{hint}</span>
      </div>
      <div className="ab-card__body">
        {loading && <LoadingRows label="your plan" rows={3} />}

        {!loading && error && sorted.length === 0 && <ErrorState message={error} onRetry={onRetry} />}

        {!loading && sorted.length === 0 && !error && (
          <EmptyState icon="📌" title="Nothing scheduled here yet" hint={emptyHint} />
        )}

        {!loading && sorted.length > 0 && (
          <>
            {error && (
              <div style={{ marginBottom: 12 }}>
                <ErrorState message={`${error} The list below is the last data loaded.`} onRetry={onRetry} />
              </div>
            )}
            {groups ? (
              groups.map(([date, bucket]) => (
                <div key={date || "undated"} style={{ marginBottom: 8 }}>
                  <div className="ab-card__hint" style={{ padding: "10px 4px 4px" }}>
                    {date === today ? `Today — ${formatLongDate(date)}` : formatLongDate(date)}
                  </div>
                  <div className="ab-list">
                    {bucket.map((item) => (
                      <PlanRow
                        key={item.id}
                        item={item}
                        showDate={false}
                        onToggleCommitment={onToggleCommitment}
                        onDeleteCommitment={onDeleteCommitment}
                        busyCommitmentId={busyCommitmentId}
                      />
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="ab-list">
                {sorted.map((item) => (
                  <PlanRow
                    key={item.id}
                    item={item}
                    showDate
                    onToggleCommitment={onToggleCommitment}
                    onDeleteCommitment={onDeleteCommitment}
                    busyCommitmentId={busyCommitmentId}
                  />
                ))}
              </div>
            )}
            {!sorted.some((item) => formatHours(item.hours)) && (
              <div className="ab-note" style={{ paddingTop: 10 }}>
                Estimated hours are not shown because <code>GET /api/plan</code> does not return an
                effort field yet. Nothing here is fabricated.
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
