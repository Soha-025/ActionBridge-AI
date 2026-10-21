"""ActionBridge MCP boundary (M3-owned).

This module is the only place where a model can reach ActionBridge's
capabilities. It is deliberately thin:

  * Every tool is a pass-through wrapper around a function in
    ``services`` (M2-owned business logic).
  * NO SQL, NO database access, NO scheduling logic, NO conflict logic,
    and NO business rules live here. If it isn't a one-line delegation,
    it belongs in services.py.

CRITICAL SAFETY BOUNDARY (structural, not prompt-based):
  Exactly five tools are registered with FastMCP. ``apply_changes``,
  ``cancel_change``, and ``get_change`` are NOT registered here and MUST
  NEVER be added as MCP tools, even if the user says "yes", "apply it",
  "do it", or "confirm". The model may only call ``propose_replan``,
  which creates a pending change and returns a change_id. Applying or
  cancelling a change happens exclusively through the frontend/API
  confirmation flow (main.py), which calls the services layer directly.

Run as an MCP stdio server (from backend/):

    python mcp_server.py

M2 MISSING-CODE NOTE:
  ``services.py`` is currently an empty file (M2 has not pushed its
  implementation yet). The import below is guarded so this module stays
  importable, and each tool fails loudly at call time with a
  developer-facing message naming the missing function. There is no
  fallback business logic: the MCP layer refuses to invent M2 behavior.
"""

from __future__ import annotations

import asyncio
import sys

from mcp.server.fastmcp import FastMCP

try:
    import services  # M2-owned business logic: the single source of truth
except ImportError:  # pragma: no cover - only hit if services.py is absent
    services = None
    print(
        "WARNING: backend/services.py could not be imported; every MCP tool "
        "call will fail until the M2 implementation is available.",
        file=sys.stderr,
    )

mcp = FastMCP("ActionBridge")

MODEL_TOOLS = (
    "create_commitment",
    "get_commitments",
    "create_plan",
    "detect_conflicts",
    "propose_replan",
)

# Tools that must never be model-facing. Enforced by this constant, by the
# fact that we simply do not define wrappers for them, and by
# test_mcp_tools.py::test_never_exposed_as_model_tools.
NON_MODEL_TOOLS = (
    "apply_changes",
    "cancel_change",
    "get_change",
)


def _service_fn(name: str):
    """Return the M2 service function `name`, or fail loudly.

    No stubs, no fallbacks: if M2's implementation is not in the repo yet,
    the developer gets an explicit error naming the missing function.
    """
    fn = getattr(services, name, None) if services is not None else None
    if not callable(fn):
        raise RuntimeError(
            f"M2 service function '{name}' is not available. "
            "backend/services.py is missing its implementation (not pushed "
            "yet). Ask the M2 owner to push services.py; this MCP wrapper "
            "will not fake business logic in its place."
        )
    return fn


async def _call(name: str, /, **kwargs):
    """Delegate to a sync M2 function without blocking the event loop."""
    return await asyncio.to_thread(_service_fn(name), **kwargs)


# ---------------------------------------------------------------------------
# The five model-facing tools. Each delegates directly to services.py.
# ---------------------------------------------------------------------------


@mcp.tool()
async def create_commitment(
    title: str,
    type: str,
    due_date: str,
    priority: str | None = None,
):
    """Record a new commitment the user has made.

    Args:
        title: Short description of the commitment, e.g. "Send Q3 report".
        type: Kind of commitment, e.g. "work", "personal", or "health".
        due_date: Deadline as an ISO date string, e.g. "2026-10-30".
        priority: Optional urgency, e.g. "high", "medium", or "low".

    Returns the created commitment, including its id.
    """
    return await _call(
        "create_commitment", title=title, type=type, due_date=due_date, priority=priority
    )


@mcp.tool()
async def get_commitments(status: str | None = None, title_query: str | None = None):
    """List existing commitments, optionally filtered.

    Args:
        status: Optional filter by commitment status, e.g. "active" or "done".
        title_query: Optional case-insensitive substring to match against
            commitment titles.

    Returns the matching commitments with their ids, titles, types, due
    dates, and statuses.
    """
    return await _call("get_commitments", status=status, title_query=title_query)


@mcp.tool()
async def create_plan():
    """Build the current action plan from the user's active commitments.

    Takes no arguments. Returns the planned steps the system has derived,
    including timing suggestions for each commitment.
    """
    return await _call("create_plan")


@mcp.tool()
async def detect_conflicts(commitment_id: int, new_due_date: str):
    """Check whether moving a commitment's due date would create conflicts.

    Call this BEFORE proposing a replan.

    Args:
        commitment_id: Id of the commitment to check (from get_commitments).
        new_due_date: Proposed new due date as an ISO date string,
            e.g. "2026-11-03".

    Returns any detected conflicts, such as overlapping deadlines or
    capacity problems with other commitments.
    """
    return await _call(
        "detect_conflicts", commitment_id=commitment_id, new_due_date=new_due_date
    )


@mcp.tool()
async def propose_replan(commitment_id: int, new_due_date: str):
    """Propose rescheduling a commitment to a new due date.

    This only creates a PENDING change and returns its change_id plus a
    human-readable summary. The change is NOT applied by this tool, and no
    tool exists to apply it: a real person must confirm the pending change
    in the UI before anything takes effect. Never claim a change was
    applied; tell the user to review and confirm it.

    Args:
        commitment_id: Id of the commitment to reschedule.
        new_due_date: Proposed new due date as an ISO date string,
            e.g. "2026-11-03".

    Returns the pending change's change_id and a summary of the diff.
    """
    return await _call("propose_replan", commitment_id=commitment_id, new_due_date=new_due_date)


if __name__ == "__main__":
    mcp.run()
