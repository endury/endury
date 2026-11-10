"""Tests for the training pause (pause_training)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.planning import adjust_week as adjust_week_mod
from endury.planning import pause_training as mod
from endury.planning import schedule_workout as schedule_workout_mod
from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE
from endury.planning.pause_training import pause_training, plan_pause
from endury.planning.schedule_workout import schedule_workout
from tests.planning.test_replan import (
    _TODAY,
    _AdjustablePlanRepo,
    _base_repos,
    _committed_plan,
)

_THROUGH = "2026-05-27"  # the Wednesday a week after _TODAY
_RESUME = "2026-05-28"
_FIRST_WEEK_END = "2026-06-03"

# Every pending session the fixture plan has in [_TODAY, _THROUGH].
_IN_WINDOW = (
    "2026-05-20",
    "2026-05-21",
    "2026-05-22",
    "2026-05-24",
    "2026-05-26",
    "2026-05-27",
)


class _PausablePlanRepo(_AdjustablePlanRepo):
    """Adjustable repo that also honours the bulk skip write."""

    def __init__(self, active: SimpleNamespace, macro: SimpleNamespace | None):
        super().__init__(active, macro)
        self.pushed_events: dict[str, str] = {}

    def bulk_update_session_matches(self, updates: list[dict]) -> int:
        by_id = {row.id: row for row in self.rows}
        for item in updates:
            row = by_id[item["session_id"]]
            row.matched_activity_id = item.get("matched_activity_id")
            row.status = item.get("status", "pending")
        return len(updates)

    def update_pushed_events(self, plan_id: int, events: dict[str, str]) -> None:
        self.pushed_events.update(events)

    def bulk_insert_sessions(self, plan_id: int, rows: list) -> None:
        """Materialize regenerated rows with their prescription columns."""
        self.inserted_rows = rows
        next_id = max((r.id for r in self.rows), default=0) + 1
        for offset, insert in enumerate(rows):
            self.rows.append(
                SimpleNamespace(
                    id=next_id + offset,
                    plan_id=plan_id,
                    slot_id=insert.slot_id,
                    date=insert.date,
                    week_index=insert.week_index,
                    phase_type=insert.phase_type,
                    sport=insert.sport,
                    activity_key=insert.activity_key,
                    family=insert.family,
                    target_tss=insert.target_tss,
                    achieved_tss=insert.achieved_tss,
                    duration_minutes=insert.duration_minutes,
                    duration_target_minutes=insert.duration_target_minutes,
                    workout_json=insert.workout_json,
                    tid_fractions_json=insert.tid_fractions_json,
                    matched_activity_id=None,
                    status="pending",
                    pushed_event_json=None,
                )
            )


@pytest.fixture(autouse=True)
def _offline_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture plan is built without live thresholds; match it."""
    monkeypatch.setattr(
        adjust_week_mod, "get_planner_load_context", lambda _repos: None
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.list_events.return_value = []
    client.bulk_delete_events.return_value = 6
    return client


def _harness(**plan_kwargs: object) -> tuple[MagicMock, _PausablePlanRepo]:
    active, macro, _ = _committed_plan()
    for key, value in plan_kwargs.items():
        setattr(active, key, value)
    repos = _base_repos()
    plan_repo = _PausablePlanRepo(active, macro)
    repos.plan = plan_repo
    return repos, plan_repo


def _pause(
    repos: MagicMock,
    *,
    through: str = _THROUGH,
    confirm: bool = True,
    client: MagicMock | None = None,
    today: date = _TODAY,
    **kwargs: object,
) -> dict:
    return pause_training(
        repos=repos,
        through_date=through,
        confirm=confirm,
        client=client,
        today=today,
        **kwargs,
    )


def _dates(rows: list[SimpleNamespace], status: str) -> list[str]:
    return sorted(str(row.date) for row in rows if str(row.status) == status)


# The confirm gate


class TestPreview:
    def test_a_preview_writes_nothing_and_lists_the_sessions(self) -> None:
        repos, plan_repo = _harness()
        client = _client()

        result = _pause(repos, confirm=False, client=client)

        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["window"] == {"from": str(_TODAY), "through": _THROUGH}
        assert result["resume_date"] == _RESUME
        assert [entry["date"] for entry in result["sessions"]] == list(_IN_WINDOW)
        assert all(entry["title"] for entry in result["sessions"])
        assert result["push"] is None
        assert result["replan"] is None
        assert result["first_week"] is None
        assert _dates(plan_repo.rows, "skipped") == []
        assert plan_repo.deleted_from is None
        client.bulk_delete_events.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_the_scope_caveat_names_every_effect(self) -> None:
        repos, _ = _harness()
        result = _pause(repos, confirm=False, client=_client())
        scope = result["caveats"][0]
        assert "6 session(s)" in scope
        assert str(_TODAY) in scope
        assert _THROUGH in scope
        assert "intervals.icu" in scope
        assert f"{_RESUME} (Thursday)" in scope
        assert "40%" in scope


# The applied pause


class TestApply:
    def test_it_skips_deletes_and_replans_from_the_resume_day(self) -> None:
        repos, plan_repo = _harness()
        client = _client()
        survivors = {row.slot_id for row in plan_repo.rows if str(row.date) < _RESUME}
        slots = [row.slot_id for row in plan_repo.rows if str(row.date) in _IN_WINDOW]

        result = _pause(repos, client=client)

        assert result["status"] == "ok"
        assert result["confirm_required"] is False
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)
        assert client.bulk_delete_events.call_args_list[0].args[0] == [
            f"coachie:1:{slot}" for slot in slots
        ]
        assert result["push"] == {
            "status": "ok",
            "errors": [],
            "message": None,
            "deleted": 6,
        }
        assert result["replan"]["status"] == "ok"
        assert result["replan"]["action"] == "replanned"
        assert plan_repo.deleted_from == _RESUME
        assert survivors <= {row.slot_id for row in plan_repo.rows}
        assert all(row.date >= _RESUME for row in plan_repo.inserted_rows)

    def test_the_first_week_back_is_trimmed_and_the_next_one_is_not(self) -> None:
        repos, plan_repo = _harness()

        result = _pause(repos, client=_client())

        first_week = result["first_week"]
        assert first_week["status"] == "ok"
        assert first_week["window"] == {"from": _RESUME, "through": _FIRST_WEEK_END}
        assert first_week["reduce_percent"] == 40
        assert (
            first_week["totals"]["after_minutes"]
            < (first_week["totals"]["before_minutes"])
        )
        trimmed = {entry["session_id"] for entry in first_week["sessions"]}
        assert trimmed
        for row in plan_repo.rows:
            if row.id not in trimmed:
                continue
            assert _RESUME <= str(row.date) <= _FIRST_WEEK_END
            intent = json.loads(row.workout_json)["coaching_intent"]
            assert "in your first week back" in intent

        later = [
            row
            for row in plan_repo.rows
            if str(row.date) > _FIRST_WEEK_END and row.id not in trimmed
        ]
        assert later
        assert not any(
            "in your first week back"
            in json.loads(row.workout_json).get("coaching_intent", "")
            for row in later
        )

    def test_a_pause_inside_the_current_week_replans_from_that_day(self) -> None:
        repos, plan_repo = _harness()

        result = _pause(repos, through="2026-05-22", client=_client())

        assert result["status"] == "ok"
        assert result["resume_date"] == "2026-05-23"
        assert plan_repo.deleted_from == "2026-05-23"
        assert any(
            "replan starts inside the current week" in caveat
            for caveat in result["caveats"]
        )

    def test_a_pause_past_this_week_carries_no_mid_week_caveat(self) -> None:
        repos, _ = _harness()
        result = _pause(repos, confirm=False, client=_client())
        assert not any(
            "inside the current week" in caveat for caveat in result["caveats"]
        )

    def test_the_coach_plan_summary_is_reloaded_after_the_trim(self) -> None:
        repos, _ = _harness()
        coach = SimpleNamespace(runtime=SimpleNamespace(active_plan_summary="stale"))

        _pause(repos, client=_client(), maybe_get_coach=lambda: coach)

        assert coach.runtime.active_plan_summary != "stale"

    def test_no_coach_accessor_is_fine(self) -> None:
        repos, _ = _harness()
        assert _pause(repos, client=_client())["status"] == "ok"


# What this verb refuses


class TestRefusals:
    def test_a_through_date_in_the_past_is_refused(self) -> None:
        repos, plan_repo = _harness()
        result = _pause(repos, through="2026-05-19")
        assert result["status"] == "error"
        assert "nothing to pause" in result["message"]
        assert _dates(plan_repo.rows, "skipped") == []

    def test_a_pause_that_swallows_race_day_is_refused(self) -> None:
        repos, _ = _harness()
        result = _pause(repos, through="2026-11-08")
        assert result["status"] == "error"
        assert "runs past the plan's last day" in result["message"]
        assert "start a new one" in result["message"]

    def test_an_unparseable_date_is_refused(self) -> None:
        repos, _ = _harness()
        result = _pause(repos, through="next wednesday")
        assert result["status"] == "error"
        assert "YYYY-MM-DD" in result["message"]

    def test_nothing_pending_in_the_window_reports_empty(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"

        result = _pause(repos, client=_client())

        assert result["status"] == "empty"
        assert "For a replan alone use replan_active_plan." in result["message"]
        assert result["sessions"] == []
        assert result["replan"] is None
        assert plan_repo.deleted_from is None


class TestUntouched:
    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"status": "completed"}, "already completed"),
            ({"matched_activity_id": "act-9"}, "already completed"),
            ({"status": "skipped"}, "already marked skipped"),
        ],
    )
    def test_each_reason_is_named(self, overrides: dict, reason: str) -> None:
        repos, plan_repo = _harness()
        row = next(r for r in plan_repo.rows if str(r.date) == "2026-05-21")
        for key, value in overrides.items():
            setattr(row, key, value)

        result = _pause(repos, confirm=False, client=_client())

        assert [note["session_id"] for note in result["untouched"]] == [row.id]
        assert result["untouched"][0]["reason"] == reason
        assert row.id not in {entry["session_id"] for entry in result["sessions"]}


# Remote failure paths — the local skips always stand


class TestRemoteFailures:
    def test_a_failed_calendar_delete_is_reported_and_the_skip_stands(self) -> None:
        repos, plan_repo = _harness()
        client = _client()
        client.bulk_delete_events.side_effect = RuntimeError("502")

        result = _pause(repos, client=client)

        assert result["status"] == "ok"
        assert result["push"]["status"] == "error"
        assert result["push"]["message"] in result["caveats"]
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)
        assert result["replan"]["status"] == "ok"

    def test_a_failed_replan_leaves_the_skips_in_place(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repos, plan_repo = _harness()
        monkeypatch.setattr(
            mod,
            "run_replan",
            MagicMock(side_effect=ValueError("The goal race has passed.")),
        )

        result = _pause(repos, client=_client())

        assert result["status"] == "ok"
        assert result["replan"] == {
            "status": "error",
            "message": "The goal race has passed.",
        }
        assert result["first_week"] is None
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)
        assert any(
            "call replan_active_plan once that is fixed" in caveat.lower()
            for caveat in result["caveats"]
        )

    def test_a_failed_replan_push_tells_the_athlete_to_sync(self) -> None:
        repos, _ = _harness()
        client = _client()
        client.bulk_upsert_events.side_effect = RuntimeError("502")

        result = _pause(repos, client=client)

        assert result["replan"]["push"]["status"] == "error"
        assert result["replan"]["push"]["message"] in result["caveats"]
        assert any("Sync now" in caveat for caveat in result["caveats"])

    def test_a_refused_first_week_trim_is_reported(self) -> None:
        # A plan built WITH thresholds, rebuilt without a live resolver: the
        # adjust verb refuses rather than strip the athlete's targets.
        repos, plan_repo = _harness(
            athlete_state_json=json.dumps(
                {"thresholds": {"running": {"lthr_bpm": 170}}, "sports": {}}
            )
        )

        result = _pause(repos, client=_client())

        assert result["status"] == "ok"
        assert result["replan"]["status"] == "ok"
        assert result["first_week"]["status"] == "error"
        assert "strip your pace and power targets" in result["first_week"]["message"]
        assert any("could not be trimmed" in caveat for caveat in result["caveats"])
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)

    def test_without_a_client_every_remote_block_is_skipped(self) -> None:
        repos, plan_repo = _harness()

        result = _pause(repos, client=None)

        assert result["status"] == "ok"
        assert result["push"] == {
            "status": "skipped",
            "errors": [],
            "message": PUSH_SKIPPED_MESSAGE,
            "deleted": 0,
        }
        assert result["replan"]["push"]["status"] == "skipped"
        assert result["first_week"]["push"]["status"] == "skipped"
        assert result["edited_workouts"] == []
        # An ad-hoc booking only ever lives on intervals.icu, so with no
        # client there is nothing to sweep and nothing to caveat about.
        assert result["ad_hoc"] is None
        assert not any("not configured" in caveat for caveat in result["caveats"])
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)


class TestEditedWorkouts:
    def test_only_drift_from_the_resume_day_onwards_is_reported(self) -> None:
        repos, plan_repo = _harness()
        inside = next(r for r in plan_repo.rows if str(r.date) == "2026-05-26")
        after = next(r for r in plan_repo.rows if str(r.date) == "2026-06-02")
        remote = []
        for row in (inside, after):
            external_id = f"coachie:1:{row.slot_id}"
            row.pushed_event_json = json.dumps(
                {
                    "external_id": external_id,
                    "start_date_local": f"{row.date}T00:00:00",
                    "name": "Planned",
                    "description": "as pushed",
                }
            )
            remote.append(
                SimpleNamespace(
                    external_id=external_id,
                    start_date_local=f"{row.date}T00:00:00",
                    name="Hand-edited",
                    description="as pushed",
                )
            )
        client = _client()
        client.list_events.return_value = remote

        result = _pause(repos, confirm=False, client=client)

        assert [entry["external_id"] for entry in result["edited_workouts"]] == [
            f"coachie:1:{after.slot_id}"
        ]
        assert any(
            "1 upcoming workout(s) were edited" in caveat
            for caveat in result["caveats"]
        )


# No active plan: pause_training delegates whole to pause_without_plan


def _booking(day: str, *, external_id: str | None = None, event_id: int = 9):
    return SimpleNamespace(
        external_id=external_id or f"coachie:adhoc:{day}:aaa",
        start_date_local=f"{day}T00:00:00",
        name="Easy Shakeout",
        id=event_id,
    )


class TestNoActivePlan:
    def test_pause_without_a_plan_previews_coach_scheduled_workouts(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.list_events.return_value = [_booking("2026-05-21")]

        result = _pause(repos, through="2026-05-24", confirm=False, client=client)

        assert result["status"] == "preview"
        assert result["no_plan"] is True
        assert result["confirm_required"] is True
        assert result["window"] == {"from": str(_TODAY), "through": "2026-05-24"}
        assert result["workouts"] == [
            {
                "date": "2026-05-21",
                "name": "Easy Shakeout",
                "external_id": "coachie:adhoc:2026-05-21:aaa",
                "event_id": 9,
            }
        ]
        assert result["replan"] is None
        assert result["first_week"] is None
        client.bulk_delete_events.assert_not_called()
        assert any("nothing was replanned" in c for c in result["caveats"])

    def test_pause_without_a_plan_confirm_removes_them(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.list_events.return_value = [_booking("2026-05-21")]
        client.bulk_delete_events.return_value = 1

        result = _pause(repos, through="2026-05-24", confirm=True, client=client)

        assert result["status"] == "ok"
        assert result["no_plan"] is True
        assert result["deleted"] == 1
        client.bulk_delete_events.assert_called_once_with(
            ["coachie:adhoc:2026-05-21:aaa"]
        )
        assert any("nothing was replanned" in c for c in result["caveats"])

    def test_pause_without_a_plan_reports_nothing_found(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.list_events.return_value = []

        result = _pause(repos, through="2026-05-24", confirm=True, client=client)

        assert result["status"] == "not_found"
        assert result["no_plan"] is True
        client.bulk_delete_events.assert_not_called()

    def test_pause_without_a_plan_without_client_errors(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None

        result = _pause(repos, through="2026-05-24", confirm=False, client=None)

        assert result["status"] == "error"
        assert "not configured" in result["message"]

    def test_pause_without_a_plan_says_nothing_was_replanned(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.list_events.return_value = []

        result = _pause(repos, through="2026-05-24", confirm=False, client=client)

        assert result["status"] == "not_found"
        assert "no active training plan" in result["message"].lower()
        assert "nothing was replanned" in result["message"].lower()
        assert "no plan sessions were skipped" in result["message"].lower()
        # Nothing to remove is the likeliest plan-less pause of all, and the
        # constraint still has to be recorded somewhere.
        assert any("update_memory" in c for c in result["caveats"])
        assert any("Next check-in" in c for c in result["caveats"])

    def test_pause_without_a_plan_refuses_a_past_through_date(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None

        result = _pause(repos, through="2026-05-19", confirm=False, client=None)

        assert result["status"] == "error"
        assert result["no_plan"] is True
        assert "nothing to pause" in result["message"]


# With an active plan: the pause also sweeps the coach's own ad-hoc bookings


def _client_with_workout_booking(booking_date: str) -> MagicMock:
    """A client whose WORKOUT-category reads return one ad-hoc booking.

    Filters on the requested window like the real API would, so a caller
    asking for a window that excludes ``booking_date`` gets nothing back —
    the fixture proves the window bounds actually matter, not just that a
    filter function exists.
    """
    client = MagicMock()
    booking = _booking(booking_date, external_id=f"coachie:adhoc:{booking_date}:bbb")

    def _list_events(oldest, newest, category=None):
        if (
            category == "WORKOUT"
            and oldest <= date.fromisoformat(booking_date) <= newest
        ):
            return [booking]
        return []

    client.list_events.side_effect = _list_events
    return client


class TestAdHocSweep:
    def test_pause_also_clears_coach_scheduled_workouts_after_today(self) -> None:
        repos, plan_repo = _harness()
        client = _client_with_workout_booking("2026-05-22")
        client.bulk_delete_events.side_effect = [6, 1]

        result = _pause(repos, client=client)

        assert result["status"] == "ok"
        assert result["ad_hoc"]["status"] == "ok"
        assert result["ad_hoc"]["deleted"] == 1
        assert result["ad_hoc"]["workouts"] == [
            {
                "date": "2026-05-22",
                "name": "Easy Shakeout",
                "external_id": "coachie:adhoc:2026-05-22:bbb",
                "event_id": 9,
            }
        ]
        assert client.bulk_delete_events.call_args_list[1].args[0] == [
            "coachie:adhoc:2026-05-22:bbb"
        ]
        assert any(
            "Also removed 1 coach-scheduled workout(s)" in c for c in result["caveats"]
        )
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)

    def test_pause_leaves_todays_ad_hoc_workout_alone(self) -> None:
        """The sweep's own window starts tomorrow, never today."""
        repos, _ = _harness()
        client = _client()
        client.bulk_delete_events.side_effect = [6]

        _pause(repos, client=client)

        workout_calls = [
            call
            for call in client.list_events.call_args_list
            if call.kwargs.get("category") == "WORKOUT"
        ]
        assert len(workout_calls) == 1
        assert workout_calls[0].args[0] == _TODAY + timedelta(days=1)
        assert workout_calls[0].args[1] == date.fromisoformat(_THROUGH)

    def test_pause_through_today_only_runs_no_ad_hoc_sweep(self) -> None:
        """A pause ending today leaves the sweep's window empty (``None``)."""
        repos, _ = _harness()
        client = _client()
        client.bulk_delete_events.side_effect = [1]

        result = _pause(repos, through=str(_TODAY), client=client)

        assert result["status"] == "ok"
        assert result["ad_hoc"] is None
        assert not any("Also removed" in c for c in result["caveats"])

    def test_pause_ad_hoc_removal_failure_is_reported_not_raised(self) -> None:
        repos, plan_repo = _harness()
        client = _client_with_workout_booking("2026-05-22")
        client.bulk_delete_events.side_effect = [6, RuntimeError("502")]

        result = _pause(repos, client=client)

        assert result["status"] == "ok"
        assert result["ad_hoc"]["status"] == "error"
        assert "502" in result["ad_hoc"]["message"]
        assert result["ad_hoc"]["message"] in result["caveats"]
        assert _dates(plan_repo.rows, "skipped") == list(_IN_WINDOW)


# A pause preview must NAME every ad-hoc booking confirm would remove —
# the athlete never agrees to a loss the preview did not already show.


class TestAdHocPreview:
    def test_preview_names_the_workouts_confirm_would_remove(self) -> None:
        repos, _ = _harness()
        client = _client_with_workout_booking("2026-05-22")

        result = _pause(repos, confirm=False, client=client)

        assert result["status"] == "preview"
        assert result["ad_hoc"] == {
            "status": "preview",
            "workouts": [
                {
                    "date": "2026-05-22",
                    "name": "Easy Shakeout",
                    "external_id": "coachie:adhoc:2026-05-22:bbb",
                    "event_id": 9,
                }
            ],
        }
        assert any(
            "Applying will also remove 1 coach-scheduled workout(s)" in c
            for c in result["caveats"]
        )
        client.bulk_delete_events.assert_not_called()

    def test_preview_without_a_booking_carries_no_extra_caveat(self) -> None:
        repos, _ = _harness()

        result = _pause(repos, confirm=False, client=_client())

        assert result["status"] == "preview"
        assert result["ad_hoc"] == {"status": "not_found", "workouts": []}
        assert not any("Applying will also remove" in c for c in result["caveats"])

    def test_preview_without_a_client_omits_the_ad_hoc_block(self) -> None:
        repos, _ = _harness()

        result = _pause(repos, confirm=False, client=None)

        assert result["status"] == "preview"
        assert result["ad_hoc"] is None

    def test_preview_through_today_only_runs_no_ad_hoc_preview(self) -> None:
        """The preview mirrors the applied sweep's window: tomorrow on."""
        repos, _ = _harness()

        result = _pause(repos, through=str(_TODAY), confirm=False, client=_client())

        assert result["ad_hoc"] is None

    def test_preview_ad_hoc_read_failure_is_reported_not_raised(self) -> None:
        repos, _ = _harness()
        client = _client()
        client.list_events.side_effect = RuntimeError("502")

        result = _pause(repos, confirm=False, client=client)

        assert result["status"] == "preview"
        assert result["ad_hoc"]["status"] == "error"
        assert "502" in result["ad_hoc"]["message"]
        assert result["ad_hoc"]["message"] in result["caveats"]


# An empty plan-side window still sweeps the coach's own bookings —
# "empty" is only true when neither a plan session nor a booking is there.


class TestEmptyButBooked:
    def test_preview_lists_booked_workouts_when_nothing_plan_side_is_pending(
        self,
    ) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")

        result = _pause(repos, confirm=False, client=client)

        assert result["status"] == "preview"
        assert result["sessions"] == []
        assert result["replan"] is None
        assert result["ad_hoc"]["status"] == "preview"
        assert not any("also" in c.lower() for c in result["caveats"])
        assert any(
            "Applying removes 1 coach-scheduled workout(s)" in c
            for c in result["caveats"]
        )
        client.bulk_delete_events.assert_not_called()

    def test_confirm_removes_booked_workouts_and_replans_nothing(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")
        client.bulk_delete_events.return_value = 1

        result = _pause(repos, confirm=True, client=client)

        assert result["status"] == "ok"
        assert result["ad_hoc"]["status"] == "ok"
        assert result["ad_hoc"]["deleted"] == 1
        assert result["replan"] is None
        assert result["push"] is None
        assert "nothing was replanned" in result["message"].lower()
        assert not any("also" in c.lower() for c in result["caveats"])
        assert any(
            "Removed 1 coach-scheduled workout(s)" in c for c in result["caveats"]
        )
        client.bulk_delete_events.assert_called_once_with(
            ["coachie:adhoc:2026-05-22:bbb"]
        )

    def test_truly_empty_window_preview_is_still_reported_empty(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"

        result = _pause(repos, confirm=False, client=_client())

        assert result["status"] == "empty"
        assert "For a replan alone use replan_active_plan." in result["message"]
        # A preview speaks in the conditional and writes nothing — only the
        # confirm records the window.
        assert "nothing would change" in result["message"]
        assert "recorded as unavailable" not in result["message"]
        stored = json.loads(plan_repo._active.solve_result_json)
        assert "directives" not in stored["request"]

    def test_truly_empty_window_confirm_is_still_reported_empty(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"

        result = _pause(repos, confirm=True, client=_client())

        assert result["status"] == "empty"
        assert result["ad_hoc"] == {"status": "not_found", "workouts": [], "deleted": 0}
        assert result["replan"] is None
        assert "recorded as unavailable" in result["message"]
        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            [str(_TODAY), _THROUGH]
        ]

    def test_a_booking_only_pause_never_claims_a_replan(self) -> None:
        """Nothing is skipped, so no caveat may promise a regenerated week."""
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")

        result = _pause(repos, through="2026-05-22", confirm=False, client=client)

        assert result["status"] == "preview"
        assert not any("replan" in caveat.lower() for caveat in result["caveats"])

    def test_a_failed_sweep_is_reported_as_an_error_not_an_empty_window(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")
        client.bulk_delete_events.side_effect = RuntimeError("502")

        result = _pause(repos, confirm=True, client=client)

        assert result["status"] == "error"
        assert "502" in result["message"]
        assert result["ad_hoc"]["status"] == "error"
        assert "nothing changed" not in result["message"]
        # The window recording is a local write, independent of the
        # best-effort remote sweep — it still happens on a failed sweep.
        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            [str(_TODAY), _THROUGH]
        ]


# The pure entry point


class TestPurePlanPause:
    def test_plan_pause_performs_no_writes(self) -> None:
        repos, plan_repo = _harness()

        pause = plan_pause(
            repos=repos,
            client=_client(),
            through=date.fromisoformat(_THROUGH),
            today=_TODAY,
        )

        assert pause.window == (_TODAY, date.fromisoformat(_THROUGH))
        assert pause.resume_date == date.fromisoformat(_RESUME)
        assert [item.date for item in pause.to_skip] == list(_IN_WINDOW)
        assert pause.to_skip[0].weekday == "Wed"
        assert pause.to_skip[0].sport == "running"
        assert pause.to_skip[0].minutes > 0
        assert _dates(plan_repo.rows, "skipped") == []

    def test_a_pause_through_today_still_skips_today(self) -> None:
        repos, _ = _harness()
        pause = plan_pause(
            repos=repos,
            client=None,
            through=_TODAY,
            today=_TODAY,
        )
        assert [item.date for item in pause.to_skip] == [str(_TODAY)]
        assert pause.resume_date == _TODAY + timedelta(days=1)


class TestUnavailableDirective:
    """A pause records its own window, so a later replan respects it too."""

    def test_pause_records_its_window_as_unavailable(self) -> None:
        repos, plan_repo = _harness()

        _pause(repos, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            [str(_TODAY), _THROUGH]
        ]

    def test_pause_merges_onto_an_existing_unavailable_window(self) -> None:
        _active, _macro, solve = _committed_plan()
        solve["request"]["directives"] = {"unavailable": [["2026-01-01", "2026-01-05"]]}
        repos, plan_repo = _harness(solve_result_json=json.dumps(solve))

        _pause(repos, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            ["2026-01-01", "2026-01-05"],
            [str(_TODAY), _THROUGH],
        ]

    def test_pause_coalesces_an_overlapping_existing_window(self) -> None:
        """Repeated/overlapping pauses must not grow the stored list."""
        _active, _macro, solve = _committed_plan()
        solve["request"]["directives"] = {"unavailable": [["2026-05-18", "2026-05-22"]]}
        repos, plan_repo = _harness(solve_result_json=json.dumps(solve))

        _pause(repos, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            ["2026-05-18", _THROUGH]
        ]

    def test_pause_coalesces_an_adjacent_existing_window(self) -> None:
        """A window ending the day before this one starts still merges."""
        _active, _macro, solve = _committed_plan()
        day_before = (_TODAY - timedelta(days=1)).isoformat()
        solve["request"]["directives"] = {"unavailable": [["2026-05-15", day_before]]}
        repos, plan_repo = _harness(solve_result_json=json.dumps(solve))

        _pause(repos, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            ["2026-05-15", _THROUGH]
        ]

    def test_a_preview_records_nothing(self) -> None:
        repos, plan_repo = _harness()

        _pause(repos, confirm=False, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert "directives" not in stored["request"]

    def test_a_confirmed_booking_only_pause_still_records_the_window(self) -> None:
        """Nothing plan-side pending must not skip recording the window —
        that is exactly when a later ad-hoc booking could sneak into it."""
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")
        client.bulk_delete_events.return_value = 1

        _pause(repos, confirm=True, client=client)

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            [str(_TODAY), _THROUGH]
        ]

    def test_a_confirmed_truly_empty_pause_still_records_the_window(self) -> None:
        """Even with nothing plan-side pending and no booking, the window
        the athlete just confirmed being out for must still be recorded."""
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"

        _pause(repos, confirm=True, client=_client())

        stored = json.loads(plan_repo._active.solve_result_json)
        assert stored["request"]["directives"]["unavailable"] == [
            [str(_TODAY), _THROUGH]
        ]

    def test_an_unconfirmed_booking_only_preview_records_nothing(self) -> None:
        repos, plan_repo = _harness()
        for row in plan_repo.rows:
            row.status = "skipped"
        client = _client_with_workout_booking("2026-05-22")

        _pause(repos, confirm=False, client=client)

        stored = json.loads(plan_repo._active.solve_result_json)
        assert "directives" not in stored["request"]


class TestCoachExtras:
    """An extra is a plan row, so a pause has to reach it like any other."""

    def test_pause_skips_a_coach_extra_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            schedule_workout_mod, "get_planner_load_context", lambda _repos: None
        )
        repos, plan_repo = _harness()
        scheduled = schedule_workout(
            repos=repos,
            duration_minutes=30,
            date_str="2026-05-22",
            sport="running",
            intent="easy",
            client=None,
            today=_TODAY,
        )
        assert scheduled["status"] == "ok"
        slot = scheduled["plan_row"]["slot_id"]

        result = _pause(repos, client=_client())

        assert result["status"] == "ok"
        assert slot in {item["slot_id"] for item in result["sessions"]}
        row = next(item for item in plan_repo.rows if item.slot_id == slot)
        assert row.status == "skipped"


# from_date: pause_training always starts today


class TestFromDate:
    """validate_from_date's own branches are unit-tested in
    tests/planning/test_pause_window.py; these confirm pause_training wires
    it in identically with and without an active plan, before anything else
    runs, and that a refused call writes nothing."""

    def test_omitted_behaves_exactly_like_today(self) -> None:
        repos, _ = _harness()
        with_from_date = _pause(repos, from_date=str(_TODAY), client=_client())
        repos2, _ = _harness()
        without_from_date = _pause(repos2, client=_client())
        assert with_from_date["status"] == without_from_date["status"] == "ok"
        assert with_from_date["resume_date"] == without_from_date["resume_date"]
        assert with_from_date["sessions"] == without_from_date["sessions"]

    def test_a_future_from_date_is_refused_and_writes_nothing(self) -> None:
        repos, plan_repo = _harness()
        result = _pause(repos, from_date="2026-05-27", client=_client())

        assert result["status"] == "refused"
        assert result["error_code"] == "future_window"
        assert result["suggested_tool"] in ("set_plan_directives", "set_session_status")
        assert _dates(plan_repo.rows, "skipped") == []
        assert plan_repo.deleted_from is None

    def test_a_future_from_date_later_this_week_suggests_skip_planned_session(
        self,
    ) -> None:
        repos, _ = _harness()
        # _TODAY is 2026-05-20 (Wed); 2026-05-24 is still this week's Sunday.
        result = _pause(repos, from_date="2026-05-24", client=_client())
        assert result["suggested_tool"] == "skip_planned_session"

    def test_a_future_from_date_next_week_suggests_set_plan_directives(self) -> None:
        repos, _ = _harness()
        result = _pause(repos, from_date="2026-05-25", client=_client())
        assert result["suggested_tool"] == "set_plan_directives"

    def test_a_past_from_date_is_an_error_and_writes_nothing(self) -> None:
        repos, plan_repo = _harness()
        result = _pause(repos, from_date="2026-05-19", client=_client())
        assert result["status"] == "error"
        assert _dates(plan_repo.rows, "skipped") == []

    def test_works_identically_with_no_active_plan(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.list_events.return_value = []

        result = _pause(
            repos, from_date="2026-05-25", through="2026-05-27", client=client
        )

        assert result["status"] == "refused"
        assert result["error_code"] == "future_window"
        assert result["suggested_tool"] == "set_plan_directives"
        client.bulk_delete_events.assert_not_called()

    def test_confirm_true_with_a_future_from_date_still_writes_nothing(self) -> None:
        # Even a "yes, do it" confirm must not sneak a later window through —
        # the guard runs before the confirm branch is ever reached.
        repos, plan_repo = _harness()
        result = _pause(repos, from_date="2026-05-25", confirm=True, client=_client())
        assert result["status"] == "refused"
        assert _dates(plan_repo.rows, "skipped") == []
        assert plan_repo.deleted_from is None
