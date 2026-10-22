/**
 * ConflictsPanel — M5 dashboard panel for scheduling conflicts.
 *
 * Data contract: GET /api/conflicts is contextual. It requires
 * `commitment_id` + `new_due_date` and answers "what pressure would this move
 * create?" — the backend has no endpoint that lists existing conflicts for the
 * plan as it stands, so this panel runs a real check on demand instead of
 * implying a background scan exists.
 *
 * A conflict row always stays unresolved here: detection reports pressure, and
 * only the Assistant + your explicit confirmation (the /api/changes flow) may
 * change a date. Nothing in this file marks a conflict as fixed.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Commitment, ConflictsQuery, ConflictsResponse, Conflict, PlanItem } from "../types";
import { badgeClass, capitalize, addDaysIso, formatDateShort, isIsoDate } from "../format";
import { EmptyState, ErrorState, LoadingRows } from "./PanelState";

export interface ConflictsPanelProps {
  commitments: Commitment[];
  /** Full plan, used to match a conflicted commitment to its plan steps. */
  plan: PlanItem[];
  /** Reference date used to suggest a sane default target date. */
  today: string;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  /** Injected so the panel works against the real API or the demo source. */
  getConflicts: (params: ConflictsQuery, signal?: AbortSignal) => Promise<ConflictsResponse>;
}

type CheckResult =
  | { status: "idle" }
  | { status: "ok"; conflicts: Conflict[]; checked: ConflictsQuery }
  | { status: "error"; message: string };

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
  commitments,
  plan,
  today,
  loading,
  error,
  onRetry,
  getConflicts,
}: ConflictsPanelProps) {
  const [selectedId, setSelectedId] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<CheckResult>({ status: "idle" });
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const selected = useMemo(
    () => commitments.find((item) => String(item.id) === selectedId),
    [commitments, selectedId],
  );

  /** Keep the target date blank until the user picks one; suggest +3 days. */
  const suggestedDate = useMemo(() => addDaysIso(today, 3), [today]);

  const canRun = selectedId !== "" && isIsoDate(targetDate);

  const runCheck = useCallback(async () => {
    const commitmentId = Number(selectedId);
    if (!commitmentId || !isIsoDate(targetDate)) {
      setResult({
        status: "error",
        message: "Pick a commitment and enter a valid target date (YYYY-MM-DD) before checking.",
      });
      return;
    }
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setChecking(true);
    try {
      const response = await getConflicts({ commitment_id: commitmentId, new_due_date: targetDate }, controller.signal);
      if (controller.signal.aborted) return;
      setResult({
        status: "ok",
        conflicts: response.conflicts ?? [],
        checked: { commitment_id: commitmentId, new_due_date: targetDate },
      });
    } catch (err) {
      if (controller.signal.aborted) return;
      setResult({
        status: "error",
        message: err instanceof Error ? err.message : "The conflict check failed.",
      });
    } finally {
      setChecking(false);
    }
  }, [selectedId, targetDate, getConflicts]);

  const movedCommitment = result.status === "ok"
    ? commitments.find((item) => item.id === result.checked.commitment_id)
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
                  onChange={(event) => setSelectedId(event.target.value)}
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
                  onChange={(event) => setTargetDate(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="ab-btn ab-btn--primary"
                onClick={() => void runCheck()}
                disabled={checking || !canRun}
              >
                {checking ? "Checking…" : "Check conflicts"}
              </button>
            </div>

            {selected && (
              <div className="ab-note" style={{ marginBottom: 10 }}>
                Checking “{selected.title}” moving from {selected.due_date} to{" "}
                {targetDate || "…"}.
              </div>
            )}

            {result.status === "error" && (
              <div style={{ marginBottom: 12 }}>
                <ErrorState message={result.message} onRetry={() => void runCheck()} />
              </div>
            )}

            {checking && <LoadingRows label="the conflict check" rows={2} />}

            {result.status === "ok" && result.conflicts.length === 0 && (
              <div className="ab-banner ab-banner--success" role="status">
                <div>
                  No conflicts detected by the backend for “{movedCommitment?.title ??
                    `commitment #${result.checked.commitment_id}`}” on {result.checked.new_due_date}.
                  This is only a preview — no date has been changed.
                </div>
              </div>
            )}

            {result.status === "ok" && result.conflicts.length > 0 && (
              <div className="ab-conflicts">
                <div className="ab-banner ab-banner--warning" role="status">
                  <div>
                    Proposed move: “{movedCommitment?.title ?? `commitment #${result.checked.commitment_id}`}”
                    {movedCommitment ? ` from ${movedCommitment.due_date}` : ""} to{" "}
                    <strong>{result.checked.new_due_date}</strong>. Nothing has been applied.
                  </div>
                </div>
                {result.conflicts.map((conflict, index) => (
                  <ConflictRow
                    key={`${conflict.commitment_id}-${conflict.type}-${index}`}
                    conflict={conflict}
                    plan={plan}
                  />
                ))}
                <div className="ab-banner ab-banner--warning" style={{ marginTop: 4 }} role="status">
                  <div>
                    {result.conflicts.length} conflict{result.conflicts.length === 1 ? "" : "s"} still
                    open. Ask the Assistant to reschedule, then confirm the proposal it shows you —
                    this panel cannot resolve anything by itself.
                  </div>
                </div>
              </div>
            )}

            {result.status === "idle" && !checking && (
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
