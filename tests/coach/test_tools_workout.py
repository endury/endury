"""Tests for the ad-hoc workout tool: build_workout."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from endury.coach.tools import workout_tools as workout_tools_module
from endury.coach.tools.workout_tools import make_workout_tools
from endury.intervals.models import AthleteProfile, SportSettingsRow
from tests.coach.conftest import StubIntervalsClient


def _get_tool(tools_list, name):
    return next(t for t in tools_list if t.name == name)


def _sport_settings(*rows):
    """An intervals.icu athlete profile carrying the given settings rows."""
    return AthleteProfile(id="i1", sportSettings=list(rows))


@pytest.fixture
def intervals_client():
    """No thresholds on file by default."""
    return StubIntervalsClient(athlete=_sport_settings())


@pytest.fixture
def workout_tools(intervals_client):
    repos = MagicMock()
    repos.intervals_client = intervals_client
    return make_workout_tools(repos)


@pytest.fixture
def build_workout(workout_tools):
    return _get_tool(workout_tools, "build_workout")


# make_workout_tools


def test_make_workout_tools_returns_single_tool(workout_tools):
    assert [t.name for t in workout_tools] == ["build_workout"]


# Threshold degradation (no / stale thresholds)


def test_note_points_at_the_tool_that_records_a_swap(build_workout):
    # Nothing is persisted, so a workout swapped in for a planned session
    # leaves that session dangling unless the coach either puts it into the
    # plan or marks the session.
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "threshold", "duration_minutes": 45}
        )
    )
    assert result["note"].startswith("Ad-hoc workout")
    assert "swap_planned_session" in result["note"]
    assert "skip_planned_session" in result["note"]


def test_no_thresholds_degrades_to_zone_and_rpe(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "threshold", "duration_minutes": 45}
        )
    )
    assert result["activity_key"] == "run_threshold"
    assert "targets_note" in result
    assert "No athlete thresholds" in result["targets_note"]
    interval = next(s for s in result["steps"] if s["type"] == "interval")
    assert interval["primary_target"] == "HR Z4"
    assert result["note"].startswith("Ad-hoc workout")


def test_unreachable_intervals_degrades_to_zone_and_rpe(
    build_workout, intervals_client
):
    """An API failure must degrade targets, not fail the tool."""
    intervals_client.athlete_error = RuntimeError("network down")
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "threshold", "duration_minutes": 45}
        )
    )
    assert "could not be reached" in result["targets_note"]
    interval = next(s for s in result["steps"] if s["type"] == "interval")
    assert interval["primary_target"] == "HR Z4"


def test_fresh_thresholds_resolve_real_targets(build_workout, intervals_client):
    intervals_client.athlete = _sport_settings(
        SportSettingsRow(types=["Run"], lthr=165, threshold_pace=3.97)
    )
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "threshold", "duration_minutes": 45}
        )
    )
    assert "targets_note" not in result
    interval = next(s for s in result["steps"] if s["type"] == "interval")
    assert "/km" in interval["primary_target"] or "bpm" in interval["primary_target"]


def test_lthr_only_resolves_bpm_range(build_workout, intervals_client):
    """An LTHR-only threshold must reach the rendered HR target.

    No pace is on record, so the primary target falls back to a
    heart-rate zone. 0.96 lands in HR Z4, which (unlike Z1) has a
    non-zero low boundary, so the bpm range actually renders.
    """
    intervals_client.athlete = _sport_settings(
        SportSettingsRow(types=["Run"], lthr=170)
    )
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "threshold", "duration_minutes": 45}
        )
    )
    assert "targets_note" not in result
    interval = next(s for s in result["steps"] if s["type"] == "interval")
    assert "bpm" in interval["primary_target"]


def test_thresholds_without_swim_css_use_the_default_css(
    build_workout, intervals_client
):
    """Run thresholds on file, no swim row: swim steps fall back to default CSS."""
    intervals_client.athlete = _sport_settings(
        SportSettingsRow(types=["Run"], lthr=165, threshold_pace=3.97)
    )
    result = json.loads(
        build_workout.invoke(
            {"sport": "swimming", "intent": "threshold", "duration_minutes": 30}
        )
    )
    assert "targets_note" not in result
    assert result["activity_key"]


def test_swim_css_from_sport_settings_drives_distance_steps(
    build_workout, intervals_client
):
    intervals_client.athlete = _sport_settings(
        SportSettingsRow(types=["Swim"], threshold_pace=1.11)
    )
    result = json.loads(
        build_workout.invoke(
            {"sport": "swimming", "intent": "threshold", "duration_minutes": 30}
        )
    )
    assert "targets_note" not in result
    distance_steps = [s for s in result["steps"] if s["distance_m"]]
    assert distance_steps


# Intent -> activity resolution (real registry)


@pytest.mark.parametrize(
    ("sport", "intent", "expected_key"),
    [
        ("running", "threshold", "run_threshold"),
        ("running", "vo2", "run_vo2max_short"),
        ("running", "long", "run_long"),
        ("running", "endurance", "run_easy"),
        ("running", "easy", "run_easy"),
        ("running", "recovery", "run_recovery"),
        ("running", "brick", "run_brick_short"),
        ("running", "race_pace", "run_hm_pace"),
        ("running", "strides", "run_strides"),
        ("cycling", "threshold", "bike_threshold"),
        ("cycling", "easy", "bike_endurance"),
        ("cycling", "endurance", "bike_endurance"),
        ("swimming", "threshold", "swim_css"),
        ("swimming", "vo2", "swim_vo2max"),
        ("swimming", "endurance", "swim_endurance"),
        ("swimming", "easy", "swim_endurance"),
        ("swimming", "recovery", "swim_recovery"),
        ("swimming", "long", "swim_long"),
        (" RUNNING ", " Threshold ", "run_threshold"),
    ],
)
def test_intent_resolution_picks_canonical_activity(
    build_workout, sport, intent, expected_key
):
    result = json.loads(
        build_workout.invoke({"sport": sport, "intent": intent, "duration_minutes": 40})
    )
    assert result["activity_key"] == expected_key


def test_race_pace_intent_builds_one_continuous_main_block(build_workout):
    """race_pace is a warmup + one continuous block + cooldown, not reps."""
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "race_pace", "duration_minutes": 40}
        )
    )
    step_types = [s["type"] for s in result["steps"]]
    assert step_types == ["warmup", "interval", "cooldown"]


def test_unknown_sport_returns_error_with_options(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "rowing", "intent": "threshold", "duration_minutes": 40}
        )
    )
    assert "Unknown sport" in result["error"]
    assert set(result["options"]) == {"running", "cycling", "swimming"}


def test_unknown_intent_returns_error_with_options(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "sprints", "duration_minutes": 40}
        )
    )
    assert "Unknown intent" in result["error"]
    assert "threshold" in result["options"]
    assert "brick" in result["options"]


def test_brick_unsupported_for_cycling_returns_supporting_sports(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "cycling", "intent": "brick", "duration_minutes": 40}
        )
    )
    assert "error" in result
    assert result["options"] == ["running"]


def test_brick_unsupported_for_swimming_returns_supporting_sports(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "swimming", "intent": "brick", "duration_minutes": 40}
        )
    )
    assert "error" in result
    assert result["options"] == ["running"]


def test_strides_unsupported_for_swimming_returns_supporting_sports(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "swimming", "intent": "strides", "duration_minutes": 40}
        )
    )
    assert "error" in result
    assert result["options"] == ["cycling", "running"]


# activity_key path


def test_activity_key_overrides_sport_and_intent(build_workout):
    result = json.loads(
        build_workout.invoke(
            {
                "sport": "ignored",
                "intent": "ignored",
                "duration_minutes": 30,
                "activity_key": "swim_css",
            }
        )
    )
    assert result["activity_key"] == "swim_css"
    assert result["sport"] == "swimming"


def test_unknown_activity_key_returns_error_with_registry_options(build_workout):
    result = json.loads(
        build_workout.invoke(
            {
                "sport": "running",
                "intent": "threshold",
                "duration_minutes": 30,
                "activity_key": "not_a_real_key",
            }
        )
    )
    assert "Unknown activity_key" in result["error"]
    assert "run_threshold" in result["options"]


def test_empty_activity_key_falls_back_to_intent_resolution(build_workout):
    result = json.loads(
        build_workout.invoke(
            {
                "sport": "running",
                "intent": "easy",
                "duration_minutes": 30,
                "activity_key": "",
            }
        )
    )
    assert result["activity_key"] == "run_easy"


# Validation + error handling


def test_zero_duration_returns_error(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "easy", "duration_minutes": 0}
        )
    )
    assert "duration_minutes" in result["error"]


def test_negative_duration_returns_error(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "easy", "duration_minutes": -5}
        )
    )
    assert "duration_minutes" in result["error"]


def test_exception_returns_tool_error_response(build_workout, monkeypatch):
    def _boom(_repos):
        raise RuntimeError("DB exploded")

    # get_planner_load_context itself already swallows repo-level errors, so
    # force a failure past that point to exercise build_workout's own
    # try/except.
    monkeypatch.setattr(workout_tools_module, "get_planner_load_context", _boom)
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "easy", "duration_minutes": 30}
        )
    )
    assert "error" in result
    assert "message" in result


# Response shape / coaching notes wiring


def test_brick_workout_includes_brick_coaching_note(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "brick", "duration_minutes": 25}
        )
    )
    assert any("Brick" in n for n in result["coaching_notes"])


def test_response_carries_a_quotable_outline(build_workout):
    # The model must not invent its own step durations: it quotes the
    # exact lines given here, one per step plus a total.
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "easy", "duration_minutes": 40}
        )
    )
    lines = result["outline"].splitlines()
    assert len(lines) == len(result["steps"]) + 1
    assert all(line.startswith("**") for line in lines)
    assert lines[0].startswith("**Warm-up** — ")
    assert lines[-1].startswith("**Total** — ")
    assert "load" in lines[-1]
    assert "quoting" in result["note"]


def test_swim_outline_lines_carry_metres(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "swimming", "intent": "threshold", "duration_minutes": 30}
        )
    )
    assert any(
        " m (" in line or line.endswith(" m") for line in result["outline"].splitlines()
    )


def test_response_reports_target_and_realized_duration(build_workout):
    result = json.loads(
        build_workout.invoke(
            {"sport": "running", "intent": "easy", "duration_minutes": 40}
        )
    )
    assert result["target_duration_minutes"] == 40
    assert isinstance(result["duration_minutes"], float)
    assert isinstance(result["target_tss"], float)
    assert isinstance(result["achieved_tss"], float)


# An undated workout lands in no week, so it carries no week warning


def test_a_60_min_vo2_ride_is_three_30_15_sets_with_no_week_warning(build_workout):
    # A 60-min VO2 ride came back as one 35-min 30/15 set, led by "not meant
    # for recovery weeks — use intent 'easy'", with no plan behind it.
    result = json.loads(
        build_workout.invoke(
            {"sport": "cycling", "intent": "vo2", "duration_minutes": 60}
        )
    )
    assert result["activity_key"] == "bike_vo2max_short"
    assert result["duration_minutes"] == 60.2
    assert "caveats" not in result
    assert result["outline"].count("**Main** — 13x [30 s") == 3
