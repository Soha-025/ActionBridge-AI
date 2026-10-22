/**
 * ChatPanel (M1-owned): the AI Assistant conversation surface.
 *
 * Behavior backed by the REAL contract (POST /api/chat):
 * - Distinct user / assistant bubbles with safe text rendering.
 * - Multiline composer; Enter sends, Shift+Enter adds a newline.
 * - Send/loading state + duplicate-submission guard.
 * - Friendly error handling with retry of the failed message.
 * - Shows the actual agent `tool_calls` activity (name + status).
 * - Detects a real `pending_change_id` and hands it to the parent so the
 *   confirmation modal can open. Never fabricates a reply or change id.
 * - Conversation state belongs to this component and the component stays
 *   mounted while you navigate (App keeps it alive), so history survives a
 *   trip to Today/Upcoming/Conflicts. This is UI-session history only: the
 *   backend keeps no chat memory, `session_id` is not persisted server-side,
 *   and a page reload starts a new conversation.
 *
 * Voice additions (M2, additive only — original behavior unchanged):
 * - Mic button: browser-native dictation into the composer (en-US, live
 *   interim transcript). Existing send()/retry/pending-change flow untouched.
 * - Per-assistant-bubble speaker toggle + optional "Auto-read replies"
 *   switch. Both degrade gracefully when the browser lacks the APIs.
 * - See frontend/src/hooks/useSpeech.ts (isolated helper, no new deps).
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { api, ApiError } from "../api";
import { useSpeechRecognition, useSpeak } from "../hooks/useSpeech";
import type { ChatResponse, ToolCall } from "../types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  toolCalls?: ToolCall[];
  failed?: boolean;
  /** Assistant replied, but the provider was unreachable — not a real answer. */
  degraded?: boolean;
  /** Original user text to re-send when the assistant could not answer. */
  retryText?: string;
}

interface ChatPanelProps {
  /** Stable conversation id owned by the parent (survives view switches). */
  sessionId: string;
  /** True while the Assistant view is on screen — used to focus/scroll. */
  active: boolean;
  /** Parent opens the ConfirmModal with this real change id. */
  onPendingChange: (changeId: string) => void;
  /** Called after any assistant turn so the parent can refresh the dashboard. */
  onAfterTurn: (response: ChatResponse) => void;
}

const EXAMPLE_PROMPTS = [
  "I have a Statistics quiz Thursday, an AI assignment Friday and a presentation Saturday.",
  "What should I work on today?",
  "My AI assignment deadline changed from Friday to Wednesday.",
];

function newId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return `id-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function summarizeToolInput(input: Record<string, unknown>): string {
  const entries = Object.entries(input ?? {});
  if (!entries.length) return "";
  return entries
    .slice(0, 3)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(", ");
}

/**
 * Display heuristic — NOT a status code check. When the model provider fails,
 * backend/main.py still answers HTTP 200 with a fixed "temporarily
 * unavailable" sentence and empty tool_calls, so the UI cannot tell a degraded
 * reply apart from an answer by transport alone. We show the real text verbatim
 * but style it as a warning (never as a successful answer) and offer a retry.
 */
function looksLikeProviderFallback(text: string): boolean {
  return /temporarily unavailable|model provider/i.test(text);
}

export default function ChatPanel({
  sessionId,
  active,
  onPendingChange,
  onAfterTurn,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // --- Voice state (M2 additive) -------------------------------------------
  const [autoRead, setAutoRead] = useState(false);
  /** Composer text before the current dictation started. */
  const dictSnapshotRef = useRef("");
  /** Mirror of `draft` readable inside stable speech callbacks. */
  const draftRef = useRef("");
  draftRef.current = draft;
  /** Assistant messages auto-read already (never re-read on toggle). */
  const readIdsRef = useRef<Set<string>>(new Set());
  const lastMsgIdRef = useRef<string | null>(null);

  const dictation = useSpeechRecognition({
    onTranscript: (full) => {
      // Rebuild from the pre-recording snapshot + live transcript, so typed
      // text is preserved and interim results never double-append.
      const base = dictSnapshotRef.current.trimEnd();
      setDraft(base ? `${base} ${full}` : full);
    },
  });

  const tts = useSpeak();
  // Stable callbacks / primitives from the hook, so the effects below only
  // re-run on real changes rather than on every render (the `tts` object
  // itself is recreated each render).
  const { supported: ttsSupported, speak: ttsSpeak, stop: ttsStop } = tts;

  const handleMicClick = useCallback(() => {
    if (tts.speakingId) ttsStop();
    if (!dictation.listening) {
      dictSnapshotRef.current = draftRef.current;
    }
    dictation.toggle();
  }, [dictation, tts.speakingId, ttsStop]);

  // Auto-read: only brand-new, non-degraded assistant replies get spoken,
  // and only while the Assistant view is on screen.
  useEffect(() => {
    if (!autoRead || !ttsSupported) return;
    const last = messages[messages.length - 1];
    if (!last || last.id === lastMsgIdRef.current) return;
    lastMsgIdRef.current = last.id;
    if (last.role !== "assistant" || last.degraded) return;
    if (readIdsRef.current.has(last.id)) return;
    readIdsRef.current.add(last.id);
    if (active) ttsSpeak(last.id, last.text);
  }, [messages, autoRead, active, ttsSupported, ttsSpeak]);

  // Leaving the Assistant view silences any ongoing speech.
  useEffect(() => {
    if (!active) ttsStop();
  }, [active, ttsStop]);

  // Sending a new message cancels whatever is currently being read.
  useEffect(() => {
    if (sending) ttsStop();
  }, [sending, ttsStop]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending]);

  // Coming back to the Assistant view: the panel was kept mounted, so jump to
  // the newest message and hand focus to the composer.
  useEffect(() => {
    if (!active) return;
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
    inputRef.current?.focus();
  }, [active]);

  const send = useCallback(
    async (rawText: string) => {
      const text = rawText.trim();
      if (!text || sending) return; // guard against empty + duplicate submits

      setError(null);

      const userMsg: ChatMessage = { id: newId(), role: "user", text };
      setMessages((prev) => [...prev, userMsg]);
      setDraft("");
      setSending(true);

      try {
        const res = await api.chat({ session_id: sessionId, text });
        const replyText = res.reply?.trim() ? res.reply : "(The assistant returned no text.)";

        const assistantMsg: ChatMessage = {
          id: newId(),
          role: "assistant",
          text: replyText,
          toolCalls: res.tool_calls ?? [],
          degraded: looksLikeProviderFallback(replyText),
          retryText: text,
        };
        setMessages((prev) => [...prev, assistantMsg]);

        // Let the parent refresh data (a turn may have created commitments).
        onAfterTurn(res);

        // Surface a real pending change, if the agent proposed one.
        if (res.pending_change_id) {
          onPendingChange(res.pending_change_id);
        }
      } catch (err) {
        const message =
          err instanceof ApiError
            ? err.message
            : "Something went wrong while contacting the assistant.";
        setError(message);
        // Mark the just-sent user message as failed so its Retry re-sends it.
        // (One retry affordance per failure — the banner stays informational.)
        setMessages((prev) =>
          prev.map((m) => (m.id === userMsg.id ? { ...m, failed: true } : m)),
        );
      } finally {
        setSending(false);
        requestAnimationFrame(() => inputRef.current?.focus());
      }
    },
    [sending, sessionId, onAfterTurn, onPendingChange],
  );

  const handleKeyDown = useCallback(
    (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send(draft);
      }
    },
    [draft, send],
  );

  const isEmpty = messages.length === 0;

  return (
    <div className="ab-chat">
      <div className="ab-chat__scroll" ref={scrollRef}>
        {isEmpty && (
          <div>
            <div className="ab-empty" style={{ textAlign: "left", padding: "8px 0 0" }}>
              <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text)" }}>
                ActionBridge Assistant
              </div>
              <div style={{ marginTop: 6, maxWidth: 560 }}>
                Capture commitments, ask what to work on, or change a deadline. The
                assistant plans against your real, persisted backend data — proposed
                schedule changes always wait for your confirmation.
              </div>
            </div>
            <div className="ab-examples">
              {EXAMPLE_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="ab-chip"
                  onClick={() => void send(prompt)}
                  disabled={sending}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={`ab-msg ab-msg--${m.role}`}>
            <div className="ab-msg__avatar" aria-hidden>
              {m.role === "user" ? "You" : "AB"}
            </div>
            <div>
              <div
                className={`ab-msg__bubble${m.degraded ? " ab-msg__bubble--warning" : ""}`}
                role={m.degraded ? "alert" : undefined}
              >
                {m.text}
              </div>

              {m.role === "assistant" && m.toolCalls && m.toolCalls.length > 0 && (
                <div className="ab-toolcalls">
                  {m.toolCalls.map((tc, i) => (
                    <div className="ab-toolcall" key={`${m.id}-tc-${i}`}>
                      <span
                        className={
                          tc.status === "success"
                            ? "ab-badge ab-badge--success"
                            : tc.status === "rejected"
                              ? "ab-badge ab-badge--warning"
                              : "ab-badge ab-badge--danger"
                        }
                      >
                        {tc.status}
                      </span>
                      <code title={summarizeToolInput(tc.input)}>
                        {tc.name}
                        {summarizeToolInput(tc.input) ? ` · ${summarizeToolInput(tc.input)}` : ""}
                      </code>
                    </div>
                  ))}
                </div>
              )}

              {m.role === "assistant" && m.degraded && (
                <div style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    className="ab-btn ab-btn--sm"
                    onClick={() => void send(m.retryText ?? "")}
                    disabled={sending || !m.retryText}
                  >
                    Try again
                  </button>
                </div>
              )}

              {/* Voice: manual read-aloud toggle. Degraded fallback replies
                  are intentionally not speakable. */}
              {m.role === "assistant" && !m.degraded && tts.supported && (
                <div className="ab-msg__actions">
                  <button
                    type="button"
                    className="ab-btn ab-btn--ghost ab-btn--sm ab-speak-btn"
                    onClick={() => tts.toggleSpeak(m.id, m.text)}
                    aria-pressed={tts.speakingId === m.id}
                    title={
                      tts.speakingId === m.id
                        ? "Stop reading this reply"
                        : "Read this reply aloud"
                    }
                  >
                    {tts.speakingId === m.id ? "⏹ Stop" : "🔊 Speak"}
                  </button>
                </div>
              )}

              {m.role === "user" && m.failed && (
                <div style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    className="ab-btn ab-btn--sm"
                    onClick={() => {
                      setMessages((prev) =>
                        prev.map((x) => (x.id === m.id ? { ...x, failed: false } : x)),
                      );
                      void send(m.text);
                    }}
                    disabled={sending}
                  >
                    Retry
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}

        {sending && (
          <div className="ab-msg ab-msg--assistant">
            <div className="ab-msg__avatar" aria-hidden>
              AB
            </div>
            <div className="ab-msg__bubble" style={{ color: "var(--text-muted)" }}>
              Thinking…
            </div>
          </div>
        )}
      </div>

      {error && (
        <div style={{ padding: "0 18px" }}>
          <div className="ab-banner ab-banner--error" role="alert">
            <div>{error}</div>
          </div>
        </div>
      )}

      {/* Voice: dictation problems show here, separate from API errors. */}
      {dictation.error && (
        <div style={{ padding: "0 18px" }}>
          <div className="ab-banner ab-banner--warning" role="alert">
            <div>{dictation.error}</div>
          </div>
        </div>
      )}

      <div className="ab-chat__composer">
        {/* Voice: optional auto-read switch (lives here so no other team
            component needs to change). Hidden when TTS is unavailable. */}
        {tts.supported && (
          <div className="ab-voice-bar">
            <label className="ab-voice-toggle">
              <input
                type="checkbox"
                checked={autoRead}
                onChange={(e) => {
                  setAutoRead(e.target.checked);
                  if (!e.target.checked) tts.stop();
                }}
              />
              Auto-read new replies
            </label>
            {!dictation.supported && (
              <span className="ab-voice-note">
                Voice input isn’t supported in this browser — typing still works.
              </span>
            )}
          </div>
        )}

        <textarea
          ref={inputRef}
          className="ab-chat__textarea"
          placeholder="Message ActionBridge…  (Enter to send, Shift+Enter for a new line)"
          value={draft}
          rows={2}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={sending}
          aria-label="Message the assistant"
        />
        <div className="ab-chat__row">
          <span className="ab-chat__hint">
            {dictation.listening
              ? "Listening… speak now (en-US). Click Stop when you're done."
              : "Suggestions are grounded in your saved commitments. Changes are confirmed" +
                " before they are applied."}
          </span>
          <div className="ab-chat__controls">
            {/* Voice: mic button. Rendered only when the browser supports it;
                otherwise the toggle above shows a quiet note. */}
            {dictation.supported && (
              <button
                type="button"
                className={
                  dictation.listening
                    ? "ab-btn ab-btn--sm ab-mic-btn ab-mic-btn--live"
                    : "ab-btn ab-btn--sm ab-mic-btn"
                }
                onClick={handleMicClick}
                disabled={sending}
                aria-pressed={dictation.listening}
                title={
                  dictation.listening
                    ? "Stop dictation"
                    : "Dictate your message with the microphone"
                }
              >
                {dictation.listening ? "⏹ Stop" : "🎙 Speak"}
              </button>
            )}
            <button
              type="button"
              className="ab-btn ab-btn--primary"
              onClick={() => void send(draft)}
              disabled={sending || draft.trim().length === 0}
            >
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
