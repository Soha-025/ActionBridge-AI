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
