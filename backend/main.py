"""ActionBridge FastAPI backend (M2 route skeleton, now wired to real logic).

Route paths and the frontend-friendly response shapes are preserved exactly.
Only the previously hardcoded bodies are replaced with real integrations:

  * /api/chat                 -> lazily-built ActionBridge agent (M3)
  * /api/commitments          -> services.get_commitments
  * /api/plan                 -> services.create_plan
  * /api/conflicts            -> services.detect_conflicts
  * /api/changes/{id}         -> services.get_change
  * /api/changes/{id}/apply   -> services.apply_changes   (HUMAN confirmation)
  * /api/changes/{id}/cancel  -> services.cancel_change   (HUMAN confirmation)

SAFETY BOUNDARY
---------------
``apply_changes`` / ``cancel_change`` / ``get_change`` are reachable ONLY
through these plain HTTP endpoints, which call the service layer directly.
They are NOT wired through the agent, the LLM, or MCP. The model path is
strictly /api/chat -> agent -> MCP -> the five safe tools. There is no
code path from the LLM to apply_changes.

This module imports cleanly with NO AWS credentials, NO Bedrock access, and
NO database file: the production agent/provider is built lazily on the first
real /api/chat request, and SQLite tables are created on demand by services.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import services
from agent import ActionBridgeAgent, AgentError
from llm_provider import BedrockProvider, LLMError

# Load backend/.env if present, without overriding real environment variables
# and without ever printing values (no secrets are exposed at startup).
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

# Initialize FastAPI App
app = FastAPI(title="ActionBridge AI API", version="1.0")

# Enable CORS for Frontend (Vite/React on localhost)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("actionbridge-backend")

# Pydantic Schemas
class ChatRequest(BaseModel):
    session_id: str
    text: str


# ---------------------------------------------------------------------------
# Lazy agent/provider construction.
#
# Building the agent touches AWS configuration, so it MUST NOT happen at
# import time (that would break unit tests and any environment without Bedrock
# credentials). It is created on first use and cached. Tests monkeypatch
# `get_agent` to inject a fake agent.
# ---------------------------------------------------------------------------

_AGENT: ActionBridgeAgent | None = None


def get_agent() -> ActionBridgeAgent:
    global _AGENT
    if _AGENT is None:
        _AGENT = ActionBridgeAgent(BedrockProvider())
    return _AGENT


# ---------------------------------------------------------------------------
# Error mapping helpers (never leak internals to the client)
# ---------------------------------------------------------------------------

_CHAT_FALLBACK_REPLY = (
    "⚠️ The AI assistant is temporarily unavailable (the model provider may be "
    "rate-limited or misconfigured). Your deterministic backend is still fully "
    "functional: your commitments, plan, and any pending changes are unchanged. "
    "Please try your request again shortly."
)


def _not_found_or_http(value_error: ValueError, *, not_pending_status: int | None = None) -> HTTPException:
    """Map a service ValueError to a clean HTTP error.

    Unknown entity  -> 404. A change that exists but is in the wrong state
    (e.g. already applied/cancelled) -> 409 when not_pending_status allows it.
    """
    msg = str(value_error)
    if "Unknown" in msg or "not found" in msg.lower():
        return HTTPException(status_code=404, detail=msg)
    if not_pending_status is not None and "not pending" in msg.lower():
        return HTTPException(status_code=not_pending_status, detail=msg)
    # Invalid input (e.g. bad date, unknown commitment reference)
    return HTTPException(status_code=400, detail=msg)


# 1. Chat Endpoint
@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    """Run one conversational turn through the agent.

    The request contract only carries a single user message (plus a session id,
    which is not persisted in this step), so it is converted into the Bedrock
    message format the agent expects.
    """
    messages = [
        {"role": "user", "content": [{"text": payload.text}]},
    ]
    try:
        agent = get_agent()
        result = await agent.chat(messages)
        # Normalize to the guaranteed frontend shape.
        return {
            "reply": result.get("reply", ""),
            "pending_change_id": result.get("pending_change_id"),
            "tool_calls": result.get("tool_calls", []),
        }
    except (AgentError, LLMError) as err:
        # Provider/agent failure: safe fallback, no AWS internals, no stack.
        logger.error("Chat unavailable (provider/agent error); serving fallback")
        return {
            "reply": _CHAT_FALLBACK_REPLY,
            "pending_change_id": None,
            "tool_calls": [],
        }
    except Exception:
        # Unexpected failure: keep the API alive, log details server-side only.
        logger.exception("Unexpected error in /api/chat")
        return {
            "reply": _CHAT_FALLBACK_REPLY,
            "pending_change_id": None,
            "tool_calls": [],
        }


# 2. Get Commitments
@app.get("/api/commitments")
async def get_commitments(
    status: str | None = Query(default=None),
    title_query: str | None = Query(default=None),
):
    try:
        return services.get_commitments(status=status, title_query=title_query)
    except ValueError as err:
        raise _not_found_or_http(err) from None
    except Exception:
        logger.exception("Unexpected error in /api/commitments")
        raise HTTPException(status_code=500, detail="Internal server error") from None


# 3. Get Plan
@app.get("/api/plan")
async def get_plan():
    try:
        return services.create_plan()
    except ValueError as err:
        raise _not_found_or_http(err) from None
    except Exception:
        logger.exception("Unexpected error in /api/plan")
        raise HTTPException(status_code=500, detail="Internal server error") from None


# 4. Get Conflicts
@app.get("/api/conflicts")
async def get_conflicts(
    commitment_id: int = Query(...),
    new_due_date: str = Query(...),
):
    try:
        return services.detect_conflicts(commitment_id=commitment_id, new_due_date=new_due_date)
    except ValueError as err:
        raise _not_found_or_http(err) from None
    except Exception:
        logger.exception("Unexpected error in /api/conflicts")
        raise HTTPException(status_code=500, detail="Internal server error") from None


# 5. Get Change Details (Diff)
@app.get("/api/changes/{change_id}")
async def get_change_details(change_id: str):
    try:
        return services.get_change(change_id)
    except ValueError as err:
        raise _not_found_or_http(err) from None
    except Exception:
        logger.exception("Unexpected error in /api/changes/{change_id}")
        raise HTTPException(status_code=500, detail="Internal server error") from None


# 6. Apply Change  (HUMAN-CONFIRMATION boundary: services only, never agent/LLM/MCP)
@app.post("/api/changes/{change_id}/apply")
async def apply_change(change_id: str):
    try:
        return services.apply_changes(change_id)
    except ValueError as err:
        raise _not_found_or_http(err, not_pending_status=409) from None
    except Exception:
        logger.exception("Unexpected error applying change")
        raise HTTPException(status_code=500, detail="Failed to apply changes") from None


# 7. Cancel Change  (HUMAN-CONFIRMATION boundary: services only, never agent/LLM/MCP)
@app.post("/api/changes/{change_id}/cancel")
async def cancel_change(change_id: str):
    try:
        return services.cancel_change(change_id)
    except ValueError as err:
        raise _not_found_or_http(err) from None
    except Exception:
        logger.exception("Unexpected error cancelling change")
        raise HTTPException(status_code=500, detail="Failed to cancel change") from None
