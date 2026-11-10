"""Lean preview projection for the coach's ``create_plan`` tool.

``GenerateSuccess`` carries the full session list the planner wizard
renders; the coach never gets to see (or relay) that much. This module
projects it into the trimmed shape ``create_plan`` hands back before a
commit: phase runs with dates, per-week hours/TSS with the per-sport split,
and only the opening ten days of sessions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any

from endury.planning.coach_brief import _goal_label
from endury.planning.wire.requests import GenerateSuccess
from endury.planning.wire.types import (
    SerializedSessionPayload,
    WeeklySummaryPayload,
)

#: The preview's "first days" window — enough for the athlete to recognize
#: the opening week without seeing the whole plan.
FIRST_DAYS_WINDOW_DAYS = 10

_PREVIEW_MESSAGE = (
    "Nothing has been created yet. The athlete applies this exact plan from "
    "its card; relay what it shows."
)


def plan_weeks(result: GenerateSuccess) -> int:
    """The plan's own week count — the macro row's ``total_weeks``.

    Args:
        result: The generated plan.

    Returns:
        How many weeks the phase plan spans.
    """
    return len(result.phase_calendar)


def _phase_runs(phase_calendar: list[str], plan_start: date) -> list[dict[str, Any]]:
    """Collapse a week-by-week phase calendar into contiguous labeled runs."""
    runs: list[dict[str, Any]] = []
    for index, phase in enumerate(phase_calendar):
        if runs and runs[-1]["phase"] == phase:
            runs[-1]["last"] = index
        else:
            runs.append({"phase": phase, "first": index, "last": index})
    return [
        {
            "phase": run["phase"],
            "from": (plan_start + timedelta(weeks=run["first"])).isoformat(),
            "through": (plan_start + timedelta(weeks=run["last"], days=6)).isoformat(),
            "weeks": run["last"] - run["first"] + 1,
        }
        for run in runs
    ]


def _weekly_rows(
    weekly_summary: list[WeeklySummaryPayload], plan_start: date
) -> list[dict[str, Any]]:
    """One row per week: hours, TSS, and the per-sport hours split."""
    return [
        {
            "week": week["week_index"] + 1,
            "from": (plan_start + timedelta(weeks=week["week_index"])).isoformat(),
            "hours": round(week["total_duration_minutes"] / 60, 1),
            "tss": week["total_tss"],
            "by_sport": {
                sport: round(minutes / 60, 1)
                for sport, minutes in week["sport_duration"].items()
            },
        }
        for week in weekly_summary
    ]


def _first_days(
    sessions: list[SerializedSessionPayload], plan_start: date
) -> list[dict[str, Any]]:
    """The plan's opening ``FIRST_DAYS_WINDOW_DAYS`` days of sessions."""
    cutoff = plan_start + timedelta(days=FIRST_DAYS_WINDOW_DAYS - 1)
    in_window = [
        session
        for session in sessions
        if plan_start <= date.fromisoformat(session["date"]) <= cutoff
    ]
    return [
        {
            "date": session["date"],
            "weekday": date.fromisoformat(session["date"]).strftime("%a"),
            "sport": session["sport"],
            "workout_name": session["workout"]["name"],
            "duration_minutes": session["duration_minutes"],
            "target_tss": session["target_tss"],
        }
        for session in sorted(in_window, key=lambda s: s["date"])
    ]


def _input_hash(athlete_state: dict[str, Any] | None) -> str:
    """Fingerprint the athlete-state snapshot a plan was generated from.

    A confirm regenerates with the identical request arguments, so two
    calls land on different hashes only when the athlete's live
    intervals.icu training history changed underneath them — exactly the
    signal ``create_plan``'s ``expected_input_hash`` freshness check
    compares against.

    Args:
        athlete_state: The generated plan's resolved athlete-state
            snapshot, or ``None``.

    Returns:
        A short, stable hex fingerprint.
    """
    canonical = json.dumps(athlete_state or {}, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_plan_preview(
    result: GenerateSuccess,
    *,
    plan_name: str,
    goal_type: str,
    races: list[dict[str, Any]],
    plan_start: date,
    replaces: dict[str, Any] | None,
    directives: dict[str, Any] | None = None,
    extra_caveats: list[str] | None = None,
) -> dict[str, Any]:
    """Project a ``GenerateSuccess`` into ``create_plan``'s lean preview shape.

    Args:
        result: The freshly-generated (not yet committed) plan.
        plan_name: The name the plan would be committed under.
        goal_type: The A-goal's ``GoalType`` value.
        races: The request's race objects, as given.
        plan_start: The plan's training start date.
        replaces: The active plan this would replace, or ``None``.
        directives: The directives given (``resume_date``,
            ``suppress_tests_until``, ``unavailable``, ``weekly_hours_cap``),
            or ``None``. Echoed back so the card states what is in force
            even when nothing in it changed a session.
        extra_caveats: Caveats beyond the plan's own ``warning``, e.g. a
            trail-band mismatch built anyway via ``accept_band_mismatch``.
            An Experimental plan's ``maturity_note`` is added after them.

    Returns:
        The ``status: "preview"`` wire shape ``create_plan`` returns.
    """
    sources = ((result.athlete_state or {}).get("sources")) or {}
    phase_calendar = list(result.phase_calendar)
    caveats = ([result.warning] if result.warning else []) + list(extra_caveats or [])
    if result.maturity_note:
        caveats.append(result.maturity_note)
    return {
        "status": "preview",
        "confirm_required": True,
        "input_hash": _input_hash(result.athlete_state),
        "plan_name": plan_name,
        "goal": _goal_label(goal_type),
        "maturity": result.maturity,
        "races": races,
        "plan_start": plan_start.isoformat(),
        # The plan's own week count, not weeks that happen to carry a
        # session — a trimmed opening week must not shorten it.
        "weeks": plan_weeks(result),
        "phases": _phase_runs(phase_calendar, plan_start),
        "weekly": _weekly_rows(list(result.weekly_summary), plan_start),
        "first_days": _first_days(list(result.sessions), plan_start),
        "warning": result.warning,
        "athlete_state_sources": sources,
        "replaces": replaces,
        "directives": directives or {},
        "caveats": caveats,
        "message": _PREVIEW_MESSAGE,
    }


__all__ = ["FIRST_DAYS_WINDOW_DAYS", "build_plan_preview", "plan_weeks"]
