"""Tests for the intervals.icu calendar push on commit / replan / delete."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date, timedelta
from functools import lru_cache
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.database.sqlite.repositories.self_assessment import SelfAssessmentRow
from endury.intervals.push import event_start
from endury.planner.foundations.types import SportType
from endury.planning.commit_plan import (
    PUSH_SKIPPED_MESSAGE,
    build_plan_events,
    delete_active_plan,
    push_active_plan,
    push_plan_events,
    thresholds_by_sport,
)
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.intervals_provider import (
    _build_client,
    get_intervals_client,
)
from endury.planning.replan_plan import replan_readback_diff, run_replan
from endury.planning.sync_status import READBACK_PAD_DAYS
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    RaceGoalModel,
)
from endury.wiring.config import CoachConfig

_START = "2026-03-09"  # a Monday
_RACE = "2026-08-09"  # a Sunday
_TODAY = date(2026, 5, 20)
_CUTOFF = "2026-05-25"  # next Monday
_PAD = timedelta(days=READBACK_PAD_DAYS)


class StubClient:
    """The slice of ``IntervalsClient`` the push flow uses."""

    def __init__(
        self,
        *,
        fail: bool = False,
        remote_events: list[object] | None = None,
    ) -> None:
        self.fail = fail
        self.remote_events = remote_events or []
        self.upserted: list[list[dict]] = []
        self.deleted: list[str] = []
        self.listed: list[tuple[date, date]] = []

    def bulk_upsert_events(self, events: list[dict]) -> list[object]:
        if self.fail:
            raise RuntimeError("upstream exploded")
        self.upserted.append(events)
        return []

    def bulk_delete_events(self, external_ids: list[str]) -> int:
        if self.fail:
            raise RuntimeError("upstream exploded")
        self.deleted.extend(external_ids)
        return len(external_ids)

    def list_events(self, oldest: date, newest: date) -> list[object]:
        if self.fail:
            raise RuntimeError("upstream exploded")
        self.listed.append((oldest, newest))
        # intervals.icu returns only what the requested window covers, so the
        # fake must too — otherwise a too-narrow window looks harmless here.
        return [
            event
            for event in self.remote_events
            if oldest.isoformat()
            <= str(getattr(event, "start_date_local", ""))[:10]
            <= newest.isoformat()
        ]


class OrderedStubClient(StubClient):
    """A stub that records the order of the remote calls it received."""

    def __init__(self, *, fail_delete: bool = False) -> None:
        super().__init__()
        self.fail_delete = fail_delete
        self.order: list[str] = []

    def bulk_upsert_events(self, events: list[dict]) -> list[object]:
        self.order.append("upsert")
        return super().bulk_upsert_events(events)

    def bulk_delete_events(self, external_ids: list[str]) -> int:
        self.order.append("delete")
        if self.fail_delete:
            raise RuntimeError("delete exploded")
        return super().bulk_delete_events(external_ids)


def _progress(event: object, message: object = None) -> None:
    _ = event, message


def _base_repos() -> MagicMock:
    repos = MagicMock()
    repos.activity.get_by_date_range.return_value = []
    repos.physiological.get_latest.return_value = None
    repos.self_assessment.get_all.return_value = {
        "running": SelfAssessmentRow(8.0, 60, "2026-01-01")
    }
    repos.session.transaction.return_value = nullcontext()
    return repos


def _request() -> GenerateRequest:
    return GenerateRequest(
        races=[RaceGoalModel(goal_type="running_10k", race_date=_RACE)],
        training_start=_START,
        ambition_level="moderate",
        accept_experimental=True,  # the 10K is Experimental
    )


def _races() -> list[dict[str, object]]:
    return [{"goal_type": "running_10k", "race_date": _RACE, "priority": "A"}]


@lru_cache(maxsize=1)
def _generated() -> object:
    """One real generated plan, shared by every test in this module."""
    result = run_generate(_request(), repos=_base_repos(), progress=_progress)
    assert result.status == "ok"
    return result


class TestThresholds:
    def test_sport_settings_win_over_the_plan_state(self) -> None:
        thresholds = thresholds_by_sport(
            {
                "thresholds": {"cycling": {"ftp_watts": 260, "lthr_bpm": 168}},
                "sports": {"cycling": {"ftp_watts": 200}},
            }
        )
        assert thresholds[SportType.CYCLING].ftp_watts == 260
        assert thresholds[SportType.CYCLING].lthr_bpm == 168

    def test_plan_state_backfills_a_missing_sport_settings_value(self) -> None:
        thresholds = thresholds_by_sport(
            {"sports": {"running": {"threshold_pace_sec_per_km": 250.0}}}
        )
        assert thresholds[SportType.RUNNING].threshold_pace_sec_per_km == 250.0
        assert thresholds[SportType.RUNNING].lthr_bpm is None

    def test_every_sport_is_present_even_without_data(self) -> None:
        thresholds = thresholds_by_sport(None)
        assert set(thresholds) == set(SportType)
        assert thresholds[SportType.SWIMMING].css_sec_per_100m is None


class TestPushPlanEvents:
    def test_missing_api_key_is_skipped_not_an_error(self) -> None:
        repos = _base_repos()
        push = push_plan_events(1, [], _races(), None, repos=repos, client=None)

        assert push["status"] == "skipped"
        assert push["message"] == PUSH_SKIPPED_MESSAGE
        assert push["pushed"] == 0
        repos.plan.update_pushed_events.assert_not_called()

    def test_pushes_one_event_per_session_plus_the_race(self) -> None:
        result = _generated()
        repos = _base_repos()
        client = StubClient()

        push = push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=client,
        )

        assert push["status"] == "ok"
        assert push["errors"] == []
        assert push["pushed"] == len(result.sessions) + 1
        events = client.upserted[0]
        assert events[0]["external_id"] == f"coachie:7:{result.sessions[0]['slot_id']}"
        assert events[-1]["category"] == "RACE_A"

    def test_stores_the_pushed_event_per_session(self) -> None:
        result = _generated()
        repos = _base_repos()

        push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=StubClient(),
        )

        plan_id, stored = repos.plan.update_pushed_events.call_args.args
        assert plan_id == 7
        assert set(stored) == {session["slot_id"] for session in result.sessions}
        first = json.loads(stored[result.sessions[0]["slot_id"]])
        assert first["category"] == "WORKOUT"
        assert first["start_date_local"] == f"{result.sessions[0]['date']}T00:00:00"

    def test_stale_events_are_deleted_after_the_upsert(self) -> None:
        """Order matters: a failed upsert must not leave the calendar empty."""
        result = _generated()
        client = OrderedStubClient()

        push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=_base_repos(),
            client=client,
            previous_events=[{"external_id": "coachie:7:gone"}],
        )

        assert client.deleted == ["coachie:7:gone"]
        assert client.order == ["upsert", "delete"]

    def test_a_race_the_coach_put_on_the_calendar_is_not_doubled(self) -> None:
        """Once the plan's own race event lands, the race-table event the coach
        put on that day comes off; a race on another day stays."""
        result = _generated()
        repos = _base_repos()
        repos.races.list.return_value = [
            SimpleNamespace(id=3, race_date=_RACE),
            SimpleNamespace(id=5, race_date="2026-07-12"),
        ]
        client = OrderedStubClient()

        push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=client,
        )

        assert client.deleted == ["coachie:race:3"]
        assert client.order == ["upsert", "delete"]

    def test_a_failed_race_cleanup_never_fails_the_push(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.races.list.side_effect = RuntimeError("db down")

        push = push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=StubClient(),
        )

        assert push["status"] == "ok"
        assert push["leftover"] == 0

    def test_a_failed_upsert_leaves_the_remote_calendar_untouched(self) -> None:
        result = _generated()
        client = StubClient(fail=True)

        push = push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=_base_repos(),
            client=client,
            previous_events=[{"external_id": "coachie:7:gone"}],
        )

        assert push["status"] == "error"
        assert "unchanged" in push["message"]
        assert client.deleted == []

    def test_a_failed_stale_delete_still_reports_the_pushed_plan(self) -> None:
        result = _generated()
        repos = _base_repos()
        client = OrderedStubClient(fail_delete=True)

        push = push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=client,
            previous_events=[{"external_id": "coachie:7:gone"}],
        )

        assert push["status"] == "ok"
        assert push["pushed"] == len(result.sessions) + 1
        assert push["errors"]
        repos.plan.update_pushed_events.assert_called_once()

    def test_a_remote_failure_is_reported_not_raised(self) -> None:
        result = _generated()
        repos = _base_repos()

        push = push_plan_events(
            7,
            result.sessions,
            _races(),
            result.athlete_state,
            repos=repos,
            client=StubClient(fail=True),
        )

        assert push["status"] == "error"
        assert "upstream exploded" in push["errors"][0]
        assert push["pushed"] == 0
        repos.plan.update_pushed_events.assert_not_called()

    def test_an_unrenderable_session_is_reported_not_raised(self) -> None:
        push = push_plan_events(
            7,
            [{"slot_id": "w0", "sport": "running"}],
            _races(),
            None,
            repos=_base_repos(),
            client=StubClient(),
        )

        assert push["status"] == "error"
        assert push["errors"]


def _active_plan(result: object, *, plan_id: int = 7) -> SimpleNamespace:
    solve = {"sessions": result.sessions, "request": {"races": _races()}}
    return SimpleNamespace(
        id=plan_id,
        name="10k Block",
        goal_type="running_10k",
        goal_date=_RACE,
        plan_start=_START,
        solve_result_json=json.dumps(solve),
        athlete_state_json=json.dumps(result.athlete_state),
    )


class TestPushActivePlan:
    def test_without_an_active_plan(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None

        assert push_active_plan(repos=repos, client=StubClient()) == {
            "status": "error",
            "message": "No active plan to push.",
        }

    def test_re_pushes_the_persisted_sessions(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        client = StubClient()

        out = push_active_plan(repos=repos, client=client, today=_TODAY)

        upcoming = [s for s in result.sessions if s["date"] >= _TODAY.isoformat()]
        assert out == {"status": "ok", "plan_id": 7, "push": out["push"]}
        assert out["push"]["pushed"] == len(upcoming) + 1  # + the race event
        assert client.upserted

    def _workout_ids(self, client: StubClient) -> set[str]:
        return {
            event["external_id"]
            for event in client.upserted[0]
            if event["category"] == "WORKOUT"
        }

    def test_a_re_push_leaves_already_pushed_past_sessions_alone(self) -> None:
        """The athlete owns everything up to today — never overwrite it."""
        result = _generated()
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = _session_rows(result)
        client = StubClient()

        push_active_plan(repos=repos, client=client, today=_TODAY)

        expected = {
            f"coachie:7:{session['slot_id']}"
            for session in result.sessions
            if session["date"] >= _TODAY.isoformat()
        }
        assert expected
        assert self._workout_ids(client) == expected

    def test_a_never_pushed_past_session_is_not_back_filled(self) -> None:
        """A plan predating the integration must not post weeks of history.

        Every row lacking ``pushed_event_json`` is exactly what both a
        failed first push and a pre-integration plan look like, so the
        cutoff — not the push bookkeeping — decides what a sync writes.
        """
        result = _generated()
        rows = _session_rows(result)
        for row in rows:
            row.pushed_event_json = None
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = rows
        client = StubClient()

        push_active_plan(repos=repos, client=client, today=_TODAY)

        assert self._workout_ids(client) == {
            f"coachie:7:{session['slot_id']}"
            for session in result.sessions
            if session["date"] >= _TODAY.isoformat()
        }
        assert any(s["date"] < _TODAY.isoformat() for s in result.sessions), (
            "fixture must span the cutoff for this test to mean anything"
        )

    def test_a_skipped_session_is_not_resurrected_by_a_sync(self) -> None:
        """The skip deleted the event — "Sync now" must not put it back."""
        result = _generated()
        rows = _session_rows(result)
        skipped = next(row for row in rows if row.date >= _TODAY.isoformat())
        skipped.status = "skipped"
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = rows
        client = StubClient()

        push_active_plan(repos=repos, client=client, today=_TODAY)

        assert f"coachie:7:{skipped.slot_id}" not in self._workout_ids(client)
        assert self._workout_ids(client) == {
            f"coachie:7:{session['slot_id']}"
            for session in result.sessions
            if session["date"] >= _TODAY.isoformat()
            and session["slot_id"] != skipped.slot_id
        }

    def test_an_unreadable_plan_still_answers(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = SimpleNamespace(
            id=7, solve_result_json="{oops", athlete_state_json='"not-an-object"'
        )
        client = StubClient()

        out = push_active_plan(repos=repos, client=client)

        assert out["push"]["pushed"] == 0
        assert client.upserted == [[]]

    def test_an_unreadable_athlete_state_falls_back_to_no_thresholds(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = SimpleNamespace(
            id=7, solve_result_json="{}", athlete_state_json="{oops"
        )

        out = push_active_plan(repos=repos, client=StubClient())

        assert out["push"]["status"] == "ok"


class TestCommitPush:
    def test_commit_reports_the_push_result(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.create_macro.return_value = 13
        repos.plan.create.return_value = 42
        client = StubClient()

        out = run_commit(
            CommitRequest(
                name="10k Block",
                generate_request=_request(),
                generate_result=result,
            ),
            repos=repos,
            maybe_get_coach=lambda: None,
            client=client,
        )

        assert out["status"] == "ok"
        assert out["plan_id"] == 42
        assert out["push"]["status"] == "ok"
        assert client.upserted[0][0]["external_id"].startswith("coachie:42:")

    def test_committing_over_a_plan_clears_the_old_plans_upcoming_events(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The archived plan's upcoming events must not linger on the calendar.

        ``repos.plan.create`` archives the previous plan locally, but its
        events carry their own ``coachie:{old_id}:`` ids, so nothing about
        the new push collides with them. Left alone the athlete sees both
        plans stacked on the same days — one belonging to a plan the app
        shows nowhere. The days before today stay as the athlete trained
        them: a finished block keeps its workouts beside its activities.
        """
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", _TODAY.isoformat())
        result = _generated()
        repos = _base_repos()
        repos.plan.create_macro.return_value = 13
        repos.plan.create.return_value = 6
        # The outgoing plan, as get_active() reports it *before* create()
        # archives it. Its ids come from its own persisted solve payload.
        repos.plan.get_active.return_value = SimpleNamespace(
            id=5,
            solve_result_json=json.dumps(
                {
                    "sessions": [
                        {"slot_id": "wk0_tue_run", "date": "2026-05-19"},
                        {"slot_id": "wk0_sat_run", "date": "2026-05-23"},
                    ],
                    "request": {"races": _races()},
                    # A replan of the old plan failed to delete this one.
                    "pending_event_deletes": ["coachie:5:r2:wk3_thu_run"],
                }
            ),
        )
        client = StubClient()

        out = run_commit(
            CommitRequest(
                name="Second Plan",
                generate_request=_request(),
                generate_result=result,
            ),
            repos=repos,
            maybe_get_coach=lambda: None,
            client=client,
        )

        assert out["push"]["status"] == "ok"
        assert set(client.deleted) == {
            "coachie:5:wk0_sat_run",
            f"coachie:5:race:A:{_RACE}",
            "coachie:5:r2:wk3_thu_run",
        }
        # ...and the new plan's own events survive the same push.
        assert all(
            e["external_id"].startswith("coachie:6:") for e in client.upserted[0]
        )

    def test_a_failed_push_does_not_roll_the_commit_back(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.create_macro.return_value = 13
        repos.plan.create.return_value = 42

        out = run_commit(
            CommitRequest(
                name="10k Block",
                generate_request=_request(),
                generate_result=result,
            ),
            repos=repos,
            maybe_get_coach=lambda: None,
            client=StubClient(fail=True),
        )

        assert out["status"] == "ok"
        assert out["plan_id"] == 42
        assert out["push"]["status"] == "error"
        repos.plan.bulk_insert_sessions.assert_called_once()

    def test_commit_without_a_client_skips_the_push(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.create_macro.return_value = 13
        repos.plan.create.return_value = 42

        out = run_commit(
            CommitRequest(
                name="10k Block",
                generate_request=_request(),
                generate_result=result,
            ),
            repos=repos,
            maybe_get_coach=lambda: None,
        )

        assert out["push"]["message"] == PUSH_SKIPPED_MESSAGE


class TestDeleteActivePlan:
    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", _TODAY.isoformat())

    def _repos_with_plan(self) -> MagicMock:
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(_generated())
        repos.plan.get_macro_for_plan.return_value = None
        return repos

    def test_deletes_the_plans_remote_events_from_today_on(self) -> None:
        repos = self._repos_with_plan()
        client = StubClient()

        out = delete_active_plan(
            get_repos=lambda: repos,
            maybe_get_coach=lambda: None,
            client=client,
        )

        assert out["status"] == "ok"
        assert out["remote_delete"]["status"] == "ok"
        assert out["remote_delete"]["deleted"] == len(client.deleted)
        sessions = _generated().sessions
        assert set(client.deleted) == {
            f"coachie:7:{s['slot_id']}"
            for s in sessions
            if s["date"] >= _TODAY.isoformat()
        } | {f"coachie:7:race:A:{_RACE}"}
        assert any(s["date"] < _TODAY.isoformat() for s in sessions)
        repos.plan.archive.assert_called_once_with(7)

    def test_a_remote_failure_still_archives_locally(self) -> None:
        repos = self._repos_with_plan()

        out = delete_active_plan(
            get_repos=lambda: repos,
            maybe_get_coach=lambda: None,
            client=StubClient(fail=True),
        )

        assert out["status"] == "ok"
        assert out["remote_delete"]["status"] == "error"
        assert out["remote_delete"]["errors"]
        repos.plan.archive.assert_called_once_with(7)

    def test_without_a_client_the_remote_side_is_skipped(self) -> None:
        repos = self._repos_with_plan()

        out = delete_active_plan(get_repos=lambda: repos, maybe_get_coach=lambda: None)

        assert out["remote_delete"]["status"] == "skipped"
        assert out["remote_delete"]["message"] == PUSH_SKIPPED_MESSAGE

    def test_a_plan_with_no_events_makes_no_remote_call(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = SimpleNamespace(
            id=7, solve_result_json="{}", athlete_state_json=None
        )
        repos.plan.get_macro_for_plan.return_value = None
        client = StubClient()

        out = delete_active_plan(
            get_repos=lambda: repos,
            maybe_get_coach=lambda: None,
            client=client,
        )

        assert out["remote_delete"] == {
            "status": "ok",
            "errors": [],
            "message": None,
            "deleted": 0,
        }
        assert client.deleted == []


def _session_rows(result: object, plan_id: int = 7) -> list[SimpleNamespace]:
    """Session rows carrying the events we last pushed for them."""
    events = build_plan_events(plan_id, result.sessions, _races(), result.athlete_state)
    return [
        SimpleNamespace(
            id=index,
            slot_id=session["slot_id"],
            date=session["date"],
            matched_activity_id=None,
            pushed_event_json=json.dumps(event),
        )
        for index, (session, event) in enumerate(
            zip(result.sessions, events, strict=False)
        )
    ]


class TestReplanReadbackDiff:
    def test_no_client_means_no_diff(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(_generated())

        assert replan_readback_diff(repos=repos, client=None, today=_TODAY) == []

    def test_no_active_plan_means_no_diff(self) -> None:
        repos = _base_repos()
        repos.plan.get_active.return_value = None

        assert (
            replan_readback_diff(repos=repos, client=StubClient(), today=_TODAY) == []
        )

    def test_never_pushed_sessions_mean_no_diff(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = [
            SimpleNamespace(
                id=1,
                slot_id="w0",
                date=_CUTOFF,
                matched_activity_id=None,
                pushed_event_json=None,
            )
        ]

        assert (
            replan_readback_diff(repos=repos, client=StubClient(), today=_TODAY) == []
        )

    def test_reports_a_manually_edited_future_event(self) -> None:
        result = _generated()
        rows = _session_rows(result)
        future = next(row for row in rows if row.date >= _CUTOFF)
        pushed = json.loads(future.pushed_event_json)
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = rows
        client = StubClient(
            remote_events=[
                SimpleNamespace(
                    external_id=pushed["external_id"],
                    start_date_local=pushed["start_date_local"],
                    name="Athlete's own session",
                    description=pushed["description"],
                )
            ]
        )

        diff = replan_readback_diff(repos=repos, client=client, today=_TODAY)

        assert diff == [
            {
                "external_id": pushed["external_id"],
                "date": pushed["start_date_local"],
                "name": pushed["name"],
                "changed_fields": ["name"],
            }
        ]
        assert client.listed == [
            (
                date.fromisoformat(_CUTOFF) - _PAD,
                date.fromisoformat(_RACE) + _PAD,
            )
        ]

    def test_an_event_moved_past_the_goal_date_reads_as_drift(self) -> None:
        # The readback window must not stop at the goal date, or an event
        # hand-moved past race day falls out of the readback, reports no
        # drift, and the replan overwrites the edit without confirmation.
        result = _generated()
        rows = _session_rows(result)
        future = next(row for row in rows if row.date >= _CUTOFF)
        pushed = json.loads(future.pushed_event_json)
        moved = event_start(date.fromisoformat(_RACE) + timedelta(days=3))
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = rows
        client = StubClient(
            remote_events=[
                SimpleNamespace(
                    external_id=pushed["external_id"],
                    start_date_local=moved,
                    name=pushed["name"],
                    description=pushed["description"],
                )
            ]
        )

        diff = replan_readback_diff(repos=repos, client=client, today=_TODAY)

        assert diff == [
            {
                "external_id": pushed["external_id"],
                "date": pushed["start_date_local"],
                "name": pushed["name"],
                "changed_fields": ["start_date_local"],
            }
        ]

    def test_unreadable_pushed_json_is_skipped(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = [
            SimpleNamespace(
                id=1,
                slot_id="w0",
                date=_CUTOFF,
                matched_activity_id=None,
                pushed_event_json="{oops",
            )
        ]

        assert (
            replan_readback_diff(repos=repos, client=StubClient(), today=_TODAY) == []
        )

    def test_an_unreachable_api_does_not_block_the_replan(self) -> None:
        result = _generated()
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = _session_rows(result)

        diff = replan_readback_diff(
            repos=repos, client=StubClient(fail=True), today=_TODAY
        )

        assert diff == []

    def test_a_resume_date_moves_the_readback_to_that_day(self) -> None:
        # A mid-week resume keeps the days before it, so an edit there is
        # the athlete's own and must not be reported as drift.
        result = _generated()
        rows = _session_rows(result)
        resume = date(2026, 5, 28)
        kept = next(row for row in rows if _CUTOFF <= row.date < resume.isoformat())
        pushed = json.loads(kept.pushed_event_json)
        repos = _base_repos()
        repos.plan.get_active.return_value = _active_plan(result)
        repos.plan.get_sessions.return_value = rows
        client = StubClient(
            remote_events=[
                SimpleNamespace(
                    external_id=pushed["external_id"],
                    start_date_local=pushed["start_date_local"],
                    name="Athlete's own session",
                    description=pushed["description"],
                )
            ]
        )

        diff = replan_readback_diff(
            repos=repos, client=client, today=_TODAY, resume_date=resume
        )

        assert diff == []
        assert client.listed == [(resume - _PAD, date.fromisoformat(_RACE) + _PAD)]


class TestReplanPush:
    def _repos(self, result: object) -> MagicMock:
        repos = _base_repos()
        active = _active_plan(result)
        active.solver_version = "planner_v2_session_menu_v1"
        solve = json.loads(active.solve_result_json)
        solve["request"] = {
            "races": _races(),
            "training_start": _START,
            "ambition_level": "moderate",
        }
        active.solve_result_json = json.dumps(solve)
        repos.plan.get_active.return_value = active
        repos.plan.get_sessions.return_value = _session_rows(result)
        repos.plan.get_macro_for_plan.return_value = SimpleNamespace(id=5, revision=1)
        repos.plan.delete_future_sessions.return_value = 3
        return repos

    def test_pushes_the_regenerated_sessions(self) -> None:
        result = _generated()
        repos = self._repos(result)
        client = StubClient()

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
            client=client,
        )

        assert out["push"]["status"] == "ok"
        pushed = client.upserted[0]
        assert all(
            event["external_id"].startswith("coachie:7:r2:")
            for event in pushed
            if event["category"] == "WORKOUT"
        )

    def test_deletes_the_events_of_dropped_sessions(self) -> None:
        result = _generated()
        rows = _session_rows(result)
        repos = self._repos(result)
        client = StubClient()

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
            client=client,
        )

        def _ids(future: bool) -> set[str]:
            return {
                json.loads(row.pushed_event_json)["external_id"]
                for row in rows
                if (row.date >= _CUTOFF) is future
            }

        # The rewrite window is regenerated under fresh r2: slot ids, so
        # every event already pushed for it is stale; the retained past
        # keeps its events.
        assert set(client.deleted) == _ids(future=True)
        assert _ids(future=False).isdisjoint(client.deleted)

    def test_a_passed_b_race_keeps_its_calendar_event(self) -> None:
        """A B race behind the cut stays on the plan's timeline, event included."""
        result = _generated()
        repos = self._repos(result)
        active = repos.plan.get_active.return_value
        solve = json.loads(active.solve_result_json)
        solve["request"]["races"] = [
            *_races(),
            {"goal_type": "running_10k", "race_date": "2026-04-05", "priority": "B"},
        ]
        active.solve_result_json = json.dumps(solve)
        client = StubClient()

        run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
            client=client,
        )

        assert "coachie:7:race:B:2026-04-05" not in client.deleted
        assert f"coachie:7:race:A:{_RACE}" not in client.deleted

    def test_without_a_client_the_replan_still_succeeds(self) -> None:
        result = _generated()
        repos = self._repos(result)

        out = run_replan(
            repos=repos,
            progress=_progress,
            maybe_get_coach=lambda: None,
            today=_TODAY,
        )

        assert out["status"] == "ok"
        assert out["push"]["status"] == "skipped"


@pytest.mark.parametrize("payload", [{}, {"request": []}, {"request": {}}])
def test_plan_races_tolerates_a_broken_request_block(payload: dict) -> None:
    from endury.planning.commit_plan import plan_races

    assert plan_races(payload) == []


class TestIntervalsProvider:
    def teardown_method(self) -> None:
        _build_client.cache_clear()

    def test_a_missing_api_key_means_no_client(self) -> None:
        assert get_intervals_client(CoachConfig(intervals_api_key=None)) is None

    def test_a_configured_key_yields_one_shared_client(self) -> None:
        config = CoachConfig(
            intervals_api_key="secret",
            intervals_athlete_id="i12345",
            intervals_base_url="https://example.test/api/v1",
        )

        client = get_intervals_client(config)

        assert client is not None
        assert get_intervals_client(config) is client
        assert client._athlete_id == "i12345"
        assert str(client._client.base_url).startswith("https://example.test/api/v1")
        client.close()
