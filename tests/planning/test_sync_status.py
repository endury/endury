"""Tests for the plan-vs-intervals.icu sync status."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

from endury.intervals.client import IntervalsApiError, IntervalsAuthError
from endury.intervals.models import IntervalsEvent, IntervalsPushError
from endury.intervals.push import session_sync_states
from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.foundations.types import ActivityKey
from endury.planning import adjust_week as adjust_week_mod
from endury.planning.adjust_week import adjust_current_week
from endury.planning.commit_plan import build_plan_events, push_active_plan
from endury.planning.sync_status import (
    AUTH_REJECTED_REASON,
    NO_PLAN_REASON,
    NOT_CONFIGURED_REASON,
    build_sync_status,
    repushable_sessions,
)
from endury.planning.wire.sessions import (
    build_commit_session_rows,
    serialize_planned_session,
)

_TODAY = date(2026, 5, 20)
_PLAN = 7


def _row(
    slot_id: str,
    day: str,
    pushed: dict | None = None,
    status: str = "pending",
) -> SimpleNamespace:
    return SimpleNamespace(
        slot_id=slot_id,
        date=day,
        status=status,
        pushed_event_json=json.dumps(pushed) if pushed else None,
    )


def _pushed(slot_id: str, day: str) -> dict:
    return {
        "external_id": f"coachie:{_PLAN}:{slot_id}",
        "start_date_local": f"{day}T00:00:00",
        "name": "Easy",
        "description": "prose\n\n- Easy 30m 70% LTHR",
    }


def _remote(slot_id: str, day: str, **over: object) -> IntervalsEvent:
    base = {
        "external_id": f"coachie:{_PLAN}:{slot_id}",
        "start_date_local": f"{day}T00:00:00",
        "name": "Easy",
        "description": "prose\n\n- Easy 30m 70% LTHR",
    }
    base.update(over)
    return IntervalsEvent.model_validate(base)


def _adhoc(day: str, token: str = "abc", **over: object) -> IntervalsEvent:
    base = {
        "id": 900,
        "external_id": f"coachie:adhoc:{token}",
        "start_date_local": f"{day}T00:00:00",
        "name": "Tempo",
        "category": "WORKOUT",
    }
    base.update(over)
    return IntervalsEvent.model_validate(base)


class _Client:
    def __init__(self, events: list[object] | Exception) -> None:
        self._events = events

    def list_events(self, oldest: date, newest: date) -> list[object]:
        if isinstance(self._events, Exception):
            raise self._events
        # intervals.icu returns only what the requested window covers, so the
        # fake must too — otherwise a too-narrow window looks harmless here.
        return [
            event
            for event in self._events
            if oldest.isoformat()
            <= str(getattr(event, "start_date_local", ""))[:10]
            <= newest.isoformat()
        ]


def _repos(rows: list[SimpleNamespace], plan: object = object()) -> SimpleNamespace:
    return SimpleNamespace(
        plan=SimpleNamespace(
            get_active=lambda: SimpleNamespace(id=_PLAN) if plan else None,
            get_sessions=lambda _id: rows,
        )
    )


class TestSessionSyncStates:
    def test_a_matching_remote_event_is_in_sync(self) -> None:
        rows = [_row("s1", "2026-05-21", _pushed("s1", "2026-05-21"))]
        states = session_sync_states(_PLAN, rows, [_remote("s1", "2026-05-21")], _TODAY)
        assert [(s.slot_id, s.state) for s in states] == [("s1", "in_sync")]

    def test_a_never_pushed_session_is_missing(self) -> None:
        rows = [_row("s1", "2026-05-21")]
        states = session_sync_states(_PLAN, rows, [], _TODAY)
        assert states[0].state == "missing"

    def test_a_pushed_session_gone_from_the_calendar_is_removed(self) -> None:
        rows = [_row("s1", "2026-05-21", _pushed("s1", "2026-05-21"))]
        states = session_sync_states(_PLAN, rows, [], _TODAY)
        assert states[0].state == "removed"

    def test_a_hand_edited_event_reports_the_changed_fields(self) -> None:
        rows = [_row("s1", "2026-05-21", _pushed("s1", "2026-05-21"))]
        states = session_sync_states(_PLAN, rows, [_remote("s1", "2026-05-23")], _TODAY)
        assert states[0].state == "edited"
        assert states[0].changed_fields == ("start_date_local",)

    def test_a_failed_local_move_shows_as_drifted(self) -> None:
        # A reschedule whose remote write failed leaves pushed_event_json and
        # the remote event agreeing on the OLD day, so only the session row
        # itself knows the plan moved.
        rows = [_row("s1", "2026-05-22", _pushed("s1", "2026-05-21"))]
        states = session_sync_states(_PLAN, rows, [_remote("s1", "2026-05-21")], _TODAY)
        assert states[0].state == "edited"
        assert states[0].changed_fields == ("start_date_local",)

    def test_a_hand_edit_and_a_failed_move_are_not_double_counted(self) -> None:
        rows = [_row("s1", "2026-05-22", _pushed("s1", "2026-05-21"))]
        states = session_sync_states(
            _PLAN, rows, [_remote("s1", "2026-05-23", name="Mine")], _TODAY
        )
        assert states[0].changed_fields == ("start_date_local", "name")

    def test_a_device_rejection_outranks_a_hand_edit(self) -> None:
        # The edit is the athlete's own choice; the rejection means the
        # workout will not be on their device, so it must win the badge.
        rows = [_row("s1", "2026-05-21", _pushed("s1", "2026-05-21"))]
        remote = _remote(
            "s1",
            "2026-05-23",
            push_errors=[IntervalsPushError(service="GARMIN", message="nope")],
        )
        states = session_sync_states(_PLAN, rows, [remote], _TODAY)
        assert states[0].state == "delivery_failed"
        assert states[0].services == ("GARMIN",)

    def test_past_sessions_are_not_classified(self) -> None:
        rows = [_row("old", "2026-05-19"), _row("s1", "2026-05-21")]
        states = session_sync_states(_PLAN, rows, [], _TODAY)
        assert [s.slot_id for s in states] == ["s1"]

    def test_a_skipped_session_is_not_classified(self) -> None:
        # Its event was deleted on purpose, so its absence is not drift.
        rows = [
            _row("s1", "2026-05-21", _pushed("s1", "2026-05-21"), "skipped"),
            _row("s2", "2026-05-22"),
        ]
        states = session_sync_states(_PLAN, rows, [], _TODAY)
        assert [s.slot_id for s in states] == ["s2"]

    def test_a_row_without_a_status_is_still_classified(self) -> None:
        # Fixtures (and the readback diff) pass rows carrying only the
        # fields the diff needs.
        rows = [SimpleNamespace(slot_id="s1", date="2026-05-21")]
        states = session_sync_states(_PLAN, rows, [], _TODAY)
        assert [s.slot_id for s in states] == ["s1"]


class TestBuildSyncStatus:
    def test_no_client_is_unavailable(self) -> None:
        out = build_sync_status(_repos([]), None, today=_TODAY)
        assert out == {
            "available": False,
            "reason": NOT_CONFIGURED_REASON,
            "sessions": {},
        }

    def test_no_active_plan_is_unavailable(self) -> None:
        out = build_sync_status(_repos([], plan=None), _Client([]), today=_TODAY)
        assert out["reason"] == NO_PLAN_REASON

    def test_a_plan_with_no_upcoming_sessions_is_trivially_available(self) -> None:
        out = build_sync_status(
            _repos([_row("old", "2026-01-01")]), _Client([]), today=_TODAY
        )
        assert out["available"] is True
        assert out["out_of_sync"] == 0
        assert out["sessions"] == {}

    def test_coach_scheduled_workouts_report_without_a_plan(self) -> None:
        # schedule_workout persists nothing locally, so an ad-hoc event that
        # reads back is on the calendar by construction — the one open
        # question is whether the device took it. Keyed by external_id.
        remote = [
            _adhoc("2026-05-22", "ok"),
            _adhoc(
                "2026-05-24",
                "rejected",
                push_errors=[IntervalsPushError(service="GARMIN", message="nope")],
            ),
            _adhoc("2026-05-10", "past"),
        ]
        out = build_sync_status(_repos([], plan=None), _Client(remote), today=_TODAY)
        assert out["available"] is True
        assert out["summary"]["in_sync"] == 1
        assert out["summary"]["delivery_failed"] == 1
        assert out["out_of_sync"] == 1
        assert out["sessions"]["coachie:adhoc:rejected"] == {
            "state": "delivery_failed",
            "services": ["GARMIN"],
            "changed_fields": [],
        }
        assert "coachie:adhoc:past" not in out["sessions"]

    def test_coach_scheduled_workouts_count_alongside_the_plan(self) -> None:
        rows = [_row("a", "2026-05-21", _pushed("a", "2026-05-21"))]
        # The plan window reaches back READBACK_PAD_DAYS, so a past ad-hoc
        # event is read — and must be skipped, like a past plan session.
        remote = [
            _remote("a", "2026-05-21"),
            _adhoc("2026-05-22"),
            _adhoc("2026-05-12", "past"),
        ]
        out = build_sync_status(_repos(rows), _Client(remote), today=_TODAY)
        assert out["summary"]["in_sync"] == 2
        assert set(out["sessions"]) == {"a", "coachie:adhoc:abc"}

    def test_no_plan_and_nothing_scheduled_ahead_stays_unavailable(self) -> None:
        out = build_sync_status(
            _repos([], plan=None), _Client([_adhoc("2026-05-10", "past")]), today=_TODAY
        )
        assert out["available"] is False
        assert out["reason"] == NO_PLAN_REASON

    def test_counts_and_per_session_states(self) -> None:
        rows = [
            _row("a", "2026-05-21", _pushed("a", "2026-05-21")),
            _row("b", "2026-05-22"),
            _row("c", "2026-05-23", _pushed("c", "2026-05-23")),
        ]
        remote = [_remote("a", "2026-05-21"), _remote("c", "2026-05-25")]

        out = build_sync_status(_repos(rows), _Client(remote), today=_TODAY)

        assert out["summary"] == {
            "in_sync": 1,
            "missing": 1,
            "removed": 0,
            "delivery_failed": 0,
            "edited": 1,
            "leftover": 0,
        }
        assert out["out_of_sync"] == 2
        assert out["sessions"]["b"]["state"] == "missing"
        assert out["sessions"]["c"]["changed_fields"] == ["start_date_local"]

    def test_an_event_moved_past_the_last_session_reads_edited(self) -> None:
        # The readback window must not stop at the plan's own last session
        # date, or a late-moved event looks deleted.
        rows = [_row("a", "2026-05-21", _pushed("a", "2026-05-21"))]
        remote = [_remote("a", "2026-06-10")]

        out = build_sync_status(_repos(rows), _Client(remote), today=_TODAY)

        assert out["sessions"]["a"]["state"] == "edited"
        assert out["summary"]["removed"] == 0

    def test_an_event_moved_before_today_reads_edited(self) -> None:
        rows = [_row("a", "2026-05-21", _pushed("a", "2026-05-21"))]
        remote = [_remote("a", "2026-05-12")]

        out = build_sync_status(_repos(rows), _Client(remote), today=_TODAY)

        assert out["sessions"]["a"]["state"] == "edited"
        assert out["summary"]["removed"] == 0

    def test_an_api_error_degrades_instead_of_raising(self) -> None:
        out = build_sync_status(
            _repos([_row("a", "2026-05-21")]),
            _Client(IntervalsApiError(500, "boom")),
            today=_TODAY,
        )
        assert out["available"] is False
        assert "Could not reach intervals.icu" in out["reason"]

    def test_a_rejected_key_reports_an_auth_reason_instead_of_network(self) -> None:
        out = build_sync_status(
            _repos([_row("a", "2026-05-21")]),
            _Client(IntervalsAuthError(401, "bad key")),
            today=_TODAY,
        )
        assert out["available"] is False
        assert out["reason"] == AUTH_REJECTED_REASON

    def test_a_transport_failure_degrades_too(self) -> None:
        out = build_sync_status(
            _repos([_row("a", "2026-05-21")]),
            _Client(httpx.ConnectError("dns")),
            today=_TODAY,
        )
        assert out["available"] is False

    def test_a_skipped_session_does_not_read_as_out_of_sync(self) -> None:
        # The skip deleted the event itself; reporting it as drift would put
        # a "Sync now" badge on the plan view forever.
        rows = [_row("a", "2026-05-21", _pushed("a", "2026-05-21"), "skipped")]

        out = build_sync_status(_repos(rows), _Client([]), today=_TODAY)

        assert out["out_of_sync"] == 0
        assert out["sessions"] == {}


class TestRepushableSessions:
    """What "Sync now" is allowed to write back to the calendar."""

    def test_only_today_onwards(self) -> None:
        sessions = [
            {"slot_id": "a", "date": "2026-05-19"},
            {"slot_id": "b", "date": "2026-05-20"},
            {"slot_id": "c", "date": "2026-05-21"},
        ]
        out = repushable_sessions(sessions, [], _TODAY)
        assert [s["slot_id"] for s in out] == ["b", "c"]

    def test_a_skipped_session_is_never_re_pushed(self) -> None:
        sessions = [
            {"slot_id": "a", "date": "2026-05-21"},
            {"slot_id": "b", "date": "2026-05-22"},
        ]
        rows = [_row("a", "2026-05-21", status="skipped"), _row("b", "2026-05-22")]

        out = repushable_sessions(sessions, rows, _TODAY)

        assert [s["slot_id"] for s in out] == ["b"]

    def test_a_coach_extra_is_re_pushed_like_any_other_session(self) -> None:
        """An extra rides in the solve payload, so "Sync now" owns it too."""
        sessions = [
            {"slot_id": "menu:running:run_easy:w10:i1", "date": "2026-05-21"},
            {"slot_id": "x1:extra_2026-05-22_run_easy", "date": "2026-05-22"},
        ]
        rows = [
            _row("menu:running:run_easy:w10:i1", "2026-05-21"),
            _row("x1:extra_2026-05-22_run_easy", "2026-05-22"),
        ]
        out = repushable_sessions(sessions, rows, _TODAY)
        assert [s["slot_id"] for s in out] == [s["slot_id"] for s in sessions]

    def test_rows_without_a_status_exclude_nothing(self) -> None:
        sessions = [{"slot_id": "a", "date": "2026-05-21"}]
        rows = [SimpleNamespace(slot_id="a")]
        assert repushable_sessions(sessions, rows, _TODAY) == sessions


class TestATrimmedWeekIsPendingUntilItIsPushed:
    """A week adjustment whose push failed must never read ``in_sync``."""

    _DAY = "2026-05-21"
    _SLOT = "w00_run_easy_0"

    def _committed(self) -> dict:
        """The session payload the plan was committed with."""
        ensure_registry_loaded()
        activity = get_activity(ActivityKey("run_easy"))
        target = assignment_target_tss(activity, 1.0, None)
        return dict(
            serialize_planned_session(
                slot_id=self._SLOT,
                session_date=date.fromisoformat(self._DAY),
                week_index=0,
                phase_type="base",
                sport="running",
                activity_key="run_easy",
                family="easy",
                is_recovery_week=False,
                coaching_intent="Aerobic maintenance.",
                hardness=activity.hardness,
                workout=activity.build(target, resolver=None),
                workout_name="Easy Run",
                target_tss=target,
                achieved_tss=activity.achieved_tss(target, resolver=None),
                duration_minutes=activity.duration_minutes_exact(target, resolver=None),
                duration_target_minutes=60.0,
                tid_fractions=activity.tid_fractions(target, resolver=None),
            )
        )

    def _trim(self, monkeypatch, client) -> tuple[MagicMock, dict, dict]:
        """Apply a 40% trim to a one-session plan; return repos + payloads."""
        # A threshold-less plan: the resolver gate lets the rebuild proceed.
        monkeypatch.setattr(
            adjust_week_mod, "get_planner_load_context", lambda _repos: None
        )
        payload = self._committed()
        insert = build_commit_session_rows([payload])[0]
        solve = {
            "sessions": [payload],
            "phase_calendar": ["base"],
            "request": {"races": [], "training_start": "2026-05-18"},
        }
        row = SimpleNamespace(
            id=1,
            plan_id=_PLAN,
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
            workout_json=insert.workout_json,
            duration_target_minutes=60.0,
            tid_fractions_json=insert.tid_fractions_json,
            matched_activity_id=None,
            status="pending",
            pushed_event_json=None,
        )
        repos = MagicMock()
        repos.plan.get_active.return_value = SimpleNamespace(
            id=_PLAN,
            name="Plan",
            goal_type="running_marathon",
            goal_date="2026-08-30",
            plan_start="2026-05-18",
            solver_version="planner_v2_session_menu_v1",
            solve_result_json=json.dumps(solve),
            athlete_state_json="{}",
        )
        repos.plan.get_sessions_by_date_range.side_effect = lambda *_a: [row]
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["status"] == "ok"
        return repos, payload, result

    def test_the_trim_reads_edited_and_converges_on_the_next_push(
        self, monkeypatch
    ) -> None:
        failing = MagicMock()
        failing.bulk_upsert_events.side_effect = RuntimeError("502")
        repos, committed, result = self._trim(monkeypatch, failing)
        assert result["push"]["status"] == "error"

        stored = repos.plan.update_pushed_events.call_args.args[1][self._SLOT]
        row = _row(self._SLOT, self._DAY)
        row.pushed_event_json = stored
        # intervals.icu still holds the workout the plan was committed with.
        remote = IntervalsEvent.model_validate(
            build_plan_events(_PLAN, [committed], [], {})[0]
        )

        states = session_sync_states(_PLAN, [row], [remote], _TODAY)
        assert [(s.state, s.changed_fields) for s in states] == [
            ("edited", ("description",))
        ]
        status = build_sync_status(_repos([row]), _Client([remote]), today=_TODAY)
        assert status["out_of_sync"] == 1
        assert status["summary"]["in_sync"] == 0

        # "Sync now" re-renders from the mirrored solve payload and converges.
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        retry_repos = MagicMock()
        retry_repos.plan.get_active.return_value = SimpleNamespace(
            id=_PLAN,
            solve_result_json=json.dumps(mirrored),
            athlete_state_json="{}",
        )
        ok_client = MagicMock()
        push_active_plan(repos=retry_repos, client=ok_client, today=_TODAY)
        pushed = ok_client.bulk_upsert_events.call_args.args[0][0]
        assert pushed == json.loads(stored)

        converged = session_sync_states(
            _PLAN, [row], [IntervalsEvent.model_validate(pushed)], _TODAY
        )
        assert [s.state for s in converged] == ["in_sync"]
