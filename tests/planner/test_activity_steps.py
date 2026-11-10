"""Tests for the flat-step decomposition and coaching notes builders.

Covers ``endury.planner.activity.steps``: ``FlatStep.display_color``,
zone-band mapping, target formatting, ``build_flat_steps`` /
``build_table_steps``, and ``coaching_notes``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    FlatStep,
    _zone_band_from_target,
    build_flat_steps,
    build_table_steps,
    coaching_notes,
    fmt_distance,
    fmt_pace,
    fmt_seconds,
    session_coaching_notes,
)
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    PlacedSession,
    SessionOrigin,
    SportType,
)
from endury.training.workout import DurationDef, TargetDef, WorkoutStep


def test_display_color_scaffold_step_uses_zone_band():
    step = FlatStep(
        step_type="warmup",
        duration_s=60.0,
        zone_band="wu_cd",
        primary_label="HR Z1",
        secondary_label="RPE 2",
        extra_labels=[],
        description="",
    )
    assert step.display_color == "#ADB5BD"


def test_display_color_scaffold_step_unknown_band_falls_back():
    step = FlatStep(
        step_type="rest",
        duration_s=60.0,
        zone_band="unknown_band",
        primary_label="—",
        secondary_label="—",
        extra_labels=[],
        description="",
    )
    assert step.display_color == "#ADB5BD"


def test_display_color_training_step_uses_zone_number():
    step = FlatStep(
        step_type="interval",
        duration_s=60.0,
        zone_band="z_high",
        primary_label="HR Z4",
        secondary_label="RPE 7",
        extra_labels=[],
        description="",
        zone_numbers={"hr": 4},
    )
    assert step.display_color == "#F4A261"


def test_display_color_training_step_unknown_zone_number_falls_back():
    step = FlatStep(
        step_type="interval",
        duration_s=60.0,
        zone_band="z_high",
        primary_label="HR Z9",
        secondary_label="RPE 7",
        extra_labels=[],
        description="",
        zone_numbers={"hr": 99},
    )
    assert step.display_color == "#ADB5BD"


def test_display_color_training_step_no_zone_numbers_uses_band():
    step = FlatStep(
        step_type="interval",
        duration_s=60.0,
        zone_band="z_mid",
        primary_label="RPE 6",
        secondary_label="—",
        extra_labels=[],
        description="",
        zone_numbers=None,
    )
    assert step.display_color == "#F4A261"


def test_fmt_seconds_sub_minute():
    assert fmt_seconds(45) == "45s"


def test_fmt_seconds_whole_minute_under_ten():
    assert fmt_seconds(300) == "5 min"


def test_fmt_seconds_minutes_and_seconds_under_ten():
    assert fmt_seconds(125) == "2m 05s"


def test_fmt_seconds_ten_or_more_minutes_rounds():
    assert fmt_seconds(36 * 60 + 56) == "37 min"


def test_fmt_seconds_hours_with_remainder():
    assert fmt_seconds(65 * 60) == "1h 05m"


def test_fmt_seconds_exact_hour_no_remainder():
    assert fmt_seconds(60 * 60) == "1h"


def test_fmt_seconds_negative_clamped_to_zero():
    assert fmt_seconds(-5) == "0s"


def test_fmt_pace_no_rollover():
    assert fmt_pace(4.5) == "4:30/km"


def test_fmt_pace_rollover_to_next_minute():
    assert fmt_pace(4.999) == "5:00/km"


def test_fmt_distance_formats_with_thousands_separator():
    assert fmt_distance(3800) == "3,800 m"


def test_build_flat_steps_flattens_repeat_blocks():
    steps = [
        WorkoutStep(
            type="warmup",
            duration=DurationDef(type="time", value_seconds=600),
            target=TargetDef(type="heart_rate_zone", zone=1, low_bpm=120, high_bpm=140),
            secondary_target=TargetDef(type="rpe", rpe=3),
            description="warm up",
        ),
        WorkoutStep(
            type="repeat",
            iterations=3,
            duration=DurationDef(type="time", value_seconds=450),
            steps=[
                WorkoutStep(
                    type="interval",
                    duration=DurationDef(type="time", value_seconds=300),
                    target=TargetDef(type="heart_rate_zone", zone=4),
                    secondary_target=TargetDef(type="rpe", rpe=8),
                    description="work",
                ),
                WorkoutStep(
                    type="recovery",
                    duration=DurationDef(type="time", value_seconds=150),
                    target=TargetDef(type="rpe", rpe=2),
                    description="rest",
                ),
            ],
        ),
    ]
    flat = build_flat_steps(steps)
    # 1 warmup + 3 x (interval + recovery)
    assert len(flat) == 7
    assert flat[0].step_type == "warmup"
    assert flat[0].zone_band == "wu_cd"
    assert all(s.iterations == 1 for s in flat)
    assert flat[1].step_type == "interval"
    # HR zone 4 tops at LT2: the planner reads it as the middle band.
    assert flat[1].zone_band == "z_mid"


def test_build_flat_steps_skips_zero_duration_steps():
    steps = [
        WorkoutStep(type="rest", description="Rest day"),
        WorkoutStep(
            type="interval",
            duration=DurationDef(type="time", value_seconds=60),
            target=TargetDef(type="rpe", rpe=5),
        ),
    ]
    flat = build_flat_steps(steps)
    assert len(flat) == 1
    assert flat[0].step_type == "interval"


def test_build_flat_steps_distance_step_uses_swim_css():
    steps = [
        WorkoutStep(
            type="interval",
            duration=DurationDef(type="distance", value_meters=100),
            target=TargetDef(type="swim_pace_zone", zone=3),
        ),
    ]
    flat = build_flat_steps(steps, swim_css_sec_per_100m=90.0)
    assert len(flat) == 1
    assert flat[0].duration_s == 90.0
    assert flat[0].distance_m == 100
    assert flat[0].zone_band == "z_mid"


def test_build_flat_steps_default_swim_css():
    steps = [
        WorkoutStep(
            type="interval",
            duration=DurationDef(type="distance", value_meters=100),
            target=TargetDef(type="open"),
        ),
    ]
    flat = build_flat_steps(steps)
    assert flat[0].duration_s == DEFAULT_SWIM_CSS_SEC_PER_100M


def test_build_flat_steps_calorie_duration_is_zero_and_dropped():
    steps = [
        WorkoutStep(
            type="interval",
            duration=DurationDef(type="calories", value_calories=50),
            target=TargetDef(type="rpe", rpe=5),
        ),
    ]
    assert build_flat_steps(steps) == []


def test_build_flat_steps_no_duration_is_dropped():
    steps = [WorkoutStep(type="interval", target=TargetDef(type="rpe", rpe=5))]
    assert build_flat_steps(steps) == []


def test_build_table_steps_preserves_repeat_iterations():
    steps = [
        WorkoutStep(
            type="repeat",
            iterations=4,
            steps=[
                WorkoutStep(
                    type="interval",
                    duration=DurationDef(type="time", value_seconds=30),
                    target=TargetDef(type="rpe", rpe=8),
                ),
                WorkoutStep(
                    type="recovery",
                    duration=DurationDef(type="time", value_seconds=60),
                    target=TargetDef(type="rpe", rpe=2),
                ),
            ],
        ),
    ]
    rows = build_table_steps(steps)
    assert len(rows) == 2
    assert all(r.iterations == 4 for r in rows)


def test_build_table_steps_nested_repeat_multiplies_iterations():
    steps = [
        WorkoutStep(
            type="repeat",
            iterations=2,
            steps=[
                WorkoutStep(
                    type="repeat",
                    iterations=3,
                    steps=[
                        WorkoutStep(
                            type="interval",
                            duration=DurationDef(type="time", value_seconds=20),
                            target=TargetDef(type="rpe", rpe=9),
                        ),
                    ],
                ),
            ],
        ),
    ]
    rows = build_table_steps(steps)
    assert len(rows) == 1
    assert rows[0].iterations == 6


def test_build_table_steps_non_repeat_step_and_zero_duration_sub_skipped():
    steps = [
        WorkoutStep(
            type="warmup",
            duration=DurationDef(type="time", value_seconds=300),
            target=TargetDef(type="rpe", rpe=3),
        ),
        WorkoutStep(
            type="repeat",
            iterations=2,
            steps=[
                WorkoutStep(type="rest", description="pause"),
            ],
        ),
    ]
    rows = build_table_steps(steps)
    assert len(rows) == 1
    assert rows[0].step_type == "warmup"


def test_build_table_steps_distance_sub_step_uses_swim_css():
    steps = [
        WorkoutStep(
            type="repeat",
            iterations=2,
            steps=[
                WorkoutStep(
                    type="interval",
                    duration=DurationDef(type="distance", value_meters=50),
                    target=TargetDef(type="swim_pace_zone", zone=2),
                ),
            ],
        ),
    ]
    rows = build_table_steps(steps, swim_css_sec_per_100m=80.0)
    assert len(rows) == 1
    assert rows[0].duration_s == 40.0


def _one_step_label(target: TargetDef | None, secondary: TargetDef | None = None):
    step = WorkoutStep(
        type="interval",
        duration=DurationDef(type="time", value_seconds=60),
        target=target,
        secondary_target=secondary,
    )
    flat = build_flat_steps([step])
    assert len(flat) == 1
    return flat[0].primary_label, flat[0].secondary_label


def test_format_target_none_renders_dash():
    primary, _ = _one_step_label(None)
    assert primary == "—"


def test_format_target_heart_rate_zone_without_bpm():
    primary, _ = _one_step_label(TargetDef(type="heart_rate_zone", zone=2))
    assert primary == "HR Z2"


def test_format_target_heart_rate_zone_with_bpm():
    primary, _ = _one_step_label(
        TargetDef(type="heart_rate_zone", zone=2, low_bpm=130, high_bpm=145)
    )
    assert primary == "HR Z2 (130-145 bpm)"


def test_format_target_heart_rate_zone_with_ceiling_only():
    # Zone 1's band starts at 0 bpm, so the resolver leaves low_bpm unset:
    # the label must still carry the ceiling instead of a bare zone number.
    primary, _ = _one_step_label(
        TargetDef(type="heart_rate_zone", zone=1, low_bpm=None, high_bpm=144)
    )
    assert primary == "HR Z1 (under 144 bpm)"


def test_format_target_heart_rate_band_crossing_the_athletes_zones_has_no_number():
    # The bpm band is the prescription; a zone number is only honest when
    # the band lies inside one of the athlete's own zones.
    primary, _ = _one_step_label(
        TargetDef(type="heart_rate_zone", zone=None, low_bpm=None, high_bpm=143)
    )
    assert primary == "HR under 143 bpm"
    primary, _ = _one_step_label(
        TargetDef(type="heart_rate_zone", zone=None, low_bpm=126, high_bpm=143)
    )
    assert primary == "HR 126-143 bpm"


def test_format_target_pace_without_bounds():
    primary, _ = _one_step_label(TargetDef(type="pace"))
    assert primary == "Pace"


def test_format_target_pace_with_bounds():
    primary, _ = _one_step_label(
        TargetDef(type="pace", low_min_per_km=4.5, high_min_per_km=4.8)
    )
    assert primary == "4:30/km-4:48/km"


def test_format_target_pace_zone():
    primary, _ = _one_step_label(TargetDef(type="pace_zone", zone=3))
    assert primary == "Pace Z3"


def test_format_target_swim_pace_zone():
    primary, _ = _one_step_label(TargetDef(type="swim_pace_zone", zone=2))
    assert primary == "Swim Z2"


def test_format_target_swim_pace_without_bounds():
    primary, _ = _one_step_label(TargetDef(type="swim_pace"))
    assert primary == "Swim pace"


def test_format_target_swim_pace_with_bounds():
    primary, _ = _one_step_label(
        TargetDef(type="swim_pace", low_sec_per_100m=90, high_sec_per_100m=95)
    )
    assert primary == "1:30-1:35/100m"


def test_format_target_power_without_bounds():
    primary, _ = _one_step_label(TargetDef(type="power"))
    assert primary == "Power"


def test_format_target_power_with_bounds():
    primary, _ = _one_step_label(TargetDef(type="power", low_watts=200, high_watts=220))
    assert primary == "200-220 W"


def test_format_target_power_zone():
    primary, _ = _one_step_label(TargetDef(type="power_zone", zone=5))
    assert primary == "Power Z5"


def test_format_target_rpe():
    primary, _ = _one_step_label(TargetDef(type="rpe", rpe=6))
    assert primary == "RPE 6"


def test_format_target_cadence():
    primary, _ = _one_step_label(TargetDef(type="cadence", low_rpm=85, high_rpm=95))
    assert primary == "85-95 rpm"


def test_format_target_open():
    primary, _ = _one_step_label(TargetDef(type="open"))
    assert primary == "Open effort"


def test_format_target_unknown_type_falls_back_to_raw():
    primary, _ = _one_step_label(TargetDef(type="speed"))
    assert primary == "speed"


def _band_for(target: TargetDef, additional: list[TargetDef] | None = None) -> str:
    step = WorkoutStep(
        type="interval",
        duration=DurationDef(type="time", value_seconds=60),
        target=target,
        additional_targets=additional,
    )
    return build_flat_steps([step])[0].zone_band


def test_zone_band_warmup_and_cooldown():
    for step_type in ("warmup", "cooldown"):
        step = WorkoutStep(
            type=step_type,
            duration=DurationDef(type="time", value_seconds=60),
            target=TargetDef(type="rpe", rpe=3),
        )
        assert build_flat_steps([step])[0].zone_band == "wu_cd"


def test_zone_band_recovery_and_rest():
    recovery_step = WorkoutStep(
        type="recovery",
        duration=DurationDef(type="time", value_seconds=60),
        target=TargetDef(type="rpe", rpe=2),
    )
    assert build_flat_steps([recovery_step])[0].zone_band == "recovery"
    # A "rest" step always carries a zero/None duration in practice (see
    # _rest_workout), so this branch is never reached through
    # build_flat_steps — call the mapper directly to cover it.
    assert _zone_band_from_target("rest", None) == "rest"


def test_zone_band_follows_the_planner_split():
    """Zones 1-2 low, 3-4 middle, 5 up high: LT1 tops zone 2, LT2 zone 4."""
    for target_type in ("heart_rate_zone", "pace_zone", "swim_pace_zone"):
        bands = [_band_for(TargetDef(type=target_type, zone=z)) for z in range(1, 6)]
        assert bands == ["z_low", "z_low", "z_mid", "z_mid", "z_high"]


def test_zone_band_pace_zone_clamped_above_five():
    assert _band_for(TargetDef(type="pace_zone", zone=7)) == "z_high"


def test_zone_band_power_zone_clamped_above_seven():
    assert _band_for(TargetDef(type="power_zone", zone=9)) == "z_high"


def test_zone_band_rpe_low():
    assert _band_for(TargetDef(type="rpe", rpe=4)) == "z_low"


def test_zone_band_rpe_mid():
    assert _band_for(TargetDef(type="rpe", rpe=6)) == "z_mid"


def test_zone_band_rpe_high():
    assert _band_for(TargetDef(type="rpe", rpe=9)) == "z_high"


def test_zone_band_falls_back_to_additional_targets():
    assert (
        _band_for(TargetDef(type="open"), [TargetDef(type="power_zone", zone=6)])
        == "z_high"
    )


def test_zone_band_no_matching_target_defaults_low():
    assert _band_for(TargetDef(type="open")) == "z_low"


def test_zone_numbers_extracted_from_all_target_slots():
    step = WorkoutStep(
        type="interval",
        duration=DurationDef(type="time", value_seconds=60),
        target=TargetDef(type="heart_rate_zone", zone=3),
        secondary_target=TargetDef(type="pace_zone", zone=2),
        additional_targets=[
            TargetDef(type="power_zone", zone=4),
            TargetDef(type="swim_pace_zone", zone=1),
        ],
    )
    zones = build_flat_steps([step])[0].zone_numbers
    assert zones == {"hr": 3, "pace": 2, "power": 4, "swim_pace": 1}


def test_zone_numbers_none_when_no_zone_targets():
    step = WorkoutStep(
        type="interval",
        duration=DurationDef(type="time", value_seconds=60),
        target=TargetDef(type="open"),
    )
    assert build_flat_steps([step])[0].zone_numbers is None


def test_zone_numbers_warmup_defaults_hr_pace_power_swim_pace():
    step = WorkoutStep(
        type="warmup",
        duration=DurationDef(type="time", value_seconds=60),
        target=TargetDef(type="pace"),
        secondary_target=TargetDef(type="power"),
        additional_targets=[TargetDef(type="swim_pace"), TargetDef(type="rpe", rpe=3)],
    )
    zones = build_flat_steps([step])[0].zone_numbers
    assert zones == {"hr": 1, "pace": 1, "power": 1, "swim_pace": 1}


def test_zone_numbers_warmup_keeps_explicit_hr_zone():
    step = WorkoutStep(
        type="cooldown",
        duration=DurationDef(type="time", value_seconds=60),
        target=TargetDef(type="heart_rate_zone", zone=2),
    )
    zones = build_flat_steps([step])[0].zone_numbers
    assert zones == {"hr": 2}


def _placed(activity_key: ActivityKey, sport: SportType) -> PlacedSession:
    return PlacedSession(
        slot_id="s1",
        activity_key=activity_key,
        sport=sport,
        origin=SessionOrigin.ANCHOR,
        week=0,
        day_of_week=None,
        target_hours=1.0,
    )


def test_coaching_notes_no_notes_for_plain_easy_run():
    placed = _placed(ActivityKey.RUN_EASY, SportType.RUNNING)
    notes = coaching_notes(placed, duration_min=30.0, total_meters=0.0, flat_steps=[])
    assert notes == []


def test_coaching_notes_brick_with_warmup():
    placed = _placed(ActivityKey.RUN_BRICK_SHORT, SportType.RUNNING)
    flat_steps = [
        FlatStep(
            step_type="warmup",
            duration_s=300,
            zone_band="wu_cd",
            primary_label="",
            secondary_label="",
            extra_labels=[],
            description="",
        )
    ]
    notes = coaching_notes(
        placed, duration_min=30.0, total_meters=0.0, flat_steps=flat_steps
    )
    assert any("start within 5 min of bike dismount" in n for n in notes)


def test_coaching_notes_brick_without_warmup():
    placed = _placed(ActivityKey.RUN_BRICK_SHORT, SportType.RUNNING)
    notes = coaching_notes(placed, duration_min=30.0, total_meters=0.0, flat_steps=[])
    assert any("no separate run warmup" in n for n in notes)


def test_coaching_notes_cycling_fueling_moderate_duration():
    placed = _placed(ActivityKey.BIKE_LONG, SportType.CYCLING)
    notes = coaching_notes(placed, duration_min=160.0, total_meters=0.0, flat_steps=[])
    assert any("60-80 g carbs" in n for n in notes)
    # Up to 75 minutes water alone covers the ride, as on the brief sheet.
    shorter = coaching_notes(placed, duration_min=75.0, total_meters=0.0, flat_steps=[])
    assert not any("Fueling" in n for n in shorter)


def test_coaching_notes_cycling_fueling_long_duration():
    placed = _placed(ActivityKey.BIKE_LONG, SportType.CYCLING)
    notes = coaching_notes(placed, duration_min=200.0, total_meters=0.0, flat_steps=[])
    assert any("60-90 g carbs" in n for n in notes)


def test_coaching_notes_cycling_short_duration_no_fueling_note():
    placed = _placed(ActivityKey.BIKE_ENDURANCE, SportType.CYCLING)
    notes = coaching_notes(placed, duration_min=40.0, total_meters=0.0, flat_steps=[])
    assert notes == []


def test_coaching_notes_running_long_cadence_note():
    placed = _placed(ActivityKey.RUN_LONG, SportType.RUNNING)
    notes = coaching_notes(
        placed,
        duration_min=120.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.IRONMAN,
    )
    assert any("Long-run cadence" in n for n in notes)


def test_coaching_notes_running_long_short_duration_no_cadence_note():
    placed = _placed(ActivityKey.RUN_LONG, SportType.RUNNING)
    notes = coaching_notes(
        placed,
        duration_min=60.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.IRONMAN,
    )
    assert notes == []


def test_coaching_notes_half_ironman_long_run_gets_fueling_not_im_note():
    """R-SC-031 + R-SC-014: a 70.3 PEAK long run gets the generic fueling
    cue, never the IM run-leg note."""
    placed = _placed(ActivityKey.RUN_LONG_PROGRESSION, SportType.RUNNING)
    notes = coaching_notes(
        placed,
        duration_min=125.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.HALF_IRONMAN,
    )
    assert not any("Long-run cadence" in n for n in notes)
    assert any("Fueling" in n for n in notes)


def test_coaching_notes_goalless_long_run_has_no_goal_gated_note():
    """R-SC-031: an ad-hoc workout belongs to no plan → no goal-specific
    note; the goal-independent fueling cue (names no race distance) stays."""
    placed = _placed(ActivityKey.RUN_LONG, SportType.RUNNING)
    notes = coaching_notes(placed, duration_min=120.0, total_meters=0.0, flat_steps=[])
    assert not any("Long-run cadence" in n for n in notes)
    assert any("Fueling" in n for n in notes)


def test_coaching_notes_marathon_long_run_gets_fueling_not_im_note():
    """R-SC-031: the IM note stays IM-routed; a marathon long run carries
    the generic fueling cue instead."""
    placed = _placed(ActivityKey.RUN_LONG, SportType.RUNNING)
    notes = coaching_notes(
        placed,
        duration_min=150.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.RUNNING_MARATHON,
    )
    assert not any("Long-run cadence" in n for n in notes)
    assert any("30-60 g carbs" in n for n in notes)


def test_coaching_notes_short_long_run_has_no_fueling_note():
    """The fueling cue starts at 75 min (R-SC-014's threshold), like the
    bike side."""
    placed = _placed(ActivityKey.RUN_LONG, SportType.RUNNING)
    notes = coaching_notes(
        placed,
        duration_min=70.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.RUNNING_HALF_MARATHON,
    )
    assert notes == []


def test_coaching_notes_swim_ows_swap_in():
    placed = _placed(ActivityKey.SWIM_LONG, SportType.SWIMMING)
    notes = coaching_notes(
        placed, duration_min=60.0, total_meters=3000.0, flat_steps=[]
    )
    assert any("OWS swap-in" in n for n in notes)


@pytest.mark.parametrize("key", [ActivityKey.RUN_TRAIL_LONG, ActivityKey.RUN_TRAIL_B2B])
def test_coaching_notes_trail_long_run_says_how_to_fuel(key: ActivityKey):
    placed = _placed(key, SportType.RUNNING)

    long_day = coaching_notes(
        placed,
        duration_min=75.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.TRAIL_20K,
    )
    short_day = coaching_notes(
        placed,
        duration_min=74.0,
        total_meters=0.0,
        flat_steps=[],
        goal=GoalType.TRAIL_20K,
    )

    assert sum(n.startswith("<b>Fueling:") for n in long_day) == 1
    assert not any("Fueling" in n for n in short_day)


def test_coaching_notes_swim_short_distance_no_ows_note():
    placed = _placed(ActivityKey.SWIM_LONG, SportType.SWIMMING)
    notes = coaching_notes(placed, duration_min=60.0, total_meters=500.0, flat_steps=[])
    assert notes == []


def test_coaching_notes_race_rehearsal_cycling_kit():
    placed = _placed(ActivityKey.BIKE_LONG_IM_RACE_PACE, SportType.CYCLING)
    notes = coaching_notes(placed, duration_min=180.0, total_meters=0.0, flat_steps=[])
    assert any("aero bars" in n for n in notes)


def test_coaching_notes_race_rehearsal_run_kit():
    placed = _placed(ActivityKey.RUN_BRICK_IM_TEMPO, SportType.RUNNING)
    notes = coaching_notes(placed, duration_min=30.0, total_meters=0.0, flat_steps=[])
    assert any("dress rehearsal" in n for n in notes)


def test_session_coaching_notes_routes_on_the_activity_and_duration():
    ensure_registry_loaded()
    activity = get_activity(ActivityKey.BIKE_LONG)
    workout = activity.build(activity.tss_max)

    notes = session_coaching_notes(
        activity, workout, duration_min=200.0, resolver=None, goal=GoalType.IRONMAN
    )

    assert any("Fueling" in n for n in notes)


def test_session_coaching_notes_reads_swim_css_off_the_resolver():
    ensure_registry_loaded()
    activity = get_activity(ActivityKey.SWIM_LONG)
    workout = activity.build(activity.tss_max)
    resolver = SimpleNamespace(swim_css_sec_per_100m=95.0)

    with_resolver = session_coaching_notes(
        activity, workout, duration_min=70.0, resolver=resolver
    )
    without = session_coaching_notes(
        activity, workout, duration_min=70.0, resolver=None
    )

    assert with_resolver == without  # notes are duration/distance routed, not paced


def _trail_vert_notes(key, duration_min, goal):
    ensure_registry_loaded()
    activity = get_activity(key)
    workout = activity.build(activity.tss_min)
    notes = session_coaching_notes(
        activity, workout, duration_min=duration_min, resolver=None, goal=goal
    )
    return [n for n in notes if "Vert" in n]


def test_trail_long_run_vert_target_scales_with_duration():
    one_hour = _trail_vert_notes(ActivityKey.RUN_TRAIL_LONG, 60.0, GoalType.TRAIL_20K)
    two_hours = _trail_vert_notes(ActivityKey.RUN_TRAIL_LONG, 120.0, GoalType.TRAIL_20K)
    # 20K density band 170-260 m/h, rounded to 50 m.
    assert any("~150-250 m" in n for n in one_hour)
    assert any("~350-500 m" in n for n in two_hours)


def test_trail_b2b_vert_target_uses_the_goal_band():
    notes = _trail_vert_notes(ActivityKey.RUN_TRAIL_B2B, 60.0, GoalType.TRAIL_100K)
    # 100K density band 280-420 m/h, rounded to 100 m on the card.
    assert any("~300-400 m" in n for n in notes)


def test_trail_vert_note_needs_a_trail_goal():
    assert (
        _trail_vert_notes(ActivityKey.RUN_TRAIL_LONG, 90.0, GoalType.RUNNING_MARATHON)
        == []
    )
    assert _trail_vert_notes(ActivityKey.RUN_TRAIL_LONG, 90.0, None) == []


def test_trail_vert_note_tiny_run_keeps_a_readable_band():
    notes = _trail_vert_notes(ActivityKey.RUN_TRAIL_LONG, 15.0, GoalType.TRAIL_20K)
    # Both ends round to 50 m; the guard keeps the band open (50-100).
    assert any("~50-100 m" in n for n in notes)
