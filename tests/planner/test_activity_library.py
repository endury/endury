"""Tests for planner activity library.

Covers registry integrity, query helpers, helper functions, and all 41
registered activity definitions (38 training + 3 test protocols).
"""

from __future__ import annotations

import pytest

from endury.domain.analysis.zones import athlete_zone_set, compute_all_zones
from endury.domain.models.zones import TrainingZones, ZoneMetric
from endury.planner.activity import (
    ACTIVITY_REGISTRY,
    ActivityStimulusTag,
    build_catalog_enum,
    ensure_registry_loaded,
    get_activity,
    query_activities,
)
from endury.planner.activity.capabilities import ActivityCapabilityProfile
from endury.planner.activity.config import (
    EASY_HARDNESS,
    HARD_HARDNESS,
    HR_ZONE_THRESHOLDS,
    POWER_ZONE_THRESHOLDS,
    SWIM_STEP_METERS,
)
from endury.planner.activity.materialize import (
    assignment_target_tss,
    workout_total_meters,
)
from endury.planner.activity.shapes.aquatic import (
    SwimLinearDistanceActivity,
    _round_to_swim_step,
    _select_lattice_target,
    _swim_meters_tss,
)
from endury.planner.activity.shapes.base import (
    _build_step_targets,
    _if_to_tid_fractions,
    _if_to_zone,
    _tid_from_segments,
    _zone_target,
)
from endury.planner.activity.shapes.land import (
    FixedBlockActivity,
    IntervalPiecewiseActivity,
    LinearDurationActivity,
    ProgressiveBlockActivity,
)
from endury.planner.activity.steps import build_flat_steps
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    GoalType,
    PhaseType,
    SportType,
    TargetPreference,
)
from endury.training.load import LoadMetricContext, tss_from_duration_minutes

# Eagerly load so tests can safely reference ACTIVITY_REGISTRY.
ensure_registry_loaded()

_REGISTERED_KEYS = list(ActivityKey)


def _make_resolver(css: float | None = 90.0) -> LoadMetricContext:
    return LoadMetricContext(swim_css_sec_per_100m=css)


def _workout_minutes(steps) -> float:
    """Sum a workout's listed step durations in minutes, expanding repeats."""
    total = 0.0
    for step in steps:
        if step.type == "repeat":
            assert step.steps is not None
            assert step.iterations is not None
            total += step.iterations * _workout_minutes(step.steps)
        else:
            assert step.duration is not None
            assert step.duration.value_seconds is not None
            total += step.duration.value_seconds / 60.0
    return total


class TestRegistryIntegrity:
    """Verify the registry contains exactly the expected entries."""

    def test_registry_has_61_entries(self) -> None:
        assert len(ACTIVITY_REGISTRY) == 61

    def test_all_activity_keys_registered(self) -> None:
        for key in ActivityKey:
            assert key in ACTIVITY_REGISTRY, f"'{key}' not registered"

    def test_no_duplicate_keys(self) -> None:
        # Registry is a dict - duplicates would have raised at registration time.
        # We can verify counts match unique keys.
        assert len(ACTIVITY_REGISTRY) == len(set(ACTIVITY_REGISTRY))

    def test_every_activity_has_capability_profile(self) -> None:
        for key, activity in ACTIVITY_REGISTRY.items():
            assert activity.capability_profile is not None, (
                f"activity '{key}' missing capability_profile"
            )

    def test_get_activity_returns_correct_instance(self) -> None:
        act = get_activity(ActivityKey.RUN_EASY)
        assert act.key == ActivityKey.RUN_EASY
        assert act.sport == SportType.RUNNING

    def test_get_activity_raises_for_unknown_key(self) -> None:
        with pytest.raises(KeyError, match="unknown activity key"):
            get_activity("nonexistent_activity")  # type: ignore[arg-type]


class TestBuildCatalogEnum:
    def test_returns_sorted_keys(self) -> None:
        keys = build_catalog_enum()
        assert keys == sorted(keys)

    def test_length_is_61(self) -> None:
        keys = build_catalog_enum()
        assert len(keys) == 61

    def test_all_strings(self) -> None:
        for k in build_catalog_enum():
            assert isinstance(k, str)


class TestQueryActivities:
    def test_no_filter_returns_all_61(self) -> None:
        results = query_activities()
        assert len(results) == 61

    def test_filter_by_sport_running(self) -> None:
        results = query_activities(sport=SportType.RUNNING)
        assert len(results) == 29
        assert all(a.sport == SportType.RUNNING for a in results)

    def test_filter_by_sport_cycling(self) -> None:
        results = query_activities(sport=SportType.CYCLING)
        assert len(results) == 19
        assert all(a.sport == SportType.CYCLING for a in results)

    def test_filter_by_sport_swimming(self) -> None:
        results = query_activities(sport=SportType.SWIMMING)
        assert len(results) == 13
        assert all(a.sport == SportType.SWIMMING for a in results)

    def test_filter_by_family_easy(self) -> None:
        results = query_activities(family=ActivityFamily.EASY)
        assert len(results) > 0
        assert all(a.family == ActivityFamily.EASY for a in results)

    def test_filter_by_family_recovery(self) -> None:
        results = query_activities(family=ActivityFamily.RECOVERY)
        assert len(results) > 0
        assert all(a.family == ActivityFamily.RECOVERY for a in results)

    def test_filter_by_stimulus_tag(self) -> None:
        results = query_activities(stimulus_tags={ActivityStimulusTag.THRESHOLD})
        assert len(results) > 0
        for a in results:
            assert ActivityStimulusTag.THRESHOLD in a.capability_profile.stimulus_tags

    def test_filter_by_phase(self) -> None:
        results = query_activities(phase=PhaseType.TAPER)
        assert len(results) > 0
        for a in results:
            assert a.capability_profile.supports_phase(PhaseType.TAPER)

    def test_filter_by_goal(self) -> None:
        results = query_activities(goal=GoalType.IRONMAN)
        assert len(results) > 0
        for a in results:
            assert a.capability_profile.supports_goal(GoalType.IRONMAN)

    def test_filter_recovery_ok_true(self) -> None:
        results = query_activities(recovery_ok=True)
        assert len(results) > 0
        assert all(a.capability_profile.recovery_ok for a in results)

    def test_filter_recovery_ok_false(self) -> None:
        results = query_activities(recovery_ok=False)
        assert len(results) > 0
        assert all(not a.capability_profile.recovery_ok for a in results)

    def test_filter_taper_ok_true(self) -> None:
        results = query_activities(taper_ok=True)
        assert len(results) > 0
        assert all(a.capability_profile.taper_ok for a in results)

    def test_combined_filters(self) -> None:
        results = query_activities(
            sport=SportType.RUNNING,
            family=ActivityFamily.INTERVAL,
            taper_ok=True,
        )
        assert len(results) > 0
        for a in results:
            assert a.sport == SportType.RUNNING
            assert a.family == "interval"
            assert a.capability_profile.taper_ok

    def test_sorted_by_tss_min(self) -> None:
        results = query_activities(sport=SportType.RUNNING)
        tss_mins = [a.tss_min for a in results]
        assert tss_mins == sorted(tss_mins)

    def test_impossible_filter_returns_empty(self) -> None:
        results = query_activities(
            sport=SportType.SWIMMING,
            family="interval",
            recovery_ok=True,
        )
        assert results == []


class TestHelpers:
    def test_if_to_zone_low(self) -> None:
        assert _if_to_zone(0.50, HR_ZONE_THRESHOLDS) == 1

    def test_if_to_zone_boundary(self) -> None:
        # 0.85 is exactly at the zone 1 upper bound (from domain HR_BAND_SPECS).
        assert _if_to_zone(0.85, HR_ZONE_THRESHOLDS) == 1
        assert _if_to_zone(0.86, HR_ZONE_THRESHOLDS) == 2

    def test_if_to_zone_mid(self) -> None:
        assert _if_to_zone(0.91, HR_ZONE_THRESHOLDS) == 3

    def test_if_to_zone_top(self) -> None:
        assert _if_to_zone(1.20, HR_ZONE_THRESHOLDS) == 5

    def test_if_to_zone_power(self) -> None:
        assert _if_to_zone(0.80, POWER_ZONE_THRESHOLDS) == 3

    def test_if_to_zone_power_high(self) -> None:
        assert _if_to_zone(2.50, POWER_ZONE_THRESHOLDS) == 7

    def test_zone_target_returns_hr_zone(self) -> None:
        primary, secondary = _zone_target(0.70, 3)
        assert primary.type == "heart_rate_zone"
        assert secondary.type == "rpe"

    def test_hr_zone_is_raised_to_the_power_zone_for_hard_work(self) -> None:
        """A ride at tempo or harder shows the higher of its HR and power zones.

        IF 0.85 is HR Z1 by the LTHR table but power Z3 (2026-09-30). Easy
        work keeps the HR table's zone: IF 0.75 is power Z2, HR Z1.
        """
        zone = lambda i: _zone_target(i, 5, SportType.CYCLING)[0].zone  # noqa: E731
        assert zone(0.70) == 1
        assert zone(0.75) == 1
        assert zone(0.82) == 3
        assert zone(0.85) == 3
        assert zone(0.95) == 4
        assert zone(1.30) == 5  # power Z6 capped at the HR table's top

    def test_run_hr_zone_is_its_pace_zone(self) -> None:
        """A run's HR zone sits at its pace rung (Friel's zones): the two agree.

        Its IF is a fraction of threshold speed: 85 % is pace zone 2, the
        half-Ironman brick's 91 % zone 3 tempo (it read zone 4 threshold
        when raised to the power zone).
        """
        zone = lambda i: _zone_target(i, 5, SportType.RUNNING)[0].zone  # noqa: E731
        assert zone(0.70) == 1
        assert zone(0.80) == 2
        assert zone(0.85) == 2
        assert zone(0.91) == 3
        assert zone(0.97) == 4
        assert zone(1.30) == 5  # pace Z7 capped at the HR table's top

    def test_step_description_aligns_with_hr_zone_band(self) -> None:
        """R-SC-008: phrasing matches the HR zone band the IF lands in."""
        from endury.planner.activity.shapes.base import _step_description

        assert "Easy aerobic" in _step_description("interval", 0.74)
        assert "Tempo" in _step_description("interval", 0.88)  # power Z3
        assert "Threshold" in _step_description("interval", 0.92)  # power Z4
        assert "Threshold" in _step_description("interval", 0.97)
        assert "VO₂max" in _step_description("interval", 1.05)

    def test_step_description_cycling_bands_by_power(self) -> None:
        """R-SC-008: a bike step is banded by power, the metric it is prescribed in.

        Power Z3 Tempo spans 76-90 % FTP where HR Z2 Aerobic spans 85-90 %
        LTHR, so BIKE_TEMPO's 87 % FTP step read "Aerobic effort" beside a
        Z3 tempo power target (2026-09-24).
        """
        from endury.planner.activity.shapes.base import _step_description

        def bike(if_value: float, **kwargs: float) -> str:
            return _step_description(
                "interval",
                if_value,
                sport=SportType.CYCLING,
                target_preference=TargetPreference.POWER_FIRST,
                **kwargs,
            )

        assert bike(0.45).startswith("Easy aerobic effort")
        assert bike(0.65).startswith("Aerobic effort")
        assert bike(0.87).startswith("Tempo effort")
        assert bike(0.92).startswith("Threshold effort")  # sweet spot, power Z4
        assert bike(1.10, work_duration_s=240.0).startswith("VO₂max effort")
        # A run step reads its card's HR zone, its pace rung: 87 % of
        # threshold speed is pace zone 2, 91 % zone 3.
        run = _step_description("interval", 0.87, sport=SportType.RUNNING)
        assert run.startswith("Aerobic effort")
        run = _step_description("interval", 0.91, sport=SportType.RUNNING)
        assert run.startswith("Tempo effort")

    def test_step_description_cycling_supra_threshold_reps(self) -> None:
        """R-SC-015 holds above VO2max: bike anaerobic/sprint reps read by length."""
        from endury.planner.activity.shapes.base import _step_description

        def bike(if_value: float, work_duration_s: float) -> str:
            return _step_description(
                "interval",
                if_value,
                sport=SportType.CYCLING,
                target_preference=TargetPreference.POWER_FIRST,
                work_duration_s=work_duration_s,
            )

        assert "Speed rep" in bike(1.30, 40.0)  # BIKE_ANAEROBIC's 30-45 s reps
        assert "sprint" in bike(1.70, 12.0).lower()  # BIKE_SPRINT's 10-15 s reps
        long_anaerobic = bike(1.30, 180.0)
        assert "Anaerobic" in long_anaerobic
        assert "VO₂max" not in long_anaerobic

    def test_step_description_short_sprint_overrides_vo2_band(self) -> None:
        """R-SC-015: ≤30 s VO2max-band reps use sprint wording, not VO2max copy."""
        from endury.planner.activity.shapes.base import _step_description

        short = _step_description("interval", 1.15, work_duration_s=30.0)
        long_rep = _step_description("interval", 1.15, work_duration_s=240.0)
        assert "sprint" in short.lower() or "stride" in short.lower()
        assert "VO₂max" in long_rep
        assert "sprint" not in long_rep.lower()

    def test_short_vo2max_reps_are_not_called_sprints(self) -> None:
        """30/15 and 30/30 reps are hard, repeatable work: not sprints or strides."""
        for key in (ActivityKey.BIKE_VO2MAX_SHORT, ActivityKey.RUN_VO2MAX_SHORT):
            activity = get_activity(key)
            repeat = next(
                s for s in activity.build(activity.tss_max).steps if s.type == "repeat"
            )
            work = (repeat.steps[0].description or "").lower()
            assert "sprint" not in work, key
            assert "stride" not in work, key

    def test_step_description_sub_90s_vo2_band_reads_speed_rep(self) -> None:
        """R-SC-015 extension: 30-90 s VO2-band reps read as speed reps.

        VO2 kinetics need ~90 s+ of sustained work before uptake approaches
        VO2max, so RUN_SPEED's solved 45-60 s reps must never carry the
        "near maximal sustainable" VO2max copy.
        """
        from endury.planner.activity.shapes.base import _step_description

        mid = _step_description("interval", 1.12, work_duration_s=50.0)
        edge = _step_description("interval", 1.12, work_duration_s=90.0)
        assert "Speed rep" in mid
        assert "VO₂max" not in mid
        assert "VO₂max" in edge

    def test_step_description_long_rest_reads_steady_state(self) -> None:
        """R-SC-013: long recovery (≥work, ≥300 s) reads as steady-state, not active recovery."""
        from endury.planner.activity.shapes.base import _step_description

        long_rest = _step_description(
            "recovery", 0.62, work_duration_s=900.0, rest_duration_s=3300.0
        )
        short_rest = _step_description(
            "recovery", 0.55, work_duration_s=900.0, rest_duration_s=180.0
        )
        assert "Steady-state" in long_rest
        assert "Active recovery" in short_rest

    def test_step_description_power_disclaimer_only_for_running_power(self) -> None:
        """R-SC-003: disclaimer suppressed unless sport=run + POWER_FIRST."""
        from endury.planner.activity.shapes.base import _step_description
        from endury.planner.foundations.types import SportType, TargetPreference

        baseline = _step_description("interval", 1.03)
        with_power = _step_description(
            "interval",
            1.03,
            sport=SportType.RUNNING,
            target_preference=TargetPreference.POWER_FIRST,
        )
        with_pace = _step_description(
            "interval",
            1.03,
            sport=SportType.RUNNING,
            target_preference=TargetPreference.PACE_FIRST,
        )
        assert baseline == with_pace, "no disclaimer when pace-first"
        assert "running-power zone" in with_power, "disclaimer when power-first"

    def test_linear_duration_explicit_warmup_cooldown_intensities(self) -> None:
        """LinearDurationActivity honours explicit if_warmup / if_cooldown."""
        from endury.planner.activity.shapes.land import LinearDurationActivity
        from endury.planner.foundations.types import (
            ActivityFamily,
            SportType,
            TargetPreference,
        )

        act = LinearDurationActivity(
            key=ActivityKey.RUN_EASY,
            sport=SportType.RUNNING,
            family=ActivityFamily.EASY,
            hardness=EASY_HARDNESS,
            tss_min=20.0,
            tss_max=80.0,
            target_preference=TargetPreference.HR_FIRST,
            capability_profile=None,
            intensity_factor=0.75,
            rpe=3,
            if_warmup=0.62,
            if_cooldown=0.50,
        )
        assert act._resolved_if_warmup == 0.62
        assert act._resolved_if_cooldown == 0.50

    def test_select_lattice_prefers_non_overshoot(self) -> None:
        candidates = [20.0, 30.0, 40.0, 50.0]
        assert _select_lattice_target(candidates, 35.0) == 30.0

    def test_select_lattice_exact_match(self) -> None:
        candidates = [20.0, 30.0, 40.0]
        assert _select_lattice_target(candidates, 30.0) == 30.0

    def test_select_lattice_overshoot_fallback(self) -> None:
        candidates = [50.0, 60.0]
        assert _select_lattice_target(candidates, 20.0) == 50.0

    def test_round_to_swim_step_at_boundary(self) -> None:
        assert _round_to_swim_step(12.4) == SWIM_STEP_METERS
        assert _round_to_swim_step(12.5) == SWIM_STEP_METERS
        assert _round_to_swim_step(13.0) == SWIM_STEP_METERS
        assert _round_to_swim_step(37.5) == 50

    def test_round_to_100m_at_boundary(self) -> None:
        """R-DU-003: total swim distance rounds to whole 100 m."""
        from endury.planner.activity.shapes.aquatic import (
            SWIM_TOTAL_GRANULARITY_METERS,
            _round_to_100m,
        )

        assert _round_to_100m(40.0) == SWIM_TOTAL_GRANULARITY_METERS
        assert _round_to_100m(0.0) == SWIM_TOTAL_GRANULARITY_METERS
        assert _round_to_100m(149.0) == 100
        assert _round_to_100m(150.0) == 200  # banker's: round to even x 100
        assert _round_to_100m(975.0) == 1000

    def test_round_cooldown_for_100m_total(self) -> None:
        """Cooldown shifts so warmup+content+cooldown is a multiple of 100 m."""
        from endury.planner.activity.shapes.aquatic import (
            _round_cooldown_for_100m_total,
        )

        # No shift when already a multiple of 100.
        assert _round_cooldown_for_100m_total(400, 1000, 200) == 200
        # Slop of 50 rounds up via banker's to 1000 — cooldown gains 25.
        assert _round_cooldown_for_100m_total(400, 375, 200) == 225
        # Slop of 75 rounds down to a smaller cooldown (within floor).
        assert _round_cooldown_for_100m_total(200, 550, 100) == 50
        # Floor at one pool length so the cooldown remains renderable
        # even when rounded_total - warmup - content lands at or below zero.
        assert _round_cooldown_for_100m_total(0, 200, 0) == SWIM_STEP_METERS


class TestSwimHelpers:
    def test_swim_zone_target_returns_swim_pace_zone(self) -> None:
        from endury.planner.activity.shapes.aquatic import (
            _swim_zone_target,
        )

        primary, secondary = _swim_zone_target(0.95, 5)
        assert primary.type == "swim_pace_zone"
        assert secondary.type == "rpe"
        assert secondary.rpe == 5
        # Without a resolver there is no CSS to derive a pace band from.
        assert primary.low_sec_per_100m is None
        assert primary.high_sec_per_100m is None

    def test_swim_zone_target_carries_css_pace_band(self) -> None:
        """With CSS known, the athlete sees a concrete s/100m band, not
        just a bare zone number (bike shows watts, run shows pace)."""
        from endury.planner.activity.shapes.aquatic import (
            _swim_zone_target,
        )

        primary, _ = _swim_zone_target(1.0, 8, _make_resolver(css=106.0))
        assert primary.type == "swim_pace_zone"
        # CSS / IF = 106.0 center, ± TARGET_BAND_FRACTION (3%).
        assert primary.low_sec_per_100m == 102.8
        assert primary.high_sec_per_100m == 109.2

    def test_swim_zone_target_without_css_omits_pace_band(self) -> None:
        from endury.planner.activity.shapes.aquatic import (
            _swim_zone_target,
        )

        primary, _ = _swim_zone_target(1.0, 8, _make_resolver(css=None))
        assert primary.low_sec_per_100m is None
        assert primary.high_sec_per_100m is None

    def test_swim_zone_low(self) -> None:
        from endury.planner.activity.config import SWIM_PACE_ZONE_THRESHOLDS

        zone = _if_to_zone(0.70, SWIM_PACE_ZONE_THRESHOLDS)
        assert zone == 1

    def test_swim_zone_high(self) -> None:
        from endury.planner.activity.config import SWIM_PACE_ZONE_THRESHOLDS

        zone = _if_to_zone(1.20, SWIM_PACE_ZONE_THRESHOLDS)
        assert zone == 6


class TestFixedProtocolActivity:
    def test_duration_is_fixed_regardless_of_tss(self) -> None:
        act = get_activity(ActivityKey.RUN_THRESHOLD_TEST)
        d_low = act.duration_minutes_exact(act.tss_min)
        d_high = act.duration_minutes_exact(act.tss_max)
        assert d_low == d_high

    def test_achieved_tss_is_midpoint(self) -> None:
        act = get_activity(ActivityKey.BIKE_FTP_TEST)
        expected = (act.tss_min + act.tss_max) / 2.0
        assert act.achieved_tss(act.tss_min) == expected

    def test_build_produces_warmup_protocol_cooldown(self) -> None:
        # RUN_THRESHOLD_TEST has no strides - basic 3-step shape.
        act = get_activity(ActivityKey.RUN_THRESHOLD_TEST)
        workout = act.build(act.tss_min)
        assert len(workout.steps) == 3
        assert workout.steps[0].type == "warmup"
        assert workout.steps[1].type == "interval"
        assert workout.steps[2].type == "cooldown"

    def test_all_three_protocols_registered(self) -> None:
        for key in (
            ActivityKey.RUN_THRESHOLD_TEST,
            ActivityKey.BIKE_FTP_TEST,
            ActivityKey.SWIM_CSS_TEST,
        ):
            act = get_activity(key)
            assert act.family == ActivityFamily.TEST

    def test_protocols_are_available_in_maintain(self) -> None:
        # The maintenance menus put a test in every MAINTAIN recovery week,
        # so the capability profile has to agree.
        for key in (
            ActivityKey.RUN_THRESHOLD_TEST,
            ActivityKey.BIKE_FTP_TEST,
            ActivityKey.SWIM_CSS_TEST,
        ):
            profile = get_activity(key).capability_profile
            assert profile.supports_phase(PhaseType.MAINTAIN)
            assert not profile.taper_ok
            # recovery_ok stays False: it only gates the ad-hoc picker, and a
            # test is never a recovery-safe substitute there.
            assert not profile.recovery_ok


class TestPowerZoneMismatchNote:
    def test_agree_returns_empty(self) -> None:
        # Low IF maps both pace and power to zone 1 -> zones agree -> "".
        from endury.planner.activity.shapes.base import _power_zone_mismatch_note

        assert _power_zone_mismatch_note(0.5) == ""


class TestFixedProtocolStrides:
    """Cover the warmup_strides_count > 0 paths on FixedProtocolActivity.

    No registered FixedProtocolActivity sets strides, so derive one from
    RUN_THRESHOLD_TEST via dataclasses.replace.
    """

    def _with_strides(self):  # type: ignore[no-untyped-def]
        import dataclasses

        base = get_activity(ActivityKey.RUN_THRESHOLD_TEST)
        return dataclasses.replace(
            base,
            warmup_strides_count=4,
            warmup_strides_duration_seconds=20.0,
            warmup_strides_rest_seconds=40.0,
            if_warmup_strides=0.95,
        )

    def test_duration_includes_strides(self) -> None:
        act = self._with_strides()
        base = get_activity(ActivityKey.RUN_THRESHOLD_TEST)
        # Strides add 4 * (20 + 40) / 60 = 4.0 minutes.
        assert act.duration_minutes_exact(act.tss_min) == pytest.approx(
            base.duration_minutes_exact(base.tss_min) + 4.0
        )

    def test_tid_fractions_with_strides(self) -> None:
        act = self._with_strides()
        tid = act.tid_fractions(act.tss_min)
        total = tid.z_low + tid.z_mid + tid.z_high
        assert abs(total - 1.0) <= 0.01

    def test_build_inserts_strides_repeat_block(self) -> None:
        act = self._with_strides()
        # Non-rest target so the rest short-circuit is skipped.
        workout = act.build(act.tss_max)
        assert [s.type for s in workout.steps] == [
            "warmup",
            "repeat",
            "interval",
            "cooldown",
        ]
        repeat = workout.steps[1]
        assert repeat.iterations == 4


class TestSwimCssTestActivity:
    def test_shape_warmup_strides_tt1_rest_tt2_cooldown(self) -> None:
        # R-DU-007: structured CSS test renders WU -> 4x 50 m strides ->
        # 400 m TT -> wall rest -> 200 m TT -> CD.
        act = get_activity(ActivityKey.SWIM_CSS_TEST)
        workout = act.build(act.tss_min)
        assert [s.type for s in workout.steps] == [
            "warmup",
            "repeat",
            "interval",
            "recovery",
            "interval",
            "cooldown",
        ]
        assert workout.steps[1].iterations == 4

    def test_swim_steps_are_distance_based(self) -> None:
        # R-DU-007: WU, strides, both TTs, and CD all carry value_meters,
        # not value_seconds, so the card renders "400 m" not "10 min".
        act = get_activity(ActivityKey.SWIM_CSS_TEST)
        workout = act.build(act.tss_min)
        wu, strides_block, tt1, _rest, tt2, cd = workout.steps
        for step in (wu, tt1, tt2, cd):
            assert step.duration is not None
            assert step.duration.type == "distance"
            assert step.duration.value_meters is not None
            assert step.duration.value_meters > 0
        stride_step = strides_block.steps[0]
        assert stride_step.duration is not None
        assert stride_step.duration.type == "distance"
        assert stride_step.duration.value_meters == 50

    def test_time_trials_use_400m_and_200m(self) -> None:
        act = get_activity(ActivityKey.SWIM_CSS_TEST)
        workout = act.build(act.tss_min)
        _, _, tt1, _, tt2, _ = workout.steps
        assert tt1.duration is not None
        assert tt1.duration.value_meters == 400
        assert tt2.duration is not None
        assert tt2.duration.value_meters == 200

    def test_total_distance_is_multiple_of_100m(self) -> None:
        # R-DU-003: every materialised swim session must sum to a whole
        # number of hundred metres.
        act = get_activity(ActivityKey.SWIM_CSS_TEST)
        workout = act.build(act.tss_min)
        total_m = 0.0
        for step in workout.steps:
            if step.type == "repeat" and step.steps and step.iterations:
                for sub in step.steps:
                    if sub.duration and sub.duration.value_meters:
                        total_m += step.iterations * sub.duration.value_meters
            elif step.duration and step.duration.value_meters:
                total_m += step.duration.value_meters
        assert total_m % 100 == 0

    def test_duration_and_tss_are_fixed(self) -> None:
        act = get_activity(ActivityKey.SWIM_CSS_TEST)
        assert act.duration_minutes_exact(act.tss_min) == act.duration_minutes_exact(
            act.tss_max
        )
        assert act.achieved_tss(act.tss_min) == (act.tss_min + act.tss_max) / 2.0


class TestResolveCss:
    def test_returns_default_when_resolver_is_none(self) -> None:
        from endury.planner.activity.config import DEFAULT_SWIM_CSS_SEC_PER_100M
        from endury.planner.activity.shapes.aquatic import _resolve_css

        assert _resolve_css(None) == DEFAULT_SWIM_CSS_SEC_PER_100M

    def test_returns_resolver_value_when_present(self) -> None:
        from endury.planner.activity.shapes.aquatic import _resolve_css

        resolver = _make_resolver(css=88.0)
        assert _resolve_css(resolver) == 88.0

    def test_falls_back_to_default_when_resolver_has_no_css(self) -> None:
        from endury.planner.activity.config import DEFAULT_SWIM_CSS_SEC_PER_100M
        from endury.planner.activity.shapes.aquatic import _resolve_css

        resolver = _make_resolver(css=None)
        assert _resolve_css(resolver) == DEFAULT_SWIM_CSS_SEC_PER_100M


class TestWarmupCooldownTss:
    def test_returns_positive_value(self) -> None:
        from endury.planner.activity.shapes.land import _warmup_cooldown_tss

        result = _warmup_cooldown_tss(10.0, 0.65, 10.0, 0.55, 2.0, 100.0)
        assert result > 0.0

    def test_symmetric_inputs(self) -> None:
        from endury.planner.activity.shapes.land import _warmup_cooldown_tss

        result = _warmup_cooldown_tss(10.0, 0.60, 10.0, 0.60, 2.0, 100.0)
        # With identical inputs, warmup TSS == cooldown TSS.
        from endury.training.load import tss_from_duration_minutes

        single = tss_from_duration_minutes(
            10.0, 0.60, exponent=2.0, threshold_tss_per_hour=100.0
        )
        assert abs(result - 2 * single) < 1e-9


class TestEarliestPhaseWeek:
    def test_default_is_zero(self) -> None:
        act = get_activity(ActivityKey.RUN_EASY)
        assert act.capability_profile.earliest_phase_week == 0

    def test_all_registered_have_non_negative(self) -> None:
        for key, act in ACTIVITY_REGISTRY.items():
            assert act.capability_profile.earliest_phase_week >= 0, (
                f"{key}: earliest_phase_week is negative"
            )


class TestPhaseSuitability:
    """Verify the general-to-specific periodization arc."""

    def test_race_specific_excluded_from_base(self) -> None:
        for key in (
            ActivityKey.RUN_HM_PACE,
            ActivityKey.RUN_FULL_MP,
            ActivityKey.RUN_LONG_PROGRESSION,
            ActivityKey.RUN_5K_RACE_PACE,
            ActivityKey.RUN_10K_RACE_PACE,
            ActivityKey.BIKE_SPRINT,
            ActivityKey.SWIM_SPRINT,
        ):
            act = get_activity(key)
            assert not act.capability_profile.supports_phase(PhaseType.BASE), (
                f"{key} should be excluded from BASE"
            )
            assert act.capability_profile.supports_phase(PhaseType.BUILD), (
                f"{key} should be available in BUILD"
            )
            assert act.capability_profile.supports_phase(PhaseType.PEAK), (
                f"{key} should be available in PEAK"
            )

    def test_vo2max_delayed_in_base(self) -> None:
        for key in (ActivityKey.RUN_VO2MAX, ActivityKey.BIKE_VO2MAX):
            act = get_activity(key)
            assert act.capability_profile.earliest_phase_week == 3, (
                f"{key}: expected earliest_phase_week=3"
            )

    def test_moderate_intensity_delayed_in_base(self) -> None:
        for key in (ActivityKey.RUN_FARTLEK, ActivityKey.RUN_HILL_REPEATS):
            act = get_activity(key)
            assert act.capability_profile.earliest_phase_week == 2, (
                f"{key}: expected earliest_phase_week=2"
            )

    def test_speed_delayed_in_base(self) -> None:
        act = get_activity(ActivityKey.RUN_SPEED)
        assert act.capability_profile.earliest_phase_week == 3

    def test_foundation_activities_all_phases_no_delay(self) -> None:
        foundation_keys = (
            ActivityKey.RUN_EASY,
            ActivityKey.RUN_LONG,
            ActivityKey.RUN_RECOVERY,
            ActivityKey.RUN_STRIDES,
            ActivityKey.RUN_AEROBIC_TEMPO,
            ActivityKey.RUN_PROGRESSION,
            ActivityKey.BIKE_ENDURANCE,
            ActivityKey.BIKE_LONG,
            ActivityKey.BIKE_RECOVERY,
            ActivityKey.BIKE_TEMPO,
            ActivityKey.SWIM_ENDURANCE,
            ActivityKey.SWIM_CSS,
            ActivityKey.SWIM_THRESHOLD,
        )
        for key in foundation_keys:
            act = get_activity(key)
            for phase in PhaseType:
                assert act.capability_profile.supports_phase(phase), (
                    f"{key} should support {phase}"
                )
            assert act.capability_profile.earliest_phase_week == 0, (
                f"{key} should have no delay"
            )


class TestConfigZoneDerivation:
    def test_hr_thresholds_derived_from_domain(self) -> None:
        from endury.training.zones import HR_BAND_SPECS

        for spec in HR_BAND_SPECS:
            match = next(
                (z, ub) for z, ub in HR_ZONE_THRESHOLDS if z == spec.zone_number
            )
            assert match[1] == spec.high_factor

    def test_power_thresholds_derived_from_domain(self) -> None:
        from endury.training.zones import POWER_BAND_SPECS

        for spec in POWER_BAND_SPECS:
            match = next(
                (z, ub) for z, ub in POWER_ZONE_THRESHOLDS if z == spec.zone_number
            )
            assert match[1] == spec.high_factor

    def test_pace_thresholds_derived_from_domain(self) -> None:
        from endury.planner.activity.config import RUNNING_PACE_ZONE_THRESHOLDS
        from endury.training.zones import PACE_BAND_SPECS

        for spec in PACE_BAND_SPECS:
            match = next(
                (z, ub)
                for z, ub in RUNNING_PACE_ZONE_THRESHOLDS
                if z == spec.zone_number
            )
            assert match[1] == spec.high_factor


@pytest.mark.parametrize("activity_key", _REGISTERED_KEYS)
class TestAllRegisteredActivities:
    """Smoke-test each registered activity (41 total) at mid-TSS."""

    def test_build_at_mid_tss(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        workout = act.build(mid_tss)
        assert workout.workout_name == activity_key
        assert len(workout.steps) >= 1

    def test_duration_minutes_exact_positive(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        d = act.duration_minutes_exact(mid_tss)
        assert d > 0.0

    def test_achieved_tss_positive(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        achieved = act.achieved_tss(mid_tss)
        assert achieved > 0.0

    def test_tid_fractions_sum_to_one(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        tid = act.tid_fractions(mid_tss)
        total = tid.z_low + tid.z_mid + tid.z_high
        assert abs(total - 1.0) <= 0.01, (
            f"{activity_key}: TID fractions sum to {total:.4f}"
        )

    def test_build_zero_tss_produces_rest(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        workout = act.build(0.0)
        assert len(workout.steps) >= 1
        assert workout.steps[0].type == "rest"

    def test_duration_minutes_exact_at_zero(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        d = act.duration_minutes_exact(0.0)
        assert d >= 0.0

    def test_achieved_tss_at_zero(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        result = act.achieved_tss(0.0)
        assert result >= 0.0

    def test_tid_fractions_at_zero(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        tid = act.tid_fractions(0.0)
        total = tid.z_low + tid.z_mid + tid.z_high
        assert abs(total - 1.0) <= 0.01

    def test_project_target_tss_basic(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        result = act.project_target_tss(mid_tss)
        assert act.tss_min <= result <= act.tss_max + 1e-6

    def test_tss_from_duration(self, activity_key: ActivityKey) -> None:
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        d = act.duration_minutes_exact(mid_tss)
        result = act.tss_from_duration(d)
        if result is not None:
            assert result >= 0.0
        # zero-duration path covers zero-guard branches
        result_zero = act.tss_from_duration(0.0)
        if result_zero is not None:
            assert result_zero >= 0.0


class TestRecoveryRunPricing:
    """A recovery run is priced like the jog athletes actually run.

    Priced at IF 0.55 a 30-minute recovery run was worth 15 TSS, so a jog
    in the top of Zone 1 read as 160 % of plan.
    """

    def test_it_sits_one_band_below_an_easy_run(self) -> None:
        recovery = get_activity(ActivityKey.RUN_RECOVERY)
        easy = get_activity(ActivityKey.RUN_EASY)
        assert recovery.intensity_factor == pytest.approx(easy.intensity_factor - 0.10)

    def test_its_duration_window_is_unchanged(self) -> None:
        recovery = get_activity(ActivityKey.RUN_RECOVERY)
        assert recovery.duration_minutes_exact(recovery.tss_min) == pytest.approx(
            24.5, abs=0.5
        )
        assert recovery.duration_minutes_exact(recovery.tss_max) == pytest.approx(
            38.8, abs=0.5
        )


class TestIfToTidFractionsHelper:
    def test_z_low(self) -> None:
        tid = _if_to_tid_fractions(0.70)
        assert tid.z_low == 1.0

    def test_z_mid(self) -> None:
        tid = _if_to_tid_fractions(0.85)
        assert tid.z_mid == 1.0

    def test_z_high(self) -> None:
        tid = _if_to_tid_fractions(1.0)
        assert tid.z_high == 1.0


class TestTidFromSegmentsHelper:
    def test_empty_segments_returns_z_low(self) -> None:
        tid = _tid_from_segments([], SportType.RUNNING)
        assert tid.z_low == 1.0
        assert tid.z_mid == 0.0

    def test_each_sport_reads_its_own_cut_points(self) -> None:
        """A ride's LT1 is 75 % FTP, a swim's 90 % of CSS speed, a run's 0.85."""
        ride = _tid_from_segments([(60.0, 0.80)], SportType.CYCLING)
        run = _tid_from_segments([(60.0, 0.80)], SportType.RUNNING)
        swim = _tid_from_segments([(60.0, 0.88)], SportType.SWIMMING)
        assert (ride.z_mid, run.z_low, swim.z_low) == (1.0, 1.0, 1.0)


def _pace_resolver():
    """Resolver with running threshold pace and training_zones."""
    from types import SimpleNamespace

    class _Band:
        def __init__(self, zone: int, low: float, high: float) -> None:
            self.zone_number, self.low, self.high = zone, low, high

    class _HrZones:
        def __init__(self) -> None:
            self.threshold_value = 150.0
            self.bands = [
                _Band(z, float(z * 30), float(z * 30 + 30)) for z in range(1, 6)
            ]

    class _Zones:
        def get_hr_zones(self, sport: str) -> _HrZones:
            return _HrZones()

    return SimpleNamespace(
        training_zones=_Zones(),
        running_threshold_pace_min_per_km=5.0,
        running_ftp_watts=None,
        cycling_ftp_watts=None,
    )


def _power_only_resolver():
    """Resolver with running FTP only (no pace)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        training_zones=None,
        running_threshold_pace_min_per_km=None,
        running_ftp_watts=300.0,
        cycling_ftp_watts=None,
    )


def _cycling_resolver():
    """Resolver with cycling FTP."""
    from types import SimpleNamespace

    return SimpleNamespace(
        training_zones=None,
        running_threshold_pace_min_per_km=None,
        running_ftp_watts=None,
        cycling_ftp_watts=250.0,
    )


class TestZoneTargetWithResolver:
    def test_training_zones_lookup(self) -> None:
        primary, _rpe = _zone_target(0.80, 5, SportType.RUNNING, _pace_resolver())
        assert primary.type == "heart_rate_zone"
        assert primary.high_bpm is not None

    def test_materialized_run_threshold_step_carries_bpm_range(self) -> None:
        """A real LoadMetricContext built from LTHR must reach the
        rendered step label, not just the raw TargetDef.

        Regression for the bug where ``get_planner_load_context`` always
        passed ``training_zones=None``, so every HR step rendered as a bare
        zone number instead of the bpm range the pushed intervals.icu DSL
        already carries (e.g. "65-85% LTHR"). RUN_THRESHOLD's IF (0.96)
        lands in HR Z4, whose band has a real (non-zero) low boundary.
        """
        resolver = LoadMetricContext(
            running_lthr_bpm=170,
            training_zones=compute_all_zones(lthr_bpm=170),
        )
        activity = get_activity(ActivityKey.RUN_THRESHOLD)
        workout = activity.build(60.0, resolver=resolver)
        flat_steps = build_flat_steps(workout.steps)
        interval = next(
            s
            for s in flat_steps
            if s.step_type == "interval" and s.zone_band == "z_mid"
        )
        assert "bpm" in interval.primary_label


class TestBuildStepTargetsWithResolver:
    def test_pace_first_with_pace_resolver(self) -> None:
        primary, _secondary, _additional = _build_step_targets(
            0.80, SportType.RUNNING, 5, TargetPreference.PACE_FIRST, _pace_resolver()
        )
        assert primary.type == "pace"

    def test_pace_first_power_only_resolver(self) -> None:
        """PACE_FIRST but no pace available → power promoted to primary."""
        primary, _secondary, _additional = _build_step_targets(
            0.80,
            SportType.RUNNING,
            5,
            TargetPreference.PACE_FIRST,
            _power_only_resolver(),
        )
        assert primary.type == "power"

    def test_power_first_cycling_resolver(self) -> None:
        primary, _secondary, _additional = _build_step_targets(
            0.80,
            SportType.CYCLING,
            5,
            TargetPreference.POWER_FIRST,
            _cycling_resolver(),
        )
        assert primary.type == "power"

    def test_bike_tempo_main_step_reads_tempo_beside_its_power_band(self) -> None:
        """R-SC-008: BIKE_TEMPO at FTP 250 W is a 211-224 W (Z3) step, so it reads tempo.

        The description used to come from the HR table, where 0.87 is Z2
        "Aerobic effort" — contradicting the tempo power band beside it.
        """
        activity = get_activity(ActivityKey.BIKE_TEMPO)
        workout = activity.build(60.0, resolver=_cycling_resolver())
        main = workout.steps[1]
        assert main.type == "interval"
        assert main.target is not None
        assert main.target.type == "power"
        assert (main.target.low_watts, main.target.high_watts) == (211, 224)
        power_zone = next(
            t for t in main.additional_targets or [] if t.type == "power_zone"
        )
        assert power_zone.zone == 3
        assert main.description is not None
        assert main.description.startswith("Tempo effort")

    def test_hr_first_with_pace_resolver_puts_pace_in_extras(self) -> None:
        primary, _secondary, additional = _build_step_targets(
            0.80, SportType.RUNNING, 5, TargetPreference.HR_FIRST, _pace_resolver()
        )
        assert primary.type == "heart_rate_zone"
        assert any(t.type == "pace" for t in additional)

    def test_sub_zone1_pace_drops_the_zone_tag(self) -> None:
        """Warm-up IF below the pace table's floor gets a band but no zone tag.

        Pace Z1 starts at IF 1/1.40 (~0.714); a 0.65 warm-up is easier than
        that, so tagging it "Pace Z1" would contradict the explicit band
        printed beside it.
        """
        primary, secondary, additional = _build_step_targets(
            0.65, SportType.RUNNING, 2, TargetPreference.PACE_FIRST, _pace_resolver()
        )
        assert primary.type == "pace"
        all_targets = [primary, secondary, *additional]
        assert not any(t.type == "pace_zone" for t in all_targets)
        # Threshold 5.00 min/km / 0.65 = 7.69 center; Z1's slowest pace is
        # 5.00 / 0.7143 = 7.00 — the whole band sits outside Zone 1.
        assert primary.low_min_per_km == 7.46
        assert primary.high_min_per_km == 7.92

    def test_pace_zone_tag_kept_inside_zone_one(self) -> None:
        """IF 0.75 is inside pace Z1 (0.714-0.775) — the tag stays."""
        _primary, _secondary, additional = _build_step_targets(
            0.75, SportType.RUNNING, 3, TargetPreference.PACE_FIRST, _pace_resolver()
        )
        pace_zone = next(t for t in additional if t.type == "pace_zone")
        assert pace_zone.zone == 1

    def test_pace_zone_tag_clamps_above_top_band(self) -> None:
        """Above the top band the tag still clamps to the top zone."""
        _primary, _secondary, additional = _build_step_targets(
            1.50, SportType.RUNNING, 9, TargetPreference.PACE_FIRST, _pace_resolver()
        )
        pace_zone = next(t for t in additional if t.type == "pace_zone")
        assert pace_zone.zone == 7

    def test_sub_zone1_pace_keeps_hr_zone_one(self) -> None:
        """The HR table opens at 0.00, so a 0.65 warm-up is honestly HR Z1."""
        _primary, secondary, _additional = _build_step_targets(
            0.65, SportType.RUNNING, 2, TargetPreference.PACE_FIRST, _pace_resolver()
        )
        assert secondary.type == "heart_rate_zone"
        assert secondary.zone == 1
        assert (secondary.low_bpm, secondary.high_bpm) == (30, 60)

    def test_easy_cycling_keeps_power_zone_one(self) -> None:
        """The power table also opens at 0.00 — an easy spin stays Power Z1."""
        _primary, _secondary, additional = _build_step_targets(
            0.45,
            SportType.CYCLING,
            2,
            TargetPreference.POWER_FIRST,
            _cycling_resolver(),
        )
        power_zone = next(t for t in additional if t.type == "power_zone")
        assert power_zone.zone == 1

    @pytest.mark.parametrize("key", [ActivityKey.RUN_LONG, ActivityKey.RUN_BRICK_LONG])
    def test_long_run_main_step_carries_pace_zone_tag(self, key: ActivityKey) -> None:
        """The long runs are authored above the pace-Z1 floor.

        The main step's IF must sit at or above 1/1.40 (~0.714), the
        pace-zone floor, so the tag attaches as Pace Z1.
        """
        act = get_activity(key)
        workout = act.build(act.tss_max, resolver=_pace_resolver())
        main = workout.steps[1]
        all_targets = [
            t
            for t in (
                main.target,
                main.secondary_target,
                *(main.additional_targets or []),
            )
            if t is not None
        ]
        pace_zone = next(t for t in all_targets if t.type == "pace_zone")
        assert pace_zone.zone == 1


class TestActivityDefinitionValidation:
    def _easy_cap(self) -> ActivityCapabilityProfile:
        return ActivityCapabilityProfile(
            sport=SportType.RUNNING,
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC,),
        )

    def _make_linear(self, **overrides) -> LinearDurationActivity:
        defaults = {
            "key": ActivityKey.RUN_EASY,
            "sport": SportType.RUNNING,
            "family": ActivityFamily.EASY,
            "hardness": EASY_HARDNESS,
            "tss_min": 20.0,
            "tss_max": 80.0,
            "target_preference": TargetPreference.PACE_FIRST,
            "capability_profile": self._easy_cap(),
            "intensity_factor": 0.65,
            "rpe": 3,
        }
        defaults.update(overrides)
        return LinearDurationActivity(**defaults)

    def test_tss_min_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="tss_min must be >= 0"):
            self._make_linear(tss_min=-1.0)

    def test_tss_max_less_than_min_raises(self) -> None:
        with pytest.raises(ValueError, match="tss_max must be >= tss_min"):
            self._make_linear(tss_min=30.0, tss_max=10.0)

    def test_capability_profile_none_autogen(self) -> None:
        act = self._make_linear(capability_profile=None)
        assert act.capability_profile is not None
        assert act.capability_profile.sport == SportType.RUNNING

    def test_sport_mismatch_raises(self) -> None:
        bad_cap = ActivityCapabilityProfile(
            sport=SportType.CYCLING,
            family=ActivityFamily.EASY,
            stimulus_tags=(),
        )
        with pytest.raises(ValueError, match="sport must match"):
            self._make_linear(capability_profile=bad_cap)

    def test_family_mismatch_raises(self) -> None:
        bad_cap = ActivityCapabilityProfile(
            sport=SportType.RUNNING,
            family=ActivityFamily.TEMPO,
            stimulus_tags=(),
        )
        with pytest.raises(ValueError, match="family must match"):
            self._make_linear(capability_profile=bad_cap)


class TestProjectTargetTssEdge:
    def test_upper_less_than_lower_clamps_to_lower(self) -> None:
        act = get_activity(ActivityKey.RUN_EASY)
        result = act.project_target_tss(
            25.0, lower_bound_tss=30.0, upper_bound_tss=20.0
        )
        assert result == pytest.approx(30.0)


class TestCadenceInBuild:
    def _make_fixed_block_with_cadence(self) -> FixedBlockActivity:
        return FixedBlockActivity(
            key=ActivityKey.RUN_TEMPO,
            sport=SportType.RUNNING,
            family=ActivityFamily.TEMPO,
            hardness=HARD_HARDNESS,
            tss_min=40.0,
            tss_max=80.0,
            target_preference=TargetPreference.PACE_FIRST,
            capability_profile=None,
            warmup_min=10.0,
            cooldown_min=10.0,
            if_warmup=0.65,
            if_main=0.88,
            if_cooldown=0.65,
            main_rpe=7,
            cadence_low_rpm=80,
            cadence_high_rpm=90,
        )

    def _make_progressive_block_with_cadence(self) -> ProgressiveBlockActivity:
        return ProgressiveBlockActivity(
            key=ActivityKey.RUN_PROGRESSION,
            sport=SportType.RUNNING,
            family=ActivityFamily.TEMPO,
            hardness=HARD_HARDNESS,
            tss_min=40.0,
            tss_max=90.0,
            target_preference=TargetPreference.PACE_FIRST,
            capability_profile=None,
            warmup_min=10.0,
            cooldown_min=10.0,
            if_warmup=0.65,
            if_main_start=0.72,
            if_main_end=0.88,
            if_cooldown=0.65,
            rpe_start=5,
            rpe_end=7,
            cadence_low_rpm=80,
            cadence_high_rpm=90,
        )

    def _make_interval_piecewise_with_cadence(self) -> IntervalPiecewiseActivity:
        return IntervalPiecewiseActivity(
            key=ActivityKey.RUN_THRESHOLD,
            sport=SportType.RUNNING,
            family=ActivityFamily.INTERVAL,
            hardness=HARD_HARDNESS,
            tss_min=40.0,
            tss_max=90.0,
            target_preference=TargetPreference.PACE_FIRST,
            capability_profile=None,
            warmup_min=10.0,
            cooldown_min=10.0,
            if_warmup=0.65,
            if_cooldown=0.65,
            if_work=1.0,
            if_rest=0.50,
            work_rpe=8,
            rest_rpe=3,
            rep_work_min=4.0,
            rep_work_max=6.0,
            rep_rest_min=2.0,
            rep_count_min=3,
            rep_count_max=6,
            cadence_low_rpm=80,
            cadence_high_rpm=90,
        )

    def test_fixed_block_cadence_in_main_step(self) -> None:
        act = self._make_fixed_block_with_cadence()
        workout = act.build(60.0)
        main_step = workout.steps[1]
        additional = main_step.additional_targets or []
        assert any(t.type == "cadence" for t in additional)

    def test_progressive_block_cadence_in_steps(self) -> None:
        act = self._make_progressive_block_with_cadence()
        workout = act.build(60.0)
        has_cadence = any(
            any(t.type == "cadence" for t in (s.additional_targets or []))
            for s in workout.steps
            if s.type == "interval"
        )
        assert has_cadence

    def test_interval_piecewise_cadence_in_work_step(self) -> None:
        act = self._make_interval_piecewise_with_cadence()
        workout = act.build(60.0)
        repeat_step = next(s for s in workout.steps if s.type == "repeat")
        work_step = next(s for s in (repeat_step.steps or []) if s.type == "interval")
        additional = work_step.additional_targets or []
        assert any(t.type == "cadence" for t in additional)

    def test_linear_duration_cadence_in_step(self) -> None:
        """LinearDurationActivity must surface cadence target when bounds are set."""
        act = LinearDurationActivity(
            key=ActivityKey.BIKE_ENDURANCE,
            sport=SportType.CYCLING,
            family=ActivityFamily.EASY,
            hardness=EASY_HARDNESS,
            tss_min=20.0,
            tss_max=80.0,
            target_preference=TargetPreference.POWER_FIRST,
            capability_profile=None,
            intensity_factor=0.65,
            rpe=4,
            cadence_low_rpm=85,
            cadence_high_rpm=95,
        )
        workout = act.build(40.0)
        # The main interval is the second step now that LinearDurationActivity
        # always carves WU/CD per R-SC-004.
        main_step = next(s for s in workout.steps if s.type == "interval")
        additional = main_step.additional_targets or []
        assert any(t.type == "cadence" for t in additional)


class TestProgressionStepsOne:
    def _make_one_step(self) -> ProgressiveBlockActivity:
        return ProgressiveBlockActivity(
            key=ActivityKey.RUN_PROGRESSION,
            sport=SportType.RUNNING,
            family=ActivityFamily.TEMPO,
            hardness=HARD_HARDNESS,
            tss_min=40.0,
            tss_max=90.0,
            target_preference=TargetPreference.PACE_FIRST,
            capability_profile=None,
            warmup_min=10.0,
            cooldown_min=10.0,
            if_warmup=0.65,
            if_main_start=0.72,
            if_main_end=0.88,
            if_cooldown=0.65,
            rpe_start=5,
            rpe_end=7,
            progression_steps=1,
        )

    def test_step_if_single_step(self) -> None:
        act = self._make_one_step()
        if_val = act._step_if(0)
        assert if_val == pytest.approx((act.if_main_start + act.if_main_end) / 2.0)

    def test_step_rpe_single_step(self) -> None:
        act = self._make_one_step()
        rpe_val = act._step_rpe(0)
        assert rpe_val == round((act.rpe_start + act.rpe_end) / 2.0)


class TestRegistryErrors:
    def test_duplicate_key_raises(self) -> None:
        from endury.planner.activity.registry import register_activity

        existing = get_activity(ActivityKey.RUN_EASY)
        with pytest.raises(ValueError, match="duplicate activity key"):
            register_activity(existing)

    def test_invalid_stimulus_tags_raises(self) -> None:
        from endury.planner.activity.registry import (
            ACTIVITY_REGISTRY,
            register_activity,
        )

        key = ActivityKey.RUN_EASY
        original = ACTIVITY_REGISTRY.pop(key)
        try:
            bad_cap = ActivityCapabilityProfile(
                sport=SportType.RUNNING,
                family=ActivityFamily.EASY,
                stimulus_tags=("not_a_tag",),  # type: ignore[arg-type]
            )
            act = LinearDurationActivity(
                key=key,
                sport=SportType.RUNNING,
                family=ActivityFamily.EASY,
                hardness=EASY_HARDNESS,
                tss_min=20.0,
                tss_max=80.0,
                target_preference=TargetPreference.PACE_FIRST,
                capability_profile=bad_cap,
                intensity_factor=0.65,
                rpe=3,
            )
            with pytest.raises(ValueError, match="invalid stimulus_tags"):
                register_activity(act)
        finally:
            ACTIVITY_REGISTRY[key] = original


class TestSwimLinearEdgeCases:
    """Cover upper<lower guard and no-candidates fallback in SwimLinearDistanceActivity."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_inverted_bounds_returns_lower(self) -> None:
        """upper_bound_tss < lower_bound_tss must clamp upper to lower."""
        act = get_activity(ActivityKey.SWIM_ENDURANCE)
        result = act.project_target_tss(
            30.0, lower_bound_tss=40.0, upper_bound_tss=30.0
        )
        assert result == pytest.approx(40.0)

    def test_no_candidates_returns_clamped_target(self) -> None:
        """When no 25m-quantized TSS falls in the range, return clamped target."""
        act = get_activity(ActivityKey.SWIM_ENDURANCE)
        # The lattice has values at ~23.501 and ~24.012; a range [23.6, 23.9]
        # contains no lattice value, so the fallback returns target=23.75.
        result = act.project_target_tss(
            23.75, lower_bound_tss=23.6, upper_bound_tss=23.9
        )
        assert result == pytest.approx(23.75)


class TestSwimLinearBlockwise:
    """The card's minutes and load are its steps': each block at its own pace."""

    RESOLVER = LoadMetricContext(swim_css_sec_per_100m=107.0)

    @staticmethod
    def _blockwise_tss(act: SwimLinearDistanceActivity, steps) -> float:  # type: ignore[no-untyped-def]
        """Sum each swum step's load at the IF its block is timed at."""
        if_by_type = {
            "warmup": act._resolved_if_warmup,
            "interval": act.intensity_factor,
            "cooldown": act._resolved_if_cooldown,
        }
        total = 0.0
        for step in steps:
            if step.type == "repeat":
                total += step.iterations * TestSwimLinearBlockwise._blockwise_tss(
                    act, step.steps
                )
            elif step.duration.value_meters:
                total += _swim_meters_tss(
                    step.duration.value_meters,
                    if_by_type[step.type],
                    107.0,
                    load_exponent=act.load_exponent,
                    threshold_tss_per_hour=act.threshold_tss_per_hour,
                )
        return total

    @pytest.mark.parametrize(
        ("key", "target"),
        [(ActivityKey.SWIM_ENDURANCE, 50.0), (ActivityKey.SWIM_LONG, 80.0)],
    )
    def test_duration_and_load_are_the_built_steps(
        self, key: ActivityKey, target: float
    ) -> None:
        act = get_activity(key)
        steps = act.build(target, resolver=self.RESOLVER).steps
        achieved = act.achieved_tss(target, resolver=self.RESOLVER)
        assert act.duration_minutes_exact(
            target, resolver=self.RESOLVER
        ) == pytest.approx(_workout_minutes(steps))
        assert achieved == pytest.approx(self._blockwise_tss(act, steps))
        assert sum(
            tss_from_duration_minutes(
                minutes,
                intensity,
                exponent=act.load_exponent,
                threshold_tss_per_hour=act.threshold_tss_per_hour,
            )
            for minutes, intensity in act.segments(target, resolver=self.RESOLVER)
        ) == pytest.approx(achieved)


class TestSwimCssEdgeCases:
    """Cover upper<lower guard and no-candidates fallback in SwimCssIntervalActivity."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_inverted_bounds_returns_lower(self) -> None:
        """upper_bound_tss < lower_bound_tss must clamp upper to lower."""
        act = get_activity(ActivityKey.SWIM_CSS)
        result = act.project_target_tss(
            30.0, lower_bound_tss=40.0, upper_bound_tss=30.0
        )
        assert result == pytest.approx(40.0)

    def test_no_candidates_returns_clamped_target(self) -> None:
        """When no (reps, meters) combo lands in the narrow range, return target."""
        act = get_activity(ActivityKey.SWIM_CSS)
        # Lattice values jump from ~30.56 to ~33.89 (a 500 m warm-up since
        # 2026-10-11); range [31.0, 33.0] has none.
        result = act.project_target_tss(
            32.0, lower_bound_tss=31.0, upper_bound_tss=33.0
        )
        assert result == pytest.approx(32.0)


class TestSwimDrillSwimEdgeCases:
    """Cover upper<lower guard and no-candidates fallback in SwimDrillSwimActivity."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_inverted_bounds_returns_lower(self) -> None:
        """upper_bound_tss < lower_bound_tss must clamp upper to lower."""
        act = get_activity(ActivityKey.SWIM_DRILL)
        result = act.project_target_tss(
            15.0, lower_bound_tss=20.0, upper_bound_tss=15.0
        )
        assert result == pytest.approx(20.0)

    def test_no_candidates_returns_clamped_target(self) -> None:
        """When no rep count lands in the narrow range, return clamped target."""
        act = get_activity(ActivityKey.SWIM_DRILL)
        # Post R-DU-003 the cooldown shifts to land totals on 100 m, so the
        # rep-count lattice clusters: reps 10/11/12 ≈ 14.39/14.56/16.27.
        # The range [14.7, 16.1] sits in the gap between 14.56 and 16.27.
        result = act.project_target_tss(
            15.0, lower_bound_tss=14.7, upper_bound_tss=16.1
        )
        assert result == pytest.approx(15.0)


class TestSwimDrillAerobicBlock:
    """Past ten drill reps the card swims the rest as easy whole-stroke."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    @staticmethod
    def _card(minutes: float, css: float | None = None):
        act = get_activity(ActivityKey.SWIM_DRILL)
        resolver = None if css is None else LoadMetricContext(swim_css_sec_per_100m=css)
        tss = assignment_target_tss(act, minutes / 60.0, resolver)
        return act, tss, act.build(tss, resolver=resolver), resolver

    def test_short_booking_is_the_drill_set_alone(self) -> None:
        """A booking the set holds carries no aerobic block."""
        _act, _tss, workout, _ = self._card(30.0)
        assert [s.type for s in workout.steps] == ["warmup", "repeat", "cooldown"]

    def test_long_booking_adds_the_block_after_ten_reps(self) -> None:
        """An 1800 m booking keeps ten drills and swims the rest easy."""
        act, tss, workout, _ = self._card(47.0)
        types = [s.type for s in workout.steps]
        assert types == ["warmup", "repeat", "interval", "cooldown"]
        assert workout.steps[1].iterations == act.rep_count_max
        block = workout.steps[2]
        assert block.description == act.aerobic_description
        assert block.duration.value_meters % 100 == 0
        assert workout_total_meters(workout.steps) == pytest.approx(1800.0)
        assert act.duration_minutes_exact(tss) == pytest.approx(47.0, abs=1.5)
        assert sum(m for m, _ in act.segments(tss)) == pytest.approx(
            act.duration_minutes_exact(tss)
        )

    def test_block_tops_out_at_its_cap(self) -> None:
        """The longest card is ten reps and the block's cap, 2500 m in all."""
        act, _tss, workout, _ = self._card(500.0)
        assert workout.steps[2].duration.value_meters == act.aerobic_max_meters
        assert workout_total_meters(workout.steps) == pytest.approx(2500.0)

    def test_slow_swimmer_swims_the_same_metres(self) -> None:
        """At CSS 2:30 the same card takes longer and swims the same metres."""
        act, _tss, base, _ = self._card(47.0)
        slow_minutes = 47.0 * 150.0 / 120.0
        _act, tss, slow, resolver = self._card(slow_minutes, css=150.0)
        assert workout_total_meters(slow.steps) == workout_total_meters(base.steps)
        assert act.achieved_tss(tss, resolver=resolver) > 0.0

    def test_lattice_is_reachable(self) -> None:
        """Every lattice point projects onto itself and builds its metres."""
        act = get_activity(ActivityKey.SWIM_DRILL)
        for reps, aerobic in act._lattice():
            tss = act._point_tss(reps, aerobic, None)
            assert act.project_target_tss(tss) == pytest.approx(tss)
            assert act._solve(tss, None) == (reps, aerobic)


class TestSwimSessionInvariants:
    """Cross-activity invariants for swim sessions (R-DU-003 to R-DU-006)."""

    SWIM_KEYS = (
        ActivityKey.SWIM_ENDURANCE,
        ActivityKey.SWIM_LONG,
        ActivityKey.SWIM_RECOVERY,
        ActivityKey.SWIM_CSS,
        ActivityKey.SWIM_CSS_SHORT,
        ActivityKey.SWIM_VO2MAX,
        ActivityKey.SWIM_VO2MAX_SHORT,
        ActivityKey.SWIM_THRESHOLD,
        ActivityKey.SWIM_SPRINT,
        ActivityKey.SWIM_DRILL,
        ActivityKey.SWIM_PULL,
        ActivityKey.SWIM_KICK,
    )

    def setup_method(self) -> None:
        ensure_registry_loaded()

    @staticmethod
    def _total_meters(workout) -> int:  # type: ignore[no-untyped-def]
        total = 0.0
        for step in workout.steps:
            if step.type == "repeat" and step.steps:
                iters = step.iterations or 1
                for sub in step.steps:
                    if sub.duration and sub.duration.value_meters:
                        total += iters * sub.duration.value_meters
            elif step.duration and step.duration.value_meters:
                total += step.duration.value_meters
        return round(total)

    @pytest.mark.parametrize("key", SWIM_KEYS)
    def test_total_distance_is_multiple_of_100m(self, key: ActivityKey) -> None:
        """R-DU-003: every materialised swim session sums to a whole 100 m."""
        act = get_activity(key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        workout = act.build(mid_tss)
        total = self._total_meters(workout)
        assert total % 100 == 0, (
            f"{key.value}: total {total} m is not a multiple of 100 m"
        )

    @pytest.mark.parametrize("key", SWIM_KEYS)
    def test_rep_intervals_are_at_least_50m(self, key: ActivityKey) -> None:
        """R-DU-004: every rep interval in a swim session spans at least 50 m.

        For drill activities a rep is the drill+swim pair (≥ 50 m total);
        for CSS interval activities the rep equals ``rep_meters_min``.
        """
        act = get_activity(key)
        rep_min = getattr(act, "rep_meters_min", None)
        if rep_min is not None:
            assert rep_min >= 50, (
                f"{key.value} rep_meters_min ({rep_min}) < 50 (R-DU-004)"
            )
        drill_m = getattr(act, "drill_meters", None)
        swim_m = getattr(act, "swim_meters", None)
        if drill_m is not None and swim_m is not None:
            assert drill_m + swim_m >= 50, (
                f"{key.value} drill+swim pair ({drill_m}+{swim_m}) < 50 m (R-DU-004)"
            )

    def test_drill_activity_carries_drill_description(self) -> None:
        """R-DU-005: drill steps render a coaching explanation."""
        act = get_activity(ActivityKey.SWIM_DRILL)
        workout = act.build((act.tss_min + act.tss_max) / 2.0)
        repeat = next(s for s in workout.steps if s.type == "repeat")
        assert repeat.steps is not None
        drill_step, swim_step = repeat.steps
        assert drill_step.description is not None
        assert "drill" in drill_step.description.lower()
        assert "freestyle" in drill_step.description.lower()
        assert swim_step.description is not None
        assert "freestyle" in swim_step.description.lower()

    @pytest.mark.parametrize("key", SWIM_KEYS)
    def test_swim_step_carries_stroke_metadata(self, key: ActivityKey) -> None:
        """R-DU-006: the work step of every swim session names a stroke."""
        act = get_activity(key)
        workout = act.build((act.tss_min + act.tss_max) / 2.0)
        descriptions: list[str] = []
        for step in workout.steps:
            if step.description:
                descriptions.append(step.description.lower())
            if step.steps:
                descriptions.extend(
                    sub.description.lower() for sub in step.steps if sub.description
                )
        joined = " | ".join(descriptions)
        assert any(
            stroke in joined
            for stroke in ("freestyle", "backstroke", "breaststroke", "kick")
        ), f"{key.value} workout has no stroke metadata; descriptions={joined!r}"


#: Activity key → the longest rep the athlete may ever be handed (R-DU-008).
#: Pinned here so widening a cap — or adding an uncapped continuous swim — is
#: a reviewed diff, not a silent regression.
_SWIM_REP_CAPS: dict[ActivityKey, int] = {
    ActivityKey.SWIM_ENDURANCE: 400,
    ActivityKey.SWIM_LONG: 1000,
    ActivityKey.SWIM_RECOVERY: 400,
    ActivityKey.SWIM_PULL: 400,
    ActivityKey.SWIM_KICK: 200,
}


class TestSwimLinearRepCap:
    """R-DU-008: swim main sets are handed over as swimmable reps.

    A single 1,600-5,500 m continuous interval is not a set anyone swims off
    a pace clock.  Every continuous-distance swim breaks its main block into
    whole-hundred reps of even length, capped per activity, without moving a
    metre of session volume; the wall rest after each rep is a step the
    card's clock counts.
    """

    CAPPED = tuple(_SWIM_REP_CAPS.items())

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_every_continuous_swim_declares_a_rep_cap(self) -> None:
        """No ``SwimLinearDistanceActivity`` may ship uncapped."""
        declared = {
            key: get_activity(key).max_rep_meters
            for key in ActivityKey
            if isinstance(get_activity(key), SwimLinearDistanceActivity)
        }
        assert declared == _SWIM_REP_CAPS

    @staticmethod
    def _targets(act) -> list[float]:  # type: ignore[no-untyped-def]
        """Every whole-TSS target across the activity's declared window."""
        return [float(t) for t in range(int(act.tss_min), int(act.tss_max) + 1)]

    @staticmethod
    def _main_set(workout) -> list:  # type: ignore[no-untyped-def]
        """The main set's steps in swim order, repeat blocks expanded."""
        out = []
        for step in workout.steps:
            if step.type == "repeat" and step.steps:
                out.extend(step.steps * (step.iterations or 1))
            elif step.type not in {"warmup", "cooldown"}:
                out.append(step)
        return out

    @staticmethod
    def _expected_main_meters(act, target: float) -> int:  # type: ignore[no-untyped-def]
        """The main-block distance the unsplit shape would have prescribed."""
        return act._main_meters(target)

    @pytest.mark.parametrize(("key", "cap"), CAPPED)
    def test_no_main_set_rep_exceeds_the_cap(self, key: ActivityKey, cap: int) -> None:
        act = get_activity(key)
        for target in self._targets(act):
            for step in self._main_set(act.build(target)):
                if step.type != "interval":
                    continue
                meters = step.duration.value_meters
                assert meters is not None
                assert meters <= cap, (
                    f"{key.value} at {target} TSS prescribes a {meters:.0f} m "
                    f"rep (cap {cap} m)"
                )

    @pytest.mark.parametrize(("key", "cap"), CAPPED)
    def test_split_preserves_total_distance(self, key: ActivityKey, cap: int) -> None:
        """Breaking the block up must not shrink the session by one metre."""
        act = get_activity(key)
        for target in self._targets(act):
            workout = act.build(target)
            expected_main = self._expected_main_meters(act, target)
            assert workout_total_meters(workout.steps) == (
                act.warmup_meters + expected_main + act.cooldown_meters
            ), f"{key.value} at {target} TSS lost distance in the rep split"

    @pytest.mark.parametrize(("key", "cap"), CAPPED)
    def test_split_reps_rest_at_the_wall(self, key: ActivityKey, cap: int) -> None:
        """Every rep of a split set is followed by a wall rest the card counts."""
        act = get_activity(key)
        splits = 0
        for target in self._targets(act):
            workout = act.build(target)
            main_set = self._main_set(workout)
            if len(main_set) > 1:
                splits += 1
                assert [step.type for step in main_set] == ["interval", "rest"] * (
                    len(main_set) // 2
                ), f"{key.value} at {target} TSS: a rep without its wall rest"
                for rest in main_set[1::2]:
                    assert rest.duration.value_seconds == 15.0
                    lead = rest.description.split(" — ")[0]
                    assert not any(char.isdigit() for char in lead)
            assert act.duration_minutes_exact(target) == pytest.approx(
                _workout_minutes(workout.steps)
            )
        assert splits, f"{key.value} never splits its main set"


class TestAchievedTssWithinDeclaredRange:
    """R-SC-005: achieved TSS must lie within the declared tss_min..tss_max range.

    Iterates the registry and checks each activity at lower-bound, midpoint,
    and upper-bound targets so the catalog never displays an achieved TSS
    that contradicts the prominently rendered TSS-range badge.
    """

    def setup_method(self) -> None:
        ensure_registry_loaded()

    @pytest.mark.parametrize("key", list(ACTIVITY_REGISTRY.keys()))
    def test_achieved_tss_within_bounds(self, key: ActivityKey) -> None:
        """achieved_tss(target) ∈ [tss_min, tss_max] for low/mid/high targets."""
        act = get_activity(key)
        targets = [
            act.tss_min,
            (act.tss_min + act.tss_max) / 2.0,
            act.tss_max,
        ]
        for target in targets:
            projected = act.project_target_tss(target)
            achieved = act.achieved_tss(projected)
            tolerance = max(0.5, act.tss_max * 0.01)
            assert act.tss_min - tolerance <= achieved <= act.tss_max + tolerance, (
                f"{key.value}: achieved={achieved:.2f} outside "
                f"[{act.tss_min}, {act.tss_max}] for target={target:.2f} "
                f"(projected={projected:.2f})"
            )


class TestIntervalPiecewiseEdgeCases:
    """Cover edge cases in IntervalPiecewiseActivity.project_target_tss."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_inverted_bounds_clamps_upper_to_lower(self) -> None:
        """upper_bound_tss < lower_bound_tss must clamp upper to lower."""
        act = get_activity(ActivityKey.BIKE_THRESHOLD)
        # lower=65 and upper=55 (inverted) within the activity's feasible range.
        result = act.project_target_tss(
            60.0, lower_bound_tss=65.0, upper_bound_tss=55.0
        )
        # upper clamped to lower=65; requested=65.0 lands in reps=3 interval.
        assert result == pytest.approx(65.0)

    def test_bounds_below_rep_range_triggers_fallback(self) -> None:
        """Bounds entirely below the rep-TSS range trigger the full-range fallback."""
        act = get_activity(ActivityKey.BIKE_THRESHOLD)
        # tss_min=45, first rep starts at ~51.9; bounds [46, 50] have no primary intervals.
        result = act.project_target_tss(
            48.0, lower_bound_tss=46.0, upper_bound_tss=50.0
        )
        # Fallback returns the nearest feasible TSS (min interval_low ~51.9).
        assert result >= act.tss_min

    def test_bounds_below_all_intervals_uses_full_range_fallback(self) -> None:
        """Bounds entirely below interval coverage make the primary list empty.

        The first ``_intervals(lower, upper)`` returns [] (every rep's
        interval_low exceeds the clip_upper), so the method falls back to
        ``_intervals(self.tss_min, self.tss_max)`` and returns the nearest
        feasible TSS.
        """
        act = get_activity(ActivityKey.BIKE_THRESHOLD)
        # Interval coverage starts at ~38.2; bounds [10, 30] sit entirely below it.
        result = act.project_target_tss(
            20.0, lower_bound_tss=10.0, upper_bound_tss=30.0
        )
        # Fallback returns the lattice floor: the smallest feasible pattern
        # (rep_count_min reps of rep_work_min work).
        lattice_floor = act._fixed_tss() + act.rep_count_min * (
            act.rep_work_min * act._work_rate() + act.rep_rest_min * act._rest_rate()
        )
        assert result == pytest.approx(lattice_floor)


class TestFlexibleRepDurationInversion:
    """R-SESS-B-008: flexible-rep shapes derive TSS from the solved duration.

    Before the inversion existed these shapes returned ``None`` from
    ``tss_from_duration`` and every card rendered at mid-window TSS — a
    ``bike_threshold`` solved at 75 min built the same workout as one solved
    at 50 min.
    """

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_solved_hours_drive_the_interval_pattern(self) -> None:
        """Different solved durations must build different-sized cards."""
        act = get_activity(ActivityKey.BIKE_THRESHOLD)
        short = act.tss_from_duration(50.0)
        long = act.tss_from_duration(75.0)
        assert short is not None
        assert long is not None
        assert long > short
        gap = act.duration_minutes_exact(long) - act.duration_minutes_exact(short)
        assert gap > 15.0

    @pytest.mark.parametrize(
        "activity_key",
        [
            ActivityKey.BIKE_THRESHOLD,  # IntervalPiecewiseActivity
            ActivityKey.SWIM_CSS,  # SwimCssIntervalActivity
            ActivityKey.SWIM_DRILL,  # SwimDrillSwimActivity
        ],
    )
    def test_mid_lattice_round_trip(self, activity_key: ActivityKey) -> None:
        """A realizable duration must survive the duration→TSS→duration trip."""
        act = get_activity(activity_key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        realizable = act.duration_minutes_exact(mid_tss)
        derived = act.tss_from_duration(realizable)
        assert derived is not None
        realized = act.duration_minutes_exact(derived)
        assert realized == pytest.approx(realizable, abs=0.5)

    def test_fixed_protocols_stay_duration_independent(self) -> None:
        """The test protocols never resize with solved hours (by design)."""
        for key in (
            ActivityKey.BIKE_FTP_TEST,
            ActivityKey.RUN_THRESHOLD_TEST,
            ActivityKey.SWIM_CSS_TEST,
        ):
            assert get_activity(key).tss_from_duration(60.0) is None


class TestScaledRecoveryBand:
    """``rep_rest_max`` scales the recovery with the work rep it follows.

    A fixed recovery quantizes the rep lattice in ``work + rest`` steps,
    which can leave a wide band of requested durations unreachable when
    one rep's fixed filler dominates the step size; scaling the rest with
    the work rep shrinks the lattice step and closes the gap.
    """

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_no_rest_max_keeps_the_recovery_fixed(self) -> None:
        """Activities without a declared band keep their historical arithmetic."""
        act = get_activity(ActivityKey.BIKE_THRESHOLD)
        assert act.rep_rest_max is None
        assert act._rest_slope() == 0.0
        assert act._rest_minutes(act.rep_work_min) == act.rep_rest_min
        assert act._rest_minutes(act.rep_work_max) == act.rep_rest_min

    @pytest.mark.parametrize("booked_min", [45, 60, 90, 102, 120])
    def test_sweet_spot_recovery_stays_short(self, booked_min: int) -> None:
        """Five easy minutes between sweet spot reps, however long the rep.

        Sweet spot sits below threshold, so it never earns more recovery
        than the threshold ride's four minutes plus one.
        """
        act = get_activity(ActivityKey.BIKE_SWEET_SPOT)
        workout = act.build(assignment_target_tss(act, booked_min / 60.0, None))
        repeat = next(s for s in workout.steps if s.type == "repeat")
        assert repeat.steps is not None
        rest = next(s for s in repeat.steps if s.type == "recovery")
        assert rest.duration is not None
        assert rest.duration.value_seconds == pytest.approx(300.0)

    def test_rest_max_interpolates_across_the_work_band(self) -> None:
        """Recovery runs from ``rep_rest_min`` to ``rep_rest_max`` over the band."""
        act = get_activity(ActivityKey.BIKE_LONG_SWEET_SPOT)
        assert act.rep_rest_max is not None
        assert act._rest_minutes(act.rep_work_min) == pytest.approx(act.rep_rest_min)
        assert act._rest_minutes(act.rep_work_max) == pytest.approx(act.rep_rest_max)
        mid_work = (act.rep_work_min + act.rep_work_max) / 2.0
        assert act._rest_minutes(mid_work) == pytest.approx(
            (act.rep_rest_min + act.rep_rest_max) / 2.0
        )
        # Work outside the band clamps rather than extrapolating.
        assert act._rest_minutes(0.0) == pytest.approx(act.rep_rest_min)
        assert act._rest_minutes(999.0) == pytest.approx(act.rep_rest_max)

    def test_degenerate_work_band_falls_back_to_fixed_rest(self) -> None:
        """A zero-width work band cannot interpolate — the recovery stays fixed."""
        import dataclasses

        act = dataclasses.replace(
            get_activity(ActivityKey.BIKE_LONG_SWEET_SPOT),
            rep_work_max=15.0,  # collapses onto rep_work_min
        )
        assert act._rest_slope() == 0.0
        assert act._rest_minutes(15.0) == act.rep_rest_min

    @pytest.mark.parametrize(
        "booked_min", [107, 120, 138, 156, 165, 175, 190, 204, 240, 265, 290, 315]
    )
    def test_sweet_spot_ride_card_matches_its_booking(self, booked_min: int) -> None:
        """Every duration the menus author renders at that duration.

        Spans the authored envelope: half-Ironman BASIC starts at 107 min,
        Ironman COMPETITIVE tops out at 315 min.
        """
        act = get_activity(ActivityKey.BIKE_LONG_SWEET_SPOT)
        target = assignment_target_tss(act, booked_min / 60.0, None)
        assert act.duration_minutes_exact(target) == pytest.approx(booked_min, abs=1.0)

    @pytest.mark.parametrize("booked_min", [107, 138, 204, 315])
    def test_sweet_spot_ride_steps_sum_to_its_card(self, booked_min: int) -> None:
        """R-ACT-017: the listed steps add up to the card's stated duration."""
        act = get_activity(ActivityKey.BIKE_LONG_SWEET_SPOT)
        target = assignment_target_tss(act, booked_min / 60.0, None)
        workout = act.build(target)
        assert _workout_minutes(workout.steps) == pytest.approx(
            act.duration_minutes_exact(target), abs=1e-6
        )


class TestIntervalPiecewiseDescriptionOverrides:
    """R-SC-011 / R-SC-022 / R-SC-023: work/rest description overrides flow into card steps."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def _work_and_rest(self, key: ActivityKey) -> tuple[str | None, str | None]:
        """Materialise the activity and return (work_desc, rest_desc)."""
        act = get_activity(key)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        workout = act.build(act.project_target_tss(mid_tss))
        repeat = next(s for s in workout.steps if s.type == "repeat")
        assert repeat.steps is not None
        work = next(s for s in repeat.steps if s.type == "interval")
        rest = next(s for s in repeat.steps if s.type == "recovery")
        return work.description, rest.description

    def test_bike_long_im_race_pace_uses_explicit_descriptions(self) -> None:
        """R-SC-022, R-SC-023: IM long race-pace block describes the actual purpose."""
        work, rest = self._work_and_rest(ActivityKey.BIKE_LONG_IM_RACE_PACE)
        assert work is not None
        assert "race-pace" in work.lower()
        # The Z2 rest segment must read as steady-state work, not as
        # "Active recovery — keep moving, very easy effort".
        assert rest is not None
        assert "active recovery" not in rest.lower()
        rest_lower = rest.lower()
        assert "race" in rest_lower or "steady" in rest_lower

    def test_bike_opener_uses_race_day_priming_wording(self) -> None:
        """R-SC-027: BIKE_OPENER reads as race-day priming, not a generic easy ride."""
        work, _rest = self._work_and_rest(ActivityKey.BIKE_OPENER)
        assert work is not None
        work_lower = work.lower()
        assert "race" in work_lower or "primer" in work_lower

    def test_run_strides_reads_as_strides_not_vo2max(self) -> None:
        """R-SC-015 / R-SC-012: strides describe themselves, not the VO2max band."""
        work, _rest = self._work_and_rest(ActivityKey.RUN_STRIDES)
        assert work is not None
        text = work.lower()
        assert "stride" in text or "sprint" in text

    def test_bike_cadence_steps_name_their_rpm(self) -> None:
        """Cadence Drills must actually prescribe cadence, like SWIM_DRILL does."""
        work, rest = self._work_and_rest(ActivityKey.BIKE_CADENCE)
        assert work is not None
        assert rest is not None
        assert "100-110 rpm" in work
        assert "cadence" in work.lower()
        assert "85-95 rpm" in rest
        # The generic easy-ride / active-recovery boilerplate must be gone.
        assert "conversational pace" not in work.lower()
        assert "active recovery" not in rest.lower()

    def test_bike_long_sweet_spot_recovery_cue_is_not_race_pace(self) -> None:
        """The sweet-spot ride's Z2 filler must not cite race-pace blocks.

        Its blocks are sweet spot (IF 0.91), never race pace; the generic
        long-recovery cue in ``_step_description`` says "race-pace blocks".
        """
        _work, rest = self._work_and_rest(ActivityKey.BIKE_LONG_SWEET_SPOT)
        assert rest is not None
        assert "race-pace" not in rest.lower()
        assert "sweet-spot" in rest.lower()

    @pytest.mark.parametrize(
        "key", [ActivityKey.BIKE_SWEET_SPOT, ActivityKey.BIKE_LONG_SWEET_SPOT]
    )
    def test_sweet_spot_reps_are_labelled_sweet_spot(self, key: ActivityKey) -> None:
        """A sweet spot rep is not labelled threshold, on the card or the watch."""
        work, _rest = self._work_and_rest(key)
        assert work is not None
        assert work.startswith("Sweet spot")
        assert "threshold effort" not in work.lower()


class TestProgressiveBlockBlockPrefix:
    """R-SC-012: progression-block steps prefix with Block N/M so identical bands diverge."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_progression_steps_have_block_prefix(self) -> None:
        act = get_activity(ActivityKey.RUN_LONG_PROGRESSION)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        workout = act.build(act.project_target_tss(mid_tss))
        interval_descs = [s.description for s in workout.steps if s.type == "interval"]
        assert len(interval_descs) >= 2
        for i, desc in enumerate(interval_descs):
            assert desc is not None
            assert desc.startswith(f"Block {i + 1}/{len(interval_descs)} — ")


class TestSwimPacedWarmupCooldownDescriptions:
    """R-SC-020: swim WU/CD have non-empty descriptions even when paced."""

    def setup_method(self) -> None:
        ensure_registry_loaded()

    def test_swim_endurance_warmup_and_cooldown_carry_descriptions(self) -> None:
        """SWIM_ENDURANCE uses paced WU/CD targets but must still cue the athlete."""
        act = get_activity(ActivityKey.SWIM_ENDURANCE)
        mid_tss = (act.tss_min + act.tss_max) / 2.0
        workout = act.build(act.project_target_tss(mid_tss))
        wu = next(s for s in workout.steps if s.type == "warmup")
        cd = next(s for s in workout.steps if s.type == "cooldown")
        assert wu.description is not None
        assert wu.description.strip()
        assert cd.description is not None
        assert cd.description.strip()
        assert "warm-up" in wu.description.lower()
        assert "cool-down" in cd.description.lower()


class TestTrainingZonesHrLookup:
    """The HR fallback chain the step builder reads zones through."""

    @staticmethod
    def _zones(*sports: str):
        from endury.domain.models.zones import (
            TrainingZones,
            ZoneBand,
            ZoneMetric,
            ZoneModel,
            ZoneSet,
        )

        return TrainingZones(
            zone_sets=[
                ZoneSet(
                    sport=sport,
                    metric=ZoneMetric.HEART_RATE,
                    model=ZoneModel.LTHR_5ZONE,
                    threshold_value=165.0,
                    threshold_label="LTHR",
                    bands=[ZoneBand(zone_number=1, name="Recovery", low=0, high=120)],
                )
                for sport in sports
            ]
        )

    def test_exact_sport_match_wins(self) -> None:
        zones = self._zones("cycling", "running")
        assert zones.get_hr_zones("cycling").sport == "cycling"

    def test_falls_back_to_running_for_a_sport_without_its_own_set(self) -> None:
        zones = self._zones("running")
        assert zones.get_hr_zones("swimming").sport == "running"

    def test_falls_back_to_all_when_running_is_absent(self) -> None:
        zones = self._zones("all")
        assert zones.get_hr_zones("swimming").sport == "all"

    def test_returns_none_when_nothing_matches(self) -> None:
        assert self._zones("cycling").get_hr_zones("swimming") is None
        assert self._zones().get_hr_zones("running") is None


class TestPoolLengthGrid:
    """Every swim step distance is a whole number of 50 m lengths."""

    SWIM_KEYS = TestSwimSessionInvariants.SWIM_KEYS

    def setup_method(self) -> None:
        ensure_registry_loaded()

    @staticmethod
    def _step_distances(workout) -> list[float]:
        distances: list[float] = []
        for step in workout.steps:
            children = step.steps or [] if step.type == "repeat" else []
            distances.extend(
                sub.duration.value_meters
                for sub in children
                if sub.duration and sub.duration.value_meters
            )
            if step.type != "repeat" and step.duration and step.duration.value_meters:
                distances.append(step.duration.value_meters)
        return distances

    @pytest.mark.parametrize("key", SWIM_KEYS)
    def test_every_step_is_a_50m_multiple(self, key: ActivityKey) -> None:
        """A card swims in whole 50 m lengths in a 25 m or a 50 m pool."""
        act = get_activity(key)
        for frac in (0.0, 0.2, 0.35, 0.5, 0.7, 0.85, 1.0):
            tss = act.tss_min + frac * (act.tss_max - act.tss_min)
            workout = act.build(tss)
            for meters in self._step_distances(workout):
                assert meters % 50 == 0, (
                    f"{key.value} @ TSS {tss:.1f}: step of {meters} m is not "
                    f"a whole number of 50 m lengths"
                )


class TestSwimCssDurationInversion:
    """A sized swim renders the lattice point nearest its duration.

    Every duration-driven CSS-lattice swim (reps x rep metres behind a fixed
    warm-up and a cool-down that rounds the card to 100 m) must never render
    a card that skips past a reachable lattice point lying between it and
    the requested duration: the athlete gets the card the plan sized.
    """

    CSS_LATTICE_KEYS = (
        ActivityKey.SWIM_CSS,
        ActivityKey.SWIM_CSS_SHORT,
        ActivityKey.SWIM_THRESHOLD,
        ActivityKey.SWIM_VO2MAX,
        ActivityKey.SWIM_VO2MAX_SHORT,
        ActivityKey.SWIM_SPRINT,
    )

    def setup_method(self) -> None:
        ensure_registry_loaded()

    @staticmethod
    def _lattice_minutes(act, resolver) -> list[float]:  # type: ignore[no-untyped-def]
        """Card durations of every reachable lattice point inside the window."""
        fixed = act._fixed_tss(resolver)
        coeff = act._work_tss_per_meter(resolver)
        minutes = set()
        for reps in range(act.rep_count_min, act.rep_count_max + 1):
            for meters in range(
                act.rep_meters_min, act.rep_meters_max + 1, SWIM_STEP_METERS
            ):
                tss = act.achieved_tss(fixed + reps * meters * coeff, resolver)
                tss = act.achieved_tss(tss, resolver)
                if act.tss_min <= tss <= act.tss_max:
                    minutes.add(round(act.duration_minutes_exact(tss, resolver), 6))
        return sorted(minutes)

    @pytest.mark.parametrize("key", CSS_LATTICE_KEYS)
    def test_no_lattice_point_is_skipped(self, key: ActivityKey) -> None:
        from endury.training.load import LoadMetricContext

        resolver = LoadMetricContext(swim_css_sec_per_100m=120.0)
        act = get_activity(key)
        points = self._lattice_minutes(act, resolver)
        for requested in range(int(points[0]) + 1, int(points[-1]) + 1):
            tss = assignment_target_tss(act, requested / 60.0, resolver)
            realized = act.duration_minutes_exact(tss, resolver)
            lo, hi = sorted((realized, float(requested)))
            skipped = [p for p in points if lo + 1e-6 < p < hi - 1e-6]
            assert not skipped, (
                f"{key.value}: a {requested}-min swim renders a {realized:.1f}-min "
                f"card, skipping the {skipped[0]:.1f}-min lattice point"
            )


#: The long sessions whose easy main steps carry an explicit HR band.
LONG_EASY_HR_BAND_KEYS = (
    ActivityKey.RUN_LONG,
    ActivityKey.RUN_LONG_PROGRESSION,
    ActivityKey.RUN_LONG_WITH_MP,
    ActivityKey.RUN_TRAIL_LONG,
    ActivityKey.RUN_TRAIL_B2B,
    ActivityKey.RUN_BRICK_LONG,
    ActivityKey.BIKE_LONG,
)

_RUN_LTHR = 165
_BIKE_LTHR = 155


class TestLongEasyHrBand:
    """A long session's easy main steps target 75-85 % LTHR, not all of Z1.

    HR Zone 1 spans 0-85 % LTHR, so without the band an 80-minute long run
    pushed its warm-up, main set and cool-down as one and the same band.
    """

    @staticmethod
    def _resolver() -> LoadMetricContext:
        return LoadMetricContext(
            running_lthr_bpm=_RUN_LTHR,
            cycling_lthr_bpm=_BIKE_LTHR,
            training_zones=compute_all_zones(
                lthr_bpm=_RUN_LTHR, cycling_lthr_bpm=_BIKE_LTHR
            ),
        )

    @staticmethod
    def _hr(step):
        targets = [step.target, step.secondary_target, *(step.additional_targets or [])]
        return next(t for t in targets if t is not None and t.type == "heart_rate_zone")

    @staticmethod
    def _build(key: ActivityKey, resolver=None):
        activity = get_activity(key)
        return activity.build((activity.tss_min + activity.tss_max) / 2.0, resolver)

    @pytest.mark.parametrize("key", LONG_EASY_HR_BAND_KEYS)
    def test_easy_main_steps_carry_the_band(self, key: ActivityKey) -> None:
        lthr = _BIKE_LTHR if key == ActivityKey.BIKE_LONG else _RUN_LTHR
        mains = [
            self._hr(s)
            for s in self._build(key, self._resolver()).steps
            if s.type == "interval"
        ]
        easy = [t for t in mains if t.zone == 1]
        assert easy, f"{key.value} has no easy main step"
        for target in easy:
            assert (target.low_bpm, target.high_bpm) == (
                round(0.75 * lthr),
                round(0.85 * lthr),
            )

    @pytest.mark.parametrize("key", LONG_EASY_HR_BAND_KEYS)
    def test_warmup_and_cooldown_keep_the_open_zone_1(self, key: ActivityKey) -> None:
        scaffold = [
            self._hr(s)
            for s in self._build(key, self._resolver()).steps
            if s.type in {"warmup", "cooldown"}
        ]
        assert scaffold
        assert all(t.zone == 1 and t.low_bpm is None for t in scaffold)

    def test_a_faster_progression_block_keeps_its_own_zone(self) -> None:
        mains = [
            self._hr(s)
            for s in self._build(
                ActivityKey.RUN_LONG_PROGRESSION, self._resolver()
            ).steps
            if s.type == "interval"
        ]
        faster = [t for t in mains if t.zone > 1]
        assert faster
        assert all(t.low_bpm != round(0.75 * _RUN_LTHR) for t in faster)

    def test_without_an_lthr_the_step_stays_a_bare_zone(self) -> None:
        mains = [
            self._hr(s)
            for s in self._build(ActivityKey.RUN_LONG).steps
            if s.type == "interval"
        ]
        assert [(t.zone, t.low_bpm, t.high_bpm) for t in mains] == [(1, None, None)]

    def test_only_the_long_sessions_carry_a_band(self) -> None:
        ensure_registry_loaded()
        banded = {
            key
            for key, activity in ACTIVITY_REGISTRY.items()
            if getattr(activity, "easy_hr_band", None) is not None
        }
        assert banded == set(LONG_EASY_HR_BAND_KEYS)


class TestPaceLeadWorkSteps:
    """A progression's rising blocks and a brick's race-pace step lead with pace.

    Their work is defined by pace, so the card and the watch carry the pace;
    the rest of each session stays heart-rate led.
    """

    @staticmethod
    def _resolver(threshold_pace: float | None = 4.5) -> LoadMetricContext:
        return LoadMetricContext(
            running_lthr_bpm=_RUN_LTHR,
            running_threshold_pace_min_per_km=threshold_pace,
            training_zones=compute_all_zones(
                lthr_bpm=_RUN_LTHR, threshold_pace_min_per_km=threshold_pace
            ),
        )

    @staticmethod
    def _build(key: ActivityKey, resolver: LoadMetricContext):
        activity = get_activity(key)
        return activity.build((activity.tss_min + activity.tss_max) / 2.0, resolver)

    def test_a_progressions_rising_blocks_lead_with_a_rising_pace(self) -> None:
        workout = self._build(ActivityKey.RUN_LONG_PROGRESSION, self._resolver())
        easy, *rising = [s for s in workout.steps if s.type == "interval"]
        assert easy.target is not None
        assert easy.target.type == "heart_rate_zone"
        assert len(rising) == 2
        paces = []
        for step in rising:
            assert step.target is not None
            assert step.target.type == "pace"
            assert step.secondary_target is not None
            assert step.secondary_target.type == "heart_rate_zone"
            assert step.target.low_min_per_km is not None
            assert step.target.high_min_per_km is not None
            paces.append((step.target.low_min_per_km + step.target.high_min_per_km) / 2)
        assert paces[0] > paces[1]

    @pytest.mark.parametrize(
        "key", [ActivityKey.RUN_BRICK_HIM_TEMPO, ActivityKey.RUN_BRICK_IM_TEMPO]
    )
    def test_a_bricks_race_pace_step_leads_with_pace(self, key: ActivityKey) -> None:
        workout = self._build(key, self._resolver())
        work = [s for s in workout.steps if s.type == "interval"]
        assert [s.target.type for s in work if s.target] == ["pace"]
        rest = [s for s in workout.steps if s.type != "interval"]
        assert rest
        assert all(s.target and s.target.type == "heart_rate_zone" for s in rest)

    @pytest.mark.parametrize(
        "key",
        [
            ActivityKey.RUN_LONG_PROGRESSION,
            ActivityKey.RUN_BRICK_HIM_TEMPO,
            ActivityKey.RUN_BRICK_IM_TEMPO,
        ],
    )
    def test_without_a_threshold_pace_every_step_stays_heart_rate_led(
        self, key: ActivityKey
    ) -> None:
        workout = self._build(key, self._resolver(threshold_pace=None))
        assert all(
            s.target and s.target.type == "heart_rate_zone" for s in workout.steps
        )


# ---------------------------------------------------------------------------
# The athlete's own zones number the card
# ---------------------------------------------------------------------------


def _athlete_zones(
    hr_bounds: list[float] | None = None, swim_bounds: list[float] | None = None
) -> TrainingZones:
    """Zones as intervals.icu keeps them, differing from Endury's bands.

    Run HR zones end at 141/150/158/167 bpm (Endury: 143/151/160/168 at
    LTHR 168), run pace zone 1 ends at 82 % of threshold speed (Endury:
    77.5 %), bike power zone 2 reaches 80 % of FTP (Endury: 75 %) and swim
    zone 2 ends at 87.7 % of CSS (Endury: 90 %). No cycling HR set.
    """
    return TrainingZones(
        zone_sets=[
            athlete_zone_set(
                sport="running",
                metric=ZoneMetric.HEART_RATE,
                threshold_value=168.0,
                threshold_label="LTHR",
                upper_bounds=hr_bounds or [141, 150, 158, 167, 185],
                names=None,
                scale=1.0,
            ),
            athlete_zone_set(
                sport="running",
                metric=ZoneMetric.PACE,
                threshold_value=4.0,
                threshold_label="Threshold Pace",
                upper_bounds=[82, 90, 95, 100, 999],
                names=None,
                scale=0.01,
                invert=True,
            ),
            athlete_zone_set(
                sport="cycling",
                metric=ZoneMetric.POWER,
                threshold_value=310.0,
                threshold_label="FTP",
                upper_bounds=[60, 80, 95, 105, 120, 150, 999],
                names=None,
                scale=3.1,
            ),
            athlete_zone_set(
                sport="swimming",
                metric=ZoneMetric.SWIM_PACE,
                threshold_value=107.0,
                threshold_label="CSS",
                upper_bounds=swim_bounds or [77.5, 87.7, 94.3, 100, 103.4, 111.5, 999],
                names=None,
                scale=0.01,
                invert=True,
            ),
        ]
    )


def _resolver_with(athlete: TrainingZones | None) -> LoadMetricContext:
    return LoadMetricContext(
        running_lthr_bpm=168,
        running_threshold_pace_min_per_km=4.0,
        cycling_ftp_watts=310.0,
        cycling_lthr_bpm=160,
        swim_css_sec_per_100m=107.0,
        training_zones=compute_all_zones(
            lthr_bpm=168,
            cycling_ftp_watts=310,
            threshold_pace_min_per_km=4.0,
            css_sec_per_100m=107.0,
            cycling_lthr_bpm=160,
        ),
        athlete_zones=athlete,
    )


class TestAthleteZoneLabels:
    """A step's zone number is the athlete's own; its prescription stays Endury's."""

    def test_swim_steps_take_the_athletes_zones_and_keep_their_paces(self) -> None:
        activity = get_activity(ActivityKey.SWIM_ENDURANCE)
        # A scheme whose zone 2 ends at 84 % of CSS (2:07/100m) holds the
        # main set at 88 % (1:58-2:05) whole in its zone 3.
        athlete = _athlete_zones(swim_bounds=[77.5, 84, 94.3, 100, 103.4, 111.5, 999])
        numbered = build_flat_steps(
            activity.build(50.0, resolver=_resolver_with(athlete)).steps,
            swim_css_sec_per_100m=107.0,
        )
        endury = build_flat_steps(
            activity.build(50.0, resolver=_resolver_with(None)).steps,
            swim_css_sec_per_100m=107.0,
        )

        assert numbered[1].primary_label.startswith("Swim Z3 (")
        assert endury[1].primary_label.startswith("Swim Z2 (")
        # The cool-down at 75 % is zone 1 in both schemes.
        assert numbered[-1].primary_label.startswith("Swim Z1 (")
        assert endury[-1].primary_label.startswith("Swim Z1 (")
        # The pace bands, distances and durations are the same swim.
        assert [
            s.primary_label.rstrip(")")[-14:] for s in numbered if s.distance_m
        ] == [s.primary_label.rstrip(")")[-14:] for s in endury if s.distance_m]
        assert [(s.distance_m, s.duration_s) for s in numbered] == [
            (s.distance_m, s.duration_s) for s in endury
        ]

    def test_swim_band_crossing_the_athletes_zones_carries_no_number(self) -> None:
        # This athlete's zone 2 runs 2:02-2:18/100m: the warm-up band
        # (2:13-2:21) crosses its top and the main set (1:58-2:05) its
        # bottom, so neither carries a number; the cool-down sits inside
        # zone 1. The bands stay the prescription.
        activity = get_activity(ActivityKey.SWIM_ENDURANCE)
        steps = build_flat_steps(
            activity.build(50.0, resolver=_resolver_with(_athlete_zones())).steps,
            swim_css_sec_per_100m=107.0,
        )
        assert [s.primary_label for s in steps[:2]] == [
            "Swim 2:13-2:21/100m",
            "Swim 1:58-2:05/100m",
        ]
        assert steps[1].zone_numbers is None
        assert steps[-1].primary_label == "Swim Z1 (2:18-2:27/100m)"

    def test_hr_band_crossing_the_athletes_zones_carries_no_number(self) -> None:
        # Endury's open zone 1 ends at 143 bpm; this athlete's zone 1 at 141.
        primary, _ = _zone_target(
            0.75, 3, SportType.RUNNING, _resolver_with(_athlete_zones())
        )
        assert (primary.zone, primary.low_bpm, primary.high_bpm) == (None, None, 143)

    def test_hr_band_inside_one_of_the_athletes_zones_takes_its_number(self) -> None:
        # IF 0.88 is Endury's HR Z3 (151-160 bpm); zone 2 for an athlete
        # whose zone 2 runs 150-160, zone 5 (168-185) stays zone 5 at IF 1.02.
        wide = _athlete_zones(hr_bounds=[150, 160, 170, 180, 190])
        primary, _ = _zone_target(0.88, 5, SportType.RUNNING, _resolver_with(wide))
        assert (primary.zone, primary.low_bpm, primary.high_bpm) == (2, 151, 160)
        primary, _ = _zone_target(
            1.02, 8, SportType.RUNNING, _resolver_with(_athlete_zones())
        )
        assert (primary.zone, primary.low_bpm, primary.high_bpm) == (5, 168, 185)

    def test_sport_without_its_own_set_keeps_endurys_number(self) -> None:
        primary, _ = _zone_target(
            0.65, 2, SportType.CYCLING, _resolver_with(_athlete_zones())
        )
        assert (primary.zone, primary.high_bpm) == (1, 136)

    def test_without_bpm_bounds_the_hr_zone_stays_endurys(self) -> None:
        from types import SimpleNamespace

        resolver = SimpleNamespace(
            training_zones=None,
            athlete_zones=_athlete_zones(),
            running_threshold_pace_min_per_km=None,
            running_ftp_watts=None,
            cycling_ftp_watts=None,
        )
        primary, _ = _zone_target(0.75, 3, SportType.RUNNING, resolver)
        assert (primary.zone, primary.high_bpm) == (1, None)

    def test_run_pace_tag_follows_the_athletes_pace_zones(self) -> None:
        # 79 % of threshold speed: Endury's pace zone 2 (77.5-87.7 %), and
        # its band (4:55-5:13/km) inside this athlete's zone 1 (under 82 %).
        _, _, numbered = _build_step_targets(
            0.79,
            SportType.RUNNING,
            4,
            TargetPreference.PACE_FIRST,
            _resolver_with(_athlete_zones()),
        )
        _, _, endury = _build_step_targets(
            0.79,
            SportType.RUNNING,
            4,
            TargetPreference.PACE_FIRST,
            _resolver_with(None),
        )
        assert [t.zone for t in numbered if t.type == "pace_zone"] == [1]
        assert [t.zone for t in endury if t.type == "pace_zone"] == [2]

    def test_run_pace_band_crossing_the_athletes_zones_carries_no_tag(self) -> None:
        # 90 % of threshold speed is this athlete's zone 2/3 boundary
        # (4:27/km); the band around it (4:19-4:35) crosses it, so the
        # pace stands alone. Without their zones Endury's zone 3 tags it.
        _, _, numbered = _build_step_targets(
            0.90,
            SportType.RUNNING,
            6,
            TargetPreference.PACE_FIRST,
            _resolver_with(_athlete_zones()),
        )
        _, _, endury = _build_step_targets(
            0.90,
            SportType.RUNNING,
            6,
            TargetPreference.PACE_FIRST,
            _resolver_with(None),
        )
        assert [t.zone for t in numbered if t.type == "pace_zone"] == []
        assert [t.zone for t in endury if t.type == "pace_zone"] == [3]

    def test_bike_power_tag_follows_the_athletes_power_zones(self) -> None:
        # 77 % of FTP: Endury's power zone 3 (76-90 %), with its band
        # (232-246 W) inside this athlete's zone 2 (186-248 W).
        _, _, numbered = _build_step_targets(
            0.77,
            SportType.CYCLING,
            5,
            TargetPreference.POWER_FIRST,
            _resolver_with(_athlete_zones()),
        )
        assert [t.zone for t in numbered if t.type == "power_zone"] == [2]

    def test_bike_power_band_crossing_the_athletes_zones_carries_no_tag(self) -> None:
        # 78 % of FTP: the band (235-249 W) crosses this athlete's zone 2
        # top at 248 W.
        _, _, numbered = _build_step_targets(
            0.78,
            SportType.CYCLING,
            5,
            TargetPreference.POWER_FIRST,
            _resolver_with(_athlete_zones()),
        )
        assert [t.zone for t in numbered if t.type == "power_zone"] == []
