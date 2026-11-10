"""Tests for endury/planner/macro/capacity.py."""

from __future__ import annotations

import itertools

import pytest

from endury.planner.foundations.types import (
    GoalType,
    PhaseType,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.macro.capacity import (
    MAX_CAPACITY_CEILING_HOURS,
    PEAK_SHED_FACTORS,
    RACE_RECOVERY_CEILING_FACTOR,
    RECOVERY_CEILING_FACTOR,
    TAPER_DECAY_FACTOR,
    _lone_peak_load_week,
    _peak_load_shed_window,
    _race_recovery_block_lengths,
    build_capacity_projection,
)
from endury.planner.macro.macro_config import POST_RECOVERY_BOUNCE_BACK


def _load_week(week: int) -> WeekSpec:
    return WeekSpec(
        week=week,
        phase=PhaseType.BASE,
        mode=WeekMode.LOAD,
        sport_split={SportType.RUNNING: 1.0},
        effective_goal=GoalType.RUNNING_MARATHON,
        owning_goal=None,
    )


def _recovery_week(week: int) -> WeekSpec:
    return WeekSpec(
        week=week,
        phase=PhaseType.BASE,
        mode=WeekMode.RECOVERY,
        sport_split={SportType.RUNNING: 1.0},
        effective_goal=GoalType.RUNNING_MARATHON,
        owning_goal=None,
    )


class TestBuildCapacityProjection:
    def test_load_weeks_grow(self) -> None:
        """Each LOAD week ceiling should be >= the previous."""
        weeks = [_load_week(i) for i in range(4)]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.10,
        )
        ceilings = [proj.weekly_ceiling[i] for i in range(4)]
        for a, b in itertools.pairwise(ceilings):
            assert b >= a

    def test_recovery_week_below_preceding_load(self) -> None:
        """Recovery week ceiling must be below the preceding LOAD ceiling."""
        weeks = [_load_week(0), _load_week(1), _load_week(2), _recovery_week(3)]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.10,
            recovery_ceiling_factor=RECOVERY_CEILING_FACTOR,
        )
        load_ceil = proj.weekly_ceiling[2]
        recovery_ceil = proj.weekly_ceiling[3]
        assert recovery_ceil < load_ceil
        assert recovery_ceil == pytest.approx(load_ceil * RECOVERY_CEILING_FACTOR)

    def test_per_sport_ceiling_respects_split(self) -> None:
        """Per-sport ceiling should be total ceiling x sport split fraction."""
        weeks = [
            WeekSpec(
                week=0,
                phase=PhaseType.BASE,
                mode=WeekMode.LOAD,
                sport_split={SportType.RUNNING: 0.5, SportType.CYCLING: 0.5},
                effective_goal=GoalType.OLYMPIC_TRIATHLON,
                owning_goal=None,
            )
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 4.0, SportType.CYCLING: 4.0},
            weeks,
        )
        total = proj.weekly_ceiling[0]
        run_ceil = proj.weekly_ceiling_by_sport[0][SportType.RUNNING]
        bike_ceil = proj.weekly_ceiling_by_sport[0][SportType.CYCLING]
        assert run_ceil == pytest.approx(total * 0.5)
        assert bike_ceil == pytest.approx(total * 0.5)

    def test_empty_weeks_produces_empty_projection(self) -> None:
        proj = build_capacity_projection({SportType.RUNNING: 5.0}, [])
        assert proj.weekly_ceiling == {}
        assert proj.weekly_ceiling_by_sport == {}

    def _make_week(self, week: int, mode: WeekMode) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.TAPER,
            mode=mode,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.RUNNING_MARATHON,
            owning_goal=None,
        )

    def test_race_week_continues_taper_decay(self) -> None:
        """RACE week ceiling continues taper decay (same as TAPER)."""
        weeks = [self._make_week(0, WeekMode.TAPER), self._make_week(1, WeekMode.RACE)]
        proj = build_capacity_projection({SportType.RUNNING: 8.0}, weeks)
        # Week 0 (TAPER): 8.0 * 0.85 = 6.8.  Week 1 (RACE): 6.8 * 0.85 = 5.78.
        expected = 8.0 * TAPER_DECAY_FACTOR * TAPER_DECAY_FACTOR
        assert proj.weekly_ceiling[1] == pytest.approx(expected)

    def _peak_week(self, week: int, mode: WeekMode) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.PEAK,
            mode=mode,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.RUNNING_MARATHON,
            owning_goal=None,
        )

    def test_recovery_before_taper_does_not_compound_deloads(self) -> None:
        """A RECOVERY week before TAPER must not stack onto the taper decay.

        The taper chains off the last *issued* (peak-shed) LOAD ceiling, so
        the intervening recovery deload is not multiplied into it.
        """
        weeks = [
            _load_week(0),
            _load_week(1),
            self._peak_week(2, WeekMode.LOAD),
            self._peak_week(3, WeekMode.LOAD),
            self._peak_week(4, WeekMode.RECOVERY),
            self._make_week(5, WeekMode.TAPER),
            self._make_week(6, WeekMode.TAPER),
            self._make_week(7, WeekMode.RACE),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.10,
            recovery_ceiling_factor=RECOVERY_CEILING_FACTOR,
        )
        last_load_issued = proj.weekly_ceiling[3]
        recovery_ceil = proj.weekly_ceiling[4]
        first_taper = proj.weekly_ceiling[5]

        # The taper anchors on the shed LOAD ceiling, not the recovery week.
        assert first_taper == pytest.approx(last_load_issued * TAPER_DECAY_FACTOR)
        # It must stay below the LOAD week preceding it -- the property the
        # un-shed anchoring violated.
        assert first_taper < last_load_issued
        # And it must not compound the recovery deload.
        assert first_taper > recovery_ceil
        # Subsequent taper/race weeks continue the decay normally.
        assert proj.weekly_ceiling[6] == pytest.approx(first_taper * TAPER_DECAY_FACTOR)
        assert proj.weekly_ceiling[7] == pytest.approx(
            first_taper * TAPER_DECAY_FACTOR * TAPER_DECAY_FACTOR
        )

    def test_load_before_taper_chains_off_the_shed_load_ceiling(self) -> None:
        """LOAD -> TAPER is unaffected: the shed ceiling is already the chain base."""
        weeks = [
            _load_week(0),
            _load_week(1),
            self._peak_week(2, WeekMode.LOAD),
            self._peak_week(3, WeekMode.LOAD),
            self._make_week(4, WeekMode.TAPER),
            self._make_week(5, WeekMode.RACE),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.10,
        )
        # Week 3 carries the final peak shed; the taper decays from it.
        assert proj.weekly_ceiling[4] == pytest.approx(
            proj.weekly_ceiling[3] * TAPER_DECAY_FACTOR
        )
        assert proj.weekly_ceiling[5] == pytest.approx(
            proj.weekly_ceiling[3] * TAPER_DECAY_FACTOR * TAPER_DECAY_FACTOR
        )

    def test_no_peak_shed_before_a_two_week_taper(self) -> None:
        """Before a taper under three weeks the PEAK weeks keep the full ramp (R-PP-007)."""
        weeks = [
            _load_week(0),
            _load_week(1),
            self._peak_week(2, WeekMode.LOAD),
            self._peak_week(3, WeekMode.LOAD),
            self._make_week(4, WeekMode.TAPER),
            self._make_week(5, WeekMode.RACE),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0}, weeks, hours_ramp_limit=1.10
        )
        assert proj.weekly_ceiling[2] == pytest.approx(proj.weekly_ceiling[1] * 1.10)
        assert proj.weekly_ceiling[3] == pytest.approx(proj.weekly_ceiling[2] * 1.10)

    def test_peak_shed_before_a_three_week_taper(self) -> None:
        """Before a three-week taper the last two PEAK weeks shed 10 % then 20 %."""
        weeks = [
            _load_week(0),
            _load_week(1),
            self._peak_week(2, WeekMode.LOAD),
            self._peak_week(3, WeekMode.LOAD),
            self._make_week(4, WeekMode.TAPER),
            self._make_week(5, WeekMode.TAPER),
            self._make_week(6, WeekMode.RACE),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0}, weeks, hours_ramp_limit=1.10
        )
        unshed_2 = proj.weekly_ceiling[1] * 1.10
        assert proj.weekly_ceiling[2] == pytest.approx(unshed_2 * PEAK_SHED_FACTORS[0])
        assert proj.weekly_ceiling[3] == pytest.approx(
            unshed_2 * 1.10 * PEAK_SHED_FACTORS[1]
        )

    def test_post_recovery_bounce_back_caps_reentry(self) -> None:
        """First LOAD after RECOVERY must not exceed pre-recovery ceiling x bounce-back."""
        weeks = [
            _load_week(0),
            _load_week(1),
            _load_week(2),
            _recovery_week(3),
            _load_week(4),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.10,
            recovery_ceiling_factor=RECOVERY_CEILING_FACTOR,
            post_recovery_bounce_back=POST_RECOVERY_BOUNCE_BACK,
        )
        pre_recovery_ceil = proj.weekly_ceiling[2]
        post_recovery_ceil = proj.weekly_ceiling[4]
        # The ceiling after recovery must be capped by bounce-back.
        assert (
            post_recovery_ceil <= pre_recovery_ceil * POST_RECOVERY_BOUNCE_BACK + 1e-9
        )
        # And it must be above the recovery week itself (it's a LOAD week).
        assert post_recovery_ceil > proj.weekly_ceiling[3]

    def test_post_recovery_bounce_back_does_not_suppress_growth(self) -> None:
        """When ramp is small, bounce-back should not lower the ceiling below ramp."""
        weeks = [
            _load_week(0),
            _load_week(1),
            _recovery_week(2),
            _load_week(3),
        ]
        # Tiny ramp: 1.02x. Bounce-back at 1.05x should not bind.
        proj = build_capacity_projection(
            {SportType.RUNNING: 5.0},
            weeks,
            hours_ramp_limit=1.02,
            post_recovery_bounce_back=1.05,
        )
        # Wk 3 ceiling should still grow (ramp < bounce-back, so ramp wins).
        assert proj.weekly_ceiling[3] >= proj.weekly_ceiling[1]

    def test_race_recovery_increases_linearly(self) -> None:
        """RACE_RECOVERY ceiling ramps linearly across the block."""
        recent = 8.0  # single sport
        weeks = [
            self._make_week(0, WeekMode.RACE),
            self._make_week(1, WeekMode.RACE_RECOVERY),
            self._make_week(2, WeekMode.RACE_RECOVERY),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: recent},
            weeks,
            hours_ramp_limit=1.0,
        )
        # last_load_ceiling is the initial total_recent (no LOAD weeks to ramp it).
        last_load = recent
        expected_1 = (1 / 2) * RACE_RECOVERY_CEILING_FACTOR * last_load
        expected_2 = (2 / 2) * RACE_RECOVERY_CEILING_FACTOR * last_load
        assert proj.weekly_ceiling[1] == pytest.approx(expected_1)
        assert proj.weekly_ceiling[2] == pytest.approx(expected_2)

    def test_race_recovery_after_load_enters_gap(self) -> None:
        """A LOAD → RACE_RECOVERY transition triggers the in-gap entry path."""
        recent = 8.0
        weeks = [
            self._make_week(0, WeekMode.LOAD),
            self._make_week(1, WeekMode.RACE_RECOVERY),
            self._make_week(2, WeekMode.RACE_RECOVERY),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: recent},
            weeks,
            hours_ramp_limit=1.0,
        )
        # After a LOAD week with no further LOAD growth (ramp=1.0),
        # last_load_ceiling stays at recent. The RACE_RECOVERY ramp scales
        # it linearly across the 2-week block.
        last_load = recent
        expected_1 = (1 / 2) * RACE_RECOVERY_CEILING_FACTOR * last_load
        expected_2 = (2 / 2) * RACE_RECOVERY_CEILING_FACTOR * last_load
        assert proj.weekly_ceiling[1] == pytest.approx(expected_1)
        assert proj.weekly_ceiling[2] == pytest.approx(expected_2)


class TestRaceRecoveryBlockLengths:
    def _rr(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.BASE,
            mode=WeekMode.RACE_RECOVERY,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.RUNNING_MARATHON,
            owning_goal=None,
        )

    def _load(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.BASE,
            mode=WeekMode.LOAD,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.RUNNING_MARATHON,
            owning_goal=None,
        )

    def test_single_rr_block_has_length_one(self) -> None:
        specs = [self._rr(0)]
        result = _race_recovery_block_lengths(specs)
        assert result == {0: 1}

    def test_consecutive_rr_block_shares_length(self) -> None:
        specs = [self._rr(0), self._rr(1), self._rr(2)]
        result = _race_recovery_block_lengths(specs)
        assert result == {0: 3, 1: 3, 2: 3}

    def test_load_breaks_block(self) -> None:
        specs = [self._rr(0), self._rr(1), self._load(2), self._rr(3)]
        result = _race_recovery_block_lengths(specs)
        assert result == {0: 2, 1: 2, 3: 1}

    def test_no_rr_weeks_returns_empty(self) -> None:
        specs = [self._load(0), self._load(1)]
        result = _race_recovery_block_lengths(specs)
        assert result == {}


class TestPeakLoadShedWindow:
    def _peak_load(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.PEAK,
            mode=WeekMode.LOAD,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.IRONMAN,
            owning_goal=None,
        )

    def _peak_recovery(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.PEAK,
            mode=WeekMode.RECOVERY,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.IRONMAN,
            owning_goal=None,
        )

    def _build_load(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.BUILD,
            mode=WeekMode.LOAD,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.IRONMAN,
            owning_goal=None,
        )

    def _taper(self, week: int) -> WeekSpec:
        return WeekSpec(
            week=week,
            phase=PhaseType.TAPER,
            mode=WeekMode.TAPER,
            sport_split={SportType.RUNNING: 1.0},
            effective_goal=GoalType.IRONMAN,
            owning_goal=None,
        )

    def test_empty_shed_factors_returns_empty(self) -> None:
        weeks = [self._peak_load(0), self._taper(1)]
        assert _peak_load_shed_window(weeks, ()) == {}

    def test_no_taper_week_returns_empty(self) -> None:
        weeks = [self._peak_load(0), self._peak_load(1), self._peak_load(2)]
        assert _peak_load_shed_window(weeks, (0.9, 0.8)) == {}

    def test_final_peak_loads_receive_shed(self) -> None:
        # 4 peak/load weeks then taper; shed applies to the last 2.
        weeks = [
            self._peak_load(0),
            self._peak_load(1),
            self._peak_load(2),
            self._peak_load(3),
            self._taper(4),
        ]
        result = _peak_load_shed_window(weeks, (0.9, 0.8))
        # Week 3 (immediately before taper) gets the *last* factor (0.8);
        # week 2 gets 0.9. Earlier peaks are unaffected.
        assert result == {3: 0.8, 2: 0.9}

    def test_recovery_week_inside_peak_does_not_break_window(self) -> None:
        # A recovery week between peak/loads should be skipped, not stop the
        # shed walk.
        weeks = [
            self._peak_load(0),
            self._peak_load(1),
            self._peak_recovery(2),
            self._peak_load(3),
            self._taper(4),
        ]
        result = _peak_load_shed_window(weeks, (0.9, 0.8))
        # Walking back from taper: wk3 (load), wk2 (recovery, skipped),
        # wk1 (load). Assigns 0.8 to wk3 and 0.9 to wk1.
        assert result == {3: 0.8, 1: 0.9}

    def test_non_peak_phase_breaks_walk(self) -> None:
        # Walking back from taper, a BUILD week stops the shed window.
        weeks = [
            self._build_load(0),
            self._peak_load(1),
            self._taper(2),
        ]
        result = _peak_load_shed_window(weeks, (0.9, 0.8))
        # Only wk1 is in PEAK; wk0 is BUILD and stops the walk. The window
        # still assigns the plain last shed factor...
        assert result == {1: 0.8}
        # ...and separately, the week is reported as the lone PEAK LOAD
        # week (R-PP-007): build_capacity_projection floors it instead of
        # shedding it, using this plain value rather than a flag hidden in
        # the shed factor itself.
        assert _lone_peak_load_week(weeks) == 1

    def test_peak_shed_lowers_final_load_ceilings(self) -> None:
        """End-to-end: the build projection should apply PEAK_SHED_FACTORS."""
        # Construct: 3 build/load, 2 peak/load, a three-week taper (the shed
        # applies before a taper of PEAK_SHED_MIN_TAPER_WEEKS or more).
        weeks = [
            self._build_load(0),
            self._build_load(1),
            self._build_load(2),
            self._peak_load(3),
            self._peak_load(4),
            self._taper(5),
            self._taper(6),
            self._taper(7),
        ]
        proj = build_capacity_projection(
            {SportType.RUNNING: 8.0},
            weeks,
            hours_ramp_limit=1.05,
        )
        # Without the shed, wk3 and wk4 would be last_load * ramp each step.
        # Reconstruct expected un-shed values to isolate the shed effect.
        ramp = 1.05
        expected_wk2 = 8.0 * (ramp**3)
        expected_wk3_unshed = min(expected_wk2 * ramp, 18.0)
        expected_wk4_unshed = min(expected_wk3_unshed * ramp, 18.0)
        # wk3 gets PEAK_SHED_FACTORS[0]=0.90, wk4 gets PEAK_SHED_FACTORS[-1]=0.80.
        assert proj.weekly_ceiling[3] == pytest.approx(
            expected_wk3_unshed * PEAK_SHED_FACTORS[0]
        )
        assert proj.weekly_ceiling[4] == pytest.approx(
            expected_wk4_unshed * PEAK_SHED_FACTORS[-1]
        )


def _week(week: int, phase: PhaseType, mode: WeekMode) -> WeekSpec:
    return WeekSpec(
        week=week,
        phase=phase,
        mode=mode,
        sport_split={SportType.RUNNING: 1.0},
        effective_goal=GoalType.TRAIL_50K,
        owning_goal=None,
    )


def test_lone_peak_load_week_not_below_the_load_week_before() -> None:
    """R-PP-007: a lone PEAK LOAD week is floored at the build week's ceiling.

    The athlete's skeleton: a BUILD LOAD week, a PEAK RECOVERY week, a lone
    PEAK LOAD week, then TAPER and RACE. Before the fix the lone week took
    the strongest peak-shed factor (0.80) right after the post-recovery
    bounce cap, landing below the build week it follows -- the plan's
    "peak" week smaller than its build week. The floor keeps the peak
    week at least as large as the build week before it.
    """
    weeks = [
        _week(0, PhaseType.BUILD, WeekMode.LOAD),
        _week(1, PhaseType.PEAK, WeekMode.RECOVERY),
        _week(2, PhaseType.PEAK, WeekMode.LOAD),
        _week(3, PhaseType.TAPER, WeekMode.TAPER),
        _week(4, PhaseType.TAPER, WeekMode.RACE),
    ]
    proj = build_capacity_projection(
        {SportType.RUNNING: 3.53}, weeks, hours_ramp_limit=1.12
    )
    assert proj.weekly_ceiling[2] >= proj.weekly_ceiling[0] - 1e-9


def test_two_peak_load_weeks_after_a_recovery_gap_shed_as_before() -> None:
    """Guard: a PEAK run of two or more LOAD weeks is unaffected by R-PP-007.

    Same skeleton as the lone-week case, but with two PEAK LOAD weeks: the
    window function returns the two-step factors, no week is reported as
    lone, and the projection applies them exactly as the un-shed values times those
    factors, same as before this change.
    """
    weeks = [
        _week(0, PhaseType.BUILD, WeekMode.LOAD),
        _week(1, PhaseType.PEAK, WeekMode.RECOVERY),
        _week(2, PhaseType.PEAK, WeekMode.LOAD),
        _week(3, PhaseType.PEAK, WeekMode.LOAD),
        _week(4, PhaseType.TAPER, WeekMode.TAPER),
        _week(5, PhaseType.TAPER, WeekMode.TAPER),
        _week(6, PhaseType.TAPER, WeekMode.RACE),
    ]
    window = _peak_load_shed_window(weeks, PEAK_SHED_FACTORS)
    assert window == {2: PEAK_SHED_FACTORS[0], 3: PEAK_SHED_FACTORS[-1]}

    proj = build_capacity_projection(
        {SportType.RUNNING: 3.53}, weeks, hours_ramp_limit=1.12
    )
    build_ceiling = proj.weekly_ceiling[0]
    bounce_cap = build_ceiling * POST_RECOVERY_BOUNCE_BACK
    unshed_2 = min(build_ceiling * 1.12, bounce_cap)
    unshed_3 = min(unshed_2 * 1.12, MAX_CAPACITY_CEILING_HOURS)
    assert proj.weekly_ceiling[2] == pytest.approx(unshed_2 * PEAK_SHED_FACTORS[0])
    assert proj.weekly_ceiling[3] == pytest.approx(unshed_3 * PEAK_SHED_FACTORS[-1])


def test_lone_peak_load_week_with_no_earlier_load_week_behaves_as_before() -> None:
    """Guard: a lone PEAK LOAD week with nothing before it is shed as before.

    When the PEAK LOAD week opens the plan (no earlier LOAD week issued a
    ceiling) before a three-week taper, there is nothing to floor against,
    so it still takes the strongest peak-shed factor, exactly as today.
    """
    weeks = [
        _week(0, PhaseType.PEAK, WeekMode.LOAD),
        _week(1, PhaseType.TAPER, WeekMode.TAPER),
        _week(2, PhaseType.TAPER, WeekMode.TAPER),
        _week(3, PhaseType.TAPER, WeekMode.RACE),
    ]
    proj = build_capacity_projection(
        {SportType.RUNNING: 3.53}, weeks, hours_ramp_limit=1.12
    )
    unshed = min(3.53 * 1.12, MAX_CAPACITY_CEILING_HOURS)
    assert proj.weekly_ceiling[0] == pytest.approx(unshed * PEAK_SHED_FACTORS[-1])
