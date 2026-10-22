
import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import "./components/actionbridge.css";
import { api, ApiError } from "./api";
import type {
  ApplyChangeResponse,
  CancelChangeResponse,
  ChatResponse,
  Commitment,
  Conflict,
  PlanItem,
} from "./types";
import ChatPanel from "./components/ChatPanel";
import ConfirmModal from "./components/ConfirmModal";

type ViewId = "today" | "upcoming" | "conflicts" | "assistant";

const NAV: { id: ViewId; label: string; icon: ReactElement }[] = [
  {
    id: "today",
    label: "Today",
    icon: (
      <svg className="ab-nav__ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="4" width="18" height="17" rx="2" />
        <path d="M3 9h18M8 2v4M16 2v4" />
      </svg>
    ),
  },
  {
    id: "upcoming",
    label: "Upcoming",
    icon: (
      <svg className="ab-nav__ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
      </svg>
    ),
  },
  {
    id: "conflicts",
    label: "Conflicts",
    icon: (
      <svg className="ab-nav__ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      </svg>
    ),
  },
  {
    id: "assistant",
    label: "Assistant",
    icon: (
      <svg className="ab-nav__ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" />
      </svg>
    ),
  },
];

const VIEW_TITLES: Record<ViewId, string> = {
  today: "Today",
  upcoming: "Upcoming plan",
  conflicts: "Conflict check",
  assistant: "Assistant",
};

function todayIso(): string {
  const d = new Date();
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60_000).toISOString().slice(0, 10);
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Reference "today" for the UI. The backend plans against its own DEMO_TODAY
 * (services._today()), which the browser cannot observe, so the demo date has to
 * be shared explicitly. Precedence: a server-provided date (not sent by the
 * current backend) > VITE_DEMO_TODAY (frontend/.env) > the machine clock.
 */
function envDemoToday(): string | null {
  const env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env;
  const value = env?.VITE_DEMO_TODAY?.trim();
  return value && ISO_DATE_RE.test(value) ? value : null;
}

function resolveReferenceDate(serverToday?: string): string {
  if (serverToday && ISO_DATE_RE.test(serverToday)) return serverToday;
  return envDemoToday() ?? todayIso();
}

function newSessionId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/**
 * Device-local record of proposal ids that were created by the backend and not
 * yet applied or cancelled. The backend has no "list pending changes" endpoint,
 * so this is only a way to reopen a proposal on this device/browser — the modal
 * always re-reads the real status from GET /api/changes/{id} before acting.
 */
const PENDING_STORE_KEY = "actionbridge:pending-changes";

function readStoredPendingIds(): string[] {
  try {
    const parsed: unknown = JSON.parse(globalThis.localStorage?.getItem(PENDING_STORE_KEY) ?? "[]");
    return Array.isArray(parsed)
      ? parsed.filter((v): v is string => typeof v === "string" && v.length > 0).slice(0, 5)
      : [];
  } catch {
    return [];
  }
}

function writeStoredPendingIds(ids: string[]): void {
  try {
    globalThis.localStorage?.setItem(PENDING_STORE_KEY, JSON.stringify(ids.slice(0, 5)));
  } catch {
    // Private mode / storage disabled: reopening falls back to the current page.
  }
}

function formatLongDate(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function formatDateShort(iso: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function App() {
  const [view, setView] = useState<ViewId>("today");

  // One conversation identity for the whole page life, owned here so switching
  // views cannot rotate it and orphans the chat.
  const [sessionId] = useState(newSessionId);

  const [commitments, setCommitments] = useState<Commitment[]>([]);
  const [plan, setPlan] = useState<PlanItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);

  const [modalChangeId, setModalChangeId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [pendingIds, setPendingIds] = useState<string[]>(readStoredPendingIds);
  const [flash, setFlash] = useState<{ tone: "success" | "info"; text: string } | null>(null);

  const [referenceDate, setReferenceDate] = useState(() => resolveReferenceDate());

  useEffect(() => {
    writeStoredPendingIds(pendingIds);
  }, [pendingIds]);

  useEffect(() => {
    if (!flash) return undefined;
    const timer = window.setTimeout(() => setFlash(null), 12_000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  const load = useCallback(async (mode: "initial" | "refresh") => {
    if (mode === "initial") setLoading(true);
    else setRefreshing(true);
    try {
      const [c, p] = await Promise.all([api.getCommitments(), api.getPlan()]);
      setCommitments(c.commitments ?? []);
      setPlan(p.plan_items ?? []);
      setReferenceDate(resolveReferenceDate(p.today));
      setDataError(null);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : "Could not load your dashboard from the backend.";
      setDataError(msg);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load("initial");
  }, [load]);

  const handleRefresh = useCallback(() => {
    void load("refresh");
  }, [load]);

  const rememberPending = useCallback((changeId: string) => {
    setPendingIds((prev) =>
      prev.includes(changeId) ? prev : [changeId, ...prev].slice(0, 5),
    );
  }, []);

  const forgetPending = useCallback((changeId: string) => {
    setPendingIds((prev) => prev.filter((id) => id !== changeId));
  }, []);

  /** Reopen a known proposal: the modal re-reads the live status from the API. */
  const openProposal = useCallback((changeId: string) => {
    setModalChangeId(changeId);
    setModalOpen(true);
    // Mirror the deep link so a reopened proposal survives a reload exactly like
    // one opened from ?change=<id>. handleModalClose strips the parameter again.
    const loc = globalThis.location;
    if (loc && globalThis.history?.replaceState) {
      const url = new URL(loc.href);
      if (url.searchParams.get("change") !== changeId) {
        url.searchParams.set("change", changeId);
        globalThis.history.replaceState(null, "", url.toString());
      }
    }
  }, []);

  /** Dismissing the UI must not leave a stale ?change=<id> in the address bar. */
  const handleModalClose = useCallback(() => {
    setModalOpen(false);
    const loc = globalThis.location;
    if (loc && globalThis.history?.replaceState && loc.search.includes("change=")) {
      const url = new URL(loc.href);
      url.searchParams.delete("change");
      globalThis.history.replaceState(null, "", url.toString());
    }
  }, []);

  const handlePendingChange = useCallback(
    (changeId: string) => {
      rememberPending(changeId);
      openProposal(changeId);
    },
    [rememberPending, openProposal],
  );

  // A change is only removed from the device list once the server says it is
  // no longer pending (applied / cancelled / unknown).
  const handleStatusObserved = useCallback(
    (changeId: string, status: string) => {
      if (status !== "pending") forgetPending(changeId);
      // "applied" observed without us having applied it here means the server's
      // data moved, so the dashboard on screen is stale: resync it.
      if (status === "applied") void load("refresh");
    },
    [forgetPending, load],
  );

  /** The server has no such proposal (404): stop offering it. Any other status
   *  may be a transient outage, so the id is kept and can be retried. */
  const handleLoadError = useCallback(
    (changeId: string, status: number) => {
      if (status !== 404) return;
      forgetPending(changeId);
      setFlash({
        tone: "info",
        text: "That proposal id does not exist on the server, so it is no longer listed here.",
      });
    },
    [forgetPending],
  );

  const handleApplied = useCallback(
    (result: ApplyChangeResponse) => {
      forgetPending(result.change_id);
      const moved = result.commitment;
      setFlash({
        tone: "success",
        text: moved
          ? `Applied — “${moved.title}” is now due ${moved.due_date} and the plan was refreshed (${result.plan_items.length} steps).`
          : `Applied — the backend confirmed this change and the plan was refreshed.`,
      });
      void load("refresh");
    },
    [forgetPending, load],
  );

  const handleTurnFinished = useCallback(
    (_res: ChatResponse) => {
      // A turn may have created or moved commitments; always resync from the API.
      void load("refresh");
    },
    [load],
  );

  const handleCancelled = useCallback(
    (changeId: string, _result: CancelChangeResponse) => {
      forgetPending(changeId);
      setFlash({
        tone: "info",
        text: "Proposal cancelled on the server. Nothing was applied — your plan is unchanged.",
      });
      void load("refresh");
    },
    [forgetPending, load],
  );

  // Deep link support: /?change=<id> opens that proposal straight from the API.
  useEffect(() => {
    const params = new URLSearchParams(globalThis.location?.search ?? "");
    const change = params.get("change");
    if (change) handlePendingChange(change);
  }, [handlePendingChange]);

  const activeCommitments = useMemo(
    () => commitments.filter((c) => c.status === "active"),
    [commitments],
  );

  const todayItems = useMemo(
    () => plan.filter((p) => p.planned_date === referenceDate),
    [plan, referenceDate],
  );
  const browserDate = todayIso();
  const usingDemoReference = referenceDate !== browserDate;

  return (
    <div className="ab-shell">
      <aside className="ab-sidebar">
        <div className="ab-brand">
          <div className="ab-brand__mark" aria-hidden>
            AB
          </div>
          <div>
            <div className="ab-brand__name">ActionBridge AI</div>
            <div className="ab-brand__sub">From intent to action</div>
          </div>
        </div>

        <nav className="ab-nav" aria-label="Primary">
          <div className="ab-nav__label">Workspace</div>
          {NAV.map((item) => (
            <button
              key={item.id}
              type="button"
              className="ab-nav__item"
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              {item.icon}
              <span>{item.label}</span>
              {item.id === "assistant" && pendingIds.length > 0 && (
                <span
                  className="ab-badge ab-badge--warning"
                  title={`${pendingIds.length} proposal(s) still awaiting your review`}
                >
                  {pendingIds.length}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="ab-sidebar__foot">
          Changes to your schedule are always pending until you confirm them.
        </div>
      </aside>

      <main className="ab-main">
        <header className="ab-topbar">
          <div>
            <h1 className="ab-topbar__title">{VIEW_TITLES[view]}</h1>
            <div className="ab-topbar__date">
              {formatLongDate(referenceDate)}
              {usingDemoReference && (
                <span
                  className="ab-badge ab-badge--warning"
                  style={{ marginLeft: 8 }}
                  title={`The backend plans against ${referenceDate} (DEMO_TODAY); this device's clock reads ${browserDate}.`}
                >
                  demo reference date
                </span>
              )}
            </div>
          </div>
          <div className="ab-topbar__actions">
            {looksOffline(dataError) ? (
              <span className="ab-badge ab-badge--danger">Offline</span>
            ) : (
              <span className="ab-badge">{activeCommitments.length} active</span>
            )}
            {pendingIds.length > 0 && (
              <button
                type="button"
                className="ab-btn ab-btn--primary"
                onClick={() => openProposal(pendingIds[0])}
              >
                Review proposal
              </button>
            )}
            <button
              type="button"
              className="ab-btn"
              onClick={handleRefresh}
              disabled={loading || refreshing}
              aria-label="Refresh data from the backend"
            >
              {refreshing ? "Refreshing…" : "Refresh"}
            </button>
          </div>
        </header>

        <div className="ab-content">
          {flash && (
            <div
              className={`ab-banner ab-banner--${flash.tone === "success" ? "success" : "info"} ab-flash`}
              role="status"
              aria-live="polite"
            >
              <span>{flash.text}</span>
              <button
                type="button"
                className="ab-btn ab-btn--sm ab-btn--ghost"
                onClick={() => setFlash(null)}
              >
                Dismiss
              </button>
            </div>
          )}

          {view !== "assistant" &&
            (loading ? (
              <LoadingSkeleton />
            ) : dataError && commitments.length === 0 ? (
              <ErrorState message={dataError} onRetry={handleRefresh} />
            ) : (
              <>
                {dataError && (
                  <div style={{ marginBottom: 16 }}>
                    <ErrorState message={dataError} onRetry={handleRefresh} />
                  </div>
                )}

                {view === "today" && (
                  <TodayView
                    today={referenceDate}
                    todayItems={todayItems}
                    commitments={activeCommitments}
                    planCount={plan.length}
                  />
                )}

                {view === "upcoming" && <UpcomingView plan={plan} />}

                {view === "conflicts" && <ConflictsView commitments={activeCommitments} />}
              </>
            ))}

          {/* Mounted for the whole life of the page: leaving the Assistant view
              hides it with display:none instead of destroying the conversation. */}
          <div
            className="ab-assistant-slot"
            style={{ display: view === "assistant" ? "flex" : "none" }}
          >
            <div className="ab-card">
              <div className="ab-card__head">
                <h2 className="ab-card__title">ActionBridge Assistant</h2>
                <span className="ab-card__hint">Grounded in your real, saved plan</span>
              </div>
              <div className="ab-card__body">
                <ChatPanel
                  sessionId={sessionId}
                  active={view === "assistant"}
                  onPendingChange={handlePendingChange}
                  onAfterTurn={handleTurnFinished}
                />
              </div>
            </div>
          </div>
        </div>
      </main>

      <ConfirmModal
        changeId={modalChangeId}
        open={modalOpen}
        onClose={handleModalClose}
        onApplied={handleApplied}
        onCancelled={handleCancelled}
        onStatusObserved={handleStatusObserved}
        onLoadError={handleLoadError}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ pieces */

function looksOffline(message: string | null): boolean {
  // Lightweight heuristic over the normalized ApiError message text.
  return !!message && /cannot reach|timed out/i.test(message);
}

function LoadingSkeleton() {
  return (
    <div className="ab-grid ab-grid--two">
      <div className="ab-card">
        <div className="ab-card__head">
          <div className="ab-skeleton" style={{ width: 160 }} />
        </div>
        <div className="ab-card__body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="ab-skeleton" style={{ height: 44 }} />
          <div className="ab-skeleton" style={{ height: 44 }} />
          <div className="ab-skeleton" style={{ height: 44 }} />
        </div>
      </div>
      <div className="ab-card">
        <div className="ab-card__head">
          <div className="ab-skeleton" style={{ width: 120 }} />
        </div>
        <div className="ab-card__body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div className="ab-skeleton" style={{ height: 30 }} />
          <div className="ab-skeleton" style={{ height: 30 }} />
        </div>
      </div>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="ab-banner ab-banner--error" role="alert">
      <div style={{ display: "flex", alignItems: "center", gap: 14, width: "100%" }}>
        <span style={{ flex: "1 1 auto" }}>{message}</span>
        <button type="button" className="ab-btn ab-btn--sm" onClick={onRetry}>
          Retry
        </button>
      </div>
    </div>
  );
}

function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="ab-empty">
      <div className="ab-empty__icon" aria-hidden>
        🗂️
      </div>
      <div style={{ fontWeight: 600, color: "var(--text)" }}>{title}</div>
      <div style={{ marginTop: 4 }}>{hint}</div>
    </div>
  );
}

function TodayView({
  today,
  todayItems,
  commitments,
  planCount,
}: {
  /** Reference date the backend plans against (see resolveReferenceDate). */
  today: string;
  todayItems: PlanItem[];
  commitments: Commitment[];
  planCount: number;
}) {
  const nextDue = useMemo(
    () =>
      [...commitments]
        .sort((a, b) => a.due_date.localeCompare(b.due_date))
        .find((c) => c.due_date >= today),
    [commitments, today],
  );

  return (
    <div className="ab-grid ab-grid--two">
      <section className="ab-card">
        <div className="ab-card__head">
          <h2 className="ab-card__title">Today&apos;s focus</h2>
          <span className="ab-card__hint">{todayItems.length} scheduled</span>
        </div>
        <div className="ab-card__body">
          {todayItems.length === 0 ? (
            <EmptyState
              title="Nothing scheduled for today"
              hint="Ask the Assistant what to work on, or add a commitment. Your plan is built from saved deadlines."
            />
          ) : (
            <div className="ab-list">
              {todayItems.map((item) => (
                <div className="ab-row" key={item.id}>
                  <span className="ab-dot" aria-hidden />
                  <div className="ab-row__main">
                    <div className="ab-row__title" title={item.title}>
                      {item.title}
                    </div>
                    <div className="ab-row__meta">
                      Planned {formatDateShort(item.planned_date)} · {item.status}
                    </div>
                  </div>
                  <span className="ab-badge ab-badge--primary">#{item.commitment_id}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="ab-card">
        <div className="ab-card__head">
          <h2 className="ab-card__title">Commitments</h2>
          <span className="ab-card__hint">{planCount} plan steps</span>
        </div>
        <div className="ab-card__body">
          {commitments.length === 0 ? (
            <EmptyState
              title="No active commitments"
              hint="Add one through the Assistant, e.g. “I have a Statistics quiz Thursday.”"
            />
          ) : (
            <>
              {nextDue && (
                <div className="ab-banner ab-banner--info" style={{ marginBottom: 12 }} role="status">
                  <div>
                    Next up: <strong>{nextDue.title}</strong> · due{" "}
                    {formatDateShort(nextDue.due_date)}
                  </div>
                </div>
              )}
              <div className="ab-list">
                {commitments.slice(0, 6).map((c) => (
                  <div className="ab-row" key={c.id}>
                    <div className="ab-row__main">
                      <div className="ab-row__title" title={c.title}>
                        {c.title}
                      </div>
                      <div className="ab-row__meta">
                        {c.type} · due {formatDateShort(c.due_date)}
                        {c.priority ? ` · ${c.priority}` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              {commitments.length > 6 && (
                <div className="ab-note" style={{ paddingTop: 10 }}>
                  Showing the first 6 of {commitments.length} active commitments — every
                  one is listed in Upcoming and checked by the Assistant.
                </div>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}

function UpcomingView({ plan }: { plan: PlanItem[] }) {
  const grouped = useMemo(() => {
    const map = new Map<string, PlanItem[]>();
    for (const item of [...plan].sort((a, b) => a.planned_date.localeCompare(b.planned_date))) {
      const arr = map.get(item.planned_date) ?? [];
      arr.push(item);
      map.set(item.planned_date, arr);
    }
    return Array.from(map.entries());
  }, [plan]);

  if (plan.length === 0) {
    return (
      <section className="ab-card">
        <div className="ab-card__body">
          <EmptyState
            title="Your plan is empty"
            hint="Capture a few commitments with the Assistant to build a plan."
          />
        </div>
      </section>
    );
  }

  return (
    <section className="ab-card">
      <div className="ab-card__head">
        <h2 className="ab-card__title">Full plan</h2>
        <span className="ab-card__hint">{plan.length} steps</span>
      </div>
      <div className="ab-card__body">
        {grouped.map(([date, items]) => (
          <div key={date} style={{ marginBottom: 8 }}>
            <div className="ab-card__hint" style={{ padding: "10px 4px 4px" }}>
              {formatLongDate(date)}
            </div>
            <div className="ab-list">
              {items.map((item) => (
                <div className="ab-row" key={item.id}>
                  <span className="ab-dot" aria-hidden />
                  <div className="ab-row__main">
                    <div className="ab-row__title" title={item.title}>
                      {item.title}
                    </div>
                    <div className="ab-row__meta">{item.status}</div>
                  </div>
                  <span className="ab-badge ab-mono">{formatDateShort(date)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function ConflictsView({ commitments }: { commitments: Commitment[] }) {
  const [selectedId, setSelectedId] = useState<string>("");
  const [targetDate, setTargetDate] = useState<string>("");
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<
    { status: "idle" | "ok" | "error"; message?: string; conflicts?: Conflict[] }
  >({ status: "idle" });

  const runCheck = useCallback(async () => {
    const commitmentId = Number(selectedId);
    if (!commitmentId || !/^\d{4}-\d{2}-\d{2}$/.test(targetDate)) {
      setResult({
        status: "error",
        message: "Pick a commitment and enter a valid target date (YYYY-MM-DD).",
      });
      return;
    }
    setChecking(true);
    try {
      const res = await api.getConflicts({ commitment_id: commitmentId, new_due_date: targetDate });
      setResult({ status: "ok", conflicts: res.conflicts ?? [] });
    } catch (err) {
      setResult({
        status: "error",
        message: err instanceof ApiError ? err.message : "Conflict check failed.",
      });
    } finally {
      setChecking(false);
    }
  }, [selectedId, targetDate]);

  const byId = useMemo(() => new Map(commitments.map((c) => [String(c.id), c])), [commitments]);

  return (
    <section className="ab-card" style={{ maxWidth: 760 }}>
      <div className="ab-card__head">
        <h2 className="ab-card__title">Conflict check</h2>
        <span className="ab-card__hint">Preview pressure before you commit to a move</span>
      </div>
      <div className="ab-card__body">
        <p className="ab-note" style={{ marginTop: 0 }}>
          The backend detects conflicts <em>for a proposed move</em> (a commitment plus a new
          due date). Choose a commitment and a target date to run a real check. The Assistant
          runs this automatically whenever you change a deadline.
        </p>

        {commitments.length === 0 ? (
          <EmptyState
            title="No commitments to check"
            hint="Add a commitment first, then preview how rescheduling it affects your week."
          />
        ) : (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end", margin: "12px 0" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, flex: "1 1 260px" }}>
                <span style={{ color: "var(--text-muted)" }}>Commitment</span>
                <select
                  className="ab-chat__textarea"
                  style={{ minHeight: 42, padding: "8px 12px" }}
                  value={selectedId}
                  onChange={(e) => setSelectedId(e.target.value)}
                >
                  <option value="">Select a commitment…</option>
                  {commitments.map((c) => (
                    <option key={c.id} value={String(c.id)}>
                      {c.title} (due {c.due_date})
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
                <span style={{ color: "var(--text-muted)" }}>New due date</span>
                <input
                  className="ab-chat__textarea"
                  style={{ minHeight: 42, padding: "8px 12px" }}
                  type="date"
                  value={targetDate}
                  onChange={(e) => setTargetDate(e.target.value)}
                />
              </label>
              <button
                type="button"
                className="ab-btn ab-btn--primary"
                onClick={() => void runCheck()}
                disabled={checking || !Number(selectedId) || !ISO_DATE_RE.test(targetDate)}
              >
                {checking ? "Checking…" : "Check conflicts"}
              </button>
            </div>

            {selectedId && byId.get(selectedId) && (
              <div className="ab-note" style={{ marginBottom: 10 }}>
                Checking “{byId.get(selectedId)!.title}” moving from{" "}
                {byId.get(selectedId)!.due_date} to {targetDate || "…"}.
                {ISO_DATE_RE.test(targetDate) &&
                  targetDate === byId.get(selectedId)!.due_date && (
                    <div>
                      That is already the current due date, so this preview describes the
                      schedule as it stands — nothing would move.
                    </div>
                  )}
              </div>
            )}

            {result.status === "error" && (
              <div className="ab-banner ab-banner--error" role="alert">
                <div>{result.message}</div>
              </div>
            )}

            {result.status === "ok" && (result.conflicts?.length ?? 0) === 0 && (
              <div className="ab-banner ab-banner--info" role="status">
                <div>No conflicts detected for that move. It looks safe to reschedule.</div>
              </div>
            )}

            {result.status === "ok" && (result.conflicts?.length ?? 0) > 0 && (
              <div className="ab-list">
                {result.conflicts!.map((c, i) => (
                  <div className="ab-row" key={`${c.commitment_id}-${i}`}>
                    <span
                      className={`ab-badge ${c.type === "collision" ? "ab-badge--danger" : "ab-badge--warning"}`}
                    >
                      {c.type}
                    </span>
                    <div className="ab-row__main">
                      <div className="ab-row__title" title={c.title}>
                        {c.title}
                      </div>
                      <div className="ab-row__meta">{c.reason}</div>
                    </div>
                    <span className="ab-badge ab-mono">{c.due_date}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
