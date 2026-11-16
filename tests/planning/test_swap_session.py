"""Tests for the single-session swap (swap_planned_session)."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import ActivityKey, GoalType
from endury.planning import adjust_week as adjust_mod
from endury.planning import swap_session as mod
from endury.planning.commit_plan import (
    PUSH_SKIPPED_MESSAGE,
    build_plan_events,
    reschedule_session,
)
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from endury.planning.swap_session import (
    WEEK_DELTA_WARN_PERCENT,
    plan_session_swap,
    swap_planned_session,
)
from endury.planning.wire.requests import ScheduleRequest
from endury.planning.wire.sessions import build_commit_session_rows
from endury.training.load import LoadMetricContext

_PLAN_ID = 7
_PLAN_START = "2026-03-09"  # a Monday
_RACE = "2026-06-14"  # a Sunday
_TODAY = date(2026, 5, 20)  # a Wednesday
_MONDAY = "2026-05-18"
_SUNDAY = "2026-05-24"
_THURSDAY = "2026-05-21"

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
        adjust_mod._rebuild(
            proto, {}, activity, 1.0, "", RESOLVER, GoalType.RUNNING_MARATHON
        )
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
    repos.plan.get_sessions.return_value = rows
    return parent, repos, client


def _swap(repos, client=None, **kwargs) -> dict:
    """Call the verb with this suite's defaults filled in."""
    kwargs.setdefault("session_id", 501)
    kwargs.setdefault("intent", "threshold")
    return swap_planned_session(repos=repos, client=client, today=_TODAY, **kwargs)


def _call_names(parent: MagicMock) -> list[str]:
    return [entry[0] for entry in parent.mock_calls]


def _big_week() -> list[SimpleNamespace]:
    """A week heavy enough that one 60-minute swap stays under the warning."""
    return [
        _row("run_easy", 60.0, _THURSDAY),
        *[
            _row("bike_long", 180.0, day, session_id=600 + index)
            for index, day in enumerate([_MONDAY, "2026-05-19", "2026-05-22", _SUNDAY])
        ],
    ]


# Guards: nothing is written and the reason is named


class TestGuards:
    def test_no_active_plan(self):
        repos = MagicMock()
        repos.plan.get_active.return_value = None
        result = _swap(repos)
        assert result == {"status": "error", "message": NO_ACTIVE_PLAN_MESSAGE}

    def test_a_session_outside_the_active_plan(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, session_id=999)
        assert result["status"] == "error"
        assert "999 is not part of the active plan" in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    @pytest.mark.parametrize(
        "overrides",
        [{"status": "completed"}, {"matched_activity_id": "act-9"}],
        ids=["completed", "matched"],
    )
    def test_a_delivered_session_is_not_rewritten(self, overrides):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY, **overrides)])
        result = _swap(repos)
        assert result["status"] == "error"
        assert "already done" in result["message"]
        assert "unlinked" in result["message"]
        assert "skip_planned_session" in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    def test_a_past_session_is_not_rewritten(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, "2026-05-19")])
        result = _swap(repos)
        assert result["status"] == "error"
        assert "has passed" in result["message"]
        assert "skipped" in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    def test_todays_session_is_still_swappable(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _TODAY.isoformat())])
        assert _swap(repos)["status"] == "preview"

    def test_a_taper_week_is_refused(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY, phase_type="taper")])
        result = _swap(repos)
        assert result["status"] == "error"
        assert result["guards"] == ["taper week"]
        assert "allow_protected=true" in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    def test_a_race_week_is_refused(self):
        row = _row("run_easy", 60.0, _THURSDAY)
        solve = _solve(
            [row],
            races=[{"goal_type": "running_10k", "race_date": _SUNDAY, "priority": "B"}],
        )
        _, repos, _ = _harness([row], solve=solve)
        result = _swap(repos)
        assert result["guards"] == ["race week"]
        repos.plan.update_session_swap.assert_not_called()

    def test_a_maintenance_block_end_is_not_a_race_week(self):
        row = _row("run_easy", 60.0, _THURSDAY)
        solve = _solve(
            [row],
            races=[
                {
                    "goal_type": "running_maintenance",
                    "race_date": _SUNDAY,
                    "priority": "A",
                }
            ],
        )
        _, repos, _ = _harness([row], solve=solve)
        result = _swap(repos)
        assert result["status"] == "preview"
        assert result["guards"] == []

    def test_allow_protected_swaps_anyway_and_shouts_about_it(self):
        row = _row("run_easy", 60.0, _THURSDAY, phase_type="taper")
        solve = _solve(
            [row],
            races=[
                {"goal_type": "running_marathon", "race_date": _SUNDAY, "priority": "A"}
            ],
        )
        _, repos, _ = _harness([row], solve=solve)
        result = _swap(repos, allow_protected=True, confirm=True)
        assert result["status"] == "ok"
        assert result["guards"] == ["taper week", "race week"]
        assert result["caveats"][0].startswith("This goes against your")
        assert "taper week, race week" in result["caveats"][0]

    def test_missing_thresholds_refuse_a_plan_that_was_built_with_them(
        self, monkeypatch
    ):
        monkeypatch.setattr(mod, "get_planner_load_context", lambda _repos: None)
        _, repos, client = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, client=client, confirm=True)
        assert result["status"] == "error"
        assert "strip your pace and power targets" in result["message"]
        repos.plan.update_session_swap.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_a_session_rescheduled_into_a_taper_week_is_then_refused(self):
        """The reschedule re-stamps ``phase_type``, so this guard still fires.

        Without the re-stamp, a peak session moved into the taper would keep
        ``phase_type='peak'`` and slip past the taper's protection.
        """
        row = _row("run_easy", 60.0, _THURSDAY, phase_type="peak")
        solve = _solve([row])
        # _PLAN_START 2026-03-09 is a Monday: week 12 opens on 2026-06-01.
        solve["phase_calendar"][12] = "taper"
        _, repos, _ = _harness([row], solve=solve)

        # Where it sits now, nothing protects it.
        assert _swap(repos)["status"] == "preview"

        def _move(_session_id, _plan_id, new_date, *, week_index, phase_type):
            row.date, row.week_index = new_date, week_index
            if phase_type is not None:
                row.phase_type = phase_type
            return True

        repos.plan.update_session_date.side_effect = _move
        moved = reschedule_session(
            row.id,
            ScheduleRequest(date="2026-06-04"),
            get_repos=lambda: repos,
            today=_TODAY,
        )
        assert moved["status"] == "ok"
        assert row.phase_type == "taper"

        result = _swap(repos)
        assert result["status"] == "error"
        assert result["guards"] == ["taper week"]
        repos.plan.update_session_swap.assert_not_called()


# Choosing the replacement


class TestOpenTest:
    def test_a_swap_to_a_test_moves_the_missed_one_instead(self):
        # Seen live: "book a threshold test instead of this session"
        # while Thursday's test sat missed.
        rows = [
            *_big_week(),
            _row("run_threshold_test", 55.0, "2026-05-14", session_id=777),
        ]
        _, repos, _ = _harness(rows)
        result = _swap(repos, intent="test")
        assert result["status"] == "error"
        assert (
            'move_planned_session(session_id=777, date="2026-05-21")'
            in result["message"]
        )

    def test_swapping_the_test_itself_is_not_redirected(self):
        rows = [_row("run_threshold_test", 55.0, _THURSDAY), *_big_week()[1:]]
        _, repos, _ = _harness(rows)
        result = _swap(repos, intent="test")
        assert "move_planned_session" not in result.get("message", "")


class TestReplacementSelection:
    def test_sport_defaults_to_the_sessions_own(self):
        _, repos, _ = _harness([_row("bike_endurance", 90.0, _THURSDAY)])
        result = _swap(repos)
        assert result["replacement"]["sport"] == "cycling"
        assert result["replacement"]["activity_key"].startswith("bike_")

    def test_duration_defaults_to_the_sessions_own(self):
        row = _row("run_easy", 60.0, _THURSDAY)
        _, repos, _ = _harness([row])
        result = _swap(repos)
        assert result["replacement"]["requested_duration_minutes"] == (
            row.duration_minutes
        )

    def test_an_explicit_activity_key_wins(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(
            repos, intent=None, activity_key="swim_endurance", duration_minutes=45
        )
        assert result["replacement"]["activity_key"] == "swim_endurance"

    def test_the_replacement_carries_its_execution_notes(self):
        _, repos, _ = _harness([_row("bike_long", 150.0, _THURSDAY)])
        result = _swap(
            repos, intent=None, activity_key="bike_long", duration_minutes=150
        )
        assert result["replacement"]["coaching_notes"][0].startswith("<b>Fueling:</b>")

    def test_an_unknown_activity_key_lists_the_catalog(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, intent=None, activity_key="run_from_the_past")
        assert result["status"] == "error"
        assert "run_from_the_past" in result["message"]
        assert result["options"]

    def test_naming_neither_an_intent_nor_a_key_is_refused(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, intent=None)
        assert result["status"] == "error"
        assert "Name the replacement" in result["message"]
        assert "threshold" in result["options"]

    def test_an_unknown_sport_lists_the_valid_ones(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, sport="rowing")
        assert result["status"] == "error"
        assert result["options"] == ["cycling", "running", "swimming"]

    def test_an_unknown_intent_lists_the_valid_ones(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, intent="sufferfest")
        assert result["status"] == "error"
        assert "sufferfest" in result["message"]
        assert "vo2" in result["options"]

    def test_an_intent_the_sport_cannot_serve_names_the_sports_that_can(self):
        _, repos, _ = _harness([_row("swim_endurance", 45.0, _THURSDAY)])
        result = _swap(repos, intent="brick")
        assert result["status"] == "error"
        assert "No 'brick' activities for sport 'swimming'" in result["message"]
        assert result["options"] == ["running"]

    def test_race_pace_intent_resolves_a_continuous_tempo_run(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, intent="race_pace")
        assert result["replacement"]["activity_key"] == "run_hm_pace"

    def test_a_row_with_no_duration_and_no_request_is_refused(self):
        row = _row(
            "run_easy",
            60.0,
            _THURSDAY,
            duration_minutes=None,
            duration_target_minutes=None,
        )
        _, repos, _ = _harness([row])
        result = _swap(repos)
        assert result["status"] == "error"
        assert "duration_minutes must be greater than 0" in result["message"]

    def test_a_legacy_row_falls_back_to_its_plan_intent(self):
        row = _row("run_easy", 60.0, _THURSDAY, duration_minutes=None)
        _, repos, _ = _harness([row])
        result = _swap(repos)
        assert result["replacement"]["requested_duration_minutes"] == 60.0


# The preview


class TestPreview:
    def test_a_preview_writes_nothing_and_pushes_nothing(self):
        _, repos, client = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, client=client)
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["push"] is None
        assert result["message"].startswith("Nothing has changed yet.")
        repos.plan.update_session_swap.assert_not_called()
        repos.plan.update_solve_result_json.assert_not_called()
        repos.plan.update_pushed_events.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_the_preview_names_both_sides_and_the_week(self):
        rows = _big_week()
        _, repos, _ = _harness(rows)
        result = _swap(repos)
        assert result["session_id"] == 501
        assert result["date"] == _THURSDAY
        assert result["current"] == {
            "date": _THURSDAY,
            "activity_key": "run_easy",
            "family": rows[0].family,
            "sport": "running",
            "phase": "build",
            "duration_minutes": rows[0].duration_minutes,
            "target_tss": round(float(rows[0].target_tss), 1),
            "steps_summary": result["current"]["steps_summary"],
        }
        assert result["current"]["steps_summary"].startswith("3 steps: warmup")
        replacement = result["replacement"]
        assert replacement["activity_key"] == "run_threshold"
        assert replacement["duration_minutes"] > 0
        assert replacement["achieved_tss"] > 0
        assert replacement["steps"][0]["type"] == "warmup"
        assert (
            "Swapped in on request for the planned" in (replacement["coaching_intent"])
        )
        assert result["week"]["from"] == _MONDAY
        assert result["week"]["through"] == _SUNDAY
        assert result["week"]["before_tss"] == round(
            sum(float(row.target_tss) for row in rows), 1
        )

    def test_the_week_delta_replaces_only_this_session(self):
        rows = _big_week()
        _, repos, _ = _harness(rows)
        week = _swap(repos, duration_minutes=90)["week"]
        assert week["after_tss"] != week["before_tss"]
        assert week["after_minutes"] != week["before_minutes"]
        assert week["tss_delta_percent"] == pytest.approx(
            (week["after_tss"] - week["before_tss"]) / week["before_tss"] * 100,
            abs=0.05,
        )

    def test_a_session_without_readable_steps_says_so(self):
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY, workout_json="{{")])
        result = _swap(repos)
        assert result["current"]["steps_summary"] == "No structured steps recorded."

    def test_the_preview_reports_exactly_what_the_apply_writes(self):
        rows = _big_week()
        _, preview_repos, _ = _harness(rows)
        preview = _swap(preview_repos)
        _, apply_repos, _ = _harness(rows)
        applied = _swap(apply_repos, confirm=True)
        assert preview["replacement"] == applied["replacement"]
        assert preview["week"] == applied["week"]
        assert preview["current"] == applied["current"]


# The caveats the coach must relay


class TestCaveats:
    def test_the_scope_caveat_is_always_there(self):
        _, repos, _ = _harness(_big_week())
        caveats = _swap(repos)["caveats"]
        assert any("Only this one session changed" in text for text in caveats)

    def test_a_swap_beyond_this_week_says_a_replan_undoes_it(self, monkeypatch):
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-05-13")
        _, repos, _ = _harness(_big_week())
        caveats = _swap(repos)["caveats"]
        assert (
            "A replan rebuilds the plan from next Monday and would undo this swap."
            in caveats
        )

    def test_a_key_session_in_an_ordinary_week_carries_no_capability_caveat(self):
        _, repos, _ = _harness(_big_week())
        caveats = _swap(repos, intent="long")["caveats"]
        assert not any("Long Run" in text for text in caveats)

    def test_a_key_session_in_a_recovery_week_carries_the_caveat_first(self):
        rows = _big_week()
        solve = _solve(rows)
        for entry in solve["sessions"]:
            entry["is_recovery_week"] = True
        _, repos, _ = _harness(rows, solve=solve)
        caveats = _swap(repos, intent="long")["caveats"]
        assert "Long Run" in caveats[0]
        assert "recovery week" in caveats[0]

    def test_a_quiet_swap_carries_no_week_warning(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos)
        assert abs(result["week"]["tss_delta_percent"]) <= WEEK_DELTA_WARN_PERCENT
        assert not any("moves the week by" in text for text in result["caveats"])

    def test_a_big_swing_names_both_percentages(self):
        # One session in the week: replacing it moves everything.
        _, repos, _ = _harness([_row("run_easy", 60.0, _THURSDAY)])
        result = _swap(repos, intent="long", duration_minutes=180)
        assert abs(result["week"]["tss_delta_percent"]) > WEEK_DELTA_WARN_PERCENT
        warning = next(
            text for text in result["caveats"] if "moves the week by" in text
        )
        assert f"{result['week']['tss_delta_percent']}% of planned load" in warning
        assert f"{result['week']['minutes_delta_percent']}% of planned hours" in warning
        # Seen live: the coach's own instruction reached the athlete.
        assert "Quote" not in warning

    def test_a_swap_this_week_says_nothing_of_a_replan(self, monkeypatch):
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", _TODAY.isoformat())
        _, repos, _ = _harness(_big_week())
        assert not any("replan" in text for text in _swap(repos)["caveats"])

    def test_a_sport_change_is_flagged(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, sport="cycling")
        assert result["replacement"]["sport"] == "cycling"
        assert any(
            "changes the session's sport: running -> cycling" in text
            for text in result["caveats"]
        )

    def test_a_same_sport_swap_is_not_flagged(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos)
        assert not any(
            "changes the session's sport" in text for text in result["caveats"]
        )


# What gets persisted


class TestApply:
    def test_the_row_keeps_its_identity_and_gains_the_new_workout(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        result = _swap(repos, sport="cycling", confirm=True)
        call = repos.plan.update_session_swap.call_args
        assert call.args == (501, _PLAN_ID)
        assert call.kwargs["sport"] == "cycling"
        assert call.kwargs["activity_key"] == result["replacement"]["activity_key"]
        assert call.kwargs["family"] == result["replacement"]["family"]
        assert call.kwargs["target_tss"] == result["replacement"]["target_tss"]
        assert call.kwargs["achieved_tss"] == result["replacement"]["achieved_tss"]
        assert (
            call.kwargs["duration_minutes"]
            == (result["replacement"]["duration_minutes"])
        )
        # The swap redefines the hours-space base too.
        assert (
            call.kwargs["duration_target_minutes"]
            == (result["replacement"]["requested_duration_minutes"])
        )
        stored = json.loads(call.kwargs["workout_json"])
        assert stored["steps"] == result["replacement"]["steps"]
        assert stored["coaching_intent"] == result["replacement"]["coaching_intent"]

    def test_the_replacement_carries_notes_but_no_menu_why(self):
        """An ad-hoc pick has execution cues; it has no menu rationale."""
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)

        _swap(
            repos,
            sport="cycling",
            activity_key="bike_long",
            duration_minutes=200.0,
            confirm=True,
        )

        stored = json.loads(
            repos.plan.update_session_swap.call_args.kwargs["workout_json"]
        )
        assert "why_details" not in stored
        assert any("Fueling" in note for note in stored["coaching_notes"])

    def test_the_solve_payload_is_mirrored_onto_the_same_slot(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        result = _swap(repos, confirm=True)
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        entry = next(
            item
            for item in mirrored["sessions"]
            if item["slot_id"] == str(rows[0].slot_id)
        )
        assert entry["activity_key"] == result["replacement"]["activity_key"]
        assert entry["date"] == _THURSDAY
        assert entry["week_index"] == rows[0].week_index
        assert entry["duration_minutes"] == result["replacement"]["duration_minutes"]

    @pytest.mark.parametrize("status", ["skipped", "unlinked"])
    def test_a_manually_overridden_session_goes_back_to_pending(self, status):
        """Otherwise the new workout stays invisible to matching and compliance."""
        rows = _big_week()
        rows[0].status = status
        _, repos, _ = _harness(rows, with_client=False)
        result = _swap(repos, confirm=True)
        assert result["status"] == "ok"
        repos.plan.update_session_status.assert_called_once_with(501, "pending", None)
        assert any(
            f"The session was marked {status}." in text for text in result["caveats"]
        )

    def test_a_pending_session_keeps_its_status_untouched(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        result = _swap(repos, confirm=True)
        repos.plan.update_session_status.assert_not_called()
        assert not any("was marked" in text for text in result["caveats"])

    def test_a_preview_of_a_skipped_session_warns_without_writing(self):
        rows = _big_week()
        rows[0].status = "skipped"
        _, repos, _ = _harness(rows, with_client=False)
        result = _swap(repos)
        assert result["status"] == "preview"
        repos.plan.update_session_status.assert_not_called()
        assert any("back in play" in text for text in result["caveats"])

    def test_the_applied_message_names_both_workouts_and_the_day(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        result = _swap(repos, confirm=True)
        assert result["status"] == "ok"
        assert result["confirm_required"] is False
        assert result["message"].endswith(f"run_easy on {_THURSDAY}.")


class TestPushPaths:
    def test_the_event_keeps_its_external_id_and_is_stored_before_the_call(self):
        rows = _big_week()
        parent, repos, client = _harness(rows)
        result = _swap(repos, client=client, confirm=True)
        assert result["push"]["status"] == "ok"
        names = _call_names(parent)
        assert names.index("repos.plan.update_pushed_events") < names.index(
            "client.bulk_upsert_events"
        )
        events = client.bulk_upsert_events.call_args.args[0]
        assert [event["external_id"] for event in events] == [
            f"coachie:{_PLAN_ID}:{rows[0].slot_id}"
        ]

    def test_the_pushed_dsl_describes_the_replacement(self):
        rows = _big_week()
        before = build_plan_events(
            _PLAN_ID, [rows[0].committed_payload], [], _ATHLETE_STATE
        )[0]
        _, repos, client = _harness(rows)
        _swap(repos, client=client, confirm=True)
        event = client.bulk_upsert_events.call_args.args[0][0]
        assert event["description"] != before["description"]
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        entry = next(
            item
            for item in mirrored["sessions"]
            if item["slot_id"] == str(rows[0].slot_id)
        )
        assert (
            build_plan_events(_PLAN_ID, [entry], [], _ATHLETE_STATE)[0]["description"]
            == event["description"]
        )

    def test_a_failed_push_keeps_the_local_write_and_says_so(self):
        _, repos, client = _harness(_big_week())
        client.bulk_upsert_events.side_effect = RuntimeError("502")
        result = _swap(repos, client=client, confirm=True)
        assert result["status"] == "ok"
        assert result["push"]["status"] == "error"
        repos.plan.update_session_swap.assert_called_once()
        assert result["push"]["message"] in result["caveats"]
        assert (
            "Your intervals.icu calendar still shows the previous workout "
            "until the push is retried."
        ) in result["caveats"]

    def test_without_a_client_nothing_is_pre_stored(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        result = _swap(repos, confirm=True)
        assert result["push"]["status"] == "skipped"
        assert result["push"]["message"] == PUSH_SKIPPED_MESSAGE
        repos.plan.update_pushed_events.assert_not_called()
        repos.plan.update_session_swap.assert_called_once()


class TestCoachContext:
    def test_the_coach_summary_is_rebuilt_after_the_swap(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        coach = SimpleNamespace(runtime=SimpleNamespace(active_plan_summary="stale"))
        _swap(repos, confirm=True, maybe_get_coach=lambda: coach)
        assert coach.runtime.active_plan_summary != "stale"

    def test_no_coach_accessor_is_fine(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        assert _swap(repos, confirm=True)["status"] == "ok"


class TestPureEntryPoint:
    def test_plan_session_swap_performs_no_writes(self):
        rows = _big_week()
        _, repos, _ = _harness(rows)
        swap = plan_session_swap(
            repos=repos,
            session_id=501,
            sport=None,
            intent="threshold",
            activity_key=None,
            duration_minutes=None,
            allow_protected=False,
            today=_TODAY,
        )
        assert swap.row.id == 501
        assert swap.week == (date(2026, 5, 18), date(2026, 5, 24))
        assert swap.payload["slot_id"] == str(rows[0].slot_id)
        repos.plan.update_session_swap.assert_not_called()


# Moving the session's day in the same call (the `date` argument)

_WEDNESDAY = "2026-05-20"


class TestRedate:
    def test_the_preview_reports_both_the_current_and_the_new_date(self):
        # The requested date must not be silently treated as the current one.
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, date=_WEDNESDAY)
        assert result["status"] == "preview"
        assert result["current"]["date"] == _THURSDAY
        assert result["date"] == _WEDNESDAY
        repos.plan.update_session_date.assert_not_called()

    def test_the_confirmed_swap_re_dates_the_row_and_pushes_once(self):
        rows = _big_week()
        _, repos, client = _harness(rows)
        result = _swap(repos, client=client, date=_WEDNESDAY, confirm=True)

        assert result["status"] == "ok"
        assert result["date"] == _WEDNESDAY
        assert _WEDNESDAY in result["message"]
        repos.plan.update_session_date.assert_called_once()
        assert repos.plan.update_session_date.call_args.args[2] == _WEDNESDAY
        # One push, under the slot's own external_id — not a delete/recreate.
        assert client.bulk_upsert_events.call_count == 1
        pushed = client.bulk_upsert_events.call_args.args[0]
        assert len(pushed) == 1
        assert pushed[0]["start_date_local"].startswith(_WEDNESDAY)

    def test_the_mirrored_solve_entry_carries_the_new_date(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        _swap(repos, date=_WEDNESDAY, confirm=True)
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        entry = next(
            item
            for item in mirrored["sessions"]
            if item["slot_id"] == str(rows[0].slot_id)
        )
        assert entry["date"] == _WEDNESDAY

    def test_the_session_s_own_date_is_not_a_move(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, date=_THURSDAY, confirm=True)
        assert result["status"] == "ok"
        repos.plan.update_session_date.assert_not_called()

    @pytest.mark.parametrize(
        ("bad_date", "fragment"),
        [
            ("2026-05-19", "into the past"),
            ("not-a-date", "Invalid date"),
            ("2026-07-01", "outside the plan window"),
            (_RACE, "race day"),
        ],
    )
    def test_the_move_rules_are_the_ones_move_planned_session_applies(
        self, bad_date, fragment
    ):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, date=bad_date)
        assert result["status"] == "error"
        assert fragment in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    def test_a_date_in_another_week_points_at_move_planned_session(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, date="2026-05-26")
        assert result["status"] == "error"
        assert "move_planned_session" in result["message"]
        repos.plan.update_session_swap.assert_not_called()

    def test_a_day_that_already_holds_a_session_is_flagged_as_a_caveat(self):
        _, repos, _ = _harness(_big_week())
        result = _swap(repos, date="2026-05-22")
        assert result["status"] == "preview"
        assert any("already holds" in text for text in result["caveats"])
