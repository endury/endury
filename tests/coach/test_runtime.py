"""Tests for CoachRuntime state loading, LLM resolution, and helpers."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from endury.coach.runtime import CoachRuntime
from endury.domain.models.athlete import AthleteProfile
from endury.wiring.config import CoachConfig
from tests.coach.conftest import StubIntervalsClient, make_activity, make_wellness

pytestmark = pytest.mark.coach


def _repos(intervals_client=None):
    repos = MagicMock()
    for name in ("athlete", "history", "memory", "plan"):
        setattr(repos, name, MagicMock())
    # The container raises when no API key is configured; tests opt in.
    if intervals_client is None:
        type(repos).intervals_client = PropertyMock(side_effect=ValueError("no key"))
    else:
        repos.intervals_client = intervals_client
    return repos


def _runtime(repos=None, llm="fake-llm", intervals_client=None):
    repos = repos or _repos()
    with patch("endury.coach.runtime.make_tools", return_value=["tool"]):
        return CoachRuntime(
            config=CoachConfig(),
            llm=llm,
            repos=repos,
            intervals_client=intervals_client,
        )


# Properties + LLM resolution


def test_properties_expose_repos_and_client():
    repos = _repos()
    client = StubIntervalsClient()
    runtime = _runtime(repos, intervals_client=client)
    assert runtime.repos is repos
    assert runtime.athlete_repo is repos.athlete
    assert runtime.memory_repo is repos.memory
    assert runtime.intervals_client is client
    assert runtime.tools == ["tool"]


def test_intervals_client_falls_back_to_the_container():
    client = StubIntervalsClient()
    runtime = _runtime(_repos(intervals_client=client))
    assert runtime.intervals_client is client


def test_intervals_client_is_none_when_not_configured():
    """An unconfigured container must not break the coach — just no tools."""
    assert _runtime().intervals_client is None


def test_make_tools_receives_the_intervals_client():
    client = StubIntervalsClient()
    with patch("endury.coach.runtime.make_tools", return_value=[]) as factory:
        CoachRuntime(
            config=CoachConfig(),
            llm="fake",
            repos=_repos(),
            intervals_client=client,
        )
    assert factory.call_args.kwargs["intervals_client"] is client


def test_coach_llm_uses_injected_llm():
    runtime = _runtime(llm="the-llm")
    assert runtime.coach_llm() == "the-llm"


def test_coach_llm_builds_real_client_when_not_injected():
    runtime = _runtime(llm=None)
    with patch("endury.coach.runtime.create_llm", return_value="client") as factory:
        assert runtime.coach_llm() == "client"
    factory.assert_called_once()


def test_create_session_returns_session_bound_to_runtime():
    runtime = _runtime()
    session = runtime.create_session()
    assert session.runtime is runtime


def test_maybe_get_coach_none_before_session_created():
    runtime = _runtime()
    assert runtime.maybe_get_coach() is None


def test_maybe_get_coach_returns_created_session():
    runtime = _runtime()
    session = runtime.create_session()
    assert runtime.maybe_get_coach() is session


# run_initializer


def test_run_initializer_recovers_from_profile_load_failure():
    repos = _repos()
    repos.athlete.get.side_effect = RuntimeError("profile unavailable")
    runtime = _runtime(repos)
    runtime.run_initializer()
    assert runtime.athlete_profile is None


def test_run_initializer_stamps_the_session_timestamp():
    profile = AthleteProfile(name="Sam", last_session_at="2026-01-01T08:00:00")
    repos = _repos()
    repos.athlete.get.return_value = profile
    runtime = _runtime(repos)
    runtime.run_initializer()
    repos.athlete.update_last_session_at.assert_called_once()
    assert runtime.athlete_profile.last_session_at != "2026-01-01T08:00:00"


def test_run_initializer_can_skip_the_session_stamp():
    repos = _repos()
    repos.athlete.get.return_value = AthleteProfile(name="Sam")
    with patch("endury.coach.runtime.make_tools", return_value=["tool"]):
        runtime = CoachRuntime(
            config=CoachConfig(), llm="fake-llm", repos=repos, stamp_last_session=False
        )
    runtime.run_initializer()
    repos.athlete.update_last_session_at.assert_not_called()


def test_run_initializer_swallows_timestamp_failures():
    profile = AthleteProfile(name="Sam")
    repos = _repos()
    repos.athlete.get.return_value = profile
    repos.athlete.update_last_session_at.side_effect = RuntimeError("write failed")
    runtime = _runtime(repos)
    runtime.run_initializer()
    assert runtime.athlete_profile is profile


# Context helpers + profile mutation


def test_enrich_context_summarizes_recent_wellness():
    client = StubIntervalsClient(
        wellness=[make_wellness("2026-01-08", sleep_secs=28800, hrv=60.0)]
    )
    runtime = _runtime(intervals_client=client)
    with patch("endury.common.clock.today", return_value=date(2026, 1, 8)):
        runtime.enrich_context()
    assert "sleep 8.0h/night" in runtime.recent_health_summary


def test_enrich_context_reads_todays_form():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-07", "Ride", activity_id="r1", load=80.0)]
    )
    repos = _repos()
    repos.plan.get_active.return_value = None
    runtime = _runtime(repos, intervals_client=client)
    with patch("endury.common.clock.today", return_value=date(2026, 1, 8)):
        runtime.enrich_context()
    assert runtime.form_summary.startswith(
        "As of Thursday 2026-01-08, from completed sessions: fitness "
    )


def test_enrich_context_summarizes_the_latest_session():
    client = StubIntervalsClient(
        activities=[make_activity("2026-01-07", "Ride", activity_id="r1", load=80.0)]
    )
    repos = _repos()
    repos.plan.get_active.return_value = None
    runtime = _runtime(repos, intervals_client=client)
    with patch("endury.common.clock.today", return_value=date(2026, 1, 8)):
        runtime.enrich_context()
    assert runtime.latest_session_summary.startswith(
        "2026-01-07 (Wed) 07:00 Ride: 60.0 min"
    )
    assert "get_activity_detail(activity_id='r1')" in runtime.latest_session_summary


def test_enrich_context_loads_scheduled_workouts():
    from types import SimpleNamespace

    client = MagicMock()
    client.list_wellness.return_value = []
    client.list_activities.return_value = []
    client.list_events.return_value = [
        SimpleNamespace(
            external_id="coachie:adhoc:2026-01-08:aaa",
            start_date_local="2026-01-08T00:00:00",
            name="Shakeout",
            moving_time=1800,
            icu_training_load=40.0,
        )
    ]
    repos = _repos()
    repos.plan.get_active.return_value = None
    runtime = _runtime(repos, intervals_client=client)
    with patch("endury.common.clock.today", return_value=date(2026, 1, 8)):
        runtime.enrich_context()
    assert (
        runtime.scheduled_workouts_summary
        == "2026-01-08 (Thu) Shakeout — 30 min — load 40"
    )


def test_enrich_context_without_a_client_leaves_the_summary_empty():
    runtime = _runtime()
    runtime.enrich_context()
    assert runtime.recent_health_summary is None
    assert runtime.form_summary is None
    assert runtime.latest_session_summary is None
    assert runtime.scheduled_workouts_summary is None


def test_enrich_context_survives_an_unreachable_api():
    client = StubIntervalsClient(wellness_error=RuntimeError("network down"))
    runtime = _runtime(intervals_client=client)
    with patch("endury.common.clock.today", return_value=date(2026, 1, 8)):
        runtime.enrich_context()
    assert runtime.recent_health_summary is None


def test_profile_helpers():
    repos = _repos()
    profile = AthleteProfile(name="Sam", language="en")
    repos.athlete.get.return_value = profile
    runtime = _runtime(repos)

    assert runtime.refresh_athlete_profile() == profile
    assert runtime.athlete_profile == profile

    runtime.set_athlete_profile(None)
    runtime.update_language("de")  # no-op when profile is None
    assert runtime.athlete_profile is None

    runtime.set_athlete_profile(profile)
    runtime.update_language("de")
    assert runtime.athlete_profile.language == "de"


# load_plan_context — rebuilds the commit-time plan context on restart


def _active_plan():
    import json

    from endury.planning.generate_plan import SOLVER_VERSION

    plan = MagicMock()
    plan.solver_version = SOLVER_VERSION
    plan.name = "Test Plan"
    plan.goal_type = "running_marathon"
    plan.goal_date = "2026-06-01"
    plan.solve_result_json = json.dumps(
        {
            "sessions": [{"id": 1}],
            "coach_brief": "A steady marathon build.",
            "weekly_summary": [
                {
                    "week_index": 0,
                    "phase": "BASE",
                    "total_tss": 250.0,
                    "session_count": 5,
                }
            ],
        }
    )
    return plan


def test_load_plan_context_rebuilds_the_full_commit_time_summary():
    repos = _repos()
    repos.plan.get_active.return_value = _active_plan()
    macro = MagicMock(
        goal_type="running_marathon",
        total_weeks=16,
        ambition_level="moderate",
        coaching_brief="Build the long run.",
    )
    repos.plan.get_active_macro.return_value = macro
    runtime = _runtime(repos)

    runtime.load_plan_context()

    # active_plan_summary must carry the full commit-time context — coach_brief
    # and per-week lines — not a truncated stub.
    assert "Test Plan" in runtime.active_plan_summary
    assert "A steady marathon build." in runtime.active_plan_summary
    assert "- Week 1 (BASE): 250 load, 5 sessions" in runtime.active_plan_summary
    assert runtime.macro_plan_summary == (
        "Goal: running_marathon, 16 weeks total\n"
        "Ambition: moderate\n"
        "Brief: Build the long run."
    )


def test_load_plan_context_without_a_plan_leaves_the_summaries_empty():
    repos = _repos()
    repos.plan.get_active.return_value = None
    repos.plan.get_active_macro.return_value = None
    runtime = _runtime(repos)

    runtime.load_plan_context()

    assert runtime.active_plan_summary is None
    assert runtime.macro_plan_summary is None


def test_load_plan_context_swallows_each_halfs_failure():
    repos = _repos()
    repos.plan.get_active.side_effect = RuntimeError("db gone")
    repos.plan.get_active_macro.side_effect = RuntimeError("db gone")
    runtime = _runtime(repos)

    runtime.load_plan_context()  # must not raise

    assert runtime.active_plan_summary is None
    assert runtime.macro_plan_summary is None
