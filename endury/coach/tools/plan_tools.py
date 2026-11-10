"""Planner tools for the coach agent.

Let the coach observe the deterministic planner: read the athlete's
committed sessions (with structured steps and coaching intent), compliance
and prescribed-vs-delivered training, the persisted Race Day Outlook
projection, and the session-menu blueprint for any goal. The write half —
status, move, replan, adjust, pause, swap, schedule — lives in
:mod:`endury.coach.tools.plan_edit_tools`; :func:`make_plan_tools` returns
both, reads first.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from endury.analysis.charts import make_spec
from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.activity_tools import delivered_view
from endury.coach.tools.analysis_tools import CHARTS_KEY
from endury.coach.tools.plan_edit_tools import make_plan_edit_tools
from endury.coach.tools.prescription_summary import summarize_prescribed_steps
from endury.coach.tools.registry import coach_tool
from endury.coach.tools.threshold_readout import read_fitness_test
from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import (
    DAILY_TO_WEEKLY_TSS,
    per_sport_ctl_atl,
)
from endury.intervals.models import ActivityStream, IntervalsActivity
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
    is_maintenance_goal,
)
from endury.planner.sessions.menus import BLUEPRINTS, get_session_menu
from endury.planning.commit_plan import (
    plan_races,
    read_athlete_state,
)
from endury.planning.generate_plan import SOLVER_VERSION
from endury.planning.get_active_plan import (
    build_active_render_result,
    get_active_plan_payload,
)
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.wiring.container import RepositoryContainer

logger = logging.getLogger(__name__)

# (phase, mode_key) pairs a plan walks through, in order.
_PHASE_MODES = (
    (PhaseType.BASE, "load"),
    (PhaseType.BUILD, "load"),
    (PhaseType.PEAK, "load"),
    (PhaseType.TAPER, "taper"),
    (PhaseType.TAPER, "race"),
    (PhaseType.MAINTAIN, "load"),
    (PhaseType.MAINTAIN, "recovery"),
)

# Above this many days, get_planned_sessions swaps full steps for a summary.
_FULL_STEPS_MAX_DAYS = 8

#: How far either side of a session's date a linked activity may sit when
#: looking it up for the prescribed-vs-delivered comparison.
_LINK_WINDOW_DAYS = 3

# History window (days) behind today for the live CTL/ATL cross-check:
# 90 days of PMC history plus the 42-day EMA warmup.
_LIVE_PMC_DAYS = 132

#: Per-sport (protocol, where-to-set) for a test session's follow-up. The
#: app never stores a threshold — Planning Inputs writes the new number to
#: intervals.icu, which every later session then reads back.
_THRESHOLD_UPDATE_BY_SPORT: dict[str, tuple[str, str]] = {
    "running": (
        "30 min all-out effort — find sustainable threshold pace",
        "Planning Inputs → Running → Edit: threshold pace and LTHR",
    ),
    "cycling": (
        "20 min all-out effort — FTP = 0.95 x 20-min average power",
        "Planning Inputs → Cycling → Edit: FTP and LTHR",
    ),
    "swimming": (
        "400 m TT + 200 m TT — establish critical swim speed",
        "Planning Inputs → Swimming → Edit: swim CSS",
    ),
}

_THRESHOLD_UPDATE_WHY = (
    "saving there updates the athlete's intervals.icu sport settings, "
    "which every later session's pace/HR targets are read from live"
)


def _threshold_update(sport: str) -> dict[str, str]:
    """Build the post-test "set your new threshold" follow-up for a sport.

    Args:
        sport: The plan session's sport value (``running``/``cycling``/
            ``swimming``).

    Returns:
        Dict with the test ``protocol``, where to ``set_in``, and ``why``
        it matters.
    """
    protocol, set_in = _THRESHOLD_UPDATE_BY_SPORT.get(
        sport,
        (
            "all-out test effort — establish the new threshold",
            "intervals.icu Settings → Sports → <this sport> → threshold "
            "pace/FTP and LTHR",
        ),
    )
    return {
        "protocol": protocol,
        "set_in": set_in,
        "why": _THRESHOLD_UPDATE_WHY,
    }


_TEST_NEXT_STEP = (
    "Offer set_athlete_thresholds with suggested_thresholds as its arguments; "
    "the athlete applies the card. Name the effort it was read from."
)


def _run_test_streams(
    client: IntervalsClient, sport: str, activity: IntervalsActivity
) -> list[ActivityStream]:
    """A run test's time and heart-rate streams, best-effort.

    Without them the readout takes LTHR from the whole effort's average.
    """
    if sport != SportType.RUNNING.value:
        return []
    try:
        return client.get_activity_streams(str(activity.id), ["time", "heartrate"])
    except Exception:
        logger.warning("Could not read test streams %s", activity.id, exc_info=True)
        return []


def _test_follow_up(
    client: IntervalsClient, sport: str, activity: IntervalsActivity
) -> dict[str, Any]:
    """Read a completed test's thresholds, or point at where to set them.

    Args:
        client: intervals.icu client, for the activity's interval rows.
        sport: The plan session's sport value.
        activity: The linked completed activity.

    Returns:
        ``test_result`` plus ``suggested_thresholds`` and ``next_step`` when
        the test effort was read; otherwise ``test_result`` (its ``note``
        says why) and the ``threshold_update`` pointer.
    """
    try:
        detail = client.get_activity(str(activity.id))
    except Exception:
        logger.warning("Could not read test activity %s", activity.id, exc_info=True)
        result: dict[str, Any] = {"note": "The test's intervals could not be read."}
    else:
        result = read_fitness_test(
            sport, detail, _run_test_streams(client, sport, activity)
        )
    suggested = result.pop("suggested_thresholds", None)
    if suggested is None:
        return {"test_result": result, "threshold_update": _threshold_update(sport)}
    return {
        "test_result": result,
        "suggested_thresholds": suggested,
        "next_step": _TEST_NEXT_STEP,
    }


def _parse_workout(workout_json: str | None) -> dict[str, Any]:
    """Parse a plan session's ``workout_json`` column into a dict.

    Args:
        workout_json: Raw JSON string from the ``plan_sessions.workout_json``
            column, or None.

    Returns:
        The parsed workout dict (with ``name``, ``steps``, and
        ``coaching_intent`` keys when present), or an empty dict when
        absent/unparseable.
    """
    if not workout_json:
        return {}
    try:
        parsed = json.loads(workout_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_date(value: str) -> date:
    """Parse an ISO 8601 date string, raising on anything else.

    Args:
        value: Date string expected in YYYY-MM-DD format.

    Returns:
        The parsed date.

    Raises:
        ValueError: If ``value`` is not a valid ISO 8601 date.
    """
    return date.fromisoformat(value)


def _fmt_hours_minutes(total_minutes: float) -> str:
    """Format minutes as ``"5h 27m"`` (or ``"45m"`` under an hour).

    Args:
        total_minutes: Duration in minutes.

    Returns:
        Compact hours/minutes string.
    """
    hours, minutes = divmod(round(total_minutes), 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


#: Human labels for the races block in get_active_plan_overview — a local
#: presentation table (goal/distance names, not the planner's own slugs).
_GOAL_LABELS: dict[str, str] = {
    "running_5k": "5K",
    "running_10k": "10K",
    "running_half_marathon": "half marathon",
    "running_marathon": "marathon",
    "trail_20k": "Trail 20K",
    "trail_50k": "Trail 50K",
    "trail_100k": "Trail 100K",
    "sprint_triathlon": "sprint triathlon",
    "olympic_triathlon": "standard-distance triathlon",
    "half_ironman": "half-distance triathlon",
    "ironman": "full-distance triathlon",
    "running_maintenance": "running maintenance",
    "trail_maintenance": "trail maintenance",
    "triathlon_maintenance": "triathlon maintenance",
}


def _goal_label(goal_type_value: str) -> str:
    """Human label for a race's goal-type slug, e.g. "running_10k" -> "10K".

    Args:
        goal_type_value: Raw ``GoalType`` value string.

    Returns:
        A human-readable label, or the slug with underscores replaced by
        spaces when it is not one of the known goal types.
    """
    return _GOAL_LABELS.get(goal_type_value, goal_type_value.replace("_", " "))


def _days_or_weeks_until(target: date, today: date) -> str:
    """Human countdown from today to a race date.

    Args:
        target: The race date.
        today: The reference "now" date.

    Returns:
        "today", a day count for a race under two weeks out, a week count
        beyond that, or "N days ago" for a race that has already passed.
    """
    delta = (target - today).days
    if delta == 0:
        return "today"
    if delta < 0:
        return f"{-delta} days ago"
    if delta < 14:
        return f"in {delta} days"
    return f"in {delta // 7} weeks"


def _step_summary(steps: list[Any]) -> str:
    """Build a one-line summary of a session's structured steps.

    Args:
        steps: Parsed workout step dicts.

    Returns:
        A short human-readable summary of the step sequence.
    """
    if not steps:
        return "No structured steps recorded."
    step_types = [str(step.get("type", "step")) for step in steps]
    preview = ", ".join(step_types[:6])
    if len(step_types) > 6:
        preview += ", …"
    return f"{len(steps)} steps: {preview}"


def make_plan_tools(
    repos: RepositoryContainer,
    maybe_get_coach: Callable[[], Any],
    intervals_client: IntervalsClient | None = None,
) -> list:
    """Create the planner coach tools with injected dependencies.

    Args:
        repos: Repository container (active plan and its sessions).
        maybe_get_coach: Accessor returning the live coach session (or None),
            used to refresh the coach's plan-context summary after a replan
            — handed on to the plan-editing tools.
        intervals_client: intervals.icu read client used for delivered
            training load and live fitness (optional — those blocks are
            omitted when it is missing).

    Returns:
        List of planner coach tools.
    """
    _repos = repos
    _client = intervals_client

    @coach_tool
    def get_planned_sessions(start_date: str, end_date: str) -> str:
        """Get the athlete's planned training sessions within a date range.

        Use this to see exactly what workouts are scheduled — sport, target
        load, duration, phase, structured steps, and coaching intent. Each
        session's ``session_id`` is what ``set_session_status`` and
        ``skip_planned_session`` expect.
        The ``totals`` block carries precomputed duration/TSS sums, the
        per-sport minute split, and which past sessions were missed —
        quote those numbers instead of re-adding the table yourself.

        For ranges longer than 8 days, full structured steps are replaced
        with a ``step_count`` and a one-line ``summary`` per session to keep
        the payload compact — call again with a shorter range to see the
        full steps for a specific session.

        Args:
            start_date: Start date in YYYY-MM-DD format (inclusive).
            end_date: End date in YYYY-MM-DD format (inclusive).

        Returns:
            JSON string with the active plan name and its scheduled sessions.
        """
        try:
            # Query with the canonical form: fromisoformat also accepts
            # e.g. '20260805', which the TEXT-column compare would miss.
            start = _validate_date(start_date)
            end = _validate_date(end_date)
            active = _repos.plan.get_active()
            if active is None:
                return json.dumps({"message": NO_ACTIVE_PLAN_MESSAGE})
            rows = _repos.plan.get_sessions_by_date_range(
                active.id, start.isoformat(), end.isoformat()
            )

            span_days = (end - start).days + 1
            include_full_steps = span_days <= _FULL_STEPS_MAX_DAYS

            sessions = []
            for row in rows:
                workout = _parse_workout(row.workout_json)
                steps = workout.get("steps") or []
                session_payload: dict[str, Any] = {
                    "session_id": row.id,
                    "date": row.date,
                    "sport": row.sport,
                    "activity": row.activity_key,
                    "family": row.family,
                    "phase": row.phase_type,
                    "target_tss": round(row.target_tss, 1),
                    "duration_minutes": row.duration_minutes,
                    "status": row.status,
                    "completed": row.matched_activity_id is not None,
                    "coaching_intent": workout.get("coaching_intent", ""),
                    "optional": getattr(row, "optional", False),
                }
                if include_full_steps:
                    session_payload["steps"] = steps
                else:
                    session_payload["step_count"] = len(steps)
                    session_payload["summary"] = _step_summary(steps)
                sessions.append(session_payload)

            # Precomputed totals so replies never re-add durations by hand
            # (LLM arithmetic over a session table is error-prone). An
            # optional session is outside the plan's load (R-SESS-037).
            booked = [row for row in rows if not getattr(row, "optional", False)]
            total_minutes = sum(float(row.duration_minutes or 0.0) for row in booked)
            by_sport: dict[str, float] = {}
            for row in booked:
                by_sport[row.sport] = by_sport.get(row.sport, 0.0) + float(
                    row.duration_minutes or 0.0
                )
            today_iso = clock.today().isoformat()
            missed_keys = [
                s["activity"]
                for s in sessions
                if not s["completed"]
                and not s["optional"]
                and s["status"] in ("pending", "unlinked")
                and str(s["date"]) < today_iso
            ]
            totals = {
                "sessions": len(booked),
                "optional_sessions": len(rows) - len(booked),
                "planned_duration_minutes": round(total_minutes, 1),
                "planned_duration": _fmt_hours_minutes(total_minutes),
                "planned_tss": round(sum(float(row.target_tss) for row in booked), 1),
                "by_sport_minutes": {
                    sport: round(minutes, 1) for sport, minutes in by_sport.items()
                },
                "completed": sum(1 for s in sessions if s["completed"]),
                "missed": len(missed_keys),
                "missed_sessions": missed_keys,
            }
            return json.dumps(
                {"plan": active.name, "totals": totals, "sessions": sessions},
                indent=2,
            )
        except Exception as exc:
            return tool_error_response("retrieve planned sessions", exc)

    @coach_tool
    def get_active_plan_overview() -> str:
        """Get a high-level summary of the athlete's active training plan.

        Includes the goal event, goal date, plan start, revision, and the
        phase calendar with exact date boundaries — each phase's start_date
        is the MONDAY its week begins, even when that Monday is a rest day,
        so quote these dates for "when does my taper start?" instead of
        inferring boundaries from individual sessions.

        The ``races`` list is every race the plan was built around — the A
        goal plus any B/C tune-ups — each with a human ``label``, its
        ``date``, and ``days_or_weeks_until``. Use it to answer "when's my
        tune-up 10k?" instead of relying on the single ``goal_date`` field,
        which only ever names the A goal.

        ``ambition`` is the plan's ambition tier ("basic"/"moderate"/
        "competitive"), or null when the linked macro row is missing or
        unreadable. ``measured_inputs`` is the per-sport
        ``{weekly_hours, source}`` the plan was sized from (``source`` is
        "history"/"self_report"/"default"), or null when no athlete-state
        snapshot was persisted with the plan.

        Returns:
            JSON string with active plan metadata, or a message if none exists.
        """
        try:
            active = _repos.plan.get_active()
            if active is None:
                return json.dumps({"message": NO_ACTIVE_PLAN_MESSAGE})
            solve = _parse_workout(active.solve_result_json)
            ambition: str | None = None
            try:
                macro = _repos.plan.get_macro_for_plan(active)
                if macro is not None:
                    ambition = AmbitionLevel(str(macro.ambition_level)).value
            except Exception:
                logger.debug(
                    "Could not resolve the active plan's ambition", exc_info=True
                )
            measured_inputs: dict[str, Any] | None = None
            state = read_athlete_state(active)
            if state.get("sports"):
                sources = state.get("sources") or {}
                measured_inputs = {
                    sport: {
                        "weekly_hours": info.get("recent_weekly_hours"),
                        "source": sources.get(sport),
                    }
                    for sport, info in state["sports"].items()
                }
            phase_weeks: list[dict[str, Any]] = []
            phase_calendar = solve.get("phase_calendar")
            if isinstance(phase_calendar, list) and phase_calendar:
                plan_start = date.fromisoformat(str(active.plan_start))
                runs: list[dict[str, Any]] = []
                for idx, phase in enumerate(phase_calendar):
                    if runs and runs[-1]["phase"] == phase:
                        runs[-1]["last"] = idx
                    else:
                        runs.append({"phase": str(phase), "first": idx, "last": idx})
                phase_weeks = [
                    {
                        "phase": run["phase"],
                        "weeks": (
                            f"{run['first'] + 1}-{run['last'] + 1}"
                            if run["first"] != run["last"]
                            else str(run["first"] + 1)
                        ),
                        "start_date": (
                            plan_start + timedelta(weeks=run["first"])
                        ).isoformat(),
                        "end_date": (
                            plan_start + timedelta(weeks=run["last"], days=6)
                        ).isoformat(),
                    }
                    for run in runs
                ]
            today = clock.today()
            races = [
                {
                    "priority": race.get("priority", "A"),
                    "goal_type": race["goal_type"],
                    "label": _goal_label(race["goal_type"]),
                    "date": race["race_date"],
                    "days_or_weeks_until": _days_or_weeks_until(
                        date.fromisoformat(race["race_date"]), today
                    ),
                }
                for race in plan_races(solve)
            ]
            return json.dumps(
                {
                    "name": active.name,
                    "goal_type": active.goal_type,
                    "goal_date": active.goal_date,
                    "plan_start": active.plan_start,
                    "revision": active.revision,
                    "status": active.status,
                    "solver_version": active.solver_version,
                    "ambition": ambition,
                    "measured_inputs": measured_inputs,
                    "phase_weeks": phase_weeks,
                    "races": races,
                },
                indent=2,
            )
        except Exception as exc:
            return tool_error_response("retrieve active plan overview", exc)

    @coach_tool
    def get_session_menu_catalog(goal: str) -> str:
        """Look up the session-menu blueprint for a training goal.

        Returns the deterministic session families the planner prescribes per
        phase (base/build/peak/taper) at moderate ambition, so you can explain
        why a plan looks the way it does.

        Args:
            goal: Goal slug, e.g. "ironman", "half_ironman", "olympic_triathlon",
                "sprint_triathlon", "running_marathon", "running_half_marathon",
                "running_10k", "running_5k".

        Returns:
            JSON string with the goal's per-phase session families.
        """
        try:
            try:
                goal_type = GoalType(goal.lower())
            except ValueError:
                options = sorted(g.value for g in BLUEPRINTS)
                return json.dumps(
                    {"error": f"Unknown goal '{goal}'.", "options": options}
                )
            if goal_type not in BLUEPRINTS:  # pragma: no cover — every goal has one
                options = sorted(g.value for g in BLUEPRINTS)
                return json.dumps(
                    {"error": f"No blueprint for '{goal}'.", "options": options}
                )

            phases = []
            for phase, mode_key in _PHASE_MODES:
                specs = get_session_menu(
                    goal_type, phase, mode_key, AmbitionLevel.MODERATE
                )
                if specs:
                    phases.append(
                        {
                            "phase": phase.value,
                            "mode_key": mode_key,
                            "sessions": [s.activity_key.value for s in specs],
                        }
                    )
            return json.dumps({"goal": goal_type.value, "phases": phases}, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve session menu catalog", exc)

    @coach_tool
    def get_plan_compliance(chart: bool = False) -> str:
        """Report whole-plan compliance: completion and planned-vs-delivered TSS.

        Wraps the same compliance block the Schedule tab shows (completed,
        upcoming, missed session counts, completion rate), plus planned vs.
        delivered training load summed across the whole active plan, plus
        ``missed_sessions`` naming each missed session (date, sport,
        activity, target TSS) — quote those when the athlete asks WHICH
        sessions they missed. Delivered load comes from the intervals.icu
        activities linked to the plan's sessions.

        Args:
            chart: Draw planned against done load per week so far under
                your reply; set it when the athlete asks how they are
                keeping to the plan or to see it.

        Returns:
            JSON string with plan compliance stats, or a message if no
            active plan exists.
        """
        try:
            payload = get_active_plan_payload(
                repos=_repos, solver_version=SOLVER_VERSION
            )
            active_plan = payload.get("active_plan")
            if payload.get("status") != "ok" or active_plan is None:
                return json.dumps({"message": NO_ACTIVE_PLAN_MESSAGE})

            # An optional session is outside the plan's load (R-SESS-037).
            sessions = [
                row
                for row in _repos.plan.get_sessions(active_plan["id"])
                if not getattr(row, "optional", False)
            ]
            # One ranged fetch instead of a lookup per matched session; the
            # payload dict carries the plan's date bounds.
            load_by_id = _load_by_activity_id(
                _client, active_plan["start_date"], active_plan["goal_date"]
            )
            planned_tss = 0.0
            delivered_tss = 0.0
            for row in sessions:
                planned_tss += float(row.target_tss)
                if row.matched_activity_id is None:
                    continue
                delivered_tss += load_by_id.get(str(row.matched_activity_id), 0.0)

            # Name the missed sessions — "which ones did I miss?" must be
            # answerable from this payload, not with generalities.
            today_iso = clock.today().isoformat()
            missed_sessions = [
                {
                    "date": row.date,
                    "sport": row.sport,
                    "activity": row.activity_key,
                    "target_tss": round(float(row.target_tss), 1),
                }
                for row in sessions
                if row.matched_activity_id is None
                and str(getattr(row, "status", "pending")) in ("pending", "unlinked")
                and str(row.date) < today_iso
            ]
            result = {
                "plan": active_plan["name"],
                **active_plan["compliance"],
                "planned_tss": round(planned_tss, 1),
                "delivered_tss": (
                    round(delivered_tss, 1) if _client is not None else None
                ),
                "missed_sessions": missed_sessions,
                CHARTS_KEY: _compliance_chart(sessions, load_by_id, clock.today())
                if chart and _client is not None
                else [],
            }
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve plan compliance", exc)

    @coach_tool
    def get_session_vs_actual(date: str) -> str:
        """Compare prescribed vs. delivered training for a date's session(s).

        Prescribed comes from the plan's structured workout (steps, target
        TSS, duration, coaching intent). Delivered comes from the linked
        intervals.icu activity: training load, moving time, intervals.icu's
        own planned-vs-actual compliance score, plus the session shape (avg
        HR, intensity factor, normalized power or pace, decoupling, and
        intervals.icu's interval recap). For split-by-split detail follow up
        with ``get_activity_detail`` on the delivered ``activity_id``. A
        session not yet linked to an activity is reported as such rather
        than guessed at. ``prescribed.targets_summary`` restates every
        step's bands in prose, each next to its own zone label — quote
        heart-rate bands from there: power zones and HR zones are numbered
        separately and never share a band, so a power zone 3 step is NOT
        heart-rate zone 3. A completed fitness-test session also carries
        ``test_result`` (the effort read off the activity) and, when the
        effort was found, ``suggested_thresholds`` with a ``next_step``:
        offer ``set_athlete_thresholds`` with those values. Without a usable
        effort it carries ``threshold_update`` instead — the protocol and
        where the athlete sets the threshold by hand.

        Reply contract — when relaying the result:
        * The athlete's own ``feel`` / ``rpe`` is their verdict on the session
          and outranks ``compliance_pct`` and the load ratio when judging how
          it went; with ``source`` GARMIN_CONNECT the figures come from the
          Garmin watch.

        Args:
            date: ISO date (YYYY-MM-DD) to inspect.

        Returns:
            JSON string comparing prescribed and delivered training for that
            date, or a message if there is no active plan or no planned
            session that day.
        """
        try:
            # Query with the canonical form: fromisoformat also accepts
            # e.g. '20260805', which the TEXT-column compare would miss.
            day_date = _validate_date(date)
            day = day_date.isoformat()
            active = _repos.plan.get_active()
            if active is None:
                return json.dumps({"message": NO_ACTIVE_PLAN_MESSAGE})
            rows = _repos.plan.get_sessions_by_date_range(active.id, day, day)
            if not rows:
                return json.dumps({"message": f"No planned session on {day}."})

            # A session can be fulfilled by an activity logged a day or
            # two off, so widen the lookup window around the session date.
            activities = _activities_by_id(
                _client,
                day_date - timedelta(days=_LINK_WINDOW_DAYS),
                day_date + timedelta(days=_LINK_WINDOW_DAYS),
            )
            sessions = []
            for row in rows:
                workout = _parse_workout(row.workout_json)
                steps = workout.get("steps") or []
                prescribed = {
                    "activity": row.activity_key,
                    "family": row.family,
                    "target_tss": round(row.target_tss, 1),
                    "duration_minutes": row.duration_minutes,
                    "coaching_intent": workout.get("coaching_intent", ""),
                    "targets_summary": summarize_prescribed_steps(steps),
                    "steps": steps,
                }
                entry: dict[str, Any] = {
                    "session_id": row.id,
                    "prescribed": prescribed,
                }
                if row.matched_activity_id is None:
                    entry["delivered"] = None
                    entry["message"] = "Not yet linked to a completed activity."
                    sessions.append(entry)
                    continue

                activity = activities.get(str(row.matched_activity_id))
                if activity is None:
                    entry["delivered"] = None
                    entry["message"] = "Linked activity could not be found."
                    sessions.append(entry)
                    continue

                entry["delivered"] = delivered_view(activity)
                if row.family == ActivityFamily.TEST.value:
                    # Only a test that actually happened has a number to set;
                    # the activity was found through the client.
                    assert _client is not None
                    entry.update(_test_follow_up(_client, row.sport, activity))
                sessions.append(entry)
            return json.dumps({"date": day, "sessions": sessions}, indent=2)
        except Exception as exc:
            return tool_error_response("compare planned vs actual training", exc)

    @coach_tool
    def get_race_day_outlook() -> str:
        """Get the persisted CTL/TSB projection curve (Race Day Outlook).

        Reads the per-sport and aggregate fitness (CTL) and freshness (TSB)
        forecast that was computed and stored when the active plan was
        generated or last replanned — it is not recomputed here. Also
        reports today's LIVE CTL/ATL/TSB next to the projection's value for
        today: when they diverge (missed sessions, illness), quote the live
        numbers and treat the race-day curve as the plan's intent, not a
        prediction. Downsampled to weekly points plus the explicit race-day
        value to keep the payload compact.

        Returns:
            JSON string with weekly CTL/TSB points per sport and in
            aggregate, ``projected_today`` vs ``live_today`` current values,
            or a message if there is no active plan or stored projection.
        """
        try:
            active = _repos.plan.get_active()
            if active is None:
                return json.dumps({"message": NO_ACTIVE_PLAN_MESSAGE})
            render = build_active_render_result(active)
            if render is None:
                return json.dumps(
                    {"message": "No render data stored for the active plan."}
                )
            projections = render.get("projections") or {}
            if not projections:
                return json.dumps(
                    {"message": "No projection data stored for the active plan."}
                )

            plan_start = date.fromisoformat(active.plan_start)
            # Maintenance plans have no race: the final point marks the
            # end of the block, not a race day.
            maintenance = False
            with contextlib.suppress(ValueError):
                maintenance = is_maintenance_goal(GoalType(str(active.goal_type)))
            end_marker = "block_end" if maintenance else "race_day"

            def _downsample(series: list[float]) -> list[dict[str, Any]]:
                if not series:
                    return []
                points = [
                    {
                        "date": (plan_start + timedelta(days=i)).isoformat(),
                        "value": series[i],
                    }
                    for i in range(0, len(series), 7)
                ]
                last_index = len(series) - 1
                if last_index % 7 != 0:
                    race_date = plan_start + timedelta(days=last_index)
                    points.append(
                        {
                            "date": race_date.isoformat(),
                            "value": series[last_index],
                            end_marker: True,
                        }
                    )
                else:
                    points[-1][end_marker] = True
                return points

            result: dict[str, Any] = {
                "plan": active.name,
                "goal_date": active.goal_date,
            }
            if maintenance:
                result["note"] = (
                    "Maintenance plan: goal_date marks the end of the "
                    "block, not a race."
                )
            # The stored curve assumes the plan is delivered as prescribed;
            # after missed or extra sessions it diverges from reality until
            # the next replan. Surface today's live vs projected values so
            # the outlook is never quoted as if it were current fitness.
            today = clock.today()
            result["projection_vintage"] = (
                "Stored at plan generation / last replan, assuming full "
                "compliance — it does not absorb missed or extra sessions. "
                "Compare projected_today with live_today; if they diverge, "
                "trust live_today and consider a replan."
            )
            day_index = (today - plan_start).days
            aggregate_ctl = projections.get("aggregate_ctl") or []
            aggregate_tsb = projections.get("aggregate_tsb") or []
            if 0 <= day_index < len(aggregate_ctl):
                result["projected_today"] = {
                    "date": today.isoformat(),
                    "ctl": round(aggregate_ctl[day_index], 1),
                    "tsb": round(aggregate_tsb[day_index], 1),
                }
            live = _live_fitness(_client, today)
            if live is not None:
                result["live_today"] = live
            result |= {
                "aggregate_ctl": _downsample(projections.get("aggregate_ctl") or []),
                "aggregate_tsb": _downsample(projections.get("aggregate_tsb") or []),
                "ctl_by_sport": {
                    sport: _downsample(series)
                    for sport, series in (projections.get("ctl_by_sport") or {}).items()
                },
                "tsb_by_sport": {
                    sport: _downsample(series)
                    for sport, series in (projections.get("tsb_by_sport") or {}).items()
                },
            }
            return json.dumps(result, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve race day outlook", exc)

    return [
        get_planned_sessions,
        get_active_plan_overview,
        get_session_menu_catalog,
        get_plan_compliance,
        get_session_vs_actual,
        get_race_day_outlook,
        *make_plan_edit_tools(repos, maybe_get_coach, intervals_client),
    ]


def _activities_by_id(
    client: IntervalsClient | None,
    oldest: str | date,
    newest: str | date,
) -> dict[str, IntervalsActivity]:
    """Fetch intervals.icu activities in a window, keyed by activity id.

    Args:
        client: intervals.icu read client, or None when no key is
            configured.
        oldest: Window start (ISO date string or date).
        newest: Window end (ISO date string or date).

    Returns:
        Activity id -> activity; empty when there is no client.
    """
    if client is None:
        return {}
    start = date.fromisoformat(str(oldest))
    end = date.fromisoformat(str(newest))
    return {
        str(activity.id): activity
        for activity in client.list_activities(start, end)
        if activity.id is not None
    }


#: The most weeks the compliance chart shows: the page draws up to 12 pairs
#: of bars with the change above each.
_CHART_WEEKS = 12


def _compliance_chart(
    sessions: list[Any], load_by_id: dict[str, float], today: date
) -> list[dict[str, Any]]:
    """Planned against done load for each plan week up to today."""
    weeks: dict[str, list[float]] = {}
    for row in sessions:
        day = date.fromisoformat(str(row.date)[:10])
        if day > today:
            continue
        week = weeks.setdefault(
            (day - timedelta(days=day.weekday())).isoformat(), [0.0, 0.0]
        )
        week[0] += float(row.target_tss)
        if row.matched_activity_id is not None:
            week[1] += load_by_id.get(str(row.matched_activity_id), 0.0)
    shown = sorted(weeks)[-_CHART_WEEKS:]
    if not shown:
        return []
    return [
        make_spec(
            "bar",
            shown,
            [[round(weeks[w][k]) for w in shown] for k in (0, 1)],
            "Planned and done load per week",
            x_label="week",
            unit="load",
            labels=["Planned", "Done"],
        )
    ]


def _load_by_activity_id(
    client: IntervalsClient | None,
    oldest: str | date,
    newest: str | date,
) -> dict[str, float]:
    """Return each activity's training load in a window, keyed by id."""
    return {
        activity_id: float(activity.icu_training_load or 0.0)
        for activity_id, activity in _activities_by_id(client, oldest, newest).items()
    }


def _live_fitness(
    client: IntervalsClient | None,
    today: date,
) -> dict[str, Any] | None:
    """Compute today's live combined CTL/ATL/TSB from intervals.icu load.

    Args:
        client: intervals.icu read client, or None when no key is
            configured.
        today: Reference date.

    Returns:
        A ``{date, ctl, atl, tsb}`` dict in daily-TSS space, or None when
        there is no client or no training history.
    """
    if client is None:
        return None
    activities = client.list_activities(today - timedelta(days=_LIVE_PMC_DAYS), today)
    by_sport = per_sport_ctl_atl(activities, today)
    measured = [values for values in by_sport.values() if values is not None]
    if not measured:
        return None
    ctl = sum(values[0] for values in measured) / DAILY_TO_WEEKLY_TSS
    atl = sum(values[1] for values in measured) / DAILY_TO_WEEKLY_TSS
    return {
        "date": today.isoformat(),
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(ctl - atl, 1),
    }
