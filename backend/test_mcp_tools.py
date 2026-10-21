"""Local tests for the M3 MCP boundary (backend/mcp_server.py).

These tests require NO AWS credentials, NO Bedrock, NO network access,
NO SQLite, and NO real M2 implementation: every service function is
monkeypatched with an in-memory fake.

They prove three things:
  1. Exactly the five approved model-facing tools are registered.
  2. Each wrapper delegates to the correct services function with the
     correct arguments and returns the service result unchanged.
  3. The safety boundary is structural: apply_changes, cancel_change, and
     get_change are not, and cannot be, discovered through the MCP server.

Run from backend/:

    python -m pytest test_mcp_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.types import TextContent

import mcp_server
import services
from mcp_server import MODEL_TOOLS, mcp


def run(coro):
    """Drive a coroutine without pytest-asyncio (not a dependency here)."""
    return asyncio.run(coro)


def payload(result):
    """Rebuild the Python object a tool returned from FastMCP content blocks.

    FastMCP serializes a list result as one TextContent block per element
    and wraps other non-dict results as {"result": ...}; normalize both so
    tests compare against the raw service return value.
    """
    assert result and all(isinstance(b, TextContent) for b in result)
    bodies = [json.loads(b.text) for b in result]
    if len(bodies) > 1:
        return bodies
    body = bodies[0]
    if isinstance(body, dict) and set(body) == {"result"}:
        return body["result"]
    return body


class FakeService:
    """Records the last call and returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.args = None
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        return self.result


@pytest.fixture
def fakes(monkeypatch):
    """Patch all five M2 service names on the real `services` module."""
    registry = {
        "create_commitment": FakeService({"id": 1, "title": "T"}),
        "get_commitments": FakeService([{"id": 1, "title": "T"}, {"id": 2, "title": "U"}]),
        "create_plan": FakeService(
            [{"step": "draft report", "when": "morning"}, {"step": "review", "when": "afternoon"}]
        ),
        "detect_conflicts": FakeService(
            [{"with": 2, "reason": "same day"}, {"with": 3, "reason": "capacity"}]
        ),
        "propose_replan": FakeService({"change_id": 42, "summary": "moved to 2026-11-03"}),
    }
    for name, fake in registry.items():
        monkeypatch.setattr(services, name, fake, raising=False)
    return registry


# ---------------------------------------------------------------------------
# 1. Registration surface
# ---------------------------------------------------------------------------


def test_exactly_five_model_facing_tools_registered():
    tools = run(mcp.list_tools())
    assert sorted(t.name for t in tools) == sorted(MODEL_TOOLS)
    assert len(tools) == 5


def test_every_tool_has_a_model_facing_description():
    for tool in run(mcp.list_tools()):
        assert tool.description and tool.description.strip(), tool.name


def test_create_commitment_input_schema_matches_contract():
    tool = next(t for t in run(mcp.list_tools()) if t.name == "create_commitment")
    schema = tool.inputSchema
    assert set(schema["properties"]) == {"title", "type", "due_date", "priority"}
    assert schema["required"] == ["title", "type", "due_date"]
    assert schema["properties"]["type"]["type"] == "string"


def test_server_module_name_is_actionbridge():
    assert mcp.name == "ActionBridge"


# ---------------------------------------------------------------------------
# 2. Structural safety boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", mcp_server.NON_MODEL_TOOLS)
def test_never_exposed_as_model_tools(forbidden):
    # Not discoverable through the MCP tool surface...
    tool_names = [t.name for t in run(mcp.list_tools())]
    assert forbidden not in tool_names
    # ...not registered in the underlying FastMCP tool manager at all...
    assert forbidden not in [t.name for t in mcp._tool_manager.list_tools()]
    # ...and cannot be invoked, even by name, through the server.
    with pytest.raises(Exception):
        run(mcp.call_tool(forbidden, {}))
    # The wrapper module also defines no handler that could be re-registered
    # by accident later.
    assert not hasattr(mcp_server, forbidden)


def test_non_model_tools_constant():
    assert set(mcp_server.NON_MODEL_TOOLS) == {"apply_changes", "cancel_change", "get_change"}
    assert not set(MODEL_TOOLS) & set(mcp_server.NON_MODEL_TOOLS)


# ---------------------------------------------------------------------------
# 3. Delegation: correct function, correct arguments, unchanged result
# ---------------------------------------------------------------------------


def test_create_commitment_delegates_with_all_arguments(fakes):
    fake = fakes["create_commitment"]
    result = run(
        mcp.call_tool(
            "create_commitment",
            {"title": "T", "type": "work", "due_date": "2026-10-30", "priority": "high"},
        )
    )
    assert fake.kwargs == {
        "title": "T",
        "type": "work",
        "due_date": "2026-10-30",
        "priority": "high",
    }
    assert payload(result) == fake.result


def test_create_commitment_omitted_priority_defaults_to_none(fakes):
    fake = fakes["create_commitment"]
    run(mcp.call_tool("create_commitment", {"title": "T", "type": "work", "due_date": "2026-10-30"}))
    assert fake.kwargs["priority"] is None


def test_get_commitments_delegates_filters(fakes):
    fake = fakes["get_commitments"]
    result = run(mcp.call_tool("get_commitments", {"status": "active", "title_query": "report"}))
    assert fake.kwargs == {"status": "active", "title_query": "report"}
    assert payload(result) == fake.result  # list returned unchanged


def test_get_commitments_defaults_are_none(fakes):
    fake = fakes["get_commitments"]
    run(mcp.call_tool("get_commitments", {}))
    assert fake.kwargs == {"status": None, "title_query": None}


def test_create_plan_delegates_with_no_arguments(fakes):
    fake = fakes["create_plan"]
    result = run(mcp.call_tool("create_plan", {}))
    assert fake.args == () and fake.kwargs == {}
    assert payload(result) == fake.result


def test_detect_conflicts_delegates(fakes):
    fake = fakes["detect_conflicts"]
    result = run(mcp.call_tool("detect_conflicts", {"commitment_id": 3, "new_due_date": "2026-11-03"}))
    assert fake.kwargs == {"commitment_id": 3, "new_due_date": "2026-11-03"}
    assert payload(result) == fake.result


def test_propose_replan_delegates_and_returns_change_id(fakes):
    fake = fakes["propose_replan"]
    result = run(mcp.call_tool("propose_replan", {"commitment_id": 3, "new_due_date": "2026-11-03"}))
    assert fake.kwargs == {"commitment_id": 3, "new_due_date": "2026-11-03"}
    body = payload(result)
    assert body["change_id"] == 42  # pending change surfaced, nothing applied


# ---------------------------------------------------------------------------
# 4. Error handling: service errors are not caught and hidden
# ---------------------------------------------------------------------------


def test_service_exception_propagates_to_caller(monkeypatch):
    def boom(**kwargs):
        raise ValueError("due date is not a valid ISO date")

    monkeypatch.setattr(services, "create_commitment", boom, raising=False)
    with pytest.raises(Exception) as exc_info:
        run(mcp.call_tool("create_commitment", {"title": "T", "type": "work", "due_date": "bad"}))
    # The original service error message reaches the developer, unhidden.
    assert "due date is not a valid ISO date" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. M2 missing-code handling: loud failure, no fake business logic
# ---------------------------------------------------------------------------


def test_missing_m2_function_fails_loudly(monkeypatch):
    monkeypatch.delattr(services, "propose_replan", raising=False)
    with pytest.raises(Exception) as exc_info:
        run(mcp.call_tool("propose_replan", {"commitment_id": 1, "new_due_date": "2026-11-03"}))
    message = str(exc_info.value)
    assert "propose_replan" in message
    assert "services.py" in message  # developer-facing hint toward the M2 gap


def test_service_fn_helper_raises_runtime_error(monkeypatch):
    monkeypatch.delattr(services, "get_commitments", raising=False)
    with pytest.raises(RuntimeError, match="M2 service function 'get_commitments'"):
        mcp_server._service_fn("get_commitments")


def test_mcp_server_imports_with_empty_services():
    """The current repo state (empty services.py) must not break import."""
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(mcp_server.__file__).parent
    proc = subprocess.run(
        [sys.executable, "-c", "import mcp_server"],
        cwd=backend,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    # And with the empty services module, the tools are still registered:
    # the failure boundary is invocation time, not import time.
