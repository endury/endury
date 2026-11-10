"""Coach tools package.

Provides `make_tools()` which composes all tool groups into a single list
ready for binding to the coach turn loop.

Tool groups:
    profile_tools         — athlete profile and the athlete-memory document
    activity_tools        — completed activities from intervals.icu
    zone_tools            — thresholds: read, set (Apply card), race estimate
    load_tools            — training-load and readiness analytics
    brief_tools           — the brief for a coming session or race
    plan_tools            — planner reads (make_plan_tools also returns the edits)
    plan_edit_tools       — status, move, replan, adjust, pause, swap, schedule
    plan_build_tools      — create_plan and record_self_assessment
    plan_directive_tools  — set_plan_directives (an active plan's constraints)
    race_tools            — upsert_race, cancel_or_move_race, list_races,
                            add_race_to_plan
    workout_tools         — ad-hoc structured workout generation
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from endury.coach.tools.activity_tools import make_activity_tools
from endury.coach.tools.analysis_tools import Executor, make_analysis_tools
from endury.coach.tools.brief_tools import make_brief_tools
from endury.coach.tools.load_tools import make_load_tools
from endury.coach.tools.plan_build_tools import make_plan_build_tools
from endury.coach.tools.plan_directive_tools import make_plan_directive_tools
from endury.coach.tools.plan_tools import make_plan_tools
from endury.coach.tools.profile_tools import make_profile_tools
from endury.coach.tools.race_tools import make_race_tools
from endury.coach.tools.registry import CoachTool
from endury.coach.tools.workout_tools import make_workout_tools
from endury.coach.tools.zone_tools import make_zone_tools
from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    AthleteMemoryRepository,
)
from endury.intervals.client import IntervalsClient
from endury.wiring.container import RepositoryContainer

#: The tools that read or edit the active plan and can only refuse without
#: one. The model is offered them while a plan is active.
NEEDS_ACTIVE_PLAN = frozenset(
    {
        "get_planned_sessions",
        "get_active_plan_overview",
        "get_plan_compliance",
        "get_session_vs_actual",
        "get_race_day_outlook",
        "recommend_todays_session",
        "set_session_status",
        "skip_planned_session",
        "move_planned_session",
        "replan_active_plan",
        "adjust_current_week",
        "swap_planned_session",
        "schedule_workout_override",
        "set_plan_directives",
        "add_race_to_plan",
    }
)


def offered_tools(tools: list[CoachTool], *, has_plan: bool) -> list[CoachTool]:
    """Return the tools the model is offered this turn.

    Args:
        tools: Every registered tool.
        has_plan: Whether the athlete has an active plan.

    Returns:
        All of them with a plan; without one, all but
        :data:`NEEDS_ACTIVE_PLAN`.
    """
    if has_plan:
        return tools
    return [tool for tool in tools if tool.name not in NEEDS_ACTIVE_PLAN]


def make_tools(
    athlete_repo: AthleteProfileRepository,
    memory_repo: AthleteMemoryRepository,
    repos: RepositoryContainer | None = None,
    intervals_client: IntervalsClient | None = None,
    maybe_get_coach: Callable[[], Any] | None = None,
    analysis_executor: Executor | None = None,
) -> list:
    """Create the full tool list with injected dependencies.

    Tool groups whose dependency is missing are simply omitted: without an
    intervals.icu client the coach keeps its plan/profile tools but cannot
    read activities, thresholds, or training load.

    Args:
        athlete_repo: Repository for athlete profile data.
        memory_repo: Repository for the athlete-memory document.
        repos: Repository container, used for the plan and workout tools
            (optional — pass to enable them).
        intervals_client: intervals.icu read client (optional — pass to
            enable the activity, threshold, and training-load tools).
        maybe_get_coach: Accessor returning the live coach session (or
            None), used by the planner tools to refresh the coach's
            plan-context summary after a replan.
        analysis_executor: Runs an analysis script in a sandbox (optional —
            pass to enable ``run_analysis``; the browser passes the sandbox
            worker, tests the in-process runner).
    """
    tools = make_profile_tools(athlete_repo, memory_repo)
    if intervals_client is not None:
        tools += make_activity_tools(intervals_client)
        tools += make_zone_tools(intervals_client)
        if analysis_executor is not None:
            tools += make_analysis_tools(intervals_client, analysis_executor)
    if repos is not None:
        if intervals_client is not None:
            tools += make_load_tools(repos.plan, intervals_client)
            tools += make_brief_tools(repos, intervals_client)
        tools += make_plan_tools(
            repos, maybe_get_coach or (lambda: None), intervals_client
        )
        tools += make_plan_build_tools(
            repos, maybe_get_coach or (lambda: None), intervals_client
        )
        tools += make_plan_directive_tools(
            repos, maybe_get_coach or (lambda: None), intervals_client
        )
        tools += make_race_tools(
            repos, maybe_get_coach or (lambda: None), intervals_client
        )
        tools += make_workout_tools(repos)
    return tools


__all__ = [
    "NEEDS_ACTIVE_PLAN",
    "make_activity_tools",
    "make_analysis_tools",
    "make_brief_tools",
    "make_load_tools",
    "make_plan_build_tools",
    "make_plan_directive_tools",
    "make_plan_tools",
    "make_profile_tools",
    "make_race_tools",
    "make_tools",
    "make_workout_tools",
    "make_zone_tools",
    "offered_tools",
]
