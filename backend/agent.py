"""ActionBridge agent loop (M3-owned).

This module turns a provider-neutral LLM plus the M3 MCP tool boundary into
a full conversation loop that can call tools, feed results back, and stop
when the model is done. It knows nothing about AWS, Bedrock credentials,
SQL, dates, or M2's business rules:

  * The LLM is injected as an ``LLMProvider`` (no global Bedrock client).
  * Tools are executed ONLY through the MCP boundary in ``mcp_server``, so
    no scheduling/conflict/database logic is duplicated here.
  * The set of callable tools is a fixed allowlist of the five safe
    model-facing tools. ``apply_changes`` / ``cancel_change`` /
    ``get_change`` are structurally unreachable from the agent, even if a
    (malicious or buggy) provider asks for them by name.

The loop is fully testable with a fake provider and fake M2 services: no
network, Bedrock, or database is required.
"""

from __future__ import annotations

import json
import os
from typing import Any

from llm_provider import LLMError, LLMProvider

import mcp_server


class AgentError(Exception):
    """Raised for agent-loop failures that the caller must handle.

    The message is safe to show to a developer: it never contains raw AWS
    credentials, session tokens, or provider exception internals.
    """


# ---------------------------------------------------------------------------
# Provider-facing tool schemas (Bedrock Converse format).
#
# Defined explicitly here to avoid coupling the agent to FastMCP's private
# tool-manager internals. These five descriptions MUST match the contracts
# in mcp_server.py. There is deliberately NO schema for apply_changes,
# cancel_change, or get_change.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "toolSpec": {
            "name": "create_commitment",
            "description": (
                "Record a new commitment the user has made, with a title, "
                "type, due date, and optional priority."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Short description of the commitment."},
                        "type": {"type": "string", "description": "Kind of commitment, e.g. work, personal, health."},
                        "due_date": {"type": "string", "description": "Deadline as an ISO date, e.g. 2026-10-30."},
                        "priority": {"type": "string", "description": "Optional urgency, e.g. high, medium, low."},
                    },
                    "required": ["title", "type", "due_date"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_commitments",
            "description": (
                "List existing commitments, optionally filtered by status "
                "and a title substring."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "description": "Optional status filter, e.g. active or done."},
                        "title_query": {"type": "string", "description": "Optional case-insensitive title substring."},
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "create_plan",
            "description": "Build the current action plan from the user's active commitments.",
            "inputSchema": {"json": {"type": "object", "properties": {}, "required": []}},
        }
    },
    {
        "toolSpec": {
            "name": "detect_conflicts",
            "description": (
                "Check whether moving a commitment to a new due date would "
                "create conflicts. Call this before propose_replan."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "commitment_id": {"type": "integer", "description": "Id of the commitment to check."},
                        "new_due_date": {"type": "string", "description": "Proposed due date as an ISO date."},
                    },
                    "required": ["commitment_id", "new_due_date"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "propose_replan",
            "description": (
                "Propose rescheduling a commitment to a new due date. This "
                "creates a PENDING change and returns a change_id; it does "
                "NOT apply anything. A human confirms via the UI."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "commitment_id": {"type": "integer", "description": "Id of the commitment to reschedule."},
                        "new_due_date": {"type": "string", "description": "Proposed new due date as an ISO date."},
                    },
                    "required": ["commitment_id", "new_due_date"],
                }
            },
        }
    },
]

# The agent will only ever dispatch to these names. This allowlist, plus the
# fact that mcp_server only registers these same five tools, is what makes
# the "apply_changes is unreachable from the model" guarantee structural.
ALLOWED_TOOL_NAMES = frozenset(schema["toolSpec"]["name"] for schema in TOOL_SCHEMAS)

MAX_TOOL_ITERATIONS = 8


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BODY = """\
You are ActionBridge, a personal planning and action agent. You help the
user manage commitments, deadlines, plans, and schedule changes.

Guidelines:
- Use your tools rather than inventing stored commitments, plans, or ids.
- If essential information is missing, ask a short clarifying question.
- Be concise and action-oriented.
- When a deadline changes, retrieve relevant commitments if needed, call
  detect_conflicts, and then propose_replan.

Confirmation safety rules (non-negotiable):
- A proposed replan is PENDING only. propose_replan does not apply anything.
- NEVER claim a pending change has been applied, cancelled, or confirmed.
- You have no tool to apply, cancel, or read a change. Only the external
  frontend/API confirmation flow may apply a pending change.
- If the user says "yes", "apply it", "confirm", or similar, explain that
  they must use the Apply Changes action in the UI to make it real.
- NEVER invent a change_id. Only report a change_id returned by a tool.
"""


def build_system_prompt() -> str:
    """Return the ActionBridge system prompt.

    When ``DEMO_TODAY`` is set in the environment, it is included so that
    relative-date reasoning ("next Friday") is deterministic in demos.
    """
    prompt = _SYSTEM_PROMPT_BODY
    demo_today = os.environ.get("DEMO_TODAY")
    if demo_today:
        prompt += f"\nCurrent date (authoritative for relative dates): {demo_today}\n"
    return prompt


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------


def _extract_text(content: list) -> str:
    parts = [b["text"] for b in content if isinstance(b, dict) and "text" in b]
    return "".join(p or "" for p in parts)


def _tool_use_blocks(content: list) -> list[dict]:
    return [b["toolUse"] for b in content if isinstance(b, dict) and "toolUse" in b]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ActionBridgeAgent:
    """Provider-neutral agent that runs the tool-use conversation loop.

    The LLM provider is injected so this module never instantiates Bedrock
    (or any provider) itself and so tests can pass a deterministic fake.
    """

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    # ------------------------------- loop -------------------------------- #
    async def chat(self, messages: list) -> dict:
        """Run the conversation to completion.

        Args:
            messages: Bedrock-format history, a list of
                ``{"role": "user"|"assistant", "content": [...]}`` dicts.
                The caller owns persistence; this method does not touch a DB.

        Returns:
            ``{"reply": str, "pending_change_id": <id|None>,
               "tool_calls": [{"name", "input", "status"}]}``

        Raises:
            AgentError: on a provider/LLM failure, a malformed provider
                response, or an exceeded iteration limit. Credential- and
                secret-bearing provider internals are never leaked; a
                concise, sanitized reason is used instead.

        ``chat`` is async because tools are executed through the async MCP
        boundary and because the future FastAPI /api/chat handler is async.
        The injected provider's ``converse`` is a normal sync call.
        """
        conversation = list(messages)
        tool_calls: list[dict] = []
        pending_change_id: Any = None

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                resp = self.provider.converse(
                    messages=conversation,
                    tools=TOOL_SCHEMAS,
                    system=build_system_prompt(),
                )
            except LLMError as err:
                # LLMError is already secret-safe by design; surface it as a
                # controlled AgentError without adding provider internals.
                raise AgentError(f"Provider failed during chat: {err}") from None

            if not isinstance(resp, dict) or "message" not in resp:
                raise AgentError("Malformed provider response: missing 'message'.")

            message = resp["message"] or {}
            content = message.get("content") or []
            if not isinstance(content, list):
                raise AgentError("Malformed provider response: 'content' is not a list.")

            tool_uses = _tool_use_blocks(content)

            # No tool use -> this is the model's final answer.
            if not tool_uses:
                return {
                    "reply": _extract_text(content),
                    "pending_change_id": pending_change_id,
                    "tool_calls": tool_calls,
                }

            # Preserve the assistant turn (with its toolUse blocks) verbatim.
            conversation.append({"role": "assistant", "content": content})

            # Execute each requested tool through the MCP boundary and
            # collect Bedrock-compatible toolResult blocks for the next turn.
            result_blocks: list[dict] = []
            for use in tool_uses:
                block, change_id = await self._dispatch(use, tool_calls)
                result_blocks.append(block)
                if change_id is not None:
                    pending_change_id = change_id

            conversation.append({"role": "user", "content": result_blocks})

        raise AgentError(
            f"Tool-use loop exceeded {MAX_TOOL_ITERATIONS} iterations without "
            "finishing; aborting to avoid an infinite loop."
        )

    # ------------------------------ dispatch ----------------------------- #
    async def _dispatch(self, use: dict, tool_calls: list) -> tuple[dict, Any]:
        """Run one requested tool via the MCP boundary.

        Returns the toolResult content block and, for ``propose_replan``,
        any ``change_id`` taken from the tool's real output (never invented).
        Unknown or forbidden tools are rejected before any service call.
        """
        name = use.get("name", "")
        tool_use_id = use.get("toolUseId", "")
        raw_input = use.get("input") or {}
        arguments = raw_input if isinstance(raw_input, dict) else {}

        # Structural safety gate: only the five allowlisted tools ever run.
        if name not in ALLOWED_TOOL_NAMES:
            reason = (
                "Tool is not available to the model. Applying or cancelling "
                "a change must be done by a human via the Apply Changes UI."
                if name in mcp_server.NON_MODEL_TOOLS
                else "Unknown tool."
            )
            tool_calls.append({"name": name, "input": arguments, "status": "rejected"})
            return (
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": f"Refused to call '{name}': {reason}"}],
                        "status": "error",
                    }
                },
                None,
            )

        tool_call = {"name": name, "input": arguments, "status": "success"}
        tool_calls.append(tool_call)

        try:
            # Delegate through the MCP boundary: this is the exact same
            # registered tool a real MCP client would hit, so no business
            # logic is duplicated here and forbidden tools are unreachable.
            blocks = await mcp_server.mcp.call_tool(name, arguments)
            text = _blocks_to_text(blocks)
            change_id = self._maybe_change_id(name, text)
        except Exception as err:  # missing M2 service, bad args, etc.
            # Keep it developer-useful but secret-free: use only the exception
            # summary, never a full provider/AWS traceback.
            tool_call["status"] = "error"
            safe = _sanitize(err)
            return (
                {
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": f"Tool '{name}' failed: {safe}"}],
                        "status": "error",
                    }
                },
                None,
            )

        return (
            {
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": text}],
                    "status": "success",
                }
            },
            change_id,
        )

    @staticmethod
    def _maybe_change_id(name: str, result_text: str) -> Any:
        """Extract a real change_id from a propose_replan result, if present."""
        if name != "propose_replan":
            return None
        try:
            data = json.loads(result_text)
        except (ValueError, TypeError):
            return None
        if isinstance(data, dict):
            return data.get("change_id")
        return None


def _blocks_to_text(blocks) -> str:
    """Flatten FastMCP call_tool output (TextContent objects and/or dicts)."""
    parts = []
    for b in blocks or []:
        if isinstance(b, dict):
            if "text" in b:
                parts.append(b["text"] or "")
        else:
            text = getattr(b, "text", None)
            if text is not None:
                parts.append(text)
    return "".join(parts)


def _sanitize(err: Exception) -> str:
    """Reduce an exception to a short, secret-free developer message."""
    from llm_provider import _redact

    msg = str(err) or type(err).__name__
    return _redact(msg)
