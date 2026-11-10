"""Tests for the ad-hoc calendar push (schedule_workout)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.intervals.workout_dsl import SportThresholds
from endury.planner.foundations.types import SportType
from endury.planning import schedule_workout as mod
from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE
from endury.planning.schedule_workout import schedule_workout
from endury.training.load import LoadMetricContext

_TODAY = date(2026, 5, 20)
_FUTURE = "2026-05-25"
_PAST = "2026-05-01"

RESOLVER = LoadMetricContext(
    running_threshold_pace_min_per_km=4.5,
    running_lthr_bpm=170,
    cycling_ftp_watts=250.0,
    cycling_lthr_bpm=160,
    swim_css_sec_per_100m=95.0,
    threshold_updated_at=date(2026, 1, 1),
)


@pytest.fixture(autouse=True)
def _resolver(monkeypatch) -> None:
    """Live thresholds are read from intervals.icu; stub them offline."""
    monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: RESOLVER)


def _repos(*, plan: SimpleNamespace | None = None) -> MagicMock:
    """A repos double with no active plan, or one that does not cover _FUTURE.

    Either way this module's path is the standalone one: a plan covering the
    date makes the workout a coach extra instead (see
    ``test_plan_extra_session.py``).
    """
    repos = MagicMock()
    repos.plan.get_active.return_value = plan
    repos.plan.get_sessions_by_date_range.return_value = []
    return repos


def _client() -> MagicMock:
    """An intervals.icu client double whose push lands."""
    client = MagicMock()
    client.bulk_upsert_events.return_value = [SimpleNamespace(id=999)]
    return client


def _schedule(repos, client=None, **kwargs) -> dict:
    kwargs.setdefault("duration_minutes", 45)
    kwargs.setdefault("date_str", _FUTURE)
    kwargs.setdefault("sport", "running")
    kwargs.setdefault("intent", "threshold")
    return schedule_workout(repos=repos, client=client, today=_TODAY, **kwargs)


class TestBooks:
    """Scheduling books and pushes in a single call, with no confirm step."""

    def test_one_call_books_and_pushes_with_no_confirm_argument(self):
        client = _client()
        result = _schedule(_repos(), client=client)
        assert result["status"] == "ok"
        assert "confirm_required" not in result
        assert result["push"]["status"] == "ok"
        assert result["push"]["event_id"] == 999
        assert result["date"] == _FUTURE
        assert result["workout"]["activity_key"] == "run_threshold"
        assert result["workout"]["duration_minutes"] > 0
        assert result["workout"]["target_tss"] > 0
        assert result["workout"]["steps"]
        assert result["plan_collision"] is None
        # With no plan behind it there is no recovery week to warn about.
        assert result["caveats"] == []
        client.bulk_upsert_events.assert_called_once()

    def test_todays_date_is_still_schedulable(self):
        result = _schedule(_repos(), client=_client(), date_str=_TODAY.isoformat())
        assert result["status"] == "ok"


class TestPreview:
    """``preview=True`` is ``schedule_workout_override``'s first phase."""

    def test_a_preview_writes_nothing_and_pushes_nothing(self):
        client = _client()
        result = _schedule(_repos(), client=client, preview=True)
        assert result["status"] == "preview"
        assert result["push"] is None
        assert result["workout"]["activity_key"] == "run_threshold"
        client.bulk_upsert_events.assert_not_called()


class TestPastDate:
    def test_a_past_date_is_refused(self):
        result = _schedule(_repos(), date_str=_PAST)
        assert result["status"] == "error"
        assert "passed" in result["message"]


class TestDuration:
    def test_a_non_positive_duration_is_refused(self):
        result = _schedule(_repos(), duration_minutes=0)
        assert result["status"] == "error"
        assert "duration_minutes must be greater than 0" in result["message"]


class TestOutsideAnActivePlan:
    def test_a_date_past_the_plans_last_day_is_a_standalone_entry(self):
        plan = SimpleNamespace(
            id=7, plan_start="2026-03-09", goal_date="2026-05-24", status="active"
        )
        result = _schedule(_repos(plan=plan), client=_client())
        assert result["status"] == "ok"
        assert result["plan_collision"] is None
        assert "plan_row" not in result


class TestReplacementSelection:
    def test_an_explicit_activity_key_wins(self):
        result = _schedule(
            _repos(),
            client=_client(),
            sport=None,
            intent=None,
            activity_key="bike_vo2max",
        )
        assert result["workout"]["activity_key"] == "bike_vo2max"

    def test_an_unknown_activity_key_lists_the_catalog(self):
        result = _schedule(
            _repos(), sport=None, intent=None, activity_key="run_from_the_past"
        )
        assert result["status"] == "error"
        assert "run_from_the_past" in result["message"]
        assert result["options"]

    def test_no_sport_and_no_activity_key_is_refused(self):
        result = _schedule(_repos(), sport=None)
        assert result["status"] == "error"
        assert "Name a sport" in result["message"]

    def test_an_unknown_sport_lists_the_valid_ones(self):
        result = _schedule(_repos(), sport="rowing")
        assert result["status"] == "error"
        assert result["options"] == ["cycling", "running", "swimming"]

    def test_naming_no_intent_is_refused(self):
        result = _schedule(_repos(), intent=None)
        assert result["status"] == "error"
        assert "Name the workout" in result["message"]
        assert "threshold" in result["options"]

    def test_an_unknown_intent_lists_the_valid_ones(self):
        result = _schedule(_repos(), intent="sufferfest")
        assert result["status"] == "error"
        assert "sufferfest" in result["message"]
        assert "vo2" in result["options"]

    def test_an_intent_the_sport_cannot_serve_names_the_sports_that_can(self):
        result = _schedule(_repos(), sport="swimming", intent="brick")
        assert result["status"] == "error"
        assert "No 'brick' activities for sport 'swimming'" in result["message"]
        assert result["options"] == ["running"]


class TestPushedEvent:
    def test_the_event_is_a_tagged_ad_hoc_workout(self):
        client = _client()
        result = _schedule(_repos(), client=client)
        assert result["push"]["external_id"].startswith(f"coachie:adhoc:{_FUTURE}:")
        assert _FUTURE in result["message"]

        event = client.bulk_upsert_events.call_args.args[0][0]
        assert event["external_id"] == result["push"]["external_id"]
        assert event["category"] == "WORKOUT"
        assert event["type"] == "Run"
        assert event["tags"] == ["endury"]
        assert event["start_date_local"] == f"{_FUTURE}T00:00:00"

    def test_two_bookings_on_the_same_day_get_distinct_events(self):
        client = _client()
        first = _schedule(_repos(), client=client)
        second = _schedule(_repos(), client=client)
        assert first["push"]["external_id"] != second["push"]["external_id"]


class TestPushFailure:
    def test_a_failed_push_reports_the_error_and_claims_nothing(self):
        client = MagicMock()
        client.bulk_upsert_events.side_effect = RuntimeError("502")
        result = _schedule(_repos(), client=client)
        assert result["status"] == "error"
        assert result["push"]["status"] == "error"
        assert "502" in result["push"]["message"]
        assert result["push"]["message"] in result["caveats"]


class TestNoClient:
    def test_scheduling_without_a_client_is_a_refusal_not_a_false_success(self):
        result = _schedule(_repos(), client=None)
        assert result["status"] == "error"
        assert result["push"]["status"] == "skipped"
        assert result["push"]["message"] == PUSH_SKIPPED_MESSAGE
        assert "not scheduled" in result["message"]


class TestNoThresholds:
    def test_no_resolver_degrades_targets_and_is_caveated(self, monkeypatch):
        monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: None)
        result = _schedule(_repos(), client=_client())
        assert result["status"] == "ok"
        assert any("No athlete thresholds" in c for c in result["caveats"])


class TestCapabilityCaveat:
    """The picked activity's taper_ok/recovery_ok flags decide the caveat."""

    def test_a_key_long_session_outside_a_plan_is_not_caveated(self):
        result = _schedule(_repos(), client=_client(), sport="swimming", intent="long")
        assert result["workout"]["activity_key"] == "swim_long"
        assert not any("Long Swim" in c for c in result["caveats"])

    def test_an_easy_session_carries_no_capability_caveat(self):
        result = _schedule(_repos(), client=_client(), sport="swimming", intent="easy")
        assert result["workout"]["activity_key"] == "swim_endurance"
        assert not any("taper" in c for c in result["caveats"])


class TestSportThresholds:
    """Direct unit coverage of the resolver -> DSL threshold mapping."""

    def test_no_resolver_is_all_none(self):
        assert mod.sport_thresholds(SportType.RUNNING, None) == SportThresholds()

    def test_running_maps_pace_and_lthr(self):
        thresholds = mod.sport_thresholds(SportType.RUNNING, RESOLVER)
        assert thresholds.threshold_pace_sec_per_km == 270.0
        assert thresholds.lthr_bpm == 170

    def test_running_without_a_pace_leaves_it_unset(self):
        resolver = LoadMetricContext(running_lthr_bpm=170)
        thresholds = mod.sport_thresholds(SportType.RUNNING, resolver)
        assert thresholds.threshold_pace_sec_per_km is None

    def test_cycling_maps_ftp_and_lthr(self):
        thresholds = mod.sport_thresholds(SportType.CYCLING, RESOLVER)
        assert thresholds.ftp_watts == 250
        assert thresholds.lthr_bpm == 160

    def test_cycling_without_ftp_leaves_it_unset(self):
        resolver = LoadMetricContext(cycling_lthr_bpm=160)
        thresholds = mod.sport_thresholds(SportType.CYCLING, resolver)
        assert thresholds.ftp_watts is None

    def test_swimming_maps_css(self):
        thresholds = mod.sport_thresholds(SportType.SWIMMING, RESOLVER)
        assert thresholds.css_sec_per_100m == 95.0
