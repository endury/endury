"""Query-side helpers for the planner RPC methods and the bootstrap payload.

These functions turn persisted planner state back into the JSON structures used
by the schedule UI, while hiding repository details from the RPC layer.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from endury.common import clock
from endury.intervals.athlete_source import IntervalsSource
from endury.planner.foundations.maintenance_tables import MAINTENANCE_WEEKLY_HOURS
from endury.planner.foundations.types import AmbitionLevel, GoalType, SportType
from endury.planner.phase.phase_config import MAX_HORIZON_WEEKS
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.pipeline import default_profile_for
from endury.planning.athlete_state import (
    measured_sports_with_reason,
    read_once,
    resolve_athlete_state,
)
from endury.planning.commit_plan import plan_races
from endury.planning.generate_plan import min_join_weeks
from endury.planning.offered_plans import is_offered
from endury.planning.plan_maturity import TIER_NOTES
from endury.planning.wire.sessions import (
    _session_is_completed,
    _session_status,
    serialize_active_plan,
)
from endury.planning.wire.types import (
    SessionSummaryLike,
    WeeklySummaryPayload,
)

logger = logging.getLogger(__name__)


def build_self_assessment_block(repos: Any) -> dict[str, dict[str, float | int]]:
    """Return the stored self-assessment answers in wire shape."""
    return {
        sport: {
            "weekly_hours": row.weekly_hours,
            "longest_session_min": row.longest_session_min,
        }
        for sport, row in repos.self_assessment.get_all().items()
    }


def build_measured_hours_block(
    repos: Any, measured: set[SportType], source: IntervalsSource | None = None
) -> dict[str, dict[str, float]]:
    """Return the measured weekly training hours per goal and sport.

    Uses ``resolve_athlete_state`` per goal and keeps only sports resolved
    from measured history — a goal default is left out, since the wizard
    uses these numbers to size a maintenance block's ambition tier.

    Args:
        repos: Repository container (intervals_client, self_assessment).
        measured: The sports history can measure; empty skips the read.
        source: The intervals.icu source to read, shared with the caller so
            the history is fetched once.
    """
    if not measured:
        return {}
    block: dict[str, dict[str, float]] = {}
    for goal in GoalType:
        resolved = resolve_athlete_state(repos, goal, source=source)
        hours = {
            sport.value: round(state.recent_weekly_hours, 1)
            for sport, state in resolved.athlete.sports.items()
            if resolved.sources.get(sport) == "history"
        }
        if hours:
            block[goal.value] = hours
    return block


def build_bootstrap_payload(*, repos: Any) -> dict[str, Any]:
    """Build the initial planner bootstrap payload consumed by Plan Studio.

    Serves the goal/ambition catalogs (each goal with its maturity tier,
    that tier's sentence and the tiers on offer), the ``athlete_data`` block the
    wizard uses to decide whether to ask the self-assessment questions,
    and the ``plan_limits`` block (min/max horizon, min-join weeks,
    maintenance hours) read straight from the phase planner's own tables
    so the wizard never hand-duplicates them.

    Always available: with no ``INTERVALS_API_KEY`` or an intervals.icu
    outage, it still returns the catalogs, with no measured sports and
    ``available_reason`` explaining why.

    Args:
        repos: Repository container (intervals_client, self_assessment).
    """
    source = read_once(repos)
    measured, reason = measured_sports_with_reason(repos, source=source)
    return {
        "goal_types": [
            {
                "id": goal.value,
                "maturity": GOAL_PROFILES[goal].maturity.value,
                "maturity_note": TIER_NOTES[GOAL_PROFILES[goal].maturity],
                "offered_tiers": [
                    level.value for level in AmbitionLevel if is_offered(goal, level)
                ],
            }
            for goal in GoalType
        ],
        "ambition_levels": [level.value for level in AmbitionLevel],
        "default_ambition_level": "moderate",
        "plan_limits": {
            "min_horizon_weeks": {
                goal.value: GOAL_PROFILES[goal].min_horizon_weeks for goal in GoalType
            },
            "min_join_weeks": {goal.value: min_join_weeks(goal) for goal in GoalType},
            "max_horizon_weeks": MAX_HORIZON_WEEKS,
            "maintenance_weekly_hours": {
                goal.value: {level.value: hours for level, hours in tiers.items()}
                for goal, tiers in MAINTENANCE_WEEKLY_HOURS.items()
            },
        },
        "athlete_data": {
            "history_sufficient": bool(measured),
            "measured_sports": sorted(sport.value for sport in measured),
            "measured_weekly_hours": build_measured_hours_block(
                repos, measured, source
            ),
            "available_reason": reason,
            "self_assessment": build_self_assessment_block(repos),
            "goal_sports": {
                goal.value: [sport.value for sport in default_profile_for(goal).sports]
                for goal in GoalType
            },
        },
    }


def get_active_plan_payload(
    *,
    repos: Any,
    solver_version: str,
) -> dict[str, Any]:
    """Return the canonical active-plan summary shown in lightweight planner views.

    The ``races`` block is the plan's own request races (A plus any B/C
    tune-ups), the only place they survive commit, so reopening the plan
    editor can reseed the tune-ups.
    """
    active = repos.plan.get_active()
    if active is None or active.solver_version != solver_version:
        return {"status": "empty", "active_plan": None}

    sessions = repos.plan.get_sessions(active.id)
    macro_row = repos.plan.get_macro_for_plan(active)
    macro_summary: dict[str, Any] | None = None
    if macro_row is not None:
        macro_summary = {
            "id": macro_row.id,
            "goal_type": macro_row.goal_type,
            "goal_date": macro_row.goal_date,
            "plan_start": macro_row.plan_start,
            "total_weeks": macro_row.total_weeks,
            "ambition_level": macro_row.ambition_level,
            "coaching_brief": macro_row.coaching_brief,
            "revision": macro_row.revision,
            "status": macro_row.status,
        }
    return {
        "status": "ok",
        "active_plan": serialize_active_plan(active, sessions),
        "macro_plan": macro_summary,
        "races": plan_races(json.loads(active.solve_result_json or "{}")),
    }


def build_active_render_result(
    active: Any, live_sessions: list[Any] | None = None
) -> dict[str, Any] | None:
    """Build the rich render payload used when Plan Studio reopens an active plan.

    The stored ``solve_result_json`` is authoritative for the plan's
    *shape*; delivery state is not, since its ``achieved_tss`` is baked
    equal to ``target_tss`` at materialization. When ``live_sessions`` is
    given, each session is overlaid with its real ``status``,
    ``matched_activity_id``, and delivered ``achieved_tss`` (``None`` when
    nothing was delivered), and each ``weekly_summary`` entry gets a
    computed ``delivered_tss`` beside the untouched planned ``total_tss``.
    Without ``live_sessions`` no delivery state is reported.

    Args:
        active: Active plan row with ``solve_result_json``.
        live_sessions: Current ``plan_sessions`` rows to overlay delivery
            state from; ``None`` serves the plan shape as stored.
    """
    solve_data = json.loads(active.solve_result_json or "{}")

    sessions: list[SessionSummaryLike] = [
        cast(dict[str, Any], item)
        for item in solve_data.get("sessions") or []
        if isinstance(item, dict)
    ]
    if not sessions:
        return None

    delivered_by_week: dict[Any, float] = {}
    if live_sessions is not None:
        today = clock.today()
        rows_by_slot = {str(row.slot_id): row for row in live_sessions}
        for item in sessions:
            row = rows_by_slot.get(str(item.get("slot_id")))
            if row is None:
                continue
            # The delivery-state keys are an overlay on top of the stored
            # blob shape — widen past the TypedDict for the writes.
            live = cast(dict[str, Any], item)
            completed = _session_is_completed(row, row.matched_activity_id)
            live["status"] = _session_status(row, completed, today)
            live["matched_activity_id"] = row.matched_activity_id
            live["achieved_tss"] = (
                round(float(row.achieved_tss), 1) if completed else None
            )
            if completed:
                week_key = live.get("week_index")
                delivered_by_week[week_key] = (
                    delivered_by_week.get(week_key, 0.0) + live["achieved_tss"]
                )

    phase_calendar = [
        str(item)
        for item in solve_data.get("phase_calendar") or []
        if isinstance(item, str)
    ]
    weekly_summary: list[WeeklySummaryPayload] = [
        cast(WeeklySummaryPayload, item)
        for item in solve_data.get("weekly_summary") or []
        if isinstance(item, dict)
    ]
    if live_sessions is not None:
        for week in weekly_summary:
            # Same read-time overlay as the per-session one: the stored
            # blob keeps the planned totals, delivery is computed here.
            live_week = cast(dict[str, Any], week)
            live_week["delivered_tss"] = round(
                delivered_by_week.get(live_week.get("week_index"), 0.0), 1
            )

    return {
        "status": "ok",
        "sessions": sessions,
        "weekly_summary": weekly_summary,
        "phase_calendar": phase_calendar,
        "coach_brief": str(solve_data.get("coach_brief") or ""),
        "projections": solve_data.get("projections") or {},
        "goal_type": str(active.goal_type),
        "plan_start": str(active.plan_start),
        "goal_date": str(active.goal_date),
    }


def get_macro_plan_payload(*, repos: Any) -> dict[str, Any]:
    """Return the active macro-plan record in a frontend-friendly JSON shape."""
    active = repos.plan.get_active()
    if active is None:
        return {"status": "empty", "macro_plan": None}
    macro_row = repos.plan.get_macro_for_plan(active)
    if macro_row is None:
        return {"status": "empty", "macro_plan": None}
    try:
        weeks = json.loads(macro_row.weeks_json)
    except (json.JSONDecodeError, TypeError):
        weeks = []
    return {
        "status": "ok",
        "macro_plan": {
            "id": macro_row.id,
            "goal_type": macro_row.goal_type,
            "goal_date": macro_row.goal_date,
            "plan_start": macro_row.plan_start,
            "total_weeks": macro_row.total_weeks,
            "ambition_level": macro_row.ambition_level,
            "coaching_brief": macro_row.coaching_brief,
            "revision": macro_row.revision,
            "revision_reason": macro_row.revision_reason,
            "status": macro_row.status,
            "weeks": weeks,
        },
    }
