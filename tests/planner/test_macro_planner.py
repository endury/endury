"""Tests for endury/planner/macro/macro_planner.py."""

from __future__ import annotations

import warnings
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    CapacityProjection,
    GoalType,
    PhaseRequest,
    RaceGoal,
    SportType,
    WeekMode,
)
from endury.planner.macro.capacity import build_capacity_projection
from endury.planner.macro.macro_config import (
    ATL_DECAY_DAILY,
    ATL_DECAY_WEEKLY,
    CTL_DECAY_DAILY,
    CTL_DECAY_WEEKLY,
    DAILY_TSB_DIVISOR,
)
from endury.planner.macro.macro_planner import (
    _ROUND_THRESHOLD_HOURS,
    SPORT_RAMP_LIMIT,
    MacroRequest,
    QPInfeasibleError,
    _should_round,
    plan_macro,
    race_day_carryover,
    race_day_session_weights,
    skip_macro,
)
from endury.planner.macro.session_pricing import session_tss_per_hour
from endury.planner.phase.phase_planner import plan_phases
from endury.planner.sessions.session_generator import generate_session_menu

_START = date(2026, 6, 1)


def _build_request(weeks: int = 22) -> MacroRequest:
    """Build a minimal valid MacroRequest for an Ironman plan."""
    goal = RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=weeks), "A")
    plan = plan_phases(
        PhaseRequest(
            goals=[goal],
            training_start=_START,
            ambition=AmbitionLevel.MODERATE,
        )
    )
    recent_hours = {
        SportType.RUNNING: 4.0,
        SportType.CYCLING: 6.0,
        SportType.SWIMMING: 2.0,
    }
    longest = {
        (SportType.RUNNING, ActivityFamily.LONG): 60,
        (SportType.CYCLING, ActivityFamily.LONG): 90,
        (SportType.SWIMMING, ActivityFamily.EASY): 30,
    }
    amb = get_ambition_profile(AmbitionLevel.MODERATE)
    capacity = build_capacity_projection(
        recent_hours,
        plan.weeks,
        hours_ramp_limit=amb.hours_ramp_limit,
        max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
        recovery_ceiling_factor=amb.recovery_load_fraction,
    )
    sessions = generate_session_menu(
        plan, longest, ambition=AmbitionLevel.MODERATE, capacity=capacity
    )
    ctl_0 = {s: recent_hours[s] * 50.0 for s in recent_hours}
    return MacroRequest(
        phase_plan=plan,
        sessions=sessions,
        ctl_0=ctl_0,
        atl_0=ctl_0,
        capacity=capacity,
    )


class TestPlanMacro:
    def test_feasible_returns_budget(self) -> None:
        """A well-specified request returns a MacroBudget with all sessions."""
        req = _build_request()
        budget = plan_macro(req)
        # budget.sessions is a list and must have one entry per input session.
        assert len(budget.sessions) == len(req.sessions)
        # session_hours is keyed by slot_id; may be fewer than sessions if
        # slot_ids are not unique, but must be non-empty.
        assert len(budget.session_hours) > 0

    def test_cap_skips_a_week_with_no_sessions(self) -> None:
        """A capped plan tolerates a week whose total is a bare constant."""
        req = _build_request()
        empty_week = 3
        req = replace(
            req,
            sessions=[sess for sess in req.sessions if sess.week != empty_week],
            weekly_hours_cap=5.0,
        )
        budget = plan_macro(req)
        assert all(sess.week != empty_week for sess in budget.sessions)
        assert len(budget.sessions) == len(req.sessions)

    def test_prices_every_session_with_its_own_activity(self) -> None:
        """Each slot carries the TSS/h it was sized with: its menu-target price."""
        req = _build_request()
        budget = plan_macro(req)
        assert len({s.slot_id for s in req.sessions}) == len(req.sessions)
        assert budget.session_tss_per_hour == {
            s.slot_id: session_tss_per_hour(s.activity_key, s.target_hours)
            for s in req.sessions
        }
        assert all(v > 0 for v in budget.session_tss_per_hour.values())

    def test_session_hours_within_bounds(self) -> None:
        """QP-optimised hours must lie within [min_hours, max_hours] for each session."""
        req = _build_request()
        budget = plan_macro(req)
        for orig, solved in zip(req.sessions, budget.sessions, strict=True):
            lo = orig.min_hours if orig.min_hours > 0 else orig.target_hours
            hi = orig.max_hours if orig.max_hours > 0 else orig.target_hours
            assert solved.target_hours >= lo - 1e-3, (
                f"{orig.slot_id}: hours={solved.target_hours:.4f} below min={lo:.4f}"
            )
            assert solved.target_hours <= hi + 1e-3, (
                f"{orig.slot_id}: hours={solved.target_hours:.4f} above max={hi:.4f}"
            )

    def test_infeasible_raises_value_error(self) -> None:
        """A zero-capacity plan must raise QPInfeasibleError (a ValueError)."""
        req = _build_request()
        # Override capacity with zero ceilings to force infeasibility.
        zero_capacity = CapacityProjection(
            weekly_ceiling={ws.week: 0.0 for ws in req.phase_plan.weeks},
            weekly_ceiling_by_sport={
                ws.week: dict.fromkeys(SportType, 0.0) for ws in req.phase_plan.weeks
            },
        )
        bad_req = MacroRequest(
            phase_plan=req.phase_plan,
            sessions=req.sessions,
            ctl_0=req.ctl_0,
            atl_0=req.atl_0,
            capacity=zero_capacity,
        )
        with pytest.raises(QPInfeasibleError, match="infeasible"):
            plan_macro(bad_req)

    def test_b_and_c_priority_race_goals(self) -> None:
        """B and C priority race goals trigger their respective freshness targets."""
        goals = [
            RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=36), "A"),
            RaceGoal(GoalType.HALF_IRONMAN, _START + timedelta(weeks=20), "B"),
            RaceGoal(GoalType.OLYMPIC_TRIATHLON, _START + timedelta(weeks=8), "C"),
        ]
        plan = plan_phases(
            PhaseRequest(
                goals=goals,
                training_start=_START,
                ambition=AmbitionLevel.MODERATE,
            )
        )
        recent_hours = {
            SportType.RUNNING: 4.0,
            SportType.CYCLING: 6.0,
            SportType.SWIMMING: 2.0,
        }
        longest = {
            (SportType.RUNNING, ActivityFamily.LONG): 60,
            (SportType.CYCLING, ActivityFamily.LONG): 90,
            (SportType.SWIMMING, ActivityFamily.EASY): 30,
        }
        amb = get_ambition_profile(AmbitionLevel.MODERATE)
        capacity = build_capacity_projection(
            recent_hours,
            plan.weeks,
            hours_ramp_limit=amb.hours_ramp_limit,
            max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
            recovery_ceiling_factor=amb.recovery_load_fraction,
        )
        sessions = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.MODERATE, capacity=capacity
        )
        ctl_0 = {s: recent_hours[s] * 50.0 for s in recent_hours}
        req = MacroRequest(
            phase_plan=plan,
            sessions=sessions,
            ctl_0=ctl_0,
            atl_0=ctl_0,
            capacity=capacity,
        )
        # Should not raise — the B/C freshness branches must be reachable.
        budget = plan_macro(req)
        assert len(budget.sessions) == len(sessions)

    def test_per_sport_ramp_constrains_spike(self) -> None:
        """Per-sport ramp constraint prevents running from spiking across LOAD weeks."""
        req = _build_request(weeks=22)
        budget = plan_macro(req)

        # Aggregate per-(week, sport) hours from the solved sessions.
        from collections import defaultdict

        hours_by_ws: dict[tuple[int, str], float] = defaultdict(float)
        for s in budget.sessions:
            hours_by_ws[(s.week, s.sport.value)] += s.target_hours

        plan_weeks = {ws.week: ws for ws in req.phase_plan.weeks}
        violations = 0
        for wk in sorted({w for w, _ in hours_by_ws}):
            ws_curr = plan_weeks.get(wk)
            ws_next = plan_weeks.get(wk + 1)
            if not ws_curr or not ws_next:
                continue
            if ws_curr.mode != WeekMode.LOAD or ws_next.mode != WeekMode.LOAD:
                continue
            for sport in ("running", "cycling", "swimming"):
                h_k = hours_by_ws.get((wk, sport), 0.0)
                h_k1 = hours_by_ws.get((wk + 1, sport), 0.0)
                if h_k > 0.5 and h_k1 > SPORT_RAMP_LIMIT * h_k + 1.0 + 1e-3:
                    violations += 1
        assert violations == 0, (
            f"Found {violations} per-sport ramp violation(s) across LOAD→LOAD weeks"
        )

    def test_tracks_menu_targets(self) -> None:
        """QP output closely tracks menu-prescribed target_hours.

        The tracking-first objective pulls every flexible session toward
        its menu target.  Smoothing and freshness penalties may induce
        small deviations, but the RMS deviation across flexible sessions
        must stay well below the band width.
        """
        import numpy as np

        req = _build_request(weeks=22)
        budget = plan_macro(req)

        deviations: list[float] = []
        mean_target = 0.0
        n_flex = 0
        for orig, solved in zip(req.sessions, budget.sessions, strict=True):
            lo = orig.min_hours if orig.min_hours > 0 else orig.target_hours
            hi = orig.max_hours if orig.max_hours > 0 else orig.target_hours
            if hi - lo < 1e-6:
                continue  # fixed-duration: nothing to track
            deviations.append(solved.target_hours - orig.target_hours)
            mean_target += orig.target_hours
            n_flex += 1

        assert n_flex > 0, "no flexible sessions in the request"
        mean_target /= n_flex
        rms_dev = float(np.sqrt(np.mean(np.square(deviations))))
        rms_pct = rms_dev / mean_target * 100

        assert rms_pct < 10.0, (
            f"RMS tracking deviation {rms_pct:.1f}% of mean target "
            f"exceeds 10% — menu prescription is being distorted"
        )

    def test_ctl_is_tiebreaker_only(self) -> None:
        """Race-week CTL term must not dominate the tracking objective.

        With ε·CTL as a tiebreaker, the sum of session hours must stay
        close to the menu-prescribed total.  If the CTL term were
        dominant, the solver would push sessions to their upper bounds
        and produce significantly more volume than the menu prescribes.
        """
        req = _build_request(weeks=22)
        budget = plan_macro(req)

        menu_total = sum(s.target_hours for s in req.sessions)
        solved_total = sum(s.target_hours for s in budget.sessions)
        delta_pct = (solved_total - menu_total) / menu_total * 100

        assert abs(delta_pct) < 3.0, (
            f"Solved total hours deviates {delta_pct:+.2f}% from menu — "
            f"CTL tiebreaker appears to be overriding tracking"
        )

    def test_quadratic_smoothing_prevents_spike(self) -> None:
        """Quadratic smoothing prevents large volume jumps across LOAD weeks."""
        req = _build_request(weeks=22)
        budget = plan_macro(req)

        from collections import defaultdict

        from endury.planner.macro.macro_planner import SPORT_IDX

        hours_by_ws: dict[tuple[int, int], float] = defaultdict(float)
        for s in budget.sessions:
            s_idx = SPORT_IDX.get(s.sport)
            if s_idx is not None:
                hours_by_ws[(s.week, s_idx)] += s.target_hours

        plan_weeks = {ws.week: ws for ws in req.phase_plan.weeks}
        max_ratio = 0.0
        for wk in sorted({w for w, _ in hours_by_ws}):
            ws_curr = plan_weeks.get(wk)
            ws_next = plan_weeks.get(wk + 1)
            if not ws_curr or not ws_next:
                continue
            if ws_curr.mode != WeekMode.LOAD or ws_next.mode != WeekMode.LOAD:
                continue
            for s_idx in range(3):
                h_k = hours_by_ws.get((wk, s_idx), 0.0)
                h_k1 = hours_by_ws.get((wk + 1, s_idx), 0.0)
                if h_k > 0.5:
                    ratio = h_k1 / h_k
                    max_ratio = max(max_ratio, ratio)

        # With quadratic smoothing, the effective ramp should stay well
        # below the 1.5x safety net — typically under 1.45x.
        assert max_ratio < 1.45, (
            f"Max week-to-week per-sport ratio {max_ratio:.3f} exceeds 1.45"
        )

    def test_smoothing_across_recovery_gap(self) -> None:
        """Within-phase volume jump from last LOAD before recovery to first LOAD after must be bounded.

        Cross-phase recovery gaps (e.g. BASE→BUILD) are excluded: smoothing
        is intentionally phase-aware and does not damp deliberate inter-phase
        volume ramps.
        """
        req = _build_request(weeks=22)
        budget = plan_macro(req)

        from collections import defaultdict

        hours_by_week: dict[int, float] = defaultdict(float)
        for s in budget.sessions:
            hours_by_week[s.week] += s.target_hours

        plan_weeks = {ws.week: ws for ws in req.phase_plan.weeks}
        load_weeks = sorted(
            wk for wk, ws in plan_weeks.items() if ws.mode == WeekMode.LOAD
        )

        max_jump_pct = 0.0
        for i in range(len(load_weeks) - 1):
            k_prev = load_weeks[i]
            k_next = load_weeks[i + 1]
            if k_next == k_prev + 1:
                continue  # consecutive LOAD, not a recovery gap
            if plan_weeks[k_prev].phase != plan_weeks[k_next].phase:
                continue  # cross-phase gap — deliberate ramp, not smoothed
            h_prev = hours_by_week.get(k_prev, 0.0)
            h_next = hours_by_week.get(k_next, 0.0)
            if h_prev > 1.0:
                jump_pct = (h_next / h_prev - 1.0) * 100
                max_jump_pct = max(max_jump_pct, jump_pct)

        # Within-phase post-recovery re-entry should not spike more than 25%.
        assert max_jump_pct < 25.0, (
            f"Within-phase post-recovery volume jump {max_jump_pct:.1f}% exceeds 25%"
        )


class TestSkipMacro:
    """Cover the QP-bypass path used by devtools."""

    def test_passes_through_target_hours_with_rounding(self) -> None:
        req = _build_request(weeks=22)
        budget = skip_macro(req.sessions)
        assert len(budget.sessions) == len(req.sessions)
        # Every input slot has an entry in session_hours.
        for sess in req.sessions:
            assert sess.slot_id in budget.session_hours

    def test_session_hours_dict_consistent_with_updated_sessions(self) -> None:
        req = _build_request(weeks=22)
        budget = skip_macro(req.sessions)
        for sess in budget.sessions:
            assert budget.session_hours[sess.slot_id] == sess.target_hours

    def test_prices_every_session_at_its_menu_target(self) -> None:
        req = _build_request(weeks=22)
        budget = skip_macro(req.sessions)
        assert budget.session_tss_per_hour == {
            s.slot_id: session_tss_per_hour(s.activity_key, s.target_hours)
            for s in req.sessions
        }


class TestRaceDayInstant:
    """The day-stepped race-day freshness expression."""

    def test_carryover_over_a_full_week_matches_the_weekly_decay(self) -> None:
        """A Sunday race is exactly one weekly step from race-week entry."""
        ctl, atl = race_day_carryover(6)
        assert ctl == pytest.approx(CTL_DECAY_WEEKLY)
        assert atl == pytest.approx(ATL_DECAY_WEEKLY)

    def test_carryover_on_a_monday_race_is_a_single_day(self) -> None:
        ctl, atl = race_day_carryover(0)
        assert ctl == pytest.approx(CTL_DECAY_DAILY)
        assert atl == pytest.approx(ATL_DECAY_DAILY)

    def test_session_after_the_race_contributes_nothing(self) -> None:
        """The day assigner drops post-race sessions in the A-race week."""
        assert race_day_session_weights(5, 3) == (0.0, 0.0)

    def test_session_on_the_race_day_is_evicted_one_day_earlier(self) -> None:
        assert race_day_session_weights(3, 3) == race_day_session_weights(2, 3)

    def test_monday_race_leaves_no_room_for_race_week_training(self) -> None:
        assert race_day_session_weights(0, 0) == (0.0, 0.0)

    def test_missing_day_falls_back_to_monday(self) -> None:
        assert race_day_session_weights(None, 6) == race_day_session_weights(0, 6)

    def test_closer_sessions_carry_more_fatigue(self) -> None:
        """ATL weight rises steeply toward the race; CTL weight barely moves."""
        far_ctl, far_atl = race_day_session_weights(0, 6)
        near_ctl, near_atl = race_day_session_weights(5, 6)
        assert near_atl > 2.0 * far_atl
        assert near_ctl == pytest.approx(far_ctl, rel=0.2)

    def test_matches_a_daily_banister_replay(self) -> None:
        """The affine weights reproduce a day-by-day simulation exactly.

        This is the property the QP relies on: the race-day state stays
        affine in the week-entry state and the session hours (so the problem
        stays convex) *and* equals what the daily projector would compute.
        The race-day slot is left empty because the day assigner evicts any
        session landing on it.
        """
        race_dow = 6
        entry_ctl, entry_atl = 520.0, 338.0  # weekly-TSS units
        daily_tss = [28.3, 53.7, 0.0, 29.2, 34.6, 16.3, 0.0]

        ctl = entry_ctl / DAILY_TSB_DIVISOR
        atl = entry_atl / DAILY_TSB_DIVISOR
        for tss in daily_tss:
            ctl = CTL_DECAY_DAILY * ctl + (1 - CTL_DECAY_DAILY) * tss
            atl = ATL_DECAY_DAILY * atl + (1 - ATL_DECAY_DAILY) * tss

        ctl_carry, atl_carry = race_day_carryover(race_dow)
        weekly_ctl = ctl_carry * entry_ctl
        weekly_atl = atl_carry * entry_atl
        for day, tss in enumerate(daily_tss):
            w_ctl, w_atl = race_day_session_weights(day, race_dow)
            weekly_ctl += w_ctl * tss
            weekly_atl += w_atl * tss

        assert (weekly_ctl - weekly_atl) / DAILY_TSB_DIVISOR == pytest.approx(ctl - atl)


class TestShouldRound:
    """Cover the short-duration rounding decision in _should_round."""

    def test_unregistered_activity_key_returns_false(self, monkeypatch) -> None:
        """A sub-threshold duration with an unknown activity_key is not rounded."""

        def _raise(_key):
            raise KeyError(_key)

        monkeypatch.setattr("endury.planner.activity.registry.get_activity", _raise)
        # Hours below the threshold so the family lookup branch is taken.
        small_hours = _ROUND_THRESHOLD_HOURS / 2.0
        assert _should_round(small_hours, ActivityKey.RUN_EASY) is False


class TestRaceWeekFreshnessSkipsZeroWeightSports:
    """Race-week sessions in a sport the goal does not score are skipped.

    ``GOAL_CTL_WEIGHTS`` gives a running goal weight 0.0 for cycling and
    swimming, so a cross-training session sitting in the race week must
    drop out of the race-day freshness expression instead of pulling the
    QP toward a sport the race never tests.
    """

    def test_zero_weight_race_week_session_is_excluded(self) -> None:
        req = _build_request()
        run_goal = replace(req.phase_plan.a_goal, goal_type=GoalType.RUNNING_MARATHON)
        race_events = [
            replace(
                event, goal=replace(event.goal, goal_type=GoalType.RUNNING_MARATHON)
            )
            for event in req.phase_plan.race_events
        ]
        phase_plan = replace(req.phase_plan, a_goal=run_goal, race_events=race_events)
        run_req = MacroRequest(
            phase_plan=phase_plan,
            sessions=req.sessions,
            ctl_0=req.ctl_0,
            atl_0=req.atl_0,
            capacity=req.capacity,
        )

        race_week = max(event.week for event in race_events)
        assert any(
            s.week == race_week and s.sport is not SportType.RUNNING
            for s in req.sessions
        ), "fixture must place a zero-weight sport in the race week"

        budget = plan_macro(run_req)

        assert len(budget.sessions) == len(req.sessions)


class TestNoNumpyWarnings:
    """cvxpy's shape probe must not leak numpy FP warnings out of plan_macro."""

    @staticmethod
    def _dirty_small_alloc_cache() -> None:
        """Free small numpy buffers holding ±inf so ``np.empty`` hands them back.

        ``cvxpy.atoms.affine.sum.Sum.shape_from_args`` sums an uninitialised
        ``np.empty(shape)`` purely to read ``.shape`` off the result. When that
        buffer comes back holding both +inf and -inf, the sum is an invalid FP
        operation and numpy warns about a value nobody reads.
        """
        junk = []
        for size in range(1, 12):
            for _ in range(60):
                buf = np.empty(size)
                buf[:] = np.inf
                if size > 1:
                    buf[1::2] = -np.inf
                junk.append(buf)
        del junk

    def test_plan_macro_emits_no_invalid_value_warning(self) -> None:
        req = _build_request()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._dirty_small_alloc_cache()
            plan_macro(req)

        offenders = [w for w in caught if "invalid value" in str(w.message)]
        assert not offenders, f"{len(offenders)} numpy FP warnings escaped plan_macro"


class TestRaceDayFreshnessRatio:
    """The race-day freshness floor is a TSB / CTL ratio, reachable at any fitness."""

    def test_a_low_fitness_runner_is_not_loaded_up_to_chase_freshness(self) -> None:
        """A basic 10K's opening week tracks the menu.

        Under an absolute daily-TSB floor (+14 for a two-week taper) a runner
        arriving at a CTL near 12 a day could never be fresh enough, so the
        QP inflated the opening weeks (load four or more weeks out lifts
        race-day form) and cut the taper to its floors. A ratio floor is
        within reach, so the menu prescription stands.
        """
        from endury.planner import PlanRequest, generate_plan
        from endury.planner.foundations.request import SolverOptions

        goals = [
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=12, days=-1), "A")
        ]
        request = PlanRequest(
            goals=goals, training_start=_START, ambition=AmbitionLevel.BASIC
        )
        sized = generate_plan(request).sessions
        menu = generate_plan(
            replace(request, solver=SolverOptions(use_macro_solver=False))
        ).sessions

        def week_hours(sessions, week):
            return sum(s.target_hours for s in sessions if s.week == week)

        assert week_hours(sized, 0) == pytest.approx(week_hours(menu, 0), abs=0.02)
