"""Tests for the deterministic Bedrock-quota fallback (DemoProvider).

These run the REAL ActionBridgeAgent + MCP boundary + services + SQLite,
with the offline DemoProvider in place of Bedrock, against a TEMP database.
No AWS/Bedrock/network is ever touched.

They prove the demo flow still works while Bedrock is throttled:
  * commitments are created (through MCP)
  * "what should I work on today?" builds a plan
  * a deadline change produces a REAL pending change via propose_replan
  * the returned pending_change_id comes from the real tool result
  * "apply it" never applies anything (state unchanged)
  * apply_changes is structurally unreachable from the model path

Run from backend/:

    python -m pytest test_fallback.py -v
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

import demo_provider
import mcp_server
import services
from agent import ActionBridgeAgent
from demo_provider import DemoProvider

DEMO_TODAY = "2026-10-26"  # Monday
THU, FRI, SAT, WED = "2026-10-29", "2026-10-30", "2026-10-31", "2026-10-28"
ALLOWED = {
    "create_commitment", "get_commitments", "create_plan",
    "detect_conflicts", "propose_replan",
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    # DemoProvider now uses the REAL system date, so tests freeze *its* clock by
    # mocking the single shared date source (`_today`). A Monday is used so the
    # fixed weekday constants below stay valid. DEMO_TODAY is still set because
    # services.* (unchanged, out of scope) reads it for its own date logic.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fallback.db"))
    monkeypatch.setenv("DEMO_TODAY", DEMO_TODAY)
    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    monkeypatch.setattr(demo_provider, "_today", lambda: date(2026, 10, 26))
    services.init_db()
    return str(tmp_path / "fallback.db")


@pytest.fixture
def tmp_db_oct22(tmp_path, monkeypatch):
    """Freeze 'today' to Thursday 2026-10-22 for BOTH provider and services."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "oct22.db"))
    monkeypatch.setenv("DEMO_TODAY", "2026-10-22")
    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    monkeypatch.setattr(demo_provider, "_today", lambda: date(2026, 10, 22))
    services.init_db()
    return str(tmp_path / "oct22.db")


def chat(text: str) -> dict:
    """One full agent turn with the deterministic provider."""
    agent = ActionBridgeAgent(DemoProvider())
    return asyncio.run(agent.chat([{"role": "user", "content": [{"text": text}]}]))


def _seed_three() -> None:
    services.create_commitment("Statistics quiz", "quiz", THU, "high")
    services.create_commitment("AI assignment", "assignment", FRI, "high")
    services.create_commitment("Presentation", "presentation", SAT, "medium")


def _titles(result: dict) -> set[str]:
    return {tc["name"] for tc in result["tool_calls"]}


# ---------------------------------------------------------------------------
# A. Create commitments (through MCP) + multiple items
# ---------------------------------------------------------------------------


def test_fallback_creates_commitments_through_mcp(tmp_db):
    result = chat(
        "I have a Statistics quiz Thursday, AI assignment Friday and "
        "presentation Saturday."
    )
    names = [tc["name"] for tc in result["tool_calls"]]
    assert names.count("create_commitment") == 3          # multiple commitments
    assert all(tc["status"] == "success" for tc in result["tool_calls"])
    assert _titles(result) <= ALLOWED                     # only safe tools

    stored = services.get_commitments()["commitments"]
    # Titles keep the wording/casing from the spoken sentence.
    assert {c["title"] for c in stored} == {"Statistics quiz", "AI assignment", "presentation"}
    dates = {c["title"]: c["due_date"] for c in stored}
    assert dates == {
        "Statistics quiz": THU,
        "AI assignment": FRI,
        "presentation": SAT,
    }


def test_fallback_tool_calls_execute_via_mcp_boundary(tmp_db, monkeypatch):
    seen: list[str] = []
    original = mcp_server.mcp.call_tool

    async def spy(name, arguments):
        seen.append(name)
        return await original(name, arguments)

    monkeypatch.setattr(mcp_server.mcp, "call_tool", spy)
    chat("I have a quiz Thursday and an assignment Friday.")
    assert "create_commitment" in seen                    # really went through MCP


# ---------------------------------------------------------------------------
# B. Today plan
# ---------------------------------------------------------------------------


def test_fallback_today_plan(tmp_db):
    _seed_three()
    result = chat("What should I work on today?")
    assert "create_plan" in _titles(result)
    assert "plan" in result["reply"].lower()
    # Earliest deadline is listed first.
    assert result["reply"].index("Statistics quiz") < result["reply"].index("Presentation")


# ---------------------------------------------------------------------------
# C. Deadline change -> real PENDING change
# ---------------------------------------------------------------------------


def test_fallback_deadline_change_creates_pending_change(tmp_db):
    _seed_three()
    result = chat("My AI assignment deadline changed from Friday to Wednesday.")

    # Used the real tools to locate, assess impact, and propose.
    assert {"get_commitments", "detect_conflicts", "propose_replan"} <= _titles(result)
    assert all(tc["status"] == "success" for tc in result["tool_calls"])

    change_id = result["pending_change_id"]
    assert change_id, "expected a real pending change id"

    # The id corresponds to a REAL, PENDING stored change.
    change = services.get_change(change_id)
    assert change["status"] == "pending"
    assert change["new_due_date"] == WED

    # Nothing was applied: the commitment keeps its original due date.
    assignment = next(
        c for c in services.get_commitments()["commitments"] if c["title"] == "AI assignment"
    )
    assert assignment["due_date"] == FRI
    # And the proposal is genuinely pending (not a duplicate applied row).
    assert "apply_changes" not in _titles(result)


def test_pending_change_id_matches_real_propose_replan_result(tmp_db, monkeypatch):
    _seed_three()
    captured: dict = {}
    original = services.propose_replan

    def spy(commitment_id, new_due_date):
        out = original(commitment_id, new_due_date)
        captured["change_id"] = out["change_id"]
        return out

    monkeypatch.setattr(services, "propose_replan", spy)
    result = chat("My AI assignment deadline changed from Friday to Wednesday.")
    assert captured["change_id"] == result["pending_change_id"]


# ---------------------------------------------------------------------------
# D. "Apply it" must NOT apply anything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", ["Apply it", "Confirm", "Yes"])
def test_apply_it_does_not_mutate_state(tmp_db, phrase):
    _seed_three()
    proposed = chat("My AI assignment deadline changed from Friday to Wednesday.")
    change_id = proposed["pending_change_id"]

    result = chat(phrase)
    assert _titles(result).isdisjoint(ALLOWED) or result["tool_calls"] == []  # no apply path
    assert "apply_changes" not in _titles(result)
    assert "button" in result["reply"].lower()

    # Still pending; assignment date untouched.
    assert services.get_change(change_id)["status"] == "pending"
    assignment = next(
        c for c in services.get_commitments()["commitments"] if c["title"] == "AI assignment"
    )
    assert assignment["due_date"] == FRI


def test_fallback_never_calls_apply_changes(tmp_db, monkeypatch):
    _seed_three()

    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("DemoProvider must never reach services.apply_changes")

    monkeypatch.setattr(services, "apply_changes", boom)
    chat("I have a quiz Thursday.")
    chat("My quiz deadline changed from Thursday to Wednesday.")
    chat("Apply it")  # would explode if it tried to apply


# ---------------------------------------------------------------------------
# B2. Timing / schedule follow-ups after a plan exists
# ---------------------------------------------------------------------------


_TIMING_PHRASES = [
    "timings?",
    "what time?",
    "what are the timings?",
    "schedule timings",
    "when should I do these?",
]


def _has_fabricated_clock_time(reply: str) -> bool:
    import re
    return bool(re.search(r"\b\d{1,2}(:\d{2})?\s?(AM|PM|am|pm|a\.m\.|p\.m\.|o'clock)\b", reply))


@pytest.mark.parametrize("phrase", _TIMING_PHRASES)
def test_timing_followup_is_recognized_and_not_rejected(tmp_db, phrase):
    _seed_three()
    result = chat(phrase)
    # It uses the real plan tool flow (no second planning system).
    assert "create_plan" in _titles(result)
    assert all(tc["status"] == "success" for tc in result["tool_calls"])
    # It answers rather than falling back to the "not part of the demo" reply.
    assert "isn't part of the offline demo" not in result["reply"]
    assert "Demo Mode" in result["reply"]


@pytest.mark.parametrize("phrase", _TIMING_PHRASES)
def test_timing_followup_reports_dates_without_fabricating_times(tmp_db, phrase):
    _seed_three()
    result = chat(phrase)
    reply = result["reply"]
    # Honest: scheduled by day/date, but no clock times assigned yet.
    assert "no specific" in reply and "clock times" in reply
    # It summarizes the REAL plan (the seeded dates appear).
    assert THU in reply and FRI in reply and SAT in reply
    # No invented hours like 9 AM / 2 PM.
    assert not _has_fabricated_clock_time(reply)


def test_timing_followup_no_plan_guides_user(tmp_db):
    # No commitments -> empty plan -> honest, does not fabricate a schedule.
    result = chat("timings?")
    assert "no plan" in result["reply"].lower()
    assert "add a commitment" in result["reply"].lower()
    assert not _has_fabricated_clock_time(result["reply"])


def test_timing_followup_preserves_safety(tmp_db):
    _seed_three()
    result = chat("when should I do these?")
    # A timing question never proposes or applies a change.
    assert result["pending_change_id"] is None
    assert "propose_replan" not in _titles(result)
    assert "apply_changes" not in _titles(result)
    assert _titles(result) <= ALLOWED


def test_timing_followup_returns_real_clock_times_if_present():
    # Provider-level proof (requirement #3): when the plan DOES carry times,
    # the real values are returned verbatim rather than the 'no clock times'
    # message. No services needed -- we drive two converse rounds directly.
    provider = DemoProvider()
    msgs = [{"role": "user", "content": [{"text": "timings?"}]}]
    first = provider.converse(msgs, tools=None, system=None)
    assert first["stop_reason"] == "tool_use"                       # asked for plan
    assert first["message"]["content"][0]["toolUse"]["name"] == "create_plan"
    call_id = first["message"]["content"][0]["toolUse"]["toolUseId"]
    plan = {"plan_items": [
        {"title": "AI assignment", "planned_date": FRI, "planned_time": "14:00"},
        {"title": "Statistics quiz", "planned_date": THU, "planned_time": "09:30"},
    ]}
    msgs += [
        {"role": "assistant", "content": first["message"]["content"]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": call_id, "status": "success",
            "content": [{"text": json.dumps({"result": plan})}],
        }}]},
    ]
    second = provider.converse(msgs, tools=None, system=None)
    reply = second["message"]["content"][0]["text"]
    assert "14:00" in reply and "09:30" in reply                    # real values echoed
    assert "no specific" not in reply                                # not the date-only branch


# ---------------------------------------------------------------------------
# Transparency + no-AWS guarantees
# ---------------------------------------------------------------------------


def test_fallback_replies_are_marked_and_hide_internals(tmp_db):
    result = chat("What should I work on today?")
    # Polished, honest demo tag is present...
    assert "Demo Mode" in result["reply"]
    # ...but internal provider / cloud-quota details are no longer exposed.
    for leak in ("Bedrock", "DemoProvider", "daily-token", "quota", "AWS"):
        assert leak not in result["reply"]


def test_fallback_requires_no_aws(tmp_path, monkeypatch):
    for key in ("AWS_REGION", "BEDROCK_MODEL_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "noaws.db"))
    monkeypatch.setenv("DEMO_TODAY", DEMO_TODAY)
    services.init_db()
    result = chat("I have a presentation Saturday.")
    assert result["tool_calls"][0]["status"] == "success"


# ---------------------------------------------------------------------------
# Provider selection in main.py (Bedrock stays primary)
# ---------------------------------------------------------------------------


def test_build_provider_defaults_to_bedrock(tmp_path, monkeypatch):
    import main

    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    sentinel = object()
    monkeypatch.setattr(main, "BedrockProvider", lambda: sentinel, raising=True)
    assert main.build_provider() is sentinel               # primary preserved


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
def test_build_provider_fallback_on(tmp_path, monkeypatch, value):
    import main

    monkeypatch.setenv("DEMO_FALLBACK", value)
    assert isinstance(main.build_provider(), DemoProvider)


# ---------------------------------------------------------------------------
# E. Generic natural-language handling (arbitrary input, no create_plan default)
# ---------------------------------------------------------------------------

NEXT_DAY = "2026-10-27"   # DEMO_TODAY (Mon 2026-10-26) + 1 = Tuesday
MON = "2026-10-26"        # today == tonight
OLD_RESTRICTIVE = "isn't part of the offline demo"


def _created_titles() -> set[str]:
    return {c["title"] for c in services.get_commitments()["commitments"]}


def _due(title: str) -> str | None:
    for c in services.get_commitments()["commitments"]:
        if c["title"] == title:
            return c["due_date"]
    return None


def test_generic_commitment_meeting_tomorrow(tmp_db):
    result = chat("I have meeting tomorrow")
    assert "create_commitment" in _titles(result)
    assert "create_plan" not in _titles(result)          # never default to plan
    assert _titles(result) <= ALLOWED
    assert "meeting" in _created_titles()
    assert _due("meeting") == NEXT_DAY


def test_dinner_tonight_creates_commitment_not_plan(tmp_db):
    # The headline intent-classification bug: must NOT call create_plan.
    result = chat("dinner tonight")
    assert "create_commitment" in _titles(result)
    assert "create_plan" not in _titles(result)
    assert "dinner" in _created_titles()
    assert _due("dinner") == MON                         # tonight -> today's DATE
    assert not _has_fabricated_clock_time(result["reply"])  # ...but no invented hour


@pytest.mark.parametrize(
    "phrase,expected_title,expected_due",
    [
        ("dentist Friday", "dentist", FRI),
        ("submit report Wednesday", "submit report", WED),
        ("call Sarah Monday", "call Sarah", MON),
    ],
)
def test_generic_commitment_phrasing(tmp_db, phrase, expected_title, expected_due):
    result = chat(phrase)
    assert "create_commitment" in _titles(result)
    assert "create_plan" not in _titles(result)
    assert expected_title in _created_titles()
    assert _due(expected_title) == expected_due


@pytest.mark.parametrize("phrase", ["hello", "hi", "hey"])
def test_greeting_is_natural_and_calls_no_tool(tmp_db, phrase):
    result = chat(phrase)
    assert result["tool_calls"] == []                    # no MCP call for a greeting
    assert "ActionBridge" in result["reply"]
    assert OLD_RESTRICTIVE not in result["reply"]
    assert "Demo Mode" in result["reply"]


@pytest.mark.parametrize("phrase", ["thanks", "thank you", "okay", "got it"])
def test_acknowledgement_is_brief_and_calls_no_tool(tmp_db, phrase):
    result = chat(phrase)
    assert result["tool_calls"] == []
    assert OLD_RESTRICTIVE not in result["reply"]
    assert "Demo Mode" in result["reply"]


@pytest.mark.parametrize(
    "phrase", ["what can you do?", "help", "how can you help me?"]
)
def test_capability_question_explains_without_overclaiming(tmp_db, phrase):
    result = chat(phrase)
    assert result["tool_calls"] == []                    # answering, not acting
    reply = result["reply"].lower()
    assert "commitment" in reply and "plan" in reply     # real capabilities only
    assert OLD_RESTRICTIVE not in result["reply"]


def test_unsupported_request_is_helpful_not_rejected(tmp_db):
    result = chat("tell me a joke")
    assert result["tool_calls"] == []                    # no random tool
    assert OLD_RESTRICTIVE not in result["reply"]
    assert "commitments" in result["reply"].lower()
    assert "I have a meeting tomorrow" in result["reply"]


def test_ambiguous_commitment_asks_for_date(tmp_db):
    result = chat("I have a meeting")
    assert result["tool_calls"] == []                    # must not guess a date
    assert "when" in result["reply"].lower()
    assert services.get_commitments()["commitments"] == []  # nothing fabricated


@pytest.mark.parametrize(
    "phrase",
    ["qwerty asdf zxcv", "the purple elephant dances", "12 34 56 ??", "banana"],
)
def test_unknown_input_never_defaults_to_create_plan(tmp_db, phrase):
    result = chat(phrase)
    # A useful reply, and crucially NOT a silent create_plan / create tool call.
    assert "create_plan" not in _titles(result)
    assert "apply_changes" not in _titles(result)
    assert _titles(result) <= ALLOWED
    assert OLD_RESTRICTIVE not in result["reply"]
    assert result["reply"].strip()


# ---------------------------------------------------------------------------
# F. The exact five model-facing tools are unchanged by this fix
# ---------------------------------------------------------------------------


def test_model_facing_tools_remain_exactly_five(tmp_db):
    registered = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert registered == ALLOWED
    assert "apply_changes" not in registered
    assert "cancel_change" not in registered
    assert "get_change" not in registered


@pytest.mark.parametrize(
    "phrase",
    ["I have meeting tomorrow", "dinner tonight", "tell me a joke",
     "what can you do?", "qwerty zxcv", "I have a meeting"],
)
def test_arbitrary_input_cannot_reach_apply(tmp_db, monkeypatch, phrase):
    # None of these should ever route through the human-only apply path.
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("apply_changes must be unreachable from chat")

    monkeypatch.setattr(services, "apply_changes", boom)
    result = chat(phrase)
    assert "apply_changes" not in _titles(result)



# ==========================================================================
# G. REAL-CURRENT-DATE ANCHORING (DEMO_TODAY is ignored by DemoProvider)
# ==========================================================================
# These freeze 'today' to Thursday 2026-10-22 via the tmp_db_oct22 fixture so
# they never depend on the machine's actual date. 2026-10-22 is a Thursday, so:
#   tomorrow = 2026-10-23, upcoming Friday = 2026-10-23, Saturday = 2026-10-24.
OCT22 = "2026-10-22"


def test_provider_ignores_DEMO_TODAY(monkeypatch):
    # The single shared date source must be the real system date, even when a
    # (now-obsolete) DEMO_TODAY is present in the environment.
    monkeypatch.setenv("DEMO_TODAY", "2030-01-01")
    assert demo_provider._today() == date.today()


def test_meeting_tonight_uses_real_today(tmp_db_oct22):
    result = chat("meeting tonight")
    assert result["tool_calls"][0]["name"] == "create_commitment"
    assert _due("meeting") == OCT22


def test_meeting_today_uses_real_today(tmp_db_oct22):
    result = chat("meeting today")
    assert _due("meeting") == OCT22


def test_meeting_tomorrow_resolves_to_next_day(tmp_db_oct22):
    chat("I have a meeting tomorrow")
    assert _due("meeting") == "2026-10-23"


def test_dinner_tonight_still_commitment_not_plan(tmp_db_oct22):
    result = chat("dinner tonight")
    assert result["tool_calls"][0]["name"] == "create_commitment"
    assert "create_plan" not in _titles(result)
    assert _due("dinner") == OCT22


def test_weekday_resolution_based_on_real_date(tmp_db_oct22):
    chat("meeting Friday")
    due = _due("meeting")
    assert due == "2026-10-23"  # the upcoming Friday after Thu 2026-10-22
    # Independent check: the resolved day really is a Friday and is not before
    # the mocked today -- proving it keyed off the current date, not a fixed one.
    resolved = date.fromisoformat(due)
    assert resolved.weekday() == 4
    assert resolved >= date(2026, 10, 22)


def test_weekday_thursday_on_or_after_today(tmp_db_oct22):
    # "on or after from_day": Thursday maps to today itself when today is Thu.
    chat("standup Thursday")
    assert _due("standup") == OCT22


def test_relative_and_weekday_agree_on_today(tmp_db_oct22):
    # Date consistency: tonight / today / tomorrow all key off the SAME source.
    chat("yoga tonight")
    chat("gym today")
    chat("run tomorrow")
    tonight = _due("yoga")
    today = _due("gym")
    tomorrow = _due("run")
    assert tonight == today == OCT22
    assert (date.fromisoformat(tomorrow) - date.fromisoformat(today)) == timedelta(days=1)


def test_plan_as_of_line_uses_real_today(tmp_db_oct22):
    # The assistant must not print a stale fixed demo date in its "as of" line.
    _seed_three()
    plan = chat("What should I work on today?")
    text = plan["reply"].replace("**", "")
    assert f"as of {OCT22}" in text
    assert "2026-10-26" not in text  # the old fake DEMO_TODAY must not leak
