"""Tests for the calendar payload builder (``endury.planning.calendar_data``)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from endury.intervals.models import IntervalsActivity, IntervalsEvent
from endury.planning.calendar_data import (
    _build_phase_ranges,
    _build_phase_ranges_from_calendar_weeks,
    _build_phase_ranges_from_plan,
    _build_races,
    _process_plan_sessions,
    build_calendar_data,
    delete_ad_hoc_workout,
)

MODULE = "endury.planning.calendar_data"


class _PlanRepo:
    """Stub plan repository: one active plan, its sessions, and match writes."""

    def __init__(
        self,
        active: SimpleNamespace | None,
        sessions: list[SimpleNamespace],
        *,
        fail_writes: bool = False,
    ) -> None:
        self._active = active
        self._sessions = sessions
        self._fail_writes = fail_writes
        self.range_calls: list[tuple[int, str, str]] = []
        self.match_updates: list[list[dict[str, Any]]] = []

    def get_active(self) -> SimpleNamespace | None:
        return self._active

    def get_sessions_by_date_range(
        self, plan_id: int, start: str, end: str
    ) -> list[SimpleNamespace]:
        self.range_calls.append((plan_id, start, end))
        return self._sessions

    def get_sessions(self, plan_id: int) -> list[SimpleNamespace]:
        return self._sessions

    def bulk_update_session_matches(self, updates: list[dict[str, Any]]) -> None:
        if self._fail_writes:
            raise RuntimeError("disk full")
        self.match_updates.append(updates)


class _DbSession:
    """Stub database session whose transaction is a no-op block."""

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        yield


_NO_FACTS = {"goal_type": None, "distance_km": None, "climb_m": None, "notes": None}
_PLAN_FACTS = {**_NO_FACTS, "goal_type": "running_marathon"}


def _repos(
    active: SimpleNamespace | None = None,
    sessions: list[SimpleNamespace] | None = None,
    *,
    fail_writes: bool = False,
    races: list[SimpleNamespace] | None = None,
) -> SimpleNamespace:
    race_repo = MagicMock()
    race_repo.list.return_value = races or []
    race_repo.get_by_date.return_value = None
    return SimpleNamespace(
        plan=_PlanRepo(active, sessions or [], fail_writes=fail_writes),
        session=_DbSession(),
        races=race_repo,
    )


class _Client:
    """Stub intervals.icu client with scripted activity/event reads."""

    def __init__(
        self,
        *,
        activities: list[IntervalsActivity] | None = None,
        events: list[IntervalsEvent] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._activities = activities or []
        self._events = events or []
        self._exc = exc
        self.windows: list[tuple[date, date]] = []

    def list_activities(self, oldest: date, newest: date) -> list[IntervalsActivity]:
        self.windows.append((oldest, newest))
        if self._exc is not None:
            raise self._exc
        return self._activities

    def list_events(
        self, oldest: date, newest: date, category: str | None = None
    ) -> list[IntervalsEvent]:
        return self._events


WINDOW = (date(2026, 2, 9), date(2026, 2, 15))


def _session(
    session_id: int,
    slot_id: str = "s1",
    status: str = "pending",
    matched_activity_id: int | str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        plan_id=7,
        slot_id=slot_id,
        date="2026-02-10",
        week_index=0,
        phase_type="base",
        sport="running",
        activity_key="run_easy",
        family="easy",
        target_tss=60.0,
        duration_minutes=60.0,
        workout_json=None,
        matched_activity_id=matched_activity_id,
        status=status,
    )


def test_build_phase_ranges_from_calendar_weeks_groups_and_clips_goal_date() -> None:
    """Phase ranges should follow contiguous weeks and clip the final partial week."""
    phases = _build_phase_ranges_from_calendar_weeks(
        ["base", "base", "build", "taper"],
        plan_start=date(2030, 1, 7),
        goal_date=date(2030, 1, 30),
    )

    assert phases == [
        {
            "name": "Base",
            "phase_type": "base",
            "start_date": "2030-01-07",
            "end_date": "2030-01-20",
            "duration_weeks": 2,
            "is_current": False,
        },
        {
            "name": "Build",
            "phase_type": "build",
            "start_date": "2030-01-21",
            "end_date": "2030-01-27",
            "duration_weeks": 1,
            "is_current": False,
        },
        {
            "name": "Taper",
            "phase_type": "taper",
            "start_date": "2030-01-28",
            "end_date": "2030-01-30",
            "duration_weeks": 1,
            "is_current": False,
        },
    ]


def test_build_phase_ranges_from_calendar_weeks_returns_empty_for_invalid_range() -> (
    None
):
    """Empty calendars or inverted dates should produce no phase blocks."""
    assert (
        _build_phase_ranges_from_calendar_weeks(
            [],
            plan_start=date(2030, 1, 7),
            goal_date=date(2030, 1, 30),
        )
        == []
    )
    assert (
        _build_phase_ranges_from_calendar_weeks(
            ["base"],
            plan_start=date(2030, 1, 7),
            goal_date=date(2030, 1, 6),
        )
        == []
    )


def test_build_phase_ranges_from_plan_uses_stored_phase_calendar() -> None:
    """Committed plans should expose phase ranges from solve_result_json."""
    plan = SimpleNamespace(
        plan_start="2030-01-07",
        goal_date="2030-01-30",
        solve_result_json=json.dumps(
            {"phase_calendar": ["base", "base", "build", "taper"]}
        ),
    )

    phases = _build_phase_ranges_from_plan(plan)

    assert [phase["phase_type"] for phase in phases] == ["base", "build", "taper"]
    assert phases[-1]["end_date"] == "2030-01-30"
    assert phases[0]["duration_weeks"] == 2


def test_build_phase_ranges_from_plan_returns_empty_for_invalid_payload() -> None:
    """Missing or invalid stored phase calendars should not raise."""
    assert (
        _build_phase_ranges_from_plan(
            SimpleNamespace(
                plan_start="2030-01-07",
                goal_date="2030-01-30",
                solve_result_json="{{bad json",
            )
        )
        == []
    )


def test_build_phase_ranges_from_plan_returns_empty_without_a_stored_result() -> None:
    """A plan row with no (or a blank) solve result has no stored calendar."""
    assert _build_phase_ranges_from_plan(SimpleNamespace()) == []
    assert _build_phase_ranges_from_plan(SimpleNamespace(solve_result_json="  ")) == []


def _active_plan() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        name="Plan",
        goal_type="running_marathon",
        goal_date="2026-06-01",
        plan_start="2026-01-05",
        solve_result_json="{}",
    )


def test_process_plan_sessions_overlays_intervals_completion() -> None:
    """A paired intervals.icu activity marks the session completed."""
    session = _session(session_id=1, slot_id="s1")
    completions = {
        1: {
            "matched_activity_id": "999",
            "compliance_pct": 91.0,
            "intervals_activity_url": "https://intervals.icu/activities/999",
        }
    }

    plans_payload, *_rest = _process_plan_sessions(
        _active_plan(), [session], [session], completions
    )

    assert plans_payload[0]["completed"] is True
    assert plans_payload[0]["status"] == "completed"
    assert plans_payload[0]["matched_activity_id"] == "999"
    assert plans_payload[0]["intervals_activity_url"] == (
        "https://intervals.icu/activities/999"
    )
    assert plans_payload[0]["compliance_pct"] == 91.0


def test_process_plan_sessions_respects_manual_skip_override() -> None:
    """A manually-skipped session is never flipped completed by intervals data."""
    session = _session(session_id=1, slot_id="s1", status="skipped")
    completions = {
        1: {
            "matched_activity_id": "999",
            "compliance_pct": 91.0,
            "intervals_activity_url": "https://intervals.icu/activities/999",
        }
    }

    plans_payload, *_rest = _process_plan_sessions(
        _active_plan(), [session], [session], completions
    )

    assert plans_payload[0]["status"] == "skipped"
    assert "intervals_activity_url" not in plans_payload[0]


def test_process_plan_sessions_without_completions_leaves_sessions_pending() -> None:
    """No intervals.icu data (no API key) degrades to a plan-only calendar."""
    session = _session(session_id=1, slot_id="s1")

    plans_payload, _phases, _races, plan_payload = _process_plan_sessions(
        _active_plan(), [session], [session], {}
    )

    assert plans_payload[0]["completed"] is False
    assert plan_payload is not None
    assert plan_payload["id"] == 7


def test_process_plan_sessions_returns_empty_payloads_without_active_plan() -> None:
    """No active plan should short-circuit with empty calendar plan data."""
    assert _process_plan_sessions(None, [], [], {}) == ([], [], [], None)
    assert (
        _build_phase_ranges_from_plan(
            SimpleNamespace(
                plan_start="2030-01-07",
                goal_date="2030-01-30",
                solve_result_json=json.dumps([]),
            )
        )
        == []
    )
    assert (
        _build_phase_ranges_from_plan(
            SimpleNamespace(
                plan_start="2030-01-07",
                goal_date="2030-01-30",
                solve_result_json=json.dumps({}),
            )
        )
        == []
    )
    assert (
        _build_phase_ranges_from_plan(
            SimpleNamespace(
                plan_start="2030-01-07",
                goal_date="2030-01-30",
                solve_result_json=json.dumps({"phase_calendar": [" ", "", None]}),
            )
        )
        == []
    )
    assert (
        _build_phase_ranges_from_plan(
            SimpleNamespace(
                plan_start="bad-date",
                goal_date="2030-01-30",
                solve_result_json=json.dumps({"phase_calendar": ["base"]}),
            )
        )
        == []
    )


def _paired_client(slot_id: str = "s1") -> _Client:
    """A client whose one activity is paired with the session's pushed event."""
    return _Client(
        activities=[
            IntervalsActivity(
                id="i99", type="Run", paired_event_id=555, compliance=88.0
            )
        ],
        events=[IntervalsEvent(id=555, external_id=f"coachie:7:{slot_id}")],
    )


def test_build_calendar_data_without_a_client_is_plan_only() -> None:
    """No intervals.icu key degrades to the plan alone, flagged as such."""
    repos = _repos(_active_plan(), [_session(1)])

    data = build_calendar_data(repos, None, *WINDOW)

    assert data["compliance_unavailable"] is True
    assert data["activities"] == []
    assert data["calendar_only"] == []
    assert [row["id"] for row in data["plans"]] == [1]
    assert data["plans"][0]["completed"] is False
    assert [phase["phase_type"] for phase in data["phases"]] == ["base"]
    assert data["races"] == [
        {"name": "Running Marathon", "race_date": "2026-06-01", "priority": "A"}
        | _PLAN_FACTS
    ]
    assert data["active_plan"]["id"] == 7
    # The window reaches the repository as ISO date strings.
    assert repos.plan.range_calls == [(7, "2026-02-09", "2026-02-15")]


def test_build_calendar_data_overlays_and_persists_intervals_pairings() -> None:
    """A paired activity completes its session, and the match is written back."""
    repos = _repos(_active_plan(), [_session(1, slot_id="s1")])
    client = _paired_client("s1")

    data = build_calendar_data(repos, client, *WINDOW)

    assert client.windows == [WINDOW]
    assert data["compliance_unavailable"] is False
    assert [activity["id"] for activity in data["activities"]] == ["i99"]
    assert data["plans"][0]["status"] == "completed"
    assert data["plans"][0]["matched_activity_id"] == "i99"
    assert data["plans"][0]["compliance_pct"] == 88.0
    assert repos.plan.match_updates == [
        [{"session_id": 1, "matched_activity_id": "i99", "status": "completed"}]
    ]


class _PairingClient(_Client):
    """Stub that also records pairing writes and answers them as recorded."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pairs: list[tuple[str, int]] = []

    def pair_activity(self, activity_id: str, event_id: int) -> IntervalsActivity:
        self.pairs.append((activity_id, event_id))
        return IntervalsActivity(id=activity_id, paired_event_id=event_id)


def test_build_calendar_data_remembers_a_pairing_it_made() -> None:
    """Persisted like any match, so an unpair on intervals.icu afterwards sticks."""
    repos = _repos(_active_plan(), [_session(1, slot_id="s1")])
    client = _PairingClient(
        activities=[
            IntervalsActivity(
                id="i99", type="Run", start_date_local="2026-02-10T07:00:00"
            )
        ],
        events=[
            IntervalsEvent(
                id=555,
                external_id="coachie:7:s1",
                type="Run",
                start_date_local="2026-02-10T00:00:00",
            )
        ],
    )

    data = build_calendar_data(repos, client, *WINDOW)

    assert client.pairs == [("i99", 555)]
    assert data["plans"][0]["status"] == "completed"
    assert repos.plan.match_updates == [
        [{"session_id": 1, "matched_activity_id": "i99", "status": "completed"}]
    ]


def test_build_calendar_data_never_persists_an_unchanged_or_overridden_match() -> None:
    """Rows already matched, or manually skipped, are not rewritten."""
    matched = _session(1, slot_id="s1", matched_activity_id="i99")
    repos = _repos(_active_plan(), [matched])
    build_calendar_data(repos, _paired_client("s1"), *WINDOW)

    skipped = _session(2, slot_id="s1", status="skipped")
    skipped_repos = _repos(_active_plan(), [skipped])
    data = build_calendar_data(skipped_repos, _paired_client("s1"), *WINDOW)

    assert repos.plan.match_updates == []
    assert skipped_repos.plan.match_updates == []
    assert data["plans"][0]["status"] == "skipped"


def test_build_calendar_data_survives_a_failed_match_write() -> None:
    """The payload is already right; a failed write-back is only logged."""
    repos = _repos(_active_plan(), [_session(1, slot_id="s1")], fail_writes=True)

    data = build_calendar_data(repos, _paired_client("s1"), *WINDOW)

    assert data["plans"][0]["matched_activity_id"] == "i99"
    assert data["compliance_unavailable"] is False


def test_build_calendar_data_degrades_when_intervals_icu_fails() -> None:
    """An unreachable API degrades to a plan-only calendar, never raises."""
    repos = _repos(_active_plan(), [_session(1)])

    data = build_calendar_data(
        repos, _Client(exc=ConnectionError("unreachable")), *WINDOW
    )

    assert data["compliance_unavailable"] is True
    assert data["activities"] == []
    assert data["plans"][0]["completed"] is False
    assert repos.plan.match_updates == []


def test_build_calendar_data_without_a_plan_still_lists_calendar_only_workouts() -> (
    None
):
    """Coach extras and a former plan's finished sessions render with no plan.

    Thresholds are read once for all of them.
    """
    repos = _repos()
    extra = IntervalsEvent(
        id=556,
        external_id="coachie:adhoc:2026-02-11:ab12cd34",
        start_date_local="2026-02-11T00:00:00",
        type="Run",
        name="Easy Run",
    )
    former = IntervalsEvent(
        id=557,
        external_id="coachie:3:w0_tue",
        start_date_local="2026-02-10T00:00:00",
        type="Run",
        name="Strides",
    )

    with patch(f"{MODULE}.get_planner_load_context", return_value=None) as source:
        data = build_calendar_data(repos, _Client(events=[extra, former]), *WINDOW)

    source.assert_called_once_with(repos)
    assert [(row["external_id"], row["status"]) for row in data["calendar_only"]] == [
        (extra.external_id, "adhoc"),
        (former.external_id, "missed"),
    ]
    assert data["plans"] == []
    assert data["phases"] == []
    assert data["races"] == []
    assert data["active_plan"] is None
    assert repos.plan.range_calls == []


def test_build_calendar_data_reads_a_broken_or_malformed_plan_as_none() -> None:
    """A failing plan read, or a row without an int id, is treated as no plan."""
    broken = SimpleNamespace(
        plan=SimpleNamespace(get_active=MagicMock(side_effect=RuntimeError("db")))
    )
    malformed = _repos(
        SimpleNamespace(id="7", plan_start="2026-01-05", goal_date="2026-06-01"),
        [_session(1)],
    )

    assert build_calendar_data(broken, None, *WINDOW)["active_plan"] is None
    data = build_calendar_data(malformed, None, *WINDOW)
    assert data["active_plan"] is None
    assert data["plans"] == []


def test_phase_ranges_fall_back_to_contiguous_session_weeks() -> None:
    """Without a stored phase calendar, blocks follow contiguous session weeks."""
    sessions = [
        SimpleNamespace(week_index=0, phase_type="base", date="2026-01-01"),
        SimpleNamespace(week_index=1, phase_type="base", date="2026-01-08"),
        SimpleNamespace(week_index=3, phase_type="base", date="2026-01-22"),
        SimpleNamespace(week_index=4, phase_type="build", date="2026-01-29"),
    ]

    phases = _build_phase_ranges(sessions)

    assert _build_phase_ranges([]) == []
    assert [
        (p["phase_type"], p["start_date"], p["end_date"], p["duration_weeks"])
        for p in phases
    ] == [
        ("base", "2026-01-01", "2026-01-08", 2),
        ("base", "2026-01-22", "2026-01-22", 1),
        ("build", "2026-01-29", "2026-01-29", 1),
    ]


def _table_race(**overrides: Any) -> SimpleNamespace:
    fields = {"name": "City 10K", "race_date": "2026-02-14", "priority": "C"}
    fields["status"] = "planned"
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_races_the_coach_entered_show_on_their_day() -> None:
    """A race from the race table shows on the calendar even with no plan;
    a cancelled one, one outside the window and a day the plan's own race
    already holds are left out."""
    repos = _repos(
        _active_plan(),
        races=[
            _table_race(),
            _table_race(name="Skipped 5K", race_date="2026-02-12", status="cancelled"),
            _table_race(name="Later Half", race_date="2026-03-01"),
            _table_race(name="Plan Marathon", race_date="2026-06-01", priority="A"),
        ],
    )

    data = build_calendar_data(repos, None, *WINDOW)

    assert data["races"] == [
        {"name": "Running Marathon", "race_date": "2026-06-01", "priority": "A"}
        | _PLAN_FACTS,
        {"name": "City 10K", "race_date": "2026-02-14", "priority": "C"} | _NO_FACTS,
    ]


def test_the_plan_race_takes_its_facts_from_the_race_table_row() -> None:
    repos = _repos(_active_plan())
    repos.races.get_by_date.return_value = _table_race(
        goal_type="road_marathon", distance_km=42.2, climb_m=120, notes="Bib at 8."
    )

    [race] = build_calendar_data(repos, None, *WINDOW)["races"]

    assert (race["goal_type"], race["distance_km"], race["climb_m"], race["notes"]) == (
        "road_marathon",
        42.2,
        120,
        "Bib at 8.",
    )


def test_a_failed_race_table_read_leaves_the_plan_race_bare() -> None:
    repos = _repos(_active_plan())
    repos.races.get_by_date.side_effect = RuntimeError("db")

    [race] = build_calendar_data(repos, None, *WINDOW)["races"]

    assert race["distance_km"] is None


def test_the_race_table_read_reaches_back_to_the_window_start() -> None:
    repos = _repos(races=[_table_race()])
    with patch(f"{MODULE}.clock.today", return_value=date(2026, 2, 20)):
        data = build_calendar_data(repos, None, *WINDOW)

    repos.races.list.assert_called_once_with(include_past_days=11)
    assert [race["name"] for race in data["races"]] == ["City 10K"]


def test_a_failed_race_table_read_keeps_the_plan_races() -> None:
    repos = _repos(_active_plan())
    repos.races.list.side_effect = RuntimeError("db down")

    data = build_calendar_data(repos, None, *WINDOW)

    assert [race["name"] for race in data["races"]] == ["Running Marathon"]


def test_races_come_from_the_plan_goal_except_for_maintenance() -> None:
    """A maintenance block has no race; an unknown goal still gets a card."""
    assert (
        _build_races(
            SimpleNamespace(goal_type="running_maintenance", goal_date="2026-09-27")
        )
        == []
    )
    assert _build_races(
        SimpleNamespace(goal_type="ironman", goal_date="2026-09-27")
    ) == [
        {
            "name": "Ironman",
            "race_date": "2026-09-27",
            "priority": "A",
            "goal_type": "ironman",
            "distance_km": None,
            "climb_m": None,
            "notes": None,
        }
    ]
    assert _build_races(SimpleNamespace(goal_type="Goal", goal_date="2026-09-27")) == [
        {
            "name": "Goal",
            "race_date": "2026-09-27",
            "priority": "A",
            "goal_type": "Goal",
            "distance_km": None,
            "climb_m": None,
            "notes": None,
        }
    ]


AD_HOC_ID = "coachie:adhoc:2026-02-11:ab12cd34"


def test_delete_ad_hoc_workout_deletes_the_one_event() -> None:
    client = MagicMock()
    client.bulk_delete_events.return_value = 1

    assert delete_ad_hoc_workout(AD_HOC_ID, client) == {"status": "ok", "deleted": 1}
    client.bulk_delete_events.assert_called_once_with([AD_HOC_ID])


def test_delete_ad_hoc_workout_refuses_plan_and_race_events() -> None:
    """Only the ad-hoc external_id scheme is in reach."""
    client = MagicMock()

    assert delete_ad_hoc_workout("coachie:7:s1", client) == {
        "status": "error",
        "message": "Not an ad-hoc workout.",
    }
    client.bulk_delete_events.assert_not_called()


def test_delete_ad_hoc_workout_without_intervals_icu_removes_nothing() -> None:
    result = delete_ad_hoc_workout(AD_HOC_ID, None)

    assert result["status"] == "error"
    assert "not configured" in result["message"]


def test_delete_ad_hoc_workout_reports_a_remote_failure_without_raising() -> None:
    client = MagicMock()
    client.bulk_delete_events.side_effect = ConnectionError("unreachable")

    result = delete_ad_hoc_workout(AD_HOC_ID, client)

    assert result["status"] == "error"
    assert "unreachable" in result["message"]
