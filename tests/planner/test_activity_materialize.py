"""Tests for the hours→workout materialization bridge (activity/materialize)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.config import DEFAULT_SWIM_CSS_SEC_PER_100M
from endury.planner.activity.materialize import (
    ACTIVITY_BRIEFS,
    ACTIVITY_DISPLAY_NAMES,
    FAMILY_BRIEFS,
    activity_brief,
    activity_display_name,
    assignment_target_tss,
    capability_caveat,
    family_intent,
    materialized_swim_meters,
    workout_total_meters,
)
from endury.planner.foundations.types import ActivityFamily, ActivityKey
from endury.planner.sessions.session_generator import _swim_minutes
from endury.training.load import LoadMetricContext
from endury.training.workout import DurationDef, StructuredWorkout, WorkoutStep

ensure_registry_loaded()


class TestAssignmentTargetTss:
    def test_duration_driven_activity_inverts_hours(self) -> None:
        act = get_activity(ActivityKey.RUN_EASY)
        tss = assignment_target_tss(act, 1.0, None)
        assert act.tss_min <= tss <= act.tss_max
        # More hours must never produce less TSS.
        assert assignment_target_tss(act, 2.0, None) >= tss

    def test_none_hours_falls_back_to_mid_range(self) -> None:
        act = get_activity(ActivityKey.RUN_EASY)
        expected = act.project_target_tss((act.tss_min + act.tss_max) / 2.0)
        assert assignment_target_tss(act, None, None) == expected
        assert assignment_target_tss(act, 0.0, None) == expected

    def test_fixed_protocol_ignores_hours(self) -> None:
        act = get_activity(ActivityKey.BIKE_FTP_TEST)
        assert assignment_target_tss(act, 0.5, None) == assignment_target_tss(
            act, 3.0, None
        )


class TestWorkoutTotalMeters:
    def test_sums_distance_steps_and_expands_repeats(self) -> None:
        steps = [
            WorkoutStep(
                type="warmup",
                duration=DurationDef(type="distance", value_meters=400.0),
            ),
            WorkoutStep(
                type="repeat",
                iterations=4,
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=DurationDef(type="distance", value_meters=100.0),
                    ),
                ],
            ),
            WorkoutStep(
                type="cooldown",
                duration=DurationDef(type="time", value_seconds=300.0),
            ),
        ]
        assert workout_total_meters(steps) == pytest.approx(800.0)

    def test_time_based_steps_sum_to_zero(self) -> None:
        steps = [
            WorkoutStep(
                type="interval",
                duration=DurationDef(type="time", value_seconds=600.0),
            ),
        ]
        assert workout_total_meters(steps) == 0.0


class TestMaterializedSwimMeters:
    def test_distance_based_swim_returns_meters(self) -> None:
        act = get_activity(ActivityKey.SWIM_ENDURANCE)
        meters = materialized_swim_meters(act, 1.0, None)
        assert meters is not None
        assert meters > 0

    def test_time_based_workout_returns_none(self) -> None:
        # RUN_EASY builds time-based steps; the bridge is sport-agnostic, so
        # it exercises the meters == 0 → None fallback deterministically.
        act = get_activity(ActivityKey.RUN_EASY)
        assert materialized_swim_meters(act, 1.0, None) is None

    @pytest.mark.parametrize(
        "key",
        [
            ActivityKey.SWIM_ENDURANCE,
            ActivityKey.SWIM_LONG,
            ActivityKey.SWIM_RECOVERY,
            ActivityKey.SWIM_PULL,
            ActivityKey.SWIM_KICK,
        ],
    )
    def test_continuous_swim_without_css_renders_the_menu_metres(
        self, key: ActivityKey
    ) -> None:
        # The generator budgets a metre-authored swim at the default CSS, so
        # a card built without an athlete CSS must swim at that same pace:
        # a 1,000 m menu slot renders 1,000 m, not a faster swimmer's 1,300 m.
        act = get_activity(key)
        hours = _swim_minutes(key, 1000, DEFAULT_SWIM_CSS_SEC_PER_100M) / 60.0
        at_default = LoadMetricContext(
            swim_css_sec_per_100m=DEFAULT_SWIM_CSS_SEC_PER_100M
        )
        assert materialized_swim_meters(act, hours, at_default) == 1000
        assert materialized_swim_meters(act, hours, None) == 1000


class TestActivityDisplayName:
    def test_every_registered_key_has_curated_name(self) -> None:
        for key in ActivityKey:
            assert key in ACTIVITY_DISPLAY_NAMES, f"missing display name: {key}"

    def test_curated_name(self) -> None:
        assert activity_display_name(ActivityKey.RUN_VO2MAX) == "VO₂max Intervals"

    def test_slug_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(ACTIVITY_DISPLAY_NAMES, ActivityKey.RUN_EASY)
        assert activity_display_name(ActivityKey.RUN_EASY) == "Run Easy"


class TestActivityBrief:
    def test_family_fallback_for_an_unauthored_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delitem(ACTIVITY_BRIEFS, ActivityKey.RUN_EASY)
        assert (
            activity_brief(ActivityKey.RUN_EASY, ActivityFamily.EASY)
            == FAMILY_BRIEFS[ActivityFamily.EASY]
        )

    def test_an_authored_key_wins_over_its_family(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(ACTIVITY_BRIEFS, ActivityKey.RUN_EASY, "Jog it.")
        assert activity_brief(ActivityKey.RUN_EASY, ActivityFamily.EASY) == "Jog it."

    def test_family_intent_is_the_fallback_accessor(self) -> None:
        assert family_intent(ActivityFamily.TEST) == FAMILY_BRIEFS[ActivityFamily.TEST]


class TestStructuredWorkoutJson:
    """The JSON round-trip on the training kernel's workout schema."""

    def test_round_trip_preserves_name_and_steps(self) -> None:
        workout = StructuredWorkout(
            workout_name="Easy Run",
            steps=[
                WorkoutStep(
                    type="warmup",
                    duration=DurationDef(type="time", value_seconds=600.0),
                )
            ],
        )

        restored = StructuredWorkout.from_json(workout.to_json())

        assert restored.workout_name == "Easy Run"
        assert len(restored.steps) == 1
        assert restored.steps[0].type == "warmup"
        assert restored.steps[0].duration is not None
        assert restored.steps[0].duration.value_seconds == 600.0


class TestDurationFirstLowerBound:
    """Duration-derived targets are not inflated to the declared tss_min."""

    def test_small_solved_duration_renders_as_solved(self) -> None:
        """A continuous long ride solved below its window renders honestly."""
        act = get_activity(ActivityKey.BIKE_LONG)
        tss = assignment_target_tss(act, 0.5, None)
        assert act.duration_minutes_exact(tss) == pytest.approx(30.0, abs=0.5)

    def test_declared_window_still_caps_above(self) -> None:
        """The upper window clamp is unchanged."""
        act = get_activity(ActivityKey.BIKE_LONG)
        assert assignment_target_tss(act, 50.0, None) == act.tss_max


class TestCapabilityCaveat:
    def test_none_for_a_key_session_in_an_ordinary_week(self):
        # A VO2 ride with no plan behind it warned about recovery weeks.
        activity = get_activity(ActivityKey.BIKE_VO2MAX_SHORT)
        assert (
            capability_caveat(activity, recovery_week=False, taper_week=False) is None
        )

    def test_none_for_a_recovery_safe_activity_in_a_recovery_week(self):
        activity = get_activity(ActivityKey.SWIM_ENDURANCE)
        assert capability_caveat(activity, recovery_week=True, taper_week=True) is None

    def test_names_the_recovery_week_in_coaching_language(self):
        activity = get_activity(ActivityKey.RUN_LONG)
        caveat = capability_caveat(activity, recovery_week=True, taper_week=False)
        assert caveat is not None
        assert "Long Run" in caveat
        assert "recovery week" in caveat
        assert "intent" not in caveat
        assert "'" not in caveat

    def test_a_test_is_not_flagged_in_a_recovery_week(self):
        # The planner defers tests into recovery weeks itself: fresh legs
        # give an honest number. Seen live: the caveat told an athlete
        # making up a missed test that an easy session fits better.
        activity = get_activity(ActivityKey("run_threshold_test"))
        assert capability_caveat(activity, recovery_week=True, taper_week=False) is None

    def test_a_taper_safe_session_is_not_flagged_in_a_taper_week(self):
        # run_long is taper-safe but not recovery-safe: no false taper claim.
        activity = get_activity(ActivityKey.RUN_LONG)
        assert capability_caveat(activity, recovery_week=False, taper_week=True) is None

    def test_names_the_taper_week_for_a_taper_unsafe_session(self):
        activity = get_activity(ActivityKey.SWIM_LONG)
        caveat = capability_caveat(activity, recovery_week=False, taper_week=True)
        assert caveat is not None
        assert "taper week" in caveat

    def test_none_when_the_activity_has_no_profile(self):
        # Unreachable through the registry (``__post_init__`` always fills a
        # profile in), so the optional-at-the-type-level branch needs a stub.
        stub = SimpleNamespace(capability_profile=None)
        assert capability_caveat(stub, recovery_week=True, taper_week=True) is None
