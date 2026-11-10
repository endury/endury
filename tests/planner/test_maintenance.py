"""Tests for the raceless maintenance-goal planner core.

Covers the L1 phase planner's maintenance branch, the maintenance
weekly-hours table, the MAINTAIN TID rows, the maintenance menu
blueprints, the menu-contract coverage matrix, and the pipeline's
capacity-ceiling behavior for maintenance goals.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from itertools import pairwise

import pytest

from endury.planner import (
    AthleteState,
    PlanRequest,
    SportState,
    generate_plan,
)
from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.maintenance_tables import (
    MAINTENANCE_WEEKLY_HOURS,
    maintenance_weekly_hours,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    RaceEvent,
    RaceGoal,
    SessionOrigin,
    SportType,
    TIDFractions,
    WeekMode,
    WeekSpec,
)
from endury.planner.macro.tid_tables import get_tid_fractions
from endury.planner.phase.phase_planner import (
    _validate_maintenance_plan,
    plan_phases,
    validate_phase_request,
)
from endury.planner.pipeline import default_profile_for
from endury.planner.sessions.menus import BLUEPRINTS, get_session_menu
from endury.planner.sessions.menus.contracts import (
    MenuContractError,
    expected_coverage,
    validate_blueprint,
)
from endury.planner.sessions.menus.types import PhaseMenu, PlanBlueprint, Weekday
from tests.planner.menus.helpers import session

_MONDAY = date(2026, 1, 5)
_MAINTENANCE_GOALS = (
    GoalType.RUNNING_MAINTENANCE,
    GoalType.TRAIL_MAINTENANCE,
    GoalType.TRIATHLON_MAINTENANCE,
)


def _maintenance_a_goal(
    goal_type: GoalType, weeks: int = 8, start: date = _MONDAY
) -> RaceGoal:
    """An 8-week (by default) maintenance block ending on a Sunday."""
    return RaceGoal(
        goal_type=goal_type,
        race_date=start + timedelta(weeks=weeks, days=-1),
        priority="A",
    )


class TestMaintenanceSkeleton:
    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    def test_all_maintain_load_recovery_only_no_races(
        self, goal_type: GoalType, ambition: AmbitionLevel
    ) -> None:
        a = _maintenance_a_goal(goal_type)
        request = PhaseRequest(goals=[a], training_start=_MONDAY, ambition=ambition)
        plan = plan_phases(request)

        assert len(plan.weeks) == 8
        assert [w.week for w in plan.weeks] == list(range(8))
        assert all(w.phase is PhaseType.MAINTAIN for w in plan.weeks)
        assert all(w.mode in (WeekMode.LOAD, WeekMode.RECOVERY) for w in plan.weeks)
        assert plan.race_events == []

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    def test_every_tier_deloads_every_fourth_week(self, goal_type: GoalType) -> None:
        """3:1 at every tier (BASIC was 2:1, recovery weeks {2, 5}, until 2026-10-06)."""
        a = _maintenance_a_goal(goal_type)

        for ambition in AmbitionLevel:
            plan = plan_phases(
                PhaseRequest(goals=[a], training_start=_MONDAY, ambition=ambition)
            )
            recovery_weeks = {w.week for w in plan.weeks if w.mode is WeekMode.RECOVERY}
            # Week 7 is the last week of this 8-week block; the natural
            # cadence would land RECOVERY there (8 % 4 == 0), but R-PHASE-020
            # reverts a terminal RECOVERY back to LOAD.
            assert recovery_weeks == {3}

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize("weeks", range(4, 17))
    def test_final_week_never_recovery_across_block_lengths(
        self, goal_type: GoalType, ambition: AmbitionLevel, weeks: int
    ) -> None:
        """R-PHASE-020: a maintenance block never ends on RECOVERY.

        Holds for every block length 4-16, both maintenance goals, and all
        three ambition tiers. Earlier recovery weeks keep exact cadence
        spacing (``k * cadence - 1``); only a would-be-terminal RECOVERY
        (block length an exact multiple of the cadence) is reverted to LOAD.
        """
        a = _maintenance_a_goal(goal_type, weeks=weeks)
        request = PhaseRequest(goals=[a], training_start=_MONDAY, ambition=ambition)
        plan = plan_phases(request)

        assert plan.weeks[-1].mode is not WeekMode.RECOVERY
        assert plan.weeks[-1].phase is PhaseType.MAINTAIN

        cadence = get_ambition_profile(ambition).recovery_cadence
        expected_recovery = {k * cadence - 1 for k in range(1, weeks // cadence + 1)}
        expected_recovery.discard(weeks - 1)  # terminal recovery suppressed
        recovery_weeks = {w.week for w in plan.weeks if w.mode is WeekMode.RECOVERY}
        assert recovery_weeks == expected_recovery

        for prev, nxt in pairwise(sorted(recovery_weeks)):
            assert nxt - prev == cadence


def _tune_up(
    goal_type: GoalType, priority: str, week: int, start: date = _MONDAY
) -> RaceGoal:
    """A tune-up race on the Saturday of plan week ``week`` (0-indexed)."""
    return RaceGoal(
        goal_type=goal_type,
        race_date=start + timedelta(weeks=week, days=5),
        priority=priority,
    )


def _block_with(
    goal_type: GoalType, race: RaceGoal, weeks: int = 8, **request: object
) -> PhasePlan:
    a = _maintenance_a_goal(goal_type, weeks=weeks)
    return plan_phases(
        PhaseRequest(
            goals=[a, race],
            training_start=_MONDAY,
            ambition=AmbitionLevel.MODERATE,
            **request,  # type: ignore[arg-type]
        )
    )


class TestMaintenanceTuneUps:
    """A maintenance block takes a tune-up race in any of its weeks
    (R-PHASE-019; until 2026-10-20 B/C goals were hard-rejected, so an A
    race inside a block had no planner path and the coach hand-wrote a
    taper the calendar never saw)."""

    def test_a_b_race_week_is_its_own_mini_taper(self) -> None:
        race = _tune_up(GoalType.TRAIL_20K, "B", 4)
        plan = _block_with(GoalType.TRIATHLON_MAINTENANCE, race)

        week = plan.weeks[4]
        assert week.mode is WeekMode.RACE
        assert week.phase is PhaseType.TAPER
        assert week.owning_goal == race
        assert week.effective_goal is GoalType.TRAIL_20K
        assert [e.goal for e in plan.race_events] == [race]
        assert plan.race_events[0].event_id == "race:b_trail_20k_w4"
        assert plan.race_events[0].day_of_week == 5
        assert all(w.phase is PhaseType.MAINTAIN for w in plan.weeks if w.week != 4)
        assert all(w.owning_goal is None for w in plan.weeks if w.week != 4)

    def test_the_race_week_is_the_blocks_deload(self) -> None:
        """The load week before it stays LOAD and the cadence restarts after
        (a 10K is a short B race: no easier week follows it)."""
        plan = _block_with(
            GoalType.TRIATHLON_MAINTENANCE, _tune_up(GoalType.RUNNING_10K, "B", 4)
        )
        assert plan.weeks[3].mode is WeekMode.LOAD
        assert {w.week for w in plan.weeks if w.mode is WeekMode.RECOVERY} == set()
        plan = _block_with(
            GoalType.TRIATHLON_MAINTENANCE, _tune_up(GoalType.RUNNING_10K, "B", 1)
        )
        assert {w.week for w in plan.weeks if w.mode is WeekMode.RECOVERY} == {5}

    def test_a_c_race_week_is_the_blocks_own_lighter_week(self) -> None:
        race = _tune_up(GoalType.RUNNING_10K, "C", 5)
        plan = _block_with(GoalType.RUNNING_MAINTENANCE, race)

        week = plan.weeks[5]
        assert week.mode is WeekMode.RACE
        assert week.phase is PhaseType.MAINTAIN
        assert week.owning_goal == race
        # A race goal's menu has no MAINTAIN phase: the block's recovery
        # menu serves the lighter week with the race in it.
        assert week.effective_goal is GoalType.RUNNING_MAINTENANCE

    def test_a_long_b_race_gets_an_easier_week_after(self) -> None:
        plan = _block_with(
            GoalType.TRIATHLON_MAINTENANCE, _tune_up(GoalType.TRAIL_50K, "B", 4)
        )
        after = plan.weeks[5]
        assert after.mode is WeekMode.RACE_RECOVERY
        assert after.phase is PhaseType.MAINTAIN
        assert after.owning_goal is None
        assert after.effective_goal is GoalType.TRIATHLON_MAINTENANCE

    def test_a_long_b_race_in_the_last_week_has_no_week_after_it(self) -> None:
        plan = _block_with(
            GoalType.TRIATHLON_MAINTENANCE, _tune_up(GoalType.TRAIL_50K, "B", 7)
        )
        assert plan.race_events[0].week == len(plan.weeks) - 1
        assert WeekMode.RACE_RECOVERY not in {w.mode for w in plan.weeks}

    def test_a_tune_up_may_sit_in_the_first_weeks(self) -> None:
        """No A-race build to protect: the week-3 floor does not apply."""
        plan = _block_with(
            GoalType.TRAIL_MAINTENANCE, _tune_up(GoalType.TRAIL_20K, "B", 1)
        )
        assert plan.weeks[1].mode is WeekMode.RACE

    def test_the_race_week_split_leans_to_the_race_sport(self) -> None:
        plan = _block_with(
            GoalType.TRIATHLON_MAINTENANCE, _tune_up(GoalType.TRAIL_20K, "B", 4)
        )
        race_split = plan.weeks[4].sport_split
        assert (
            race_split[SportType.RUNNING] > plan.weeks[3].sport_split[SportType.RUNNING]
        )
        assert abs(sum(race_split.values()) - 1.0) < 0.01

    def test_a_replan_keeps_the_tune_up_on_the_blocks_timeline(self) -> None:
        effective_start = _MONDAY - timedelta(weeks=2)
        a = _maintenance_a_goal(
            GoalType.TRAIL_MAINTENANCE, weeks=8, start=effective_start
        )
        race = _tune_up(GoalType.TRAIL_20K, "B", 4, start=effective_start)
        plan = plan_phases(
            PhaseRequest(
                goals=[a, race], training_start=_MONDAY, prior_training_weeks=2
            )
        )
        assert [e.week for e in plan.race_events] == [2]
        assert plan.weeks[2].mode is WeekMode.RACE

    def test_two_tune_ups_in_one_week_are_rejected(self) -> None:
        a = _maintenance_a_goal(GoalType.RUNNING_MAINTENANCE)
        first = _tune_up(GoalType.RUNNING_10K, "B", 4)
        second = RaceGoal(GoalType.RUNNING_5K, first.race_date - timedelta(days=1), "C")
        result = validate_phase_request(
            PhaseRequest(goals=[a, first, second], training_start=_MONDAY)
        )
        assert not result.valid
        assert any("fall in the same week" in e for e in result.errors)

    def test_a_tune_up_after_the_block_end_is_rejected(self) -> None:
        a = _maintenance_a_goal(GoalType.RUNNING_MAINTENANCE)
        late = _tune_up(GoalType.RUNNING_10K, "B", 8)
        result = validate_phase_request(
            PhaseRequest(goals=[a, late], training_start=_MONDAY)
        )
        assert not result.valid
        assert any("is not before this block ends" in e for e in result.errors)


class TestValidateMaintenanceRequest:
    def test_maintenance_goal_as_bc_priority_is_rejected(self) -> None:
        a = RaceGoal(
            goal_type=GoalType.RUNNING_MARATHON,
            race_date=_MONDAY + timedelta(weeks=24),
            priority="A",
        )
        bc = RaceGoal(
            goal_type=GoalType.RUNNING_MAINTENANCE,
            race_date=_MONDAY + timedelta(weeks=8),
            priority="B",
        )
        request = PhaseRequest(goals=[a, bc], training_start=_MONDAY)
        result = validate_phase_request(request)

        assert not result.valid
        assert any(
            "A maintenance block is a plan of its own" in e for e in result.errors
        )

    def test_three_week_horizon_is_below_minimum(self) -> None:
        a = _maintenance_a_goal(GoalType.TRIATHLON_MAINTENANCE, weeks=3)
        request = PhaseRequest(goals=[a], training_start=_MONDAY)
        result = validate_phase_request(request)

        assert not result.valid
        assert any(
            "A maintenance block takes at least 4 weeks and this one has 3" in e
            for e in result.errors
        )


class TestMaintenancePriorTrainingWeeks:
    def test_prior_4_of_8_weeks_yields_reindexed_4_week_suffix(self) -> None:
        effective_start = _MONDAY - timedelta(weeks=4)
        a = _maintenance_a_goal(
            GoalType.RUNNING_MAINTENANCE, weeks=8, start=effective_start
        )
        request = PhaseRequest(
            goals=[a], training_start=_MONDAY, prior_training_weeks=4
        )
        plan = plan_phases(request)

        assert len(plan.weeks) == 4
        assert [w.week for w in plan.weeks] == [0, 1, 2, 3]
        assert all(w.phase is PhaseType.MAINTAIN for w in plan.weeks)
        assert plan.race_events == []


def _week(week: int, phase: PhaseType, mode: WeekMode) -> WeekSpec:
    return WeekSpec(
        week=week,
        phase=phase,
        mode=mode,
        sport_split={SportType.RUNNING: 1.0},
        effective_goal=GoalType.RUNNING_MAINTENANCE,
        owning_goal=None,
    )


_A_GOAL = RaceGoal(
    goal_type=GoalType.RUNNING_MAINTENANCE, race_date=_MONDAY, priority="A"
)


class TestValidateMaintenancePlan:
    def test_wrong_phase_reports_phase_error(self) -> None:
        weeks = [
            _week(0, PhaseType.BASE, WeekMode.LOAD),
            _week(1, PhaseType.MAINTAIN, WeekMode.LOAD),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)

        assert not result.valid
        assert any("maintenance plan has phase base" in e for e in result.errors)

    def test_wrong_mode_reports_mode_error(self) -> None:
        weeks = [
            _week(0, PhaseType.MAINTAIN, WeekMode.TAPER),
            _week(1, PhaseType.MAINTAIN, WeekMode.LOAD),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)

        assert not result.valid
        assert any("maintenance plan has mode taper" in e for e in result.errors)

    def test_a_race_event_for_the_blocks_end_reports_error(self) -> None:
        weeks = [_week(0, PhaseType.MAINTAIN, WeekMode.LOAD)]
        race_event = RaceEvent(
            event_id="race:a",
            goal=_A_GOAL,
            week=0,
            day_of_week=6,
            duration_by_sport={SportType.RUNNING: 1.0},
        )
        plan = PhasePlan(weeks=weeks, race_events=[race_event], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)

        assert not result.valid
        assert any(
            "RaceEvent week 0 is not a B/C race week" in e for e in result.errors
        )

    def test_a_tune_up_week_without_its_race_event_reports_error(self) -> None:
        race = _tune_up(GoalType.RUNNING_10K, "B", 0)
        week = replace(_week(0, PhaseType.TAPER, WeekMode.RACE), owning_goal=race)
        plan = PhasePlan(weeks=[week], race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)

        assert not result.valid
        assert any(
            "Missing RaceEvent for B-goal running_10k" in e for e in result.errors
        )

    def test_a_taper_phase_outside_a_tune_up_week_reports_error(self) -> None:
        plan = PhasePlan(
            weeks=[_week(0, PhaseType.TAPER, WeekMode.LOAD)],
            race_events=[],
            a_goal=_A_GOAL,
        )
        result = _validate_maintenance_plan(plan, recovery_cadence=4)
        assert any("maintenance plan has phase taper" in e for e in result.errors)

    def test_a_race_recovery_week_must_follow_a_tune_up_week(self) -> None:
        weeks = [
            _week(0, PhaseType.MAINTAIN, WeekMode.LOAD),
            _week(1, PhaseType.MAINTAIN, WeekMode.RACE_RECOVERY),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)
        assert any(
            "Week 1: RACE_RECOVERY not directly after a B/C race week" in e
            for e in result.errors
        )

    def test_gapped_week_indices_reports_error(self) -> None:
        weeks = [
            _week(0, PhaseType.MAINTAIN, WeekMode.LOAD),
            _week(2, PhaseType.MAINTAIN, WeekMode.LOAD),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=4)

        assert not result.valid
        assert any("Week indices not gap-free: got [0, 2]" in e for e in result.errors)

    def test_load_run_longer_than_cadence_reports_error(self) -> None:
        weeks = [_week(i, PhaseType.MAINTAIN, WeekMode.LOAD) for i in range(4)]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=3)

        assert not result.valid
        assert any(
            "Week 3: 4 consecutive LOAD weeks (max 3)" in e for e in result.errors
        )

    def test_valid_maintenance_plan_passes(self) -> None:
        weeks = [
            _week(0, PhaseType.MAINTAIN, WeekMode.LOAD),
            _week(1, PhaseType.MAINTAIN, WeekMode.LOAD),
            _week(2, PhaseType.MAINTAIN, WeekMode.RECOVERY),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=_A_GOAL)
        result = _validate_maintenance_plan(plan, recovery_cadence=3)
        assert result.valid
        assert result.errors == []


class TestMaintenanceWeeklyHours:
    def test_all_nine_combos_have_exact_values(self) -> None:
        assert (
            maintenance_weekly_hours(GoalType.RUNNING_MAINTENANCE, AmbitionLevel.BASIC)
            == 3.0
        )
        assert (
            maintenance_weekly_hours(
                GoalType.RUNNING_MAINTENANCE, AmbitionLevel.MODERATE
            )
            == 4.5
        )
        assert (
            maintenance_weekly_hours(
                GoalType.RUNNING_MAINTENANCE, AmbitionLevel.COMPETITIVE
            )
            == 6.0
        )
        assert (
            maintenance_weekly_hours(GoalType.TRAIL_MAINTENANCE, AmbitionLevel.BASIC)
            == 3.0
        )
        assert (
            maintenance_weekly_hours(GoalType.TRAIL_MAINTENANCE, AmbitionLevel.MODERATE)
            == 4.5
        )
        assert (
            maintenance_weekly_hours(
                GoalType.TRAIL_MAINTENANCE, AmbitionLevel.COMPETITIVE
            )
            == 6.0
        )
        assert (
            maintenance_weekly_hours(
                GoalType.TRIATHLON_MAINTENANCE, AmbitionLevel.BASIC
            )
            == 5.0
        )
        assert (
            maintenance_weekly_hours(
                GoalType.TRIATHLON_MAINTENANCE, AmbitionLevel.MODERATE
            )
            == 8.5
        )
        assert (
            maintenance_weekly_hours(
                GoalType.TRIATHLON_MAINTENANCE, AmbitionLevel.COMPETITIVE
            )
            == 10.0
        )

    def test_matches_table_directly(self) -> None:
        for goal_type, tiers in MAINTENANCE_WEEKLY_HOURS.items():
            for ambition, hours in tiers.items():
                assert maintenance_weekly_hours(goal_type, ambition) == hours

    def test_non_maintenance_goal_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            maintenance_weekly_hours(GoalType.IRONMAN, AmbitionLevel.MODERATE)


class TestMaintenanceTidFractions:
    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    def test_maintain_load_sums_to_one(self, goal_type: GoalType) -> None:
        fractions = get_tid_fractions(PhaseType.MAINTAIN, WeekMode.LOAD, goal_type)
        assert abs(fractions.z_low + fractions.z_mid + fractions.z_high - 1.0) < 1e-9

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    def test_maintain_recovery_returns_explicit_row(self, goal_type: GoalType) -> None:
        fractions = get_tid_fractions(PhaseType.MAINTAIN, WeekMode.RECOVERY, goal_type)
        assert fractions == TIDFractions(0.55, 0.35, 0.10)


class TestMaintenanceMenus:
    def test_blueprints_contains_all_maintenance_goals(self) -> None:
        assert GoalType.RUNNING_MAINTENANCE in BLUEPRINTS
        assert GoalType.TRAIL_MAINTENANCE in BLUEPRINTS
        assert GoalType.TRIATHLON_MAINTENANCE in BLUEPRINTS

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize("mode_key", ["load", "recovery"])
    def test_get_session_menu_non_empty(
        self, goal_type: GoalType, ambition: AmbitionLevel, mode_key: str
    ) -> None:
        specs = get_session_menu(goal_type, PhaseType.MAINTAIN, mode_key, ambition)
        assert specs

    def test_expected_coverage_maintenance_pair(self) -> None:
        for goal_type in _MAINTENANCE_GOALS:
            assert expected_coverage(goal_type) == frozenset(
                {
                    (PhaseType.MAINTAIN, "load"),
                    (PhaseType.MAINTAIN, "recovery"),
                }
            )

    def test_expected_coverage_classic_eight_for_ironman(self) -> None:
        assert expected_coverage(GoalType.IRONMAN) == frozenset(
            {
                (PhaseType.BASE, "load"),
                (PhaseType.BASE, "recovery"),
                (PhaseType.BUILD, "load"),
                (PhaseType.BUILD, "recovery"),
                (PhaseType.PEAK, "load"),
                (PhaseType.PEAK, "recovery"),
                (PhaseType.TAPER, "taper"),
                (PhaseType.TAPER, "race"),
            }
        )


class TestMaintenanceContractCoverage:
    def test_extra_phase_mode_raises_coverage_error(self) -> None:
        from endury.planner.foundations.types import ActivityKey

        def make(phase: PhaseType, mode_key: str) -> PhaseMenu:
            return PhaseMenu(
                phase=phase,
                mode_key=mode_key,
                slots=(
                    session("solo_run", Weekday.SUN, ActivityKey.RUN_EASY, (30, 45)),
                ),
            )

        blueprint = PlanBlueprint(
            goal=GoalType.RUNNING_MAINTENANCE,
            phases={
                (PhaseType.MAINTAIN, "load"): make(PhaseType.MAINTAIN, "load"),
                (PhaseType.MAINTAIN, "recovery"): make(PhaseType.MAINTAIN, "recovery"),
                (PhaseType.BASE, "load"): make(PhaseType.BASE, "load"),
            },
        )
        with pytest.raises(MenuContractError) as exc_info:
            validate_blueprint(blueprint)
        msg = str(exc_info.value)
        assert "coverage" in msg
        assert "unexpected" in msg

    def test_missing_recovery_phase_mode_raises_coverage_error(self) -> None:
        from endury.planner.foundations.types import ActivityKey

        def make(phase: PhaseType, mode_key: str) -> PhaseMenu:
            return PhaseMenu(
                phase=phase,
                mode_key=mode_key,
                slots=(
                    session("solo_run", Weekday.SUN, ActivityKey.RUN_EASY, (30, 45)),
                ),
            )

        blueprint = PlanBlueprint(
            goal=GoalType.RUNNING_MAINTENANCE,
            phases={
                (PhaseType.MAINTAIN, "load"): make(PhaseType.MAINTAIN, "load"),
            },
        )
        with pytest.raises(MenuContractError) as exc_info:
            validate_blueprint(blueprint)
        msg = str(exc_info.value)
        assert "coverage" in msg
        assert "missing" in msg


def _weekly_totals(result) -> dict[int, float]:
    """The hours each week books; an optional session (R-SESS-037) is extra."""
    totals: dict[int, float] = {}
    for placed in result.sessions:
        if placed.optional_reason is None:
            totals[placed.week] = totals.get(placed.week, 0.0) + placed.target_hours
    return totals


class TestMaintenanceConvergesOnTierVolume:
    """Every goal x tier actually reaches the volume its table promises.

    ``MAINTENANCE_WEEKLY_HOURS`` is the block's design volume, and the
    menus are supposed to author bounds whose weekly sums meet it. The
    capacity ceiling only bounds a block from above and the macro QP
    tracks the menu's own targets, so a tier whose bounds are authored
    too low converges quietly below its table value and nothing
    complains.
    """

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize(
        "ambition",
        [AmbitionLevel.BASIC, AmbitionLevel.MODERATE, AmbitionLevel.COMPETITIVE],
    )
    def test_peak_load_week_reaches_the_tier_volume(
        self, goal_type: GoalType, ambition: AmbitionLevel
    ) -> None:
        # 16 weeks: the ramp reaches the ceiling around 75% of LOAD weeks,
        # so a shorter block would measure the ramp, not the plateau.
        a = _maintenance_a_goal(goal_type, weeks=16)
        result = generate_plan(
            PlanRequest(goals=[a], training_start=_MONDAY, ambition=ambition)
        )
        totals = _weekly_totals(result)
        load_weeks = [
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
        ]
        peak = max(totals[w] for w in load_weeks)
        tier = maintenance_weekly_hours(goal_type, ambition)
        assert tier * 0.95 <= peak <= tier * 1.05, (
            f"{goal_type.value}/{ambition.value}: converges on {peak:.2f} h "
            f"against a {tier:.1f} h tier volume"
        )


class TestMaintenanceTuneUpPlan:
    """The plan an athlete gets around a tune-up inside a maintenance block."""

    def test_a_b_race_week_keeps_every_sport_and_lightens(self) -> None:
        a = _maintenance_a_goal(GoalType.TRIATHLON_MAINTENANCE, weeks=8)
        race = _tune_up(GoalType.TRAIL_20K, "B", 4)
        result = generate_plan(
            PlanRequest(
                goals=[a, race], training_start=_MONDAY, ambition=AmbitionLevel.MODERATE
            )
        )
        race_week = [s for s in result.sessions if s.week == 4]
        # The trail race-week menu runs; the block's supplements keep the
        # swim and the ride (R-SESS-007).
        assert {s.sport for s in race_week} == {
            SportType.RUNNING,
            SportType.CYCLING,
            SportType.SWIMMING,
        }
        totals = _weekly_totals(result)
        assert totals[4] < totals[3]
        # Race day is the race's: nothing else is booked on it.
        assert all(a.date != race.race_date for a in result.assignments)
        # The weeks around it are the block's own.
        assert {s.week for s in result.sessions} == set(range(8))


class TestMaintenancePipelineCapacity:
    def test_high_volume_athlete_stays_near_tier_ceiling_with_small_variation(
        self,
    ) -> None:
        """A well-trained triathlete's weekly volume tracks the menu's
        design volume, bounded by the COMPETITIVE tier ceiling (10.0 h),
        and consecutive LOAD weeks don't swing wildly."""
        a = _maintenance_a_goal(GoalType.TRIATHLON_MAINTENANCE)
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=6.0,
                    longest_recent_min={ActivityFamily.LONG: 90},
                ),
                SportType.CYCLING: SportState(
                    recent_weekly_hours=8.0,
                    longest_recent_min={ActivityFamily.LONG: 120},
                ),
                SportType.SWIMMING: SportState(
                    recent_weekly_hours=3.0,
                    longest_recent_min={ActivityFamily.EASY: 40},
                ),
            }
        )
        result = generate_plan(
            PlanRequest(
                goals=[a],
                training_start=_MONDAY,
                ambition=AmbitionLevel.COMPETITIVE,
                athlete=athlete,
            )
        )
        tier_ceiling = maintenance_weekly_hours(
            GoalType.TRIATHLON_MAINTENANCE, AmbitionLevel.COMPETITIVE
        )
        totals = _weekly_totals(result)
        assert all(hours <= tier_ceiling + 0.75 for hours in totals.values())

        load_weeks = sorted(
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
        )
        for prev, nxt in pairwise(load_weeks):
            if nxt == prev + 1:  # consecutive LOAD weeks only
                assert abs(totals[nxt] - totals[prev]) <= 1.0

    def test_detrained_runner_ramps_non_decreasing_toward_tier(self) -> None:
        """A detrained runner (1.5 h/wk) below the MODERATE tier (4.5 h)
        ramps upward — LOAD-week volume never decreases week over week."""
        a = _maintenance_a_goal(GoalType.RUNNING_MAINTENANCE)
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=1.5,
                    longest_recent_min={ActivityFamily.LONG: 30},
                ),
            }
        )
        result = generate_plan(
            PlanRequest(
                goals=[a],
                training_start=_MONDAY,
                ambition=AmbitionLevel.MODERATE,
                athlete=athlete,
            )
        )
        totals = _weekly_totals(result)
        load_weeks = sorted(
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
        )
        load_totals = [totals[w] for w in load_weeks]
        for prev, nxt in pairwise(load_totals):
            assert nxt >= prev - 1e-6


def _volume_scaled_athlete(
    goal_type: GoalType, ambition: AmbitionLevel, hours: float
) -> AthleteState:
    """A default-shaped athlete whose total recent hours is *hours*.

    Scales the goal's default per-sport split (and longest-recent minutes)
    to *hours*, the same recipe the sweep in M1's report uses, so a test at
    e.g. 0.5x the tier target reads as a genuinely half-volume athlete
    rather than a differently-shaped one.
    """
    base = default_profile_for(goal_type, ambition)
    total = sum(s.recent_weekly_hours for s in base.sports.values())
    factor = hours / total
    return AthleteState(
        sports={
            sport: SportState(
                recent_weekly_hours=state.recent_weekly_hours * factor,
                longest_recent_min={
                    family: max(20, round(minutes * factor))
                    for family, minutes in state.longest_recent_min.items()
                },
            )
            for sport, state in base.sports.items()
        }
    )


def _long_session_ramp_weeks(result, load_weeks: list[int]) -> set[int]:
    """The load weeks whose long ride or run is still growing into its range.

    A week ramps when a long session is shorter there than at its longest
    in the block, or grew since the load week before.
    """
    minutes: dict[str, dict[int, float]] = {}
    for placed in result.sessions:
        if placed.week in load_weeks and placed.activity_key.value in {
            "run_long",
            "run_trail_long",
            "bike_long",
            "bike_long_sweet_spot",
        }:
            # One ride, one ramp: the sweet-spot weeks are the same long ride.
            key = placed.activity_key.value.removesuffix("_sweet_spot")
            minutes.setdefault(key, {})[placed.week] = placed.target_hours
    ramping: set[int] = set()
    for by_week in minutes.values():
        longest = max(by_week.values())
        for prev, week in pairwise([None, *sorted(by_week)]):
            grew = prev is not None and by_week[week] > by_week[prev] + 1e-6
            if by_week[week] < longest - 1e-6 or grew:
                ramping.add(week)
    return ramping


class TestMaintenanceBlockHoldsFromWeekOne:
    """R-SESS-035: a maintenance load week is sized to the week's capacity
    from week one, and a recovery week lands near its tier's recovery
    fraction of the load week before it.

    Written before the fix: ``generate_session_menu`` interpolated every
    session's nominal duration from the menu's minimum (week one) to its
    maximum (the block's last week) across the *whole* maintenance block,
    since nothing in a maintenance menu is progressive and MAINTAIN is one
    contiguous phase run start to finish (``_compute_phase_runs``) — so an
    athlete at or above the tier target still opened near the menu's floor.
    A second, compounding defect: the generic per-sport step cap (meant to
    protect a *progressive* session's week-over-week ramp) also clamped
    non-progressive sessions authored above its sport's generic floor
    (``_PROGRESSIVE_FLOOR``, e.g. the 90-110 min competitive running long
    run against a 75-min floor) to that floor every week forever, because a
    non-progressive spec never records a ``last_duration`` for the cap to
    grow from. Both are scoped to ``ws.phase == PhaseType.MAINTAIN`` in
    ``session_generator.py`` — race-goal plans never hit either branch.

    A small (≤5%) tolerance absorbs the macro QP's own week-to-week
    smoothing noise (``LAMBDA_SMOOTH`` in ``macro_planner.py``; also
    documented for progressive sessions at R-SESS-B-002 in
    ``docs/specs/planner/behavioral.md``) — not a session_generator concern.

    Amended 2026-10-13: a menu whose long ride or run ramps from the
    athlete's longest (triathlon MODERATE's one-a-day week) opens below the
    volume while it ramps, and its test week books the tests' shortfall
    less; both carry their own floor below.
    """

    _WEEKS = (4, 8, 12)
    _VOLUME_FRACTIONS = (0.5, 0.8, 1.0, 1.2, 1.5)

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize("weeks", _WEEKS)
    @pytest.mark.parametrize("volume_fraction", _VOLUME_FRACTIONS)
    def test_load_and_recovery_weeks_hold_the_athlete_and_tier_shape(
        self,
        goal_type: GoalType,
        ambition: AmbitionLevel,
        weeks: int,
        volume_fraction: float,
    ) -> None:
        target = maintenance_weekly_hours(goal_type, ambition)
        hours = round(target * volume_fraction, 2)
        athlete = _volume_scaled_athlete(goal_type, ambition, hours)
        a = _maintenance_a_goal(goal_type, weeks=weeks)
        result = generate_plan(
            PlanRequest(
                goals=[a], training_start=_MONDAY, ambition=ambition, athlete=athlete
            )
        )
        totals = _weekly_totals(result)
        load_weeks = sorted(
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
        )
        recovery_weeks = sorted(
            w.week for w in result.phase_plan.weeks if w.mode is not WeekMode.LOAD
        )
        ramping = _long_session_ramp_weeks(result, load_weeks)
        test_weeks = {s.week for s in result.sessions if s.origin is SessionOrigin.TEST}

        if volume_fraction >= 1.0:
            # At or above the tier target: every load week within 5% of it
            # from week one (flat, apart from recovery weeks). Amended
            # 2026-10-13: a week whose long sessions still ramp into their
            # range books at least 0.8 of it, and a load week carrying the
            # fitness tests, shorter than the quality sessions they replace,
            # at least 0.9.
            for w in load_weeks:
                ratio = totals[w] / target
                floor = 0.8 if w in ramping else 0.9 if w in test_weeks else 0.95
                assert floor <= ratio <= 1.05, (
                    f"{goal_type.value}/{ambition.value} {weeks}wk wk{w}: "
                    f"{totals[w]:.2f}h vs target {target:.2f}h (ratio {ratio:.3f})"
                )
        else:
            # Below the tier target: the first load week is at or above the
            # athlete's own recent volume (at least 0.8 of it while the long
            # sessions ramp) and at most one ramp step above it (a 5%
            # allowance over the ramp step absorbs QP rounding), and load
            # weeks never fall week to week (a small tolerance absorbs the
            # QP's own smoothing noise, see class docstring). Amended
            # 2026-11-03: a load week carrying the fitness tests may fall to
            # 0.9, as above: triathlon MODERATE's half-volume athlete books
            # the week before at the sweet-spot ride's 107-minute card floor.
            ramp = get_ambition_profile(ambition).hours_ramp_limit
            first = totals[load_weeks[0]]
            floor = 0.8 if load_weeks[0] in ramping else 1.0
            assert first >= hours * floor - 1e-6, (
                f"{goal_type.value}/{ambition.value} {weeks}wk: first load week "
                f"{first:.2f}h is below the athlete's own {hours:.2f}h"
            )
            assert first <= hours * ramp * 1.05 + 1e-6, (
                f"{goal_type.value}/{ambition.value} {weeks}wk: first load week "
                f"{first:.2f}h is more than one ramp step above {hours:.2f}h"
            )
            for prev, nxt in pairwise(load_weeks):
                fall = 0.9 if nxt in test_weeks else 0.95
                assert totals[nxt] >= totals[prev] * fall - 1e-6, (
                    f"{goal_type.value}/{ambition.value} {weeks}wk: load week "
                    f"{nxt} ({totals[nxt]:.2f}h) fell from week {prev} "
                    f"({totals[prev]:.2f}h)"
                )

        # Every recovery week is at least half its preceding load week and
        # at most the tier's recovery fraction plus a small tolerance —
        # except a recovery week whose fixed test doesn't fit a
        # still-ramping athlete's small early-block ceiling: the test
        # can't partially execute, so it drops entirely (R-SESS-030) rather
        # than shrink, and the week reads low. That carve-out is named
        # explicitly here, not silently absorbed into a wider tolerance.
        recovery_fraction = get_ambition_profile(ambition).recovery_load_fraction
        for w in recovery_weeks:
            preceding_load = [lw for lw in load_weeks if lw < w]
            if not preceding_load:
                continue
            prev = max(preceding_load)
            ratio = totals[w] / totals[prev]
            has_test = any(
                s.week == w and s.origin is SessionOrigin.TEST for s in result.sessions
            )
            if not has_test and volume_fraction < 1.0:
                continue
            assert ratio >= 0.5 - 0.02, (
                f"{goal_type.value}/{ambition.value} {weeks}wk wk{w}: recovery "
                f"{totals[w]:.2f}h is below half the {totals[prev]:.2f}h load "
                f"week before it (ratio {ratio:.3f})"
            )
            assert ratio <= recovery_fraction + 0.1, (
                f"{goal_type.value}/{ambition.value} {weeks}wk wk{w}: recovery "
                f"{totals[w]:.2f}h is more than the tier's {recovery_fraction:.2f} "
                f"fraction (plus tolerance) of the {totals[prev]:.2f}h load week "
                f"before it (ratio {ratio:.3f})"
            )

    @pytest.mark.parametrize("goal_type", _MAINTENANCE_GOALS)
    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    def test_long_session_stays_the_longest_in_every_load_week(
        self, goal_type: GoalType, ambition: AmbitionLevel
    ) -> None:
        """R-SESS-035, requirement 2: the week keeps its shape.

        The long(est) session of the goal's primary sport — the run for
        running/trail, the bike for triathlon (a triathlon week's biggest
        single session has always been the ride, not the run) — stays the
        week's longest session at every volume this block can carry.
        """
        long_keys = {
            "run_long",
            "run_trail_long",
            "bike_long",
            "bike_long_sweet_spot",
            "bike_endurance",
        }
        for volume_fraction in (0.5, 1.0, 1.5):
            target = maintenance_weekly_hours(goal_type, ambition)
            hours = round(target * volume_fraction, 2)
            athlete = _volume_scaled_athlete(goal_type, ambition, hours)
            a = _maintenance_a_goal(goal_type, weeks=12)
            result = generate_plan(
                PlanRequest(
                    goals=[a],
                    training_start=_MONDAY,
                    ambition=ambition,
                    athlete=athlete,
                )
            )
            by_week: dict[int, list] = {}
            for placed in result.sessions:
                if placed.optional_reason is None:
                    by_week.setdefault(placed.week, []).append(placed)
            load_weeks = {
                w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
            }
            for week, sessions in by_week.items():
                if week not in load_weeks:
                    continue
                longs = [s for s in sessions if s.activity_key.value in long_keys]
                if not longs:
                    continue
                longest_overall = max(s.target_hours for s in sessions)
                longest_long = max(s.target_hours for s in longs)
                if (
                    (goal_type, ambition)
                    in {
                        (GoalType.TRAIL_MAINTENANCE, AmbitionLevel.BASIC),
                        (GoalType.TRIATHLON_MAINTENANCE, AmbitionLevel.MODERATE),
                    }
                    and volume_fraction < 1.0
                    and longest_long < longest_overall
                ):
                    # 2026-10-08: BASIC's trail long run now ramps up from the
                    # athlete's own longest, so a half-volume athlete (longest
                    # 30 min) opens it at 38 while the climb day cannot book
                    # under its card's 50. It is the longest again once the
                    # ramp reaches it (TestTrailMaintenanceBasicLongRunRamp).
                    # 2026-10-12: triathlon MODERATE's long ride ramps the same
                    # way and its bike session is key, so a half-volume
                    # athlete (longest ride 45) opens the ride under the
                    # 58-minute bike session; the ride is the longest again
                    # from week 7.
                    continue
                assert longest_long >= longest_overall - 1e-6, (
                    f"{goal_type.value}/{ambition.value} vol={volume_fraction} "
                    f"wk{week}: the long session ({longest_long * 60:.0f}m) is not "
                    f"the week's longest ({longest_overall * 60:.0f}m)"
                )


def _trail_basic_plan(weeks: int, fraction: float):
    """The trail maintenance BASIC plan for the default athlete scaled by *fraction*."""
    from devtools.quality.planner import score_quality

    scenario = score_quality._scaled_scenario(
        weeks, AmbitionLevel.BASIC, GoalType.TRAIL_MAINTENANCE, fraction, "t"
    )
    return generate_plan(scenario.to_request())


class TestTrailMaintenanceBasicLongRunRamp:
    """2026-10-08: BASIC's trail long run opens from the athlete's longest.

    Written before the fix: the menu authored the long run as a plain 60-80
    slot, so a maintenance block (which holds rather than ramps) opened it at
    the menu's range whatever the athlete had run lately: 71 min for an
    athlete whose longest was 51, 48 for one whose longest was 30.
    """

    @pytest.mark.parametrize("fraction", [0.5, 0.85])
    @pytest.mark.parametrize("weeks", [8, 12, 16])
    def test_opens_near_the_recent_longest_and_grows_a_step_a_week(
        self, fraction: float, weeks: int
    ) -> None:
        longest = round(60 * fraction)
        result = _trail_basic_plan(weeks, fraction)
        load_weeks = {
            w.week for w in result.phase_plan.weeks if w.mode is WeekMode.LOAD
        }
        longs = [
            s.target_hours * 60
            for s in sorted(result.sessions, key=lambda s: s.week)
            if s.activity_key.value == "run_trail_long" and s.week in load_weeks
        ]
        # One step: 10 % of the previous, at least 8 minutes, plus rounding.
        previous = float(longest)
        for minutes in longs:
            assert minutes <= previous + max(0.1 * previous, 8.0) + 1.0, (
                f"{fraction}x {weeks}wk: long run {minutes:.0f} after {previous:.0f}"
            )
            previous = max(previous, minutes)


class TestTrailMaintenanceBasicNoShrunkSession:
    """2026-10-08: a booked session never falls under its menu minimum.

    Written before the fix: a squeezed week scaled every minimum alike, so a
    half-volume athlete saw a 15-minute easy run (menu 35-45), a 34-minute
    recovery long run (37) and 25-minute strides (27). A session the week
    cannot hold at its minimum stays whole and optional instead.
    """

    @pytest.mark.parametrize("fraction", [0.5, 0.85, 1.0, 1.5])
    @pytest.mark.parametrize("weeks", [8, 12, 16])
    def test_booked_sessions_hold_their_menu_minimum(
        self, fraction: float, weeks: int
    ) -> None:
        result = _trail_basic_plan(weeks, fraction)
        for week in result.phase_plan.weeks:
            mode_key = "load" if week.mode is WeekMode.LOAD else "recovery"
            menu = get_session_menu(
                GoalType.TRAIL_MAINTENANCE, week.phase, mode_key, AmbitionLevel.BASIC
            )
            minimum = {
                (s.day_hint, s.sport): s.min_minutes
                for s in menu
                if not s.key and not s.progressive and not s.optional
            }
            for s in result.sessions:
                floor = minimum.get((s.day_of_week, s.sport))
                if (
                    s.week != week.week
                    or s.optional_reason
                    or floor is None
                    or "test" in s.activity_key.value
                ):
                    continue
                assert s.target_hours * 60 >= 0.95 * floor - 1.0, (
                    f"{fraction}x {weeks}wk wk{week.week}: "
                    f"{s.activity_key.value} {s.target_hours * 60:.0f} < {floor}"
                )


class TestTrailMaintenanceBasicTestsEveryAthlete:
    """Every athlete tests every eight weeks, from week 5, never in week 1.

    2026-10-11 (R-SESS-B-000): at half volume a keyed test whose share fell
    short was the first thing the fit dropped. 2026-10-12 (maintainer): a
    maintenance block follows a season the athlete has trained and tested
    through, so it opens on their current zones; the test comes in the
    first load week after each recovery week every eight weeks.
    """

    @pytest.mark.parametrize("fraction", [0.5, 0.85, 1.0])
    @pytest.mark.parametrize("weeks", [8, 12, 16])
    def test_tests_in_weeks_5_and_13_never_in_week_1(
        self, fraction: float, weeks: int
    ) -> None:
        result = _trail_basic_plan(weeks, fraction)
        tests = sorted(
            s.week + 1
            for s in result.sessions
            if s.activity_key is ActivityKey.RUN_THRESHOLD_TEST
            and s.optional_reason is None
        )
        assert tests == [w for w in (5, 13) if w <= weeks]

    def test_half_volume_test_week_keeps_its_test_over_the_ceiling(self) -> None:
        """A 1.5 h week books the 55-minute test; the easy runs go optional."""
        result = _trail_basic_plan(8, 0.5)
        week = [s for s in result.sessions if s.week == 4]
        (test,) = [s for s in week if s.activity_key is ActivityKey.RUN_THRESHOLD_TEST]
        assert test.optional_reason is None
        assert test.target_hours * 60 == pytest.approx(55)
        assert any(
            s.activity_key is ActivityKey.RUN_EASY and s.optional_reason for s in week
        )
