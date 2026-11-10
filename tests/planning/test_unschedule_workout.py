"""Tests for the ad-hoc calendar removal (unschedule_workout)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from endury.intervals.push import race_external_id, session_external_id
from endury.planner.foundations.types import GoalType, RaceGoal
from endury.planning import unschedule_workout as mod
from endury.planning.unschedule_workout import (
    pause_without_plan,
    remove_ad_hoc_workouts,
    unschedule_workout,
)

_TODAY = date(2026, 5, 20)
_FUTURE = "2026-05-25"
_FUTURE_2 = "2026-05-27"
_PAST = "2026-05-01"
_AD_HOC = "coachie:adhoc:2026-05-25:72b33a4d"
# Built by the real helpers, not hand-typed: the filter's whole job is to be
# blind to these two schemes, so the test must break if either one changes.
_PLAN_SESSION = session_external_id(7, "w05_run_0")
_RACE = race_external_id(
    7,
    RaceGoal(goal_type=GoalType.RUNNING_10K, race_date=date(2026, 6, 14), priority="A"),
)


def _event(
    external_id: str, *, day: str = _FUTURE, name: str = "Easy Run", event_id: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(
        external_id=external_id,
        start_date_local=f"{day}T00:00:00",
        name=name,
        id=event_id,
    )


def _unschedule(client=None, **kwargs) -> dict:
    kwargs.setdefault("date_str", _FUTURE)
    kwargs.setdefault("through_str", None)
    kwargs.setdefault("confirm", False)
    return unschedule_workout(client=client, today=_TODAY, **kwargs)


class TestDateValidation:
    def test_an_unparseable_date_is_refused(self):
        result = _unschedule(date_str="not-a-date")
        assert result["status"] == "error"
        assert "not-a-date" in result["message"]

    def test_an_unparseable_through_date_is_refused(self):
        result = _unschedule(through_str="not-a-date")
        assert result["status"] == "error"
        assert "not-a-date" in result["message"]

    def test_a_past_date_is_refused(self):
        result = _unschedule(date_str=_PAST)
        assert result["status"] == "error"
        assert "passed" in result["message"]

    def test_todays_date_is_still_reachable(self):
        client = MagicMock()
        client.list_events.return_value = []
        result = _unschedule(client=client, date_str=_TODAY.isoformat())
        assert result["status"] == "not_found"

    def test_through_before_date_is_refused(self):
        result = _unschedule(date_str=_FUTURE_2, through_str=_FUTURE)
        assert result["status"] == "error"
        assert "before" in result["message"]

    def test_through_equal_to_date_is_fine(self):
        client = MagicMock()
        client.list_events.return_value = []
        result = _unschedule(client=client, through_str=_FUTURE)
        assert result["status"] == "not_found"


class TestNoClient:
    def test_no_client_is_a_refusal(self):
        result = _unschedule(client=None)
        assert result["status"] == "error"
        assert "not configured" in result["message"]


class TestNotFound:
    def test_no_events_at_all(self):
        client = MagicMock()
        client.list_events.return_value = []
        result = _unschedule(client=client)
        assert result["status"] == "not_found"
        assert _FUTURE in result["message"]

    def test_only_non_ad_hoc_events_are_ignored(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event(_PLAN_SESSION),
            _event(_RACE),
            _event(""),
        ]
        result = _unschedule(client=client)
        assert result["status"] == "not_found"


class TestFiltering:
    def test_list_events_is_called_with_the_window_and_workout_category(self):
        client = MagicMock()
        client.list_events.return_value = []
        _unschedule(client=client, through_str=_FUTURE_2)
        client.list_events.assert_called_once_with(
            date.fromisoformat(_FUTURE),
            date.fromisoformat(_FUTURE_2),
            category="WORKOUT",
        )

    def test_only_ad_hoc_events_are_kept_among_a_mixed_list(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event(_PLAN_SESSION),
            _event(_AD_HOC, event_id=42),
            _event(_RACE),
        ]
        result = _unschedule(client=client)
        assert result["status"] == "preview"
        assert result["workouts"] == [
            {
                "date": _FUTURE,
                "name": "Easy Run",
                "external_id": _AD_HOC,
                "event_id": 42,
            }
        ]


class TestPreview:
    def test_preview_writes_nothing(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event("coachie:adhoc:2026-05-25:aaa", event_id=1)
        ]
        result = _unschedule(client=client)
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert len(result["workouts"]) == 1
        client.bulk_delete_events.assert_not_called()


class TestListEventsFailure:
    def test_a_failed_read_reports_the_error(self):
        client = MagicMock()
        client.list_events.side_effect = RuntimeError("502")
        result = _unschedule(client=client)
        assert result["status"] == "error"
        assert "502" in result["message"]


class TestConfirm:
    def test_confirm_deletes_every_matched_workout(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event("coachie:adhoc:2026-05-25:aaa", event_id=1),
            _event("coachie:adhoc:2026-05-27:bbb", day=_FUTURE_2, event_id=2),
        ]
        client.bulk_delete_events.return_value = 2
        result = _unschedule(client=client, through_str=_FUTURE_2, confirm=True)
        assert result["status"] == "ok"
        assert result["confirm_required"] is False
        assert result["deleted"] == 2
        assert len(result["workouts"]) == 2
        client.bulk_delete_events.assert_called_once_with(
            ["coachie:adhoc:2026-05-25:aaa", "coachie:adhoc:2026-05-27:bbb"]
        )

    def test_confirm_never_deletes_a_plan_session_or_a_race(self):
        """The filter has to bound what is DELETED, not just what the preview
        shows: a plan session sharing the window is not collateral."""
        client = MagicMock()
        client.list_events.return_value = [
            _event(_PLAN_SESSION),
            _event(_AD_HOC, event_id=42),
            _event(_RACE),
        ]
        client.bulk_delete_events.return_value = 1
        result = _unschedule(client=client, confirm=True)
        assert result["status"] == "ok"
        assert result["deleted"] == 1
        client.bulk_delete_events.assert_called_once_with([_AD_HOC])

    def test_confirm_with_nothing_matching_is_not_found(self):
        """A confirm that finds nothing never reports a removal."""
        client = MagicMock()
        client.list_events.return_value = [_event(_PLAN_SESSION)]
        result = _unschedule(client=client, confirm=True)
        assert result["status"] == "not_found"
        assert _FUTURE in result["message"]
        client.bulk_delete_events.assert_not_called()

    def test_a_partial_delete_is_reported_as_an_error(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event("coachie:adhoc:2026-05-25:aaa", event_id=1),
            _event("coachie:adhoc:2026-05-25:bbb", event_id=2),
        ]
        client.bulk_delete_events.return_value = 1
        result = _unschedule(client=client, confirm=True)
        assert result["status"] == "error"
        assert "1 of 2" in result["message"]

    def test_a_failed_delete_reports_the_error(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event("coachie:adhoc:2026-05-25:aaa", event_id=1)
        ]
        client.bulk_delete_events.side_effect = RuntimeError("boom")
        result = _unschedule(client=client, confirm=True)
        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestPauseWithoutPlan:
    def test_remove_ad_hoc_workouts_helper_is_the_shared_path(self, monkeypatch):
        """pause_without_plan's confirm branch removes through the very same
        helper unschedule_workout's own confirm delete would use — one
        deletion path, not two forks of the same logic."""
        calls: list[dict] = []
        original = mod.remove_ad_hoc_workouts

        def spy(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(mod, "remove_ad_hoc_workouts", spy)
        client = MagicMock()
        client.list_events.return_value = [_event(_AD_HOC, day=_FUTURE, event_id=42)]
        client.bulk_delete_events.return_value = 1

        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=True,
            client=client,
            today=_TODAY,
        )
        unscheduled = _unschedule(client=client, confirm=True)

        assert result["status"] == "ok"
        assert result["deleted"] == 1
        assert unscheduled["status"] == "ok"
        assert calls == [
            {"client": client, "day": _TODAY, "through": date.fromisoformat(_FUTURE)},
            {
                "client": client,
                "day": date.fromisoformat(_FUTURE),
                "through": date.fromisoformat(_FUTURE),
            },
        ]

    def test_preview_lists_workouts_without_deleting(self):
        client = MagicMock()
        client.list_events.return_value = [_event(_AD_HOC, day=_FUTURE, event_id=42)]

        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=False,
            client=client,
            today=_TODAY,
        )

        assert result["status"] == "preview"
        assert result["no_plan"] is True
        assert len(result["workouts"]) == 1
        client.bulk_delete_events.assert_not_called()

    def test_nothing_found_is_reported_as_such_not_ok(self):
        client = MagicMock()
        client.list_events.return_value = []

        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=True,
            client=client,
            today=_TODAY,
        )

        assert result["status"] == "not_found"
        client.bulk_delete_events.assert_not_called()

    def test_a_past_through_date_is_refused(self):
        result = pause_without_plan(
            through=date.fromisoformat(_PAST),
            confirm=False,
            client=MagicMock(),
            today=_TODAY,
        )
        assert result["status"] == "error"
        assert "nothing to pause" in result["message"]

    def test_no_client_is_a_refusal(self):
        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=False,
            client=None,
            today=_TODAY,
        )
        assert result["status"] == "error"
        assert "not configured" in result["message"]

    def test_preview_reports_a_failed_calendar_read(self):
        client = MagicMock()
        client.list_events.side_effect = RuntimeError("502")

        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=False,
            client=client,
            today=_TODAY,
        )

        assert result["status"] == "error"
        assert "502" in result["message"]

    def test_confirm_relays_a_failed_removal(self):
        client = MagicMock()
        client.list_events.return_value = [_event(_AD_HOC, day=_FUTURE, event_id=42)]
        client.bulk_delete_events.side_effect = RuntimeError("boom")

        result = pause_without_plan(
            through=date.fromisoformat(_FUTURE),
            confirm=True,
            client=client,
            today=_TODAY,
        )

        assert result["status"] == "error"
        assert "boom" in result["message"]


class TestRemoveAdHocWorkouts:
    def test_no_client_is_an_error(self):
        result = remove_ad_hoc_workouts(
            client=None,
            day=date.fromisoformat(_FUTURE),
            through=date.fromisoformat(_FUTURE),
        )

        assert result["status"] == "error"
        assert "not configured" in result["message"]
        assert result["deleted"] == 0
        assert result["workouts"] == []

    def test_a_failed_calendar_read_is_an_error(self):
        client = MagicMock()
        client.list_events.side_effect = RuntimeError("502")

        result = remove_ad_hoc_workouts(
            client=client,
            day=date.fromisoformat(_FUTURE),
            through=date.fromisoformat(_FUTURE),
        )

        assert result["status"] == "error"
        assert "502" in result["message"]
        assert result["deleted"] == 0
        assert result["workouts"] == []

    def test_a_partial_delete_is_an_error(self):
        client = MagicMock()
        client.list_events.return_value = [
            _event("coachie:adhoc:2026-05-25:aaa", event_id=1),
            _event("coachie:adhoc:2026-05-25:bbb", event_id=2),
        ]
        client.bulk_delete_events.return_value = 1

        result = remove_ad_hoc_workouts(
            client=client,
            day=date.fromisoformat(_FUTURE),
            through=date.fromisoformat(_FUTURE),
        )

        assert result["status"] == "error"
        assert "1 of 2" in result["message"]
        assert result["deleted"] == 1
        assert len(result["workouts"]) == 2
