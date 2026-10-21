"""Tests for the ActionBridge service layer (backend/services.py).

Every test runs against a throwaway SQLite database selected via the
``DB_PATH`` environment variable, so the real demo database is never
touched. No AWS, no Bedrock, no network, no mocks of the service layer
itself: the critical demo workflow drives the real functions end to end.

Run from backend/:

    python -m pytest test_services.py -v
"""

from __future__ import annotations

import sqlite3

import pytest

import services


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point services at an isolated empty database for each test."""
    db_file = tmp_path / "svc_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.delenv("DEMO_TODAY", raising=False)
    services.init_db()
    return str(db_file)


@pytest.fixture
def demo_week(tmp_db, monkeypatch):
    """Deterministic week: DEMO_TODAY = Monday 2026-10-26.

    Tue 27, Wed 28, Thu 29 (stats quiz), Fri 30 (AI assignment),
    Sat 31 (presentation).
    """
    monkeypatch.setenv("DEMO_TODAY", "2026-10-26")
    return {
        "mon": "2026-10-26",
        "wed": "2026-10-28",
        "thu": "2026-10-29",
        "fri": "2026-10-30",
        "sat": "2026-10-31",
    }


def _make(title, type_, due, **kw):
    return services.create_commitment(title, type_, due, **kw)


# ---------------------------------------------------------------------------
# DB / schema initialization
# ---------------------------------------------------------------------------


def test_db_path_defaults_to_actionbridge(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    assert services._resolve_db_path() == "actionbridge.db"


def test_tables_created(tmp_db):
    conn = sqlite3.connect(tmp_db)
    names = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    conn.close()
    assert {"commitments", "plan_items", "pending_changes"} <= names


def test_init_is_idempotent(tmp_db):
    services.init_db()  # second call must not error
    _make("A", "work", "2026-11-01")
    services.init_db()  # still fine after writes


# ---------------------------------------------------------------------------
# create_commitment
# ---------------------------------------------------------------------------


def test_create_commitment_returns_shape(tmp_db):
    result = _make("Send report", "work", "2026-11-05", priority="high")
    assert set(result) == {"commitment", "already_exists"}
    c = result["commitment"]
    assert c["id"]
    assert c["title"] == "Send report"
    assert c["type"] == "work"
    assert c["due_date"] == "2026-11-05"
    assert c["priority"] == "high"
    assert c["status"] == "active"
    assert result["already_exists"] is False


def test_duplicate_commitment_detected(tmp_db):
    first = _make("Gym session", "health", "2026-11-02")
    dup = _make("  GYM   SESSION ", "health", "2026-11-02")  # same normalized title + date
    assert first["already_exists"] is False
    assert dup["already_exists"] is True
    assert dup["commitment"]["id"] == first["commitment"]["id"]

    stored = services.get_commitments()["commitments"]
    assert len(stored) == 1


def test_same_title_different_date_is_not_duplicate(tmp_db):
    _make("Study", "personal", "2026-11-01")
    second = _make("Study", "personal", "2026-11-03")
    assert second["already_exists"] is False
    assert len(services.get_commitments()["commitments"]) == 2


# ---------------------------------------------------------------------------
# get_commitments + filtering
# ---------------------------------------------------------------------------


def test_get_commitments_filters(tmp_db):
    _make("Statistics quiz", "school", "2026-10-29")
    _make("AI assignment", "school", "2026-10-30")
    _make("Dentist", "health", "2026-10-28")

    all_items = services.get_commitments()["commitments"]
    assert len(all_items) == 3
    # ordered by due date ascending
    assert [c["due_date"] for c in all_items] == ["2026-10-28", "2026-10-29", "2026-10-30"]

    by_query = services.get_commitments(title_query="quiz")["commitments"]
    assert [c["title"] for c in by_query] == ["Statistics quiz"]

    by_status = services.get_commitments(status="done")["commitments"]
    assert by_status == []


# ---------------------------------------------------------------------------
# create_plan (deterministic)
# ---------------------------------------------------------------------------


def test_create_plan_one_step_per_active_commitment(tmp_db):
    _make("A", "work", "2026-11-10")
    _make("B", "work", "2026-11-08")
    plan = services.create_plan()["plan_items"]
    assert [p["planned_date"] for p in plan] == ["2026-11-08", "2026-11-10"]
    assert all(p["status"] == "planned" for p in plan)


def test_create_plan_is_idempotent(tmp_db):
    _make("A", "work", "2026-11-10")
    first = services.create_plan()["plan_items"]
    second = services.create_plan()["plan_items"]
    assert [p["title"] for p in first] == [p["title"] for p in second]
    # no duplicate plan rows accumulate
    assert len(second) == 1


def test_create_plan_skips_completed(tmp_db):
    _make("Active", "work", "2026-11-10")
    done = _make("Finished", "work", "2026-11-09")
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE commitments SET status='completed' WHERE id=?", (done["commitment"]["id"],))
    conn.commit()
    conn.close()

    plan = services.create_plan()["plan_items"]
    assert [p["title"] for p in plan] == ["Active"]


def test_create_plan_uses_demo_today(tmp_db, monkeypatch):
    monkeypatch.setenv("DEMO_TODAY", "2026-10-26")
    assert services._today().isoformat() == "2026-10-26"
    monkeypatch.delenv("DEMO_TODAY", raising=False)
    # falls back to real local date without erroring
    assert services._today() is not None


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


def test_detect_conflicts_tight_week(tmp_db):
    _make("Statistics quiz", "school", "2026-10-29")
    assignment = _make("AI assignment", "school", "2026-10-30")
    conflicts = services.detect_conflicts(assignment["commitment"]["id"], "2026-10-28")["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "tight_week"
    assert conflicts[0]["title"] == "Statistics quiz"


def test_detect_conflicts_same_day_collision(tmp_db):
    _make("Presentation", "school", "2026-10-31")
    moving = _make("Essay", "school", "2026-10-28")
    conflicts = services.detect_conflicts(moving["commitment"]["id"], "2026-10-31")["conflicts"]
    assert conflicts[0]["type"] == "collision"


def test_detect_conflicts_unknown_commitment(tmp_db):
    with pytest.raises(ValueError, match="Unknown commitment"):
        services.detect_conflicts(99999, "2026-10-30")


def test_detect_conflicts_invalid_date(tmp_db):
    c = _make("A", "work", "2026-10-30")
    with pytest.raises(ValueError):
        services.detect_conflicts(c["commitment"]["id"], "30-10-2026")


# ---------------------------------------------------------------------------
# propose_replan (must NOT mutate state)
# ---------------------------------------------------------------------------


def test_propose_replan_creates_pending_without_applying(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    cid = c["commitment"]["id"]

    proposal = services.propose_replan(cid, "2026-10-28")
    assert set(proposal) == {"change_id", "summary", "diff"}
    assert proposal["change_id"]
    assert "nothing has been applied" in proposal["summary"].lower()

    # commitment is untouched
    still = services.get_commitments(title_query="assignment")["commitments"][0]
    assert still["due_date"] == "2026-10-30"


def test_propose_replan_includes_pressure_in_diff(tmp_db):
    _make("Statistics quiz", "school", "2026-10-29")
    assignment = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(assignment["commitment"]["id"], "2026-10-28")
    diff = proposal["diff"]
    assert diff["old_due_date"] == "2026-10-30"
    assert diff["new_due_date"] == "2026-10-28"
    assert diff["moved"] == []
    assert "Statistics quiz" in proposal["summary"]


def test_propose_replan_collision_rebalances(tmp_db):
    _make("Presentation", "school", "2026-10-31")
    essay = _make("Essay", "school", "2026-10-28")
    proposal = services.propose_replan(essay["commitment"]["id"], "2026-10-31")
    moved = proposal["diff"]["moved"]
    assert len(moved) == 1
    assert moved[0]["to"] == "2026-11-01"  # pushed to next free day


def test_propose_replan_unknown_commitment(tmp_db):
    with pytest.raises(ValueError, match="Unknown commitment"):
        services.propose_replan(4242, "2026-10-28")


# ---------------------------------------------------------------------------
# get_change
# ---------------------------------------------------------------------------


def test_get_change_shape(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    change = services.get_change(proposal["change_id"])
    assert change["change_id"] == proposal["change_id"]
    assert change["status"] == "pending"
    assert change["commitment_id"] == c["commitment"]["id"]
    assert change["old_due_date"] == "2026-10-30"
    assert change["new_due_date"] == "2026-10-28"
    for key in ("moved", "added", "removed", "summary"):
        assert key in change


def test_get_change_unknown(tmp_db):
    with pytest.raises(ValueError, match="Unknown change"):
        services.get_change("does-not-exist")


# ---------------------------------------------------------------------------
# apply_changes
# ---------------------------------------------------------------------------


def test_apply_changes_updates_and_marks_applied(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    cid = c["commitment"]["id"]
    proposal = services.propose_replan(cid, "2026-10-28")

    applied = services.apply_changes(proposal["change_id"])
    assert applied["status"] == "applied"
    assert applied["commitment"]["due_date"] == "2026-10-28"

    stored = services.get_commitments()["commitments"][0]
    assert stored["due_date"] == "2026-10-28"
    assert services.get_change(proposal["change_id"])["status"] == "applied"


def test_apply_changes_updates_plan(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    applied = services.apply_changes(proposal["change_id"])
    dates = {p["planned_date"] for p in applied["plan_items"]}
    assert "2026-10-28" in dates


def test_apply_changes_twice_rejected(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    services.apply_changes(proposal["change_id"])
    with pytest.raises(ValueError, match="not pending"):
        services.apply_changes(proposal["change_id"])


def test_apply_changes_rebuilds_plan_with_moves(tmp_db):
    pres = _make("Presentation", "school", "2026-10-31")
    essay = _make("Essay", "school", "2026-10-28")
    proposal = services.propose_replan(essay["commitment"]["id"], "2026-10-31")
    services.apply_changes(proposal["change_id"])

    dates = {c["id"]: c["due_date"] for c in services.get_commitments()["commitments"]}
    assert dates[essay["commitment"]["id"]] == "2026-10-31"
    assert dates[pres["commitment"]["id"]] == "2026-11-01"  # rebalanced move applied


def test_apply_changes_unknown(tmp_db):
    with pytest.raises(ValueError, match="Unknown change"):
        services.apply_changes("nope")


# ---------------------------------------------------------------------------
# cancel_change
# ---------------------------------------------------------------------------


def test_cancel_change(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    result = services.cancel_change(proposal["change_id"])
    assert result["status"] == "cancelled"
    # due date never changed
    assert services.get_commitments()["commitments"][0]["due_date"] == "2026-10-30"


def test_cancelled_change_cannot_be_applied(tmp_db):
    c = _make("AI assignment", "school", "2026-10-30")
    proposal = services.propose_replan(c["commitment"]["id"], "2026-10-28")
    services.cancel_change(proposal["change_id"])
    with pytest.raises(ValueError, match="not pending"):
        services.apply_changes(proposal["change_id"])


def test_cancel_unknown(tmp_db):
    with pytest.raises(ValueError, match="Unknown change"):
        services.cancel_change("nope")


# ---------------------------------------------------------------------------
# Date validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["2026-13-01", "2026/10/30", "30-10-2026", "today", "", "2026-02-30", None, 12345])
def test_invalid_due_date_rejected(tmp_db, bad):
    with pytest.raises(ValueError):
        services.create_commitment("X", "work", bad)


def test_blank_title_rejected(tmp_db):
    with pytest.raises(ValueError):
        services.create_commitment("   ", "work", "2026-10-30")


# ---------------------------------------------------------------------------
# Critical demo verification: real service functions, no mocks
# ---------------------------------------------------------------------------


def test_demo_workflow_end_to_end(demo_week):
    w = demo_week

    # 2. create the three demo commitments
    quiz = _make("Statistics quiz", "school", w["thu"])
    assignment = _make("AI assignment", "school", w["fri"])
    _make("Presentation", "school", w["sat"])

    # 3. initial plan has all three active commitments
    plan = services.create_plan()["plan_items"]
    assert len(plan) == 3

    # 4. proposal: move AI assignment from Friday to Wednesday
    assignment_id = assignment["commitment"]["id"]

    # 5. schedule pressure detected (quiz is the very next day after Wed)
    conflicts = services.detect_conflicts(assignment_id, w["wed"])["conflicts"]
    assert conflicts, "expected schedule pressure when moving to Wednesday"
    assert any(c["title"] == "Statistics quiz" for c in conflicts)

    # 6. propose the replan
    proposal = services.propose_replan(assignment_id, w["wed"])
    change_id = proposal["change_id"]

    # 7. the original Friday deadline is STILL stored (pending only)
    stored = services.get_commitments(title_query="AI assignment")["commitments"][0]
    assert stored["due_date"] == w["fri"]

    # 8. apply the returned change_id
    applied = services.apply_changes(change_id)

    # 9. the deadline is now Wednesday
    final = services.get_commitments(title_query="AI assignment")["commitments"][0]
    assert final["due_date"] == w["wed"]
    assert applied["commitment"]["due_date"] == w["wed"]

    # 10. change status is applied
    assert services.get_change(change_id)["status"] == "applied"
