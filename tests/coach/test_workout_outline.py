"""Tests for the shared ad-hoc workout outline formatter."""

from __future__ import annotations

from typing import Any

from endury.coach.workout_outline import build_workout_outline


def _step(step_type: str, duration_s: float, **extra: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "type": step_type,
        "duration_s": duration_s,
        "zone_band": "z_low",
        "primary_target": "HR Z2",
        "secondary_target": "",
        "additional_targets": [],
        "description": "",
        "distance_m": None,
        "zone_numbers": None,
    }
    step.update(extra)
    return step


def _payload(*steps: dict[str, Any], **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "activity_key": "run_easy",
        "duration_minutes": 35.0,
        "target_duration_minutes": 35.0,
        "achieved_tss": 42.4,
        "steps": list(steps),
    }
    payload.update(extra)
    return payload


def test_three_step_run_renders_phase_lines_and_total() -> None:
    outline = build_workout_outline(
        _payload(
            _step("warmup", 480.0, primary_target="HR Z1"),
            _step("interval", 1320.0, primary_target="4:45/km"),
            _step("cooldown", 300.0, primary_target="HR Z1"),
        )
    )
    assert list(outline.steps) == [
        "**Warm-up** — 8 min (HR Z1)",
        "**Main** — 22 min (4:45/km)",
        "**Cool-down** — 5 min (HR Z1)",
    ]
    assert outline.total == "**Total** — 35 min · ~42 load"
    assert outline.total_minutes == 35
    assert outline.text == "\n".join([*outline.steps, outline.total])


def test_swim_step_with_distance_renders_metres() -> None:
    outline = build_workout_outline(
        _payload(
            _step("interval", 600.0, distance_m=400, primary_target="1:45/100m"),
            activity_key="swim_css",
        )
    )
    assert outline.steps == ("**Main** — 400 m (1:45/100m)",)


def test_clean_half_minute_step_keeps_the_half() -> None:
    outline = build_workout_outline(_payload(_step("recovery", 450.0)))
    assert outline.steps == ("**Recovery** — 7.5 min (HR Z2)",)


def test_sub_minute_rest_is_quoted_in_seconds() -> None:
    # A 20 s swim rest reads as "0 min" if rounded to whole minutes.
    outline = build_workout_outline(
        _payload(_step("recovery", 20.0, primary_target="Open effort"))
    )
    assert outline.steps == ("**Recovery** — 20 s (Open effort)",)


def test_target_with_its_own_brackets_is_not_wrapped_in_a_second_pair() -> None:
    # A resolved HR target already reads "HR Z1 (under 143 bpm)"; it must not
    # be wrapped in a second pair of brackets.
    outline = build_workout_outline(
        _payload(_step("warmup", 480.0, primary_target="HR Z1 (under 143 bpm)"))
    )
    assert outline.steps == ("**Warm-up** — 8 min (HR Z1, under 143 bpm)",)


def test_bracketed_swim_band_folds_into_a_comma_clause() -> None:
    outline = build_workout_outline(
        _payload(
            _step(
                "interval",
                600.0,
                distance_m=400,
                primary_target="Swim Z2 (1:50-2:05/100m)",
            )
        )
    )
    assert outline.steps == ("**Main** — 400 m (Swim Z2, 1:50-2:05/100m)",)


def test_a_wholly_bracketed_target_keeps_exactly_one_pair() -> None:
    outline = build_workout_outline(
        _payload(_step("rest", 60.0, primary_target="(easy)"))
    )
    assert outline.steps == ("**Rest** — 1 min (easy)",)


def test_step_without_primary_target_falls_back_to_zone_band() -> None:
    outline = build_workout_outline(
        _payload(_step("rest", 60.0, primary_target="", zone_band="rest"))
    )
    assert outline.steps == ("**Rest** — 1 min (rest)",)


def test_step_with_no_target_at_all_drops_the_parenthetical() -> None:
    outline = build_workout_outline(
        _payload(_step("interval", 600.0, primary_target="", zone_band=""))
    )
    assert outline.steps == ("**Main** — 10 min",)


def test_unknown_step_type_is_titled() -> None:
    outline = build_workout_outline(_payload(_step("free_swim", 600.0)))
    assert outline.steps[0].startswith("**Free Swim** —")


def test_total_without_tss_omits_the_tss_clause() -> None:
    payload = _payload(_step("interval", 1800.0))
    del payload["achieved_tss"]
    outline = build_workout_outline(payload)
    assert outline.total == "**Total** — 35 min"


def test_total_falls_back_to_summed_step_minutes() -> None:
    payload = _payload(_step("warmup", 600.0), _step("interval", 900.0))
    del payload["duration_minutes"]
    outline = build_workout_outline(payload)
    assert outline.total_minutes == 25


def test_a_rep_and_its_recovery_fold_into_one_count() -> None:
    # A 30/15 set came out as 26 lines, one per rep and recovery.
    rep = _step("interval", 30.0, primary_target="346-367 W")
    rest = _step("recovery", 15.0, primary_target="165-176 W")
    outline = build_workout_outline(
        _payload(
            _step("warmup", 900.0, primary_target="165-176 W"),
            *[rep, rest] * 13,
            _step("cooldown", 600.0, primary_target="135-144 W"),
        )
    )
    assert outline.steps == (
        "**Warm-up** — 15 min (165-176 W)",
        "**Main** — 13x [30 s (346-367 W) + 15 s recovery (165-176 W)]",
        "**Cool-down** — 10 min (135-144 W)",
    )


def test_identical_steps_back_to_back_fold_into_one_count() -> None:
    swim = _step("interval", 600.0, distance_m=400, primary_target="Swim Z2")
    outline = build_workout_outline(_payload(swim, swim, swim, swim))
    assert outline.steps == ("**Main** — 4x 400 m (Swim Z2)",)


def test_two_work_blocks_fold_without_a_phase_word_between() -> None:
    under = _step("interval", 120.0, primary_target="280-297 W")
    over = _step("interval", 60.0, primary_target="316-335 W")
    rest = _step("recovery", 180.0, primary_target="135-144 W")
    outline = build_workout_outline(_payload(*[under, over] * 3, rest))
    assert outline.steps == (
        "**Main** — 3x [2 min (280-297 W) + 1 min (316-335 W)]",
        "**Recovery** — 3 min (135-144 W)",
    )
