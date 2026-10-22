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
