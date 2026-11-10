"""Aggregate serialized sessions into week-level planner summaries.

This module groups session payloads by training week so the planner frontend
can render compact weekly totals, date spans, and phase labels.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any, cast

from endury.planning.wire.types import (
    SessionSummaryLike,
    WeeklySummaryPayload,
)


def build_weekly_summary(
    sessions: Sequence[SessionSummaryLike],
    phase_calendar: list[str],
    plan_start: date,
) -> list[WeeklySummaryPayload]:
    """Aggregate serialized sessions into per-week summaries."""
    weeks: dict[int, dict[str, Any]] = {}
    for session in sessions:
        # An optional session is outside the plan's load (R-SESS-037).
        if session.get("optional"):
            continue
        week_index = session["week_index"]
        if week_index not in weeks:
            weeks[week_index] = _init_week_bucket(
                week_index,
                phase_calendar,
                plan_start,
            )
        _aggregate_session_into_week(weeks[week_index], session, plan_start)
    return [_finalize_week_bucket(weeks[index]) for index in sorted(weeks)]


def _week0_monday(plan_start: date) -> date:
    """Return the Monday of the week containing ``plan_start``."""
    return plan_start - timedelta(days=plan_start.weekday())


def _init_week_bucket(
    week_index: int,
    phase_calendar: list[str],
    plan_start: date,
) -> dict[str, Any]:
    """Build an empty week bucket dict for a given week index."""
    week0_monday = _week0_monday(plan_start)
    return {
        "week_index": week_index,
        "phase": (
            phase_calendar[week_index]
            if week_index < len(phase_calendar)
            else "unknown"
        ),
        "total_tss": 0.0,
        "total_duration_minutes": 0.0,
        "total_distance_meters": 0.0,
        "session_count": 0,
        "sport_tss": {},
        "sport_duration": {},
        "is_recovery_week": False,
        "days": {
            day_offset: {
                "date": (
                    week0_monday + timedelta(days=(week_index * 7) + day_offset)
                ).isoformat(),
                "weekday": (
                    week0_monday + timedelta(days=(week_index * 7) + day_offset)
                ).strftime("%a"),
                "weekday_index": day_offset,
                "session_count": 0,
                "total_tss": 0.0,
                "total_duration_minutes": 0.0,
                "sessions": [],
            }
            for day_offset in range(7)
        },
    }


def _aggregate_session_into_week(
    week: dict[str, Any],
    session: SessionSummaryLike,
    plan_start: date,
) -> None:
    """Accumulate one session's metrics into its week bucket."""
    week_index = week["week_index"]
    achieved_tss = _coerce_metric_value(session.get("achieved_tss"))
    duration_minutes = _coerce_metric_value(session.get("duration_minutes"))
    distance_meters = _coerce_metric_value(session.get("distance_meters"))

    week["total_tss"] += achieved_tss
    week["total_duration_minutes"] += duration_minutes
    week["total_distance_meters"] += distance_meters
    week["session_count"] += 1
    week["is_recovery_week"] = week["is_recovery_week"] or bool(
        session.get("is_recovery_week")
    )
    sport = session["sport"]
    week["sport_tss"][sport] = week["sport_tss"].get(sport, 0.0) + achieved_tss
    week["sport_duration"][sport] = (
        week["sport_duration"].get(sport, 0.0) + duration_minutes
    )
    monday = _week0_monday(plan_start)
    day_index = (date.fromisoformat(session["date"]) - monday).days - (week_index * 7)
    day_bucket = week["days"].get(day_index)
    if day_bucket is None:
        return
    day_bucket["session_count"] += 1
    day_bucket["total_tss"] += achieved_tss
    day_bucket["total_duration_minutes"] += duration_minutes
    day_bucket["sessions"].append(session)


def _coerce_metric_value(value: object) -> float:
    """Convert optional numeric payload values to float, defaulting to 0.0."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _finalize_week_bucket(week: dict[str, Any]) -> WeeklySummaryPayload:
    """Round values and sort days for JSON output."""
    week["total_tss"] = round(week["total_tss"], 1)
    week["total_duration_minutes"] = round(week["total_duration_minutes"], 1)
    week["total_distance_meters"] = round(week["total_distance_meters"], 1)
    week["sport_tss"] = {
        sport: round(value, 1) for sport, value in week["sport_tss"].items()
    }
    week["sport_duration"] = {
        sport: round(value, 1) for sport, value in week["sport_duration"].items()
    }
    week["days"] = [
        {
            **day,
            "total_tss": round(day["total_tss"], 1),
            "total_duration_minutes": round(day["total_duration_minutes"], 1),
            "sessions": sorted(
                day["sessions"],
                key=lambda item: (item["sport"], item["activity_key"]),
            ),
        }
        for _, day in sorted(week["days"].items())
    ]
    return cast(WeeklySummaryPayload, week)
