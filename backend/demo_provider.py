"""Deterministic offline LLMProvider for the ActionBridge hackathon demo.

Purpose
-------
Amazon Bedrock currently returns a *daily-token* ``ThrottlingException`` for
the configured model(s). This is an external AWS quota limit, not an app bug,
but it would break the live demo. This module provides a small, rule-based
``DemoProvider`` so the demo stays fully functional WITHOUT Bedrock.

Crucially, this is a **provider-level** fallback: it plugs into the *same*
``ActionBridgeAgent`` loop, which *still* dispatches through the *same* MCP
boundary to the *same* services and SQLite database. It exercises the real
tool-use, multi-round agent loop — it does NOT bypass anything.

It is intentionally dumb and transparent:

  * It understands the ActionBridge demo intents -- create commitments, "what
    should I work on today?", a timing/schedule follow-up once a plan exists
    ("timings?", "what time?", "when should I do these?"), change a deadline,
    and a confirmation like "apply it". The timing follow-up reuses the SAME
    create_plan tool flow and only reports the real day/date values; it never
    invents clock times.
  * Beyond those scripted flows it responds usefully to ARBITRARY input: a
    generic commitment parser recognises natural phrasing ("I have a meeting
    tomorrow", "dinner tonight", "submit report Wednesday") without
    hard-coding activity nouns, greetings/acknowledgements/capability
    questions get short non-tool replies, an under-specified commitment asks
    for a date instead of guessing, and anything else gets a helpful
    product-oriented reply. It NEVER silently routes an unknown request to
    create_plan.
  * Dates are REAL, not simulated: relative-date reasoning (today/tonight/
    tomorrow/weekdays) and the "as of <date>" plan line are anchored to the
    actual current system date via ``date.today()``. DemoProvider deliberately
    ignores ``DEMO_TODAY`` so Demo Mode behaves like a real application; nobody
    has to set a fake demo date for it to make sense.
  * It ONLY ever emits the five model-facing tool calls. It never emits
    ``apply_changes`` / ``cancel_change`` / ``get_change`` -- and those are
    already unreachable through the agent's structural allowlist anyway.
  * It never pretends to be Bedrock: every final reply carries a visible
    fallback marker, and it fabricates no ``change_id`` (the agent extracts
    the real one from the actual ``propose_replan`` result).

The real ``BedrockProvider`` remains the default/primary provider; this is
selected only when ``DEMO_FALLBACK=true``.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from llm_provider import LLMProvider

# A short, polished note appended to every final fallback reply so the demo is
# honest about running in a scripted mode -- without exposing internal provider
# or cloud-quota details to end users.
FALLBACK_MARKER = "\n\n— ActionBridge Demo Mode"

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_WEEKDAY_ALTERNATION = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

# keyword -> (type, priority) for a light, deterministic classification.
_TYPE_HINTS = (
    ("quiz", "quiz", "high"),
    ("exam", "exam", "high"),
    ("test", "test", "high"),
    ("assignment", "assignment", "high"),
    ("homework", "assignment", "high"),
    ("presentation", "presentation", "medium"),
    ("project", "project", "medium"),
    ("meeting", "meeting", "low"),
    ("call", "meeting", "low"),
    ("report", "report", "medium"),
)

_CONFIRM_WORDS = {
    "yes", "y", "yeah", "yep", "yup",
    "do it", "go ahead", "apply it", "apply", "confirm",
    "confirm it", "yes please", "please apply",
}

# Simple timing/schedule follow-up questions asked AFTER a plan already exists.
# Deliberately narrow so it never shadows the create/deadline/today-plan intents.
_TIMING_PATTERNS = (
    r"\btimings?\b",
    r"\bwhat\s+time\b",
    r"\bwhat\s+times?\b",
    r"\bwhat\s+are\s+the\s+times?\b",
    r"\bany\s+times?\b",
    r"\bwhen\s+should\s+i\b",
    r"\bwhen\s+do\s+i\b",
    r"\bwhen\s+are\s+(?:these|they|my|the)\b",
)

_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)

# Relative day expressions resolved against the REAL current date (offset days).
# "tonight"/"today" map to today's DATE only -- never an invented clock time.
_RELATIVE_DAYS = {"tonight": 0, "today": 0, "tomorrow": 1}

# Longer alternatives first so "tonight" wins over "today"/"to".
_RELATIVE_ALTERNATION = "tonight|tomorrow|today"
_DATE_ALTERNATION = f"{_WEEKDAY_ALTERNATION}|{_RELATIVE_ALTERNATION}"

# Conversational intents that must NEVER trigger an MCP tool.
_GREETING_RE = re.compile(
    r"^(?:hi|hey|hello|hiya|howdy|yo|good\s+(?:morning|afternoon|evening))\b", re.I
)
_ACK_WORDS = {
    "thanks", "thank you", "thankyou", "thx", "ty", "cheers", "ok", "okay", "k",
    "got it", "gotcha", "alright", "cool", "nice", "great", "awesome", "perfect",
    "sure", "sure thing", "sounds good", "no problem", "np",
}
_CAPABILITY_RE = re.compile(
    r"what\s+can\s+you\s+do|what\s+do\s+you\s+do|how\s+can\s+you\s+help|"
    r"what\s+are\s+you\s+(?:able|capable)|your\s+(?:capabilities|features)|"
    r"^(?:please\s+)?help(?:\s+(?:me|out))?/?$|what\s+should\s+i\s+say|"
    r"how\s+do\s+i\s+use",
    re.I,
)
# Phrases signalling the user is stating a task, even without a date.
_COMMIT_LEADIN_RE = re.compile(
    r"\b(?:i(?:'ve| have| had)?\s+(?:got|have|need|must|want|would\s+like)|"
    r"i\s+need\s+to|i\s+must|remind\s+me|schedule|add\b|book\b|"
    r"set\s+(?:a\s+)?reminder|due(?:\s+on)?|appointment|deadline)\b",
    re.I,
)
# Leading words that make a line a *question* (plan/timing/etc.), not a commitment.
_QUESTION_RE = re.compile(
    r"^(?:what|when|how|why|who|where|can|could|should|would|will|do|does|did|"
    r"is|are|am|please|may|might)\b",
    re.I,
)


def _looks_like_timing(low: str) -> bool:
    return any(re.search(p, low) for p in _TIMING_PATTERNS)


def _today() -> date:
    """The real current system date.

    Demo Mode behaves like a real application: every relative-date calculation
    (today / tonight / tomorrow / weekdays) and the assistant's "as of" line are
    anchored to ``date.today()`` -- never a fake or fixed demo date. ``DEMO_TODAY``
    intentionally does NOT influence DemoProvider any more. Tests freeze the
    clock for determinism.
    """
    return date.today()


def _next_weekday(from_day: date, target: int) -> date:
    """The upcoming ``target`` weekday (0=Mon) on or after ``from_day``."""
    return from_day + timedelta(days=(target - from_day.weekday()) % 7)


def _resolve_date_token(token, base: date):
    """Resolve a weekday or relative token against the real current date."""
    if token is None:
        return None
    lt = token.lower()
    if lt in _WEEKDAYS:
        return _next_weekday(base, _WEEKDAYS[lt])
    if lt in _RELATIVE_DAYS:
        return base + timedelta(days=_RELATIVE_DAYS[lt])
    return None


def _find_date_token(text):
    """First weekday token (preferred) or relative token in ``text``."""
    rel = None
    for tok in re.findall(r"[A-Za-z]+", text or ""):
        lt = tok.lower()
        if lt in _WEEKDAYS:
            return tok
        if rel is None and lt in _RELATIVE_DAYS:
            rel = tok
    return rel


def _is_question(low: str) -> bool:
    return (
        low.endswith("?")
        or bool(_QUESTION_RE.match(low))
        or "should i" in low
        or "what should" in low
    )


def _is_greeting(low: str) -> bool:
    if not _GREETING_RE.match(low):
        return False
    if _find_date_token(low) or _COMMIT_LEADIN_RE.search(low):
        return False
    return len(low.split()) <= 5


def _is_acknowledgement(low: str) -> bool:
    return re.sub(r"[^a-z ]", "", low).strip() in _ACK_WORDS


def _is_capability(low: str) -> bool:
    return bool(_CAPABILITY_RE.search(low))


def _has_resolvable_date(text) -> bool:
    return _find_date_token(text) is not None


def _commitment_subject(text) -> str:
    """A best-effort noun phrase for an under-specified commitment."""
    t = re.sub(r"[?!.]+$", "", (text or "").strip())
    t = _COMMIT_LEADIN_RE.sub(" ", t)
    t = re.sub(rf"(?i)\b({_DATE_ALTERNATION})\b", " ", t)
    t = re.sub(r"(?i)\b(?:to|that|the|a|an|my|for|about)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,-:")
    return t or "it"


def _parse_json(text: str):
    """Tolerantly parse a tool result: unwraps FastMCP's {"result": ...}."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and set(data.keys()) == {"result"}:
        return data["result"]
    return data


class DemoProvider(LLMProvider):
    """Rule-based provider implementing the LLMProvider.converse() contract.

    Returns the exact dict shape the agent loop already understands, so the
    real agent/MCP/services code paths run unchanged.
    """

    def __init__(self):
        # Counter only used to mint stable, readable toolUseIds.
        self._id_seq = 0

    # --------------------------- public API ------------------------------ #
    def converse(self, messages, tools=None, system=None):
        req_idx, req_text = self._find_request(messages)
        if req_text is None:
            return self._final("I did not catch a request. Try adding "
                               "commitments, asking what to work on today, or "
                               "changing a deadline.")

        executed = self._executed(messages, req_idx)
        names_done = {e["name"] for e in executed}
        intent = self._classify(req_text)

        if intent == "deadline_change":
            return self._deadline_change(req_text, executed, names_done)
        if intent == "create_commitments":
            return self._create_commitments(req_text, executed, names_done)
        if intent == "today_plan":
            return self._today_plan(executed, names_done)
        if intent == "timing_followup":
            return self._timing_followup(executed, names_done)
        if intent == "apply_confirm":
            return self._apply_confirm()
        if intent == "greeting":
            return self._greeting_reply()
        if intent == "acknowledgement":
            return self._acknowledgement_reply()
        if intent == "capability":
            return self._capability_reply()
        if intent == "clarify_commitment":
            return self._clarify_commitment(req_text)
        # Unknown input gets a helpful product-oriented reply -- never create_plan.
        return self._unsupported()

    # ------------------------- request parsing --------------------------- #
    @staticmethod
    def _find_request(messages):
        """Index + text of the most recent human turn that carries 'text'."""
        found_idx, found_text = None, None
        for i, msg in enumerate(messages):
            if msg.get("role") != "user":
                continue
            for block in msg.get("content") or []:
                if isinstance(block, dict) and "text" in block:
                    found_idx, found_text = i, (block["text"] or "").strip()
        return found_idx, (found_text or None)

    @staticmethod
    def _executed(messages, req_idx):
        """Tool calls already made in response to the current request.

        Returns a list of {"name","input","result"} where result is the parsed
        tool output (dict) or None if not yet available.
        """
        by_id: dict[str, dict] = {}
        ordered: list[dict] = []
        for msg in messages[req_idx + 1:]:
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                use = block.get("toolUse")
                if isinstance(use, dict):
                    entry = {
                        "name": use.get("name", ""),
                        "input": use.get("input") or {},
                        "result": None,
                    }
                    by_id[use.get("toolUseId", "")] = entry
                    ordered.append(entry)
                res = block.get("toolResult")
                if isinstance(res, dict):
                    text = "".join(
                        (b.get("text") or "")
                        for b in (res.get("content") or [])
                        if isinstance(b, dict)
                    )
                    target = by_id.get(res.get("toolUseId", ""))
                    if target is not None:
                        target["result"] = _parse_json(text)
        return ordered

    # --------------------------- intents --------------------------------- #
    def _create_commitments(self, req_text, executed, names_done):
        items = self._parse_items(req_text)
        if not items:
            return self._final("I couldn't identify any dated commitments in "
                               "that. Try e.g. 'I have a quiz Thursday and an "
                               "assignment Friday.'")

        if "create_commitment" not in names_done:
            blocks = [self._tool_use("create_commitment", item) for item in items]
            return self._tool_turn(blocks)

        # Summarize from the real tool results (created vs already-existing).
        done = [e for e in executed if e["name"] == "create_commitment"]
        lines = []
        for e in done:
            title = (e["input"] or {}).get("title", "?")
            due = (e["input"] or {}).get("due_date", "?")
            res = e["result"] or {}
            existed = isinstance(res, dict) and res.get("already_exists")
            lines.append(f"  - {title} — due {due} ({'already recorded' if existed else 'saved'})")
        return self._final(
            "Got it — I've recorded these commitments:\n" + "\n".join(lines) +
            "\nAsk me 'what should I work on today?' to build your plan."
        )

    def _today_plan(self, executed, names_done):
        if "create_plan" not in names_done:
            return self._tool_turn([self._tool_use("create_plan", {})])

        plan_entry = next((e for e in executed if e["name"] == "create_plan"), None)
        res = (plan_entry or {}).get("result") or {}
        items = res.get("plan_items") if isinstance(res, dict) else None
        items = items or []
        if not items:
            return self._final(
                "You have no active commitments yet, so there's nothing to "
                "plan. Add a commitment first."
            )
        today = _today().isoformat()
        lines = [f"  {i}. {p.get('title')} — planned for {p.get('planned_date')}"
                 for i, p in enumerate(items, 1)]
        return self._final(
            f"Here's your plan (as of {today}):\n" + "\n".join(lines) +
            "\nWork top-to-bottom; the earliest deadline comes first."
        )

    def _timing_followup(self, executed, names_done):
        # Reuse the SAME create_plan tool flow to read the authoritative plan.
        if "create_plan" not in names_done:
            return self._tool_turn([self._tool_use("create_plan", {})])

        plan_entry = next((e for e in executed if e["name"] == "create_plan"), None)
        res = (plan_entry or {}).get("result") or {}
        items = res.get("plan_items") if isinstance(res, dict) else None
        items = items or []
        if not items:
            return self._final(
                "There's no plan to give timings for yet. Add a commitment and "
                "ask 'what should I work on today?' first, then I can lay out "
                "the schedule."
            )

        def _clock_time(p):
            # Return real clock-time info ONLY if the plan actually stores it.
            for key in ("planned_time", "time", "start_time", "clock_time"):
                val = p.get(key)
                if val:
                    return val
            return None

        timed = [p for p in items if _clock_time(p)]
        if timed:
            lines = [
                f"  {i}. {p.get('title', '?')} — {self._describe_day(p.get('planned_date'))}"
                + (f" at {_clock_time(p)}" if _clock_time(p) else " (time not set)")
                for i, p in enumerate(items, 1)
            ]
            return self._final(
                "Here are the scheduled times for your plan:\n" + "\n".join(lines)
            )

        # Day/date only, no clock times anywhere: be honest, do NOT invent times.
        lines = [
            f"  {i}. {p.get('title', '?')} — {self._describe_day(p.get('planned_date'))}"
            for i, p in enumerate(items, 1)
        ]
        return self._final(
            "ActionBridge has scheduled these by day/date, but no specific "
            "clock times have been assigned yet — so you can pick the hours "
            "that suit you. Here's the current order:\n" + "\n".join(lines) +
            "\nAsk me to reschedule any of them if a day doesn't work."
        )

    @staticmethod
    def _describe_day(iso) -> str:
        if not iso:
            return "date not set"
        try:
            d = date.fromisoformat(str(iso).strip())
        except ValueError:
            return str(iso)
        return f"{_WEEKDAY_NAMES[d.weekday()]} {d.isoformat()}"

    def _deadline_change(self, req_text, executed, names_done):
        new_date = self._new_due_date(req_text)
        target = self._change_target(req_text)
        if not new_date or not target:
            return self._final(
                "Tell me which commitment and the new day, e.g. 'My AI "
                "assignment deadline changed from Friday to Wednesday.'"
            )

        if "get_commitments" not in names_done:
            return self._tool_turn(
                [self._tool_use("get_commitments", {"title_query": target})]
            )

        cid = self._find_commitment_id(executed, target)
        if cid is None:
            return self._final(
                f"I couldn't find a saved commitment matching '{target}', so "
                "I haven't changed anything."
            )

        if "propose_replan" not in names_done:
            if "detect_conflicts" not in names_done:
                return self._tool_turn([self._tool_use(
                    "detect_conflicts",
                    {"commitment_id": cid, "new_due_date": new_date},
                )])
            return self._tool_turn([self._tool_use(
                "propose_replan",
                {"commitment_id": cid, "new_due_date": new_date},
            )])

        # Read the REAL change_id from the propose_replan result (never invent).
        prop = next((e for e in executed if e["name"] == "propose_replan"), None)
        res = (prop or {}).get("result") or {}
        change_id = res.get("change_id") if isinstance(res, dict) else None
        summary = res.get("summary") if isinstance(res, dict) else None
        if not change_id:
            return self._final("The replan could not be created, so nothing changed.")

        return self._final(
            f"I've drafted a replan: {summary or 'Move the commitment to ' + new_date + '.'}\n"
            f"This is PENDING (change_id: {change_id}) and has NOT been applied. "
            "Review it and click the 'Apply Changes' button to confirm."
        )

    def _apply_confirm(self):
        # Safety: never applies anything. The model has no apply tool at all.
        return self._final(
            "For safety, plan changes are never applied through chat. Please "
            "review the proposed change and click the 'Apply Changes' button "
            "in the app to confirm it."
        )

    # -------------- conversational / clarification (no tools) ------------ #
    def _greeting_reply(self):
        return self._final(
            "Hi! I'm ActionBridge — I can help you capture commitments, organize "
            "your plan, check for conflicts, and adjust deadlines. Try saying "
            "something like \"I have a meeting tomorrow.\""
        )

    def _acknowledgement_reply(self):
        return self._final(
            "Anytime. Add a commitment, ask what to work on today, or tell me "
            "when a deadline changes and I'll take it from there."
        )

    def _capability_reply(self):
        return self._final(
            "I focus on a few things and do them well:\n"
            "  - Capture commitments with due days (e.g. \"I have an exam Friday\").\n"
            "  - Build your plan (\"what should I work on today?\").\n"
            "  - Check for scheduling conflicts.\n"
            "  - Propose a replan when a deadline changes (you confirm before it applies).\n"
            "What would you like to do?"
        )

    def _clarify_commitment(self, req_text):
        subject = _commitment_subject(req_text)
        return self._final(
            f"Sure — when is {subject}? Give me a day such as tomorrow or a "
            "weekday and I'll note it down."
        )

    def _unsupported(self):
        return self._final(
            "I'm focused on helping you manage commitments, plans, deadlines, "
            "and schedule changes. Try telling me something you need to do, "
            "such as \"I have a meeting tomorrow.\""
        )

    # ----------------------- text-parsing helpers ------------------------ #
    @staticmethod
    def _classify(text: str) -> str:
        low = text.lower().strip()
        # 1. Deadline-change language stays a replan intent.
        if re.search(r"\b(changed|change|moved|move|rescheduled?|postponed?|pushed?)\b.*\bto\b", low) \
                or ("deadline" in low and re.search(r"\bto\b", low)):
            return "deadline_change"
        # 2. Pure conversational turns -- answered without any tool call.
        if _is_greeting(low):
            return "greeting"
        if _is_acknowledgement(low):
            return "acknowledgement"
        if _is_capability(low):
            return "capability"
        # 3. Bare confirmation of a pending change (still never applies).
        if low in _CONFIRM_WORDS or (
            any(re.search(rf"\b{re.escape(w)}\b", low) for w in ("apply", "confirm", "yes"))
            and not any(w in low for w in ("commitment", "quiz", "assignment", "deadline"))
        ):
            return "apply_confirm"
        # 4. Timing follow-up stays timing (checked before plan/commitment).
        if _looks_like_timing(low):
            return "timing_followup"
        # 5. A statement carrying a safely resolvable date -> commitment(s).
        if not _is_question(low) and _has_resolvable_date(low):
            return "create_commitments"
        # 6. Looks like a stated task but the date is missing -> ask, don't guess.
        if not _is_question(low) and _COMMIT_LEADIN_RE.search(low):
            return "clarify_commitment"
        # 7. Planning questions / requests.
        if ("plan" in low) or re.search(
            r"\b(what\s+should|work\s+on|prioriti[sz]e|study|what\s+do\s+i\s+have|anything\s+today)\b",
            low,
        ):
            return "today_plan"
        # 8. Anything else -> helpful product reply (never defaults to create_plan).
        return "unsupported"

    @staticmethod
    def _parse_items(text: str) -> list[dict]:
        """Extract dated commitments from a sentence into tool-call inputs."""
        body = re.sub(
            r"(?i)^\s*(?:i(?:'ve| have| had)?\s+(?:got|have|need(?:\s+to)?|must|want(?:\s+to)?|"
            r"would\s+like(?:\s+to)?)|remind\s+me(?:\s+to)?|set\s+(?:a\s+)?reminder(?:\s+to)?|"
            r"add|schedule|new)\s+",
            "", text.strip(),
        )
        # Split on commas, semicolons, and the word "and" between chunks.
        parts = re.split(r",|;|\band\b", body, flags=re.IGNORECASE)
        base = _today()
        items: list[dict] = []
        seen: set[str] = set()
        for chunk in parts:
            c = chunk.strip(" .")
            if not c:
                continue
            token = _find_date_token(c)
            due = _resolve_date_token(token, base)
            if due is None:
                continue  # no safely resolvable day -> skip this chunk
            # Title = the chunk minus the day token and leading filler.
            title = re.sub(rf"(?i)\b{re.escape(token)}\b", "", c).strip(" -,.")
            title = re.sub(r"(?i)^(a|an|the|my|due on|due|is|on)\s+", "", title).strip()
            if not title or re.sub(r"[^a-z]", "", title.lower()) == re.sub(r"[^a-z]", "", token.lower()):
                continue
            ctype, priority = "task", None
            for kw, t, p in _TYPE_HINTS:
                if kw in title.lower():
                    ctype, priority = t, p
                    break
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            items.append({
                "title": title,
                "type": ctype,
                "due_date": due.isoformat(),
                "priority": priority,
            })
        return items

    @staticmethod
    def _new_due_date(text: str) -> str | None:
        """The NEW day in a '... from X to Y' phrase (weekday or relative)."""
        m = re.search(rf"(?i)\bto\s+({_DATE_ALTERNATION})\b", text)
        token = m.group(1) if m else None
        if token is None:
            toks = [t for t in re.findall(r"[A-Za-z]+", text)
                    if t.lower() in _WEEKDAYS or t.lower() in _RELATIVE_DAYS]
            token = toks[-1] if toks else None
        due = _resolve_date_token(token, _today())
        return due.isoformat() if due else None

    @staticmethod
    def _change_target(text: str) -> str | None:
        """Best-effort title substring of the commitment being changed."""
        m = re.search(r"(?i)\bmy\s+(.+?)\s+deadline\b", text)
        if m:
            return m.group(1).strip()
        cleaned = re.sub(rf"(?i)\b({_WEEKDAY_ALTERNATION}|deadline|changed|change|from|to|my|the|it)\b",
                         " ", text)
        cleaned = re.sub(r"[^A-Za-z ]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or None

    @staticmethod
    def _find_commitment_id(executed, target: str) -> int | None:
        entry = next((e for e in executed if e["name"] == "get_commitments"), None)
        res = (entry or {}).get("result") or {}
        commitments = res.get("commitments") if isinstance(res, dict) else None
        if not commitments:
            return None
        needle = re.sub(r"\s+", " ", target.lower()).strip()
        # Prefer a title that contains the needle, else a shared-word match.
        for c in commitments:
            title = re.sub(r"\s+", " ", (c.get("title") or "").lower())
            if needle and (needle in title or title in needle):
                return c.get("id")
        for c in commitments:
            title = (c.get("title") or "").lower()
            if any(word in title for word in needle.split() if len(word) > 2):
                return c.get("id")
        return commitments[0].get("id") if len(commitments) == 1 else None

    # ----------------------- response builders --------------------------- #
    def _tool_use(self, name: str, arguments: dict) -> dict:
        self._id_seq += 1
        return {
            "toolUse": {
                "name": name,
                "input": arguments,
                "toolUseId": f"demo_{name}_{self._id_seq}",
            }
        }

    def _tool_turn(self, blocks: list[dict]) -> dict:
        return {
            "message": {"role": "assistant", "content": blocks},
            "stop_reason": "tool_use",
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        }

    def _final(self, text: str) -> dict:
        return {
            "message": {
                "role": "assistant",
                "content": [{"text": text + FALLBACK_MARKER}],
            },
            "stop_reason": "end_turn",
            "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        }
