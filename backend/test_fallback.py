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
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fallback.db"))
    monkeypatch.setenv("DEMO_TODAY", DEMO_TODAY)
    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    services.init_db()
    return str(tmp_path / "fallback.db")


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
