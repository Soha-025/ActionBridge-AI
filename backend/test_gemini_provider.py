"""Tests for the live Gemini provider (backend/gemini_provider.py).

NO live Gemini/API calls are made: the SDK client is faked and injected.
These cover the translation contract, tool safety, provider selection, and
that the existing ActionBridgeAgent can consume GeminiProvider output with no
architectural changes.

Run from backend/:

    python -m pytest test_gemini_provider.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

import gemini_provider
import services
from agent import TOOL_SCHEMAS, ActionBridgeAgent
from gemini_provider import GeminiProvider
from llm_provider import LLMError

ALLOWED = {
    "create_commitment", "get_commitments", "create_plan",
    "detect_conflicts", "propose_replan",
}


# ---------------------------------------------------------------------------
# Fakes for the google-genai "Interactions" client
# ---------------------------------------------------------------------------


class FakeStep:
    def __init__(self, type, name=None, arguments=None, id=None, content=None):
        self.type = type
        self.name = name
        self.arguments = arguments
        self.id = id
        self.content = content


class FakeInteraction:
    def __init__(self, steps=None, output_text=None, id="inter_1", usage=None):
        self.steps = steps or []
        self.output_text = output_text
        self.id = id
        self.usage = usage


class FakeInteractions:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._queue:
            raise AssertionError("Fake Gemini ran out of scripted responses")
        return self._queue.pop(0)


class FakeClient:
    def __init__(self, queue):
        self.interactions = FakeInteractions(queue)


class RawFakeStep:
    """A step that behaves like the *real* SDK step objects: it exposes
    ``model_dump`` so the provider can echo it back verbatim.

    The plain ``FakeStep`` has no ``model_dump`` (it exercises the reconstruct
    fallback). ``RawFakeStep`` is what reproduces the live multi-round shape --
    including the ``thought`` step that carries an encrypted ``signature`` the
    Interactions API requires on the follow-up turn.
    """

    def __init__(self, data):
        self._data = dict(data)
        self.type = data.get("type")
        self.name = data.get("name")
        self.arguments = data.get("arguments")
        self.id = data.get("id")
        self.content = data.get("content")

    def model_dump(self, mode="python", exclude_none=False):
        d = dict(self._data)
        if exclude_none:
            d = {k: v for k, v in d.items() if v is not None}
        return d


def provider_for(*interactions, api_key=None):
    client = FakeClient(list(interactions))
    return GeminiProvider(client=client, api_key=api_key or "test-key"), client


def user_msg(text):
    return [{"role": "user", "content": [{"text": text}]}]


# ---------------------------------------------------------------------------
# 1-2. Plain text + one tool call translation
# ---------------------------------------------------------------------------


def test_plain_text_translation():
    provider, client = provider_for(FakeInteraction(steps=[], output_text="Hello there"))
    resp = provider.converse(user_msg("hi"), tools=TOOL_SCHEMAS, system="SYS")

    assert resp["stop_reason"] == "end_turn"
    assert resp["message"] == {"role": "assistant", "content": [{"text": "Hello there"}]}

    call = client.interactions.calls[0]
    assert call["model"] == gemini_provider.DEFAULT_MODEL
    assert call["store"] is False                 # stateless, per the agent contract
    assert call["system_instruction"] == "SYS"
    assert call["input"][0] == {
        "type": "user_input", "content": [{"type": "text", "text": "hi"}],
    }
    assert len(call["tools"]) == 5


def test_single_tool_call_translation():
    provider, _ = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="create_plan", arguments={}, id="call_9")
        ])
    )
    resp = provider.converse(user_msg("plan please"), tools=TOOL_SCHEMAS, system="SYS")

    assert resp["stop_reason"] == "tool_use"
    assert resp["message"]["content"] == [
        {"toolUse": {"name": "create_plan", "input": {}, "toolUseId": "call_9"}}
    ]


def test_multiple_tool_calls_in_one_turn():
    provider, _ = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="create_commitment",
                     arguments={"title": "A", "type": "t", "due_date": "2026-11-01"}, id="c1"),
            FakeStep("function_call", name="create_commitment",
                     arguments={"title": "B", "type": "t", "due_date": "2026-11-02"}, id="c2"),
        ])
    )
    resp = provider.converse(user_msg("add two"), tools=TOOL_SCHEMAS, system="SYS")
    blocks = resp["message"]["content"]
    assert len(blocks) == 2
    assert [b["toolUse"]["toolUseId"] for b in blocks] == ["c1", "c2"]
    assert blocks[1]["toolUse"]["input"]["due_date"] == "2026-11-02"


# ---------------------------------------------------------------------------
# 3. Argument translation + multi-round history reconstruction
# ---------------------------------------------------------------------------


def test_tool_arguments_are_translated_to_dicts():
    provider, _ = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="detect_conflicts",
                     arguments={"commitment_id": 7, "new_due_date": "2026-11-03"}, id="cx")
        ])
    )
    resp = provider.converse(user_msg("conflict?"), tools=TOOL_SCHEMAS, system="SYS")
    use = resp["message"]["content"][0]["toolUse"]
    assert use["input"] == {"commitment_id": 7, "new_due_date": "2026-11-03"}


def test_build_history_maps_tooluse_and_toolresult_for_next_round():
    messages = [
        {"role": "user", "content": [{"text": "add a task"}]},
        {"role": "assistant", "content": [
            {"toolUse": {"name": "create_commitment",
                         "input": {"title": "Task", "type": "t", "due_date": "2026-11-01"},
                         "toolUseId": "call_1"}}]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "call_1", "status": "success",
                            "content": [{"text": json.dumps({"commitment": {"id": 1}})}]}}]},
    ]
    steps = GeminiProvider(client=FakeClient([]))._build_history(messages)

    assert [s["type"] for s in steps] == ["user_input", "function_call", "function_result"]
    assert steps[1]["name"] == "create_commitment"
    assert steps[1]["arguments"] == {"title": "Task", "type": "t", "due_date": "2026-11-01"}
    # function_result is linked back to its call by id + name (multi-round).
    assert steps[2]["call_id"] == "call_1"
    assert steps[2]["name"] == "create_commitment"
    assert json.loads(steps[2]["result"][0]["text"]) == {"commitment": {"id": 1}}


def test_multi_round_reasoning_completes():
    # Round 1: Gemini asks for a tool. Round 2: after the result, it answers.
    provider, client = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="create_plan", arguments={}, id="p1")
        ]),
        FakeInteraction(steps=[], output_text="Your plan is ready."),
    )
    first = provider.converse(user_msg("what now?"), tools=TOOL_SCHEMAS, system="SYS")
    assert first["stop_reason"] == "tool_use"

    follow_up = [
        {"role": "user", "content": [{"text": "what now?"}]},
        {"role": "assistant", "content": first["message"]["content"]},
        {"role": "user", "content": [
            {"toolResult": {"toolUseId": "p1", "status": "success",
                            "content": [{"text": json.dumps({"plan_items": []})}]}}]},
    ]
    second = provider.converse(follow_up, tools=TOOL_SCHEMAS, system="SYS")
    assert second["stop_reason"] == "end_turn"
    assert second["message"]["content"][0]["text"] == "Your plan is ready."
    # The second call carried the function_result back to Gemini.
    assert client.interactions.calls[1]["input"][-1]["type"] == "function_result"


# ---------------------------------------------------------------------------
# 4. Tool safety: only the five allowed tools are exposed
# ---------------------------------------------------------------------------


def test_only_five_allowed_declarations():
    decls = GeminiProvider(client=FakeClient([]))._declarations(TOOL_SCHEMAS)
    names = {d["name"] for d in decls}
    assert names == ALLOWED
    assert all(d["type"] == "function" for d in decls)


def test_forbidden_tools_are_filtered_out():
    poisoned = list(TOOL_SCHEMAS) + [
        {"toolSpec": {"name": "apply_changes", "description": "x",
                      "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        {"toolSpec": {"name": "cancel_change", "description": "x",
                      "inputSchema": {"json": {"type": "object", "properties": {}}}}},
        {"toolSpec": {"name": "get_change", "description": "x",
                      "inputSchema": {"json": {"type": "object", "properties": {}}}}},
    ]
    decls = GeminiProvider(client=FakeClient([]))._declarations(poisoned)
    names = {d["name"] for d in decls}
    assert "apply_changes" not in names
    assert "cancel_change" not in names
    assert "get_change" not in names
    assert names == ALLOWED


@pytest.mark.parametrize("enum_type", ["function_call", "Type.FUNCTION_CALL",
                                        "InteractionStepType.FUNCTION_CALL"])
def test_function_call_detection_tolerates_enums(enum_type):
    provider, _ = provider_for(FakeInteraction(steps=[
        FakeStep(enum_type, name="create_plan", arguments={}, id="c")
    ]))
    resp = provider.converse(user_msg("x"), tools=TOOL_SCHEMAS, system="SYS")
    assert resp["stop_reason"] == "tool_use"


# ---------------------------------------------------------------------------
# 5. Error handling never leaks the API key
# ---------------------------------------------------------------------------


class _BoomInteractions:
    def __init__(self, key):
        self.key = key

    def create(self, **kwargs):
        raise RuntimeError(f"Request failed with credentials {self.key}")


class BoomClient:
    def __init__(self, key):
        self.interactions = _BoomInteractions(key)


def test_api_error_is_wrapped_without_leaking_key():
    secret = "SECRET_GEMINI_KEY_123"
    provider = GeminiProvider(client=BoomClient(secret), api_key=secret)
    with pytest.raises(LLMError) as exc:
        provider.converse(user_msg("hi"), tools=TOOL_SCHEMAS, system="SYS")
    assert secret not in str(exc.value)
    assert "Request failed" in str(exc.value)  # still debuggable


class _GeminiAPIError(Exception):
    """Mimics google-genai's APIError: HTTP code + structured details."""
    def __init__(self, key):
        self.code = 400
        self.message = "Bad Request"
        self.details = {
            "error": {"message": (
                "Invalid input: a 'function_result' must be preceded by the "
                "model's 'thought' step and its 'function_call' (signature "
                f"missing). key={key}")}
        }
        super().__init__(self.message)


class _APIErrorInteractions:
    def create(self, **kwargs):
        raise _GeminiAPIError("SECRET_KEY_ABC")


class _APIErrorClient:
    def __init__(self):
        self.interactions = _APIErrorInteractions()


def test_api_error_surfaces_400_validation_details_without_leaking_key():
    secret = "SECRET_KEY_ABC"
    provider = GeminiProvider(client=_APIErrorClient(), api_key=secret)
    with pytest.raises(LLMError) as exc:
        provider.converse(user_msg("hi"), tools=TOOL_SCHEMAS, system="SYS")
    msg = str(exc.value)
    assert secret not in msg                       # key scrubbed even inside details
    assert "<GEMINI_KEY_REDACTED>" in msg
    assert "400" in msg and "thought" in msg       # real validation text surfaced


# ---------------------------------------------------------------------------
# 6. Provider selection (precedence + Bedrock stays default)
# ---------------------------------------------------------------------------


def test_selection_gemini_when_requested(monkeypatch):
    import main

    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    sentinel = object()
    monkeypatch.setattr(main, "GeminiProvider", lambda: sentinel)
    assert main.build_provider() is sentinel


def test_selection_demo_fallback_takes_precedence(monkeypatch):
    import main

    monkeypatch.setenv("DEMO_FALLBACK", "true")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")   # even with gemini requested...
    provider = main.build_provider()
    from demo_provider import DemoProvider
    assert isinstance(provider, DemoProvider)      # ...fallback wins


def test_selection_bedrock_is_default(monkeypatch):
    import main

    monkeypatch.delenv("DEMO_FALLBACK", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    sentinel = object()
    monkeypatch.setattr(main, "BedrockProvider", lambda: sentinel)
    assert main.build_provider() is sentinel


# ---------------------------------------------------------------------------
# 7. The real agent consumes GeminiProvider output + apply stays human-only
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "gemini.db"))
    monkeypatch.setenv("DEMO_TODAY", "2026-10-26")
    services.init_db()
    return str(tmp_path / "gemini.db")


def _run(messages, provider):
    return asyncio.run(ActionBridgeAgent(provider).chat(messages))


def test_agent_runs_gemini_tool_call_end_to_end(tmp_db):
    provider, _ = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="create_commitment",
                     arguments={"title": "Read paper", "type": "personal",
                                "due_date": "2026-11-05"}, id="c1")
        ]),
        FakeInteraction(steps=[], output_text="Saved 'Read paper' for you."),
    )
    result = _run(user_msg("add Read paper on Nov 5"), provider)

    assert result["reply"] == "Saved 'Read paper' for you."
    assert [tc["name"] for tc in result["tool_calls"]] == ["create_commitment"]
    assert result["tool_calls"][0]["status"] == "success"
    stored = services.get_commitments()["commitments"]
    assert {c["title"] for c in stored} == {"Read paper"}


def test_agent_rejects_apply_changes_even_if_model_requests_it(tmp_db, monkeypatch):
    def boom(*a, **k):  # pragma: no cover
        raise AssertionError("apply_changes must never run from the model path")

    monkeypatch.setattr(services, "apply_changes", boom)

    provider, _ = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="apply_changes",
                     arguments={"change_id": "abc"}, id="x1")
        ]),
        FakeInteraction(steps=[], output_text="I can't apply that here."),
    )
    result = _run(user_msg("apply it"), provider)

    # The agent's own allowlist refused the call before any service ran.
    assert result["tool_calls"][0]["status"] == "rejected"
    assert result["reply"] == "I can't apply that here."


# ---------------------------------------------------------------------------
# 8. REGRESSION: live multi-round function calling.
#
# Root cause of the HTTP 400: a thinking Gemini model returns a `thought` step
# (with an encrypted `signature`) alongside its `function_call`. The stateless
# Interactions API requires those model steps to be echoed back VERBATIM on the
# follow-up turn. The old provider rebuilt the `function_call` by hand from the
# ActionBridge toolUse block and dropped the `thought` step entirely, so the
# second request went out as [user_input, function_call, function_result] with
# no signature -> 400 Bad Request. The provider now caches and replays them.
# ---------------------------------------------------------------------------


def test_second_round_echoes_model_thought_signature_verbatim():
    thought = RawFakeStep({"type": "thought", "signature": "SIG_AB12", "summary": []})
    call = RawFakeStep({"type": "function_call", "id": "call_5",
                        "name": "get_commitments", "arguments": {"status": "active"}})
    provider, client = provider_for(
        FakeInteraction(steps=[thought, call]),
        FakeInteraction(steps=[], output_text="You have 3 items."),
    )

    # Round 1: model asks to call get_commitments.
    first = provider.converse(user_msg("what do I have?"), tools=TOOL_SCHEMAS, system="SYS")
    assistant_content = first["message"]["content"]        # the raw blocks the agent stores
    call_id = assistant_content[0]["toolUse"]["toolUseId"]
    # The original Gemini call id is preserved so the result can reference it.
    assert call_id == "call_5"

    # Agent appends the assistant toolUse turn + the MCP toolResult turn.
    follow_up = [
        {"role": "user", "content": [{"text": "what do I have?"}]},
        {"role": "assistant", "content": assistant_content},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "call_5", "status": "success",
            "content": [{"text": json.dumps({"commitments": []})}]}}]},
    ]
    second = provider.converse(follow_up, tools=TOOL_SCHEMAS, system="SYS")
    assert second["stop_reason"] == "end_turn"

    sent = client.interactions.calls[1]["input"]
    types = [s["type"] for s in sent]
    # The thought step is replayed *before* the function_call / function_result.
    assert types == ["user_input", "thought", "function_call", "function_result"], types
    echoed_thought = next(s for s in sent if s["type"] == "thought")
    assert echoed_thought["signature"] == "SIG_AB12"          # signature preserved
    echoed_call = next(s for s in sent if s["type"] == "function_call")
    assert echoed_call["id"] == "call_5"
    result_step = next(s for s in sent if s["type"] == "function_result")
    assert result_step["call_id"] == "call_5"                 # links back to the call


def test_error_tool_result_sets_is_error_flag():
    thought = RawFakeStep({"type": "thought", "signature": "SIGX", "summary": []})
    call = RawFakeStep({"type": "function_call", "id": "c9",
                        "name": "create_plan", "arguments": {}})
    provider, client = provider_for(
        FakeInteraction(steps=[thought, call]),
        FakeInteraction(steps=[], output_text="failed, but handled"),
    )
    provider.converse(user_msg("plan"), tools=TOOL_SCHEMAS, system="SYS")
    follow_up = [
        {"role": "user", "content": [{"text": "plan"}]},
        {"role": "assistant", "content": [{"toolUse": {"name": "create_plan", "input": {}, "toolUseId": "c9"}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "c9", "status": "error",
            "content": [{"text": "boom"}]}}]},
    ]
    provider.converse(follow_up, tools=TOOL_SCHEMAS, system="SYS")
    result_step = next(s for s in client.interactions.calls[1]["input"]
                       if s["type"] == "function_result")
    assert result_step["is_error"] is True


def test_legacy_reconstruct_fallback_when_steps_have_no_model_dump():
    # FakeStep (no model_dump) -> provider can't echo verbatim, so it rebuilds
    # the function_call. This keeps non-thinking/legacy behaviour working and
    # proves the cache is purely additive (no crash when model_dump is absent).
    provider, client = provider_for(
        FakeInteraction(steps=[
            FakeStep("function_call", name="create_plan", arguments={}, id="p1")
        ]),
        FakeInteraction(steps=[], output_text="done"),
    )
    provider.converse(user_msg("plan?"), tools=TOOL_SCHEMAS, system="SYS")
    follow_up = [
        {"role": "user", "content": [{"text": "plan?"}]},
        {"role": "assistant", "content": [{"toolUse": {"name": "create_plan", "input": {}, "toolUseId": "p1"}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "p1", "status": "success", "content": [{"text": "{}"}]}}]},
    ]
    provider.converse(follow_up, tools=TOOL_SCHEMAS, system="SYS")
    types = [s["type"] for s in client.interactions.calls[1]["input"]]
    assert types == ["user_input", "function_call", "function_result"]
