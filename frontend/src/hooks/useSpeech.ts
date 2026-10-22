/**
 * useSpeech — isolated browser-native voice helpers for the chat UI (M2).
 *
 * Zero dependencies: wraps window.SpeechRecognition (Web Speech API) for
 * speech-to-text and window.speechSynthesis for text-to-speech. Nothing here
 * touches the backend, api.ts, or any other team component.
 *
 * Language is pinned to en-US for both recognition and speech.
 *
 * Graceful fallback: every consumer gets a `supported` flag; when the browser
 * lacks the API the hooks become no-ops and the UI hides/disables the control
 * — no throws, no alerts on mount.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const SPEECH_LANG = "en-US";

// ---------------------------------------------------------------------------
// Minimal Web Speech API type declarations (TS DOM lib doesn't ship these).
// ---------------------------------------------------------------------------

interface SpeechRecognitionAlternative {
  readonly transcript: string;
  readonly confidence: number;
}

interface SpeechRecognitionResult {
  readonly length: number;
  isFinal: boolean;
  item(index: number): SpeechRecognitionAlternative;
  [index: number]: SpeechRecognitionAlternative;
}

interface SpeechRecognitionResultList {
  readonly length: number;
  item(index: number): SpeechRecognitionResult;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
  readonly message: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: ((event: Event) => void) | null;
  onstart: ((event: Event) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function friendlyRecognitionError(code: string): string {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone permission was denied. Allow mic access in your browser to use voice input.";
    case "no-speech":
      return "No speech detected — try again a little louder.";
    case "audio-capture":
      return "No microphone was found. Plug one in or check your device settings.";
    case "network":
      return "Voice recognition needs a network connection and it failed. Check your internet.";
    default:
      return "Voice input stopped unexpectedly.";
  }
}

// ---------------------------------------------------------------------------
// Speech-to-text
// ---------------------------------------------------------------------------

export interface DictationHandlers {
  /**
   * Called with the FULL live transcript on every result event (interim
   * included) so the composer can show dictation as it happens; the last
   * call for an utterance carries the finalized text.
   */
  onTranscript: (fullTranscript: string, isFinal: boolean) => void;
  /** Optional: fired on a recognition error with a friendly message. */
  onError?: (message: string) => void;
}

export interface UseSpeechRecognitionResult {
  /** False when the browser has no SpeechRecognition at all (e.g. Firefox). */
  supported: boolean;
  listening: boolean;
  error: string | null;
  start: () => void;
  stop: () => void;
  toggle: () => void;
}

export function useSpeechRecognition(
  handlers: DictationHandlers,
): UseSpeechRecognitionResult {
  const supported = useRef(getRecognitionCtor() !== null).current;

  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep the latest callbacks without re-creating the recognition instance.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const recRef = useRef<SpeechRecognitionLike | null>(null);
  // True while the user *wants* dictation active. Even a continuous session
  // is ended by Chrome after a short pause, so onend uses this flag to decide
  // whether to auto-restart (still wanted) or truly stop (manual stop()).
  const wantListenRef = useRef(false);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearRestart = useCallback(() => {
    if (restartTimerRef.current !== null) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    // Manual stop: forbid any pending/auto restart, then finalize.
    wantListenRef.current = false;
    clearRestart();
    try {
      recRef.current?.stop();
    } catch {
      /* already ended */
    }
    setListening(false);
  }, [clearRestart]);

  const start = useCallback(() => {
    const Ctor = getRecognitionCtor();
    if (!Ctor) return; // unsupported: silent no-op, UI already hid the button
    if (recRef.current) return; // already starting/started

    setError(null);
    wantListenRef.current = true;

    const rec = new Ctor();
    rec.lang = SPEECH_LANG;
    rec.continuous = true; // keep an open session…
    rec.interimResults = true; // …with live dictation in the composer
    rec.maxAlternatives = 1;

    rec.onstart = () => setListening(true);

    rec.onresult = (event) => {
      // In a continuous session `results` is cumulative for the session, so
      // rebuild the whole running transcript each time. The caller sets the
      // composer from a pre-recording snapshot, so this never double-appends.
      let full = "";
      let anyFinal = false;
      for (let i = 0; i < event.results.length; i += 1) {
        const result = event.results[i];
        full += result[0]?.transcript ?? "";
        if (result.isFinal) anyFinal = true;
      }
      handlersRef.current.onTranscript(full, anyFinal);
    };

    rec.onerror = (event) => {
      // Silence inside a continuous session is normal — keep listening.
      if (event.error === "no-speech" || event.error === "aborted") return;
      // Hard failures: stop for real so we never enter a restart loop.
      if (
        event.error === "not-allowed" ||
        event.error === "service-not-allowed" ||
        event.error === "audio-capture"
      ) {
        wantListenRef.current = false;
        clearRestart();
        const message = friendlyRecognitionError(event.error);
        setError(message);
        handlersRef.current.onError?.(message);
        return;
      }
      const message = friendlyRecognitionError(event.error);
      setError(message);
      handlersRef.current.onError?.(message);
    };

    rec.onend = () => {
      // Chrome ends even a continuous session on a pause. If the user still
      // wants to listen, reopen it shortly — calling start() synchronously
      // here throws InvalidStateError, hence the small delay.
      if (wantListenRef.current) {
        clearRestart();
        restartTimerRef.current = setTimeout(() => {
          restartTimerRef.current = null;
          if (!wantListenRef.current) return;
          try {
            rec.start();
          } catch {
            setListening(false);
            if (recRef.current === rec) recRef.current = null;
          }
        }, 300);
        return;
      }
      setListening(false);
      if (recRef.current === rec) recRef.current = null;
    };

    recRef.current = rec;
    try {
      rec.start();
    } catch {
      setListening(false);
      recRef.current = null;
    }
  }, [clearRestart]);

  const toggle = useCallback(() => {
    if (recRef.current) stop();
    else start();
  }, [start, stop]);

  // Never leave the mic session or a restart timer alive after unmount.
  useEffect(() => {
    return () => {
      wantListenRef.current = false;
      clearRestart();
      const rec = recRef.current;
      recRef.current = null;
      try {
        rec?.abort();
      } catch {
        /* already finished */
      }
    };
  }, [clearRestart]);

  return { supported, listening, error, start, stop, toggle };
}

// ---------------------------------------------------------------------------
// Text-to-speech
// ---------------------------------------------------------------------------

export interface UseSpeakResult {
  /** False when the browser has no speechSynthesis. */
  supported: boolean;
  /** Id of the message currently being read aloud (null = none). */
  speakingId: string | null;
  speak: (id: string, text: string) => void;
  stop: () => void;
  toggleSpeak: (id: string, text: string) => void;
}

export function useSpeak(): UseSpeakResult {
  const supported =
    typeof window !== "undefined" && "speechSynthesis" in window;

  const [speakingId, setSpeakingId] = useState<string | null>(null);
  // Id we *want* spoken; real voices only report back via events.
  const desiredIdRef = useRef<string | null>(null);

  // Chrome populates getVoices() asynchronously; warming it once means the
  // first Speak click finds a matching voice instead of staying silent.
  useEffect(() => {
    if (!supported) return;
    const synth = window.speechSynthesis;
    synth.getVoices();
    const warm = () => synth.getVoices();
    synth.addEventListener?.("voiceschanged", warm);
    return () => synth.removeEventListener?.("voiceschanged", warm);
  }, [supported]);

  const stop = useCallback(() => {
    desiredIdRef.current = null;
    if (!supported) return;
    try {
      window.speechSynthesis.cancel();
    } catch {
      /* nothing queued */
    }
    setSpeakingId(null);
  }, [supported]);

  const speak = useCallback(
    (id: string, text: string) => {
      if (!supported || !text.trim()) return;
      const synth = window.speechSynthesis;
      desiredIdRef.current = id;
      setSpeakingId(id);

      const startTalk = () => {
        try {
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = SPEECH_LANG;
          // Prefer an English voice; some builds stay silent with no match.
          const match = synth
            .getVoices()
            .find((v) => (v.lang || "").toLowerCase().startsWith("en"));
          if (match) utterance.voice = match;
          utterance.onend = () => {
            if (desiredIdRef.current === id) setSpeakingId(null);
          };
          utterance.onerror = () => {
            if (desiredIdRef.current === id) setSpeakingId(null);
          };
          synth.speak(utterance);
        } catch {
          setSpeakingId(null);
        }
      };

      try {
        // Chrome bug: cancel() immediately followed by speak() in the same
        // tick silently drops the new utterance. So only cancel what's playing
        // and re-speak a moment later; otherwise speak right away.
        if (synth.speaking || synth.pending) {
          synth.cancel();
          setTimeout(startTalk, 80);
        } else {
          synth.resume(); // clear any stuck "paused" state from a prior cancel
          startTalk();
        }
      } catch {
        setSpeakingId(null);
      }
    },
    [supported],
  );

  const toggleSpeak = useCallback(
    (id: string, text: string) => {
      if (speakingId === id) {
        stop();
        return;
      }
      speak(id, text);
    },
    [speakingId, speak, stop],
  );

  // Switching views / unmounting should not leave the assistant talking.
  useEffect(() => stop, [stop]);

  return { supported, speakingId, speak, stop, toggleSpeak };
}
