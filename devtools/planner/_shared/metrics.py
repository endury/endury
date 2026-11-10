"""Pipeline-run + plan-metrics engine shared across the planner devtools.

Runs the session-menu pipeline for a ``Scenario`` (``run_full_pipeline``),
aggregates the result into ``PlanMetrics`` (phase/mode/sport distributions and
the Banister CTL/ATL/TSB trajectory), and formats the athlete's initial state.
The trajectory reads the QP's load: sessions are priced by their own activity
(``session_tss_per_hour``), race events by the week's TID bridge, and the seed
comes from ``initial_load_state``. This is the substrate both the report sweep (``report_sweep``) and the
plan-quality scorecard / dossier (``devtools.quality.planner``) build on; it
holds no rendering — charts, calendars, and HTML live in ``report_sweep``.
"""

from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from devtools.planner._shared.pipeline import (
    SolverOptions,
    default_profile_for,
    generate_plan,
    initial_load_state,
    seed_tss_per_hour,
)
from devtools.planner._shared.scenarios import Scenario
from endury.planner.activity.registry import (
    ensure_registry_loaded,
    get_activity,
)
from endury.planner.foundations.types import (
    ActivityAssignment,
    PhasePlan,
    PlacedSession,
    SportType,
)
from endury.planner.macro.macro_config import ATL_DECAY_WEEKLY, CTL_DECAY_WEEKLY
from endury.planner.macro.macro_planner import (
    race_day_carryover,
    race_day_session_weights,
)
from endury.planner.macro.macro_tables import GOAL_CTL_WEIGHTS
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour

# Shared constants
_ALL_SPORTS = [SportType.RUNNING, SportType.CYCLING, SportType.SWIMMING]


# Data types


@dataclass
class PipelineResult:
    """All outputs from the session-menu pipeline run."""

    scenario: Scenario
    plan: PhasePlan
    sessions: list[PlacedSession]
    assignments: list[ActivityAssignment]
    elapsed_s: float
    # slot_id → the TSS/h coefficient the QP sized that session with.
    session_tss_per_hour: dict[str, float]


@dataclass
class PlanMetrics:
    """Aggregate plan metrics for console and chart output."""

    n_weeks: int
    phase_distribution: dict[str, int]
    mode_distribution: dict[str, int]
    n_sessions: int
    sessions_by_sport: dict[str, int]
    total_hours: float
    hours_by_sport: dict[str, float]
    family_distribution: Counter
    # CTL/ATL trajectory (weekly-TSS units)
    w_ctl: np.ndarray  # weighted composite
    w_atl: np.ndarray
    w_tsb: np.ndarray
    s_ctl: np.ndarray  # sum across sports
    s_atl: np.ndarray
    s_tsb: np.ndarray
    # Per-sport absolute CTL/ATL (weekly-TSS units); keys are SportType values.
    a_ctl: dict[str, np.ndarray]
    a_atl: dict[str, np.ndarray]
    # State at the RACE-DAY instant (end of the race weekday), not at
    # race-week entry — see ``_race_day_state``.
    race_day_w_ctl: float
    race_day_w_tsb: float
    race_day_s_ctl: float
    race_day_s_tsb: float
    # Per-week data
    weekly_hours: dict[int, dict[str, float]]
    weekly_session_count: dict[int, int]


# Pipeline runner


def run_full_pipeline(
    scenario: Scenario,
    use_macro_solver: bool = True,
) -> PipelineResult:
    """Run the session-menu pipeline for a scenario.

    Args:
        scenario: Scenario to evaluate.
        use_macro_solver: If True (default), run the cvxpy QP macro
            planner. If False, use session menu target hours directly.

    Returns:
        PipelineResult with all intermediate outputs and timing.
    """
    ensure_registry_loaded()

    t0 = time.monotonic()
    result = generate_plan(
        scenario.to_request(solver=SolverOptions(use_macro_solver=use_macro_solver))
    )
    elapsed = time.monotonic() - t0

    # The instruments grade the plan the weeks book: an optional session
    # (R-SESS-037) is outside the load model, as the solver treats it.
    optional = {s.slot_id for s in result.sessions if s.optional_reason is not None}
    return PipelineResult(
        scenario=scenario,
        plan=result.phase_plan,
        sessions=[s for s in result.sessions if s.slot_id not in optional],
        assignments=[a for a in result.assignments if a.slot_id not in optional],
        elapsed_s=elapsed,
        session_tss_per_hour=result.session_tss_per_hour,
    )


# Metrics computation


def _compute_trajectory(
    result: PipelineResult,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    dict[str, np.ndarray],
    dict[str, np.ndarray],
]:
    """Forward-simulate Banister dynamics from placed sessions.

    Reads the load the QP plans with: the seed comes from
    ``initial_load_state`` over the opening week's sessions, each session is
    priced by its own activity at its placed hours
    (``session_tss_per_hour``), and race hours by the week's TID bridge.

    Returns:
        Tuple of (w_ctl, w_atl, w_tsb, s_ctl, s_atl, s_tsb, a_ctl, a_atl).
        The first six are 1D arrays of weekly values (weekly-TSS units). The
        last two are dicts keyed by sport value (``"running"`` etc.) mapping
        to 1D per-sport CTL/ATL arrays - the "absolute" (un-weighted) view.
        Divide by 7 for TSS/d.
    """
    plan = result.plan
    goal_type = plan.a_goal.goal_type
    weights = GOAL_CTL_WEIGHTS.get(goal_type, {})
    athlete = result.scenario.to_request().athlete or default_profile_for(
        goal_type, result.scenario.ambition
    )
    ctl_seed, atl_seed = initial_load_state(
        athlete,
        goal_type,
        [(s.activity_key, s.target_hours) for s in result.sessions if s.week == 0],
    )
    ctl = {s: ctl_seed.get(s, 0.0) for s in _ALL_SPORTS}
    atl = {s: atl_seed.get(s, 0.0) for s in _ALL_SPORTS}

    alpha, beta = CTL_DECAY_WEEKLY, ATL_DECAY_WEEKLY

    # Group sessions by week.
    sessions_by_week: dict[int, list[PlacedSession]] = defaultdict(list)
    for s in result.sessions:
        sessions_by_week[s.week].append(s)

    w_ctl_list: list[float] = []
    w_atl_list: list[float] = []
    s_ctl_list: list[float] = []
    s_atl_list: list[float] = []
    a_ctl_lists: dict[SportType, list[float]] = {s: [] for s in _ALL_SPORTS}
    a_atl_lists: dict[SportType, list[float]] = {s: [] for s in _ALL_SPORTS}

    for ws in plan.weeks:
        k = ws.week
        race_mult = tss_per_hour(
            get_tid_fractions(ws.phase, ws.mode, ws.effective_goal)
        )

        # Weekly TSS per sport: sessions by their activity, races by the bridge.
        sport_tss: dict[SportType, float] = defaultdict(float)
        for sess in sessions_by_week.get(k, []):
            sport_tss[sess.sport] += (
                session_tss_per_hour(sess.activity_key, sess.target_hours)
                * sess.target_hours
            )
        for re in plan.race_events:
            if re.week == k:
                for sp, h in re.duration_by_sport.items():
                    sport_tss[sp] += h * race_mult

        # Weighted composite.
        w_ctl_list.append(sum(weights.get(s, 0.0) * ctl[s] for s in _ALL_SPORTS))
        w_atl_list.append(sum(weights.get(s, 0.0) * atl[s] for s in _ALL_SPORTS))
        s_ctl_list.append(sum(ctl[s] for s in _ALL_SPORTS))
        s_atl_list.append(sum(atl[s] for s in _ALL_SPORTS))
        for sp in _ALL_SPORTS:
            a_ctl_lists[sp].append(ctl[sp])
            a_atl_lists[sp].append(atl[sp])

        # Banister update.
        for sp in _ALL_SPORTS:
            t_tss = sport_tss.get(sp, 0.0)
            ctl[sp] = alpha * ctl[sp] + (1 - alpha) * t_tss
            atl[sp] = beta * atl[sp] + (1 - beta) * t_tss

    wa = np.asarray(w_ctl_list)
    wb = np.asarray(w_atl_list)
    sa = np.asarray(s_ctl_list)
    sb = np.asarray(s_atl_list)
    a_ctl = {sp.value: np.asarray(a_ctl_lists[sp]) for sp in _ALL_SPORTS}
    a_atl = {sp.value: np.asarray(a_atl_lists[sp]) for sp in _ALL_SPORTS}
    return wa, wb, wa - wb, sa, sb, sa - sb, a_ctl, a_atl


def _race_day_state(
    result: PipelineResult,
    a_ctl: dict[str, np.ndarray],
    a_atl: dict[str, np.ndarray],
) -> tuple[float, float, float, float]:
    """Step the race-week entry state forward to the race-day instant.

    ``_compute_trajectory`` produces one state per week *entry*, but the A
    race falls up to six days into its week, after the rest days the taper
    front-loads. Reading the week-entry row understates freshness by the
    whole taper-week ATL collapse (+27.6 entering the week vs +35.7 on race
    day for ``ironman_moderate``). This applies the planner's own day-stepped
    expression (``race_day_carryover`` / ``race_day_session_weights``) so the
    scorecard grades the instant the QP optimises. Each race-week session is
    priced by its own activity (``session_tss_per_hour``). Raceless maintenance plans
    have no race event and fall back to the end of the final week.

    Args:
        result: Pipeline output.
        a_ctl: Per-sport CTL trajectory, keyed by sport value.
        a_atl: Per-sport ATL trajectory, keyed by sport value.

    Returns:
        ``(weighted_ctl, weighted_tsb, summed_ctl, summed_tsb)`` in
        weekly-TSS units.
    """
    plan = result.plan
    goal_type = plan.a_goal.goal_type
    weights = GOAL_CTL_WEIGHTS.get(goal_type, {})
    n_weeks = len(plan.weeks)
    races = sorted(plan.race_events, key=lambda re: re.week)
    event = races[-1] if races else None
    week_idx = min(event.week if event else n_weeks - 1, n_weeks - 1)
    race_dow = event.day_of_week if event else 6

    ctl_carry, atl_carry = race_day_carryover(race_dow)
    ctl = {sp: ctl_carry * float(a_ctl[sp.value][week_idx]) for sp in _ALL_SPORTS}
    atl = {sp: atl_carry * float(a_atl[sp.value][week_idx]) for sp in _ALL_SPORTS}

    for sess in result.sessions:
        if sess.week != week_idx or sess.sport not in ctl:
            continue
        w_ctl, w_atl = race_day_session_weights(sess.day_of_week, race_dow)
        tss = (
            session_tss_per_hour(sess.activity_key, sess.target_hours)
            * sess.target_hours
        )
        ctl[sess.sport] += w_ctl * tss
        atl[sess.sport] += w_atl * tss

    summed_ctl = sum(ctl.values())
    weighted_ctl = sum(weights.get(sp, 0.0) * ctl[sp] for sp in _ALL_SPORTS)
    weighted_atl = sum(weights.get(sp, 0.0) * atl[sp] for sp in _ALL_SPORTS)
    return (
        weighted_ctl,
        weighted_ctl - weighted_atl,
        summed_ctl,
        summed_ctl - sum(atl.values()),
    )


def _compute_metrics(result: PipelineResult) -> PlanMetrics:
    """Compute aggregate metrics from pipeline output."""
    plan = result.plan
    sessions = result.sessions

    phase_dist: Counter = Counter(ws.phase.value for ws in plan.weeks)
    mode_dist: Counter = Counter(ws.mode.value for ws in plan.weeks)

    ensure_registry_loaded()
    by_sport: Counter = Counter(s.sport.value for s in sessions)
    hours_by_sport: dict[str, float] = defaultdict(float)
    family_dist: Counter = Counter()
    weekly_hours: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    weekly_count: dict[int, int] = defaultdict(int)

    for s in sessions:
        act = get_activity(s.activity_key)
        hours_by_sport[s.sport.value] += s.target_hours
        family_dist[act.family.value] += 1
        weekly_hours[s.week][s.sport.value] += s.target_hours
        weekly_count[s.week] += 1

    w_ctl, w_atl, w_tsb, s_ctl, s_atl, s_tsb, a_ctl, a_atl = _compute_trajectory(result)

    # Race-day metrics — the instant the QP's freshness term targets.
    rd_w_ctl, rd_w_tsb, rd_s_ctl, rd_s_tsb = _race_day_state(result, a_ctl, a_atl)

    return PlanMetrics(
        n_weeks=len(plan.weeks),
        phase_distribution=dict(phase_dist),
        mode_distribution=dict(mode_dist),
        n_sessions=len(sessions),
        sessions_by_sport=dict(by_sport),
        total_hours=sum(s.target_hours for s in sessions),
        hours_by_sport=dict(hours_by_sport),
        family_distribution=family_dist,
        w_ctl=w_ctl,
        w_atl=w_atl,
        w_tsb=w_tsb,
        s_ctl=s_ctl,
        s_atl=s_atl,
        s_tsb=s_tsb,
        a_ctl=a_ctl,
        a_atl=a_atl,
        race_day_w_ctl=rd_w_ctl,
        race_day_w_tsb=rd_w_tsb,
        race_day_s_ctl=rd_s_ctl,
        race_day_s_tsb=rd_s_tsb,
        weekly_hours={w: dict(h) for w, h in weekly_hours.items()},
        weekly_session_count=dict(weekly_count),
    )


# Athlete state formatter


def format_athlete_state(scenario: Scenario) -> str:
    """Return a formatted multi-line string describing the athlete's initial state.

    Covers race goals, training start, ambition level, current weekly
    training volume, initial CTL/ATL fitness estimates, longest recent
    sessions, and day-scheduling constraints.

    Args:
        scenario: The evaluation scenario.

    Returns:
        ASCII-formatted summary ready for printing.
    """
    lines: list[str] = []
    sep = "─" * 78

    lines += [sep, "  ATHLETE INITIAL STATE", sep]

    # Race goals
    lines.append("\n  Race goals:")
    for goal in scenario.goals:
        weeks_out = (goal.race_date - scenario.training_start).days // 7
        lines.append(
            f"    [{goal.priority}]  {goal.goal_type.value:<30}  "
            f"race: {goal.race_date}  ({weeks_out} wks from start)"
        )

    lines += [
        "",
        f"  Training start : {scenario.training_start}",
        f"  Ambition level : {scenario.ambition.value}",
    ]

    # Recent weekly training hours
    recent = scenario.recent_hours
    if recent:
        total_h = sum(recent.values())
        lines.append("\n  Current weekly training volume:")
        for sport in _ALL_SPORTS:
            hrs = recent.get(sport, 0.0)
            if hrs > 0.0:
                bar = "▪" * int(hrs)
                lines.append(f"    {sport.value:<10} {hrs:5.1f} h/wk  {bar}")
        lines.append(f"    {'Total':<10} {total_h:5.1f} h/wk")
    else:
        lines.append("\n  Current weekly training volume: (pipeline defaults apply)")

    # Initial CTL / ATL
    ctl_0 = scenario.ctl_0 or {}
    atl_0 = scenario.atl_0 or {}
    recent_h = scenario.recent_hours or {}

    lines.append("\n  Initial fitness (CTL / ATL in weekly-TSS units, ÷ 7 = TSS/day):")
    tph = seed_tss_per_hour(scenario.goals[0].goal_type, ())
    shown_any = False
    for sport in _ALL_SPORTS:
        implied = recent_h.get(sport, 0.0) * tph
        has_explicit = sport in ctl_0 or sport in atl_0
        has_recent = sport in recent_h
        if not has_explicit and not has_recent:
            continue
        shown_any = True

        ctl_val = ctl_0.get(sport, implied)
        atl_val = atl_0.get(sport, implied)
        tsb = ctl_val - atl_val
        src = (
            "explicit"
            if has_explicit
            else f"implied ({recent_h.get(sport, 0.0):.1f}h x {tph:.0f})"
        )
        fresh = "fresh" if tsb >= 0 else "fatigued"
        lines.append(
            f"    {sport.value:<10}  CTL {ctl_val:7.1f}  "
            f"ATL {atl_val:7.1f}  TSB {tsb:+.1f} ({fresh})   [{src}]"
        )
    if not shown_any:
        lines.append("    (none - pipeline will initialise from defaults)")

    # Longest recent sessions
    longest = scenario.longest_recent
    if longest:
        lines.append("\n  Longest recent sessions (progressive long-session baseline):")
        for (sport, family), mins in sorted(
            longest.items(), key=lambda x: (x[0][0].value, x[0][1].value)
        ):
            h, m = divmod(mins, 60)
            lines.append(f"    {sport.value:<10} {family.value:<12}  {h}h {m:02d}min")
    else:
        lines.append("\n  Longest recent sessions: (pipeline defaults apply)")

    lines.append(sep)
    return "\n".join(lines)
