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

  * It only understands the four ActionBridge demo intents (create
    commitments, "what should I work on today?", change a deadline, and a
    confirmation like "apply it"). Anything else gets a helpful canned reply.
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
import os
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
    "yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure",
    "do it", "go ahead", "apply it", "apply", "confirm",
    "confirm it", "yes please", "please apply",
}


def _today() -> date:
    """Deterministic 'today': DEMO_TODAY if set, else the real local date.

    Mirrors the fallback used by services so weekday reasoning is consistent.
    """
    demo = os.environ.get("DEMO_TODAY")
    if demo:
        try:
            return date.fromisoformat(demo.strip())
        except ValueError:
            pass
    return date.today()


def _next_weekday(from_day: date, target: int) -> date:
    """The upcoming ``target`` weekday (0=Mon) on or after ``from_day``."""
    return from_day + timedelta(days=(target - from_day.weekday()) % 7)


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
        if intent == "apply_confirm":
            return self._apply_confirm()
        return self._final(
            "Sorry, that request isn't part of the offline demo. I can record "
            "commitments with due days, tell you what to work on today, or "
            "propose a replan when a deadline changes."
        )

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

    # ----------------------- text-parsing helpers ------------------------ #
    @staticmethod
    def _classify(text: str) -> str:
        low = text.lower().strip()
        if re.search(r"\b(changed|change|moved|move|rescheduled?|postponed?|pushed?)\b.*\bto\b", low) \
                or ("deadline" in low and re.search(r"\bto\b", low)):
            return "deadline_change"
        if low in _CONFIRM_WORDS or any(
            low == w or re.search(rf"\b{re.escape(w)}\b", low) for w in ("apply", "confirm")
        ) and not any(w in low for w in ("commitment", "quiz", "assignment", "deadline")):
            return "apply_confirm"
        if re.search(rf"\b({_WEEKDAY_ALTERNATION})\b", low) and (
            re.search(r"\bi (have|need|must|got)|add |schedule |due\b", low)
            or "," in low or " and " in low
        ):
            return "create_commitments"
        if re.search(r"\b(today|tonight|plan|prioriti[sz]e|work on|what should|study)\b", low):
            return "today_plan"
        return "unknown"

    @staticmethod
    def _parse_items(text: str) -> list[dict]:
        """Extract dated commitments from a sentence into tool-call inputs."""
        body = re.sub(r"(?i)^\s*(i(?:'ve)?\s+(?:have|got|need(?:\s+to)?)|add|schedule|new)\s+",
                      "", text.strip())
        # Split on commas, semicolons, and the word "and" between chunks.
        parts = re.split(r",|;|\band\b", body, flags=re.IGNORECASE)
        items = []
        for chunk in parts:
            c = chunk.strip(" .")
            if not c:
                continue
            wd = None
            for token in re.findall(r"[A-Za-z]+", c):
                if token.lower() in _WEEKDAYS:
                    wd = _WEEKDAYS[token.lower()]
                    break
            if wd is None:
                continue  # no day of week -> skip (only handle weekday demo)
            # Title = the chunk minus the weekday token and leading articles.
            title = re.sub(rf"(?i)\b({_WEEKDAY_ALTERNATION})\b", "", c).strip(" -,.")
            title = re.sub(r"(?i)^(a|an|the|my|due on|due|is|on)\s+", "", title).strip()
            if not title:
                continue
            ctype, priority = "task", None
            for kw, t, p in _TYPE_HINTS:
                if kw in title.lower():
                    ctype, priority = t, p
                    break
            items.append({
                "title": title,
                "type": ctype,
                "due_date": _next_weekday(_today(), wd).isoformat(),
                "priority": priority,
            })
        return items

    @staticmethod
    def _new_due_date(text: str) -> str | None:
        """The NEW weekday in a '... from X to Y' phrase (or the last weekday)."""
        m = re.search(rf"(?i)\bto\s+({_WEEKDAY_ALTERNATION})\b", text)
        token = m.group(1) if m else None
        if token is None:
            toks = [t for t in re.findall(r"[A-Za-z]+", text) if t.lower() in _WEEKDAYS]
            token = toks[-1] if toks else None
        if token is None:
            return None
        return _next_weekday(_today(), _WEEKDAYS[token.lower()]).isoformat()

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
