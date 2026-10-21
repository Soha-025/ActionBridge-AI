"""Seed the core ActionBridge hackathon demo (M3-owned helper).

This is a small, idempotent, executable script that populates the demo by
reusing the service layer exclusively. It contains NO SQL and NO duplicated
planning logic: every write goes through ``services.create_commitment`` and
``services.create_plan``. It never touches AWS/Bedrock.

Demo data (relative to the deterministic "today"):

  * Statistics quiz  -> upcoming Thursday   (type: quiz)
  * AI assignment    -> upcoming Friday     (type: assignment)
  * Presentation     -> upcoming Saturday   (type: presentation)

"Today" comes from ``services._today()``: the ``DEMO_TODAY`` environment
variable when set, otherwise the real local date -- the exact same
deterministic fallback services already uses. For DEMO_TODAY=2026-10-26
(a Monday) the due dates resolve to Thursday 10-29, Friday 10-30,
Saturday 10-31, i.e. the required Thursday -> Friday -> Saturday ordering.

Idempotency: services.create_commitment() already refuses an obvious
duplicate (same normalized title + due date), so re-running this script just
reports each item as "already existed" instead of creating a second copy.

Run (from backend/), for the real demo DB:

    python seed.py

Override the database without editing code:

    DB_PATH=/tmp/demo.db python seed.py
"""

from __future__ import annotations

from datetime import date, timedelta

import services

# Weekday targets using Python's convention: Monday=0 ... Sunday=6.
THURSDAY, FRIDAY, SATURDAY = 3, 4, 5

# (title, type, weekday, priority) -- sensible demo priorities.
DEMO_COMMITMENTS = (
    ("Statistics quiz", "quiz", THURSDAY, "high"),
    ("AI assignment", "assignment", FRIDAY, "high"),
    ("Presentation", "presentation", SATURDAY, "medium"),
)


def next_weekday(from_day: date, target_weekday: int) -> date:
    """Return the upcoming ``target_weekday`` on or after ``from_day``.

    Deterministic and simple: from a Monday this yields the same week's
    Thursday/Friday/Saturday, preserving the required ordering.
    """
    delta = (target_weekday - from_day.weekday()) % 7
    return from_day + timedelta(days=delta)


def seed() -> dict:
    """Create the demo commitments + plan via the service layer.

    Returns a structured summary for callers/tests (and printed by main):
      {"today": iso, "commitments": [{title, due_date, priority,
        already_exists}], "plan_item_count": int}
    """
    today = services._today()

    created = []
    for title, type_, weekday, priority in DEMO_COMMITMENTS:
        due_date = next_weekday(today, weekday).isoformat()
        result = services.create_commitment(title, type_, due_date, priority)
        created.append(
            {
                "title": title,
                "due_date": due_date,
                "priority": priority,
                "already_exists": result["already_exists"],
            }
        )

    plan_items = services.create_plan()["plan_items"]

    return {
        "today": today.isoformat(),
        "commitments": created,
        "plan_item_count": len(plan_items),
    }


def _print_summary(summary: dict) -> None:
    print("ActionBridge demo seed")
    print(f"  Reference 'today': {summary['today']}")
    print("  Commitments:")
    for item in summary["commitments"]:
        status = "already existed" if item["already_exists"] else "created"
        print(
            f"    - {item['title']:<16} due {item['due_date']} "
            f"({item['priority']} priority) [{status}]"
        )
    print(f"  Plan items: {summary['plan_item_count']}")


if __name__ == "__main__":
    _print_summary(seed())
