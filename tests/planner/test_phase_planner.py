"""Tests for L1 phase planner.

Covers reference tables, input validation, post-condition checks, and the
four-step phase allocation algorithm.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pytest

from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    PlanMaturity,
    RaceEvent,
    RaceGoal,
    SportType,
    WeekMode,
    WeekSpec,
    is_maintenance_goal,
)
from endury.planner.phase.phase_config import (
    BC_EARLIEST_WEEK,
    SHORT_TAPER_SUPPRESSION_WEEKS,
    TAPER_SUPPRESSION_WEEKS,
)
from endury.planner.phase.phase_planner import (
    _allocate_phases,
    _build_a_skeleton,
    _normalize_split,
    _splice_secondary_goals,
    plan_phases,
    validate_phase_plan,
    validate_phase_request,
)
from endury.planner.phase.phase_tables import GOAL_PROFILES

_MONDAY = date(2026, 1, 5)  # a Monday


def _a_goal(
    goal_type: GoalType = GoalType.RUNNING_MARATHON,
    weeks_from_start: int = 16,
    start: date = _MONDAY,
) -> RaceGoal:
    return RaceGoal(
        goal_type=goal_type,
        race_date=start + timedelta(weeks=weeks_from_start),
        priority="A",
    )


def _bc_goal(
    goal_type: GoalType = GoalType.RUNNING_10K,
    weeks_from_start: int = 8,
    priority: str = "B",
    start: date = _MONDAY,
) -> RaceGoal:
    return RaceGoal(
        goal_type=goal_type,
        race_date=start + timedelta(weeks=weeks_from_start),
        priority=priority,
    )


def _request(
    *goals: RaceGoal,
    start: date = _MONDAY,
) -> PhaseRequest:
    return PhaseRequest(goals=list(goals), training_start=start)


def _assert_post_conditions(plan: PhasePlan, request: PhaseRequest) -> None:
    """Assert that all post-conditions pass."""
    result = validate_phase_plan(plan, request)
    assert result.valid, f"Post-condition failures: {result.errors}"


class TestGoalProfiles:
    """Verify completeness and consistency of GOAL_PROFILES."""

    def test_all_goal_types_present(self) -> None:
        for gt in GoalType:
            assert gt in GOAL_PROFILES, f"Missing GoalProfile for {gt.value}"

    def test_max_recommended_weeks_bounds(self) -> None:
        """Every race goal has a focused-prep ceiling above its minimum."""
        for gt, p in GOAL_PROFILES.items():
            if is_maintenance_goal(gt):
                assert p.max_recommended_weeks is None
            else:
                assert p.max_recommended_weeks is not None
                assert p.max_recommended_weeks >= p.min_horizon_weeks

    def test_phase_ratios_sum_to_one(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            total = sum(p.phase_ratios)
            assert abs(total - 1.0) < 0.01, f"{gt.value}: ratios sum to {total}"

    def test_sport_split_sums_to_one(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            total = sum(p.default_sport_split.values())
            assert abs(total - 1.0) < 0.01, f"{gt.value}: split sums to {total}"

    def test_taper_at_least_one(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            if is_maintenance_goal(gt):
                assert p.taper_weeks == 0, f"{gt.value}: taper_weeks != 0"
                continue
            assert p.taper_weeks >= 1, f"{gt.value}: taper_weeks < 1"

    def test_min_horizon_sufficient(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            assert p.min_horizon_weeks >= p.taper_weeks + 3, (
                f"{gt.value}: min_horizon too small for taper + base/build/peak"
            )

    def test_every_goal_carries_a_plan_maturity(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            assert isinstance(p.maturity, PlanMaturity), f"{gt.value}: no maturity"

    def test_maturity_grades_are_pinned_per_goal(self) -> None:
        """A re-grade must show up as a reviewed diff, not drift in silently.

        The criteria are on ``PlanMaturity``; ``test_maturity_audit.py``
        checks each goal's tier against its evidence.
        """
        by_level: dict[PlanMaturity, set[GoalType]] = {}
        for gt, p in GOAL_PROFILES.items():
            by_level.setdefault(p.maturity, set()).add(gt)
        # The goals with a tier on offer whose menus have been reviewed
        # session by session.
        alpha = {
            GoalType.RUNNING_HALF_MARATHON,
            GoalType.HALF_IRONMAN,
            GoalType.IRONMAN,
            GoalType.TRAIL_20K,
            GoalType.TRAIL_MAINTENANCE,
            GoalType.TRIATHLON_MAINTENANCE,
        }
        assert by_level == {
            PlanMaturity.ALPHA: alpha,
            PlanMaturity.EXPERIMENTAL: set(GOAL_PROFILES) - alpha,
        }

    def test_min_build_peak_positive(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            if is_maintenance_goal(gt):
                assert p.min_build_weeks == 0, f"{gt.value}: min_build_weeks != 0"
                assert p.min_peak_weeks == 0, f"{gt.value}: min_peak_weeks != 0"
                continue
            assert p.min_build_weeks >= 2, f"{gt.value}: min_build_weeks < 2"
            assert p.min_peak_weeks >= 1, f"{gt.value}: min_peak_weeks < 1"

    def test_running_goals_single_sport(self) -> None:
        running = [
            GoalType.RUNNING_5K,
            GoalType.RUNNING_10K,
            GoalType.RUNNING_HALF_MARATHON,
            GoalType.RUNNING_MARATHON,
        ]
        for gt in running:
            p = GOAL_PROFILES[gt]
            assert set(p.default_sport_split.keys()) == {SportType.RUNNING}

    def test_triathlon_goals_three_sports(self) -> None:
        tri = [
            GoalType.SPRINT_TRIATHLON,
            GoalType.OLYMPIC_TRIATHLON,
            GoalType.HALF_IRONMAN,
            GoalType.IRONMAN,
        ]
        for gt in tri:
            p = GOAL_PROFILES[gt]
            assert set(p.default_sport_split.keys()) == {
                SportType.SWIMMING,
                SportType.CYCLING,
                SportType.RUNNING,
            }

    def test_race_load_keys_match_split(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            if is_maintenance_goal(gt):
                assert p.race_duration == {}, f"{gt.value}: race_duration not empty"
                continue
            assert set(p.race_duration.keys()) == set(p.default_sport_split.keys()), (
                f"{gt.value}: race_duration keys don't match split keys"
            )

    def test_triathlon_phase_ratios_increase_base_with_distance(self) -> None:
        """Triathlon goals have distance-appropriate phase ratios.

        Shorter events (sprint) are VO2max-dominant and get less base.
        Longer events (ironman) are aerobic-dominant and get more base.
        """
        sprint = GOAL_PROFILES[GoalType.SPRINT_TRIATHLON].phase_ratios
        olympic = GOAL_PROFILES[GoalType.OLYMPIC_TRIATHLON].phase_ratios
        him = GOAL_PROFILES[GoalType.HALF_IRONMAN].phase_ratios
        im = GOAL_PROFILES[GoalType.IRONMAN].phase_ratios
        # Base ratio increases with distance.
        assert sprint[0] <= olympic[0] <= him[0] <= im[0]
        # Build ratio decreases with distance (or stays equal).
        assert sprint[1] >= olympic[1] >= him[1] >= im[1]


class TestValidatePhaseRequest:
    """Verify hard rejections and soft warnings."""

    def test_no_a_goal(self) -> None:
        req = _request(_bc_goal())
        result = validate_phase_request(req)
        assert not result.valid
        assert any("this one has none" in e for e in result.errors)

    def test_two_a_goals(self) -> None:
        req = _request(
            _a_goal(GoalType.RUNNING_MARATHON, 16),
            RaceGoal(GoalType.RUNNING_10K, _MONDAY + timedelta(weeks=20), "A"),
        )
        result = validate_phase_request(req)
        assert not result.valid

    def test_race_before_training_start(self) -> None:
        past = RaceGoal(GoalType.RUNNING_5K, _MONDAY - timedelta(days=1), "A")
        result = validate_phase_request(_request(past))
        assert not result.valid

    def test_bc_after_a_race(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        b = _bc_goal(GoalType.RUNNING_10K, 20)  # after A-race
        result = validate_phase_request(_request(a, b))
        assert not result.valid
        assert any("is not before your A race" in e for e in result.errors)

    def test_below_min_horizon(self) -> None:
        # Marathon min horizon = 12, give 8 weeks.
        a = _a_goal(GoalType.RUNNING_MARATHON, 8)
        result = validate_phase_request(_request(a))
        assert not result.valid
        assert any("at least 12 weeks" in e for e in result.errors)

    def test_bc_within_2_wk_of_a_race(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        b = _bc_goal(GoalType.RUNNING_5K, 15)  # 1 week before A-race
        result = validate_phase_request(_request(a, b))
        assert not result.valid
        assert any("peak and taper" in e for e in result.errors)

    def test_two_bc_same_week(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b1 = _bc_goal(GoalType.RUNNING_5K, 10, "B")
        b2 = _bc_goal(GoalType.RUNNING_10K, 10, "C")
        result = validate_phase_request(_request(a, b1, b2))
        assert not result.valid
        assert any("same week" in e for e in result.errors)

    def test_bc_in_first_3_weeks(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        c = _bc_goal(GoalType.RUNNING_5K, 2, "C")
        result = validate_phase_request(_request(a, c))
        assert not result.valid
        assert any("opening weeks" in e for e in result.errors)

    def test_bc_windows_exceed_40_percent(self) -> None:
        # Marathon 16wk, taper=3 -> pre-taper=14, 40% = 5.6. Each tune-up
        # costs its race week, so six race weeks exceed the budget.
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        tune_ups = [_bc_goal(GoalType.RUNNING_5K, k, "C") for k in range(3, 9)]
        result = validate_phase_request(_request(a, *tune_ups))
        assert not result.valid
        assert any("at most 5" in e for e in result.errors)

    def test_valid_a_goal_only(self) -> None:
        result = validate_phase_request(
            _request(_a_goal(GoalType.RUNNING_MARATHON, 16))
        )
        assert result.valid

    def test_valid_a_plus_b(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        result = validate_phase_request(_request(a, b))
        assert result.valid

    def test_valid_a_plus_c(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        c = _bc_goal(GoalType.RUNNING_5K, 8, "C")
        result = validate_phase_request(_request(a, c))
        assert result.valid

    def test_long_horizon_5k_warns_with_maintenance_suggestion(self) -> None:
        # 5K ceiling = 16; race 16 wk out -> 17-week horizon.
        a = _a_goal(GoalType.RUNNING_5K, 16)
        result = validate_phase_request(_request(a))
        assert result.valid
        assert any("runs 17 weeks" in w for w in result.warnings)
        assert any("maintenance block" in w for w in result.warnings)

    def test_long_horizon_ironman_warns_above_ceiling(self) -> None:
        # Ironman ceiling = 36; race 36 wk out -> 37-week horizon.
        a = _a_goal(GoalType.IRONMAN, 36)
        result = validate_phase_request(_request(a))
        assert result.valid
        assert any("maintenance block" in w for w in result.warnings)

    def test_horizon_at_ceiling_does_not_warn(self) -> None:
        # Race 35 wk out -> 36-week horizon, exactly the Ironman ceiling.
        a = _a_goal(GoalType.IRONMAN, 35)
        result = validate_phase_request(_request(a))
        assert result.valid
        assert result.warnings == []

    def test_maintenance_goal_has_no_horizon_ceiling(self) -> None:
        a = _a_goal(GoalType.RUNNING_MAINTENANCE, 19)
        result = validate_phase_request(_request(a))
        assert result.valid
        assert result.warnings == []

    def test_replan_suppresses_long_horizon_warning(self) -> None:
        """Mid-prep, "start with a maintenance block" is no longer actionable."""
        a = _a_goal(GoalType.RUNNING_5K, 16)
        req = PhaseRequest(
            goals=[a],
            training_start=_MONDAY + timedelta(weeks=4),
            prior_training_weeks=4,
        )
        result = validate_phase_request(req)
        assert result.valid
        assert result.warnings == []

    def test_early_b_goal_does_not_warn(self) -> None:
        # An early B race costs only its race week; no sharpening block
        # disrupts the aerobic base any more (R-PHASE-017).
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b = _bc_goal(GoalType.RUNNING_5K, 5, "B")
        result = validate_phase_request(_request(a, b))
        assert result.valid
        assert result.warnings == []

    def test_b_goal_different_sport_warning(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.SPRINT_TRIATHLON, 10, "B")
        result = validate_phase_request(_request(a, b))
        assert result.valid
        assert any("not the same sport" in w for w in result.warnings)

    def test_three_secondary_goals_warning(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 30)
        b1 = _bc_goal(GoalType.RUNNING_5K, 8, "B")
        c1 = _bc_goal(GoalType.RUNNING_5K, 14, "C")
        c2 = _bc_goal(GoalType.RUNNING_5K, 20, "C")
        result = validate_phase_request(_request(a, b1, c1, c2))
        assert result.valid
        assert any("3 tune-up races" in w for w in result.warnings)

    def test_no_warnings_clean_request(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        result = validate_phase_request(_request(a))
        assert result.valid
        assert result.warnings == []

    def test_horizon_exceeds_max(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 53)
        result = validate_phase_request(_request(a))
        assert not result.valid
        assert any("at most 52" in e for e in result.errors)

    def test_bc_in_a_goal_peak_zone_rejected(self) -> None:
        # B-goal in the A-goal peak/taper zone must be rejected at
        # validation time, not silently skipped.
        a = _a_goal(GoalType.RUNNING_5K, 12)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        result = validate_phase_request(_request(a, b))
        assert not result.valid
        assert any("peak and taper" in e for e in result.errors)

    def test_bc_races_in_consecutive_weeks_accepted(self) -> None:
        # A tune-up's window is its race week, so back-to-back weeks no
        # longer overlap; only a shared week is rejected (check 6).
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b1 = _bc_goal(GoalType.RUNNING_10K, 4, "B")
        b2 = _bc_goal(GoalType.RUNNING_10K, 5, "B")
        result = validate_phase_request(_request(a, b1, b2))
        assert result.valid


#: What a refusal must not say: the wizard and the coach show it verbatim.
_PLANNER_SPEAK = (
    "A-goal",
    "B-goal",
    "C-goal",
    "B/C",
    " wk",
    "peak/taper",
    "pre-taper",
    "orizon",
    "%",
    "_",
    "ironman",
)


def _dates_that_fit(message: str) -> tuple[date, date]:
    """The first and last date a refusal says fit."""
    found = re.search(r"Any date from (.+?) to (.+?) fits\.", message)
    assert found is not None, message
    first, last = found.groups()
    return (
        datetime.strptime(first, "%b %d, %Y").date(),
        datetime.strptime(last, "%b %d, %Y").date(),
    )


class TestRefusalsSpeakToTheAthlete:
    """The plan wizard and the coach show a refusal verbatim."""

    def test_a_tune_up_in_the_final_weeks_says_which_dates_fit(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 17)
        assert validate_phase_request(_request(a, b)).errors == [
            "Your tune-up race on May 4, 2026 falls in week 18 of this plan, in "
            "the peak and taper before your A race on May 25, 2026, and those "
            "weeks stay free of other races. "
            "Any date from Jan 26, 2026 to Apr 19, 2026 fits."
        ]

    def test_a_tune_up_in_the_opening_weeks_says_which_dates_fit(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        c = _bc_goal(GoalType.RUNNING_5K, 1, "C")
        assert validate_phase_request(_request(a, c)).errors == [
            "Your tune-up race on Jan 12, 2026 falls in week 2 of this plan, too "
            "soon after it starts: the opening weeks are kept for training. "
            "Any date from Jan 26, 2026 to Apr 19, 2026 fits."
        ]

    def test_a_short_notice_plan_skips_the_weeks_it_joined_past(self) -> None:
        """Week 1 is the first week the athlete trains, not the virtual one."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 7)
        b = _bc_goal(GoalType.RUNNING_10K, 6)
        result = validate_phase_request(
            PhaseRequest(goals=[a, b], training_start=_MONDAY, horizon_join_weeks=4)
        )
        assert "falls in week 7 of this plan" in result.errors[0]

    def test_a_replan_counts_the_weeks_already_trained(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 9)
        b = _bc_goal(GoalType.RUNNING_10K, 8)
        result = validate_phase_request(
            PhaseRequest(goals=[a, b], training_start=_MONDAY, prior_training_weeks=10)
        )
        assert "falls in week 19 of this plan" in result.errors[0]

    @pytest.mark.parametrize(
        ("weeks_from_start", "reason"),
        [
            (-1, "is not after this plan's start on Jan 5, 2026"),
            (0, "is not after this plan's start on Jan 5, 2026"),
            (20, "is not before your A race on May 25, 2026"),
            (24, "is not before your A race on May 25, 2026"),
        ],
    )
    def test_a_tune_up_gets_one_reason(
        self, weeks_from_start: int, reason: str
    ) -> None:
        """A tune-up past the A race used to read as three separate refusals."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, weeks_from_start)
        errors = validate_phase_request(_request(a, b)).errors
        assert len(errors) == 1
        assert reason in errors[0]
        assert _dates_that_fit(errors[0]) == (date(2026, 1, 26), date(2026, 4, 19))

    def test_a_tune_up_past_a_maintenance_block_names_the_block_end(self) -> None:
        a = _a_goal(GoalType.RUNNING_MAINTENANCE, 7)
        b = _bc_goal(GoalType.RUNNING_10K, 8)
        errors = validate_phase_request(_request(a, b)).errors
        assert errors == [
            "Your tune-up race on Mar 2, 2026 is not before this block ends on "
            "Feb 23, 2026. Any date from Jan 6, 2026 to Feb 22, 2026 fits."
        ]

    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize(("prior", "joined"), [(0, 0), (0, 2), (5, 0), (3, 2)])
    def test_the_dates_a_refusal_names_are_the_dates_accepted(
        self, ambition: AmbitionLevel, prior: int, joined: int
    ) -> None:
        """Both ends fit, the day beyond each is refused, across every goal."""

        def refusals(a: RaceGoal, day: date) -> list[str]:
            return validate_phase_request(
                PhaseRequest(
                    goals=[a, RaceGoal(GoalType.RUNNING_10K, day, "C")],
                    training_start=_MONDAY,
                    ambition=ambition,
                    prior_training_weeks=prior,
                    horizon_join_weeks=joined,
                )
            ).errors

        for goal_type, profile in GOAL_PROFILES.items():
            for horizon in (profile.min_horizon_weeks, profile.min_horizon_weeks + 7):
                race = _MONDAY + timedelta(weeks=horizon - prior - joined - 1, days=6)
                if race <= _MONDAY:
                    continue
                a = RaceGoal(goal_type, race, "A")
                message = refusals(a, race)
                assert len(message) == 1, (goal_type, horizon)
                if "no week free" in message[0]:
                    # On a replan the start day itself is history (R-PHASE-002b).
                    days = (race - _MONDAY).days
                    assert all(
                        refusals(a, _MONDAY + timedelta(days=d))
                        for d in range(1 if prior else 0, days)
                    )
                    continue
                first, last = _dates_that_fit(message[0])
                assert refusals(a, first) == [], (goal_type, horizon)
                assert refusals(a, last) == [], (goal_type, horizon)
                before = first - timedelta(days=1)
                if prior and before <= _MONDAY:
                    # A replan keeps a tune-up on or before its start as
                    # history instead of refusing it (R-PHASE-002b).
                    assert refusals(a, before) == [], (goal_type, horizon)
                else:
                    assert refusals(a, before), (goal_type, horizon)
                assert refusals(a, last + timedelta(days=1)), (goal_type, horizon)

    def test_a_plan_with_no_free_week_says_so(self) -> None:
        """Naming dates here would name dates that are refused."""
        a = _a_goal(GoalType.RUNNING_5K, 3)
        b = _bc_goal(GoalType.RUNNING_10K, 2)
        assert validate_phase_request(_request(a, b)).errors == [
            "Your tune-up race on Jan 19, 2026 falls in week 3 of this plan, in "
            "the peak and taper before your A race on Jan 26, 2026, and those "
            "weeks stay free of other races. This plan has no week free for a "
            "tune-up race, so take it out or pick a later A race."
        ]

    def test_two_tune_ups_in_a_week_are_named(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_5K, 10, "B")
        c = RaceGoal(GoalType.RUNNING_10K, b.race_date + timedelta(days=1), "C")
        assert validate_phase_request(_request(a, b, c)).errors == [
            "Your tune-up races on Mar 16, 2026 and Mar 17, 2026 fall in the same "
            "week. A plan takes one race a week, so move one to another week."
        ]

    def test_an_a_race_too_soon_says_when_it_would_fit(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 7)
        assert validate_phase_request(_request(a)).errors == [
            "A preparation for this race takes at least 12 weeks and this one "
            "has 8. Any date from Mar 23, 2026 on fits."
        ]

    def test_an_a_race_before_the_start_is_not_a_negative_horizon(self) -> None:
        a = RaceGoal(GoalType.RUNNING_MARATHON, _MONDAY - timedelta(days=30), "A")
        assert validate_phase_request(_request(a)).errors == [
            "Your race on Dec 6, 2025 is not after this plan's start on "
            "Jan 5, 2026. Pick a later date."
        ]

    def test_an_a_race_too_far_out_says_what_fits(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 53)
        assert validate_phase_request(_request(a)).errors == [
            "This plan would run 54 weeks and a plan runs at most 52. Any date up "
            "to Jan 3, 2027 fits, or start with a maintenance block and build the "
            "race plan later."
        ]

    def test_too_many_tune_ups_says_how_many_fit(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        tune_ups = [_bc_goal(GoalType.RUNNING_5K, k, "C") for k in range(3, 9)]
        assert validate_phase_request(_request(a, *tune_ups)).errors == [
            "This plan has 6 tune-up races and can take at most 5. Take some out "
            "or give the plan more weeks."
        ]

    def test_a_plan_without_an_a_race_says_to_make_one(self) -> None:
        b = _bc_goal(GoalType.RUNNING_MARATHON, 20)
        assert validate_phase_request(_request(b)).errors == [
            "A plan builds toward one A race and this one has none. Make the race "
            "you are building toward your A race."
        ]

    def test_two_a_races_are_named(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        other = _a_goal(GoalType.RUNNING_10K, 22)
        assert validate_phase_request(_request(a, other)).errors == [
            "A plan builds toward one A race and this one has 2, on May 25, 2026 "
            "and Jun 8, 2026. Keep the race you are building toward as your A race "
            "and make the rest tune-up races."
        ]

    def test_a_maintenance_tune_up_is_named_by_its_end(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        block = _bc_goal(GoalType.RUNNING_MAINTENANCE, 8)
        assert validate_phase_request(_request(a, block)).errors == [
            "A maintenance block is a plan of its own, so the one ending on "
            "Mar 2, 2026 cannot be a tune-up race. Take it out of this plan."
        ]

    def test_refusals_never_speak_planner(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        requests = [
            _request(a, _bc_goal(GoalType.RUNNING_10K, k)) for k in (-1, 1, 17, 19, 20)
        ] + [
            _request(_a_goal(GoalType.RUNNING_MARATHON, 7)),
            _request(_a_goal(GoalType.IRONMAN, 60)),
            _request(_a_goal(GoalType.RUNNING_MAINTENANCE, 2)),
            _request(
                a, _bc_goal(GoalType.RUNNING_5K, 8), _bc_goal(GoalType.TRAIL_20K, 8)
            ),
            _request(a, *[_bc_goal(GoalType.RUNNING_5K, k, "C") for k in range(3, 11)]),
            _request(_bc_goal(GoalType.HALF_IRONMAN, 20)),
            _request(a, _a_goal(GoalType.HALF_IRONMAN, 22)),
            _request(a, _bc_goal(GoalType.TRIATHLON_MAINTENANCE, 8)),
        ]
        for request in requests:
            errors = validate_phase_request(request).errors
            assert errors
            for error in errors:
                assert not [w for w in _PLANNER_SPEAK if w in error], error

    def test_refusals_join_as_sentences(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        early = _bc_goal(GoalType.RUNNING_5K, 1, "C")
        late = _bc_goal(GoalType.RUNNING_10K, 17)
        with pytest.raises(ValueError, match=r"fits\. Your tune-up race on May 4"):
            plan_phases(_request(a, early, late))


class TestWarningsSpeakToTheAthlete:
    """The plan preview, the plan's brief and the coach show a warning verbatim."""

    def test_a_long_plan_says_when_to_begin_the_race_plan(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 36)
        assert validate_phase_request(_request(a)).warnings == [
            "This plan runs 37 weeks, and a race preparation works best within 36. "
            "Begin the race plan on Jan 12, 2026, with a maintenance block before "
            "it, or add a B race along the way."
        ]

    def test_the_named_start_gives_the_recommended_length(self) -> None:
        for goal_type, profile in GOAL_PROFILES.items():
            if profile.max_recommended_weeks is None:
                continue
            a = _a_goal(goal_type, profile.max_recommended_weeks + 3)
            found = re.search(
                r"Begin the race plan on (.+?), with",
                validate_phase_request(_request(a)).warnings[0],
            )
            assert found is not None, goal_type
            start = datetime.strptime(found.group(1), "%b %d, %Y").date()
            later = validate_phase_request(_request(a, start=start))
            assert later.warnings == [], goal_type
            earlier = validate_phase_request(
                _request(a, start=start - timedelta(weeks=1))
            )
            assert earlier.warnings, goal_type

    def test_a_b_race_in_another_sport_is_named(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.SPRINT_TRIATHLON, 10, "B")
        assert validate_phase_request(_request(a, b)).warnings == [
            "Your B race on Mar 16, 2026 and your A race are not the same sport, so "
            "the B race does less to prepare you for your A race."
        ]

    def test_more_than_two_tune_ups_says_what_they_cost(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        tune_ups = [_bc_goal(GoalType.RUNNING_5K, k, "C") for k in (4, 8, 12)]
        assert validate_phase_request(_request(a, *tune_ups)).warnings == [
            "This plan has 3 tune-up races. Each one takes over its own week, and "
            "more than two break up the build, so keep the ones that matter most."
        ]

    def test_warnings_never_speak_planner(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 40)
        tune_ups = [
            _bc_goal(GoalType.RUNNING_HALF_MARATHON, 8, "B"),
            _bc_goal(GoalType.RUNNING_5K, 14, "C"),
            _bc_goal(GoalType.RUNNING_5K, 20, "C"),
        ]
        warnings = validate_phase_request(_request(a, *tune_ups)).warnings
        assert len(warnings) == 3
        for warning in warnings:
            assert not [w for w in _PLANNER_SPEAK if w in warning], warning


class TestAllocatePhases:
    """Verify the phase-ratio allocation logic."""

    def test_sums_to_total(self) -> None:
        for gt, p in GOAL_PROFILES.items():
            total_pre = 20
            base, build, peak = _allocate_phases(
                total_pre, p.phase_ratios, p.min_build_weeks, p.min_peak_weeks
            )
            assert base + build + peak == total_pre, f"{gt.value}: sum mismatch"

    def test_respects_minimums(self) -> None:
        _base, build, peak = _allocate_phases(5, (0.5, 0.3, 0.2), 2, 1)
        assert build >= 2
        assert peak >= 1

    def test_small_total(self) -> None:
        base, build, peak = _allocate_phases(3, (0.4, 0.4, 0.2), 2, 1)
        assert build >= 2
        assert peak >= 1
        assert base + build + peak == 3

    def test_zero_total(self) -> None:
        base, _build, _peak = _allocate_phases(0, (0.4, 0.4, 0.2), 2, 1)
        assert base == 0

    def test_very_tight_needs_steal_from_build(self) -> None:
        # Total = 4, min_build=2, min_peak=2 -> base=0 after steal.
        base, build, peak = _allocate_phases(4, (0.5, 0.3, 0.2), 2, 2)
        assert build >= 2
        assert peak >= 2
        assert base + build + peak == 4

    def test_rounding_excess_steals_base_first(self) -> None:
        # Ratios that round up: 0.34*10=3.4->3, 0.33*10=3.3->3, 0.33*10=3.3->3
        # Sum=9 < 10, so build gets +1. Then verify base absorbs remainder.
        base, build, peak = _allocate_phases(10, (0.34, 0.33, 0.33), 2, 1)
        assert base + build + peak == 10
        assert build >= 2
        assert peak >= 1

    def test_rounding_over_steals_from_build_then_peak(self) -> None:
        # Ratios that round high: (0.5, 0.3, 0.2) on 3 -> base=2, build=1, peak=1
        # Sum=4 > 3. First steal from base (2->1), still 3 = 3. OK.
        # But with (0.1, 0.5, 0.4) on 3 -> base=0, build=2, peak=1 = 3.
        # Test that min_build/min_peak are respected.
        base, build, peak = _allocate_phases(3, (0.1, 0.5, 0.4), 2, 1)
        assert base + build + peak == 3
        assert build >= 2
        assert peak >= 1

    def test_steal_from_peak(self) -> None:
        # Force peak reduction: very high peak ratio, tight total.
        base, build, peak = _allocate_phases(4, (0.1, 0.1, 0.8), 2, 1)
        assert build >= 2
        assert peak >= 1
        assert base + build + peak == 4

    def test_steal_both_at_min_breaks(self) -> None:
        # When minimums exceed total, base can't go below 0.
        # min_build=2 + min_peak=2 = 4 > 3 total.
        base, build, peak = _allocate_phases(3, (0.0, 0.5, 0.5), 2, 2)
        assert base == 0
        assert build >= 2
        assert peak >= 2

    def test_steal_from_build_when_base_zero(self) -> None:
        # Force base negative, then steal from build (build > min_build).
        # total=5, ratios (0.01, 0.7, 0.29) -> round: base=0, build=4, peak=1
        # After enforce min: build=max(4,2)=4, peak=max(1,2)=2, base=5-4-2=-1
        # Steal: build>2 -> build=3, base=0. Sum=0+3+2=5.
        base, build, peak = _allocate_phases(5, (0.01, 0.70, 0.29), 2, 2)
        assert base + build + peak == 5
        assert build >= 2
        assert peak >= 2

    def test_steal_from_peak_when_build_at_min(self) -> None:
        # total=5, ratios (0.01, 0.29, 0.70) -> round: base=0, build=1, peak=4
        # After enforce min: build=max(1,2)=2, peak=max(4,2)=4, base=5-2-4=-1
        # Steal: build>2? no. peak>2 -> peak=3, base=0. Sum=0+2+3=5.
        base, build, peak = _allocate_phases(5, (0.01, 0.29, 0.70), 2, 2)
        assert base + build + peak == 5
        assert build >= 2
        assert peak >= 2

    def test_reconcile_over_steals_build_in_while(self) -> None:
        # Force the "elif build > min_build: build -= 1" in the reconciliation
        # while loop (not the steal-from-minimums loop).
        # With ratios that round high and base rounds to 0:
        # (0.0, 0.7, 0.3) on total=3 -> base=0, build=round(2.1)=2, peak=round(0.9)=1
        # Sum = 3, exact. That doesn't trigger the branch.
        # Try: (0.0, 0.8, 0.2) on total=4 -> base=0, build=3, peak=1 = 4. Exact.
        # Need sum > total with base=0: (0.0, 0.6, 0.6) on total=5
        # -> base=0, build=round(3.0)=3, peak=round(3.0)=3 = 6 > 5
        # Loop: base=0 can't steal, build(3)>min(1) -> build=2. 0+2+3=5. Done.
        base, build, peak = _allocate_phases(5, (0.0, 0.60, 0.60), 1, 1)
        assert base + build + peak == 5
        assert build >= 1
        assert peak >= 1


class TestPeakLengthByHorizon:
    """A longer plan never has a shorter peak (R-PHASE-006)."""

    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize(
        "goal", [g for g in GoalType if not is_maintenance_goal(g)]
    )
    def test_peak_never_shrinks_within_the_recommended_horizons(
        self, goal: GoalType, ambition: AmbitionLevel
    ) -> None:
        profile = GOAL_PROFILES[goal]
        assert profile.max_recommended_weeks is not None
        peaks = [
            sum(
                w.phase == PhaseType.PEAK
                for w in plan_phases(
                    PhaseRequest(
                        goals=[_a_goal(goal, total - 1)],
                        training_start=_MONDAY,
                        ambition=ambition,
                    )
                ).weeks
            )
            for total in range(
                profile.min_horizon_weeks, profile.max_recommended_weeks + 1
            )
        ]
        assert peaks == sorted(peaks)

    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize("total", range(29, 37))
    def test_half_eight_weeks_before_an_ironman_fits_every_five_week_peak(
        self, total: int, ambition: AmbitionLevel
    ) -> None:
        result = validate_phase_request(
            PhaseRequest(
                goals=[
                    _a_goal(GoalType.IRONMAN, total - 1),
                    _bc_goal(GoalType.HALF_IRONMAN, total - 9),
                ],
                training_start=_MONDAY,
                ambition=ambition,
            )
        )
        assert result.valid, result.errors


class TestBuildASkeleton:
    """Verify the A-goal skeleton builder."""

    @pytest.mark.parametrize("goal_type", list(GoalType))
    def test_skeleton_length_matches_total_weeks(self, goal_type: GoalType) -> None:
        profile = GOAL_PROFILES[goal_type]
        total_weeks = profile.min_horizon_weeks + 4  # some headroom
        a = RaceGoal(
            goal_type=goal_type,
            race_date=_MONDAY + timedelta(weeks=total_weeks),
            priority="A",
        )
        skeleton = _build_a_skeleton(a, _MONDAY, total_weeks)
        assert len(skeleton) == total_weeks

    @pytest.mark.parametrize("goal_type", list(GoalType))
    def test_last_week_is_race(self, goal_type: GoalType) -> None:
        profile = GOAL_PROFILES[goal_type]
        total_weeks = profile.min_horizon_weeks + 4
        a = RaceGoal(
            goal_type=goal_type,
            race_date=_MONDAY + timedelta(weeks=total_weeks),
            priority="A",
        )
        skeleton = _build_a_skeleton(a, _MONDAY, total_weeks)
        assert skeleton[-1].mode == WeekMode.RACE

    def test_phase_ordering_monotonic(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        skeleton = _build_a_skeleton(a, _MONDAY, 20)
        phase_order = {
            PhaseType.BASE: 0,
            PhaseType.BUILD: 1,
            PhaseType.PEAK: 2,
            PhaseType.TAPER: 3,
        }
        prev = -1
        for w in skeleton:
            curr = phase_order[w.phase]
            assert curr >= prev, f"Week {w.week}: {w.phase} regresses"
            prev = curr

    def test_taper_weeks_match_profile(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        skeleton = _build_a_skeleton(a, _MONDAY, 20)
        taper_count = sum(1 for w in skeleton if w.phase == PhaseType.TAPER)
        assert taper_count == GOAL_PROFILES[GoalType.RUNNING_MARATHON].taper_weeks

    def test_all_weeks_have_a_goal_effective_goal(self) -> None:
        a = _a_goal(GoalType.RUNNING_10K, 10)
        skeleton = _build_a_skeleton(a, _MONDAY, 10)
        for w in skeleton:
            assert w.effective_goal == GoalType.RUNNING_10K


class TestPlanPhasesAGoalOnly:
    """End-to-end tests with a single A-goal."""

    @pytest.mark.parametrize("goal_type", list(GoalType))
    def test_all_goal_types_at_min_horizon(self, goal_type: GoalType) -> None:
        profile = GOAL_PROFILES[goal_type]
        a = _a_goal(goal_type, profile.min_horizon_weeks)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        # race at week N -> plan has N+1 weeks [0..N]
        assert len(plan.weeks) == profile.min_horizon_weeks + 1

    @pytest.mark.parametrize("goal_type", list(GoalType))
    def test_all_goal_types_at_double_horizon(self, goal_type: GoalType) -> None:
        profile = GOAL_PROFILES[goal_type]
        weeks = min(profile.min_horizon_weeks * 2, 36)
        a = _a_goal(goal_type, weeks)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)

    def test_exactly_one_race_event(self) -> None:
        req = _request(_a_goal(GoalType.RUNNING_MARATHON, 16))
        plan = plan_phases(req)
        assert len(plan.race_events) == 1
        assert plan.race_events[0].goal == plan.a_goal

    def test_race_event_week_is_last(self) -> None:
        req = _request(_a_goal(GoalType.RUNNING_MARATHON, 16))
        plan = plan_phases(req)
        assert plan.race_events[0].week == len(plan.weeks) - 1

    def test_race_event_day_matches_date(self) -> None:
        race_date = _MONDAY + timedelta(weeks=16)
        a = RaceGoal(GoalType.RUNNING_MARATHON, race_date, "A")
        plan = plan_phases(_request(a))
        assert plan.race_events[0].day_of_week == race_date.weekday()

    def test_race_event_week_maps_to_correct_date(self) -> None:
        # training_start=2026-01-05, race_date=2026-04-27 -> 112 days -> week 16
        start = date(2026, 1, 5)
        race_date = date(2026, 4, 27)
        a = RaceGoal(GoalType.RUNNING_MARATHON, race_date, "A")
        plan = plan_phases(PhaseRequest(goals=[a], training_start=start))
        a_event = next(e for e in plan.race_events if e.goal.priority == "A")
        # Week 16 starts on 2026-01-05 + 16*7 = 2026-04-27, which is race day.
        mapped_date = start + timedelta(weeks=a_event.week)
        assert mapped_date == race_date

    def test_recovery_every_4th_load_week(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        # Verify recovery weeks are present in the plan.
        recovery_weeks = [w for w in plan.weeks if w.mode == WeekMode.RECOVERY]
        assert len(recovery_weeks) > 0
        # The only LOAD run exceeding 4 may be the final one before taper
        # (taper suppression prevents recovery there).
        load_runs: list[int] = []
        consecutive = 0
        for w in plan.weeks:
            if w.mode == WeekMode.LOAD:
                consecutive += 1
            else:
                if consecutive > 0:
                    load_runs.append(consecutive)
                consecutive = 0
        if consecutive > 0:
            load_runs.append(consecutive)
        # All non-final runs must respect the cadence.
        for run_len in load_runs[:-1]:
            assert run_len <= get_ambition_profile(req.ambition).recovery_cadence

    def test_no_recovery_near_taper_except_peak(self) -> None:
        """Non-PEAK recovery is suppressed near taper; PEAK recovery is allowed."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        plan = plan_phases(_request(a))
        taper_start = next(
            w.week for w in plan.weeks if w.mode in (WeekMode.TAPER, WeekMode.RACE)
        )
        for w in plan.weeks:
            if w.mode == WeekMode.RECOVERY and w.phase != PhaseType.PEAK:
                assert taper_start - w.week > TAPER_SUPPRESSION_WEEKS

    def test_sport_split_sums_to_one(self) -> None:
        req = _request(_a_goal(GoalType.IRONMAN, 22))
        plan = plan_phases(req)
        for w in plan.weeks:
            total = sum(w.sport_split.values())
            assert abs(total - 1.0) < 0.01

    def test_triathlon_has_three_sports(self) -> None:
        req = _request(_a_goal(GoalType.OLYMPIC_TRIATHLON, 16))
        plan = plan_phases(req)
        for w in plan.weeks:
            assert len(w.sport_split) == 3

    def test_gap_free_weeks(self) -> None:
        req = _request(_a_goal(GoalType.RUNNING_10K, 12))
        plan = plan_phases(req)
        indices = [w.week for w in plan.weeks]
        assert indices == list(range(len(plan.weeks)))


class TestPlanPhasesBCGoals:
    """End-to-end tests with secondary goals."""

    def test_a_plus_b_produces_two_race_events(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.race_events) == 2

    def test_b_race_week_is_race_mode(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        plan = plan_phases(_request(a, b))
        b_event = next(e for e in plan.race_events if e.goal.priority == "B")
        assert plan.weeks[b_event.week].mode == WeekMode.RACE

    def test_a_preparation_keeps_its_phase_around_a_b_race(self) -> None:
        # R-PHASE-010: a B race costs the A preparation only its race week.
        # No sharpening PEAK, no separate taper week, no race recovery: the
        # weeks around it train straight through in their A-goal phase.
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b = _bc_goal(GoalType.RUNNING_HALF_MARATHON, 12, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        a_only = plan_phases(_request(a))
        k = next(e.week for e in plan.race_events if e.goal.priority == "B")

        race_week = plan.weeks[k]
        assert (race_week.phase, race_week.mode) == (PhaseType.TAPER, WeekMode.RACE)
        assert race_week.effective_goal == GoalType.RUNNING_HALF_MARATHON
        for week, alone in zip(plan.weeks, a_only.weeks, strict=True):
            if week.week != k:
                assert week.phase == alone.phase
                assert week.effective_goal == GoalType.RUNNING_MARATHON
                assert week.owning_goal is None
        assert plan.weeks[k - 1].mode == WeekMode.LOAD
        assert plan.weeks[k + 1].mode == WeekMode.LOAD
        assert WeekMode.RACE_RECOVERY not in {w.mode for w in plan.weeks}

    def test_a_long_b_race_is_followed_by_one_easier_a_goal_week(self) -> None:
        # R-PHASE-010: a B race over B_RACE_RECOVERY_MIN_HOURS (a marathon,
        # 4.5 h) costs one RACE_RECOVERY week on the A goal's own phase,
        # menus and split; the week after it trains again.
        a = _a_goal(GoalType.TRAIL_50K, 24)
        b = _bc_goal(GoalType.RUNNING_MARATHON, 8, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        a_only = plan_phases(_request(a))
        k = next(e.week for e in plan.race_events if e.goal.priority == "B")

        after, alone = plan.weeks[k + 1], a_only.weeks[k + 1]
        assert after.mode == WeekMode.RACE_RECOVERY
        assert (after.phase, after.effective_goal, after.owning_goal) == (
            alone.phase,
            GoalType.TRAIL_50K,
            None,
        )
        assert after.sport_split == alone.sport_split
        assert plan.weeks[k + 2].mode == WeekMode.LOAD
        assert [w.mode for w in plan.weeks].count(WeekMode.RACE_RECOVERY) == 1

    def test_no_race_recovery_after_a_c_race(self) -> None:
        a = _a_goal(GoalType.TRAIL_50K, 24)
        b = _bc_goal(GoalType.RUNNING_MARATHON, 8, "C")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.race_events) == 2
        assert WeekMode.RACE_RECOVERY not in {w.mode for w in plan.weeks}

    def test_an_easier_week_on_the_first_peak_week_starts_the_peak_later(self) -> None:
        # R-PHASE-010: a long B race on the last week before the A peak still
        # gets its easier week. It stays a build week, so the peak's own
        # sessions keep their places and only the peak's length gives way.
        a = _a_goal(GoalType.TRAIL_50K, 24)
        a_only = plan_phases(_request(a))
        first_peak = next(w.week for w in a_only.weeks if w.phase == PhaseType.PEAK)
        last_build = a_only.weeks[first_peak - 2]
        b = _bc_goal(GoalType.RUNNING_MARATHON, first_peak - 1, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)

        easier = plan.weeks[first_peak]
        assert easier.mode == WeekMode.RACE_RECOVERY
        assert (easier.phase, easier.effective_goal, easier.owning_goal) == (
            PhaseType.BUILD,
            GoalType.TRAIL_50K,
            None,
        )
        assert easier.sport_split == last_build.sport_split
        peak = [w.week for w in plan.weeks if w.phase == PhaseType.PEAK]
        peak_alone = [w.week for w in a_only.weeks if w.phase == PhaseType.PEAK]
        assert peak == peak_alone[1:]
        assert [w.mode for w in plan.weeks].count(WeekMode.RACE_RECOVERY) == 1
        taper = [w.week for w in plan.weeks[first_peak:] if w.phase == PhaseType.TAPER]
        assert taper == [w.week for w in a_only.weeks if w.phase == PhaseType.TAPER]

    @pytest.mark.parametrize("weeks", [29, 31, 33, 36])
    def test_a_half_right_before_a_five_week_ironman_peak(self, weeks: int) -> None:
        # The closing weeks a shorter plan with a four-week peak already has:
        # race, easier week, three load weeks, recovery, taper.
        a = _a_goal(GoalType.IRONMAN, weeks - 1)
        b = _bc_goal(GoalType.HALF_IRONMAN, weeks - 9, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        closing = plan.weeks[-9:]
        assert [w.mode for w in closing] == [
            WeekMode.RACE,
            WeekMode.RACE_RECOVERY,
            WeekMode.LOAD,
            WeekMode.LOAD,
            WeekMode.LOAD,
            WeekMode.RECOVERY,
            WeekMode.TAPER,
            WeekMode.TAPER,
            WeekMode.RACE,
        ]
        assert [w.phase for w in closing[1:6]] == [
            PhaseType.BUILD,
            PhaseType.PEAK,
            PhaseType.PEAK,
            PhaseType.PEAK,
            PhaseType.PEAK,
        ]

    def test_an_easier_week_on_a_one_week_peak_stays_a_peak_week(self) -> None:
        # A 10K's short plan has one peak week; relabelling it would leave no
        # peak at all, so the easier week sits on the peak's own recovery menu.
        a = _a_goal(GoalType.RUNNING_10K, 7)
        peak = [
            w.week for w in plan_phases(_request(a)).weeks if w.phase == PhaseType.PEAK
        ]
        assert len(peak) == 1
        b = _bc_goal(GoalType.RUNNING_MARATHON, peak[0] - 1, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert plan.weeks[peak[0]].mode == WeekMode.RACE_RECOVERY
        assert plan.weeks[peak[0]].phase == PhaseType.PEAK

    def test_c_goal_shorter_window_than_b(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        c = _bc_goal(GoalType.RUNNING_5K, 16, "C")
        req = _request(a, b, c)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.race_events) == 3

    def test_sharpening_sets_effective_goal(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        plan = plan_phases(_request(a, b))
        b_event = next(e for e in plan.race_events if e.goal.priority == "B")
        # The race week should have the B-goal's effective_goal.
        assert plan.weeks[b_event.week].effective_goal == GoalType.RUNNING_10K

    def test_post_conditions_pass_multi_goal(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 24)
        b = _bc_goal(GoalType.OLYMPIC_TRIATHLON, 12, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)


class TestSportSplitBlending:
    """Verify sport split blending at sharpening overlaps."""

    def test_normalize_split(self) -> None:
        result = _normalize_split({SportType.RUNNING: 2.0, SportType.CYCLING: 3.0})
        assert abs(result[SportType.RUNNING] - 0.4) < 0.001
        assert abs(result[SportType.CYCLING] - 0.6) < 0.001

    def test_normalize_split_zero_total(self) -> None:
        result = _normalize_split({SportType.RUNNING: 0.0})
        assert result[SportType.RUNNING] == 0.0

    def test_running_a_with_tri_b_adds_swim_bike(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.SPRINT_TRIATHLON, 10, "B")
        plan = plan_phases(_request(a, b))
        b_event = next(e for e in plan.race_events if e.goal.priority == "B")
        # The race week's split carries the B race's swim and bike.
        race_week = plan.weeks[b_event.week]
        assert SportType.SWIMMING in race_week.sport_split
        assert SportType.CYCLING in race_week.sport_split

    def test_tri_a_with_running_b_shifts_toward_running(self) -> None:
        a = _a_goal(GoalType.OLYMPIC_TRIATHLON, 20)
        b = _bc_goal(GoalType.RUNNING_10K, 10, "B")
        plan = plan_phases(_request(a, b))
        b_event = next(e for e in plan.race_events if e.goal.priority == "B")
        # The race week leans toward running against its own A-goal week.
        race_week = plan.weeks[b_event.week]
        a_week = plan_phases(_request(a)).weeks[b_event.week]
        assert (
            race_week.sport_split[SportType.RUNNING]
            > a_week.sport_split[SportType.RUNNING]
        )

    def test_phase_splits_vary_across_phases(self) -> None:
        """Ironman plan should show phase-dependent sport splits."""
        a = _a_goal(GoalType.IRONMAN, 24)
        plan = plan_phases(_request(a))
        base_weeks = [w for w in plan.weeks if w.phase == PhaseType.BASE]
        peak_weeks = [
            w
            for w in plan.weeks
            if w.phase == PhaseType.PEAK and w.mode == WeekMode.LOAD
        ]
        assert base_weeks
        assert peak_weeks
        base_swim = base_weeks[0].sport_split[SportType.SWIMMING]
        peak_swim = peak_weeks[0].sport_split[SportType.SWIMMING]
        assert base_swim > peak_swim, (
            f"Ironman base swim ({base_swim}) should exceed peak swim ({peak_swim})"
        )

    def test_running_goals_unaffected_by_phase_splits(self) -> None:
        """Running goals remain {running: 1.0} regardless of phase."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        plan = plan_phases(_request(a))
        for w in plan.weeks:
            assert w.sport_split == {SportType.RUNNING: 1.0}, (
                f"Week {w.week}: expected running-only split, got {w.sport_split}"
            )


class TestRecoveryStamping:
    """Verify recovery week placement rules."""

    def test_no_recovery_in_sharpening(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        b = _bc_goal(GoalType.RUNNING_10K, 12, "B")
        plan = plan_phases(_request(a, b))
        # Weeks owned by B-goal should not be RECOVERY.
        for w in plan.weeks:
            if w.owning_goal and w.owning_goal.priority == "B":
                assert w.mode != WeekMode.RECOVERY

    def test_no_recovery_the_week_before_a_tune_up_race(self) -> None:
        # The cadence lands on week 7 of the A-only plan; a C race in
        # week 8 is that block's deload, so week 7 stays LOAD (R-PHASE-014).
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        assert plan_phases(_request(a)).weeks[7].mode == WeekMode.RECOVERY
        c = _bc_goal(GoalType.RUNNING_10K, 8, "C")
        req = _request(a, c)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert plan.weeks[7].mode == WeekMode.LOAD
        assert plan.weeks[8].mode == WeekMode.RACE

    def test_recovery_resets_counter(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 24)
        req = _request(a)
        plan = plan_phases(req)
        # Collect all LOAD runs.
        load_runs: list[int] = []
        consecutive = 0
        for w in plan.weeks:
            if w.mode == WeekMode.LOAD:
                consecutive += 1
            else:
                if consecutive > 0:
                    load_runs.append(consecutive)
                consecutive = 0
        if consecutive > 0:
            load_runs.append(consecutive)
        # All runs must respect the cadence.
        for run_len in load_runs:
            assert run_len <= get_ambition_profile(req.ambition).recovery_cadence

    def test_peak_phase_gets_recovery_in_long_plan(self) -> None:
        """Long peak blocks get recovery even near taper."""
        a = _a_goal(GoalType.IRONMAN, 36)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        peak_weeks = [w for w in plan.weeks if w.phase == PhaseType.PEAK]
        peak_recovery = [w for w in peak_weeks if w.mode == WeekMode.RECOVERY]
        assert len(peak_recovery) >= 1, (
            f"Peak phase ({len(peak_weeks)} weeks) has no recovery"
        )

    def test_base_build_still_suppressed_near_taper(self) -> None:
        """BASE/BUILD recovery is still suppressed near taper."""
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        plan = plan_phases(_request(a))
        taper_start = next(
            w.week for w in plan.weeks if w.mode in (WeekMode.TAPER, WeekMode.RACE)
        )
        for w in plan.weeks:
            if w.mode == WeekMode.RECOVERY and w.phase in (
                PhaseType.BASE,
                PhaseType.BUILD,
            ):
                assert taper_start - w.week > TAPER_SUPPRESSION_WEEKS


class TestShortTaperSuppression:
    """R-PHASE-015 before a taper under three weeks: a two-week window, PEAK included."""

    @staticmethod
    def _trail_20k(weeks: int) -> tuple[PhasePlan, PhaseRequest]:
        req = PhaseRequest(
            goals=[_a_goal(GoalType.TRAIL_20K, weeks)],
            training_start=_MONDAY,
            ambition=AmbitionLevel.BASIC,
        )
        return plan_phases(req), req

    @pytest.mark.parametrize("weeks", [12, 16, 20])
    def test_last_recovery_sits_three_weeks_before_the_taper(self, weeks: int) -> None:
        plan, req = self._trail_20k(weeks)
        _assert_post_conditions(plan, req)
        assert GOAL_PROFILES[GoalType.TRAIL_20K].taper_weeks == 2
        taper_start = next(
            w.week for w in plan.weeks if w.mode in (WeekMode.TAPER, WeekMode.RACE)
        )
        for w in plan.weeks:
            if w.mode == WeekMode.RECOVERY:
                assert taper_start - w.week > SHORT_TAPER_SUPPRESSION_WEEKS
        last_block = 0
        for w in reversed(plan.weeks[:taper_start]):
            if w.mode != WeekMode.LOAD:
                break
            last_block += 1
        assert 2 <= last_block <= 3

    def test_validation_rejects_a_peak_recovery_beside_a_two_week_taper(self) -> None:
        plan, req = self._trail_20k(20)
        weeks = list(plan.weeks)
        taper_start = next(i for i, w in enumerate(weeks) if w.mode == WeekMode.TAPER)
        w = weeks[taper_start - 1]
        weeks[taper_start - 1] = WeekSpec(
            week=w.week,
            phase=PhaseType.PEAK,
            mode=WeekMode.RECOVERY,
            sport_split=w.sport_split,
            effective_goal=w.effective_goal,
            owning_goal=w.owning_goal,
        )
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert any("RECOVERY within 2 wk" in e for e in result.errors)


class TestValidatePhasePlan:
    """Verify the post-condition checker catches violations."""

    def _make_valid_plan(self) -> tuple[PhasePlan, PhaseRequest]:
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        req = _request(a)
        plan = plan_phases(req)
        return plan, req

    def test_valid_plan_passes(self) -> None:
        plan, req = self._make_valid_plan()
        result = validate_phase_plan(plan, req)
        assert result.valid

    def test_gap_in_weeks_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Skip week 3.
        broken_weeks = [w for w in plan.weeks if w.week != 3]
        broken = PhasePlan(
            weeks=broken_weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("gap-free" in e for e in result.errors)

    def test_sport_split_violation_detected(self) -> None:
        plan, _req = self._make_valid_plan()
        # WeekSpec.__post_init__ now rejects invalid splits at construction.
        with pytest.raises(ValueError, match=r"sport_split must sum to 1\.0"):
            WeekSpec(
                week=0,
                phase=plan.weeks[0].phase,
                mode=plan.weeks[0].mode,
                sport_split={SportType.RUNNING: 0.5},  # sums to 0.5
                effective_goal=plan.weeks[0].effective_goal,
                owning_goal=plan.weeks[0].owning_goal,
            )

    def test_missing_race_event_detected(self) -> None:
        plan, req = self._make_valid_plan()
        broken = PhasePlan(weeks=plan.weeks, race_events=[], a_goal=plan.a_goal)
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("Missing RaceEvent" in e for e in result.errors)

    def test_base_after_build_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Swap a BUILD week to BASE to create a regression.
        weeks = list(plan.weeks)
        # Find last BUILD week, replace with BASE.
        build_indices = [i for i, w in enumerate(weeks) if w.phase == PhaseType.BUILD]
        if build_indices:
            idx = build_indices[-1]
            w = weeks[idx]
            weeks[idx] = WeekSpec(
                week=w.week,
                phase=PhaseType.BASE,
                mode=w.mode,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=w.owning_goal,
            )
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("BASE after BUILD" in e for e in result.errors)

    def test_race_event_not_in_race_week_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Move race event to a non-RACE week.
        moved_event = RaceEvent(
            event_id=plan.race_events[0].event_id,
            goal=plan.race_events[0].goal,
            week=0,  # week 0 is not a RACE week
            day_of_week=plan.race_events[0].day_of_week,
            duration_by_sport=plan.race_events[0].duration_by_sport,
        )
        broken = PhasePlan(
            weeks=plan.weeks,
            race_events=[moved_event],
            a_goal=plan.a_goal,
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("not a RACE-mode week" in e for e in result.errors)

    def test_race_recovery_not_after_race_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Insert RACE_RECOVERY at week 0 (not after a race).
        weeks = list(plan.weeks)
        w = weeks[0]
        weeks[0] = WeekSpec(
            week=0,
            phase=w.phase,
            mode=WeekMode.RACE_RECOVERY,
            sport_split=w.sport_split,
            effective_goal=w.effective_goal,
            owning_goal=w.owning_goal,
        )
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("RACE_RECOVERY" in e for e in result.errors)

    def test_race_recovery_after_an_a_goal_race_week_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Only a B/C race week may precede RACE_RECOVERY: mark weeks 5-6 as
        # an A-goal race week (owning_goal None) and a recovery week after it.
        weeks = list(plan.weeks)
        for i, mode in ((5, WeekMode.RACE), (6, WeekMode.RACE_RECOVERY)):
            w = weeks[i]
            weeks[i] = WeekSpec(
                week=w.week,
                phase=w.phase,
                mode=mode,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=None,
            )
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert (
            "Week 6: RACE_RECOVERY not directly after a B/C race week" in result.errors
        )

    def test_build_minimum_violated_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Replace all BUILD weeks with BASE.
        weeks = [
            WeekSpec(
                week=w.week,
                phase=PhaseType.BASE if w.phase == PhaseType.BUILD else w.phase,
                mode=w.mode,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=w.owning_goal,
            )
            for w in plan.weeks
        ]
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("build phase" in e for e in result.errors)

    def test_peak_minimum_violated_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Replace all PEAK weeks with BUILD.
        weeks = [
            WeekSpec(
                week=w.week,
                phase=PhaseType.BUILD if w.phase == PhaseType.PEAK else w.phase,
                mode=w.mode,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=w.owning_goal,
            )
            for w in plan.weeks
        ]
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("peak phase" in e for e in result.errors)

    def test_taper_minimum_violated_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Replace all TAPER weeks with PEAK.
        weeks = [
            WeekSpec(
                week=w.week,
                phase=PhaseType.PEAK if w.phase == PhaseType.TAPER else w.phase,
                mode=w.mode,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=w.owning_goal,
            )
            for w in plan.weeks
        ]
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("taper phase" in e for e in result.errors)

    def test_consecutive_load_not_ending_at_taper_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Replace ALL non-RACE modes with LOAD to create a long LOAD run
        # ending at RACE (which is allowed). To trigger the error we need
        # a LOAD run ending at something that is NOT taper/race.
        # Insert a RACE_RECOVERY in the middle to create two LOAD runs,
        # where the first run of >4 LOAD weeks ends at RACE_RECOVERY.
        weeks = list(plan.weeks)
        # Make all weeks LOAD except the last (RACE) and week 6 (RACE_RECOVERY).
        new_weeks = []
        for w in weeks:
            if w.week == 6:
                new_weeks.append(
                    WeekSpec(
                        week=w.week,
                        phase=w.phase,
                        mode=WeekMode.RACE_RECOVERY,
                        sport_split=w.sport_split,
                        effective_goal=w.effective_goal,
                        owning_goal=RaceGoal(
                            GoalType.RUNNING_5K, _MONDAY + timedelta(weeks=6), "C"
                        ),
                    )
                )
            elif w.mode in (WeekMode.RECOVERY, WeekMode.TAPER):
                new_weeks.append(
                    WeekSpec(
                        week=w.week,
                        phase=w.phase,
                        mode=WeekMode.LOAD,
                        sport_split=w.sport_split,
                        effective_goal=w.effective_goal,
                        owning_goal=w.owning_goal,
                    )
                )
            else:
                new_weeks.append(w)
        broken = PhasePlan(
            weeks=new_weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("consecutive LOAD" in e for e in result.errors)

    def test_load_run_to_recovery_then_taper_exempt(self) -> None:
        # A long LOAD run that terminates at a RECOVERY week which is itself
        # immediately followed by TAPER is exempt from the cadence cap.
        # Build ...LOAD,LOAD,...,RECOVERY,TAPER,RACE.
        plan, req = self._make_valid_plan()
        weeks = list(plan.weeks)
        taper_start = next(i for i, w in enumerate(weeks) if w.mode == WeekMode.TAPER)
        pre_taper = taper_start - 1
        new_weeks = []
        for i, w in enumerate(weeks):
            if i == pre_taper:
                # The single RECOVERY week sandwiched between the LOAD run and
                # the taper.
                new_weeks.append(
                    WeekSpec(
                        week=w.week,
                        phase=w.phase,
                        mode=WeekMode.RECOVERY,
                        sport_split=w.sport_split,
                        effective_goal=w.effective_goal,
                        owning_goal=w.owning_goal,
                    )
                )
            elif i < pre_taper and w.mode in (WeekMode.RECOVERY, WeekMode.TAPER):
                # Stretch the earlier weeks into one long LOAD run.
                new_weeks.append(
                    WeekSpec(
                        week=w.week,
                        phase=w.phase,
                        mode=WeekMode.LOAD,
                        sport_split=w.sport_split,
                        effective_goal=w.effective_goal,
                        owning_goal=w.owning_goal,
                    )
                )
            else:
                new_weeks.append(w)
        plan_built = PhasePlan(
            weeks=new_weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        # recovery_cadence small enough that the long pre-taper LOAD run would
        # violate rule 6 were it not for the taper-adjacent exemption.
        result = validate_phase_plan(plan_built, req, recovery_cadence=2)
        assert not any("consecutive LOAD" in e for e in result.errors)

    def test_recovery_near_taper_detected(self) -> None:
        plan, req = self._make_valid_plan()
        # Insert RECOVERY in a non-PEAK week near taper to trigger the
        # validator.  Synthetically set the phase to BUILD so the PEAK
        # exemption doesn't suppress the error.
        weeks = list(plan.weeks)
        taper_start = next(i for i, w in enumerate(weeks) if w.mode == WeekMode.TAPER)
        idx = taper_start - 1
        if idx >= 0:
            w = weeks[idx]
            weeks[idx] = WeekSpec(
                week=w.week,
                phase=PhaseType.BUILD,
                mode=WeekMode.RECOVERY,
                sport_split=w.sport_split,
                effective_goal=w.effective_goal,
                owning_goal=w.owning_goal,
            )
        broken = PhasePlan(
            weeks=weeks, race_events=plan.race_events, a_goal=plan.a_goal
        )
        result = validate_phase_plan(broken, req)
        assert not result.valid
        assert any("RECOVERY within" in e for e in result.errors)


class TestPlanPhasesErrors:
    """Verify plan_phases raises on invalid input."""

    def test_raises_on_invalid_request(self) -> None:
        with pytest.raises(ValueError, match="Invalid PhaseRequest"):
            plan_phases(_request(_bc_goal()))

    def test_raises_on_short_horizon(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 8)
        with pytest.raises(ValueError, match="at least 12 weeks"):
            plan_phases(_request(a))


class TestSpliceSecondaryGoals:
    """Test _splice_secondary_goals edge cases directly."""

    def test_bc_in_taper_zone_skipped(self) -> None:
        # A-goal 16wk marathon. Taper starts week 13.
        # B-goal at week 14 (in taper zone) should be skipped.
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        skeleton = _build_a_skeleton(a, _MONDAY, 16)
        # B-goal in taper zone.
        b = RaceGoal(
            GoalType.RUNNING_5K,
            _MONDAY + timedelta(weeks=14),
            "B",
        )
        events = _splice_secondary_goals(skeleton, [b], a, _MONDAY)
        assert len(events) == 0  # skipped

    def test_bc_out_of_range_skipped(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 16)
        skeleton = _build_a_skeleton(a, _MONDAY, 16)
        # B-goal at week 20 (beyond skeleton).
        b = RaceGoal(
            GoalType.RUNNING_5K,
            _MONDAY + timedelta(weeks=20),
            "B",
        )
        events = _splice_secondary_goals(skeleton, [b], a, _MONDAY)
        assert len(events) == 0  # skipped

    def test_bc_in_peak_zone_skipped(self) -> None:
        # A-goal marathon 20wk: peak starts around week 14-15, taper at 17.
        # B-goal at week 16 falls in the A-goal peak/taper zone and should
        # be skipped per the architecture.
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        skeleton = _build_a_skeleton(a, _MONDAY, 21)  # 21 weeks [0..20]
        b = RaceGoal(
            GoalType.RUNNING_5K,
            _MONDAY + timedelta(weeks=16),
            "B",
        )
        events = _splice_secondary_goals(skeleton, [b], a, _MONDAY)
        assert len(events) == 0  # skipped — in A-goal peak zone

    def test_bc_just_before_peak_zone_accepted(self) -> None:
        # B-goal placed just before the A-goal peak zone should be accepted.
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        skeleton = _build_a_skeleton(a, _MONDAY, 21)
        # Find where peak starts to place B-goal just before it.
        peak_start = next(w.week for w in skeleton if w.phase == PhaseType.PEAK)
        b_week = peak_start - 1
        if b_week >= 3:
            b = RaceGoal(
                GoalType.RUNNING_5K,
                _MONDAY + timedelta(weeks=b_week),
                "B",
            )
            events = _splice_secondary_goals(skeleton, [b], a, _MONDAY)
            assert len(events) == 1


class TestEdgeCases:
    """Edge case coverage."""

    def test_minimal_5k_plan(self) -> None:
        a = _a_goal(GoalType.RUNNING_5K, 4)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.weeks) == 5  # race at week 4 -> [0,1,2,3,4]

    def test_race_on_sunday(self) -> None:
        sunday = _MONDAY + timedelta(days=6)  # next Sunday
        a = RaceGoal(GoalType.RUNNING_5K, sunday + timedelta(weeks=8), "A")
        req = PhaseRequest(goals=[a], training_start=sunday - timedelta(weeks=8))
        plan = plan_phases(req)
        assert plan.race_events[0].day_of_week == a.race_date.weekday()

    def test_b_goal_at_earliest_allowed_position(self) -> None:
        a = _a_goal(GoalType.RUNNING_MARATHON, 20)
        b = _bc_goal(GoalType.RUNNING_5K, BC_EARLIEST_WEEK, "C")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)

    def test_same_goal_type_a_and_b_no_crash(self) -> None:
        # A=5K + B=5K must not crash — distinct goals can share the same
        # GoalType without causing a phase ordering violation.
        a = _a_goal(GoalType.RUNNING_5K, 16)
        b = _bc_goal(GoalType.RUNNING_5K, 4, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.race_events) == 2

    def test_early_b_with_recovery_no_crash(self) -> None:
        # A 30w marathon A + week-8 half-marathon B must not crash:
        # recovery weeks must not revert to BASE phase while
        # effective_goal is the B-goal (which already has PEAK).
        a = _a_goal(GoalType.RUNNING_MARATHON, 30)
        b = _bc_goal(GoalType.RUNNING_HALF_MARATHON, 8, "B")
        req = _request(a, b)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)

    def test_duplicate_bc_goal_types_unique_event_ids(self) -> None:
        # Two B/C goals of the same type must produce unique event_ids.
        a = _a_goal(GoalType.RUNNING_MARATHON, 30)
        b1 = _bc_goal(GoalType.RUNNING_10K, 8, "B")
        b2 = _bc_goal(GoalType.RUNNING_10K, 14, "B")
        req = _request(a, b1, b2)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        event_ids = [e.event_id for e in plan.race_events]
        assert len(event_ids) == len(set(event_ids)), (
            f"Duplicate event_ids: {event_ids}"
        )

    def test_long_horizon_plan(self) -> None:
        a = _a_goal(GoalType.IRONMAN, 36)
        req = _request(a)
        plan = plan_phases(req)
        _assert_post_conditions(plan, req)
        assert len(plan.weeks) == 37  # race at week 36 -> [0..36]
