/**
 * CommitmentsPanel — M5 dashboard panel listing every commitment the backend
 * returns for the current filter.
 *
 * Data contract: GET /api/commitments -> { commitments: Commitment[] }.
 * Rows are ordered by deadline (soonest first) rather than by insertion, and
 * priority / status are rendered as badges whose tone is driven by the backend
 * value, never by a guess.
 */

import { useMemo } from "react";
import type { Commitment } from "../types";
import {
  badgeClass,
  capitalize,
  compareByDeadline,
  describeDeadline,
  dayDiff,
  formatDateShort,
  isCompletedStatus,
  priorityTone,
  statusTone,
} from "../format";
import { EmptyState, ErrorState, ItemActions, LoadingRows } from "./PanelState";

export interface CommitmentsPanelProps {
  commitments: Commitment[];
  /** Reference date used for "overdue / today / in N days" wording. */
  today: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  title?: string;
  /** Cap the visible rows (Today view). Omitted = show all, as in Upcoming. */
  maxVisible?: number;
  /**
   * Show the soonest still-open deadline. Member 1's inline Today view offered
   * this callout, so the Today mount keeps it; a full "all commitments" list
   * does not need a single next-up pointer.
   */
  showNextUp?: boolean;
  /** Check a commitment off/reactivate, keyed by commitment id. */
  onToggleCommitment?: (commitmentId: number, done: boolean) => void;
  /** Delete a commitment. */
  onDeleteCommitment?: (commitmentId: number) => void;
  /** Commitment id currently mid-request, so only that row's buttons disable. */
  busyCommitmentId?: number | null;
}

function CommitmentRow({
  item,
  today,
  onToggleCommitment,
  onDeleteCommitment,
  busyCommitmentId,
}: {
  item: Commitment;
  today: string;
  onToggleCommitment?: (commitmentId: number, done: boolean) => void;
  onDeleteCommitment?: (commitmentId: number) => void;
  busyCommitmentId?: number | null;
}) {
  const done = isCompletedStatus(item.status);
  const overdue = !done && (dayDiff(today, item.due_date) ?? 0) < 0;
  const showActions = !!onToggleCommitment || !!onDeleteCommitment;

  return (
    <div className="ab-row">
      <span className={`ab-dot ${done ? "ab-dot--done" : overdue ? "ab-dot--overdue" : "ab-dot--pending"}`} aria-hidden />
      <div className="ab-row__main">
        <div className={`ab-row__title${done ? " ab-row__title--done" : ""}`} title={item.title}>
          {item.title || `Commitment #${item.id}`}
        </div>
        <div className="ab-row__meta">
          {item.type ? `${capitalize(item.type)} · ` : ""}due {describeDeadline(item.due_date, today)}
        </div>
      </div>
      <div className="ab-row__badges">
        {item.priority ? (
          <span className={badgeClass(priorityTone(item.priority))}>{capitalize(item.priority)} priority</span>
        ) : (
          <span className="ab-badge">No priority</span>
        )}
        <span className={badgeClass(done ? "success" : statusTone(item.status))}>
          {capitalize(item.status || "unknown")}
        </span>
      </div>
      {showActions && (
        <ItemActions
          done={done}
          busy={busyCommitmentId === item.id}
          onToggle={() => onToggleCommitment?.(item.id, done)}
          onDelete={() => onDeleteCommitment?.(item.id)}
        />
      )}
    </div>
  );
}

export default function CommitmentsPanel({
  commitments,
  today,
  loading,
  error,
  onRetry,
  title = "Commitments",
  maxVisible,
  showNextUp = false,
  onToggleCommitment,
  onDeleteCommitment,
  busyCommitmentId,
}: CommitmentsPanelProps) {
  const sorted = useMemo(() => [...commitments].sort(compareByDeadline), [commitments]);

  const nextUp = useMemo(
    () => sorted.find((item) => !isCompletedStatus(item.status) && item.due_date >= today),
    [sorted, today],
  );

  const overdueCount = sorted.filter(
    (item) => !isCompletedStatus(item.status) && (dayDiff(today, item.due_date) ?? 0) < 0,
  ).length;

  const visible = maxVisible && maxVisible > 0 ? sorted.slice(0, maxVisible) : sorted;

  const hint = loading
    ? "loading…"
    : `${sorted.length} listed` + (overdueCount > 0 ? ` · ${overdueCount} overdue` : "");

  return (
    <section className="ab-card" aria-label={title}>
      <div className="ab-card__head">
        <h2 className="ab-card__title">{title}</h2>
        <span className="ab-card__hint">{hint}</span>
      </div>
      <div className="ab-card__body">
        {loading && <LoadingRows label="your commitments" rows={4} />}

        {!loading && error && sorted.length === 0 && <ErrorState message={error} onRetry={onRetry} />}

        {!loading && sorted.length === 0 && !error && (
          <EmptyState
            icon="🗂️"
            title="No commitments to show"
            hint="Add one through the Assistant, e.g. “I have a Statistics quiz Thursday.” Completed commitments are hidden from the plan."
          />
        )}

        {!loading && sorted.length > 0 && (
          <>
            {error && (
              <div style={{ marginBottom: 12 }}>
                <ErrorState message={`${error} The list below is the last data loaded.`} onRetry={onRetry} />
              </div>
            )}
            {overdueCount > 0 && (
              <div className="ab-banner ab-banner--warning" style={{ marginBottom: 12 }} role="status">
                <div>
                  {overdueCount} commitment{overdueCount === 1 ? " is" : "s are"} past due. Reschedule
                  them with the Assistant to rebuild the plan.
                </div>
              </div>
            )}
            {showNextUp && nextUp && (
              <div className="ab-banner ab-banner--info" style={{ marginBottom: 12 }} role="status">
                <div>
                  Next up: <strong>{nextUp.title || `Commitment #${nextUp.id}`}</strong> · due{" "}
                  {formatDateShort(nextUp.due_date)}
                </div>
              </div>
            )}
            <div className="ab-list">
              {visible.map((item) => (
                <CommitmentRow
                  key={item.id}
                  item={item}
                  today={today}
                  onToggleCommitment={onToggleCommitment}
                  onDeleteCommitment={onDeleteCommitment}
                  busyCommitmentId={busyCommitmentId}
                />
              ))}
            </div>
            {visible.length < sorted.length && (
              <div className="ab-note" style={{ paddingTop: 10 }}>
                Showing the first {visible.length} of {sorted.length} by deadline — every one is
                listed in Upcoming.
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
