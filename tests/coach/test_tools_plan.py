"""Tests for the planner coach tools."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.proposals import run_tool
from endury.coach.tools import make_tools
from endury.coach.tools.plan_tools import make_plan_tools
from endury.intervals.models import ActivityStream, IntervalRow
from tests.coach.conftest import (
    StubIntervalsClient,
    make_activity,
    make_activity_detail,
)


def _get(tools, name):
    return next(t for t in tools if t.name == name)


def _noop_coach():
    return None


def _tools(repos, maybe_get_coach=_noop_coach, intervals_client=None):
    return make_plan_tools(repos, maybe_get_coach, intervals_client)


def _plan_row(**overrides):
    base = {
        "id": 7,
        "name": "Ironman Build",
        "goal_type": "ironman",
        "goal_date": "2026-09-01",
        "plan_start": "2026-01-05",
        "revision": 2,
        "status": "active",
        "solver_version": "planner_v2_session_menu_v1",
        "solve_result_json": "{}",
        "athlete_state_json": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _session_row(**overrides):
    base = {
        "id": 501,
        "plan_id": 7,
        "slot_id": "w05_tempo_run_0",
        "date": "2026-02-10",
        "week_index": 5,
        "sport": "run",
        "activity_key": "tempo_run",
        "family": "tempo",
        "phase_type": "build",
        "target_tss": 66.4,
        "achieved_tss": 66.4,
        "duration_minutes": 60.0,
        "status": "pending",
        "matched_activity_id": None,
        "duration_target_minutes": None,
        "tid_fractions_json": None,
        "workout_json": json.dumps(
            {
                "name": "tempo_run",
                "coaching_intent": "Build lactate threshold.",
                "steps": [
                    {"type": "warmup", "duration_seconds": 600},
                    {"type": "interval", "duration_seconds": 1200},
                    {"type": "cooldown", "duration_seconds": 600},
                ],
            }
        ),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# get_planned_sessions


def test_planned_sessions_returns_full_steps_for_short_range():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(),
        _session_row(id=502, date="2026-02-11", matched_activity_id=42),
    ]
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "2026-02-10", "end_date": "2026-02-16"})
    )
    assert result["plan"] == "Ironman Build"
    assert len(result["sessions"]) == 2
    first = result["sessions"][0]
    assert first["session_id"] == 501
    assert first["completed"] is False
    assert first["target_tss"] == 66.4
    assert first["coaching_intent"] == "Build lactate threshold."
    assert len(first["steps"]) == 3
    assert "step_count" not in first
    assert result["sessions"][1]["completed"] is True


def test_planned_sessions_long_range_uses_step_count_and_summary():
    long_workout = json.dumps(
        {
            "name": "brick",
            "coaching_intent": "Practice race transitions.",
            "steps": [{"type": f"step{i}"} for i in range(7)],
        }
    )
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(workout_json=long_workout)
    ]
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "2026-01-01", "end_date": "2026-01-31"})
    )
    session = result["sessions"][0]
    assert "steps" not in session
    assert session["step_count"] == 7
    assert session["summary"].startswith("7 steps: ")
    assert session["summary"].endswith("…")


def test_planned_sessions_malformed_dates_return_error_not_empty_range():
    """A malformed date must surface as an error, never a silent empty range
    that the coach could mistake for a confirmed rest day."""
    repo = MagicMock()
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "not-a-date", "end_date": "also-not"})
    )
    assert "error" in result
    assert "sessions" not in result
    repo.plan.get_active.assert_not_called()
    repo.plan.get_sessions_by_date_range.assert_not_called()


def test_planned_sessions_invalid_calendar_date_returns_error():
    repo = MagicMock()
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "2026-13-40", "end_date": "2026-02-16"})
    )
    assert "error" in result
    repo.plan.get_active.assert_not_called()


def test_planned_sessions_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "2026-02-10", "end_date": "2026-02-16"})
    )
    assert "No active training plan" in result["message"]
    assert "create_plan" in result["message"]


def test_planned_sessions_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("db down")
    tool = _get(_tools(repo), "get_planned_sessions")
    result = json.loads(
        tool.invoke({"start_date": "2026-02-10", "end_date": "2026-02-16"})
    )
    assert "error" in result


# get_active_plan_overview


def test_overview_returns_metadata():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert result["name"] == "Ironman Build"
    assert result["goal_type"] == "ironman"
    assert result["revision"] == 2
    assert result["phase_weeks"] == []


def test_overview_reports_phase_week_date_boundaries():
    """The taper's start_date is its week's Monday — even a rest-day one —
    so the coach never derives phase starts from individual sessions."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=json.dumps(
            {"phase_calendar": ["base", "base", "build", "build", "taper"]}
        )
    )
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert result["phase_weeks"] == [
        {
            "phase": "base",
            "weeks": "1-2",
            "start_date": "2026-01-05",
            "end_date": "2026-01-18",
        },
        {
            "phase": "build",
            "weeks": "3-4",
            "start_date": "2026-01-19",
            "end_date": "2026-02-01",
        },
        {
            "phase": "taper",
            "weeks": "5",
            "start_date": "2026-02-02",
            "end_date": "2026-02-08",
        },
    ]


def test_overview_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert "No active training plan" in result["message"]
    assert "create_plan" in result["message"]


def test_overview_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("boom")
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert "error" in result


def test_overview_races_for_a_only_plan():
    """A single-race plan reports its own A race in the races list."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=json.dumps(
            {
                "request": {
                    "races": [
                        {
                            "goal_type": "ironman",
                            "race_date": "2026-04-12",
                            "priority": "A",
                        }
                    ]
                }
            }
        )
    )
    tool = _get(_tools(repo), "get_active_plan_overview")
    with patch("endury.common.clock.today", return_value=date(2026, 2, 1)):
        result = json.loads(tool.invoke({}))
    assert result["races"] == [
        {
            "priority": "A",
            "goal_type": "ironman",
            "label": "full-distance triathlon",
            "date": "2026-04-12",
            "days_or_weeks_until": "in 10 weeks",
        }
    ]


def test_overview_includes_ambition_and_measured_inputs():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        athlete_state_json=json.dumps(
            {
                "sources": {"running": "history"},
                "sports": {"running": {"recent_weekly_hours": 2.4}},
            }
        )
    )
    repo.plan.get_macro_for_plan.return_value = SimpleNamespace(
        ambition_level="moderate"
    )
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert result["ambition"] == "moderate"
    assert result["measured_inputs"] == {
        "running": {"weekly_hours": 2.4, "source": "history"}
    }


def test_overview_ambition_and_measured_inputs_null_when_missing():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_macro_for_plan.return_value = None
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert result["ambition"] is None
    assert result["measured_inputs"] is None


def test_overview_ambition_null_on_unreadable_macro_row():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_macro_for_plan.return_value = SimpleNamespace(
        ambition_level="not-a-real-tier"
    )
    tool = _get(_tools(repo), "get_active_plan_overview")
    result = json.loads(tool.invoke({}))
    assert result["ambition"] is None


def test_overview_races_include_bc_tuneups():
    """B/C tune-up races surface alongside the A goal, same shape, right dates."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=json.dumps(
            {
                "request": {
                    "races": [
                        {
                            "goal_type": "running_marathon",
                            "race_date": "2026-02-01",
                            "priority": "A",
                        },
                        {
                            "goal_type": "running_10k",
                            "race_date": "2026-01-27",
                            "priority": "B",
                        },
                        {
                            "goal_type": "running_5k",
                            "race_date": "2026-02-07",
                            "priority": "C",
                        },
                    ]
                }
            }
        )
    )
    tool = _get(_tools(repo), "get_active_plan_overview")
    with patch("endury.common.clock.today", return_value=date(2026, 2, 1)):
        result = json.loads(tool.invoke({}))
    assert result["races"] == [
        {
            "priority": "A",
            "goal_type": "running_marathon",
            "label": "marathon",
            "date": "2026-02-01",
            "days_or_weeks_until": "today",
        },
        {
            "priority": "B",
            "goal_type": "running_10k",
            "label": "10K",
            "date": "2026-01-27",
            "days_or_weeks_until": "5 days ago",
        },
        {
            "priority": "C",
            "goal_type": "running_5k",
            "label": "5K",
            "date": "2026-02-07",
            "days_or_weeks_until": "in 6 days",
        },
    ]


# get_session_menu_catalog


def test_menu_catalog_known_goal():
    tool = _get(_tools(MagicMock()), "get_session_menu_catalog")
    result = json.loads(tool.invoke({"goal": "ironman"}))
    assert result["goal"] == "ironman"
    assert len(result["phases"]) >= 1
    assert all("sessions" in p for p in result["phases"])


def test_menu_catalog_unknown_goal_lists_options():
    tool = _get(_tools(MagicMock()), "get_session_menu_catalog")
    result = json.loads(tool.invoke({"goal": "quidditch"}))
    assert "error" in result
    assert "ironman" in result["options"]


def test_menu_catalog_swallows_lookup_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_tools.get_session_menu",
        MagicMock(side_effect=RuntimeError("menu boom")),
    )
    tool = _get(_tools(MagicMock()), "get_session_menu_catalog")
    result = json.loads(tool.invoke({"goal": "ironman"}))
    assert "error" in result


# get_plan_compliance


def test_plan_compliance_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "get_plan_compliance")
    result = json.loads(tool.invoke({}))
    assert "No active training plan" in result["message"]
    assert "create_plan" in result["message"]


def test_plan_compliance_sums_planned_and_delivered_load():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    sessions = [
        _session_row(id=1, target_tss=100.0, matched_activity_id="act-1"),
        _session_row(id=2, target_tss=50.0, matched_activity_id="missing"),
        _session_row(id=3, target_tss=40.0, matched_activity_id=None),
    ]
    repo.plan.get_sessions.return_value = sessions
    repo.plan.get_macro_for_plan.return_value = None
    # Delivered load resolves linked activities via one ranged intervals.icu
    # fetch (no per-session round trips); "missing" is simply absent.
    client = StubIntervalsClient(
        activities=[make_activity("2026-02-10", activity_id="act-1", load=80.0)]
    )

    tool = _get(_tools(repo, intervals_client=client), "get_plan_compliance")
    result = json.loads(tool.invoke({}))

    assert result["plan"] == "Ironman Build"
    assert result["planned_tss"] == 190.0
    assert result["delivered_tss"] == 80.0
    assert client.activity_windows == [(date(2026, 1, 5), date(2026, 9, 1))]
    assert "completion_rate_pct" in result
    assert "total_workouts" in result


def test_plan_compliance_draws_planned_against_done_per_week_when_asked():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(
            id=1, date="2026-02-03", target_tss=100.0, matched_activity_id="a1"
        ),
        _session_row(id=2, date="2026-02-05", target_tss=50.0),
        _session_row(
            id=3, date="2026-02-10", target_tss=80.0, matched_activity_id="a2"
        ),
        _session_row(id=4, date="2026-02-12", target_tss=90.0),  # after today
    ]
    repo.plan.get_macro_for_plan.return_value = None
    client = StubIntervalsClient(
        activities=[
            make_activity("2026-02-03", activity_id="a1", load=90.0),
            make_activity("2026-02-10", activity_id="a2", load=88.0),
        ]
    )
    tool = _get(_tools(repo, intervals_client=client), "get_plan_compliance")

    with patch("endury.common.clock.today", return_value=date(2026, 2, 11)):
        result = json.loads(tool.invoke({"chart": True}))
        plain = json.loads(tool.invoke({}))

    (chart,) = result["charts"]
    assert (chart["kind"], chart["unit"]) == ("bar", "load")
    assert chart["labels"] == ["Planned", "Done"]
    assert chart["x"] == ["2026-02-02", "2026-02-09"]
    assert chart["ys"] == [[150, 80], [90, 88]]
    assert plain["charts"] == []


def test_plan_compliance_without_a_client_reports_no_delivered_load():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=1, target_tss=100.0, matched_activity_id="act-1")
    ]
    repo.plan.get_macro_for_plan.return_value = None
    tool = _get(_tools(repo), "get_plan_compliance")
    result = json.loads(tool.invoke({}))
    assert result["planned_tss"] == 100.0
    assert result["delivered_tss"] is None


def test_plan_compliance_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("boom")
    tool = _get(_tools(repo), "get_plan_compliance")
    result = json.loads(tool.invoke({}))
    assert "error" in result


# get_session_vs_actual


def test_session_vs_actual_malformed_date_returns_error():
    """A malformed date must surface as an error, not a silent "no session"."""
    repo = MagicMock()
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "tomorrow"}))
    assert "error" in result
    repo.plan.get_active.assert_not_called()


def test_session_vs_actual_invalid_calendar_date_returns_error():
    repo = MagicMock()
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-13-40"}))
    assert "error" in result
    repo.plan.get_active.assert_not_called()


def test_session_vs_actual_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert "No active training plan" in result["message"]
    assert "create_plan" in result["message"]


def test_session_vs_actual_no_session_that_day():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = []
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert "No planned session" in result["message"]


def test_session_vs_actual_not_yet_matched():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(matched_activity_id=None)
    ]
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    session = result["sessions"][0]
    assert session["delivered"] is None
    assert "Not yet linked" in session["message"]
    assert session["prescribed"]["target_tss"] == 66.4


def test_session_vs_actual_linked_activity_missing():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(matched_activity_id="gone")
    ]
    tool = _get(
        _tools(repo, intervals_client=StubIntervalsClient()), "get_session_vs_actual"
    )
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    session = result["sessions"][0]
    assert session["delivered"] is None
    assert "could not be found" in session["message"]


def test_session_vs_actual_reports_delivered_shape_and_compliance():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(matched_activity_id="act-1")
    ]
    client = StubIntervalsClient(
        activities=[
            make_activity(
                "2026-02-10",
                activity_id="act-1",
                load=70.0,
                moving_time=3300,
                compliance=92.0,
            ).model_copy(
                update={
                    "name": "Threshold run",
                    "average_heartrate": 158,
                    "icu_intensity": 91.0,
                    "pace": 3.333,
                    "interval_summary": ["3x 3m52s 172bpm"],
                }
            )
        ]
    )
    tool = _get(_tools(repo, intervals_client=client), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    delivered = result["sessions"][0]["delivered"]
    assert delivered == {
        "activity_id": "act-1",
        "name": "Threshold run",
        "training_load": 70.0,
        "duration_minutes": 55.0,
        "distance_km": 10.0,
        "compliance_pct": 92.0,
        "avg_hr": 158,
        "intensity_factor": 0.91,
        "pace_min_km": "5:00",
        "interval_summary": ["3x 3m52s 172bpm"],
    }


def _completed_test_client(*activity_ids: str) -> StubIntervalsClient:
    return StubIntervalsClient(
        activities=[
            make_activity("2026-02-10", activity_id=activity_id, load=60.0)
            for activity_id in activity_ids
        ]
    )


def test_session_vs_actual_test_session_carries_threshold_update():
    """A completed test must tell the athlete where to set the new number."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(
            family="test",
            sport="running",
            activity_key="run_threshold_test",
            matched_activity_id="act-1",
        )
    ]
    tool = _get(
        _tools(repo, intervals_client=_completed_test_client("act-1")),
        "get_session_vs_actual",
    )
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    update = result["sessions"][0]["threshold_update"]
    assert "threshold pace" in update["protocol"]
    assert update["set_in"] == (
        "Planning Inputs → Running → Edit: threshold pace and LTHR"
    )
    assert "intervals.icu sport settings" in update["why"]


def test_session_vs_actual_test_session_uses_the_sports_own_protocol():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(
            family="test",
            sport="cycling",
            activity_key="bike_ftp_test",
            matched_activity_id="act-1",
        ),
        _session_row(
            id=502,
            family="test",
            sport="rowing",
            activity_key="odd_test",
            matched_activity_id="act-2",
        ),
    ]
    tool = _get(
        _tools(repo, intervals_client=_completed_test_client("act-1", "act-2")),
        "get_session_vs_actual",
    )
    sessions = json.loads(tool.invoke({"date": "2026-02-10"}))["sessions"]
    assert "FTP" in sessions[0]["threshold_update"]["set_in"]
    assert "Sports" in sessions[1]["threshold_update"]["set_in"]


def test_session_vs_actual_pending_test_session_has_no_threshold_update():
    # Nothing to set until the test has actually been run.
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(family="test", sport="running", activity_key="run_threshold_test")
    ]
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert "threshold_update" not in result["sessions"][0]
    assert result["sessions"][0]["message"] == "Not yet linked to a completed activity."


def _test_readout(sport, sport_type, intervals, streams=()):
    """Run get_session_vs_actual over one completed test with these reps."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(family="test", sport=sport, matched_activity_id="act-1")
    ]
    client = StubIntervalsClient(
        activities=[make_activity("2026-02-10", sport_type, activity_id="act-1")],
        details=[
            make_activity_detail(
                "2026-02-10", sport_type, activity_id="act-1", intervals=intervals
            )
        ],
        streams={"act-1": list(streams)},
    )
    tool = _get(_tools(repo, intervals_client=client), "get_session_vs_actual")
    return json.loads(tool.invoke({"date": "2026-02-10"}))["sessions"][0]


def test_run_test_reads_threshold_pace_and_lthr_from_the_30_minute_effort():
    session = _test_readout(
        "running",
        "Run",
        [
            IntervalRow(type="WORK", elapsed_time=900, average_speed=2.8),
            # 30 min, 6.16 km: 1000 / 3.4222 = 292 s/km -> 4:52
            IntervalRow(
                type="WORK",
                elapsed_time=1800,
                distance=6160.0,
                average_speed=3.4222,
                average_heartrate=172,
            ),
            IntervalRow(type="RECOVERY", elapsed_time=600, average_speed=2.5),
        ],
    )

    assert session["suggested_thresholds"] == {
        "sport": "running",
        "threshold_pace_min_km": "4:52",
        "lthr_bpm": 172,
    }
    effort = session["test_result"]["efforts"][0]
    assert (effort["duration"], effort["distance_km"]) == ("30:00", 6.16)
    assert "set_athlete_thresholds" in session["next_step"]
    assert "threshold_update" not in session


def test_run_test_reads_lthr_from_the_efforts_last_20_minutes():
    # A 10-minute warm-up at 130 bpm, then the 30-minute effort: 160 bpm for
    # its first ten minutes and 170 for its last twenty (average 167).
    heartrate = [130.0] * 600 + [160.0] * 600 + [170.0] * 1200
    session = _test_readout(
        "running",
        "Run",
        [
            IntervalRow(
                type="WORK",
                start_time=600,
                elapsed_time=1800,
                average_speed=3.4222,
                average_heartrate=167,
            )
        ],
        streams=[
            ActivityStream(type="time", data=[float(t) for t in range(2400)]),
            ActivityStream(type="heartrate", data=heartrate),
        ],
    )

    assert session["suggested_thresholds"]["lthr_bpm"] == 170


def test_run_test_without_a_long_work_interval_reads_the_longest_with_a_note():
    session = _test_readout(
        "running",
        "Run",
        [
            IntervalRow(type="WORK", elapsed_time=600, average_speed=3.3),
            IntervalRow(elapsed_time=1500, average_speed=3.2, average_heartrate=168),
        ],
    )

    assert session["suggested_thresholds"]["lthr_bpm"] == 168
    assert "longest interval" in session["test_result"]["note"]


def test_ride_test_sets_ftp_and_lthr_from_the_20_minute_effort():
    session = _test_readout(
        "cycling",
        "Ride",
        [
            IntervalRow(
                type="WORK",
                elapsed_time=1200,
                average_watts=280,
                average_heartrate=165,
            )
        ],
    )

    # 0.95 x 280 = 266; 0.98 x 165 = 161.7 -> 162
    assert session["suggested_thresholds"] == {
        "sport": "cycling",
        "ftp_watts": 266,
        "lthr_bpm": 162,
    }


def test_swim_test_reads_css_from_the_400_and_200():
    session = _test_readout(
        "swimming",
        "Swim",
        [
            IntervalRow(type="WORK", elapsed_time=200, distance=200.0),
            IntervalRow(type="WORK", elapsed_time=360, distance=400.0),
            IntervalRow(type="WORK", elapsed_time=170, distance=200.0),
        ],
    )

    # (360 - 170) / 2 = 95 s/100 m -> 1:35; the slower 200 is not the test.
    assert session["suggested_thresholds"] == {
        "sport": "swimming",
        "css_min_100m": "1:35",
    }
    assert len(session["test_result"]["efforts"]) == 2


def test_swim_test_with_one_effort_falls_back_to_setting_it_by_hand():
    session = _test_readout(
        "swimming",
        "Swim",
        [IntervalRow(type="WORK", elapsed_time=360, distance=400.0)],
    )

    assert "suggested_thresholds" not in session
    assert "400 m and 200 m" in session["test_result"]["note"]
    assert "Swimming" in session["threshold_update"]["set_in"]


def test_test_without_pace_power_or_heart_rate_falls_back():
    session = _test_readout(
        "cycling", "Ride", [IntervalRow(type="WORK", elapsed_time=1200)]
    )

    assert "suggested_thresholds" not in session
    assert "no pace, power or heart rate" in session["test_result"]["note"]
    assert "threshold_update" in session


def test_unreadable_test_activity_falls_back():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [
        _session_row(family="test", sport="running", matched_activity_id="act-1")
    ]
    client = _completed_test_client("act-1")
    client.detail_error = RuntimeError("boom")
    tool = _get(_tools(repo, intervals_client=client), "get_session_vs_actual")

    session = json.loads(tool.invoke({"date": "2026-02-10"}))["sessions"][0]

    assert session["test_result"]["note"] == "The test's intervals could not be read."
    assert "threshold_update" in session


def test_session_vs_actual_restates_each_steps_bands_in_prose():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [_session_row()]
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert result["sessions"][0]["prescribed"]["targets_summary"] == [
        "warmup 10 min",
        "interval 20 min",
        "cooldown 10 min",
    ]


def test_session_vs_actual_non_test_session_has_no_threshold_update():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = [_session_row()]
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert "threshold_update" not in result["sessions"][0]


def test_session_vs_actual_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("boom")
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "2026-02-10"}))
    assert "error" in result


# get_race_day_outlook


def _solve_result_json(projections):
    return json.dumps(
        {
            "sessions": [{"date": "2026-01-05"}],
            "projections": projections,
        }
    )


def test_race_day_outlook_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    assert "No active training plan" in result["message"]
    assert "create_plan" in result["message"]


def test_race_day_outlook_no_render_data():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=json.dumps({"sessions": []})
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    assert "No render data" in result["message"]


def test_race_day_outlook_no_projections():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json({})
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    assert "No projection data" in result["message"]


def test_race_day_outlook_downsamples_weekly_with_race_day():
    projections = {
        "aggregate_ctl": list(range(15)),
        "aggregate_tsb": list(range(0, -15, -1)),
        "ctl_by_sport": {"running": list(range(15))},
        "tsb_by_sport": {"running": list(range(15))},
        "goal_type": "ironman",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json(projections)
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))

    ctl_points = result["aggregate_ctl"]
    assert [p["date"] for p in ctl_points] == ["2026-01-05", "2026-01-12", "2026-01-19"]
    assert ctl_points[-1]["race_day"] is True
    assert "running" in result["ctl_by_sport"]
    assert "projection_vintage" in result


def test_race_day_outlook_reports_projected_vs_live_today(monkeypatch):
    """After a disruption the live numbers must sit next to the stale curve."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-01-08")  # day 3
    projections = {
        "aggregate_ctl": [50.0, 51.0, 52.0, 53.0, 54.0],
        "aggregate_tsb": [-1.0, -2.0, -3.0, -4.0, -5.0],
        "ctl_by_sport": {},
        "tsb_by_sport": {},
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json(projections)
    )
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-06", activity_id="a1", load=70.0)]
    )
    tool = _get(_tools(repo, intervals_client=client), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))

    assert result["projected_today"] == {
        "date": "2026-01-08",
        "ctl": 53.0,
        "tsb": -4.0,
    }
    # The live trajectory comes from intervals.icu load, not the stored
    # curve — a couple of days of training reads far below the 53.0 CTL the
    # plan projected.
    assert result["live_today"]["date"] == "2026-01-08"
    assert 0 < result["live_today"]["ctl"] < 53.0


def test_race_day_outlook_omits_live_today_without_training_history(monkeypatch):
    """No intervals.icu activities at all → no live reading to quote."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-01-08")
    projections = {
        "aggregate_ctl": [50.0, 51.0, 52.0, 53.0, 54.0],
        "aggregate_tsb": [-1.0, -2.0, -3.0, -4.0, -5.0],
        "ctl_by_sport": {},
        "tsb_by_sport": {},
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json(projections)
    )
    tool = _get(
        _tools(repo, intervals_client=StubIntervalsClient()), "get_race_day_outlook"
    )
    result = json.loads(tool.invoke({}))
    assert "live_today" not in result


def test_race_day_outlook_marks_non_weekly_race_day_explicitly():
    projections = {
        "aggregate_ctl": list(range(10)),
        "aggregate_tsb": list(range(10)),
        "ctl_by_sport": {},
        "tsb_by_sport": {},
        "goal_type": "ironman",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json(projections)
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    ctl_points = result["aggregate_ctl"]
    assert len(ctl_points) == 3
    assert ctl_points[-1]["race_day"] is True
    assert ctl_points[-1]["date"] == "2026-01-14"


def test_race_day_outlook_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("boom")
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    assert "error" in result


def test_race_day_outlook_empty_series_yields_empty_points():
    projections = {
        "aggregate_ctl": [],
        "aggregate_tsb": list(range(8)),
        "ctl_by_sport": {},
        "tsb_by_sport": {},
        "goal_type": "ironman",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        solve_result_json=_solve_result_json(projections)
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    assert result["aggregate_ctl"] == []
    assert len(result["aggregate_tsb"]) == 2


def test_race_day_outlook_maintenance_plan_appends_block_end_marker():
    """Non-weekly-aligned series: block_end is appended as an extra point."""
    projections = {
        "aggregate_ctl": list(range(10)),
        "aggregate_tsb": list(range(10)),
        "ctl_by_sport": {},
        "tsb_by_sport": {},
        "goal_type": "running_maintenance",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        goal_type="running_maintenance",
        solve_result_json=_solve_result_json(projections),
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    ctl_points = result["aggregate_ctl"]
    assert len(ctl_points) == 3
    assert ctl_points[-1]["block_end"] is True
    assert "race_day" not in ctl_points[-1]
    assert "block" in result["note"].lower()


def test_race_day_outlook_maintenance_plan_marks_block_end_at_week_boundary():
    """Weekly-aligned series: block_end overwrites the last weekly point."""
    projections = {
        "aggregate_ctl": list(range(15)),
        "aggregate_tsb": list(range(0, -15, -1)),
        "ctl_by_sport": {"running": list(range(15))},
        "tsb_by_sport": {"running": list(range(15))},
        "goal_type": "running_maintenance",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        goal_type="running_maintenance",
        solve_result_json=_solve_result_json(projections),
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    ctl_points = result["aggregate_ctl"]
    assert ctl_points[-1]["block_end"] is True
    assert "race_day" not in ctl_points[-1]
    assert "block" in result["note"].lower()


def test_race_day_outlook_unparseable_goal_type_falls_back_to_race_day():
    projections = {
        "aggregate_ctl": list(range(10)),
        "aggregate_tsb": list(range(10)),
        "ctl_by_sport": {},
        "tsb_by_sport": {},
        "goal_type": "not_a_real_goal",
    }
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row(
        goal_type="not_a_real_goal",
        solve_result_json=_solve_result_json(projections),
    )
    tool = _get(_tools(repo), "get_race_day_outlook")
    result = json.loads(tool.invoke({}))
    ctl_points = result["aggregate_ctl"]
    assert ctl_points[-1]["race_day"] is True
    assert "note" not in result


# module helpers


def test_parse_workout_handles_missing_and_invalid():
    from endury.coach.tools.plan_tools import _parse_workout

    assert _parse_workout(None) == {}
    assert _parse_workout("") == {}
    assert _parse_workout("{not valid json") == {}
    assert _parse_workout('"just a string"') == {}


def test_step_summary_empty_steps():
    from endury.coach.tools.plan_tools import _step_summary

    assert _step_summary([]) == "No structured steps recorded."


# set_session_status


def test_set_session_status_invalid_status():
    repo = MagicMock()
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "bogus"}))
    assert "Invalid status" in result["error"]
    repo.plan.get_active.assert_not_called()


def test_set_session_status_no_active_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = None
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "completed"}))
    assert "No active training plan" in result["message"]


def test_set_session_status_session_not_in_plan():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [_session_row(id=999)]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "completed"}))
    assert "not part of the active plan" in result["error"]
    repo.plan.update_session_status.assert_not_called()


def test_set_session_status_updates_with_activity_link():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [_session_row(id=501)]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(
        tool.invoke({"session_id": 501, "status": "completed", "activity_id": "act-9"})
    )
    assert result["status"] == "ok"
    assert result["new_status"] == "completed"
    repo.plan.update_session_status.assert_called_once_with(501, "completed", "act-9")


def test_set_session_status_refuses_a_link_against_the_activitys_name(monkeypatch):
    # Seen live: "I ran the Sunday session today" linked yesterday's
    # strides three times, though the run was named after next Sunday's.
    from endury.coach.tools import plan_edit_tools

    asked = []
    monkeypatch.setattr(
        plan_edit_tools,
        "name_mismatch",
        lambda _repos, _client, activity_id, session_id: (
            asked.append(session_id) or "the watch ran the Long Trail Run"
        ),
    )
    repo = MagicMock()
    tool = _get(_tools(repo, intervals_client=MagicMock()), "set_session_status")
    args = {"session_id": 470, "status": "completed", "activity_id": "i9"}

    refused = json.loads(tool.invoke(args))

    assert refused == {"error": "the watch ran the Long Trail Run"}
    assert asked == [470]
    repo.plan.update_session_status.assert_not_called()


def test_set_session_status_without_activity_preserves_existing_link():
    # Re-affirming/changing status without an activity_id must not wipe an
    # existing matched activity — the repo write is unconditional.
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=501, matched_activity_id="act-9")
    ]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "completed"}))
    assert result["status"] == "ok"
    assert result["activity_id"] == "act-9"
    repo.plan.update_session_status.assert_called_once_with(501, "completed", "act-9")


def test_set_session_status_refuses_a_skip_and_names_the_card_tool():
    """Seen live: nine immediate status writes in one turn left the
    week out of step with the coach's own reply; a skip is a card now."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [_session_row(id=501)]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "skipped"}))
    assert "skip_planned_session" in result["error"]
    repo.plan.update_session_status.assert_not_called()


def test_set_session_status_unlinked_clears_the_activity_link():
    # "unlinked" is the remedy the reschedule refusal names — it must
    # actually null matched_activity_id so the session reads as not-done.
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=501, matched_activity_id="act-9")
    ]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "unlinked"}))
    assert result["status"] == "ok"
    assert result["activity_id"] is None
    repo.plan.update_session_status.assert_called_once_with(501, "unlinked", None)


def test_set_session_status_unlinked_ignores_a_supplied_activity_id():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [_session_row(id=501)]
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(
        tool.invoke({"session_id": 501, "status": "unlinked", "activity_id": "act-9"})
    )
    assert result["activity_id"] is None
    repo.plan.update_session_status.assert_called_once_with(501, "unlinked", None)


def test_move_planned_session_docstring_names_the_unlink_remedy():
    # The completed-session refusal is relayed to the athlete by the coach,
    # so the remedy has to be one the coach can carry out itself.
    tool = _get(_tools(MagicMock()), "move_planned_session")
    assert 'set_session_status(session_id, "unlinked")' in tool.description


def test_set_session_status_swallows_errors():
    repo = MagicMock()
    repo.plan.get_active.side_effect = RuntimeError("boom")
    tool = _get(_tools(repo), "set_session_status")
    result = json.loads(tool.invoke({"session_id": 501, "status": "completed"}))
    assert "error" in result


class _StatusClient:
    """The calendar slice a status write uses."""

    def __init__(self):
        self.deleted = []
        self.upserted = []

    def bulk_delete_events(self, external_ids):
        self.deleted.append(list(external_ids))
        return len(external_ids)

    def bulk_upsert_events(self, events):
        self.upserted.append(events)
        return []


def test_skip_planned_session_previews_and_writes_nothing():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=501, activity_key="run_tempo")
    ]
    client = _StatusClient()
    tool = _get(_tools(repo, intervals_client=client), "skip_planned_session")

    result = json.loads(tool.invoke({"session_id": 501}))

    assert result["status"] == "preview"
    assert result["confirm_required"] is True
    assert result["session"]["date"] == "2026-02-10"
    repo.plan.update_session_status.assert_not_called()
    assert client.deleted == []


def test_skip_planned_session_applied_deletes_the_calendar_event():
    # A skip the athlete's watch still shows is not a skip.
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=501, activity_key="run_tempo")
    ]
    client = _StatusClient()
    tool = _get(_tools(repo, intervals_client=client), "skip_planned_session")

    result = json.loads(tool.invoke({"session_id": 501, "confirm": True}))

    assert result["status"] == "ok"
    assert result["push"]["status"] == "ok"
    assert "skipped and off your calendar" in result["message"]
    repo.plan.update_session_status.assert_called_once_with(501, "skipped", None)
    assert client.deleted == [["coachie:7:w05_tempo_run_0"]]


@pytest.mark.parametrize(
    ("overrides", "phrase"),
    [
        ({"status": "skipped"}, "already skipped"),
        ({"status": "completed"}, "already done"),
        ({"matched_activity_id": "i1"}, "already done"),
    ],
)
def test_skip_planned_session_refuses_a_session_with_nothing_to_skip(overrides, phrase):
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(id=501, activity_key="run_tempo", **overrides)
    ]
    tool = _get(_tools(repo), "skip_planned_session")
    result = json.loads(tool.invoke({"session_id": 501}))
    assert result["status"] == "refused"
    assert phrase in result["message"]
    repo.plan.update_session_status.assert_not_called()


def test_set_session_status_un_skipping_re_pushes_the_event():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions.return_value = [
        _session_row(
            id=501,
            status="skipped",
            pushed_event_json=json.dumps(
                {
                    "external_id": "coachie:7:w05_tempo_run_0",
                    "start_date_local": "2026-02-10T00:00:00",
                    "name": "Tempo Run",
                }
            ),
        )
    ]
    client = _StatusClient()
    tool = _get(_tools(repo, intervals_client=client), "set_session_status")

    result = json.loads(tool.invoke({"session_id": 501, "status": "pending"}))

    assert result["push"]["pushed"] == 1
    assert client.upserted[0][0]["start_date_local"] == "2026-02-10T00:00:00"


def test_skip_planned_session_docstring_names_the_pause_tool():
    # A whole sick week is a pause, not five hand-rolled skips.
    tool = _get(_tools(MagicMock()), "skip_planned_session")
    assert "pause_training" in tool.description
    assert "Runs as a preview" in tool.description
    assert "intervals.icu" in tool.description


# move_planned_session


def test_move_planned_session_is_registered():
    assert "move_planned_session" in {t.name for t in _tools(MagicMock())}


def test_move_planned_session_delegates_to_the_use_case(monkeypatch):
    reschedule = MagicMock(
        return_value={"status": "ok", "session_id": 501, "date": "2026-08-20"}
    )
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.reschedule_session", reschedule
    )
    repo = MagicMock()
    client = MagicMock()

    tool = _get(_tools(repo, _noop_coach, client), "move_planned_session")
    result = json.loads(tool.invoke({"session_id": 501, "date": "2026-08-20"}))

    assert result["status"] == "ok"
    args, kwargs = reschedule.call_args
    assert args[0] == 501
    assert args[1].date == "2026-08-20"
    assert kwargs["get_repos"]() is repo
    assert kwargs["client"] is client


def test_move_planned_session_relays_the_refusal_reason(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.reschedule_session",
        MagicMock(
            return_value={
                "status": "error",
                "message": "Cannot move a session into the past.",
            }
        ),
    )
    tool = _get(_tools(MagicMock()), "move_planned_session")
    result = json.loads(tool.invoke({"session_id": 501, "date": "2020-01-01"}))
    assert result["status"] == "error"
    assert result["message"] == "Cannot move a session into the past."


def test_move_planned_session_relays_the_warning(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.reschedule_session",
        MagicMock(
            return_value={
                "status": "ok",
                "session_id": 501,
                "date": "2026-08-20",
                "warning": "2026-08-20 already had 1 session — the day now holds 2.",
            }
        ),
    )
    tool = _get(_tools(MagicMock()), "move_planned_session")
    result = json.loads(tool.invoke({"session_id": 501, "date": "2026-08-20"}))
    assert "the day now holds 2" in result["warning"]


def test_move_planned_session_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.reschedule_session",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "move_planned_session")
    result = json.loads(tool.invoke({"session_id": 501, "date": "2026-08-20"}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# replan_active_plan


def test_replan_previews_without_confirmation(monkeypatch):
    replan_mock = MagicMock()
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_replan", replan_mock)
    tool = _get(_tools(MagicMock()), "replan_active_plan")
    result = json.loads(tool.invoke({}))
    assert result["status"] == "preview"
    assert result["confirm_required"] is True
    replan_mock.assert_not_called()


def test_replan_confirmed_runs_and_reports_reasoning(monkeypatch):
    replan_mock = MagicMock(
        return_value={
            "status": "ok",
            "action": "replanned",
            "reasoning": "Regenerated 10 sessions.",
        }
    )
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_replan", replan_mock)
    repo = MagicMock()
    coach_accessor = MagicMock(return_value="the-coach")
    tool = _get(_tools(repo, coach_accessor), "replan_active_plan")
    result = json.loads(tool.invoke({"confirm": True}))

    assert result["status"] == "ok"
    assert result["reasoning"] == "Regenerated 10 sessions."
    _, kwargs = replan_mock.call_args
    assert kwargs["repos"] is repo
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert callable(kwargs["progress"])
    assert kwargs["progress"]({"foo": "bar"}) is None


def test_replan_preview_reports_the_readback_diff(monkeypatch):
    """The chat path gets the same drift warning the Replan button shows."""
    diff = [
        {
            "external_id": "coachie:7:w03",
            "date": "2026-08-17",
            "name": "Long Run",
            "changed_fields": ["description"],
        }
    ]
    readback = MagicMock(return_value=diff)
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.replan_readback_diff", readback
    )
    client = MagicMock()
    repo = MagicMock()

    tool = _get(_tools(repo, _noop_coach, client), "replan_active_plan")
    result = json.loads(tool.invoke({}))

    assert result["status"] == "preview"
    assert result["edited_workouts"] == diff
    assert "edited on intervals.icu" in result["message"]
    readback.assert_called_once_with(repos=repo, client=client)


def test_replan_confirmed_pushes_through_the_bound_client(monkeypatch):
    """Without the client the regenerated plan never reaches the calendar."""
    replan_mock = MagicMock(
        return_value={"status": "ok", "action": "replanned", "reasoning": "done"}
    )
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_replan", replan_mock)
    client = MagicMock()

    tool = _get(_tools(MagicMock(), _noop_coach, client), "replan_active_plan")
    json.loads(tool.invoke({"confirm": True}))

    assert replan_mock.call_args.kwargs["client"] is client


def test_replan_confirmed_surfaces_value_error(monkeypatch):
    """run_replan's ValueErrors are curated reasons — never "try again later"."""
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_replan",
        MagicMock(
            side_effect=ValueError(
                "The goal race has passed; create a new plan instead."
            )
        ),
    )
    tool = _get(_tools(MagicMock()), "replan_active_plan")
    result = json.loads(tool.invoke({"confirm": True}))
    assert result["status"] == "error"
    assert "goal race has passed" in result["message"]
    assert "try again later" not in json.dumps(result).lower()


def test_replan_unexpected_error_stays_generic(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_replan",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "replan_active_plan")
    result = json.loads(tool.invoke({"confirm": True}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


def test_replan_active_plan_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "replan_active_plan")
    assert "Runs as a preview" in tool.description
    assert "confirm" not in tool.description


# adjust_current_week


def _adjust_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_week_adjustment", spy)
    return _get(_tools(**tool_args), "adjust_current_week"), spy


def test_adjust_current_week_schema_makes_every_argument_optional():
    tool = _get(_tools(MagicMock()), "adjust_current_week")
    properties = tool.parameters["properties"]
    assert "preset" not in properties
    assert properties["reduce_percent"] == {"type": "integer"}
    assert "confirm" not in properties
    assert "required" not in tool.parameters


def test_adjust_current_week_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "adjust_current_week")
    assert "Runs as a preview" in tool.description
    assert "today through Sunday" in tool.description
    assert "SHORTER, not EASIER" in " ".join(tool.reply_contract)
    assert "Not for illness or injury" in tool.description
    assert "pause_training" in tool.description


def test_adjust_current_week_preview_is_relayed_verbatim(monkeypatch):
    preview = {"status": "preview", "confirm_required": True, "sessions": []}
    tool, spy = _adjust_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(tool.invoke({"reduce_percent": 30}))
    assert result == preview
    assert spy.call_args.kwargs["confirm"] is False
    assert spy.call_args.kwargs["reduce_percent"] == 30


def test_adjust_current_week_confirm_binds_the_injected_dependencies(monkeypatch):
    repo = MagicMock()
    client = MagicMock()
    coach_accessor = _noop_coach
    tool, spy = _adjust_tool(
        monkeypatch,
        {"status": "ok"},
        repos=repo,
        maybe_get_coach=coach_accessor,
        intervals_client=client,
    )
    result = json.loads(tool.invoke({"reduce_percent": 40, "confirm": True}))
    assert result["status"] == "ok"
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert kwargs["confirm"] is True
    assert kwargs["reduce_percent"] == 40


def test_adjust_current_week_relays_a_validation_refusal(monkeypatch):
    refusal = {
        "status": "error",
        "message": "Pass reduce_percent — a whole percent between 0 and 60.",
    }
    tool, _ = _adjust_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({}))
    assert result == refusal


def test_adjust_current_week_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_week_adjustment",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "adjust_current_week")
    result = json.loads(tool.invoke({"reduce_percent": 30, "confirm": True}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# pause_training


def _pause_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_pause_training", spy)
    return _get(_tools(**tool_args), "pause_training"), spy


def test_pause_training_schema_requires_only_the_through_date():
    tool = _get(_tools(MagicMock()), "pause_training")
    properties = tool.parameters["properties"]
    assert properties["through_date"] == {"type": "string"}
    assert properties["from_date"] == {"type": "string"}
    assert "confirm" not in properties
    assert tool.parameters["required"] == ["through_date"]


def test_pause_training_passes_from_date_through(monkeypatch):
    tool, spy = _pause_tool(monkeypatch, {"status": "ok"}, repos=MagicMock())
    tool.invoke({"through_date": "2026-09-23", "from_date": "2026-09-20"})
    assert spy.call_args.kwargs["from_date"] == "2026-09-20"


def test_pause_training_from_date_defaults_to_none(monkeypatch):
    tool, spy = _pause_tool(monkeypatch, {"status": "ok"}, repos=MagicMock())
    tool.invoke({"through_date": "2026-09-23"})
    assert spy.call_args.kwargs["from_date"] is None


def test_pause_training_relays_a_future_window_refusal(monkeypatch):
    refusal = {
        "status": "refused",
        "error_code": "future_window",
        "message": "pause_training always starts today.",
        "suggested_tool": "set_plan_directives",
    }
    tool, _ = _pause_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(
        tool.invoke({"through_date": "2026-09-29", "from_date": "2026-09-27"})
    )
    assert result == refusal


def test_pause_training_docstring_names_from_date():
    tool = _get(_tools(MagicMock()), "pause_training")
    description = tool.description
    assert 'from_date="2026-08-27"' in description
    assert "future_window" not in description  # the error_code is wire-only
    for phrase in ("set_plan_directives", "set_session_status", "writing nothing"):
        assert phrase in description, phrase


def test_pause_training_docstring_routes_illness_here():
    """Illness, injury or an away window must route here, not to a run of
    individual skips, a week adjustment, or a full replan."""
    tool = _get(_tools(MagicMock()), "pause_training")
    description = tool.description
    for phrase in (
        "sick",
        "injured",
        "away",
        "Runs as a preview",
        "With no active plan this still works",
        # A confirmed pause records its window even when nothing was
        # pending, so the description must not still promise "nothing".
        "records the window as unavailable",
    ):
        assert phrase in description, phrase
    # The three tools this one replaces, each named as the wrong answer.
    for wrong in ("set_session_status", "adjust_current_week", "replan_active_plan"):
        assert wrong in description, wrong
    assert "Schedule tab" in description
    contract = " ".join(tool.reply_contract)
    assert "actual_percent" in contract
    assert "caveats" in contract


def test_pause_training_docstring_covers_the_planless_case():
    """With no active plan, the tool still clears the coach's own ad-hoc
    bookings and says plainly that nothing was replanned."""
    description = _get(_tools(MagicMock()), "pause_training").description
    assert "no active plan" in description
    assert "unschedule_workout" in description
    assert "Next check-in" in description


def test_pause_training_docstring_covers_the_preview_naming_bookings():
    """A preview must name every ad-hoc booking confirm would also remove,
    and an empty plan-side window must not silently skip the sweep."""
    tool = _get(_tools(MagicMock()), "pause_training")
    assert "NAMES those workouts" in tool.description
    assert "will remove on apply" in " ".join(tool.reply_contract)
    assert "nothing is booked" in tool.description


def test_pause_training_preview_is_relayed_verbatim(monkeypatch):
    preview = {
        "status": "preview",
        "confirm_required": True,
        "resume_date": "2026-09-24",
        "sessions": [{"session_id": 3, "title": "Easy Run"}],
        "caveats": ["This skips 1 session(s)."],
    }
    tool, spy = _pause_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(tool.invoke({"through_date": "2026-09-23"}))
    assert result == preview
    assert spy.call_args.kwargs["confirm"] is False
    assert spy.call_args.kwargs["through_date"] == "2026-09-23"


def test_pause_training_confirm_binds_the_injected_dependencies(monkeypatch):
    repo = MagicMock()
    client = MagicMock()
    coach_accessor = _noop_coach
    tool, spy = _pause_tool(
        monkeypatch,
        {"status": "ok"},
        repos=repo,
        maybe_get_coach=coach_accessor,
        intervals_client=client,
    )
    result = json.loads(
        tool.invoke({"through_date": "2026-09-23", "confirm": True}),
    )
    assert result["status"] == "ok"
    spy.assert_called_once()
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert kwargs["confirm"] is True


def test_pause_training_relays_a_validation_refusal(monkeypatch):
    refusal = {
        "status": "error",
        "message": "'next week' is not a date — pass YYYY-MM-DD.",
    }
    tool, _ = _pause_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"through_date": "next week"}))
    assert result == refusal


def test_pause_training_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_pause_training",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "pause_training")
    result = json.loads(tool.invoke({"through_date": "2026-09-23", "confirm": True}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


def test_pause_training_tool_relays_the_no_plan_result(monkeypatch):
    """A plan-less pause is handled entirely by the use case; the tool only
    relays whatever shape it returns, unchanged."""
    no_plan_preview = {
        "status": "preview",
        "confirm_required": True,
        "no_plan": True,
        "window": {"from": "2026-09-18", "through": "2026-09-20"},
        "workouts": [
            {
                "date": "2026-09-19",
                "name": "Shakeout",
                "external_id": "coachie:adhoc:2026-09-19:aaa",
                "event_id": 5,
            }
        ],
        "replan": None,
        "first_week": None,
        "caveats": ["There is no training plan, so nothing was replanned."],
        "message": "Nothing has been removed yet.",
    }
    tool, spy = _pause_tool(monkeypatch, no_plan_preview, repos=MagicMock())
    result = json.loads(tool.invoke({"through_date": "2026-09-20"}))
    assert result == no_plan_preview
    assert spy.call_args.kwargs["through_date"] == "2026-09-20"


# swap_planned_session


def _swap_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_session_swap", spy)
    return _get(_tools(**tool_args), "swap_planned_session"), spy


def test_swap_planned_session_schema_requires_only_the_session_id():
    tool = _get(_tools(MagicMock()), "swap_planned_session")
    properties = tool.parameters["properties"]
    assert properties["session_id"] == {"type": "integer"}
    assert properties["intent"] == {"type": "string"}
    assert properties["duration_minutes"] == {"type": "number"}
    assert "confirm" not in properties
    assert properties["allow_protected"] == {"type": "boolean"}
    assert tool.parameters["required"] == ["session_id"]


def test_swap_planned_session_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "swap_planned_session")
    assert "Runs as a preview" in tool.description
    assert "allow_protected=true" in tool.description
    assert "Relay every string in ``caveats`` verbatim" in " ".join(tool.reply_contract)
    assert "build_workout" in tool.description


def test_swap_planned_session_preview_is_relayed_verbatim(monkeypatch):
    preview = {"status": "preview", "confirm_required": True, "week": {}}
    tool, spy = _swap_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(tool.invoke({"session_id": 501, "intent": "threshold"}))
    assert result == preview
    kwargs = spy.call_args.kwargs
    assert kwargs["session_id"] == 501
    assert kwargs["intent"] == "threshold"
    assert kwargs["confirm"] is False
    assert kwargs["allow_protected"] is False
    assert kwargs["sport"] is None
    assert kwargs["activity_key"] is None
    assert kwargs["duration_minutes"] is None


def test_swap_planned_session_confirm_binds_the_injected_dependencies(monkeypatch):
    repo = MagicMock()
    client = MagicMock()
    coach_accessor = _noop_coach
    tool, spy = _swap_tool(
        monkeypatch,
        {"status": "ok"},
        repos=repo,
        maybe_get_coach=coach_accessor,
        intervals_client=client,
    )
    result = json.loads(
        tool.invoke(
            {
                "session_id": 501,
                "activity_key": "run_threshold",
                "duration_minutes": 50,
                "confirm": True,
                "allow_protected": True,
            }
        )
    )
    assert result["status"] == "ok"
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert kwargs["confirm"] is True
    assert kwargs["allow_protected"] is True
    assert kwargs["activity_key"] == "run_threshold"
    assert kwargs["duration_minutes"] == 50


def test_swap_planned_session_relays_a_guard_refusal(monkeypatch):
    refusal = {
        "status": "error",
        "message": "Session 501 sits in a taper week, which the plan protects.",
        "guards": ["taper week"],
    }
    tool, _ = _swap_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"session_id": 501, "intent": "vo2"}))
    assert result == refusal


def test_swap_planned_session_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_session_swap",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "swap_planned_session")
    result = json.loads(
        tool.invoke({"session_id": 501, "intent": "easy", "confirm": True})
    )
    assert "error" in result
    assert "boom" not in json.dumps(result)


# schedule_workout


def _schedule_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_schedule_workout", spy)
    return _get(_tools(**tool_args), "schedule_workout"), spy


def test_schedule_workout_schema_requires_duration_and_date():
    tool = _get(_tools(MagicMock()), "schedule_workout")
    properties = tool.parameters["properties"]
    assert properties["duration_minutes"] == {"type": "number"}
    assert properties["date"] == {"type": "string"}
    assert properties["sport"] == {"type": "string"}
    assert properties["intent"] == {"type": "string"}
    assert properties["activity_key"] == {"type": "string"}
    # No `confirm` parameter is what makes this tool book at once instead
    # of previewing (endury/coach/proposals.py dispatches by signature).
    assert "confirm" not in properties
    # An override flag on an ungated tool would book a hard or taper-week
    # extra with no athlete approval; that path lives on the gated
    # schedule_workout_override instead.
    assert "allow_hard" not in properties
    assert "allow_protected" not in properties
    assert sorted(tool.parameters["required"]) == ["date", "duration_minutes"]


def test_schedule_workout_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "schedule_workout")
    assert "Books it at once" in tool.description
    assert "do NOT ask permission" in tool.description
    assert "locked in" in tool.description
    assert "swap_planned_session" in tool.description
    assert "past its TSS\nallowance" in tool.description
    # event_id/external_id are internal plumbing and must never reach the athlete.
    assert "``event_id`` and ``external_id`` are internal plumbing" in " ".join(
        tool.reply_contract
    )


def test_schedule_workout_books_on_one_call(monkeypatch):
    # schedule_workout books on the first call; there is no separate confirm
    # round trip.
    booked = {"status": "ok", "date": "2026-06-01", "workout": {}}
    tool, spy = _schedule_tool(monkeypatch, booked, repos=MagicMock())
    result = json.loads(
        tool.invoke(
            {
                "duration_minutes": 45,
                "date": "2026-06-01",
                "sport": "running",
                "intent": "threshold",
            }
        )
    )
    assert result == booked
    kwargs = spy.call_args.kwargs
    assert kwargs["duration_minutes"] == 45
    assert kwargs["date_str"] == "2026-06-01"
    assert kwargs["sport"] == "running"
    assert kwargs["intent"] == "threshold"
    assert kwargs["activity_key"] is None
    assert "confirm" not in kwargs


def test_schedule_workout_binds_the_injected_dependencies(monkeypatch):
    repo = MagicMock()
    client = MagicMock()
    tool, spy = _schedule_tool(
        monkeypatch, {"status": "ok"}, repos=repo, intervals_client=client
    )
    result = json.loads(
        tool.invoke(
            {
                "duration_minutes": 45,
                "date": "2026-06-01",
                "activity_key": "run_threshold",
            }
        )
    )
    assert result["status"] == "ok"
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["activity_key"] == "run_threshold"


def test_schedule_workout_is_never_forced_to_preview(monkeypatch):
    # The dispatcher previews by signature; with no `confirm` parameter the
    # very first call runs the write instead of coming back as a preview.
    tool, _ = _schedule_tool(
        monkeypatch, {"status": "ok", "date": "2026-06-01"}, repos=MagicMock()
    )
    result = json.loads(
        run_tool(
            [tool],
            "schedule_workout",
            {"duration_minutes": 45, "date": "2026-06-01", "intent": "easy"},
        )
    )
    assert result["status"] == "ok"


def test_schedule_workout_relays_a_refusal(monkeypatch):
    refusal = {"status": "error", "message": "2026-01-01 has passed."}
    tool, _ = _schedule_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"duration_minutes": 45, "date": "2026-01-01"}))
    assert result == refusal


def test_schedule_workout_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_schedule_workout",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "schedule_workout")
    result = json.loads(
        tool.invoke(
            {
                "duration_minutes": 45,
                "date": "2026-06-01",
                "sport": "running",
                "intent": "threshold",
            }
        )
    )
    assert "error" in result
    assert "boom" not in json.dumps(result)


# schedule_workout_override

_OVERRIDE_ARGS = {
    "duration_minutes": 60,
    "date": "2026-06-01",
    "sport": "running",
    "intent": "threshold",
    "allow_hard": True,
}


def _override_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_edit_tools.run_schedule_workout", spy)
    return _get(_tools(**tool_args), "schedule_workout_override"), spy


def test_schedule_workout_override_previews_first():
    tool = _get(_tools(MagicMock()), "schedule_workout_override")
    properties = tool.parameters["properties"]
    # Previews by signature; the model never sees `confirm`.
    assert "confirm" in tool.fn.__code__.co_varnames
    assert "confirm" not in properties
    assert properties["allow_hard"] == {"type": "boolean"}
    assert properties["allow_protected"] == {"type": "boolean"}
    assert sorted(tool.parameters["required"]) == ["date", "duration_minutes"]


def test_schedule_workout_override_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "schedule_workout_override")
    assert "ONLY after ``schedule_workout`` refused" in tool.description
    assert "runs as a preview" in tool.description
    assert "``event_id`` and ``external_id`` are internal plumbing" in tool.description


def test_an_override_confirmed_by_the_model_writes_nothing(monkeypatch):
    # Refused as a hard extra, the model may re-call with the override and
    # confirm=true in the same turn; the dispatcher still runs it as a
    # preview — only the athlete's Apply card books it.
    preview = {"status": "preview", "date": "2026-06-01", "workout": {}}
    tool, spy = _override_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(
        run_tool(
            [tool],
            "schedule_workout_override",
            {**_OVERRIDE_ARGS, "confirm": True},
        )
    )
    assert result["status"] == "preview"
    assert spy.call_args_list
    assert all(call.kwargs["preview"] is True for call in spy.call_args_list)


def test_an_override_books_when_the_athlete_applies_its_card(monkeypatch):
    preview = {"status": "preview", "date": "2026-06-01", "workout": {}}
    tool, spy = _override_tool(monkeypatch, preview, repos=MagicMock())
    run_tool([tool], "schedule_workout_override", dict(_OVERRIDE_ARGS))
    assert spy.call_args.kwargs["preview"] is True

    # The apply path is the one place confirm=True is ever passed.
    spy.return_value = {"status": "ok", "date": "2026-06-01", "workout": {}}
    result = json.loads(tool.invoke({**_OVERRIDE_ARGS, "confirm": True}))
    assert result["status"] == "ok"
    kwargs = spy.call_args.kwargs
    assert kwargs["preview"] is False
    assert kwargs["allow_hard"] is True
    assert kwargs["allow_protected"] is False
    assert kwargs["date_str"] == "2026-06-01"


def test_schedule_workout_override_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_schedule_workout",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "schedule_workout_override")
    result = json.loads(tool.invoke(dict(_OVERRIDE_ARGS)))
    assert "error" in result
    assert "boom" not in json.dumps(result)


def _unschedule_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_unschedule_workout", spy
    )
    return _get(_tools(**tool_args), "unschedule_workout"), spy


def test_unschedule_workout_is_registered():
    tool = _get(_tools(MagicMock()), "unschedule_workout")
    assert tool.name == "unschedule_workout"


def test_unschedule_workout_schema_requires_only_date():
    tool = _get(_tools(MagicMock()), "unschedule_workout")
    properties = tool.parameters["properties"]
    assert properties["date"] == {"type": "string"}
    assert properties["through_date"] == {"type": "string"}
    assert "confirm" not in properties
    assert tool.parameters["required"] == ["date"]


def test_unschedule_workout_docstring_states_the_contract():
    tool = _get(_tools(MagicMock()), "unschedule_workout")
    assert "Runs as a preview" in tool.description
    assert "needs no active plan" in tool.description
    assert "skip_planned_session" in tool.description
    assert "pause_training" in tool.description


def test_unschedule_workout_preview_is_relayed_verbatim(monkeypatch):
    preview = {"status": "preview", "confirm_required": True, "workouts": []}
    tool, spy = _unschedule_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(tool.invoke({"date": "2026-06-01"}))
    assert result == preview
    kwargs = spy.call_args.kwargs
    assert kwargs["date_str"] == "2026-06-01"
    assert kwargs["through_str"] is None
    assert kwargs["confirm"] is False


def test_unschedule_workout_confirm_binds_the_injected_dependencies(monkeypatch):
    client = MagicMock()
    tool, spy = _unschedule_tool(
        monkeypatch, {"status": "ok"}, repos=MagicMock(), intervals_client=client
    )
    result = json.loads(
        tool.invoke(
            {
                "date": "2026-06-01",
                "through_date": "2026-06-03",
                "confirm": True,
            }
        )
    )
    assert result["status"] == "ok"
    kwargs = spy.call_args.kwargs
    assert kwargs["client"] is client
    assert kwargs["through_str"] == "2026-06-03"
    assert kwargs["confirm"] is True


def test_unschedule_workout_relays_a_not_found(monkeypatch):
    not_found = {"status": "not_found", "message": "No coach-scheduled workout."}
    tool, _ = _unschedule_tool(monkeypatch, not_found, repos=MagicMock())
    result = json.loads(tool.invoke({"date": "2026-06-01"}))
    assert result == not_found


def test_unschedule_workout_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_edit_tools.run_unschedule_workout",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(MagicMock()), "unschedule_workout")
    result = json.loads(tool.invoke({"date": "2026-06-01"}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# make_tools wiring


def test_make_tools_includes_plan_tools_when_repos_given():
    tools = make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
        repos=MagicMock(),
        intervals_client=MagicMock(),
        maybe_get_coach=lambda: None,
    )
    names = {t.name for t in tools}
    assert "get_planned_sessions" in names
    assert "set_session_status" in names
    assert "move_planned_session" in names
    assert "replan_active_plan" in names
    assert "adjust_current_week" in names
    assert "swap_planned_session" in names
    assert "pause_training" in names
    assert "schedule_workout" in names
    assert "unschedule_workout" in names


def test_make_tools_defaults_maybe_get_coach_when_omitted():
    tools = make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
        repos=MagicMock(),
    )
    names = {t.name for t in tools}
    assert "get_planned_sessions" in names


def test_make_tools_omits_plan_tools_without_repos():
    tools = make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
    )
    names = {t.name for t in tools}
    assert "get_planned_sessions" not in names


def test_planned_sessions_queries_with_canonical_iso_dates():
    """date.fromisoformat accepts '20260805'; the SQL TEXT compare does not."""
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = []
    tool = _get(_tools(repo), "get_planned_sessions")
    tool.invoke({"start_date": "20260805", "end_date": "2026-W32-3"})
    repo.plan.get_sessions_by_date_range.assert_called_once_with(
        7, "2026-08-05", "2026-08-05"
    )


def test_session_vs_actual_queries_with_canonical_iso_date():
    repo = MagicMock()
    repo.plan.get_active.return_value = _plan_row()
    repo.plan.get_sessions_by_date_range.return_value = []
    tool = _get(_tools(repo), "get_session_vs_actual")
    result = json.loads(tool.invoke({"date": "20260805"}))
    repo.plan.get_sessions_by_date_range.assert_called_once_with(
        7, "2026-08-05", "2026-08-05"
    )
    assert "2026-08-05" in result["message"]
