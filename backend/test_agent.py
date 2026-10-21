"""Deterministic tests for the ActionBridge agent loop (backend/agent.py).

These tests require NO AWS, NO Bedrock, NO network, NO SQLite, and NO real
M2 implementation:

  * The LLM is a scripted FakeProvider (a real LLMProvider subclass).
  * M2 service functions are monkeypatched on the `services` module with
    in-memory fakes, exactly as in test_mcp_tools.py, so the agent's tool
    dispatch exercises the real MCP boundary (mcp_server.mcp.call_tool)
    without any database or business logic.

They prove the loop drives multi-turn tool use correctly, returns the
frontend-friendly contract, captures a REAL change_id, and — critically —
structurally refuses apply_changes / cancel_change / get_change.

Run from backend/:

    python -m pytest test_agent.py -v
"""

from __future__ import annotations

import asyncio

import pytest

import agent
import services
from agent import ActionBridgeAgent, AgentError, TOOL_SCHEMAS, build_system_prompt
from llm_provider import LLMError, LLMProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def text_block(text):
    return {"text": text}


def tool_use(name, tool_input, tool_use_id="t1"):
    return {"toolUse": {"name": name, "toolUseId": tool_use_id, "input": tool_input}}


def asst(content, stop_reason="tool_use"):
    return {
        "message": {"role": "assistant", "content": content},
        "stop_reason": stop_reason,
        "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    }


class FakeProvider(LLMProvider):
    """Replays scripted converse responses and records what it was sent."""

    def __init__(self, responses, repeat_last=False):
        self._responses = list(responses)
        self._repeat_last = repeat_last
        self._index = 0
        self.calls = []  # each: {"messages", "tools", "system"}

    def converse(self, messages, tools=None, system=None):
        self._last_messages_snapshot = [m for m in messages]
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if self._index < len(self._responses):
            resp = self._responses[self._index]
            self._index += 1
        elif self._repeat_last:
            resp = self._responses[-1]
        else:
            raise AssertionError("FakeProvider ran out of scripted responses")
        if isinstance(resp, Exception):
            raise resp
        return resp


class RecordingFake:
    """A stand-in M2 service function that records calls."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def fakes(monkeypatch):
    registry = {
        "create_commitment": RecordingFake({"id": 1, "title": "T"}),
        "get_commitments": RecordingFake([{"id": 1}, {"id": 2}]),
        "create_plan": RecordingFake([{"step": "a"}, {"step": "b"}]),
        "detect_conflicts": RecordingFake([{"with": 2}, {"with": 3}]),
        "propose_replan": RecordingFake({"change_id": 77, "summary": "moved"}),
    }
    for name, fake in registry.items():
        monkeypatch.setattr(services, name, fake, raising=False)
    return registry


# ---------------------------------------------------------------------------
# 1. Plain text, no tool use
# ---------------------------------------------------------------------------


def test_plain_text_response_no_tools():
    provider = FakeProvider([asst([text_block("Hello there.")], stop_reason="end_turn")])
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("hi")]}]))
    assert result["reply"] == "Hello there."
    assert result["pending_change_id"] is None
    assert result["tool_calls"] == []


# ---------------------------------------------------------------------------
# 2. One tool call, then final answer
# ---------------------------------------------------------------------------


def test_single_tool_then_final(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("create_plan", {})]),
            asst([text_block("Plan ready.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("plan")]}]))
    assert result["reply"] == "Plan ready."
    assert fakes["create_plan"].calls  # service actually reached via MCP
    assert result["tool_calls"] == [{"name": "create_plan", "input": {}, "status": "success"}]
    # toolResult from round 1 was fed back to the provider on round 2
    round2_messages = provider.calls[1]["messages"]
    assert round2_messages[-1]["role"] == "user"
    assert "toolResult" in round2_messages[-1]["content"][0]


# ---------------------------------------------------------------------------
# 3. Multiple tool calls in one assistant turn
# ---------------------------------------------------------------------------


def test_multiple_tools_one_turn(fakes):
    provider = FakeProvider(
        [
            asst(
                [
                    tool_use("get_commitments", {"status": "active"}, tool_use_id="t1"),
                    tool_use("create_plan", {}, tool_use_id="t2"),
                ]
            ),
            asst([text_block("Done.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("x")]}]))
    assert len(result["tool_calls"]) == 2
    assert {tc["name"] for tc in result["tool_calls"]} == {"get_commitments", "create_plan"}
    # Both toolResults are present in the single user message sent back
    feedback = provider.calls[1]["messages"][-1]["content"]
    assert len(feedback) == 2
    assert all("toolResult" in b for b in feedback)


# ---------------------------------------------------------------------------
# 4. Multi-round tool loop
# ---------------------------------------------------------------------------


def test_multi_round_loop(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("get_commitments", {}, tool_use_id="t1")]),
            asst([tool_use("detect_conflicts", {"commitment_id": 1, "new_due_date": "2026-11-03"}, tool_use_id="t2")]),
            asst([text_block("All set.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("move it")]}]))
    assert result["reply"] == "All set."
    assert [tc["name"] for tc in result["tool_calls"]] == ["get_commitments", "detect_conflicts"]
    assert len(provider.calls) == 3


# ---------------------------------------------------------------------------
# 5 & 6. pending_change_id capture
# ---------------------------------------------------------------------------


def test_propose_replan_captures_real_change_id(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("propose_replan", {"commitment_id": 5, "new_due_date": "2026-11-03"})]),
            asst([text_block("Proposed.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("resched")]}]))
    assert result["pending_change_id"] == 77  # real value from the fake tool result


def test_no_proposal_leaves_pending_none(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("create_plan", {})]),
            asst([text_block("Plan.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("x")]}]))
    assert result["pending_change_id"] is None


# ---------------------------------------------------------------------------
# 7 & 8. Provider sees exactly the five safe schemas, never apply_changes
# ---------------------------------------------------------------------------


def test_provider_receives_exactly_five_safe_schemas():
    provider = FakeProvider([asst([text_block("ok")], stop_reason="end_turn")])
    run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("hi")]}]))
    sent_names = {s["toolSpec"]["name"] for s in provider.calls[0]["tools"]}
    assert sent_names == {
        "create_commitment",
        "get_commitments",
        "create_plan",
        "detect_conflicts",
        "propose_replan",
    }
    assert len(provider.calls[0]["tools"]) == 5


def test_apply_changes_absent_from_provider_schemas():
    names = {s["toolSpec"]["name"] for s in TOOL_SCHEMAS}
    assert "apply_changes" not in names
    assert "cancel_change" not in names
    assert "get_change" not in names


# ---------------------------------------------------------------------------
# 9. Malicious/fake provider requests apply_changes -> refused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", ["apply_changes", "cancel_change", "get_change"])
def test_forbidden_tool_refused_and_never_reaches_service(fakes, forbidden):
    provider = FakeProvider(
        [
            asst([tool_use(forbidden, {"change_id": 77})]),
            asst([text_block("I can't apply that. Use the Apply Changes button.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("apply it")]}]))
    # Refused, recorded, and the loop continued to the model's safe reply
    assert result["reply"].startswith("I can't apply that.")
    assert result["tool_calls"][0]["status"] == "rejected"
    # No M2 service was ever invoked for the forbidden name
    assert forbidden not in {tc["name"] for tc in result["tool_calls"] if tc["status"] == "success"}
    # And the toolResult fed back says it was refused
    feedback = provider.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert feedback["status"] == "error"
    assert "Refused" in feedback["content"][0]["text"]


# ---------------------------------------------------------------------------
# 10. Unknown tool -> controlled rejection (not a crash)
# ---------------------------------------------------------------------------


def test_unknown_tool_rejected(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("drop_all_tables", {})]),
            asst([text_block("No such tool.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("x")]}]))
    assert result["tool_calls"][0]["status"] == "rejected"
    assert result["reply"] == "No such tool."


# ---------------------------------------------------------------------------
# 11. Provider/LLM error -> controlled AgentError, secret-free
# ---------------------------------------------------------------------------


def test_llm_error_becomes_agent_error():
    provider = FakeProvider([LLMError("Bedrock call failed: ThrottlingException")])
    with pytest.raises(AgentError) as exc_info:
        run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("hi")]}]))
    assert "Provider failed during chat" in str(exc_info.value)


def test_malformed_response_agent_error():
    provider = FakeProvider([{"not_a_message": True}])
    with pytest.raises(AgentError, match="Malformed provider response"):
        run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("hi")]}]))


# ---------------------------------------------------------------------------
# 12. Max iteration limit
# ---------------------------------------------------------------------------


def test_max_iterations_enforced(fakes, monkeypatch):
    monkeypatch.setattr(agent, "MAX_TOOL_ITERATIONS", 3)
    provider = FakeProvider(
        [asst([tool_use("create_plan", {})])],  # always requests a tool, never finishes
        repeat_last=True,
    )
    with pytest.raises(AgentError, match="exceeded 3 iterations"):
        run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("loop")]}]))
    # Exactly MAX_TOOL_ITERATIONS converse calls, then abort
    assert len(provider.calls) == 3


# ---------------------------------------------------------------------------
# 13. System prompt confirmation safety rule
# ---------------------------------------------------------------------------


def test_system_prompt_contains_confirmation_safety_rules():
    prompt = build_system_prompt()
    assert "PENDING" in prompt
    assert "NEVER claim" in prompt
    assert "Apply Changes" in prompt
    assert "invent a change_id" in prompt


# ---------------------------------------------------------------------------
# 14. DEMO_TODAY included when configured
# ---------------------------------------------------------------------------


def test_demo_today_included_when_configured(monkeypatch):
    monkeypatch.setenv("DEMO_TODAY", "2026-10-26")
    prompt = build_system_prompt()
    assert "2026-10-26" in prompt


def test_demo_today_absent_when_unset(monkeypatch):
    monkeypatch.delenv("DEMO_TODAY", raising=False)
    prompt = build_system_prompt()
    assert "Current date" not in prompt


# ---------------------------------------------------------------------------
# 15. "apply it" cannot cause an application (no apply tool exists)
# ---------------------------------------------------------------------------


def test_user_yes_cannot_apply(fakes):
    # Even when the user says "apply it", the model has no apply tool; the
    # best it can do is propose. Any attempt to call apply is refused.
    provider = FakeProvider(
        [
            asst([tool_use("apply_changes", {"change_id": 77})]),
            asst(
                [text_block("Please confirm using the Apply Changes button in the UI.")],
                stop_reason="end_turn",
            ),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("apply it")]}]))
    assert "Apply Changes" in result["reply"]
    assert all(tc["status"] != "success" for tc in result["tool_calls"] if tc["name"] == "apply_changes")
    # The propose_replan service was never invoked either (nothing applied)
    assert fakes["propose_replan"].calls == []


# ---------------------------------------------------------------------------
# Contract / shape sanity
# ---------------------------------------------------------------------------


def test_return_contract_keys(fakes):
    provider = FakeProvider([asst([text_block("hi")], stop_reason="end_turn")])
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("hi")]}]))
    assert set(result) == {"reply", "pending_change_id", "tool_calls"}


def test_tool_input_is_recorded_non_secret(fakes):
    provider = FakeProvider(
        [
            asst([tool_use("create_commitment", {"title": "Pay rent", "type": "personal", "due_date": "2026-11-01"})]),
            asst([text_block("Saved.")], stop_reason="end_turn"),
        ]
    )
    result = run(ActionBridgeAgent(provider).chat([{"role": "user", "content": [text_block("add rent")]}]))
    recorded = result["tool_calls"][0]
    assert recorded["name"] == "create_commitment"
    assert recorded["input"]["title"] == "Pay rent"
    # The service received the arguments through the MCP boundary.
    _, kwargs = fakes["create_commitment"].calls[0]
    assert kwargs["due_date"] == "2026-11-01"
