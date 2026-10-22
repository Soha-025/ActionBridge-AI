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
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { api, ApiError } from "../api";
import type { ChatResponse, ToolCall } from "../types";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  toolCalls?: ToolCall[];
  failed?: boolean;
}

interface ChatPanelProps {
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

export default function ChatPanel({ onPendingChange, onAfterTurn }: ChatPanelProps) {
  // A stable per-session id for the backend ChatRequest contract.
  const sessionId = useMemo(() => newId(), []);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastFailedText, setLastFailedText] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, sending]);

  const send = useCallback(
    async (rawText: string) => {
      const text = rawText.trim();
      if (!text || sending) return; // guard against empty + duplicate submits

      setError(null);
      setLastFailedText(null);

      const userMsg: ChatMessage = { id: newId(), role: "user", text };
      setMessages((prev) => [...prev, userMsg]);
      setDraft("");
      setSending(true);

      try {
        const res = await api.chat({ session_id: sessionId, text });

        const assistantMsg: ChatMessage = {
          id: newId(),
          role: "assistant",
          text: res.reply?.trim() ? res.reply : "(The assistant returned no text.)",
          toolCalls: res.tool_calls ?? [],
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
        setLastFailedText(text);
        // Mark the just-sent user message as failed so retry re-sends it.
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
              <div className="ab-msg__bubble">{m.text}</div>

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
            <div style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }}>
              <span style={{ flex: "1 1 auto" }}>{error}</span>
              {lastFailedText && (
                <button
                  type="button"
                  className="ab-btn ab-btn--sm"
                  onClick={() => void send(lastFailedText)}
                  disabled={sending}
                >
                  Retry
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      <div className="ab-chat__composer">
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
            Suggestions are grounded in your saved commitments. Changes are confirmed
            before they are applied.
          </span>
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
  );
}
