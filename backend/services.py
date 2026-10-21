"""ActionBridge backend service layer (business logic single source of truth).

This module owns all persistence and domain logic for commitments, plans, and
pending schedule changes. It is intentionally simple, deterministic, and
demo-ready: SQLite plus the Python standard library only, no ORM, no optimizer.

Design notes
------------
* The database path is read from the ``DB_PATH`` environment variable at call
  time (default ``actionbridge.db``), so tests can point it at a temp file and
  never touch the real demo database.
* Importing this module has NO side effects: no DB file is created and no
  AWS/Bedrock code runs. Tables are created lazily and idempotently
  (``CREATE TABLE IF NOT EXISTS``) whenever a connection is opened.
* ``propose_replan`` NEVER mutates a commitment's real due date; it only
  records a PENDING change. Applying that change is a separate, server-side
  operation (``apply_changes``) that the MCP/model boundary never exposes.

The five model-facing functions used by the MCP layer are:
    create_commitment, get_commitments, create_plan, detect_conflicts,
    propose_replan
The three server-side-only functions are:
    get_change, apply_changes, cancel_change
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Connection / schema management
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS commitments (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT    NOT NULL,
        type       TEXT    NOT NULL,
        due_date   TEXT    NOT NULL,
        priority   TEXT,
        status     TEXT    NOT NULL DEFAULT 'active',
        created_at TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS plan_items (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        commitment_id INTEGER NOT NULL,
        title         TEXT    NOT NULL,
        planned_date  TEXT    NOT NULL,
        status        TEXT    NOT NULL DEFAULT 'planned',
        created_at    TEXT    NOT NULL,
        FOREIGN KEY (commitment_id) REFERENCES commitments (id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_changes (
        change_id     TEXT PRIMARY KEY,
        commitment_id INTEGER NOT NULL,
        old_due_date  TEXT    NOT NULL,
        new_due_date  TEXT    NOT NULL,
        summary       TEXT    NOT NULL,
        diff          TEXT    NOT NULL,
        status        TEXT    NOT NULL DEFAULT 'pending',
        created_at    TEXT    NOT NULL,
        FOREIGN KEY (commitment_id) REFERENCES commitments (id)
    )
    """,
)


def _resolve_db_path() -> str:
    return os.environ.get("DB_PATH") or "actionbridge.db"


@contextmanager
def db():
    """Open a SQLite connection as a transactional context manager.

    - uses sqlite3.Row so rows convert cleanly to dicts
    - guarantees the schema exists (idempotent CREATE TABLE IF NOT EXISTS)
    - commits on success, rolls back on any exception, always closes
    """
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Explicitly ensure tables exist. Safe to call repeatedly."""
    with db():
        pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _validate_date(value: Any, field: str = "due_date") -> date:
    """Validate a strict ISO YYYY-MM-DD date, returning a ``date``.

    Raises a clear ValueError on anything else so callers never silently
    store corrupt dates.
    """
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError(f"{field} must be an ISO date in YYYY-MM-DD form, got {value!r}")
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} is not a real calendar date: {value!r}") from None


def _today() -> date:
    """Deterministic 'today': DEMO_TODAY if set, otherwise the real date."""
    demo = os.environ.get("DEMO_TODAY")
    if demo:
        return _validate_date(demo, "DEMO_TODAY")
    return date.today()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def _normalize_title(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def _require_commitment(conn: sqlite3.Connection, commitment_id: int) -> dict:
    cur = conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"Unknown commitment id: {commitment_id!r}")
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Model-facing services (the five MCP tools delegate here)
# ---------------------------------------------------------------------------


def create_commitment(title: str, type: str, due_date: str, priority: str | None = None) -> dict:
    """Create a commitment, refusing an obvious duplicate.

    A duplicate is an existing commitment with the same normalized title and
    the same due date. Duplicate calls return the existing row with
    ``already_exists=True`` and do NOT write a second row.

    Returns: {"commitment": {...}, "already_exists": bool}
    """
    if not title or not title.strip():
        raise ValueError("title must be a non-empty string")
    if not type or not type.strip():
        raise ValueError("type must be a non-empty string")

    _validate_date(due_date, "due_date")
    norm = _normalize_title(title)

    with db() as conn:
        existing = conn.execute(
            "SELECT * FROM commitments WHERE lower(trim(title)) = ? AND due_date = ?",
            (norm, due_date),
        ).fetchone()
        if existing is not None:
            return {"commitment": _row_to_dict(existing), "already_exists": True}

        cur = conn.execute(
            "INSERT INTO commitments (title, type, due_date, priority, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (title.strip(), type.strip(), due_date, priority, _now_iso()),
        )
        created = conn.execute("SELECT * FROM commitments WHERE id = ?", (cur.lastrowid,)).fetchone()
        return {"commitment": _row_to_dict(created), "already_exists": False}


def get_commitments(status: str | None = None, title_query: str | None = None) -> dict:
    """List commitments with optional status and title-substring filters.

    Returns: {"commitments": [...]} ordered by due date then id.
    """
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if title_query:
        clauses.append("lower(title) LIKE ?")
        params.append(f"%{_normalize_title(title_query)}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM commitments {where} ORDER BY due_date ASC, id ASC", params
        ).fetchall()
        return {"commitments": [_row_to_dict(r) for r in rows]}


def _rebuild_plan(conn: sqlite3.Connection) -> list[dict]:
    """Regenerate plan_items deterministically from active commitments.

    Simple demo planner: one plan step per active commitment, scheduled on
    its own due date, ordered by due date then id. Completed/cancelled
    commitments are not scheduled. Callers share one connection/transaction.
    """
    conn.execute("DELETE FROM plan_items")
    active = conn.execute(
        "SELECT * FROM commitments WHERE status = 'active' ORDER BY due_date ASC, id ASC"
    ).fetchall()
    now = _now_iso()
    for c in active:
        conn.execute(
            "INSERT INTO plan_items (commitment_id, title, planned_date, status, created_at) "
            "VALUES (?, ?, ?, 'planned', ?)",
            (c["id"], c["title"], c["due_date"], now),
        )
    rows = conn.execute(
        "SELECT * FROM plan_items ORDER BY planned_date ASC, id ASC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_plan() -> dict:
    """Build the current action plan from active commitments.

    Returns: {"plan_items": [...]} (regenerated deterministically each call).
    """
    with db() as conn:
        return {"plan_items": _rebuild_plan(conn)}


def detect_conflicts(commitment_id: int, new_due_date: str) -> dict:
    """Detect schedule pressure from moving a commitment to ``new_due_date``.

    General, date-based logic (no hardcoded titles):
      * collision      -> another active commitment already due that day.
      * tight_week     -> another active deadline immediately after the new
        date, so the moved work leaves no room and has to start earlier.

    Returns: {"conflicts": [...]} (possibly empty).
    """
    target = _validate_date(new_due_date, "new_due_date")

    with db() as conn:
        commitment = _require_commitment(conn, commitment_id)
        others = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM commitments WHERE status = 'active' AND id != ? "
                "ORDER BY due_date ASC, id ASC",
                (commitment_id,),
            ).fetchall()
        ]

    conflicts: list[dict] = []
    for other in others:
        other_date = _validate_date(other["due_date"], "due_date")
        if other_date == target:
            conflicts.append(
                {
                    "type": "collision",
                    "commitment_id": other["id"],
                    "title": other["title"],
                    "due_date": other["due_date"],
                    "reason": (
                        f"'{other['title']}' is already due on {new_due_date}; moving "
                        f"'{commitment['title']}' there puts two deadlines on the same day."
                    ),
                }
            )
        elif timedelta(0) < other_date - target <= timedelta(days=1):
            conflicts.append(
                {
                    "type": "tight_week",
                    "commitment_id": other["id"],
                    "title": other["title"],
                    "due_date": other["due_date"],
                    "reason": (
                        f"'{other['title']}' is due {other['due_date']}, right after the new "
                        f"date {new_due_date}; work for '{commitment['title']}' must move "
                        "earlier to avoid back-to-back deadlines."
                    ),
                }
            )

    return {"conflicts": conflicts}


def _compute_proposal(commitment: dict, new_due_date: str, conflicts: list[dict]) -> dict:
    """Build the deterministic pending diff for a replan.

    * The primary commitment moves old -> new due date.
    * Any same-day collision is rebalanced to the next free calendar day.
    * tight_week pressure is explained in the summary but needs no automatic
      move (the user is pulling work earlier on purpose).
    """
    target = _validate_date(new_due_date, "new_due_date")
    moved = []
    taken = {
        c["due_date"]
        for c in _all_active_excluding(commitment["id"])
    }
    taken.discard(commitment["due_date"])  # old date frees up

    for conflict in conflicts:
        if conflict["type"] != "collision":
            continue
        cid = conflict["commitment_id"]
        # find earliest free day at or after the target date
        candidate = target
        while candidate.isoformat() in taken:
            candidate += timedelta(days=1)
        taken.add(candidate.isoformat())
        moved.append(
            {
                "commitment_id": cid,
                "title": conflict["title"],
                "from": conflict["due_date"],
                "to": candidate.isoformat(),
                "reason": "rebalanced off the moved commitment's new date",
            }
        )

    tight = [c for c in conflicts if c["type"] == "tight_week"]

    if conflicts:
        names = ", ".join(c["title"] for c in conflicts)
        summary = (
            f"Move '{commitment['title']}' from {commitment['due_date']} to "
            f"{new_due_date}. This creates schedule pressure with {names}"
        )
        if moved:
            summary += "; rebalanced: " + ", ".join(
                f"'{m['title']}' -> {m['to']}" for m in moved
            )
        if tight:
            summary += ". Work must start earlier to absorb the change."
        summary += ". Pending confirmation; nothing has been applied yet."
    else:
        summary = (
            f"Move '{commitment['title']}' from {commitment['due_date']} to "
            f"{new_due_date}. No conflicts detected. Pending confirmation; "
            "nothing has been applied yet."
        )

    return {
        "commitment_id": commitment["id"],
        "old_due_date": commitment["due_date"],
        "new_due_date": new_due_date,
        "moved": moved,
        "added": [],
        "removed": [],
        "summary": summary,
    }


def _all_active_excluding(exclude_id: int) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM commitments WHERE status = 'active' AND id != ?",
            (exclude_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def propose_replan(commitment_id: int, new_due_date: str) -> dict:
    """Create a PENDING change for a new due date. NEVER applies it.

    Persists a pending_changes row capturing the deterministic diff and
    returns its id. The commitment's real due date is left untouched until
    ``apply_changes(change_id)`` is explicitly called by the confirmation
    flow.

    Returns: {"change_id": "...", "summary": "...", "diff": {...}}
    """
    _validate_date(new_due_date, "new_due_date")

    with db() as conn:
        commitment = _require_commitment(conn, commitment_id)
        conflicts = detect_conflicts(commitment_id, new_due_date)["conflicts"]
        diff = _compute_proposal(commitment, new_due_date, conflicts)

        change_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO pending_changes "
            "(change_id, commitment_id, old_due_date, new_due_date, summary, diff, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (
                change_id,
                commitment_id,
                commitment["due_date"],
                new_due_date,
                diff["summary"],
                json.dumps(diff),
                _now_iso(),
            ),
        )
        return {"change_id": change_id, "summary": diff["summary"], "diff": diff}


# ---------------------------------------------------------------------------
# Server-side-only change lifecycle (NOT exposed to the model)
# ---------------------------------------------------------------------------


def _load_change(conn: sqlite3.Connection, change_id: str) -> dict:
    row = conn.execute("SELECT * FROM pending_changes WHERE change_id = ?", (change_id,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown change id: {change_id!r}")
    change = _row_to_dict(row)
    change["diff"] = json.loads(change["diff"])
    return change


def get_change(change_id: str) -> dict:
    """Return a frontend-friendly view of a pending change.

    Raises ValueError for an unknown id.
    """
    with db() as conn:
        change = _load_change(conn, change_id)

    diff = change["diff"]
    return {
        "change_id": change["change_id"],
        "status": change["status"],
        "summary": change["summary"],
        "commitment_id": change["commitment_id"],
        "old_due_date": change["old_due_date"],
        "new_due_date": change["new_due_date"],
        "moved": diff.get("moved", []),
        "added": diff.get("added", []),
        "removed": diff.get("removed", []),
    }


def apply_changes(change_id: str) -> dict:
    """SERVER-SIDE ONLY: apply a pending change atomically.

    Validates the change exists and is still 'pending', applies the stored
    due-date moves and rebuilds the plan in a single transaction, then marks
    the change applied. A second application is safely rejected (idempotent
    guard) rather than silently re-applying.
    """
    with db() as conn:
        change = _load_change(conn, change_id)
        if change["status"] != "pending":
            raise ValueError(
                f"Change {change_id} is not pending (status={change['status']!r}); "
                "it cannot be applied again."
            )

        diff = change["diff"]
        # Move the primary commitment.
        _require_commitment(conn, diff["commitment_id"])
        conn.execute(
            "UPDATE commitments SET due_date = ? WHERE id = ?",
            (diff["new_due_date"], diff["commitment_id"]),
        )
        # Apply any rebalancing moves.
        for move in diff.get("moved", []):
            conn.execute(
                "UPDATE commitments SET due_date = ? WHERE id = ?",
                (move["to"], move["commitment_id"]),
            )

        plan_items = _rebuild_plan(conn)
        conn.execute(
            "UPDATE pending_changes SET status = 'applied' WHERE change_id = ?",
            (change_id,),
        )

        applied_commitment = _row_to_dict(
            conn.execute("SELECT * FROM commitments WHERE id = ?", (diff["commitment_id"],)).fetchone()
        )

    return {
        "change_id": change_id,
        "status": "applied",
        "summary": diff["summary"],
        "commitment": applied_commitment,
        "plan_items": plan_items,
    }


def cancel_change(change_id: str) -> dict:
    """SERVER-SIDE ONLY: cancel a pending change without applying it.

    A change that is already applied or cancelled is left as-is and reported.
    """
    with db() as conn:
        change = _load_change(conn, change_id)
        if change["status"] == "pending":
            conn.execute(
                "UPDATE pending_changes SET status = 'cancelled' WHERE change_id = ?",
                (change_id,),
            )
            new_status = "cancelled"
        else:
            new_status = change["status"]
    return {"change_id": change_id, "status": new_status}
