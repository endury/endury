"""Session-menu macro planner — tracking-first QP.

Treats the session menu as the coaching prescription (single source of
truth) and reduces the QP to a feasibility + continuity + race-week safety
adjuster.  One variable per session, bounded by ``[min_hours, max_hours]``.

The objective **minimises**:

* **Tracking** — squared deviation of every flexible session from its
  menu-prescribed ``target_hours`` (primary term).
* **Freshness** — squared miss of the TSB band at the race-day instant.
* **Volume smoothing** — squared per-sport week-to-week hour changes
  across LOAD→LOAD transitions (including gaps bridged by RECOVERY).
* **Weekly-hours cap** — a linear hinge on every training week's hours
  above the athlete-authored ``weekly_hours_cap`` (when one is set).

Minus a small CTL tiebreaker ``ε · Σ w_s · CTL_s[k_race]`` so that among
equivalent tracking solutions the solver prefers the one with higher
race-week fitness.

Banister dynamics, session bounds, and capacity ceiling remain as hard
constraints. The Banister impulse prices each session's hours with its own
activity (``session_pricing``) and race hours with the week's TID bridge.
A loose per-sport ramp (safety net) guards against extreme spikes that the
quadratic smoothing alone might not prevent.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np

from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    CapacityProjection,
    PhasePlan,
    PlacedSession,
    SportType,
    WeekMode,
)
from endury.planner.macro.macro_config import (
    ATL_DECAY_DAILY,
    ATL_DECAY_WEEKLY,
    CTL_DECAY_DAILY,
    CTL_DECAY_WEEKLY,
    DAILY_TSB_DIVISOR,
    EPS_CTL,
    FRESHNESS_CEILING_MARGIN,
    LAMBDA_CAP,
    LAMBDA_FRESH,
    LAMBDA_FRESH_CEILING,
    LAMBDA_SMOOTH,
    SOLVER_NAME,
    SOLVER_VERBOSE,
)
from endury.planner.macro.macro_tables import (
    GOAL_CTL_WEIGHTS,
    GOAL_FRESHNESS_TAPER_WEEKS,
    RACE_DAY_FRESHNESS_RATIO,
    TAPER_FRESHNESS_SCALE,
)
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.macro.tss_estimator import tss_per_hour

SPORTS: list[SportType] = [
    SportType.RUNNING,
    SportType.CYCLING,
    SportType.SWIMMING,
]
SPORT_IDX: dict[SportType, int] = {s: i for i, s in enumerate(SPORTS)}
_S = len(SPORTS)

SPORT_RAMP_LIMIT: float = 1.50
"""Loose safety-net per-sport ramp for LOAD→LOAD transitions.

The quadratic smoothing penalty (``LAMBDA_SMOOTH``) does the real shaping;
this hard constraint is a backstop that should rarely bind.  The +1.0 floor
prevents blocking small volumes (e.g. 0.5 h → 1.0 h swim).

Reference: Gabbett, Br J Sports Med (2016) — acute-to-chronic workload
ratio should stay within safe bounds per sport.
"""


# ---------------------------------------------------------------------------
# Input / output contracts
# ---------------------------------------------------------------------------


@dataclass
class MacroRequest:
    """Input to the session-menu macro planner.

    Args:
        phase_plan: L1 output.
        sessions: All prescribed sessions with min/max bounds.
        ctl_0: Initial CTL per sport (weekly-TSS).
        atl_0: Initial ATL per sport (weekly-TSS).
        capacity: Per-week hours ceiling.
        weekly_hours_cap: Athlete-authored weekly hours target. ``None`` →
            no cap. Soft: a linear hinge penalty, not a ceiling.
    """

    phase_plan: PhasePlan
    sessions: list[PlacedSession]
    ctl_0: dict[SportType, float]
    atl_0: dict[SportType, float]
    capacity: CapacityProjection
    weekly_hours_cap: float | None = None


@dataclass(frozen=True, slots=True)
class MacroBudget:
    """Output: QP-optimised hours per session.

    Args:
        session_hours: slot_id -> optimised hours.
        sessions: Updated PlacedSession list with target_hours set
            to the QP solution.
        session_tss_per_hour: slot_id -> the TSS/h coefficient the session
            was sized with (its activity's price at the menu target hours).
    """

    session_hours: dict[str, float]
    sessions: list[PlacedSession]
    session_tss_per_hour: dict[str, float]


class QPInfeasibleError(ValueError):
    """Raised when the session-menu macro QP has no feasible solution.

    A ``ValueError`` subclass, so every existing ``except ValueError`` call
    site keeps working unchanged. Callers that want to swap the raw solver
    status for a friendly, user-facing message (the phase planner's own
    request-validation errors are already human-readable and should pass
    through untouched) can catch this type specifically.
    """


# ---------------------------------------------------------------------------
# Duration rounding
# ---------------------------------------------------------------------------

_ROUND_THRESHOLD_HOURS: float = 80.0 / 60.0  # 1 h 20 min
_ROUND_STEP_HOURS: float = 5.0 / 60.0  # 5 min

# Families whose durations are always rounded to 5-min steps regardless of
# length — easy/recovery sessions benefit from clean, memorable numbers.
_ROUND_ALWAYS_FAMILIES: frozenset[ActivityFamily] = frozenset(
    {ActivityFamily.EASY, ActivityFamily.RECOVERY}
)


def _should_round(hours: float, activity_key: ActivityKey) -> bool:
    """Return True if the session duration should be rounded to 5 minutes."""
    if hours >= _ROUND_THRESHOLD_HOURS:
        return True
    from endury.planner.activity.registry import get_activity

    try:
        return get_activity(activity_key).family in _ROUND_ALWAYS_FAMILIES
    except KeyError:
        return False


def _round_hours(hours: float) -> float:
    """Round a duration to the nearest 5 minutes."""
    return round(hours / _ROUND_STEP_HOURS) * _ROUND_STEP_HOURS


# ---------------------------------------------------------------------------
# Race-day PMC instant
#
# The Banister state in this module is weekly: ``ctl_v[k]`` is the state
# *entering* week k.  A race, though, falls on one weekday of its week, so the
# athlete meets the start line up to six days of taper decay later.  These two
# helpers step the weekly state forward to the end of race day at day
# resolution, and stay affine in the state and in the session hours — the QP
# needs the expression to remain convex.
#
# Both are shared with the devtools plan metrics (``devtools/planner/_shared/
# metrics.py``) so the planner's internal target and the scorecard grade the
# same instant.
# ---------------------------------------------------------------------------


def race_day_carryover(race_day_of_week: int) -> tuple[float, float]:
    """Return the (CTL, ATL) decay from race-week entry to end of race day.

    Args:
        race_day_of_week: Race weekday, 0 = Monday.

    Returns:
        ``(ctl_factor, atl_factor)`` to apply to the week-entry state.
    """
    days = race_day_of_week + 1
    return CTL_DECAY_DAILY**days, ATL_DECAY_DAILY**days


def race_day_session_weights(
    day_of_week: int | None, race_day_of_week: int
) -> tuple[float, float]:
    """Return the (CTL, ATL) weights on a race-week session's TSS at race day.

    A session's load enters the state on its own day and then decays until
    race day, so its weight is ``7 · (1 - decay) · decay ** gap`` — the
    daily impulse response written in the weekly-TSS units the QP state uses.
    Assuming a flat weekly spread instead (which is what the weekly recursion
    does) is wrong here: race weeks are front-loaded, with the rest days
    immediately before the race, and that shape is exactly why ATL collapses.

    Two day-assigner behaviours are folded in so the estimate matches the
    calendar the athlete will actually get: a session landing *on* the race
    day is evicted to an earlier day, and one dated *after* it is dropped in
    the A-race week (and irrelevant to a B/C race's own freshness either way).

    Args:
        day_of_week: Menu-authored session weekday (None → Monday, matching
            the day assigner's fallback).
        race_day_of_week: Race weekday, 0 = Monday.

    Returns:
        ``(ctl_weight, atl_weight)`` to apply to the session's TSS; ``(0, 0)``
        when the session does not land before the race.
    """
    day = 0 if day_of_week is None else day_of_week
    if day > race_day_of_week:
        return 0.0, 0.0
    if day == race_day_of_week:
        day = race_day_of_week - 1  # race-day eviction moves it earlier
    if day < 0:
        return 0.0, 0.0
    gap = race_day_of_week - day
    return (
        DAILY_TSB_DIVISOR * (1.0 - CTL_DECAY_DAILY) * CTL_DECAY_DAILY**gap,
        DAILY_TSB_DIVISOR * (1.0 - ATL_DECAY_DAILY) * ATL_DECAY_DAILY**gap,
    )


# ---------------------------------------------------------------------------
# QP construction and solving
# ---------------------------------------------------------------------------


def _quiet_shape_probe(
    fn: Callable[[MacroRequest], MacroBudget],
) -> Callable[[MacroRequest], MacroBudget]:
    """Silence the numpy FP warning cvxpy's ``cp.sum`` shape probe leaks.

    ``cvxpy.atoms.affine.sum.Sum.shape_from_args`` derives the output shape of
    a sum by summing an *uninitialised* ``np.empty(shape)`` and reading
    ``.shape`` off the result. numpy's small-allocation cache can hand that
    probe a freed buffer holding both +inf and -inf (cvxpy's own bounds arrays
    are full of them), making ``inf + -inf`` report "invalid value encountered
    in reduce" for a value nobody reads. Every other cvxpy shape probe passes
    ``dtype=np.dtype([])`` to avoid exactly this; ``Sum`` is the one that does
    not.

    Args:
        fn: The QP entry point to wrap.

    Returns:
        ``fn`` with numpy's ``invalid`` FP reporting suppressed for the call.
    """

    @functools.wraps(fn)
    def _wrapped(request: MacroRequest) -> MacroBudget:
        with np.errstate(invalid="ignore"):
            return fn(request)

    return _wrapped


@_quiet_shape_probe
def plan_macro(request: MacroRequest) -> MacroBudget:
    """Solve the session-menu QP.

    Args:
        request: Macro planner input.

    Returns:
        MacroBudget with QP-optimised session hours.

    Raises:
        QPInfeasibleError: If the QP is infeasible.
    """
    # The solver loads on first use, not with the app: in the browser it costs
    # seconds and tens of megabytes, and a stored plan never needs it.
    import cvxpy as cp

    # An optional session (R-SESS-037) is outside the load model: the plan
    # is solved as if the athlete skips it, and it rides along at its card.
    optional = [s for s in request.sessions if s.optional_reason is not None]
    if optional:
        budget = plan_macro(
            replace(
                request,
                sessions=[s for s in request.sessions if s.optional_reason is None],
            )
        )
        return MacroBudget(
            session_hours=budget.session_hours
            | {s.slot_id: s.target_hours for s in optional},
            sessions=[*budget.sessions, *optional],
            session_tss_per_hour=budget.session_tss_per_hour
            | {
                s.slot_id: session_tss_per_hour(s.activity_key, s.target_hours)
                for s in optional
            },
        )
    weeks = request.phase_plan.weeks
    n = len(weeks)

    # Group sessions by week.
    sessions_by_week: dict[int, list[tuple[int, PlacedSession]]] = {}
    for idx, sess in enumerate(request.sessions):
        sessions_by_week.setdefault(sess.week, []).append((idx, sess))

    # Race hours (fixed, from race events).
    r_k = np.zeros((n, _S))
    for re in request.phase_plan.race_events:
        if re.week < n:
            for sport, hours in re.duration_by_sport.items():
                s_idx = SPORT_IDX.get(sport)
                if s_idx is not None:
                    r_k[re.week, s_idx] += hours

    # Race-hour TSS multiplier per week (the TID bridge): a race is an
    # event, not an activity, so its fixed hours are priced by the week.
    tss_mult = np.zeros((n, _S))
    for k, ws in enumerate(weeks):
        fracs = get_tid_fractions(ws.phase, ws.mode, ws.effective_goal)
        m = tss_per_hour(fracs)
        tss_mult[k, :] = m

    # Per-session TSS/h, frozen at the menu target (the linearisation point).
    mult = [
        session_tss_per_hour(s.activity_key, s.target_hours) for s in request.sessions
    ]

    # ── Decision variables ──────────────────────────────────────────────
    # One variable per session.
    n_sess = len(request.sessions)
    s_var = cp.Variable(n_sess, nonneg=True, name="s")

    # Banister state.
    ctl_v = cp.Variable((n + 1, _S), nonneg=True, name="CTL")
    atl_v = cp.Variable((n + 1, _S), nonneg=True, name="ATL")

    constraints: list[cp.Constraint] = []

    # Session bounds.
    for idx, sess in enumerate(request.sessions):
        lo = sess.min_hours if sess.min_hours > 0 else sess.target_hours
        hi = sess.max_hours if sess.max_hours > 0 else sess.target_hours
        constraints.append(s_var[idx] >= lo)
        constraints.append(s_var[idx] <= hi)

    # ── Per-week aggregates (affine expressions) ────────────────────────
    # c_by_sport[k, s_idx] = sum of session vars for that week+sport
    # c_total[k] = total controllable hours for week k
    # tss_by_sport[k, s_idx] = Σ mult_i·s_i + race hours · bridge
    c_by_sport_expr: dict[tuple[int, int], cp.Expression | float] = {}
    tss_by_sport_expr: dict[tuple[int, int], cp.Expression | float] = {}
    c_total_expr: dict[int, cp.Expression | float] = {}

    for k in range(n):
        sport_sums: dict[int, list[cp.Expression]] = {si: [] for si in range(_S)}
        sport_tss: dict[int, list[cp.Expression]] = {si: [] for si in range(_S)}
        week_sessions = sessions_by_week.get(k, [])
        for idx, sess in week_sessions:
            s_idx = SPORT_IDX.get(sess.sport)
            if s_idx is not None:
                # A session booked at its card over the week's ceiling
                # (R-SESS-037) counts toward load, not the ceiling or ramp.
                if not sess.over_ceiling:
                    sport_sums[s_idx].append(s_var[idx])
                sport_tss[s_idx].append(mult[idx] * s_var[idx])

        for si in range(_S):
            race_tss = r_k[k, si] * tss_mult[k, si]
            c_by_sport_expr[(k, si)] = (
                cp.sum(cp.hstack(sport_sums[si])) if sport_sums[si] else 0.0
            )
            tss_by_sport_expr[(k, si)] = (
                cp.sum(cp.hstack(sport_tss[si])) + race_tss
                if sport_tss[si]
                else race_tss
            )

        all_vars = []
        for idx, sess in week_sessions:
            if not sess.over_ceiling:
                all_vars.append(s_var[idx])
        c_total_expr[k] = cp.sum(cp.hstack(all_vars)) if all_vars else 0.0

    # ── Initial conditions ──────────────────────────────────────────────
    alpha = CTL_DECAY_WEEKLY
    beta = ATL_DECAY_WEEKLY

    for s_idx, sport in enumerate(SPORTS):
        constraints.append(ctl_v[0, s_idx] == request.ctl_0.get(sport, 0.0))
        constraints.append(atl_v[0, s_idx] == request.atl_0.get(sport, 0.0))

    # ── Banister dynamics ───────────────────────────────────────────────
    for k in range(n):
        for s_idx in range(_S):
            t_tss = tss_by_sport_expr[(k, s_idx)]
            constraints.append(
                ctl_v[k + 1, s_idx] == alpha * ctl_v[k, s_idx] + (1 - alpha) * t_tss
            )
            constraints.append(
                atl_v[k + 1, s_idx] == beta * atl_v[k, s_idx] + (1 - beta) * t_tss
            )

    # ── Capacity ceiling ────────────────────────────────────────────────
    for k in range(n):
        ceil = request.capacity.weekly_ceiling.get(k, 0.0)
        ct = c_total_expr[k]
        if not isinstance(ct, (int, float)):
            constraints.append(ct <= ceil)

    # ── Inter-week constraints ─────────────────────────────────────────
    modes = [ws.mode for ws in weeks]

    # ── Soft weekly-hours cap (athlete-authored) ───────────────────────
    # A PENALTY, not a ceiling: where the menu's own session floors sit
    # above the cap, the week pays for the overshoot instead of the QP going
    # infeasible. Applies to every week with session variables, including
    # taper and race weeks — exempting taper would let the QP park there
    # the volume it removed from load weeks.
    cap_overshoot: list[cp.Variable] = []
    cap = request.weekly_hours_cap
    if cap is not None:
        for k in range(n):
            ct = c_total_expr[k]
            if isinstance(ct, (int, float)):
                continue
            over = cp.Variable(nonneg=True, name=f"cap_over_{k}")
            constraints.append(over >= ct - cap)
            cap_overshoot.append(over)

    # Loose safety-net ramp: hard upper bound that should rarely bind.
    # The quadratic smoothing penalty does the real shaping.
    for k in range(n - 1):
        if modes[k] != WeekMode.LOAD or modes[k + 1] != WeekMode.LOAD:
            continue
        for s_idx in range(_S):
            c_k = c_by_sport_expr[(k, s_idx)]
            c_k1 = c_by_sport_expr[(k + 1, s_idx)]
            if not isinstance(c_k, (int, float)) and not isinstance(c_k1, (int, float)):
                constraints.append(c_k1 <= SPORT_RAMP_LIMIT * c_k + 1.0)

    # Quadratic smoothing: penalise week-to-week per-sport volume jumps.
    # Applies between consecutive LOAD weeks in the SAME phase AND across
    # recovery gaps within the same phase.  Phase boundaries (e.g. BUILD→PEAK)
    # are deliberate coaching ramps — smoothing must not soften them.
    smooth_terms: list[cp.Expression] = []
    phases = [ws.phase for ws in weeks]
    load_week_indices = [k for k in range(n) if modes[k] == WeekMode.LOAD]
    for pair_idx in range(len(load_week_indices) - 1):
        k_prev = load_week_indices[pair_idx]
        k_next = load_week_indices[pair_idx + 1]
        if phases[k_prev] != phases[k_next]:
            continue  # skip cross-phase pairs (deliberate ramp, not noise)
        for s_idx in range(_S):
            c_k = c_by_sport_expr[(k_prev, s_idx)]
            c_k1 = c_by_sport_expr[(k_next, s_idx)]
            if not isinstance(c_k, (int, float)) and not isinstance(c_k1, (int, float)):
                smooth_terms.append(c_k1 - c_k)

    # ── Tracking ───────────────────────────────────────────────────────
    # Primary objective term: pull every flexible session toward its
    # menu-prescribed ``target_hours``.  Fixed-duration sessions (band ≈ 0)
    # are pinned by their bounds and contribute nothing to tracking.
    track_terms: list[cp.Expression] = []
    for idx, sess in enumerate(request.sessions):
        lo = sess.min_hours if sess.min_hours > 0 else sess.target_hours
        hi = sess.max_hours if sess.max_hours > 0 else sess.target_hours
        if hi - lo < 1e-9:
            continue
        track_terms.append(s_var[idx] - sess.target_hours)

    # ── Freshness (TSB target at race DAY — quadratic penalty) ─────────
    freshness_penalties: list[cp.Variable] = []
    over_freshness_penalties: list[cp.Variable] = []
    for race_event in request.phase_plan.race_events:
        k = race_event.week
        if k >= n:  # pragma: no cover
            continue  # pragma: no cover
        goal = race_event.goal
        weights = GOAL_CTL_WEIGHTS.get(goal.goal_type, {})
        target = RACE_DAY_FRESHNESS_RATIO.get(goal.priority.upper(), 0.0)

        # Scale by taper length.
        taper_wks = sum(
            1
            for j in range(max(0, k - 4), k + 1)
            if j < n and modes[j] in (WeekMode.TAPER, WeekMode.RACE)
        )
        taper_wks = GOAL_FRESHNESS_TAPER_WEEKS.get(goal.goal_type, taper_wks)
        scale = TAPER_FRESHNESS_SCALE.get(min(taper_wks, 3), 1.0)
        target *= scale

        # Freshness as a soft BAND penalty on TSB / CTL: penalise both the
        # deficit below the floor and the surplus above the ceiling, rather
        # than hard-constraining (avoids infeasibility when session bounds +
        # taper decay tightly constrain the trajectory). The band is a
        # ratio, target <= TSB / CTL <= target + FRESHNESS_CEILING_MARGIN,
        # written as TSB - target·CTL >= 0 so it stays affine; the misses
        # are measured in daily TSB points, the unit LAMBDA_FRESH is tuned in.
        #
        # The expression is the COMBINED (unweighted-sum) per-sport PMC in
        # daily units — the same Performance Management Chart the athlete's
        # trends view plots, and the TSB zones in
        # docs/research/endurance_methodology.md are stated for. A
        # sport-weighted composite has no external referent: for a
        # multi-discipline athlete it reads lower than the athlete's real
        # form, so it is not used here.
        #
        # It is evaluated at the RACE-DAY instant, not at race-week entry:
        # the athlete races up to six days into the week, after the rest days
        # the taper front-loads, and ATL collapses over exactly that span.
        # The race event's own load is deliberately excluded: it is the
        # outcome, not the preparation.
        if target > 0:
            ctl_carry, atl_carry = race_day_carryover(race_event.day_of_week)
            race_sports = [s for s in SPORTS if weights.get(s, 0.0) > 0]
            ctl_terms: list[cp.Expression] = [
                ctl_carry * ctl_v[k, SPORT_IDX[sport]] for sport in race_sports
            ]
            atl_terms: list[cp.Expression] = [
                atl_carry * atl_v[k, SPORT_IDX[sport]] for sport in race_sports
            ]
            for idx, sess in sessions_by_week.get(k, []):
                if sess.sport not in race_sports:
                    continue
                w_ctl, w_atl = race_day_session_weights(
                    sess.day_of_week, race_event.day_of_week
                )
                if w_ctl or w_atl:
                    ctl_terms.append(w_ctl * mult[idx] * s_var[idx])
                    atl_terms.append(w_atl * mult[idx] * s_var[idx])
            if ctl_terms:
                ctl_rd = sum(ctl_terms)
                tsb_rd = ctl_rd - sum(atl_terms)
                ceiling = target + FRESHNESS_CEILING_MARGIN
                deficit = cp.Variable(nonneg=True, name=f"fresh_def_{k}")
                surplus = cp.Variable(nonneg=True, name=f"fresh_sur_{k}")
                constraints.append(
                    deficit >= (target * ctl_rd - tsb_rd) / DAILY_TSB_DIVISOR
                )
                constraints.append(
                    surplus >= (tsb_rd - ceiling * ctl_rd) / DAILY_TSB_DIVISOR
                )
                freshness_penalties.append(deficit)
                over_freshness_penalties.append(surplus)

    # ── Objective: tracking-first with ε·CTL tiebreaker ────────────────
    # Menu is the coaching prescription; the QP adjusts for continuity
    # and race-week freshness and uses race-week CTL only to break ties.
    ctl_tiebreaker_terms: list[cp.Expression] = []
    for race_event in request.phase_plan.race_events:
        k = race_event.week
        if k >= n:  # pragma: no cover
            continue  # pragma: no cover
        weights = GOAL_CTL_WEIGHTS.get(race_event.goal.goal_type, {})
        for sport in SPORTS:
            w = weights.get(sport, 0.0)
            if w > 0:
                ctl_tiebreaker_terms.append(w * ctl_v[k, SPORT_IDX[sport]])

    cost_track = cp.sum_squares(cp.hstack(track_terms)) if track_terms else 0.0
    cost_smooth = (
        LAMBDA_SMOOTH * cp.sum_squares(cp.hstack(smooth_terms)) if smooth_terms else 0.0
    )
    cost_fresh = (
        LAMBDA_FRESH * cp.sum_squares(cp.hstack(freshness_penalties))
        if freshness_penalties
        else 0.0
    )
    cost_over_fresh = (
        LAMBDA_FRESH_CEILING * cp.sum_squares(cp.hstack(over_freshness_penalties))
        if over_freshness_penalties
        else 0.0
    )
    tiebreaker = EPS_CTL * sum(ctl_tiebreaker_terms) if ctl_tiebreaker_terms else 0.0

    cost = cost_track + cost_smooth + cost_fresh + cost_over_fresh - tiebreaker
    if cap_overshoot:
        # Appended only when a cap is set, so a plan with no cap compiles
        # to exactly the uncapped problem and solves to the same bits.
        cost = cost + LAMBDA_CAP * cp.sum(cp.hstack(cap_overshoot))

    objective = cp.Minimize(cost)
    problem = cp.Problem(objective, constraints)
    problem.solve(solver=SOLVER_NAME, verbose=SOLVER_VERBOSE)

    if problem.status not in ("optimal", "optimal_inaccurate"):
        raise QPInfeasibleError(
            f"Session-menu QP infeasible (status={problem.status!r}). "
            "Check capacity ceilings, session bounds, and freshness targets."
        )

    # ── Extract solution ────────────────────────────────────────────────
    assert s_var.value is not None  # infeasible statuses raised above
    s_val = np.clip(s_var.value, 0.0, None)

    session_hours: dict[str, float] = {}
    updated_sessions: list[PlacedSession] = []

    for idx, sess in enumerate(request.sessions):
        lo = sess.min_hours if sess.min_hours > 0 else sess.target_hours
        hi = sess.max_hours if sess.max_hours > 0 else sess.target_hours
        raw = float(s_val[idx])
        hours = (
            max(lo, min(hi, _round_hours(raw)))
            if _should_round(raw, sess.activity_key)
            else raw
        )
        session_hours[sess.slot_id] = hours
        updated_sessions.append(replace(sess, target_hours=hours))

    return MacroBudget(
        session_hours=session_hours,
        sessions=updated_sessions,
        session_tss_per_hour={
            sess.slot_id: mult[idx] for idx, sess in enumerate(request.sessions)
        },
    )


def skip_macro(sessions: list[PlacedSession]) -> MacroBudget:
    """Bypass the QP solver and use session menu target hours directly.

    Applies the same duration rounding as the QP path (5-minute steps for
    long sessions and easy/recovery families) so that output is consistent.

    Args:
        sessions: Prescribed sessions from the session generator.

    Returns:
        MacroBudget with target_hours used as-is (after rounding).
    """
    session_hours: dict[str, float] = {}
    updated_sessions: list[PlacedSession] = []
    priced: dict[str, float] = {}

    for sess in sessions:
        lo = sess.min_hours if sess.min_hours > 0 else sess.target_hours
        hi = sess.max_hours if sess.max_hours > 0 else sess.target_hours
        raw = sess.target_hours
        hours = (
            max(lo, min(hi, _round_hours(raw)))
            if _should_round(raw, sess.activity_key)
            else raw
        )
        session_hours[sess.slot_id] = hours
        updated_sessions.append(replace(sess, target_hours=hours))
        priced[sess.slot_id] = session_tss_per_hour(
            sess.activity_key, sess.target_hours
        )

    return MacroBudget(
        session_hours=session_hours,
        sessions=updated_sessions,
        session_tss_per_hour=priced,
    )


__all__ = [
    "SPORT_RAMP_LIMIT",
    "MacroBudget",
    "MacroRequest",
    "plan_macro",
    "race_day_carryover",
    "race_day_session_weights",
    "skip_macro",
]
