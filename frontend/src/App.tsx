
import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import "./components/actionbridge.css";
import { api, ApiError } from "./api";
import type {
  ApplyChangeResponse,
  CancelChangeResponse,
  ChatResponse,
  Commitment,
  PlanItem,
} from "./types";
import ChatPanel from "./components/ChatPanel";
import ConfirmModal from "./components/ConfirmModal";
import TodayPlan from "./components/TodayPlan";
import CommitmentsPanel from "./components/CommitmentsPanel";
import ConflictChecker from "./components/ConflictChecker";
import { formatLongDate } from "./format";
import { createDemoSource, DEMO_BANNER_TEXT, isDemoMode } from "./demoData";

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

/** Resolved once: a page is either talking to the backend or to demo data. */
const DEMO_MODE = isDemoMode();

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

  // Built once from the mount-time reference date: keeping a stable identity here
  // is what stops the load effect from re-firing in a loop when it swaps between
  // the live API and the demo fixtures.
  const [demoSource] = useState(() => createDemoSource(resolveReferenceDate()));
  const source = useMemo(() => (DEMO_MODE ? demoSource : api), [demoSource]);

  useEffect(() => {
    writeStoredPendingIds(pendingIds);
  }, [pendingIds]);

  useEffect(() => {
    if (!flash) return undefined;
    const timer = window.setTimeout(() => setFlash(null), 12_000);
    return () => window.clearTimeout(timer);
  }, [flash]);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (mode === "initial") setLoading(true);
      else setRefreshing(true);
      try {
        const [c, p] = await Promise.all([source.getCommitments(), source.getPlan()]);
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
    },
    [source],
  );

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
            {DEMO_MODE ? (
              <span className="ab-badge ab-badge--warning" title={DEMO_BANNER_TEXT}>
                Demo data
              </span>
            ) : looksOffline(dataError) ? (
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

          {DEMO_MODE && (
            <div className="ab-banner ab-banner--warning" style={{ marginBottom: 16 }} role="status">
              <div>{DEMO_BANNER_TEXT}</div>
            </div>
          )}

          {view !== "assistant" && (
            <>
              {view === "today" && (
                <div className="ab-grid ab-grid--two">
                  <TodayPlan
                    items={todayItems}
                    today={referenceDate}
                    loading={loading}
                    error={dataError}
                    onRetry={handleRefresh}
                  />
                  <CommitmentsPanel
                    title="Upcoming deadlines"
                    commitments={activeCommitments}
                    today={referenceDate}
                    loading={loading}
                    error={dataError}
                    onRetry={handleRefresh}
                    maxVisible={6}
                    showNextUp
                  />
                </div>
              )}

              {view === "upcoming" && (
                <div className="ab-grid ab-grid--two">
                  <TodayPlan
                    title="Full plan"
                    items={plan}
                    today={referenceDate}
                    groupByDate
                    loading={loading}
                    error={dataError}
                    onRetry={handleRefresh}
                    emptyHint="Capture a few commitments with the Assistant to build a plan."
                  />
                  <CommitmentsPanel
                    title="All commitments"
                    commitments={commitments}
                    today={referenceDate}
                    loading={loading}
                    error={dataError}
                    onRetry={handleRefresh}
                  />
                </div>
              )}

              {view === "conflicts" && (
                <div style={{ maxWidth: 860 }}>
                  <ConflictChecker
                    commitments={activeCommitments}
                    plan={plan}
                    today={referenceDate}
                    loading={loading}
                    error={dataError}
                    onRetry={handleRefresh}
                    getConflicts={source.getConflicts}
                  />
                </div>
              )}
            </>
          )}

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
