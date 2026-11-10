"""Plan generate + commit orchestration.

The worker behind the ``generate_plan`` and ``planner_commit`` RPC
methods and the coach's ``create_plan``: derives measured athlete state,
runs the deterministic pipeline, materializes sessions, and assembles the
``GenerateSuccess`` wire payload; commit persists the previewed result and
refreshes the coach's plan context.

A race closer than its goal's ``min_horizon_weeks`` is not refused
outright: the plan is built at full length over a virtual earlier start
and its leading weeks truncated (``horizon_join_weeks``), confirm-gated
by :func:`short_horizon_gate` above :func:`min_join_weeks`. A plan whose
effective tier is Experimental (:mod:`endury.planning.plan_maturity`) is
confirm-gated too, by :func:`maturity_gate`, once the pipeline has built it.
:func:`collect_plan_warnings` merges the planner's own soft-validation
warnings with the capacity-based race-rehearsal and key-session warnings,
and the race-sim fallback report (:func:`rehearsal_skipped_warning`), into
the preview's single ``warning`` string.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from endury.intervals.workout_dsl import SportThresholds
from endury.planner import PlanRequest, PlanResult, generate_plan
from endury.planner.foundations.request import AthleteState, PlanningConstraints
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseType,
    RaceGoal,
    SportType,
    WeekMode,
    is_maintenance_goal,
)
from endury.planner.macro.macro_planner import QPInfeasibleError
from endury.planner.phase.phase_tables import GOAL_PROFILES, goal_sports
from endury.planner.pipeline import default_recent_weekly_hours
from endury.planner.sessions.menus import SessionSpec, get_session_menu
from endury.planner.sessions.session_generator import (
    REHEARSAL_STEP_FRACTION,
    race_sim_weeks,
)
from endury.planning.athlete_state import (
    ResolvedAthleteState,
    describe_missing_data,
    log_resolved_state,
    resolve_athlete_state,
)
from endury.planning.coach_brief import _goal_label, build_coach_brief
from endury.planning.commit_plan import (
    build_solve_result_payload,
    push_plan_events,
    update_coach_plan_context,
)
from endury.planning.load_planner_metrics import (
    PlannerRepos,
    get_planner_load_context,
)
from endury.planning.materialize_plan import drop_past, materialize_sessions
from endury.planning.offered_plans import is_offered, not_offered_message
from endury.planning.plan_directives import apply_directives
from endury.planning.plan_maturity import CONFIRM_TAIL, PlanVerdict, plan_verdict
from endury.planning.projections import build_projections
from endury.planning.race_book import sync_plan_races
from endury.planning.wire.macro import serialize_macro_plan
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateError,
    GenerateRequest,
    GenerateSuccess,
)
from endury.planning.wire.sessions import build_commit_session_rows
from endury.planning.wire.types import WeeklySummaryPayload
from endury.planning.wire.weekly import build_weekly_summary

logger = logging.getLogger(__name__)

#: Engine version stamped on committed plans; the single visible version.
SOLVER_VERSION = "planner_v2_session_menu_v1"

#: User-facing stand-in for a raw macro-QP solver failure (see
#: ``QPInfeasibleError``) — the phase planner's own request-validation
#: errors (e.g. "horizon too short") are already human-readable and are
#: passed through untouched.
QP_INFEASIBLE_MESSAGE = (
    "This plan can't be built with the current race date, ambition level, "
    "and recent training history. Try a later race date, a lower ambition "
    "level, or fewer overlapping goals."
)

ProgressCallback = Callable[[dict[str, Any]], None]

#: Number of progress frames one generate run emits.
_PROGRESS_TOTAL = 4

#: Refusal when the weeks left are under the goal's minimum join. A
#: mid-join can skip base weeks; it cannot skip the training.
_TOO_SHORT_MESSAGE = (
    "With only {weeks} to race day there isn't enough time to train for this "
    "race — even a short-notice plan needs at least {floor}. Treat the race "
    "as a tune-up under a later goal, or pick a later race."
)

#: Minimum peak-week hours a long-course plan's own capacity ramp must reach
#: before its longest bike/run/swim sessions plausibly rehearse race
#: distance. Bisected against the literature-derived shape rules of the
#: plan-quality scorecard (``bench/rulebook.yaml``: ``long_ride_peak``,
#: ``long_run_peak``, ``long_swim_peak``, ``race_pace_long_bike_peak``,
#: ``recovery_week_depth``, ``longest_long_session_in_peak``,
#: ``build_brick_run_duration``): every violation clears at peak >= 12.13 h
#: (Ironman) / 9.03 h (half-Ironman) recent-hours seeds and reappears below
#: it. Unlike the infeasible-horizon guard this never refuses —
#: the plan still generates, with ``RACE_REHEARSAL_WARNING`` attached.
_RACE_REHEARSAL_MIN_PEAK_HOURS: dict[GoalType, float] = {
    GoalType.HALF_IRONMAN: 9.0,
    GoalType.IRONMAN: 12.0,
    # Trail 100K's rehearsal is the 5-6 h race-simulation long run
    # (docs/research/trail_running.md, "Longest-run ceiling"). A week that
    # holds one needs the CTS peak-block floor of 9 h/wk ("Peak weekly
    # hours by tier"), also the bottom of the recreational 8-10 h peak
    # band. Trail 50K has no row: an hours guess is the wrong tool for its
    # 4 h rehearsal — whether it fits is read from what the generator
    # actually did (see rehearsal_skipped_warning below).
    GoalType.TRAIL_100K: 9.0,
    # Trail 20K's rehearsal is the 2-3 h race-sim long run
    # (docs/research/trail_running.md, "Longest-run ceiling") — the
    # longest run of the whole preparation, for a race the same
    # document's "Race duration" section puts at 3 h 15. Below ~4.4 h of
    # peak week the capacity second pass scales it under its authored
    # floor: measured over 8-20 wk x 3 tiers, 8 wk BASIC realizes 87 min
    # against a 105 min floor at a 4.23 h peak week, and the next-lowest
    # cell (12 wk BASIC, 4.48 h) is at full length. Residual: 9-11 wk
    # BASIC (4.48-4.50 h) and 8-11 wk MODERATE (5.98-6.50 h) are also
    # scaled 4-20 min under their floors and stay silent — peak-week
    # hours do not separate them from the full-length cells at the same
    # volume, so catching them needs a different signal (the realized
    # rehearsal duration itself) rather than a lower bound.
    GoalType.TRAIL_20K: 4.4,
}

#: User-facing warning when a long-course plan's capacity projection can't
#: reach race-rehearsal volume (see ``_RACE_REHEARSAL_MIN_PEAK_HOURS``).
RACE_REHEARSAL_WARNING = (
    "Your recent training volume is low enough that this plan's peak week "
    "may not reach full race-rehearsal length for its longest bike, run, "
    "and swim sessions. The plan still builds you toward race day — a "
    "longer horizon or more current training volume would close the gap."
)

#: The rehearsal warning when the plan is already at the goal's recommended
#: maximum length: a longer horizon is off the table, so the honest advice
#: is a maintenance block first (the capacity ramp is seeded from recent
#: hours, so the rebuilt plan scales up with the base the block delivers).
RACE_REHEARSAL_WARNING_MAINTENANCE_FIRST = (
    "Your recent training volume is low enough that this plan's peak week "
    "may not reach full race-rehearsal length for its longest sessions — "
    "and the preparation is already at its recommended maximum length, so "
    "a longer horizon can't close the gap. Consider running a {label} "
    "block first to build your weekly volume, then generating this "
    "preparation fresh: the plan scales up with the base you bring to it."
)

#: Which maintenance goal the maintenance-first advice names, per warned
#: goal. Only goals in ``_RACE_REHEARSAL_MIN_PEAK_HOURS`` need a row.
_MAINTENANCE_FIRST_LABEL: dict[GoalType, str] = {
    GoalType.HALF_IRONMAN: "triathlon maintenance",
    GoalType.IRONMAN: "triathlon maintenance",
    GoalType.TRAIL_20K: "trail maintenance",
    GoalType.TRAIL_100K: "trail maintenance",
}

#: Minimum peak-week hours, per road goal and tier, below which low recent
#: volume costs the plan its key work: the capacity second pass drops a
#: quality session from a PEAK load week, or scales the peak long run under
#: its authored minimum. Per tier because the menus scale by tier: a BASIC
#: plan is whole at peak weeks where a COMPETITIVE one is already squeezed.
#: Measured over each goal's matrix horizons plus its recommended
#: maximum x 3 tiers x recent 1.0-4.0 h/wk, against the same plan at
#: 10 h/wk. Residuals, silent though squeezed: 10K COMPETITIVE 12 wk at
#: 1.75 h/wk (3.83 h peak), marathon MODERATE 12 wk at 3.5 h/wk (6.03 h).
#: Warned though whole: 8-wk 10K plans at 1.0-1.75 h/wk, HM BASIC 24 wk at
#: 1.5 h/wk (3.15 h), marathon BASIC 12 wk at 3.0 h/wk and 18 wk at
#: 2.5 h/wk (4.00-4.07 h), marathon COMPETITIVE 18 wk at 2.5 h/wk (6.50 h).
#: The MODERATE marathon row sits at 5.9, not 6.1, so the whole 20-wk
#: marathon-with-HM-B plan (6.08 h peak) stays silent.
_KEY_SESSION_MIN_PEAK_HOURS: dict[GoalType, dict[AmbitionLevel, float]] = {
    GoalType.RUNNING_10K: {
        AmbitionLevel.BASIC: 2.6,
        AmbitionLevel.MODERATE: 2.9,
        AmbitionLevel.COMPETITIVE: 3.2,
    },
    GoalType.RUNNING_HALF_MARATHON: {
        AmbitionLevel.BASIC: 3.2,
        AmbitionLevel.MODERATE: 3.8,
        AmbitionLevel.COMPETITIVE: 4.2,
    },
    GoalType.RUNNING_MARATHON: {
        AmbitionLevel.BASIC: 4.1,
        AmbitionLevel.MODERATE: 5.9,
        AmbitionLevel.COMPETITIVE: 6.9,
    },
}

#: User-facing warning when a road plan's capacity can't hold its key
#: sessions (see ``_KEY_SESSION_MIN_PEAK_HOURS``).
KEY_SESSION_WARNING = (
    "Your recent training volume is low enough that this plan can't fit all "
    "of its key sessions — some harder workouts will be shortened or made "
    "optional, and the long run may stay short of what the race asks. The plan "
    "still builds you toward race day — a longer horizon or more current "
    "training volume would close the gap."
)

#: The key-session warning at the goal's recommended maximum length.
KEY_SESSION_WARNING_MAINTENANCE_FIRST = (
    "Your recent training volume is low enough that this plan can't fit all "
    "of its key sessions — and the preparation is already at its recommended "
    "maximum length, so a longer horizon can't close the gap. Consider a "
    "running maintenance block first to build your weekly volume, then "
    "generating this preparation fresh: the plan scales up with the base you "
    "bring to it."
)


def _progress_step(progress: ProgressCallback, message: str, step: int) -> None:
    """Emit one progress frame.

    Args:
        progress: The progress callback.
        message: Human-readable step label.
        step: 1-based step index.
    """
    progress(
        {
            "type": "progress",
            "message": message,
            "step": step,
            "total": _PROGRESS_TOTAL,
        }
    )


def _parse_goals(request: GenerateRequest) -> list[RaceGoal]:
    """Parse wire race models into planner RaceGoals."""
    return [
        RaceGoal(
            goal_type=GoalType(race.goal_type),
            race_date=date.fromisoformat(race.race_date),
            priority=race.priority,
        )
        for race in request.races
    ]


def _a_goal(goals: list[RaceGoal]) -> RaceGoal:
    """Return the single A-goal (validated later by the phase planner)."""
    return next((g for g in goals if g.priority == "A"), goals[0])


def _parse_request(
    request: GenerateRequest, training_start_override: date | None, offered: bool
) -> tuple[list[RaceGoal], date, AmbitionLevel] | GenerateError:
    """Read the request's goals, start and tier, refusing a malformed one.

    Args:
        request: The generate request.
        training_start_override: Replanning start date (wins over the
            request's ``training_start``).
        offered: Also refuse a plan its menu does not offer
            (:mod:`endury.planning.offered_plans`).

    Returns:
        ``(goals, training_start, ambition)``, or the refusal.
    """

    def _fail(message: str, code: str | None = None) -> GenerateError:
        return GenerateError(
            error_message=message, error_code=code, solver_version=SOLVER_VERSION
        )

    try:
        goals = _parse_goals(request)
        training_start = training_start_override or date.fromisoformat(
            request.training_start
        )
        ambition = AmbitionLevel(request.ambition_level)
    except ValueError as exc:
        logger.warning("Invalid generate request: %s", exc)
        return _fail(str(exc))
    if not goals:
        return _fail("At least one race is required.")
    goal_type = _a_goal(goals).goal_type
    if offered and not is_offered(goal_type, ambition):
        return _fail(not_offered_message(goal_type, ambition), "plan_not_offered")
    return goals, training_start, ambition


def _snapshot_athlete(
    athlete: AthleteState, derived: bool, resolved: ResolvedAthleteState
) -> dict[str, Any]:
    """Persistable athlete-state snapshot for one generation."""
    return build_athlete_state_payload(
        athlete,
        derived=derived,
        sources=resolved.sources,
        thresholds=resolved.thresholds,
    )


def _generate_plan_result(
    goals: list[RaceGoal],
    training_start: date,
    ambition: AmbitionLevel,
    athlete: AthleteState | None,
    prior_training_weeks: int,
    horizon_join_weeks: int,
    weekly_hours_cap: float | None,
) -> PlanResult | GenerateError:
    """Run the deterministic pipeline, mapping failures to a wire error.

    Macro-QP infeasibility (``QPInfeasibleError``) is swapped for a
    friendly, actionable message; every other rejection (the phase
    planner's own request validation, e.g. horizon-too-short) already
    reads as a reason and passes through minus its internal prefix.

    Args:
        goals: Parsed race goals.
        training_start: Plan start date.
        ambition: Requested ambition level.
        athlete: Derived athlete state (``None`` -> goal-type default).
        prior_training_weeks: Weeks already trained (replanning).
        horizon_join_weeks: Weeks a too-short race horizon lacks.
        weekly_hours_cap: Athlete-authored soft volume cap (``None`` -> none).

    Returns:
        The pipeline's ``PlanResult``, or a ``GenerateError`` on failure.
    """
    try:
        return generate_plan(
            PlanRequest(
                goals=goals,
                training_start=training_start,
                ambition=ambition,
                athlete=athlete,
                constraints=PlanningConstraints(weekly_hours_cap=weekly_hours_cap),
                prior_training_weeks=prior_training_weeks,
                horizon_join_weeks=horizon_join_weeks,
            )
        )
    except QPInfeasibleError as exc:
        logger.warning("Macro QP infeasible: %s", exc)
        return GenerateError(
            error_message=QP_INFEASIBLE_MESSAGE, solver_version=SOLVER_VERSION
        )
    except ValueError as exc:
        logger.warning("planner rejected the request: %s", exc)
        # "Invalid PhaseRequest: " is our class name, not a reason.
        message = str(exc).removeprefix("Invalid PhaseRequest: ")
        return GenerateError(error_message=message, solver_version=SOLVER_VERSION)


def _assemble_brief(
    result: PlanResult,
    sessions: list[Any],
    weekly_summary: Any,
    ambition: AmbitionLevel,
    prior_training_weeks: int,
    athlete_payload: dict[str, Any],
    effective_athlete: AthleteState,
    a_goal: RaceGoal,
    short_join: bool,
) -> tuple[str, str | None]:
    """Build the coach brief (with athlete-state context) and any warning."""
    coach_brief = build_coach_brief(
        result.phase_plan,
        sessions,
        ambition=ambition,
        prior_weeks=prior_training_weeks,
        athlete_state=athlete_payload,
    )
    warning = collect_plan_warnings(
        result, a_goal.goal_type, weekly_summary, ambition, effective_athlete
    )
    if short_join:
        warning = prepend_short_join_warning(
            warning, a_goal.goal_type, result.phase_plan, effective_athlete, ambition
        )
    if warning:
        coach_brief = f"{coach_brief} {warning}"
    return coach_brief, warning


def _resolve_athlete(
    repos: PlannerRepos, goal_type: GoalType, *, enforce: bool
) -> ResolvedAthleteState | GenerateError:
    """Resolve the athlete state, refusing default-only sports when enforced.

    Args:
        repos: Repository container.
        goal_type: The A-goal the plan is built for.
        enforce: Refuse when a goal sport has neither activity history nor
            a stored self-assessment answer.

    Returns:
        The resolved state, or the ``athlete_data_required`` wire error the
        wizard turns into its questionnaire.
    """
    resolved = resolve_athlete_state(repos, goal_type)
    missing = [
        sport for sport, source in resolved.sources.items() if source == "default"
    ]
    if enforce and missing:
        logger.info("Refusing to generate: no data for %s", missing)
        return GenerateError(
            error_message=describe_missing_data(missing),
            error_code="athlete_data_required",
            missing_sports=[sport.value for sport in missing],
            solver_version=SOLVER_VERSION,
        )
    log_resolved_state(resolved, goal_type)
    return resolved


def _materialize(
    request: GenerateRequest,
    repos: PlannerRepos,
    result: PlanResult,
    training_start: date,
) -> list[Any]:
    """Materialize sessions, trim the elapsed opening days, apply directives."""
    resolver = get_planner_load_context(repos)
    sessions = drop_past(materialize_sessions(result, resolver), training_start)
    return apply_directives(sessions, request.directives)


def build_athlete_state_payload(
    athlete: AthleteState,
    *,
    derived: bool,
    sources: dict[SportType, str],
    thresholds: dict[SportType, SportThresholds] | None = None,
) -> dict[str, Any]:
    """JSON-safe snapshot of the athlete state used for generation.

    Args:
        athlete: The resolved planner athlete state.
        derived: Whether any sport was measured from history.
        sources: Per-sport provenance labels.
        thresholds: Per-sport thresholds as read from the data source; they
            travel with the plan so its calendar push can emit
            percent-of-threshold workout targets.

    Returns:
        The ``athlete_state`` block persisted with the plan.
    """
    return {
        "derived_from_history": derived,
        "sources": {sport.value: source for sport, source in sources.items()},
        "thresholds": {
            sport.value: asdict(value) for sport, value in (thresholds or {}).items()
        },
        "sports": {
            sport.value: {
                "recent_weekly_hours": state.recent_weekly_hours,
                "longest_recent_min": {
                    family.value: minutes
                    for family, minutes in state.longest_recent_min.items()
                },
                "ctl_0": state.ctl_0,
                "atl_0": state.atl_0,
                "css_sec_per_100m": state.css_sec_per_100m,
                "ftp_watts": state.ftp_watts,
                "threshold_pace_sec_per_km": state.threshold_pace_sec_per_km,
            }
            for sport, state in athlete.sports.items()
        },
    }


def build_request_payload(request: GenerateRequest) -> dict[str, Any]:
    """JSON block persisted in solve_result_json for replanning.

    Also the canonical input to :func:`build_input_hash`, so an optional
    field is written only when it is set: an uncapped plan (or one with no
    directives) hashes exactly as it did before the field existed.

    Args:
        request: The generate request the plan was built from.

    Returns:
        The ``request`` block a replan resumes from.
    """
    payload: dict[str, Any] = {
        "races": [
            {
                "goal_type": race.goal_type,
                "race_date": race.race_date,
                "priority": race.priority,
            }
            for race in request.races
        ],
        "training_start": request.training_start,
        "ambition_level": request.ambition_level,
        "accept_short_horizon": request.accept_short_horizon,
    }
    if request.weekly_hours_cap is not None:
        payload["weekly_hours_cap"] = request.weekly_hours_cap
    if request.directives:
        payload["directives"] = request.directives
    return payload


def request_from_payload(payload: dict[str, Any], races: list[Any]) -> GenerateRequest:
    """Rebuild the generate request a replan re-runs the pipeline with.

    Field-by-field, like its writer: a field missing from this call is
    silently dropped from the replan. A plan committed before a field
    existed has no such key, so every read falls back to the default.

    Args:
        payload: The persisted ``request`` block.
        races: The races to plan around (a replan adds the B/C races behind
            its start to the ones ahead only when it keeps the trained weeks).

    Returns:
        The request for this replan's ``run_generate``.
    """
    return GenerateRequest(
        races=races,
        training_start=payload["training_start"],
        ambition_level=payload["ambition_level"],
        accept_short_horizon=True,  # the committed plan already carries it
        accept_experimental=True,  # confirmed when the plan was built
        weekly_hours_cap=payload.get("weekly_hours_cap"),
        directives=payload.get("directives"),
    )


def build_input_hash(
    request: GenerateRequest, athlete_payload: dict[str, Any], solver_version: str
) -> str:
    """Canonical SHA-256 over the full deterministic input.

    Args:
        request: The generate request the plan was built from.
        athlete_payload: The athlete-state snapshot it was sized from.
        solver_version: The engine version stamped on the plan.

    Returns:
        The plan's ``input_hash``.
    """
    canonical = json.dumps(
        {
            "request": build_request_payload(request),
            "athlete_state": athlete_payload,
            "solver_version": solver_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _weeks(count: int) -> str:
    """Render a week count with its unit (``1 week`` / ``5 weeks``)."""
    return f"{count} week" if count == 1 else f"{count} weeks"


def _total_weeks(race_date: date, training_start: date) -> int:
    """Weeks a horizon spans, race week included (the phase planner's math)."""
    return (race_date - training_start).days // 7 + 1


def min_join_weeks(goal_type: GoalType) -> int:
    """Shortest horizon a mid-preparation join is still worth building.

    Below the taper plus two training weeks there is nothing left to train:
    the athlete would be handed a taper (and the week before it) for a race
    they never prepared for. Served to the wizard in ``plan_limits`` so its
    date hint and the gate's own refusal agree.

    Args:
        goal_type: The A-goal type.

    Returns:
        The floor in weeks, race week included.
    """
    return GOAL_PROFILES[goal_type].taper_weeks + 2


def short_horizon_deficit(
    goal_type: GoalType, race_date: date, training_start: date
) -> int:
    """Weeks a race horizon falls short of its goal's minimum.

    Mirrors the phase planner's own week math (``validate_phase_request``
    check 4) so feeding the result back as ``prior_training_weeks`` lands
    the virtual horizon exactly on ``min_horizon_weeks``. Maintenance blocks
    prepare for no race and never fall short.

    Args:
        goal_type: The A-goal type.
        race_date: The A-race date.
        training_start: The start the horizon is measured from — the plan's
            training start, or the virtual one when weeks are already being
            truncated.

    Returns:
        The number of missing weeks, or ``0`` when the horizon suffices.
    """
    if is_maintenance_goal(goal_type):
        return 0
    total = _total_weeks(race_date, training_start)
    return max(0, GOAL_PROFILES[goal_type].min_horizon_weeks - total)


def short_horizon_gate(
    request: GenerateRequest,
    a_goal: RaceGoal,
    training_start: date,
    prior_training_weeks: int,
    solver_version: str,
) -> int | GenerateError:
    """Resolve the mid-preparation join a short race horizon needs.

    The deficit is measured from the *virtual* start the pipeline will use
    (``training_start`` minus the caller's own truncation), so a replan
    resuming mid-prep recovers the deficit its plan was built with instead
    of double-applying the horizon it has left. Only a caller that manages
    no truncation of its own can be asked to confirm — a committed plan
    already carries that decision.

    Args:
        request: The generate request (carries ``accept_short_horizon``).
        a_goal: The A-goal the plan is built for.
        training_start: The plan's training start.
        prior_training_weeks: Weeks the caller already truncates.
        solver_version: Engine version stamped on the refusal.

    Returns:
        The weeks to add to ``prior_training_weeks`` (``0`` when the horizon
        is long enough — or so broken that the pipeline's own request
        validation must be the one to report it), the ``horizon_too_short``
        refusal the wizard turns into its confirmation dialog, or the
        terminal refusal below ``min_join_weeks``.
    """
    virtual_start = training_start - timedelta(weeks=prior_training_weeks)
    total_weeks = _total_weeks(a_goal.race_date, virtual_start)
    deficit = short_horizon_deficit(a_goal.goal_type, a_goal.race_date, virtual_start)
    if total_weeks < 1:
        # A race on or before the start is not a horizon to top up: the
        # phase planner refuses it with the reason the athlete needs.
        return 0
    if not deficit or prior_training_weeks:
        return deficit
    floor = min_join_weeks(a_goal.goal_type)
    if total_weeks < floor:
        return GenerateError(
            error_message=_TOO_SHORT_MESSAGE.format(
                weeks=_weeks(total_weeks), floor=_weeks(floor)
            ),
            solver_version=solver_version,
        )
    if request.accept_short_horizon:
        return deficit
    min_weeks = GOAL_PROFILES[a_goal.goal_type].min_horizon_weeks
    return GenerateError(
        error_message=(
            f"A full {_goal_label(a_goal.goal_type.value)} preparation takes "
            f"{_weeks(min_weeks)} and your race is {_weeks(total_weeks)} away."
        ),
        error_code="horizon_too_short",
        min_weeks=min_weeks,
        actual_weeks=total_weeks,
        deficit_weeks=deficit,
        solver_version=solver_version,
    )


def _join_weeks(
    request: GenerateRequest,
    goals: list[RaceGoal],
    training_start: date,
    prior_training_weeks: int,
) -> int | GenerateError:
    """Run :func:`short_horizon_gate` for a well-formed goal set, else ``0``.

    A malformed goal set is the pipeline's refusal to make, not the gate's.
    """
    if [goal.priority for goal in goals].count("A") != 1:
        return 0
    return short_horizon_gate(
        request, _a_goal(goals), training_start, prior_training_weeks, SOLVER_VERSION
    )


def maturity_gate(
    request: GenerateRequest,
    a_goal: RaceGoal,
    horizon_start: date,
    joined: int,
    athlete: AthleteState,
) -> PlanVerdict | GenerateError:
    """Judge the plan, refusing an Experimental one the athlete has not confirmed.

    Runs once the pipeline has accepted the request, so every validation
    refusal (and the short-horizon question) comes first.

    Args:
        request: The generate request (carries the ambition tier and
            ``accept_experimental``).
        a_goal: The A-goal the plan is built for.
        horizon_start: The plan's own first Monday — before any weeks a
            replan already truncates.
        joined: Weeks the short-horizon gate added (a part-way join).
        athlete: The resolved athlete state the plan is sized from.

    Returns:
        The verdict, or the ``experimental_plan`` refusal naming its reasons.
    """
    sports = goal_sports(a_goal.goal_type)
    verdict = plan_verdict(
        a_goal.goal_type,
        AmbitionLevel(request.ambition_level),
        horizon_weeks=_total_weeks(a_goal.race_date, horizon_start),
        short_notice=joined > 0,
        recent_hours=sum(
            state.recent_weekly_hours
            for sport, state in athlete.sports.items()
            if sport in sports
        ),
    )
    if verdict.note is None or request.accept_experimental:
        return verdict
    return GenerateError(
        error_message=f"{verdict.note} {CONFIRM_TAIL}",
        error_code="experimental_plan",
        reasons=list(verdict.reasons),
        solver_version=SOLVER_VERSION,
    )


def can_retry_fresh(a_race: dict[str, Any], start: date, prior: int) -> bool:
    """Whether a failed replan may retry as a fresh full periodization.

    The fallback exists for a plan whose *truncated* request the engine
    rejects (a detrained athlete mid-prep, say): dropping the truncation
    asks for a whole periodization from the same Monday instead. That is
    only a different question while a whole periodization still fits — with
    fewer weeks left than the goal's minimum the gate hands the pipeline
    the very truncation the first attempt already failed on, and would
    stamp an ordinary plan short-notice on the way.

    Args:
        a_race: The stored request's A-race block.
        start: The Monday the regeneration starts from.
        prior: Weeks the failed attempt truncated; with nothing truncated
            there is nothing to drop.

    Returns:
        ``True`` when the retry asks the engine a genuinely different
        question.
    """
    return prior > 0 and not short_horizon_deficit(
        GoalType(a_race["goal_type"]), date.fromisoformat(a_race["race_date"]), start
    )


def prepend_short_join_warning(
    warning: str | None,
    goal_type: GoalType,
    phase_plan: PhasePlan,
    athlete: AthleteState,
    ambition: AmbitionLevel,
) -> str:
    """Prefix a plan's warning with its short-notice mid-join note.

    The second sentence is chosen by comparing the athlete's own recent
    volume with the volume the preparation assumes an arriving athlete has
    (``default_recent_weekly_hours``): below it, joining mid-plan is a real
    risk and is named as one; at or above it, it is merely a fact.

    Args:
        warning: The plan's existing warning, if any.
        goal_type: The A-goal type.
        phase_plan: The truncated phase plan — its length is the weeks the
            athlete actually has.
        athlete: The resolved athlete state the plan was sized from.
        ambition: The plan's ambition tier.

    Returns:
        The note, followed by the existing warning when there was one.
    """
    actual_weeks = len(phase_plan.weeks)
    min_weeks = GOAL_PROFILES[goal_type].min_horizon_weeks
    recent_hours = sum(st.recent_weekly_hours for st in athlete.sports.values())
    note = (
        f"Short-notice plan: a full {_goal_label(goal_type.value)} "
        f"preparation takes {_weeks(min_weeks)} and your race is "
        f"{_weeks(actual_weeks)} away, so this plan joins a "
        f"{min_weeks}-week preparation at week "
        f"{min_weeks - actual_weeks + 1}, skipping the early base weeks. "
    )
    if recent_hours < default_recent_weekly_hours(goal_type, ambition):
        note += (
            "Starting mid-preparation without consistent recent training is "
            "not recommended — expect the first weeks to feel hard."
        )
    else:
        note += (
            "Your recent training volume is already at the level this "
            "preparation assumes, so joining mid-plan should be manageable."
        )
    return f"{note} {warning}" if warning else note


def _capacity_warning(
    goal_type: GoalType,
    min_peak_hours: float,
    weekly_summary: list[WeeklySummaryPayload],
    plan_weeks: int,
    warnings: tuple[str, str],
) -> str | None:
    """Pick the warning for a peak week under *min_peak_hours*, if any.

    Args:
        goal_type: The A-goal type.
        min_peak_hours: The goal's peak-week threshold.
        weekly_summary: Materialized per-week totals for the generated plan.
        plan_weeks: Length of the generated plan in weeks.
        warnings: The plain warning and its maintenance-first variant.

    Returns:
        ``None`` at or above the threshold; below it the maintenance-first
        variant when the plan already spans the goal's
        ``max_recommended_weeks`` (so "use a longer horizon" is not
        actionable advice), else the plain warning.
    """
    peak_hours = max(w["total_duration_minutes"] for w in weekly_summary) / 60.0
    if peak_hours >= min_peak_hours:
        return None
    max_weeks = GOAL_PROFILES[goal_type].max_recommended_weeks
    if max_weeks is not None and plan_weeks >= max_weeks:
        return warnings[1]
    return warnings[0]


def race_rehearsal_warning(
    goal_type: GoalType,
    weekly_summary: list[WeeklySummaryPayload],
    plan_weeks: int,
) -> str | None:
    """Warn when a long-course plan's peak week can't reach race-rehearsal volume.

    Only ``HALF_IRONMAN``, ``IRONMAN``, ``TRAIL_20K`` and ``TRAIL_100K``
    carry a threshold — recent hours are the capacity ramp's
    base, so they gate plan *shape* as well as size. Every other
    goal returns ``None`` unconditionally.

    Args:
        goal_type: The A-goal type.
        weekly_summary: Materialized per-week totals for the generated plan.
        plan_weeks: Length of the generated plan in weeks.

    Returns:
        ``RACE_REHEARSAL_WARNING`` if the plan's peak week falls short of
        the goal's threshold — or its maintenance-first variant when the
        plan already spans the goal's ``max_recommended_weeks``, so "use a
        longer horizon" is not actionable advice. ``None`` otherwise.
    """
    min_peak_hours = _RACE_REHEARSAL_MIN_PEAK_HOURS.get(goal_type)
    if min_peak_hours is None:
        return None
    return _capacity_warning(
        goal_type,
        min_peak_hours,
        weekly_summary,
        plan_weeks,
        (
            RACE_REHEARSAL_WARNING,
            RACE_REHEARSAL_WARNING_MAINTENANCE_FIRST.format(
                label=_MAINTENANCE_FIRST_LABEL[goal_type]
            ),
        ),
    )


def key_session_warning(
    goal_type: GoalType,
    ambition: AmbitionLevel,
    weekly_summary: list[WeeklySummaryPayload],
    plan_weeks: int,
    recent_hours: float,
) -> str | None:
    """Warn when a road plan's peak week is too small to hold its key sessions.

    Only ``RUNNING_10K``, ``RUNNING_HALF_MARATHON`` and ``RUNNING_MARATHON``
    carry thresholds; every other goal returns ``None``. The warning blames
    recent volume, so it also needs the athlete's recent running hours under
    the threshold: a peak kept short by the horizon alone (a short-notice
    join, a mid-preparation replan) stays silent for an athlete who already
    trains at that volume.

    Args:
        goal_type: The A-goal type.
        ambition: The plan's ambition tier (the menus scale by tier).
        weekly_summary: Materialized per-week totals for the generated plan.
        plan_weeks: Length of the generated plan in weeks.
        recent_hours: The athlete's recent weekly running hours.

    Returns:
        ``KEY_SESSION_WARNING`` if the plan's peak week and the athlete's
        recent volume both fall short of the goal's threshold for *ambition*
        — or its maintenance-first variant at the goal's
        ``max_recommended_weeks``. ``None`` otherwise.
    """
    thresholds = _KEY_SESSION_MIN_PEAK_HOURS.get(goal_type)
    if thresholds is None or recent_hours >= thresholds[ambition]:
        return None
    return _capacity_warning(
        goal_type,
        thresholds[ambition],
        weekly_summary,
        plan_weeks,
        (KEY_SESSION_WARNING, KEY_SESSION_WARNING_MAINTENANCE_FIRST),
    )


#: User-facing warning when the plan's PEAK LOAD week could not hold its
#: race-rehearsal session at full length (session_generator R-SESS-023: a
#: `race_sim` session fires only when it fits its share of the week), for a
#: goal whose rehearsal is running only (see ``rehearsal_skipped_warning``).
REHEARSAL_SKIPPED_WARNING = (
    "Your recent running volume is too low for a full race-rehearsal long "
    "run, so the week set aside for it keeps a shorter long run. More "
    "weekly running before you start, or a later race date, would make "
    "room for it."
)

#: The same warning, worded for a goal whose race-rehearsal slots are not
#: running only — today, Ironman's PEAK LOAD race-sim week also rehearses
#: the long ride, so "running volume"/"long run" would be wrong for it.
REHEARSAL_SKIPPED_WARNING_MULTISPORT = (
    "Your recent training volume is too low for a full race-rehearsal "
    "session, so the week set aside for it keeps a shorter one. More "
    "weekly training before you start, or a later race date, would make "
    "room for it."
)


#: The warning when the PEAK LOAD week had room for the rehearsal but the
#: athlete's long run had not grown to its length in the weeks available
#: (R-SESS-023: the long run grows about 10 % a week from the athlete's
#: recent longest, and a rehearsal fires only at its authored length).
REHEARSAL_TOO_SOON_WARNING = (
    "Your long run needs more weeks to grow gradually to a full race-rehearsal "
    "length, so the week set aside for the rehearsal keeps a shorter long "
    "run. A later race date would give it that time."
)


def rehearsal_skipped_warning(
    result: PlanResult, goal_type: GoalType, ambition: AmbitionLevel
) -> str | None:
    """Warn when the plan's PEAK LOAD week fell back from its race-rehearsal.

    Reports what the generator actually did (R-SESS-023's race-sim
    fallback) rather than guessing from peak-week hours: a menu that
    authors ``race_sim`` slots for its PEAK load phase promises a
    full-length rehearsal on the plan's PEAK LOAD week; when none of those
    slots made it into the plan, that week fell back to a routine PEAK LOAD
    week instead. A running rehearsal's warning names the cause
    (``_running_rehearsal_warning``).

    Args:
        result: The pipeline result (phase plan and placed sessions).
        goal_type: The A-goal type.
        ambition: The plan's ambition tier (the menus scale by tier).

    Returns:
        ``None`` when the goal's PEAK load menu authors no ``race_sim``
        slot, the plan has no PEAK LOAD week to hold one, or a
        race-rehearsal session did fire; otherwise the warning, worded for
        a running-only rehearsal or, when the goal's rehearsal spans other
        sports too, the sport-neutral variant.
    """
    rehearsal_specs = [
        spec
        for spec in get_session_menu(goal_type, PhaseType.PEAK, "load", ambition)
        if spec.race_sim
    ]
    if not rehearsal_specs:
        return None
    has_peak_load_week = any(
        week.phase is PhaseType.PEAK and week.mode is WeekMode.LOAD
        for week in result.phase_plan.weeks
    )
    if not has_peak_load_week:
        return None
    rehearsal_slots = {spec.slot_name for spec in rehearsal_specs}
    if any(
        session.slot_name in rehearsal_slots and session.optional_reason is None
        for session in result.sessions
    ):
        return None
    if {spec.sport for spec in rehearsal_specs} != {SportType.RUNNING}:
        return REHEARSAL_SKIPPED_WARNING_MULTISPORT
    return _running_rehearsal_warning(result, rehearsal_specs)


def _running_rehearsal_warning(
    result: PlanResult, rehearsal_specs: list[SessionSpec]
) -> str:
    """Name why a running rehearsal fell back (R-SESS-023).

    When the long rehearsal's minimum fits its share of the plan's biggest
    PEAK LOAD week but is more than its step past the longest long run
    before its week, the long run had too few weeks to grow; otherwise the
    weekly volume was too low.

    Args:
        result: The pipeline result (phase plan and placed sessions).
        rehearsal_specs: The goal's PEAK load ``race_sim`` specs.

    Returns:
        ``REHEARSAL_TOO_SOON_WARNING`` or ``REHEARSAL_SKIPPED_WARNING``.
    """
    minutes = [
        (s.week, s.activity_key, 60.0 * s.target_hours)
        for s in result.sessions
        if s.optional_reason is None  # the booked weeks (R-SESS-037)
    ]
    peak_week = max(
        sum(m for w, _key, m in minutes if w == week.week)
        for week in result.phase_plan.weeks
        if week.phase is PhaseType.PEAK and week.mode is WeekMode.LOAD
    )
    # The long run the rehearsal had to step from: the longest before its week.
    sim_week = min(race_sim_weeks(result.phase_plan.weeks), default=0)
    for spec in rehearsal_specs:
        if not spec.progressive:
            continue
        longest = max(
            (m for w, key, m in minutes if key == spec.activity_key and w < sim_week),
            default=0.0,
        )
        step = 1.0 + (spec.max_step or REHEARSAL_STEP_FRACTION)
        if (
            spec.min_minutes > spec.key * peak_week
            or longest * step >= spec.min_minutes
        ):
            return REHEARSAL_SKIPPED_WARNING
    return REHEARSAL_TOO_SOON_WARNING


def collect_plan_warnings(
    result: PlanResult,
    goal_type: GoalType,
    weekly_summary: list[WeeklySummaryPayload],
    ambition: AmbitionLevel,
    athlete: AthleteState,
) -> str | None:
    """Merge planner and capacity warnings into one preview string.

    Args:
        result: The pipeline result carrying L1 soft-validation warnings.
        goal_type: The A-goal type.
        weekly_summary: Materialized per-week totals for the generated plan.
        ambition: The plan's ambition tier.
        athlete: The athlete state the plan was generated from.

    Returns:
        All applicable warnings joined into one string, or ``None``.
    """
    warnings = list(result.warnings)
    plan_weeks = len(result.phase_plan.weeks)
    running = athlete.sports.get(SportType.RUNNING)
    recent_run_hours = running.recent_weekly_hours if running else 0.0
    # rehearsal_skipped_warning reports what the generator actually did;
    # it takes precedence over the hours-based race_rehearsal_warning so
    # the athlete never gets two warnings about the same missed rehearsal.
    rehearsal = rehearsal_skipped_warning(
        result, goal_type, ambition
    ) or race_rehearsal_warning(goal_type, weekly_summary, plan_weeks)
    warnings.extend(
        capacity
        for capacity in (
            rehearsal,
            key_session_warning(
                goal_type, ambition, weekly_summary, plan_weeks, recent_run_hours
            ),
        )
        if capacity
    )
    return " ".join(warnings) if warnings else None


def run_generate(
    request: GenerateRequest,
    *,
    repos: PlannerRepos,
    progress: ProgressCallback,
    prior_training_weeks: int = 0,
    training_start_override: date | None = None,
    enforce_athlete_data: bool = True,
    enforce_offered: bool = True,
) -> GenerateSuccess | GenerateError:
    """Run the deterministic planner and assemble the wire payload.

    Args:
        request: The generate request (races, training start, ambition).
        progress: Progress callback receiving one event dict.
        repos: Repository container.
        prior_training_weeks: Weeks already trained (replanning); a race
            closer than its goal's minimum horizon adds its own deficit.
        training_start_override: Replanning start date (wins over the
            request's ``training_start``).
        enforce_athlete_data: Refuse to size a plan from the goal-type
            default profile (which assumes an already-high training
            volume) when a goal sport has neither history nor a stored
            self-assessment. Disabled on the replan path so a plan
            committed before onboarding existed still replans.
        enforce_offered: Refuse a plan its menu does not offer. Disabled
            where the plan already exists: a replan, an added race, the demo.

    Returns:
        ``GenerateSuccess`` with the full preview payload, or
        ``GenerateError`` when validation, the offered-plans guard,
        onboarding data, the short-horizon gate, the macro QP, or the
        maturity gate rejects the request.
    """
    parsed = _parse_request(request, training_start_override, enforce_offered)
    if isinstance(parsed, GenerateError):
        return parsed
    goals, training_start, ambition = parsed
    a_goal = _a_goal(goals)
    joined = _join_weeks(request, goals, training_start, prior_training_weeks)
    if isinstance(joined, GenerateError):
        return joined
    short_join = bool(joined) and not prior_training_weeks
    horizon_start = training_start - timedelta(weeks=prior_training_weeks)

    _progress_step(progress, "Reading training history", 1)
    resolved = _resolve_athlete(repos, a_goal.goal_type, enforce=enforce_athlete_data)
    if isinstance(resolved, GenerateError):
        return resolved
    effective_athlete = resolved.athlete
    derived = any(source == "history" for source in resolved.sources.values())

    _progress_step(progress, "Planning phases and sessions", 2)
    cap = request.weekly_hours_cap
    result = _generate_plan_result(
        goals,
        training_start,
        ambition,
        effective_athlete,
        prior_training_weeks,
        joined,
        cap,
    )
    if isinstance(result, GenerateError):
        return result
    verdict = maturity_gate(request, a_goal, horizon_start, joined, effective_athlete)
    if isinstance(verdict, GenerateError):
        return verdict

    _progress_step(progress, "Building workouts", 3)
    sessions = _materialize(request, repos, result, training_start)

    _progress_step(progress, "Computing projections", 4)
    phase_calendar = [week.phase.value for week in result.phase_plan.weeks]
    booked = [s for s in sessions if not s["optional"]]
    weekly_summary = build_weekly_summary(booked, phase_calendar, training_start)
    athlete_payload = _snapshot_athlete(effective_athlete, derived, resolved)
    coach_brief, warning = _assemble_brief(
        result,
        booked,
        weekly_summary,
        ambition,
        prior_training_weeks + joined,
        athlete_payload,
        effective_athlete,
        a_goal,
        short_join,
    )
    projections = build_projections(
        sessions=booked,
        athlete=effective_athlete,
        goal_type=a_goal.goal_type,
        plan_start=training_start,
        goal_date=a_goal.race_date,
    )
    macro_payload = serialize_macro_plan(
        result.phase_plan,
        booked,
        ambition=ambition,
        plan_start=training_start.isoformat(),
        coaching_brief=coach_brief,
    )

    return GenerateSuccess(
        sessions=sessions,
        weekly_summary=weekly_summary,
        phase_calendar=phase_calendar,
        coach_brief=coach_brief,
        warning=warning,
        projections=projections,
        macro_plan=macro_payload,
        athlete_state=athlete_payload,
        **verdict.wire_fields(),
        solver_version=SOLVER_VERSION,
        input_hash=build_input_hash(request, athlete_payload, SOLVER_VERSION),
    )


def run_commit(
    req: CommitRequest,
    *,
    repos: PlannerRepos,
    maybe_get_coach: Callable[[], Any],
    client: Any | None = None,
) -> dict[str, Any]:
    """Persist a previewed plan: macro row, plan row, and session rows.

    The cached ``generate_result`` from the preview is required — the
    committed plan is exactly what the athlete saw. Once it is persisted the
    plan is mirrored onto the athlete's intervals.icu calendar; a failed
    push is reported under ``push`` and never rolls the commit back.

    Args:
        req: Commit request (name + generate request + cached result).
        repos: Repository container.
        maybe_get_coach: Coach accessor for the plan-context refresh.
        client: intervals.icu client, or ``None`` when unconfigured.

    Returns:
        ``{"status": "ok", "plan_id": id, "push": {...}}`` or an error dict.
    """
    result = req.generate_result
    if not isinstance(result, GenerateSuccess):
        return {
            "status": "error",
            "message": "Commit requires the previewed generate result.",
        }

    outgoing_plan, macro = repos.plan.get_active(), dict(result.macro_plan)
    solve_payload = build_solve_result_payload(result)
    solve_payload["request"] = build_request_payload(req.generate_request)

    with repos.session.transaction():
        macro_id = repos.plan.create_macro(
            goal_type=macro["goal_type"],
            goal_date=macro["goal_date"],
            plan_start=macro["plan_start"],
            total_weeks=macro["total_weeks"],
            ambition_level=macro["ambition_level"],
            weeks_json=json.dumps(macro["weeks"]),
            coaching_brief=macro["coaching_brief"],
            revision=macro["revision"],
        )
        plan_id = repos.plan.create(
            name=req.name,
            goal_type=macro["goal_type"],
            goal_date=macro["goal_date"],
            plan_start=macro["plan_start"],
            solve_result_json=json.dumps(solve_payload),
            solver_version=SOLVER_VERSION,
            input_hash=result.input_hash,
            athlete_state_json=(
                json.dumps(result.athlete_state)
                if result.athlete_state is not None
                else None
            ),
            macro_plan_id=macro_id,
        )
        repos.plan.bulk_insert_sessions(
            plan_id, build_commit_session_rows(result.sessions)
        )

    update_coach_plan_context(result, req.name, maybe_get_coach=maybe_get_coach)
    sync_plan_races(repos, solve_payload["request"]["races"])
    logger.info("Committed planner plan %d (%s)", plan_id, req.name)
    push = push_plan_events(
        plan_id,
        result.sessions,
        solve_payload["request"]["races"],
        result.athlete_state,
        repos=repos,
        client=client,
        supersedes=outgoing_plan,
    )
    return {"status": "ok", "plan_id": plan_id, "push": push}
