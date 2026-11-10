"""Golden tests for the intervals.icu workout-DSL serializer."""

from __future__ import annotations

import pytest

from endury.intervals.workout_dsl import SportThresholds, workout_to_dsl
from endury.planner.foundations.types import SportType
from endury.training.workout import (
    DurationDef,
    StructuredWorkout,
    TargetDef,
    WorkoutStep,
)

RUN_THRESHOLDS = SportThresholds(
    lthr_bpm=170,
    threshold_pace_sec_per_km=270.0,  # 4:30/km
)
BIKE_THRESHOLDS = SportThresholds(ftp_watts=250, lthr_bpm=165)
SWIM_THRESHOLDS = SportThresholds(css_sec_per_100m=100.0)


def _time(seconds: float) -> DurationDef:
    return DurationDef(type="time", value_seconds=seconds)


def _meters(meters: float) -> DurationDef:
    return DurationDef(type="distance", value_meters=meters)


def _hr_zone(zone: int) -> TargetDef:
    return TargetDef(type="heart_rate_zone", zone=zone)


# Golden workouts


def test_run_threshold_intervals_emit_percent_pace_and_repeat_block():
    """Run intervals: labelled 4x block, blank-line delimited, % of threshold."""
    workout = StructuredWorkout(
        workout_name="Threshold intervals",
        steps=[
            WorkoutStep(
                type="warmup",
                duration=_time(900),
                target=_hr_zone(1),
                description="Warmup",
            ),
            WorkoutStep(
                type="repeat",
                iterations=4,
                description="Main set",
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_time(300),
                        target=TargetDef(
                            type="pace", low_min_per_km=4.3, high_min_per_km=4.5
                        ),
                        secondary_target=_hr_zone(4),
                        description="Threshold",
                    ),
                    WorkoutStep(
                        type="recovery",
                        duration=_time(120),
                        target=_hr_zone(1),
                        description="Jog recovery",
                    ),
                ],
            ),
            WorkoutStep(
                type="cooldown",
                duration=_time(600),
                target=_hr_zone(1),
                description="Cooldown",
            ),
        ],
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Warmup 15m 65-85% LTHR\n"
        "\n"
        "Main set 4x\n"
        "- Threshold 5m 100-105% Pace\n"
        "- Jog recovery 2m 65-85% LTHR\n"
        "\n"
        "- Cooldown 10m 65-85% LTHR"
    )


def test_run_easy_long_run_single_step():
    """A long easy run is one step; 5400 s renders as 1h30m, not 90m."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(5400),
                target=TargetDef(type="pace", low_min_per_km=5.4, high_min_per_km=5.8),
                secondary_target=_hr_zone(2),
                description="Easy aerobic running, conversational",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Easy aerobic running, conversational 1h30m 78-83% Pace"
    )


_PACE_BAND = TargetDef(type="pace", low_min_per_km=5.4, high_min_per_km=5.8)


@pytest.mark.parametrize(
    ("target", "secondary", "extras", "expected"),
    [
        # HR leads, pace is an extra: the lead metric reaches the watch.
        (_hr_zone(1), None, [_PACE_BAND], "65-85% LTHR"),
        (
            TargetDef(type="heart_rate", low_bpm=150, high_bpm=160),
            None,
            [_PACE_BAND],
            "88-94% LTHR",
        ),
        # Pace leads, HR is secondary: pace still wins.
        (_PACE_BAND, _hr_zone(2), None, "78-83% Pace"),
        # An HR lead with nothing renderable falls back to the sport's order.
        (TargetDef(type="heart_rate_zone"), None, [_PACE_BAND], "78-83% Pace"),
    ],
)
def test_the_lead_metric_of_a_run_step_decides_the_target_token(
    target: TargetDef,
    secondary: TargetDef | None,
    extras: list[TargetDef] | None,
    expected: str,
):
    """An HR-led easy run is pushed as % LTHR, never as the pace extra beside it."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(1800),
                target=target,
                secondary_target=secondary,
                additional_targets=extras,
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        f"- 30m {expected}"
    )


def test_a_zone_one_hr_step_opens_at_an_easy_jog():
    """intervals.icu prices a planned HR step at its band's midpoint.

    From a 55 % floor that midpoint is a walk, so an ordinary easy run read
    as 150-170 % of plan and intervals.icu did not auto-pair it.
    """
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="recovery",
                duration=_time(1020),
                target=_hr_zone(1),
                description="Active recovery",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Active recovery 17m 65-85% LTHR"
    )


def test_a_long_runs_main_set_gets_its_own_hr_band():
    """Warm-up, main set and cool-down must not share Zone 1's 65-85 % band."""
    from endury.domain.analysis.zones import compute_all_zones
    from endury.planner.activity import ensure_registry_loaded, get_activity
    from endury.planner.foundations.types import ActivityKey
    from endury.training.load import LoadMetricContext

    ensure_registry_loaded()
    long_run = get_activity(ActivityKey.RUN_LONG)
    resolver = LoadMetricContext(
        running_lthr_bpm=165, training_zones=compute_all_zones(lthr_bpm=165)
    )

    dsl = workout_to_dsl(
        long_run.build(80.0, resolver),
        SportThresholds(lthr_bpm=165),
        SportType.RUNNING,
    )

    assert [line.rsplit(" ", 2)[-2:] for line in dsl.splitlines()] == [
        ["65-85%", "LTHR"],
        ["75-85%", "LTHR"],
        ["65-85%", "LTHR"],
    ]


def test_a_progressions_rising_blocks_push_two_different_pace_bands():
    """The easy block goes out as % LTHR; each tempo block keeps its own % Pace."""
    from endury.domain.analysis.zones import compute_all_zones
    from endury.planner.activity import ensure_registry_loaded, get_activity
    from endury.planner.foundations.types import ActivityKey
    from endury.training.load import LoadMetricContext

    ensure_registry_loaded()
    progression = get_activity(ActivityKey.RUN_LONG_PROGRESSION)
    resolver = LoadMetricContext(
        running_lthr_bpm=170,
        running_threshold_pace_min_per_km=4.5,
        training_zones=compute_all_zones(lthr_bpm=170, threshold_pace_min_per_km=4.5),
    )

    dsl = workout_to_dsl(
        progression.build(progression.tss_max, resolver),
        RUN_THRESHOLDS,
        SportType.RUNNING,
    )

    bands = [line.rsplit(" ", 2)[-2:] for line in dsl.splitlines()]
    assert [unit for _, unit in bands] == ["LTHR", "LTHR", "Pace", "Pace", "LTHR"]
    assert bands[2][0] != bands[3][0]


def test_bike_sweet_spot_emits_percent_ftp_zone_band_and_cadence():
    """Bike: watts → %FTP, power_zone → floored band, cadence secondary token."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="warmup",
                duration=_time(1200),
                target=TargetDef(type="power", low_watts=100, high_watts=150),
                description="Warmup",
            ),
            WorkoutStep(
                type="repeat",
                iterations=3,
                description="Sweet spot",
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_time(720),
                        target=TargetDef(type="power", low_watts=225, high_watts=240),
                        additional_targets=[
                            TargetDef(type="cadence", low_rpm=85, high_rpm=95),
                            TargetDef(type="rpe", rpe=7),
                        ],
                        description="Sweet spot",
                    ),
                    WorkoutStep(
                        type="recovery",
                        duration=_time(300),
                        target=TargetDef(type="power_zone", zone=1),
                        description="Easy spin",
                    ),
                ],
            ),
            WorkoutStep(
                type="cooldown",
                duration=_time(600),
                target=_hr_zone(2),
                description="Cooldown",
            ),
        ]
    )

    assert workout_to_dsl(workout, BIKE_THRESHOLDS, SportType.CYCLING) == (
        "- Warmup 20m 40-60%\n"
        "\n"
        "Sweet spot 3x\n"
        "- Sweet spot 12m 90-96% 90rpm\n"
        "- Easy spin 5m 40-55%\n"
        "\n"
        "- Cooldown 10m 85-90% LTHR"
    )


def test_swim_css_set_uses_mtr_distances_and_percent_pace():
    """Swim: distances are ``mtr`` (never ``m``), pace is % of CSS."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="warmup",
                duration=_meters(400),
                target=TargetDef(type="swim_pace_zone", zone=1),
                description="Warmup",
            ),
            WorkoutStep(
                type="repeat",
                iterations=6,
                description="CSS set",
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_meters(100),
                        target=TargetDef(
                            type="swim_pace_zone",
                            zone=4,
                            low_sec_per_100m=95.0,
                            high_sec_per_100m=105.0,
                        ),
                        description="CSS pace",
                    ),
                    WorkoutStep(
                        type="rest",
                        duration=_time(20),
                        target=TargetDef(type="open"),
                        description="Rest",
                    ),
                ],
            ),
            WorkoutStep(
                type="cooldown",
                duration=_meters(200),
                target=TargetDef(type="swim_pace_zone", zone=1),
                description="Easy cooldown",
            ),
        ]
    )

    assert workout_to_dsl(workout, SWIM_THRESHOLDS, SportType.SWIMMING) == (
        "- Warmup 400mtr 71-85% Pace\n"
        "\n"
        "CSS set 6x\n"
        "- CSS pace 100mtr 95-105% Pace\n"
        "- Rest 20s\n"
        "\n"
        "- Easy cooldown 200mtr 71-85% Pace"
    )


def test_absolute_fallback_when_no_thresholds_known():
    """Without thresholds, concrete bands emit absolute units."""
    empty = SportThresholds()

    run = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(1800),
                target=TargetDef(type="pace", low_min_per_km=4.3, high_min_per_km=4.5),
                description="Tempo",
            )
        ]
    )
    assert workout_to_dsl(run, empty, SportType.RUNNING) == (
        "- Tempo 30m 4:18/km-4:30/km Pace"
    )

    bike = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(3750),
                target=TargetDef(type="power", low_watts=225, high_watts=240),
                secondary_target=TargetDef(
                    type="heart_rate", low_bpm=150, high_bpm=160
                ),
                description="Endurance",
            ),
            WorkoutStep(
                type="interval",
                duration=_time(600),
                target=TargetDef(type="heart_rate", low_bpm=150, high_bpm=160),
                description="Steady",
            ),
        ]
    )
    assert workout_to_dsl(bike, empty, SportType.CYCLING) == (
        "- Endurance 1h2m30s 225-240w\n- Steady 10m 150-160bpm"
    )

    swim = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_meters(1500),
                target=TargetDef(
                    type="swim_pace", low_sec_per_100m=95.0, high_sec_per_100m=105.0
                ),
                description="Steady swim",
            )
        ]
    )
    assert workout_to_dsl(swim, empty, SportType.SWIMMING) == (
        "- Steady swim 1500mtr 1:35/100m-1:45/100m Pace"
    )


def test_nested_repeats_are_flattened_into_one_block():
    """intervals.icu has no nested repeats: inner reps are unrolled."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="repeat",
                iterations=2,
                steps=[
                    WorkoutStep(
                        type="interval",
                        duration=_time(60),
                        target=_hr_zone(4),
                        description="Hard",
                    ),
                    WorkoutStep(
                        type="repeat",
                        iterations=2,
                        steps=[
                            WorkoutStep(
                                type="interval",
                                duration=_time(30),
                                target=_hr_zone(5),
                                description="Surge",
                            ),
                            WorkoutStep(
                                type="recovery",
                                duration=_time(30),
                                target=_hr_zone(1),
                                description="Float",
                            ),
                        ],
                    ),
                ],
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "2x\n"
        "- Hard 1m 95-100% LTHR\n"
        "- Surge 30s 100-110% LTHR\n"
        "- Float 30s 65-85% LTHR\n"
        "- Surge 30s 100-110% LTHR\n"
        "- Float 30s 65-85% LTHR"
    )


def test_consecutive_repeat_blocks_are_separated_by_one_blank_line():
    """Back-to-back blocks never collapse or double up their blank lines."""
    block = WorkoutStep(
        type="repeat",
        iterations=2,
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(3600),
                target=_hr_zone(3),
                description="Block",
            )
        ],
    )
    workout = StructuredWorkout(steps=[block, block])

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "2x\n- Block 1h 90-95% LTHR\n\n2x\n- Block 1h 90-95% LTHR"
    )


# Degenerate inputs


def test_unrepresentable_steps_are_dropped():
    """Steps without a usable duration, and empty repeats, are skipped."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(type="interval", description="No duration at all"),
            WorkoutStep(
                type="interval",
                duration=DurationDef(type="open"),
                description="Open-ended",
            ),
            WorkoutStep(
                type="interval",
                duration=_time(0.0),
                description="Zero length",
            ),
            WorkoutStep(
                type="interval",
                duration=DurationDef(type="distance", value_meters=0.0),
                description="Zero distance",
            ),
            WorkoutStep(type="repeat", iterations=3, steps=[]),
            WorkoutStep(
                type="repeat",
                iterations=3,
                steps=[WorkoutStep(type="interval", duration=_time(0.0))],
            ),
            WorkoutStep(
                type="interval",
                duration=_time(600),
                target=TargetDef(type="rpe", rpe=4),
                description="   Easy   effort   ",
            ),
            WorkoutStep(type="interval", duration=_time(300)),
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Easy effort RPE 4/10 10m\n- 5m"
    )


def test_labels_are_reduced_to_a_parser_safe_lead_clause():
    """Real planner prose is truncated; digit-bearing labels are dropped."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="warmup",
                duration=_meters(400),
                target=TargetDef(type="swim_pace_zone", zone=1),
                description=(
                    "Easy swim warm-up — choice of stroke, gradually loosen "
                    "up; build pace and rotation in the last 100 m"
                ),
            ),
            WorkoutStep(
                type="interval",
                duration=_meters(1000),
                target=TargetDef(type="swim_pace_zone", zone=3),
                description="400 m repeats",
            ),
        ]
    )

    assert workout_to_dsl(workout, SWIM_THRESHOLDS, SportType.SWIMMING) == (
        "- Easy swim warm-up 400mtr 71-85% Pace\n- 1000mtr 90-95% Pace"
    )


def test_an_rpe_only_step_keeps_its_intensity_in_the_label():
    """intervals.icu has no RPE syntax; the prescription rides the name."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(1800),
                target=TargetDef(type="rpe", rpe=9),
                description="30 min all-out effort — find sustainable threshold pace",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- find sustainable threshold pace RPE 9/10 30m"
    )


def test_an_rpe_alongside_a_machine_target_stays_out_of_the_label():
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(600),
                target=_hr_zone(2),
                secondary_target=TargetDef(type="rpe", rpe=5),
                description="Steady",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Steady 10m 85-90% LTHR"
    )


def test_an_effort_led_run_step_goes_to_the_watch_as_heart_rate_never_pace():
    """A hill rep leads on RPE and carries its flat pace only as an extra.

    On a 7 % grade that pace fails every rep; the heart-rate band is the one
    the athlete can hold, and it still prices the planned event.
    """
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(170),
                target=TargetDef(type="rpe", rpe=8),
                secondary_target=_hr_zone(4),
                additional_targets=[
                    TargetDef(type="pace", low_min_per_km=5.33, high_min_per_km=5.67),
                    TargetDef(type="pace_zone", zone=4),
                ],
                description="Uphill rep — hard but controlled",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Uphill rep 2m50s 95-100% LTHR"
    )


def test_an_effort_led_ride_keeps_its_power():
    """Power reads the same on any grade, so a ride led by effort keeps it."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(600),
                target=TargetDef(type="rpe", rpe=3),
                secondary_target=_hr_zone(1),
                additional_targets=[
                    TargetDef(type="power", low_watts=125, high_watts=138)
                ],
                description="Spin",
            )
        ]
    )

    assert workout_to_dsl(workout, BIKE_THRESHOLDS, SportType.CYCLING) == (
        "- Spin 10m 50-55%"
    )


def test_the_label_skips_a_digit_bearing_lead_clause():
    """A digit-bearing lead clause like ``Block 1/3`` is skipped, not the name."""
    workout = StructuredWorkout(
        steps=[
            WorkoutStep(
                type="interval",
                duration=_time(600),
                target=_hr_zone(2),
                description="Block 1/3 — Easy aerobic effort — conversational pace",
            )
        ]
    )

    assert workout_to_dsl(workout, RUN_THRESHOLDS, SportType.RUNNING) == (
        "- Easy aerobic effort 10m 85-90% LTHR"
    )


def test_empty_workout_renders_empty_string():
    """A workout with no representable steps serializes to ``""``."""
    assert (
        workout_to_dsl(StructuredWorkout(steps=[]), RUN_THRESHOLDS, SportType.RUNNING)
        == ""
    )


@pytest.mark.parametrize(
    ("sport", "thresholds", "target", "expected"),
    [
        # Out-of-range zone clamps to the top band.
        (
            SportType.CYCLING,
            BIKE_THRESHOLDS,
            TargetDef(type="heart_rate_zone", zone=9),
            "100-110% LTHR",
        ),
        # Equal bounds collapse to a single percentage.
        (
            SportType.CYCLING,
            BIKE_THRESHOLDS,
            TargetDef(type="power", low_watts=250, high_watts=250),
            "100%",
        ),
        # Absolute bpm becomes % LTHR when the threshold is known.
        (
            SportType.RUNNING,
            RUN_THRESHOLDS,
            TargetDef(type="heart_rate", low_bpm=150, high_bpm=160),
            "88-94% LTHR",
        ),
        # A bare pace zone resolves through the pace band spec.
        (
            SportType.RUNNING,
            RUN_THRESHOLDS,
            TargetDef(type="pace_zone", zone=2),
            "78-88% Pace",
        ),
        # Zone-less, value-less targets emit no target token.
        (SportType.CYCLING, BIKE_THRESHOLDS, TargetDef(type="power"), None),
        (SportType.CYCLING, BIKE_THRESHOLDS, TargetDef(type="heart_rate"), None),
        (SportType.RUNNING, RUN_THRESHOLDS, TargetDef(type="pace"), None),
        (SportType.SWIMMING, SWIM_THRESHOLDS, TargetDef(type="swim_pace"), None),
        # An unbounded cadence target contributes nothing.
        (
            SportType.CYCLING,
            BIKE_THRESHOLDS,
            TargetDef(type="cadence", low_rpm=90),
            None,
        ),
    ],
)
def test_target_edge_cases(
    sport: SportType,
    thresholds: SportThresholds,
    target: TargetDef,
    expected: str | None,
):
    """Zone clamping, collapsed ranges, and unusable targets."""
    workout = StructuredWorkout(
        steps=[WorkoutStep(type="interval", duration=_time(600), target=target)]
    )

    line = workout_to_dsl(workout, thresholds, sport)

    assert line == (f"- 10m {expected}" if expected else "- 10m")


def test_hr_ceiling_without_zone_number_emits_the_floored_percent_lthr():
    """An HR band that crosses the athlete's own zones carries no zone number.

    The DSL still sends the band as % LTHR, with the zone-1 floor that keeps
    an open ceiling coachable.
    """
    workout = StructuredWorkout(
        workout_name="Easy",
        steps=[
            WorkoutStep(
                type="interval",
                duration=DurationDef(type="time", value_seconds=600),
                target=TargetDef(type="heart_rate_zone", zone=None, high_bpm=143),
            )
        ],
    )

    dsl = workout_to_dsl(workout, SportThresholds(lthr_bpm=168), SportType.RUNNING)

    assert dsl == "- 10m 65-85% LTHR"
