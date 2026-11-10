"""Tests for endury/planner/macro/tid_tables.py."""

from __future__ import annotations

from endury.planner.foundations.types import GoalType, PhaseType, WeekMode
from endury.planner.macro.tid_tables import get_tid_fractions


class TestGetTidFractions:
    def test_recovery_falls_back_to_base_row_when_phase_missing(self) -> None:
        # TAPER + RECOVERY has no row in _TID_TABLE; the fallback must
        # return the BASE/RECOVERY row.
        result = get_tid_fractions(PhaseType.TAPER, WeekMode.RECOVERY, GoalType.IRONMAN)
        base_recovery = get_tid_fractions(
            PhaseType.BASE, WeekMode.RECOVERY, GoalType.IRONMAN
        )
        assert result == base_recovery

    def test_recovery_returns_phase_specific_row_when_present(self) -> None:
        # BASE/BUILD/PEAK each have their own RECOVERY row.
        for phase in (PhaseType.BASE, PhaseType.BUILD, PhaseType.PEAK):
            result = get_tid_fractions(phase, WeekMode.RECOVERY, GoalType.IRONMAN)
            assert (result.z_low + result.z_mid + result.z_high) == 1.0

    def test_race_mode_returns_goal_independent_row(self) -> None:
        for goal in (GoalType.IRONMAN, GoalType.RUNNING_5K):
            result = get_tid_fractions(PhaseType.TAPER, WeekMode.RACE, goal)
            assert (result.z_low + result.z_mid + result.z_high) == 1.0

    def test_load_mode_interpolates_between_marathon_and_5k(self) -> None:
        marathon_row = get_tid_fractions(
            PhaseType.BUILD, WeekMode.LOAD, GoalType.RUNNING_MARATHON
        )
        fivek_row = get_tid_fractions(
            PhaseType.BUILD, WeekMode.LOAD, GoalType.RUNNING_5K
        )
        ironman_row = get_tid_fractions(
            PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN
        )
        # IM sits closer to marathon than to 5K.
        assert ironman_row.z_low >= fivek_row.z_low
        assert ironman_row.z_high <= fivek_row.z_high
        # All sum to 1.
        for row in (marathon_row, fivek_row, ironman_row):
            assert abs(row.z_low + row.z_mid + row.z_high - 1.0) < 1e-9
