/**
 * ConfirmModal (M1-owned): shows a REAL pending schedule change and lets the
 * human apply or cancel it through the actual backend endpoints.
 *
 * Guarantees required by the brief:
 * - Fetches real change details via GET /api/changes/{id}.
 * - Compares old vs new dates and lists affected/rebalanced tasks.
 * - States plainly that nothing is applied yet.
 * - Separate Apply / Cancel actions that hit the real endpoints.
 * - Blocks duplicate requests, shows progress, surfaces meaningful errors.
 * - On successful apply, notifies the parent so the dashboard refreshes.
 * - Closing the modal (X / Esc / backdrop) NEVER cancels the server proposal.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { ChangeDetail } from "../types";

interface ConfirmModalProps {
  /** The change id to inspect. When null/empty the modal renders nothing. */
  changeId: string | null;
  open: boolean;
  /** Dismiss the UI only — does NOT cancel the pending server change. */
  onClose: () => void;
  /** Called after a successful apply so the parent can refresh the dashboard. */
  onApplied: (detail: ChangeDetail) => void;
  /** Called after a successful cancel. */
  onCancelled: (changeId: string) => void;
}

type Phase = "loading" | "ready" | "error";

export default function ConfirmModal({
  changeId,
  open,
  onClose,
  onApplied,
  onCancelled,
}: ConfirmModalProps) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [change, setChange] = useState<ChangeDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // "idle" | "applying" | "cancelling" — also used to block duplicate clicks.
  const [busy, setBusy] = useState<"idle" | "applying" | "cancelling">("idle");

  const dialogRef = useRef<HTMLDivElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);

  // Fetch whenever the modal opens with a change id.
  useEffect(() => {
    if (open && changeId) {
      // Guard against stale responses if the id changes quickly.
      let cancelled = false;
      setPhase("loading");
      setLoadError(null);
      setActionError(null);
      setBusy("idle");
      api
        .getChange(changeId)
        .then((detail) => {
          if (cancelled) return;
          setChange(detail);
          setPhase("ready");
          requestAnimationFrame(() => primaryRef.current?.focus());
        })
        .catch((err) => {
          if (cancelled) return;
          setLoadError(
            err instanceof ApiError ? err.message : "Could not load this change. Please try again.",
          );
          setPhase("error");
        });
      return () => {
        cancelled = true;
      };
    }
    return undefined;
  }, [open, changeId]);

  // Esc dismisses (UI only) and simple focus containment.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const stillPending = useMemo(
    () => !change || change.status === "pending",
    [change],
  );

  const handleApply = useCallback(async () => {
    if (!changeId || busy !== "idle" || !stillPending) return;
    setBusy("applying");
    setActionError(null);
    try {
      await api.applyChange(changeId);
      const applied: ChangeDetail = { ...change!, status: "applied" };
      onApplied(applied);
      onClose();
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? `Could not apply the change: ${err.message}`
          : "Could not apply the change. Please try again.",
      );
      setBusy("idle");
    }
  }, [changeId, busy, stillPending, change, onApplied, onClose]);

  const handleCancel = useCallback(async () => {
    if (!changeId || busy !== "idle") return;
    setBusy("cancelling");
    setActionError(null);
    try {
      const res = await api.cancelChange(changeId);
      onCancelled(changeId);
      // Reflect server truth without claiming success if it failed above.
      onClose();
      void res;
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? `Could not cancel the proposal: ${err.message}`
          : "Could not cancel the proposal. Please try again.",
      );
      setBusy("idle");
    }
  }, [changeId, busy, onCancelled, onClose]);

  if (!open || !changeId) return null;

  const affectedCount =
    (change ? change.moved.length : 0) + (change && change.new_due_date !== change.old_due_date ? 1 : 0);

  return (
    <div
      className="ab-overlay"
      onMouseDown={(e) => {
        // Backdrop click dismisses UI only (never cancels).
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="ab-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ab-confirm-title"
        ref={dialogRef}
      >
        <div className="ab-modal__head">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
            <div>
              <h2 className="ab-modal__title" id="ab-confirm-title">
                Review proposed change
              </h2>
              <p className="ab-modal__sub">
                Nothing has been applied yet. Review the diff, then confirm.
              </p>
            </div>
            <button
              type="button"
              className="ab-modal__close"
              aria-label="Close (does not cancel the proposal)"
              onClick={onClose}
            >
              &times;
            </button>
          </div>
        </div>

        <div className="ab-modal__body">
          {phase === "loading" && (
            <>
              <div className="ab-skeleton" style={{ width: "70%" }} />
              <div className="ab-skeleton" style={{ width: "100%", height: 48 }} />
              <div className="ab-skeleton" style={{ width: "100%", height: 48 }} />
            </>
          )}

          {phase === "error" && (
            <div className="ab-banner ab-banner--error" role="alert">
              <div>
                <strong>Unable to load this change.</strong>
                <div>{loadError}</div>
              </div>
            </div>
          )}

          {phase === "ready" && change && (
            <>
              {change.status !== "pending" && (
                <div className="ab-banner ab-banner--warning" role="status">
                  <div>
                    This proposal is already <strong>{change.status}</strong> on the
                    server, so it can no longer be applied.
                  </div>
                </div>
              )}

              <div>
                <div className="ab-card__hint" style={{ marginBottom: 6 }}>
                  Summary
                </div>
                <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5 }}>{change.summary}</p>
              </div>

              <div className="ab-diff">
                <div className="ab-card__hint">Primary commitment (#{change.commitment_id})</div>
                <div className="ab-diff__row">
                  <span className="ab-datepill ab-datepill--old">{change.old_due_date}</span>
                  <span className="ab-arrow" aria-hidden>
                    &rarr;
                  </span>
                  <span className="ab-datepill ab-datepill--new">{change.new_due_date}</span>
                  <span className="ab-badge ab-badge--primary" style={{ marginLeft: "auto" }}>
                    {affectedCount} task{affectedCount === 1 ? "" : "s"} affected
                  </span>
                </div>
              </div>

              {change.moved.length > 0 && (
                <div className="ab-diff">
                  <div className="ab-card__hint">Rebalanced to avoid conflicts</div>
                  {change.moved.map((m) => (
                    <div className="ab-diff__row" key={`${m.commitment_id}-${m.to}`}>
                      <div className="ab-row__main" style={{ flex: "1 1 auto" }}>
                        <div className="ab-row__title" title={m.title}>
                          {m.title}
                        </div>
                        <div className="ab-row__meta">{m.reason}</div>
                      </div>
                      <span className="ab-datepill ab-datepill--old">{m.from}</span>
                      <span className="ab-arrow" aria-hidden>
                        &rarr;
                      </span>
                      <span className="ab-datepill ab-datepill--new">{m.to}</span>
                    </div>
                  ))}
                </div>
              )}

              {change.moved.length === 0 && (
                <div className="ab-note">
                  No other tasks needed rebalancing for this proposal.
                </div>
              )}

              {actionError && (
                <div className="ab-banner ab-banner--error" role="alert">
                  <div>{actionError}</div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="ab-modal__foot">
          <button
            type="button"
            className="ab-btn ab-btn--ghost"
            onClick={onClose}
            disabled={busy !== "idle"}
          >
            Close
          </button>
          <button
            type="button"
            className="ab-btn ab-btn--danger"
            onClick={handleCancel}
            disabled={phase !== "ready" || busy !== "idle" || !stillPending}
          >
            {busy === "cancelling" ? "Cancelling…" : "Cancel proposal"}
          </button>
          <button
            ref={primaryRef}
            type="button"
            className="ab-btn ab-btn--primary"
            onClick={handleApply}
            disabled={phase !== "ready" || busy !== "idle" || !stillPending}
          >
            {busy === "applying" ? "Applying…" : "Apply changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
