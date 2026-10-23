/**
 * ConflictsPanel — presentational conflicts view (Member 5).
 *
 * This component implements the contract Member 1 declared in `types.ts`
 * (`ConflictsPanelProps { conflicts }`): it never fetches, never aborts a request
 * and never owns the picker state. `ConflictChecker.tsx` is the container that
 * calls the contextual endpoint and hands the results down here.
 *
 * A conflict row always stays unresolved here: detection reports pressure, and
 * only the Assistant + your explicit confirmation (the /api/changes flow) may
 * change a date. Nothing in this file marks a conflict as fixed.
 */

import type {
  Commitment,
  Conflict,
  ConflictsPanelProps as ConflictsPanelContract,
  ConflictsQuery,
  PlanItem,
} from "../types";
import { badgeClass, capitalize, formatDateShort, isIsoDate } from "../format";
import { EmptyState, ErrorState, LoadingRows } from "./PanelState";

export interface ConflictsPanelProps extends ConflictsPanelContract {
  commitments: Commitment[];
  /** Full plan, used to match a conflicted commitment to its plan steps. */
  plan: PlanItem[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Picker state, owned by the container. */
  selectedId: string;
  targetDate: string;
  /** The commitment behind `selectedId`, resolved by the container. */
  selected: Commitment | null;
  /** Date offered as an example target (reference date + 3 days). */
  suggestedDate: string;
  /** False until a commitment and a valid `YYYY-MM-DD` date are both set. */
  canRun: boolean;
  checking: boolean;
  /** The move the last successful check describes; null until one has run. */
  checked: ConflictsQuery | null;
  /** Message from the last failed check; null while the last run succeeded. */
  checkError: string | null;
  onSelectChange: (value: string) => void;
  onTargetDateChange: (value: string) => void;
  onRunCheck: () => void;
}

const CONFLICT_TONE: Record<string, "danger" | "warning"> = {
  collision: "danger",
  tight_week: "warning",
};

function ConflictRow({ conflict, plan }: { conflict: Conflict; plan: PlanItem[] }) {
  const affected = plan.filter((item) => item.commitment_id === conflict.commitment_id);
  const tone = CONFLICT_TONE[conflict.type] ?? "warning";

  return (
    <div className={`ab-conflict ab-conflict--${tone}`}>
      <div className="ab-conflict__head">
        <span className={badgeClass(tone)}>
          {conflict.type === "tight_week" ? "Tight schedule" : capitalize(conflict.type || "Conflict")}
        </span>
        <strong className="ab-conflict__title" title={conflict.title}>
          {conflict.title || `Commitment #${conflict.commitment_id}`}
        </strong>
        <span className="ab-badge ab-mono">due {formatDateShort(conflict.due_date)}</span>
      </div>

      {conflict.reason && <p className="ab-conflict__reason">{conflict.reason}</p>}

      <div className="ab-conflict__affected">
        {affected.length > 0 ? (
          affected.map((item) => (
            <span className="ab-badge" key={item.id}>
              plan #{item.id}: {item.title || "untitled step"} · {item.status}
            </span>
          ))
        ) : (
          <span className="ab-badge">
            No plan step for commitment #{conflict.commitment_id} yet
          </span>
        )}
        <span className="ab-badge ab-badge--danger">Unresolved — awaiting your confirmation</span>
      </div>
    </div>
  );
}

export default function ConflictsPanel({
  conflicts,
  checked,
  checkError,
  commitments,
  plan,
  loading,
  error,
  onRetry,
  selectedId,
  targetDate,
  selected,
  suggestedDate,
  canRun,
  checking,
  onSelectChange,
  onTargetDateChange,
  onRunCheck,
}: ConflictsPanelProps) {
  const movedCommitment = checked
    ? commitments.find((item) => item.id === checked.commitment_id)
    : selected;

  return (
    <section className="ab-card" aria-label="Conflict check">
      <div className="ab-card__head">
        <h2 className="ab-card__title">Conflict check</h2>
        <span className="ab-card__hint">Preview pressure before a date changes</span>
      </div>
      <div className="ab-card__body">
        <p className="ab-note" style={{ marginTop: 0 }}>
          The backend detects conflicts <em>for a proposed move</em>: a commitment plus a new due
          date. Pick both to run a real check. The Assistant runs the same check automatically
          whenever you change a deadline.
        </p>

        {loading && <LoadingRows label="commitments for the conflict check" rows={2} />}

        {!loading && error && commitments.length === 0 && <ErrorState message={error} onRetry={onRetry} />}

        {!loading && commitments.length === 0 && !error && (
          <EmptyState
            icon="🧭"
            title="No commitments to check"
            hint="Add a commitment first, then preview how rescheduling it would squeeze your week."
          />
        )}

        {!loading && commitments.length > 0 && (
          <>
            <div
              style={{
                display: "flex",
                flexWrap: "wrap",
                gap: 12,
                alignItems: "flex-end",
                margin: "12px 0",
              }}
            >
              <label
                style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, flex: "1 1 240px" }}
              >
                <span style={{ color: "var(--text-muted)" }}>Commitment</span>
                <select
                  className="ab-chat__textarea"
                  style={{ minHeight: 42, padding: "8px 12px", maxHeight: 120 }}
                  value={selectedId}
                  onChange={(event) => onSelectChange(event.target.value)}
                >
                  <option value="">Select a commitment…</option>
                  {commitments.map((item) => (
                    <option key={item.id} value={String(item.id)}>
                      {item.title} (due {item.due_date})
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
                <span style={{ color: "var(--text-muted)" }}>
                  New due date{suggestedDate ? ` — e.g. ${suggestedDate}` : ""}
                </span>
                <input
                  className="ab-chat__textarea"
                  style={{ minHeight: 42, padding: "8px 12px" }}
                  type="date"
                  value={targetDate}
                  min="2000-01-01"
                  onChange={(event) => onTargetDateChange(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="ab-btn ab-btn--primary"
                onClick={onRunCheck}
                disabled={checking || !canRun}
              >
                {checking ? "Checking…" : "Check conflicts"}
              </button>
            </div>

            {selected && (
              <div className="ab-note" style={{ marginBottom: 10 }}>
                Checking “{selected.title}” moving from {selected.due_date} to{" "}
                {targetDate || "…"}.
                {isIsoDate(targetDate) && targetDate === selected.due_date && (
                  <div>
                    That is already the current due date, so this preview describes the schedule
                    as it stands — nothing would move.
                  </div>
                )}
              </div>
            )}

            {checkError && (
              <div style={{ marginBottom: 12 }}>
                <ErrorState message={checkError} onRetry={onRunCheck} />
              </div>
            )}

            {checking && <LoadingRows label="the conflict check" rows={2} />}

            {checked && conflicts.length === 0 && (
              <div className="ab-banner ab-banner--success" role="status">
                <div>
                  No conflicts detected by the backend for “{movedCommitment?.title ??
                    `commitment #${checked.commitment_id}`}” on {checked.new_due_date}.
                  This is only a preview — no date has been changed.
                </div>
              </div>
            )}

            {checked && conflicts.length > 0 && (
              <div className="ab-conflicts">
                <div className="ab-banner ab-banner--warning" role="status">
                  <div>
                    Proposed move: “{movedCommitment?.title ?? `commitment #${checked.commitment_id}`}”
                    {movedCommitment ? ` from ${movedCommitment.due_date}` : ""} to{" "}
                    <strong>{checked.new_due_date}</strong>. Nothing has been applied.
                  </div>
                </div>
                {conflicts.map((conflict, index) => (
                  <ConflictRow
                    key={`${conflict.commitment_id}-${conflict.type}-${index}`}
                    conflict={conflict}
                    plan={plan}
                  />
                ))}
                <div className="ab-banner ab-banner--warning" style={{ marginTop: 4 }} role="status">
                  <div>
                    {conflicts.length} conflict{conflicts.length === 1 ? "" : "s"} still
                    open. Ask the Assistant to reschedule, then confirm the proposal it shows you —
                    this panel cannot resolve anything by itself.
                  </div>
                </div>
              </div>
            )}

            {!checked && !checking && !checkError && (
              <EmptyState
                icon="⚠️"
                title="No check has been run yet"
                hint="Conflicts are computed for a specific proposed move, so nothing is listed until you pick a commitment and a new due date above."
              />
            )}
          </>
        )}
      </div>
    </section>
  );
}
