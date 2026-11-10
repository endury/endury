"""Tests for the current-week adjustment (adjust_current_week)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.foundations.types import ActivityKey, GoalType
from endury.planning import adjust_week as mod
from endury.planning.adjust_week import (
    MAX_REDUCE_PERCENT,
    adjust_current_week,
    plan_week_adjustment,
)
from endury.planning.commit_plan import (
    PUSH_SKIPPED_MESSAGE,
    build_plan_events,
    reschedule_session,
)
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.replan_window import next_monday
from endury.planning.wire.requests import ScheduleRequest
from endury.planning.wire.sessions import build_commit_session_rows
from endury.planning.wire.workout_steps import _headline_duration_minutes
from endury.training.load import LoadMetricContext

_PLAN_ID = 7
_PLAN_START = "2026-03-09"  # a Monday
_RACE = "2026-06-14"  # a Sunday
_TODAY = date(2026, 5, 20)  # a Wednesday
_SUNDAY = date(2026, 5, 24)

RESOLVER = LoadMetricContext(
    running_threshold_pace_min_per_km=4.5,
    running_lthr_bpm=170,
    cycling_ftp_watts=250.0,
    cycling_lthr_bpm=160,
    swim_css_sec_per_100m=95.0,
    threshold_updated_at=date(2026, 1, 1),
)

_ATHLETE_STATE = {
    "thresholds": {
        "running": {"threshold_pace_sec_per_km": 270, "lthr_bpm": 170},
        "cycling": {"ftp_watts": 250},
        "swimming": {"css_sec_per_100m": 95},
    },
    "sports": {},
}


@pytest.fixture(autouse=True)
def _registry() -> None:
    ensure_registry_loaded()


@pytest.fixture(autouse=True)
def _resolver(monkeypatch) -> None:
    """Live thresholds are read from intervals.icu; stub them offline."""
    monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: RESOLVER)


def _payload_for(activity_key: str, minutes: float, day: str, slot: str) -> dict:
    """Materialize one session exactly as a commit would have."""
    activity = get_activity(ActivityKey(activity_key))
    proto = SimpleNamespace(
        slot_id=slot,
        date=day,
        week_index=10,
        phase_type="build",
        sport=activity.sport.value,
        activity_key=activity_key,
        family=activity.family.value,
        duration_target_minutes=minutes,
        duration_minutes=None,
        workout_json="{}",
    )
    return dict(
        mod._rebuild(proto, {}, activity, 1.0, "", RESOLVER, GoalType.RUNNING_MARATHON)
    )


def _row(
    key: str,
    minutes: float,
    day: str,
    *,
    session_id: int = 501,
    slot: str | None = None,
    **overrides: object,
) -> SimpleNamespace:
    """A persisted plan_sessions row committed at ``minutes`` of plan intent."""
    slot = slot or f"w10_{key}_{session_id}"
    payload = _payload_for(key, minutes, day, slot)
    insert = build_commit_session_rows([payload])[0]
    base: dict[str, object] = {
        "committed_payload": payload,
        "id": session_id,
        "plan_id": _PLAN_ID,
        "slot_id": insert.slot_id,
        "date": insert.date,
        "week_index": insert.week_index,
        "phase_type": insert.phase_type,
        "sport": insert.sport,
        "activity_key": insert.activity_key,
        "family": insert.family,
        "target_tss": insert.target_tss,
        "achieved_tss": insert.achieved_tss,
        "duration_minutes": insert.duration_minutes,
        "workout_json": insert.workout_json,
        "duration_target_minutes": minutes,
        "tid_fractions_json": insert.tid_fractions_json,
        "matched_activity_id": None,
        "status": "pending",
        "pushed_event_json": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _solve(rows: list[SimpleNamespace], *, races: list[dict] | None = None) -> dict:
    """The persisted solve payload whose entries twin ``rows``."""
    return {
        "sessions": [row.committed_payload for row in rows],
        "phase_calendar": ["build"] * 20,
        "weekly_summary": [],
        "projections": {"aggregate_ctl": [1.0, 2.0]},
        "macro_plan": {"weeks": [{"week_index": 0}]},
        "request": {
            "races": races
            if races is not None
            else [
                {
                    "goal_type": "running_marathon",
                    "race_date": _RACE,
                    "priority": "A",
                }
            ],
            "training_start": _PLAN_START,
            "ambition_level": "moderate",
        },
    }


def _plan(solve: dict, **overrides: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "id": _PLAN_ID,
        "name": "Marathon Build",
        "goal_type": "running_marathon",
        "goal_date": _RACE,
        "plan_start": _PLAN_START,
        "status": "active",
        "solver_version": "planner_v2_session_menu_v1",
        "solve_result_json": json.dumps(solve),
        "athlete_state_json": json.dumps(_ATHLETE_STATE),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _harness(
    rows: list[SimpleNamespace],
    *,
    solve: dict | None = None,
    plan: SimpleNamespace | None = None,
    with_client: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return ``(parent, repos, client)`` sharing one call recorder."""
    solve = solve if solve is not None else _solve(rows)
    parent = MagicMock()
    repos = parent.repos
    client = parent.client if with_client else None
    repos.plan.get_active.return_value = plan if plan is not None else _plan(solve)
    repos.plan.get_sessions_by_date_range.side_effect = lambda _plan_id, start, end: [
        row for row in rows if start <= str(row.date) <= end
    ]
    return parent, repos, client


def _call_names(parent: MagicMock) -> list[str]:
    return [entry[0] for entry in parent.mock_calls]


# Input validation


class TestResolveFactor:
    def test_missing_reduce_percent_is_refused(self):
        result = adjust_current_week(
            repos=MagicMock(),
            reduce_percent=None,
            confirm=True,
            client=None,
        )
        assert result == {
            "status": "error",
            "message": (
                "Pass reduce_percent — a whole percent between 0 and 60; "
                "0 restores the week."
            ),
        }

    def test_a_negative_percent_is_refused(self):
        result = adjust_current_week(
            repos=MagicMock(),
            reduce_percent=-1,
            confirm=True,
            client=None,
        )
        assert result["status"] == "error"
        assert "only shortens" in result["message"]

    def test_above_the_cap_routes_to_skipping_instead(self):
        result = adjust_current_week(
            repos=MagicMock(),
            reduce_percent=MAX_REDUCE_PERCENT + 1,
            confirm=True,
            client=None,
        )
        assert result["status"] == "error"
        assert "skip sessions instead" in result["message"]


# The window


class TestWindow:
    @pytest.mark.parametrize(
        "today",
        [date(2026, 5, 18), date(2026, 5, 20), date(2026, 5, 23), date(2026, 5, 24)],
        ids=["monday", "wednesday", "saturday", "sunday"],
    )
    def test_the_window_is_today_through_this_sunday(self, today):
        rows = [
            _row("run_easy", 60.0, str(today - timedelta(days=1)), session_id=1),
            _row("run_easy", 60.0, str(today), session_id=2),
            _row("run_easy", 60.0, "2026-05-24", session_id=3),
            _row("run_easy", 60.0, str(next_monday(today)), session_id=4),
        ]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=today,
        )
        sunday = next_monday(today) - timedelta(days=1)
        assert result["window"] == {
            "from": today.isoformat(),
            "through": sunday.isoformat(),
        }
        touched = {entry["session_id"] for entry in result["sessions"]}
        assert 1 not in touched
        assert 4 not in touched
        assert 2 in touched
        written = {
            call.args[0]
            for call in repos.plan.update_session_prescription.call_args_list
        }
        assert written == touched

    def test_a_through_date_overrides_the_sunday(self):
        """``pause_training`` trims its first week back past this Sunday."""
        through = date(2026, 5, 28)  # the Thursday after _TODAY's week
        rows = [
            _row("run_easy", 60.0, str(_SUNDAY), session_id=1),
            _row("run_easy", 60.0, str(through), session_id=2),
            _row("run_easy", 60.0, str(through + timedelta(days=1)), session_id=3),
        ]
        _, repos, _ = _harness(rows)
        adjustment = plan_week_adjustment(
            repos=repos,
            factor=0.6,
            reason_phrase="at your request",
            today=_TODAY,
            through=through,
        )
        assert adjustment.window == (_TODAY, through)
        assert [change.session_id for change in adjustment.changes] == [1, 2]


# Which sessions this verb may not rewrite


class TestUntouchable:
    @pytest.mark.parametrize(
        ("overrides", "reason"),
        [
            ({"status": "completed"}, "already completed"),
            ({"matched_activity_id": "act-9"}, "already completed"),
            ({"status": "skipped"}, "already marked skipped"),
            (
                {"activity_key": "run_from_a_retired_engine"},
                "unknown workout type — this plan was built by a retired engine",
            ),
            (
                {"duration_target_minutes": None, "duration_minutes": None},
                "no duration on record",
            ),
        ],
    )
    def test_each_reason_is_named(self, overrides, reason):
        row = _row("run_easy", 60.0, "2026-05-21", **overrides)
        _, repos, _ = _harness([row])
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["untouched"] == [
            {
                "session_id": 501,
                "date": str(row.date),
                "title": result["untouched"][0]["title"],
                "reason": reason,
            }
        ]
        repos.plan.update_session_prescription.assert_not_called()

    def test_an_unknown_activity_key_still_names_the_session(self):
        row = _row("run_easy", 60.0, "2026-05-21", activity_key="run_from_the_past")
        _, repos, _ = _harness([row])
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=False,
            client=None,
            today=_TODAY,
        )
        assert result["untouched"][0]["title"] == "run_from_the_past"

    @pytest.mark.parametrize("priority", ["A", "B"])
    def test_a_race_day_inside_the_window_is_excluded(self, priority):
        row = _row("run_easy", 60.0, "2026-05-21")
        goal = "running_marathon" if priority == "A" else "running_10k"
        solve = _solve(
            [row],
            races=[
                {"goal_type": goal, "race_date": "2026-05-21", "priority": priority}
            ],
        )
        _, repos, _ = _harness([row], solve=solve)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["untouched"][0]["reason"] == "race day"
        repos.plan.update_session_prescription.assert_not_called()

    def test_a_maintenance_block_end_is_still_adjustable(self):
        row = _row("run_easy", 60.0, "2026-05-21")
        solve = _solve(
            [row],
            races=[
                {
                    "goal_type": "running_maintenance",
                    "race_date": "2026-05-21",
                    "priority": "A",
                }
            ],
        )
        _, repos, _ = _harness([row], solve=solve)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert [entry["session_id"] for entry in result["sessions"]] == [501]

    def test_a_slot_missing_from_the_solve_payload_is_excluded(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        solve = _solve(rows)
        solve["sessions"] = []
        _, repos, _ = _harness(rows, solve=solve)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert "regenerate the plan" in result["untouched"][0]["reason"]
        repos.plan.update_solve_result_json.assert_not_called()

    def test_a_coach_extra_row_is_rescaled_like_any_other(self):
        """An extra is a plan row, so this week's trim has to include it."""
        rows = [
            _row("run_easy", 60.0, "2026-05-21"),
            _row(
                "run_easy",
                30.0,
                "2026-05-22",
                session_id=502,
                slot="x1:extra_2026-05-22_run_easy",
            ),
        ]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert {entry["session_id"] for entry in result["sessions"]} == {501, 502}

    def test_an_unreadable_solve_payload_yields_an_empty_window(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows, plan=_plan(_solve(rows), solve_result_json="{{"))
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["status"] == "empty"
        repos.plan.update_session_prescription.assert_not_called()

    def test_no_active_plan_is_an_error(self):
        repos = MagicMock()
        repos.plan.get_active.return_value = None
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result == {"status": "error", "message": NO_ACTIVE_PLAN_MESSAGE}


# The rebuild itself


class TestRebuild:
    @pytest.mark.parametrize("activity_key", ["run_threshold", "bike_long"])
    def test_the_rebuild_is_the_planners_own_hours_to_tss_bridge(self, activity_key):
        rows = [_row(activity_key, 90.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        activity = get_activity(ActivityKey(activity_key))
        expected_tss = assignment_target_tss(activity, 90.0 * 0.6 / 60.0, RESOLVER)
        entry = result["sessions"][0]
        assert entry["after_tss"] == round(expected_tss, 1)
        written = repos.plan.update_session_prescription.call_args
        # The minutes quoted back are the rebuilt session's own step sum.
        assert entry["after_minutes"] == _headline_duration_minutes(
            json.loads(written.kwargs["workout_json"])["steps"]
        )
        assert written.kwargs["achieved_tss"] == round(
            activity.achieved_tss(expected_tss, resolver=RESOLVER), 1
        )
        assert activity.tss_min <= expected_tss <= activity.tss_max

    def test_the_trim_sentence_reaches_the_stored_intent(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        stored = json.loads(
            repos.plan.update_session_prescription.call_args.kwargs["workout_json"]
        )
        assert (
            "Trimmed to 60% of plan this week at your request."
            in (stored["coaching_intent"])
        )
        assert stored["coaching_intent"].startswith("Fully conversational pace")

    def test_the_why_survives_the_rescale_and_the_notes_are_recomputed(self):
        """A rescale changes how long, never why — but the cues follow the size."""
        row = _row("bike_long", 300.0, "2026-05-21")
        stored_workout = json.loads(row.workout_json)
        stored_workout["why_details"] = ["Because the long ride is the plan."]
        row.workout_json = json.dumps(stored_workout)
        _, repos, _ = _harness([row])

        adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )

        written = json.loads(
            repos.plan.update_session_prescription.call_args.kwargs["workout_json"]
        )
        assert written["why_details"] == ["Because the long ride is the plan."]
        assert any("Fueling" in note for note in written["coaching_notes"])

    def test_a_row_without_the_new_keys_rebuilds_with_an_empty_why(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        assert "why_details" not in json.loads(rows[0].workout_json)
        _, repos, _ = _harness(rows)

        adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )

        written = json.loads(
            repos.plan.update_session_prescription.call_args.kwargs["workout_json"]
        )
        assert "why_details" not in written

    def test_the_noop_check_ignores_the_description_fields(self):
        """A row whose only difference is stored prose is still a no-op."""
        row = _row("bike_ftp_test", 60.0, "2026-05-21")
        stored = json.loads(row.workout_json)
        stored["why_details"] = ["Stored on commit, absent from a fresh rebuild."]
        stored["coaching_notes"] = ["<b>Stale:</b> also stored."]
        row.workout_json = json.dumps(stored)
        _, repos, _ = _harness([row])

        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )

        assert repos.plan.update_session_prescription.call_count == 0
        assert "fixed test protocol" in json.dumps(result)

    def test_the_planners_hours_space_intent_is_never_rewritten(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert (
            "duration_target_minutes"
            not in repos.plan.update_session_prescription.call_args.kwargs
        )
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        assert mirrored["sessions"][0]["duration_target_minutes"] == 60.0


class TestNoOpFilter:
    def test_a_fixed_test_protocol_is_left_alone(self):
        rows = [_row("swim_css_test", 45.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["unchanged"] == [
            {
                "session_id": 501,
                "date": "2026-05-21",
                "title": result["unchanged"][0]["title"],
                "reason": "fixed test protocol — its length is the test",
            }
        ]
        assert result["sessions"] == []
        repos.plan.update_session_prescription.assert_not_called()

    def test_a_session_already_on_its_floor_is_left_alone(self):
        # run_threshold's rep lattice bottoms out well above 70% of 42 min.
        rows = [_row("run_threshold", 42.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=30,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["unchanged"][0]["reason"] == "already at its shortest"
        repos.plan.update_session_prescription.assert_not_called()


class TestIdempotenceAndRestore:
    def test_a_second_deeper_trim_is_measured_from_the_plan_not_the_last_trim(self):
        def _apply_percent(percent: int, current: SimpleNamespace) -> SimpleNamespace:
            _, repos, _ = _harness([current])
            adjust_current_week(
                repos=repos,
                reduce_percent=percent,
                confirm=True,
                client=None,
                today=_TODAY,
            )
            kwargs = repos.plan.update_session_prescription.call_args.kwargs
            return SimpleNamespace(
                **{
                    **vars(current),
                    "target_tss": kwargs["target_tss"],
                    "achieved_tss": kwargs["achieved_tss"],
                    "duration_minutes": kwargs["duration_minutes"],
                    "workout_json": kwargs["workout_json"],
                    "tid_fractions_json": kwargs["tid_fractions_json"],
                }
            )

        committed = _row("run_easy", 60.0, "2026-05-21")
        stacked = _apply_percent(50, _apply_percent(30, committed))
        once = _apply_percent(50, _row("run_easy", 60.0, "2026-05-21"))
        assert stacked.duration_minutes == once.duration_minutes == 30.0
        assert stacked.target_tss == once.target_tss

        _, repos, _ = _harness([stacked])
        adjust_current_week(
            repos=repos,
            reduce_percent=0,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        restored = repos.plan.update_session_prescription.call_args.kwargs
        assert restored["duration_minutes"] == committed.duration_minutes
        assert restored["target_tss"] == committed.target_tss
        assert restored["workout_json"] == committed.workout_json
        assert stacked.duration_target_minutes == 60.0

    def test_a_zero_percent_call_on_an_untrimmed_week_writes_nothing(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=0,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["status"] == "empty"
        assert result["unchanged"][0]["reason"] == "already at its shortest"
        repos.plan.update_session_prescription.assert_not_called()


# The resolver gate


class TestResolverGate:
    def test_missing_thresholds_refuse_a_plan_that_was_built_with_them(
        self, monkeypatch
    ):
        monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: None)
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, client = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["status"] == "error"
        assert "strip your pace and power targets" in result["message"]
        repos.plan.update_session_prescription.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_a_threshold_less_plan_proceeds_without_a_resolver(self, monkeypatch):
        monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: None)
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        plan = _plan(_solve(rows), athlete_state_json=json.dumps({"thresholds": {}}))
        _, repos, _ = _harness(rows, plan=plan)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["status"] == "ok"
        repos.plan.update_session_prescription.assert_called_once()


# The confirm gate


class TestConfirmGate:
    def test_a_preview_writes_nothing_and_pushes_nothing(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, client = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=False,
            client=client,
            today=_TODAY,
        )
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["push"] is None
        repos.plan.update_session_prescription.assert_not_called()
        repos.plan.update_solve_result_json.assert_not_called()
        repos.plan.update_pushed_events.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_the_preview_reports_exactly_what_the_apply_writes(self):
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("bike_long", 180.0, "2026-05-23", session_id=2),
            _row("swim_css_test", 45.0, "2026-05-22", session_id=3),
        ]
        _, preview_repos, _ = _harness(rows)
        preview = adjust_current_week(
            repos=preview_repos,
            reduce_percent=40,
            confirm=False,
            client=None,
            today=_TODAY,
        )
        _, apply_repos, _ = _harness(rows)
        applied = adjust_current_week(
            repos=apply_repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert preview["sessions"] == applied["sessions"]
        assert preview["totals"] == applied["totals"]
        assert preview["unchanged"] == applied["unchanged"]


# What gets persisted


class TestSolveMirror:
    def test_the_row_and_the_solve_entry_are_written_from_one_rebuild(self):
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("run_easy", 45.0, "2026-05-23", session_id=2),
        ]
        solve = _solve(rows)
        _, repos, _ = _harness(rows, solve=solve)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        by_slot = {entry["slot_id"]: entry for entry in mirrored["sessions"]}
        for change, row in zip(result["sessions"], rows, strict=True):
            entry = by_slot[str(row.slot_id)]
            assert entry["duration_minutes"] == change["after_minutes"]
            assert entry["target_tss"] == change["after_tss"]
            assert entry["date"] == str(row.date)
            assert entry["week_index"] == row.week_index
            written = next(
                call
                for call in repos.plan.update_session_prescription.call_args_list
                if call.args[0] == change["session_id"]
            )
            assert (
                json.loads(written.kwargs["workout_json"])["steps"]
                == (entry["workout"]["steps"])
            )

        assert mirrored["projections"] == solve["projections"]
        assert mirrored["macro_plan"] == solve["macro_plan"]
        week = next(w for w in mirrored["weekly_summary"] if w["week_index"] == 10)
        assert week["total_tss"] == pytest.approx(
            sum(entry["after_tss"] for entry in result["sessions"]), abs=0.2
        )


class TestPushPaths:
    def test_the_intended_event_is_stored_before_the_network_call(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        parent, repos, client = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["push"]["status"] == "ok"
        names = _call_names(parent)
        assert names.index("repos.plan.update_pushed_events") < names.index(
            "client.bulk_upsert_events"
        )
        events = client.bulk_upsert_events.call_args.args[0]
        assert [event["external_id"] for event in events] == [
            f"coachie:{_PLAN_ID}:{rows[0].slot_id}"
        ]
        assert all(event["category"] == "WORKOUT" for event in events)

    def test_a_failed_push_keeps_the_local_write_and_the_intended_event(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, client = _harness(rows)
        client.bulk_upsert_events.side_effect = RuntimeError("502")
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["push"]["status"] == "error"
        repos.plan.update_session_prescription.assert_called_once()
        stored = json.loads(
            repos.plan.update_pushed_events.call_args.args[1][str(rows[0].slot_id)]
        )
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        expected = build_plan_events(
            _PLAN_ID, [mirrored["sessions"][0]], [], _ATHLETE_STATE
        )[0]
        assert stored == expected

    def test_without_a_client_nothing_is_pre_stored(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows, with_client=False)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["push"]["status"] == "skipped"
        assert result["push"]["message"] == PUSH_SKIPPED_MESSAGE
        repos.plan.update_pushed_events.assert_not_called()
        repos.plan.update_session_prescription.assert_called_once()


class TestPushedWorkoutMatchesTheStoredOne:
    def test_the_dsl_describes_the_trimmed_session(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        before_event = build_plan_events(
            _PLAN_ID, _solve(rows)["sessions"], [], _ATHLETE_STATE
        )[0]
        _, repos, client = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        event = client.bulk_upsert_events.call_args.args[0][0]
        assert event["description"] != before_event["description"]

        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        entry = mirrored["sessions"][0]
        assert (
            (build_plan_events(_PLAN_ID, [entry], [], _ATHLETE_STATE)[0]["description"])
            == event["description"]
        )
        steps = entry["workout"]["steps"]
        assert steps[0]["type"] == "warmup"
        assert steps[-1]["type"] == "cooldown"
        # The headline reads 36, matching the DSL's own step sum above, not
        # a nearest-5 rounding of it (35).
        assert result["sessions"][0]["after_minutes"] == 36.0
        assert result["sessions"][0]["after_minutes"] == _headline_duration_minutes(
            steps
        )


# The server-authored reply text


class TestContractStrings:
    def test_the_preview_says_nothing_has_changed_yet(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=False,
            client=None,
            today=_TODAY,
        )
        assert result["message"] == (
            "Nothing has changed yet. The athlete applies these exact changes "
            "from their card; relay what it shows."
        )
        assert result["caveats"][0] == (
            "Only 2026-05-20 through 2026-05-24 changed. Next week is "
            "untouched — a replan regenerates from next Monday and leaves "
            "this week alone."
        )

    def test_the_applied_message_names_the_count_and_the_window(self):
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("run_easy", 60.0, "2026-05-22", session_id=2),
        ]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["message"] == (
            "Applied to 2 session(s) from 2026-05-20 through 2026-05-24."
        )
        assert result["confirm_required"] is False

    def test_a_plan_that_has_not_started_warns_a_replan_would_discard_this(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        plan = _plan(_solve(rows), plan_start="2026-06-01")
        _, repos, _ = _harness(rows, plan=plan)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=False,
            client=None,
            today=_TODAY,
        )
        assert (
            "This plan has not started yet, so a replan would rewrite from the "
            "plan start and discard this adjustment."
        ) in result["caveats"]

    def test_a_failed_push_says_the_calendar_still_shows_the_old_workouts(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, client = _harness(rows)
        client.bulk_upsert_events.side_effect = RuntimeError("502")
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["push"]["message"] in result["caveats"]
        assert (
            "Your intervals.icu calendar still shows the previous workouts "
            "until the push is retried."
        ) in result["caveats"]

    def test_an_empty_window_points_at_the_replan_verb(self):
        _, repos, client = _harness([])
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=client,
            today=_TODAY,
        )
        assert result["status"] == "empty"
        assert "Use replan_active_plan" in result["message"]
        assert result["totals"] == {
            "sessions": 0,
            "before_minutes": 0.0,
            "after_minutes": 0.0,
            "actual_percent": 0.0,
        }
        repos.plan.update_session_prescription.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_the_realized_reduction_is_reported_not_the_requested_one(self):
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("swim_css_test", 45.0, "2026-05-22", session_id=2),
        ]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        totals = result["totals"]
        assert totals["sessions"] == 2
        assert totals["actual_percent"] < 40
        assert totals["actual_percent"] == pytest.approx(
            (totals["before_minutes"] - totals["after_minutes"])
            / totals["before_minutes"]
            * 100,
            abs=0.05,
        )


class TestCoachContext:
    def test_the_coach_summary_is_rebuilt_from_the_mirrored_week(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        coach = SimpleNamespace(runtime=SimpleNamespace(active_plan_summary="stale"))
        adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            maybe_get_coach=lambda: coach,
            today=_TODAY,
        )
        assert coach.runtime.active_plan_summary != "stale"

    def test_no_coach_accessor_is_fine(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["status"] == "ok"


class TestPureEntryPoint:
    def test_plan_week_adjustment_performs_no_writes(self):
        rows = [_row("run_easy", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        adjustment = plan_week_adjustment(
            repos=repos, factor=0.6, reason_phrase="at your request", today=_TODAY
        )
        assert adjustment.window == (_TODAY, _SUNDAY)
        assert [change.session_id for change in adjustment.changes] == [501]
        assert adjustment.changes[0].is_hard is False
        repos.plan.update_session_prescription.assert_not_called()


# Quantization: the realized reduction is never the requested one


class TestSwimQuantization:
    """A swim's length is a rep count, so it cannot land on an arbitrary %."""

    def test_a_trimmed_swim_reports_what_the_pool_grid_allowed(self):
        rows = [_row("swim_endurance", 60.0, "2026-05-21")]
        _, repos, _ = _harness(rows)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        entry = result["sessions"][0]
        assert entry["after_minutes"] < entry["before_minutes"]
        totals = result["totals"]
        # The request was 40%; what the reply carries is the 41.7% the rebuild
        # actually achieved on the rep lattice.
        assert result["reduce_percent"] == 40
        assert totals["actual_percent"] != 40
        assert totals["actual_percent"] == pytest.approx(
            (totals["before_minutes"] - totals["after_minutes"])
            / totals["before_minutes"]
            * 100,
            abs=0.05,
        )
        # R-DU-003: the session still totals a whole number of hundred metres.
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        distance = mirrored["sessions"][0]["distance_meters"]
        assert distance
        assert distance % 100 == 0


# Race week, legacy rows, and the two verbs this one composes with


class TestRaceWeek:
    def test_only_race_day_itself_is_off_limits(self):
        # An athlete ill in race week is exactly who needs this verb.
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("run_easy", 40.0, "2026-05-24", session_id=2),
        ]
        solve = _solve(
            rows,
            races=[
                {
                    "goal_type": "running_marathon",
                    "race_date": "2026-05-24",
                    "priority": "A",
                }
            ],
        )
        _, repos, _ = _harness(rows, solve=solve)
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert [entry["session_id"] for entry in result["sessions"]] == [1]
        assert [note["session_id"] for note in result["untouched"]] == [2]
        assert result["untouched"][0]["reason"] == "race day"


class TestLegacyRowWithoutThePlanIntent:
    """Committed before ``duration_target_minutes`` existed: fall back."""

    def _pair(self) -> tuple[SimpleNamespace, SimpleNamespace]:
        """Two rows 35 min long — one carrying a 60 min plan intent, one not."""
        modern = _row("run_easy", 35.0, "2026-05-21")
        modern.duration_target_minutes = 60.0
        legacy = _row("run_easy", 35.0, "2026-05-21")
        legacy.duration_target_minutes = None
        assert modern.duration_minutes == legacy.duration_minutes == 35.0
        return modern, legacy

    def _trim(self, row: SimpleNamespace, percent: int) -> tuple[dict, MagicMock]:
        _, repos, _ = _harness([row])
        result = adjust_current_week(
            repos=repos,
            reduce_percent=percent,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        return result, repos

    def test_the_scaling_base_falls_back_to_the_materialized_duration(self):
        # Same current length, two different bases: the modern row halves its
        # 60-minute plan intent, the legacy row its own 35 minutes.
        modern, legacy = self._pair()
        modern_result, _ = self._trim(modern, 50)
        legacy_result, _ = self._trim(legacy, 50)
        activity = get_activity(ActivityKey("run_easy"))
        assert modern_result["sessions"][0]["after_tss"] == round(
            assignment_target_tss(activity, 60.0 * 0.5 / 60.0, RESOLVER), 1
        )
        assert legacy_result["sessions"][0]["after_tss"] == round(
            assignment_target_tss(activity, 35.0 * 0.5 / 60.0, RESOLVER), 1
        )
        assert (
            legacy_result["sessions"][0]["after_minutes"]
            < modern_result["sessions"][0]["after_minutes"]
        )

    def test_a_restore_returns_to_the_last_adjusted_size_not_the_original(self):
        # The documented cost of the fallback: with no plan intent on record
        # 0% restores the row to its own current 35 min, not the 60 it was
        # first prescribed at — so it is a no-op, not a restore.
        _, legacy = self._pair()
        result, repos = self._trim(legacy, 0)
        assert result["status"] == "empty"
        repos.plan.update_session_prescription.assert_not_called()


class TestConcurrentCompletion:
    """Nothing is reserved between the preview and the apply."""

    def test_a_session_completed_after_the_preview_drops_out_of_the_apply(self):
        rows = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1),
            _row("bike_long", 150.0, "2026-05-23", session_id=2),
        ]
        _, preview_repos, _ = _harness(rows)
        preview = adjust_current_week(
            repos=preview_repos,
            reduce_percent=40,
            confirm=False,
            client=None,
            today=_TODAY,
        )
        assert [entry["session_id"] for entry in preview["sessions"]] == [1, 2]

        # The athlete trains and uploads the Thursday run before confirming.
        late = [
            _row("run_easy", 60.0, "2026-05-21", session_id=1, status="completed"),
            rows[1],
        ]
        _, apply_repos, _ = _harness(late)
        applied = adjust_current_week(
            repos=apply_repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert [entry["session_id"] for entry in applied["sessions"]] == [2]
        assert applied["untouched"] == [
            {
                "session_id": 1,
                "date": "2026-05-21",
                "title": applied["untouched"][0]["title"],
                "reason": "already completed",
            }
        ]
        # The applied totals are the honest ones, and only the session that
        # was still adjustable at apply time was written.
        assert applied["totals"] != preview["totals"]
        assert [
            call.args[0]
            for call in apply_repos.plan.update_session_prescription.call_args_list
        ] == [2]


class TestComposesWithAMove:
    """``move_planned_session`` and this verb share one solve payload."""

    def _moved_row(self) -> SimpleNamespace:
        """A row re-dated to Saturday whose solve twin still says Thursday."""
        row = _row("run_easy", 60.0, "2026-05-21")
        row.date = "2026-05-23"
        row.week_index = 11
        return row

    def test_a_moved_session_is_rebuilt_on_the_row_s_date(self):
        row = self._moved_row()
        _, repos, _ = _harness([row])
        result = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        assert result["sessions"][0]["date"] == "2026-05-23"
        assert result["sessions"][0]["weekday"] == "Sat"
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        entry = mirrored["sessions"][0]
        assert (entry["date"], entry["week_index"]) == ("2026-05-23", 11)

    def test_a_move_after_a_trim_keeps_the_trimmed_prescription(self):
        row = _row("run_easy", 60.0, "2026-05-21")
        _, repos, _ = _harness([row])
        trimmed = adjust_current_week(
            repos=repos,
            reduce_percent=40,
            confirm=True,
            client=None,
            today=_TODAY,
        )
        after_trim = json.loads(repos.plan.update_solve_result_json.call_args.args[1])

        # Now move it, reading the plan back exactly as the trim left it.
        row.duration_minutes = trimmed["sessions"][0]["after_minutes"]
        row.date = "2026-05-22"
        move_repos = MagicMock()
        move_repos.plan.get_active.return_value = _plan(
            after_trim, solve_result_json=json.dumps(after_trim)
        )
        move_repos.plan.get_sessions.return_value = [row]
        assert (
            reschedule_session(
                row.id,
                ScheduleRequest(date="2026-05-22"),
                get_repos=lambda: move_repos,
                today=_TODAY,
                client=None,
            )["status"]
            == "ok"
        )

        moved = json.loads(move_repos.plan.update_solve_result_json.call_args.args[1])
        entry = moved["sessions"][0]
        assert entry["date"] == "2026-05-22"
        assert entry["duration_minutes"] == trimmed["sessions"][0]["after_minutes"]
        assert entry["target_tss"] == trimmed["sessions"][0]["after_tss"]
        assert "Trimmed to 60% of plan this week" in entry["coaching_intent"]
