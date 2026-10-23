/**
 * Shared loading / error / empty renderings for the M5 dashboard panels.
 *
 * Every panel shows the same three states, and each one names the data it is
 * waiting for so a stalled panel is obvious rather than looking like an empty
 * section.
 */

export function LoadingRows({ rows = 3, label }: { rows?: number; label: string }) {
  return (
    <div
      className="ab-list"
      style={{ display: "flex", flexDirection: "column", gap: 14 }}
      role="status"
      aria-live="polite"
    >
      <span className="ab-sr-only">Loading {label}</span>
      {Array.from({ length: rows }, (_, index) => (
        <div className="ab-skeleton" key={index} style={{ height: 40 }} />
      ))}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="ab-banner ab-banner--error" role="alert">
      <div style={{ display: "flex", alignItems: "center", gap: 14, width: "100%" }}>
        <span style={{ flex: "1 1 auto" }}>{message}</span>
        {onRetry && (
          <button type="button" className="ab-btn ab-btn--sm" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({
  icon = "🗂️",
  title,
  hint,
}: {
  icon?: string;
  title: string;
  hint: string;
}) {
  return (
    <div className="ab-empty">
      <div className="ab-empty__icon" aria-hidden>
        {icon}
      </div>
      <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
      <div style={{ marginTop: 4 }}>{hint}</div>
    </div>
  );
}

/**
 * Per-row check-off and delete controls shared by the Today plan rows and the
 * commitments rows. Presentational only: it never fetches or decides what a
 * click means — the container (App) supplies the handlers, so the backend call
 * and optimistic update stay in one place. Buttons disable while a row is busy
 * so a double-click cannot queue two requests.
 */
export interface ItemActionsProps {
  /** Whether the item is currently completed/checked (drives the tick icon). */
  done: boolean;
  /** Disable + show a working state for this row only. */
  busy?: boolean;
  onToggle: () => void;
  onDelete: () => void;
}

export function ItemActions({ done, busy = false, onToggle, onDelete }: ItemActionsProps) {
  return (
    <div className="ab-row__actions">
      <button
        type="button"
        className={`ab-row__action ab-row__action--tick${done ? " is-done" : ""}`}
        onClick={onToggle}
        disabled={busy}
        aria-pressed={done}
        title={done ? "Mark as not done" : "Mark complete"}
        aria-label={done ? "Mark as not done" : "Mark complete"}
      >
        {done ? (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
            <path d="M3 3v5h5" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
            <path d="M20 6 9 17l-5-5" />
          </svg>
        )}
      </button>
      <button
        type="button"
        className="ab-row__action ab-row__action--delete"
        onClick={onDelete}
        disabled={busy}
        title="Delete"
        aria-label="Delete"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
          <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m2 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
          <path d="M10 11v6M14 11v6" />
        </svg>
      </button>
    </div>
  );
}
