"""Integration tests for the wired FastAPI backend (backend/main.py).

These use Starlette's TestClient against the real app and the real service
layer on a temporary SQLite DB (via DB_PATH). NO AWS, NO Bedrock, NO network:
the agent/provider is monkeypatched. They verify routing to real logic, the
human-confirmation Apply/Cancel boundary, error mapping, the safe chat
fallback, that nothing leaks provider internals, and that CORS still works.

Run from backend/:

    python -m pytest test_main.py -v
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

import main
import services
from agent import AgentError
from llm_provider import LLMError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the whole app + services at an isolated temp database."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "main_test.db"))
    monkeypatch.delenv("DEMO_TODAY", raising=False)
    services.init_db()
    return str(tmp_path / "main_test.db")


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _isolate_sessions():
    """Reset per-session chat history so tests never inherit each other's turns."""
    main._SESSION_HISTORY.clear()
    yield
    main._SESSION_HISTORY.clear()


class FakeAgent:
    """Stands in for ActionBridgeAgent; records messages, returns a canned dict."""

    def __init__(self, result=None, error=None):
        self._result = result or {"reply": "ok", "pending_change_id": None, "tool_calls": []}
        self._error = error
        self.received = None

    async def chat(self, messages):
        self.received = messages
        if self._error is not None:
            raise self._error
        return self._result


# ---------------------------------------------------------------------------
# 1. App import / startup without live Bedrock
# ---------------------------------------------------------------------------


def test_app_imports_without_live_bedrock(client):
    # Simply constructing the client exercised import + route setup with no AWS.
    assert main.app is not None
    paths = {r.path for r in main.app.routes}
    assert "/api/chat" in paths


# ---------------------------------------------------------------------------
# 2. /api/commitments returns real service data (with filters)
# ---------------------------------------------------------------------------


def test_commitments_returns_real_data(tmp_db, client):
    services.create_commitment("Statistics quiz", "school", "2026-10-29")
    services.create_commitment("AI assignment", "school", "2026-10-30")

    body = client.get("/api/commitments").json()
    assert len(body["commitments"]) == 2
    assert body["commitments"][0]["due_date"] == "2026-10-29"

    filtered = client.get("/api/commitments", params={"title_query": "quiz"}).json()
    assert [c["title"] for c in filtered["commitments"]] == ["Statistics quiz"]


# ---------------------------------------------------------------------------
# 3. /api/plan returns real plan data
# ---------------------------------------------------------------------------


def test_plan_returns_real_data(tmp_db, client):
    services.create_commitment("Task A", "work", "2026-11-10")
    services.create_commitment("Task B", "work", "2026-11-08")
    body = client.get("/api/plan").json()
    dates = [p["planned_date"] for p in body["plan_items"]]
    assert dates == ["2026-11-08", "2026-11-10"]


# ---------------------------------------------------------------------------
# 4. /api/conflicts calls real conflict logic
# ---------------------------------------------------------------------------


def test_conflicts_real_behavior(tmp_db, client):
    services.create_commitment("Statistics quiz", "school", "2026-10-29")
    assignment = services.create_commitment("AI assignment", "school", "2026-10-30")
    cid = assignment["commitment"]["id"]

    body = client.get(
        "/api/conflicts", params={"commitment_id": cid, "new_due_date": "2026-10-28"}
    ).json()
    assert body["conflicts"]
    assert any(c["title"] == "Statistics quiz" for c in body["conflicts"])


def test_conflicts_unknown_commitment_404(tmp_db, client):
    r = client.get("/api/conflicts", params={"commitment_id": 99999, "new_due_date": "2026-10-28"})
    assert r.status_code == 404


def test_conflicts_requires_params(client):
    assert client.get("/api/conflicts").status_code == 422


# ---------------------------------------------------------------------------
# 5. propose -> GET /api/changes/{id}
# ---------------------------------------------------------------------------


def test_get_change_endpoint(tmp_db, client):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    change_id = proposal["change_id"]

    body = client.get(f"/api/changes/{change_id}").json()
    assert body["change_id"] == change_id
    assert body["status"] == "pending"
    assert body["old_due_date"] == "2026-10-30"
    assert body["new_due_date"] == "2026-10-28"


def test_get_change_unknown_404(tmp_db, client):
    assert client.get("/api/changes/nope").status_code == 404


# ---------------------------------------------------------------------------
# 6 + 7. Apply endpoint (real service, no agent/MCP)
# ---------------------------------------------------------------------------


def test_apply_endpoint_applies(tmp_db, client):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    cid = c["commitment"]["id"]
    change_id = services.propose_replan(cid, "2026-10-28")["change_id"]

    r = client.post(f"/api/changes/{change_id}/apply")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    # commitment actually moved
    stored = services.get_commitments()["commitments"][0]
    assert stored["due_date"] == "2026-10-28"


def test_apply_endpoint_bypasses_agent_and_mcp(tmp_db, client, monkeypatch):
    # If anything tried to reach the agent, fail loudly.
    def explode():
        raise AssertionError("Apply path must not touch the agent/LLM/MCP")

    monkeypatch.setattr(main, "get_agent", explode)

    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    change_id = services.propose_replan(c["commitment"]["id"], "2026-10-28")["change_id"]
    assert client.post(f"/api/changes/{change_id}/apply").status_code == 200


def test_apply_twice_is_rejected(tmp_db, client):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    change_id = services.propose_replan(c["commitment"]["id"], "2026-10-28")["change_id"]
    client.post(f"/api/changes/{change_id}/apply")
    r = client.post(f"/api/changes/{change_id}/apply")
    assert r.status_code == 409


def test_apply_unknown_404(tmp_db, client):
    assert client.post("/api/changes/does-not-exist/apply").status_code == 404


# ---------------------------------------------------------------------------
# 8. Cancel endpoint
# ---------------------------------------------------------------------------


def test_cancel_endpoint(tmp_db, client):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    change_id = services.propose_replan(c["commitment"]["id"], "2026-10-28")["change_id"]

    r = client.post(f"/api/changes/{change_id}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    # nothing applied
    assert services.get_commitments()["commitments"][0]["due_date"] == "2026-10-30"


def test_cancelled_change_cannot_apply(tmp_db, client):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    change_id = services.propose_replan(c["commitment"]["id"], "2026-10-28")["change_id"]
    client.post(f"/api/changes/{change_id}/cancel")
    assert client.post(f"/api/changes/{change_id}/apply").status_code == 409


# ---------------------------------------------------------------------------
# 10. Chat with fake agent returns the frontend contract
# ---------------------------------------------------------------------------


def test_chat_with_fake_agent(tmp_db, client, monkeypatch):
    fake = FakeAgent(
        result={
            "reply": "Proposed a new date.",
            "pending_change_id": "abc123",
            "tool_calls": [{"name": "propose_replan", "input": {"commitment_id": 1}, "status": "success"}],
        }
    )
    monkeypatch.setattr(main, "get_agent", lambda: fake)

    r = client.post("/api/chat", json={"session_id": "s1", "text": "move my assignment"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "Proposed a new date."
    assert body["pending_change_id"] == "abc123"
    assert body["tool_calls"][0]["name"] == "propose_replan"
    # user text was converted into Bedrock message format
    assert fake.received[0]["role"] == "user"
    assert fake.received[0]["content"][0]["text"] == "move my assignment"


# ---------------------------------------------------------------------------
# 11 + 12. Chat failure -> safe fallback, no secret leakage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "err",
    [
        AgentError("Tool-use loop exceeded 8 iterations"),
        LLMError("Bedrock call failed: ThrottlingException | Too many tokens per day"),
        LLMError("Bedrock call failed: AccessDenied AKIAIOSFODNN7EXAMPLE"),
        RuntimeError("unexpected boom"),
    ],
)
def test_chat_failure_safe_fallback(tmp_db, client, monkeypatch, err):
    monkeypatch.setattr(main, "get_agent", lambda: FakeAgent(error=err))
    r = client.post("/api/chat", json={"session_id": "s1", "text": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert "temporarily unavailable" in body["reply"]
    assert body["pending_change_id"] is None
    assert body["tool_calls"] == []
    # No provider internals / secrets leak into the response body.
    text = r.text
    assert "AKIA" not in text
    assert "Traceback" not in text
    assert "boom" not in text


def test_chat_construction_failure_is_safe(tmp_db, client, monkeypatch):
    # Even building the provider (missing creds) must degrade gracefully.
    def cannot_build():
        raise LLMError("AWS_REGION environment variable is not set.")

    monkeypatch.setattr(main, "get_agent", cannot_build)
    r = client.post("/api/chat", json={"session_id": "s1", "text": "hi"})
    assert r.status_code == 200
    assert "temporarily unavailable" in r.json()["reply"]


# ---------------------------------------------------------------------------
# 13. Chat "apply it" cannot directly apply a pending change
# ---------------------------------------------------------------------------


def test_chat_apply_it_does_not_apply(tmp_db, client, monkeypatch):
    c = services.create_commitment("AI assignment", "school", "2026-10-30")
    change_id = services.propose_replan(c["commitment"]["id"], "2026-10-28")["change_id"]

    # A chat turn whose (fake) agent replies as if it "applied" still cannot
    # change state: the agent path has no apply capability.
    fake = FakeAgent(result={"reply": "Use the Apply Changes button.", "pending_change_id": None, "tool_calls": []})
    monkeypatch.setattr(main, "get_agent", lambda: fake)

    r = client.post("/api/chat", json={"session_id": "s1", "text": "apply it"})
    assert r.status_code == 200

    # The pending change is untouched and the commitment is still Friday.
    assert services.get_change(change_id)["status"] == "pending"
    assert services.get_commitments()["commitments"][0]["due_date"] == "2026-10-30"


# ---------------------------------------------------------------------------
# 15. CORS still works
# ---------------------------------------------------------------------------


def test_cors_preflight_allows_frontend(client):
    origin = "http://localhost:5173"
    r = client.options(
        "/api/commitments",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code in (200, 204)
    # With allow_credentials=True the middleware echoes the specific Origin
    # rather than '*'; either way CORS is still functioning for the frontend.
    assert r.headers.get("access-control-allow-origin") in (origin, "*")
    assert r.headers.get("access-control-allow-credentials") == "true"


# ---------------------------------------------------------------------------
# 16. Per-session clarification context through the REAL endpoint
# ---------------------------------------------------------------------------


def test_chat_clarification_is_scoped_per_session(tmp_db, client, monkeypatch):
    from datetime import date

    import demo_provider
    from agent import ActionBridgeAgent
    from demo_provider import DemoProvider

    # Freeze the provider clock (Mon 2026-10-26 -> upcoming Friday is 10-30).
    monkeypatch.setattr(demo_provider, "_today", lambda: date(2026, 10, 26))
    monkeypatch.setattr(main, "get_agent", lambda: ActionBridgeAgent(DemoProvider()))

    # Session A states a date-less commitment; the assistant asks for a day.
    a1 = client.post("/api/chat", json={"session_id": "A", "text": "I have lunch"}).json()
    assert a1["tool_calls"] == []
    assert "when" in a1["reply"].lower()

    # Session B has no pending clarification, so a bare "Friday" invents nothing.
    b1 = client.post("/api/chat", json={"session_id": "B", "text": "Friday"}).json()
    assert "create_commitment" not in {tc["name"] for tc in b1["tool_calls"]}
    assert services.get_commitments()["commitments"] == []

    # Session A completing its own clarification creates the lunch commitment.
    a2 = client.post("/api/chat", json={"session_id": "A", "text": "Friday"}).json()
    assert "create_commitment" in {tc["name"] for tc in a2["tool_calls"]}
    stored = services.get_commitments()["commitments"]
    assert {c["title"] for c in stored} == {"lunch"}
    assert stored[0]["due_date"] == "2026-10-30"


def test_chat_explicit_date_clarification_and_isolation(tmp_db, client, monkeypatch):
    from datetime import date

    import demo_provider
    from agent import ActionBridgeAgent
    from demo_provider import DemoProvider

    # Freeze the provider clock (Mon 2026-10-26; 31 Oct is still upcoming).
    monkeypatch.setattr(demo_provider, "_today", lambda: date(2026, 10, 26))
    monkeypatch.setattr(main, "get_agent", lambda: ActionBridgeAgent(DemoProvider()))

    # Session A: date-less request -> assistant asks for a day.
    a1 = client.post("/api/chat",
                     json={"session_id": "A", "text": "I have project submission"}).json()
    assert a1["tool_calls"] == []
    assert "when" in a1["reply"].lower()

    # Session B has no pending request, so a bare explicit date invents nothing.
    b1 = client.post("/api/chat", json={"session_id": "B", "text": "31 oct 26"}).json()
    assert "create_commitment" not in {tc["name"] for tc in b1["tool_calls"]}
    assert services.get_commitments()["commitments"] == []

    # Session A supplies the explicit date and the commitment is completed.
    a2 = client.post("/api/chat", json={"session_id": "A", "text": "31 oct 26"}).json()
    assert "create_commitment" in {tc["name"] for tc in a2["tool_calls"]}
    stored = services.get_commitments()["commitments"]
    assert {c["title"] for c in stored} == {"project submission"}
    assert stored[0]["due_date"] == "2026-10-31"


def test_chat_impossible_date_rejected(tmp_db, client, monkeypatch):
    from datetime import date

    import demo_provider
    from agent import ActionBridgeAgent
    from demo_provider import DemoProvider

    monkeypatch.setattr(demo_provider, "_today", lambda: date(2026, 10, 26))
    monkeypatch.setattr(main, "get_agent", lambda: ActionBridgeAgent(DemoProvider()))

    res = client.post("/api/chat",
                      json={"session_id": "X", "text": "I have party on 31 February"}).json()
    assert "create_commitment" not in {tc["name"] for tc in res["tool_calls"]}
    assert "real date" in res["reply"].lower()
    assert services.get_commitments()["commitments"] == []
