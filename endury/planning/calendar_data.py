"""The calendar payload: planned sessions, completed activities, phases, races.

Assembles what the calendar view renders for one date window — planned
sessions from the local plan, overlaid with completion read live from
intervals.icu (:mod:`endury.planning.calendar_projection`), the phase
ranges and race annotations derived from the active plan, and the
workouts that live only on intervals.icu (the coach's ad-hoc workouts and
the finished sessions of a plan the app has since replaced or deleted) —
plus the removal of one such ad-hoc workout. The ``calendar_data`` and
``delete_ad_hoc_workout`` RPC methods are thin adapters over
:func:`build_calendar_data` and :func:`delete_ad_hoc_workout`.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import date, timedelta
from typing import Any, cast

from endury.common import clock
from endury.intervals.push import AD_HOC_EXTERNAL_ID_PREFIX
from endury.planner.foundations.types import GoalType, is_maintenance_goal
from endury.planning.calendar_projection import fetch_intervals_calendar
from endury.planning.load_planner_metrics import get_planner_load_context
from endury.planning.pair_activity import MANUAL_OVERRIDE_STATUSES
from endury.planning.wire.sessions import (
    serialize_plan_session_row,
)

logger = logging.getLogger(__name__)


def _is_valid_active_plan(plan: Any) -> bool:
    """Check whether an object looks like a persisted plan row."""
    return (
        isinstance(getattr(plan, "id", None), int)
        and isinstance(getattr(plan, "plan_start", None), str)
        and isinstance(getattr(plan, "goal_date", None), str)
    )


def _phase_name(phase_type: str) -> str:
    """Return a user-facing phase name for a phase key."""
    return phase_type.replace("_", " ").title()


def _build_phase_ranges_from_calendar_weeks(
    phase_calendar: list[str],
    plan_start: date,
    goal_date: date,
) -> list[dict[str, Any]]:
    """Build contiguous phase blocks from a per-week phase calendar."""
    if not phase_calendar or goal_date < plan_start:
        return []

    today_str = clock.today().isoformat()
    phase_ranges: list[dict[str, Any]] = []
    current_start = 0
    current_phase = phase_calendar[0]

    def append_phase(start_week: int, end_week: int, phase_type: str) -> None:
        monday = plan_start - timedelta(days=plan_start.weekday())
        start_date = monday + timedelta(days=start_week * 7)
        end_date = min(
            goal_date,
            monday + timedelta(days=((end_week + 1) * 7) - 1),
        )
        start_date_str = start_date.isoformat()
        end_date_str = end_date.isoformat()
        phase_ranges.append(
            {
                "name": _phase_name(phase_type),
                "phase_type": phase_type,
                "start_date": start_date_str,
                "end_date": end_date_str,
                "duration_weeks": (end_week - start_week) + 1,
                "is_current": start_date_str <= today_str <= end_date_str,
            }
        )

    for week_index, phase_type in enumerate(phase_calendar[1:], start=1):
        if phase_type == current_phase:
            continue
        append_phase(current_start, week_index - 1, current_phase)
        current_start = week_index
        current_phase = phase_type
    append_phase(current_start, len(phase_calendar) - 1, current_phase)
    return phase_ranges


def _build_phase_ranges_from_plan(plan: Any) -> list[dict[str, Any]]:
    """Build phase blocks from the committed plan's stored phase calendar."""
    raw_solve_result = getattr(plan, "solve_result_json", None)
    if not isinstance(raw_solve_result, str) or not raw_solve_result.strip():
        return []

    try:
        solve_result = json.loads(raw_solve_result)
    except json.JSONDecodeError:
        return []
    if not isinstance(solve_result, dict):
        return []

    raw_phase_calendar = solve_result.get("phase_calendar")
    if not isinstance(raw_phase_calendar, list):
        return []

    phase_calendar = [
        phase.strip()
        for phase in raw_phase_calendar
        if isinstance(phase, str) and phase.strip()
    ]
    if not phase_calendar:
        return []

    try:
        plan_start = date.fromisoformat(str(plan.plan_start))
        goal_date = date.fromisoformat(str(plan.goal_date))
    except ValueError:
        return []

    return _build_phase_ranges_from_calendar_weeks(
        phase_calendar,
        plan_start,
        goal_date,
    )


def _build_phase_ranges(sessions: list[Any]) -> list[dict[str, Any]]:
    """Build contiguous phase blocks from plan sessions."""
    if not sessions:
        return []

    week_meta: dict[int, dict[str, Any]] = {}
    for session in sessions:
        week = int(session.week_index)
        meta = week_meta.setdefault(
            week,
            {
                "phase_type": session.phase_type,
                "start_date": session.date,
                "end_date": session.date,
            },
        )
        meta["start_date"] = min(meta["start_date"], session.date)
        meta["end_date"] = max(meta["end_date"], session.date)

    phase_ranges: list[dict[str, Any]] = []
    sorted_weeks = sorted(week_meta)
    current_start = sorted_weeks[0]
    current_end = sorted_weeks[0]
    current_phase = str(week_meta[current_start]["phase_type"])

    def append_phase(start_week: int, end_week: int, phase_type: str) -> None:
        start_date = week_meta[start_week]["start_date"]
        end_date = week_meta[end_week]["end_date"]
        today_str = clock.today().isoformat()
        phase_ranges.append(
            {
                "name": _phase_name(phase_type),
                "phase_type": phase_type,
                "start_date": start_date,
                "end_date": end_date,
                "duration_weeks": (end_week - start_week) + 1,
                "is_current": start_date <= today_str <= end_date,
            }
        )

    for week in sorted_weeks[1:]:
        phase_type = str(week_meta[week]["phase_type"])
        if phase_type == current_phase and week == current_end + 1:
            current_end = week
            continue
        append_phase(current_start, current_end, current_phase)
        current_start = week
        current_end = week
        current_phase = phase_type
    append_phase(current_start, current_end, current_phase)
    return phase_ranges


def _race_facts(race: Any | None) -> dict[str, Any]:
    """A race-table row's goal type, distance, climb and notes (None if unknown)."""
    return {
        "goal_type": getattr(race, "goal_type", None),
        "distance_km": getattr(race, "distance_km", None),
        "climb_m": getattr(race, "climb_m", None),
        "notes": getattr(race, "notes", None),
    }


def _build_races(plan: Any) -> list[dict[str, Any]]:
    """Build race list from active plan goal metadata.

    Maintenance plans have no race — their ``goal_date`` only marks the
    end of the block — so they contribute no race card. Distance, climb and
    notes start empty; :func:`_fill_from_race_table` supplies them.
    """
    goal_value = str(getattr(plan, "goal_type", "Goal"))
    with contextlib.suppress(ValueError):
        if is_maintenance_goal(GoalType(goal_value)):
            return []
    goal_name = goal_value.replace("_", " ").title()
    return [
        {
            "name": goal_name,
            "race_date": plan.goal_date,
            "priority": "A",
            **_race_facts(None),
            "goal_type": goal_value,
        }
    ]


def _fill_from_race_table(repos: Any, races: list[dict[str, Any]]) -> None:
    """Fill each plan race's distance, climb and notes from its race-table row.

    Best-effort: a failed read leaves the plan races as they are.
    """
    for race in races:
        try:
            row = repos.races.get_by_date(race["race_date"])
        except Exception:
            logger.warning("Race table read failed for the plan race", exc_info=True)
            return
        if row is not None:
            race.update(_race_facts(row))


def _race_table_races(
    repos: Any, start: date, end: date, plan_races: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The race table's races inside the window, for their race-day cards.

    A race the coach entered shows even with no plan around it. A cancelled
    or not-started race is left out, and so is a day a plan race already
    holds. Best-effort: a failed read leaves the plan's races alone.
    """
    taken = {race["race_date"] for race in plan_races}
    try:
        rows = repos.races.list(include_past_days=max(0, (clock.today() - start).days))
    except Exception:
        logger.warning("Race table read failed for the calendar", exc_info=True)
        return []
    return [
        {
            "name": race.name,
            "race_date": race.race_date,
            "priority": race.priority,
            **_race_facts(race),
        }
        for race in rows
        if start.isoformat() <= race.race_date <= end.isoformat()
        and race.status not in ("cancelled", "dns")
        and race.race_date not in taken
    ]


def _load_plan_context(
    repos: Any,
    start_date: str,
    end_date: str,
) -> tuple[Any | None, list[Any], list[Any]]:
    """Load active plan and associated sessions from repositories.

    Args:
        repos: Repository container.
        start_date: ISO start date string.
        end_date: ISO end date string.

    Returns:
        Tuple of (active_plan, plan_sessions_in_range, all_plan_sessions).
    """
    active_plan = None
    plan_sessions_in_range: list[Any] = []
    all_plan_sessions: list[Any] = []
    with contextlib.suppress(Exception):
        maybe_active = repos.plan.get_active()
        if _is_valid_active_plan(maybe_active):
            plan_row = cast(Any, maybe_active)
            plan_id = int(plan_row.id)
            active_plan = plan_row
            plan_sessions_in_range = repos.plan.get_sessions_by_date_range(
                plan_id, start_date, end_date
            )
            all_plan_sessions = repos.plan.get_sessions(plan_id)
    return active_plan, plan_sessions_in_range, all_plan_sessions


def _persist_matches(
    repos: Any,
    sessions: list[Any],
    overlays: dict[int, dict[str, Any]],
) -> None:
    """Write discovered activity pairings back onto the session rows.

    Every completion-dependent surface outside the calendar reads
    ``matched_activity_id`` from the DB, so a pairing found here must be
    persisted, not just returned. Only changed rows are written; a write
    failure is swallowed, since the calendar payload already has the
    answer.

    Args:
        repos: Repository container.
        sessions: The plan session rows the overlays were computed for.
        overlays: Session id -> completion overlay.
    """
    updates = [
        {
            "session_id": session.id,
            "matched_activity_id": overlays[session.id]["matched_activity_id"],
            "status": "completed",
        }
        for session in sessions
        if session.id in overlays
        and session.status not in MANUAL_OVERRIDE_STATUSES
        and str(session.matched_activity_id or "")
        != str(overlays[session.id]["matched_activity_id"])
    ]
    if not updates:
        return
    try:
        with repos.session.transaction():
            repos.plan.bulk_update_session_matches(updates)
    except Exception:  # the read path already has the right answer
        logger.warning("Persisting intervals.icu matches failed", exc_info=True)


def _process_plan_sessions(
    active_plan: Any,
    plan_sessions_in_range: list[Any],
    all_plan_sessions: list[Any],
    completions: dict[int, dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any] | None,
]:
    """Serialize plan sessions, overlaid with intervals.icu completion.

    Args:
        active_plan: Active plan row, or None.
        plan_sessions_in_range: Plan sessions within query range.
        all_plan_sessions: All sessions in the active plan.
        completions: Plan session id -> completion overlay, from
            :func:`fetch_intervals_calendar` (empty when intervals.icu is
            unavailable).

    Returns:
        Tuple of (plans_payload, phases_payload, races_payload,
        active_plan_payload).
    """
    if active_plan is None:
        return [], [], [], None

    plans_payload: list[dict[str, Any]] = []
    for session in plan_sessions_in_range:
        payload = dict(serialize_plan_session_row(session))
        overlay = completions.get(session.id)
        if overlay is not None and session.status not in MANUAL_OVERRIDE_STATUSES:
            payload.update(overlay)
            payload["completed"] = True
            payload["status"] = "completed"
        plans_payload.append(payload)

    phases_payload = _build_phase_ranges_from_plan(active_plan)
    if not phases_payload:
        phases_payload = _build_phase_ranges(all_plan_sessions)
    races_payload = _build_races(active_plan)
    active_plan_payload = {
        "id": active_plan.id,
        "name": active_plan.name,
        "goal_type": active_plan.goal_type,
        "goal_date": active_plan.goal_date,
        "start_date": active_plan.plan_start,
        "end_date": active_plan.goal_date,
    }
    return plans_payload, phases_payload, races_payload, active_plan_payload


def build_calendar_data(
    repos: Any,
    client: Any | None,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Build the calendar payload for one date window.

    intervals.icu is optional: with no client, or when its reads fail, the
    payload degrades to the plan alone and says so via
    ``compliance_unavailable``. Pairings discovered on intervals.icu are
    written back onto the session rows (best-effort).

    Args:
        repos: Repository container.
        client: intervals.icu client, or ``None`` when unconfigured.
        start: First date of the window (inclusive).
        end: Last date of the window (inclusive).

    Returns:
        ``activities``, ``calendar_only``, ``plans``, ``phases``, ``races``,
        ``active_plan`` and ``compliance_unavailable``.
    """
    active_plan, sessions_range, all_sessions = _load_plan_context(
        repos, start.isoformat(), end.isoformat()
    )

    activities: list[dict[str, Any]] = []
    completions: dict[int, dict[str, Any]] = {}
    calendar_only: list[dict[str, Any]] = []
    compliance_unavailable = client is None
    if client is not None:
        plan_id = int(active_plan.id) if active_plan is not None else 0
        try:
            activities, completions, calendar_only = fetch_intervals_calendar(
                client,
                plan_id,
                sessions_range,
                (start, end),
                thresholds_source=lambda: get_planner_load_context(repos),
            )
        except Exception:
            logger.warning("intervals.icu calendar fetch failed", exc_info=True)
            compliance_unavailable = True
        else:
            _persist_matches(repos, sessions_range, completions)

    plans, phases, races, plan_payload = _process_plan_sessions(
        active_plan, sessions_range, all_sessions, completions
    )
    _fill_from_race_table(repos, races)
    races = [*races, *_race_table_races(repos, start, end, races)]
    return {
        "activities": activities,
        "calendar_only": calendar_only,
        "plans": plans,
        "phases": phases,
        "races": races,
        "active_plan": plan_payload,
        "compliance_unavailable": compliance_unavailable,
    }


def delete_ad_hoc_workout(external_id: str, client: Any | None) -> dict[str, Any]:
    """Delete one ad-hoc workout event from the intervals.icu calendar.

    Only events keyed by the ad-hoc external_id scheme can be deleted here —
    plan sessions and race events are structurally out of reach. Nothing is
    stored locally for these workouts, so the remote delete is the whole
    operation. Never raises.

    Args:
        external_id: The event's ``coachie:adhoc:...`` external_id.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        ``{"status": "ok", "deleted": n}`` or an error dict with a message.
    """
    if not external_id.startswith(AD_HOC_EXTERNAL_ID_PREFIX):
        return {"status": "error", "message": "Not an ad-hoc workout."}
    if client is None:
        return {
            "status": "error",
            "message": "intervals.icu is not configured — nothing was removed.",
        }
    try:
        deleted = client.bulk_delete_events([external_id])
    except Exception as exc:
        logger.warning("Ad-hoc workout delete failed for %s: %s", external_id, exc)
        return {
            "status": "error",
            "message": f"Removing the workout from intervals.icu failed: {exc}",
        }
    return {"status": "ok", "deleted": int(deleted)}
