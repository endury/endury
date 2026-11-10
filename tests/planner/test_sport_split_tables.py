"""Tests for phase-dependent sport-split lookup tables."""

from __future__ import annotations

from endury.planner.foundations.types import GoalType, PhaseType, SportType
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.phase.sport_split_tables import (
    PHASE_SPORT_SPLITS,
    get_phase_sport_split,
)


class TestPhaseSportSplitData:
    """Verify structural integrity of PHASE_SPORT_SPLITS data."""

    def test_all_phase_splits_sum_to_one(self) -> None:
        for gt, phases in PHASE_SPORT_SPLITS.items():
            for phase, split in phases.items():
                total = sum(split.values())
                assert abs(total - 1.0) < 0.01, (
                    f"{gt.value}/{phase.value}: split sums to {total}"
                )

    def test_phase_splits_have_consistent_sport_keys(self) -> None:
        for gt, phases in PHASE_SPORT_SPLITS.items():
            expected_keys = set(GOAL_PROFILES[gt].default_sport_split.keys())
            for phase, split in phases.items():
                assert set(split.keys()) == expected_keys, (
                    f"{gt.value}/{phase.value}: sport keys {set(split.keys())} "
                    f"don't match default {expected_keys}"
                )

    def test_all_phases_covered(self) -> None:
        for gt, phases in PHASE_SPORT_SPLITS.items():
            for phase in PhaseType:
                # MAINTAIN is the raceless maintenance-goal phase; race
                # goals (the only entries in this table) never see it.
                if phase == PhaseType.MAINTAIN:
                    continue
                assert phase in phases, f"{gt.value}: missing phase {phase.value}"

    def test_only_triathlon_goals_present(self) -> None:
        running_goals = {
            GoalType.RUNNING_5K,
            GoalType.RUNNING_10K,
            GoalType.RUNNING_HALF_MARATHON,
            GoalType.RUNNING_MARATHON,
        }
        for gt in PHASE_SPORT_SPLITS:
            assert gt not in running_goals, (
                f"Running goal {gt.value} should not have phase splits"
            )


class TestGetPhaseSportSplit:
    """Verify the public lookup function."""

    def test_returns_split_for_triathlon_goal(self) -> None:
        split = get_phase_sport_split(GoalType.IRONMAN, PhaseType.BASE)
        assert split is not None
        assert SportType.SWIMMING in split

    def test_returns_none_for_running_goal(self) -> None:
        split = get_phase_sport_split(GoalType.RUNNING_MARATHON, PhaseType.BASE)
        assert split is None

    def test_base_has_more_swimming_than_peak(self) -> None:
        """Triathlon base phase should have more swimming than peak."""
        for gt in PHASE_SPORT_SPLITS:
            base = get_phase_sport_split(gt, PhaseType.BASE)
            peak = get_phase_sport_split(gt, PhaseType.PEAK)
            assert base is not None
            assert peak is not None
            assert base[SportType.SWIMMING] > peak[SportType.SWIMMING], (
                f"{gt.value}: base swim ({base[SportType.SWIMMING]}) "
                f"should exceed peak swim ({peak[SportType.SWIMMING]})"
            )
