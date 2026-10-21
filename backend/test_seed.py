"""Tests for the demo seed script (backend/seed.py).

Runs against a temporary SQLite DB via DB_PATH. NO AWS/Bedrock and NO real
demo database are touched. Verifies deterministic relative dates, idempotency,
plan generation, and that seed delegates entirely to the service layer
instead of issuing SQL itself.

Run from backend/:

    python -m pytest test_seed.py -v
"""

from __future__ import annotations

import inspect

import pytest

import seed
import services

# DEMO_TODAY = Monday 2026-10-26 -> upcoming Thu/Fri/Sat = 29/30/31.
MONDAY = "2026-10-26"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "seed_test.db"))
    monkeypatch.setenv("DEMO_TODAY", MONDAY)
    services.init_db()
    return str(tmp_path / "seed_test.db")


# ---------------------------------------------------------------------------
# Creation + deterministic dates
# ---------------------------------------------------------------------------


def test_three_commitments_created(tmp_db):
    summary = seed.seed()
    assert len(summary["commitments"]) == 3
    assert all(not c["already_exists"] for c in summary["commitments"])

    stored = services.get_commitments()["commitments"]
    titles = {c["title"] for c in stored}
    assert titles == {"Statistics quiz", "AI assignment", "Presentation"}


def test_dates_resolve_to_thu_fri_sat(tmp_db):
    summary = seed.seed()
    by_title = {c["title"]: c["due_date"] for c in summary["commitments"]}
    assert by_title["Statistics quiz"] == "2026-10-29"  # Thursday
    assert by_title["AI assignment"] == "2026-10-30"     # Friday
    assert by_title["Presentation"] == "2026-10-31"      # Saturday

    # Thursday -> Friday -> Saturday ordering preserved.
    dates = [c["due_date"] for c in summary["commitments"]]
    assert dates == sorted(dates)


def test_next_weekday_math_is_relative():
    from datetime import date

    monday = date(2026, 10, 26)
    assert seed.next_weekday(monday, seed.THURSDAY).isoformat() == "2026-10-29"
    assert seed.next_weekday(monday, seed.FRIDAY).isoformat() == "2026-10-30"
    assert seed.next_weekday(monday, seed.SATURDAY).isoformat() == "2026-10-31"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_run_creates_no_duplicates(tmp_db):
    first = seed.seed()
    assert all(not c["already_exists"] for c in first["commitments"])

    second = seed.seed()
    assert all(c["already_exists"] for c in second["commitments"])

    # Still exactly three commitments, no accumulation.
    assert len(services.get_commitments()["commitments"]) == 3


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def test_plan_has_three_items(tmp_db):
    summary = seed.seed()
    assert summary["plan_item_count"] == 3
    plan = services.create_plan()["plan_items"]
    assert {p["title"] for p in plan} == {"Statistics quiz", "AI assignment", "Presentation"}


# ---------------------------------------------------------------------------
# Structure: seed must reuse services, not write SQL / duplicate planning
# ---------------------------------------------------------------------------


def test_seed_uses_services_not_direct_sql(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "probe.db"))
    monkeypatch.setenv("DEMO_TODAY", MONDAY)

    calls = {"create_commitment": 0, "create_plan": 0}

    def fake_create_commitment(title, type_, due_date, priority=None):
        calls["create_commitment"] += 1
        return {"commitment": {"id": calls["create_commitment"], "title": title}, "already_exists": False}

    def fake_create_plan():
        calls["create_plan"] += 1
        return {"plan_items": [{"title": "x"}, {"title": "y"}, {"title": "z"}]}

    monkeypatch.setattr(services, "create_commitment", fake_create_commitment)
    monkeypatch.setattr(services, "create_plan", fake_create_plan)

    summary = seed.seed()

    assert calls["create_commitment"] == 3  # each commitment via the service
    assert calls["create_plan"] == 1        # planning delegated to the service
    assert summary["plan_item_count"] == 3


def test_seed_module_has_no_sql_or_db_or_aws_dependencies():
    src = inspect.getsource(seed)
    # No direct SQL / database access of its own.
    assert "sqlite3" not in src
    assert "CREATE TABLE" not in src.upper()
    assert "INSERT" not in src.upper()
    # No AWS / LLM / MCP coupling: seed only talks to the service layer.
    assert "boto3" not in src
    assert "llm_provider" not in src
    assert "mcp_server" not in src
    assert "import agent" not in src


# ---------------------------------------------------------------------------
# No AWS/Bedrock dependency: importing + running uses stdlib + services only
# ---------------------------------------------------------------------------


def test_seed_requires_no_aws(tmp_path, monkeypatch):
    for key in ("AWS_REGION", "BEDROCK_MODEL_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "noaws.db"))
    monkeypatch.setenv("DEMO_TODAY", MONDAY)
    services.init_db()

    summary = seed.seed()
    assert summary["plan_item_count"] == 3
