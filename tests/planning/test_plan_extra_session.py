"""Tests for coach extras: schedule_workout joining the active plan's books."""

from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.select import pick_for_intent
from endury.planner.foundations.types import (
    ActivityKey,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planning import adjust_week as adjust_mod
from endury.planning import plan_extra_session as guards
from endury.planning import schedule_workout as schedule_mod
from endury.planning.commit_plan import PUSH_SKIPPED_MESSAGE
from endury.planning.plan_extra_session import (
    MAX_EXTRA_WEEK_TSS_FRACTION,
    is_coach_extra,
)
from endury.planning.schedule_workout import schedule_workout
from endury.planning.wire.sessions import (
    build_commit_session_rows,
    serialize_plan_session_row,
)
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
    monkeypatch.setattr(
        schedule_mod, "get_planner_load_context", lambda _repos: RESOLVER
    )


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
    insert = build_commit_session_rows([_payload_for(key, minutes, day, slot)])[0]
    return _row_from_insert(insert, session_id=session_id, **overrides)


def _row_from_insert(
    insert, *, session_id: int, **overrides: object
) -> SimpleNamespace:
    """Shape one ``SessionInsertRow`` as the row the DB would hand back."""
    base: dict[str, object] = {
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
        "duration_target_minutes": insert.duration_target_minutes,
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
        "sessions": [
            _payload_for(
                str(row.activity_key),
                float(row.duration_target_minutes or 0.0),
                str(row.date),
                str(row.slot_id),
            )
            for row in rows
        ],
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
    reflect_inserts: bool = True,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return ``(parent, repos, client)`` sharing one call recorder.

    The plan repo double is stateful where this verb reads back what it
    wrote: an insert lands in ``rows`` so the row-id read-back finds it.
    """
    solve = solve if solve is not None else _solve(rows)
    parent = MagicMock()
    repos = parent.repos
    client = parent.client if with_client else None
    repos.plan.get_active.return_value = plan if plan is not None else _plan(solve)
    repos.plan.get_sessions.return_value = rows
    repos.plan.get_sessions_by_date_range.side_effect = lambda _plan_id, start, end: [
        row for row in rows if start <= str(row.date) <= end
    ]

    def _insert(_plan_id: int, inserts: list) -> int:
        if reflect_inserts:
            rows.extend(
                _row_from_insert(insert, session_id=900 + index)
                for index, insert in enumerate(inserts)
            )
        return len(inserts)

    repos.plan.bulk_insert_sessions.side_effect = _insert
    repos.session.transaction.return_value = nullcontext()
    return parent, repos, client


def _schedule(repos, client=None, **kwargs) -> dict:
    """Call the verb with this suite's defaults filled in."""
    kwargs.setdefault("duration_minutes", 45)
    kwargs.setdefault("date_str", _THURSDAY)
    kwargs.setdefault("sport", "running")
    kwargs.setdefault("intent", "easy")
    return schedule_workout(repos=repos, client=client, today=_TODAY, **kwargs)


def _call_names(parent: MagicMock) -> list[str]:
    return [entry[0] for entry in parent.mock_calls]


def _big_week() -> list[SimpleNamespace]:
    """A week with enough planned load to absorb a 45-minute extra."""
    return [
        _row("run_easy", 60.0, _THURSDAY),
        *[
            _row("bike_long", 180.0, day, session_id=600 + index)
            for index, day in enumerate([_MONDAY, "2026-05-19", "2026-05-22", _SUNDAY])
        ],
    ]


# When the extra path takes over at all


class TestTrigger:
    def test_without_an_active_plan_behaves_exactly_as_before(self):
        repos = MagicMock()
        repos.plan.get_active.return_value = None
        client = MagicMock()
        client.bulk_upsert_events.return_value = [SimpleNamespace(id=1)]
        result = _schedule(repos, client=client)
        assert result["status"] == "ok"
        assert "plan_row" not in result
        assert result["push"]["external_id"].startswith("coachie:adhoc:")
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_day_after_the_goal_behaves_exactly_as_before(self):
        rows = _big_week()
        _, repos, client = _harness(rows)
        client.bulk_upsert_events.return_value = [SimpleNamespace(id=1)]
        result = _schedule(repos, client=client, date_str="2026-06-15")
        assert result["status"] == "ok"
        assert "plan_row" not in result
        assert result["push"]["external_id"].startswith("coachie:adhoc:")
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_day_before_a_plan_that_has_not_started_behaves_as_before(self):
        rows = _big_week()
        _, repos, client = _harness(
            rows, plan=_plan(_solve(rows), plan_start="2026-06-01")
        )
        client.bulk_upsert_events.return_value = [SimpleNamespace(id=1)]
        result = _schedule(repos, client=client)
        assert result["status"] == "ok"
        assert "plan_row" not in result
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_the_goal_day_itself_is_inside_the_horizon(self):
        _, repos, _ = _harness(_big_week())
        result = _schedule(repos, date_str=_RACE)
        # Inside the plan, so the extra path owns it — and refuses race week.
        assert result["status"] == "error"
        assert result["guards"] == ["race week"]


# Guards: nothing is written and the reason names the alternative


class TestGuards:
    @pytest.mark.parametrize(
        ("intent", "family"),
        [("threshold", "threshold"), ("long", "long"), ("vo2", "interval")],
    )
    def test_a_harder_intent_is_refused_with_the_swap_pointer(self, intent, family):
        _, repos, _ = _harness(_big_week())
        result = _schedule(repos, intent=intent)
        assert result["status"] == "error"
        assert f"is a {family} session" in result["message"]
        assert "swap_planned_session" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_long_trail_run_is_refused_as_a_long_session_not_as_intensity(self):
        _, repos, _ = _harness(_big_week())
        result = _schedule(repos, intent="trail", duration_minutes=150)
        assert result["status"] == "error"
        assert "adds a long session the plan did not budget" in result["message"]
        assert "intensity" not in result["message"]

    def test_an_easy_trail_hour_books_as_an_easy_extra(self):
        # Seen live: "an easy hour-long trail run" was refused, since
        # every trail run was the Long Trail Run.
        _, repos, _ = _harness(_big_week(), with_client=False)
        result = _schedule(repos, intent="trail", duration_minutes=60)
        assert result["status"] == "ok"
        inserted = repos.plan.bulk_insert_sessions.call_args.args[1][0]
        assert inserted.activity_key == "run_trail_easy"
        assert result["message"].startswith("Easy Trail Run added to your plan")

    @pytest.mark.parametrize("intent", ["easy", "endurance", "recovery", "strides"])
    def test_the_easy_family_intents_are_allowed(self, intent):
        _, repos, _ = _harness(_big_week())
        assert _schedule(repos, intent=intent, duration_minutes=30)["status"] == "ok"

    def test_a_taper_week_is_refused_without_allow_protected(self):
        rows = [_row("run_easy", 60.0, _THURSDAY, phase_type="taper")]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, duration_minutes=5)
        assert result["status"] == "error"
        assert result["guards"] == ["taper week"]
        assert "allow_protected=true" in result["message"]
        # The retry it points at is the confirm-gated tool.
        assert "schedule_workout_override" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_taper_week_takes_an_extra_with_allow_protected(self):
        rows = _big_week()
        for row in rows:
            row.phase_type = "taper"
        _, repos, _ = _harness(rows)
        result = _schedule(repos, duration_minutes=30, allow_protected=True)
        assert result["status"] == "ok"
        assert result["guards"] == ["taper week"]
        assert result["caveats"][0].startswith("This adds to your")

    def test_a_race_week_is_refused(self):
        rows = _big_week()
        solve = _solve(
            rows,
            races=[{"goal_type": "running_10k", "race_date": _SUNDAY, "priority": "B"}],
        )
        _, repos, _ = _harness(rows, solve=solve)
        result = _schedule(repos, duration_minutes=30)
        assert result["status"] == "error"
        assert result["guards"] == ["race week"]

    def test_missing_thresholds_refuse_a_plan_that_was_built_with_them(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            schedule_mod, "get_planner_load_context", lambda _repos: None
        )
        _, repos, client = _harness(_big_week())
        result = _schedule(repos, client=client, duration_minutes=30)
        assert result["status"] == "error"
        assert "look wrong next to the rest of the week" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_a_plan_built_without_thresholds_still_takes_an_extra(self, monkeypatch):
        monkeypatch.setattr(
            schedule_mod, "get_planner_load_context", lambda _repos: None
        )
        rows = _big_week()
        _, repos, _ = _harness(
            rows, plan=_plan(_solve(rows), athlete_state_json=json.dumps({}))
        )
        result = _schedule(repos, duration_minutes=30)
        assert result["status"] == "ok"
        assert any("No athlete thresholds" in text for text in result["caveats"])


# allow_hard: a hard-family extra, still refused near another one


def _sparse_week(*, phase_type: str | None = None) -> list[SimpleNamespace]:
    """Big Mon/Tue/Sat/Sun sessions for budget — Wed/Thu/Fri stay clear."""
    overrides = {"phase_type": phase_type} if phase_type else {}
    return [
        _row("bike_long", 180.0, day, session_id=610 + index, **overrides)
        for index, day in enumerate([_MONDAY, "2026-05-19", "2026-05-23", _SUNDAY])
    ]


class TestAllowHard:
    def test_without_allow_hard_a_hard_intent_is_still_refused(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="threshold")
        assert result["status"] == "error"
        assert "allow_hard=true" in result["message"]
        assert "schedule_workout_override" in result["message"]

    def test_an_allow_hard_preview_writes_no_row_and_pushes_nothing(self):
        # The override tool's first phase: guarded and built, never written.
        _, repos, client = _harness(_sparse_week())
        result = _schedule(
            repos, client=client, intent="threshold", allow_hard=True, preview=True
        )
        assert result["status"] == "preview"
        assert result["push"] is None
        repos.plan.bulk_insert_sessions.assert_not_called()
        client.bulk_upsert_events.assert_not_called()

    def test_allow_hard_takes_a_clear_day(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["status"] == "ok"

    def test_a_brick_needs_allow_hard_although_its_family_is_easy(self):
        # run_brick_short is family=easy: only the BRICK_SUPPORT tag marks it
        # as the race-specific session the plan never budgeted.
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="brick")
        assert result["status"] == "error"
        assert "allow_hard=true" in result["message"]
        assert "is a brick session" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_brick_with_allow_hard_leads_with_the_intensity_caveat(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="brick", allow_hard=True)
        assert result["status"] == "ok"
        assert result["caveats"][0].startswith(
            "This adds intensity the plan did not budget"
        )
        assert "brick" in result["caveats"][0]

    def test_the_first_caveat_says_intensity_was_not_budgeted(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["caveats"][0].startswith(
            "This adds intensity the plan did not budget"
        )

    def test_a_long_extra_with_allow_hard_says_it_adds_a_long_session(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="long", allow_hard=True)
        assert result["status"] == "ok"
        assert result["caveats"][0].startswith(
            "This adds a long session the plan did not budget"
        )

    def test_an_easy_extras_first_caveat_is_not_the_intensity_one(self):
        _, repos, _ = _harness(_sparse_week())
        result = _schedule(repos, intent="easy")
        assert not result["caveats"][0].startswith("This adds intensity")

    def test_a_hard_session_the_day_before_blocks_even_with_allow_hard(self):
        rows = [
            *_sparse_week(),
            _row("run_threshold", 30.0, "2026-05-20", session_id=700),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["status"] == "error"
        assert "2026-05-20" in result["message"]
        assert "a hard session" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_skipped_neighbour_does_not_block(self):
        rows = [
            *_sparse_week(),
            _row(
                "run_threshold",
                30.0,
                "2026-05-20",
                session_id=700,
                status="skipped",
            ),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["status"] == "ok"

    def test_a_taper_week_still_refuses_a_hard_extra_without_allow_protected(self):
        _, repos, _ = _harness(_sparse_week(phase_type="taper"))
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["status"] == "error"
        assert result["guards"] == ["taper week"]

    def test_a_hard_extra_in_a_recovery_week_names_the_week(self):
        rows = _sparse_week()
        solve = _solve(rows)
        for entry in solve["sessions"]:
            entry["is_recovery_week"] = True
        _, repos, _ = _harness(rows, solve=solve)
        result = _schedule(repos, intent="threshold", allow_hard=True)
        assert result["status"] == "ok"
        assert any("recovery week" in text for text in result["caveats"])

    def test_a_taper_week_takes_a_hard_extra_with_both_overrides(self):
        _, repos, _ = _harness(_sparse_week(phase_type="taper"))
        result = _schedule(
            repos, intent="threshold", allow_hard=True, allow_protected=True
        )
        assert result["status"] == "ok"
        assert result["caveats"][0].startswith("This adds intensity")
        assert result["caveats"][1].startswith("This adds to your")


# Blackout dates: a stored unavailable directive refuses any extra there


class TestUnavailableWindow:
    def test_a_date_inside_a_stored_window_is_refused(self):
        rows = _big_week()
        solve = _solve(rows)
        solve["request"]["directives"] = {"unavailable": [[_THURSDAY, _THURSDAY]]}
        _, repos, _ = _harness(rows, solve=solve)
        result = _schedule(repos, intent="easy")
        assert result["status"] == "error"
        assert _THURSDAY in result["message"]
        assert "set_plan_directives" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_date_outside_the_window_is_unaffected(self):
        rows = _big_week()
        solve = _solve(rows)
        solve["request"]["directives"] = {"unavailable": [["2026-06-01", "2026-06-05"]]}
        _, repos, _ = _harness(rows, solve=solve)
        result = _schedule(repos, intent="easy", duration_minutes=30)
        assert result["status"] == "ok"


class TestGuardOrder:
    """Guard order is pinned: unavailable -> taper/race -> hard-intent gate
    -> adjacent hard -> TSS allowance. One case per boundary."""

    def test_unavailable_window_wins_over_taper_protection(self):
        rows = _big_week()
        for row in rows:
            row.phase_type = "taper"
        solve = _solve(rows)
        solve["request"]["directives"] = {"unavailable": [[_THURSDAY, _THURSDAY]]}
        _, repos, _ = _harness(rows, solve=solve)
        result = _schedule(repos, intent="easy")
        assert result["status"] == "error"
        assert _THURSDAY in result["message"]
        assert "set_plan_directives" in result["message"]
        assert "guards" not in result

    def test_taper_protection_wins_over_the_hard_intent_gate(self):
        rows = _big_week()
        for row in rows:
            row.phase_type = "taper"
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="threshold")
        assert result["status"] == "error"
        assert result["guards"] == ["taper week"]
        assert "allow_hard=true" not in result["message"]

    def test_the_hard_intent_gate_wins_over_an_adjacent_hard_neighbour(self):
        rows = [
            *_sparse_week(),
            _row("run_threshold", 30.0, "2026-05-20", session_id=700),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="threshold")
        assert result["status"] == "error"
        assert "allow_hard=true" in result["message"]
        assert "2026-05-20" not in result["message"]

    def test_adjacent_hard_wins_over_the_budget_allowance(self):
        # A tiny week: the budget allowance alone would already refuse this
        # extra, but the neighbour refusal must fire first.
        rows = [
            _row("run_easy", 20.0, _MONDAY, session_id=610),
            _row("run_threshold", 30.0, "2026-05-20", session_id=700),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(
            repos, intent="threshold", allow_hard=True, duration_minutes=60
        )
        assert result["status"] == "error"
        assert "2026-05-20" in result["message"]
        assert "TSS of room" not in result["message"]


class TestOpenTest:
    def test_a_missed_test_is_moved_rather_than_booked_twice(self):
        # Seen live: asked for "a threshold test" with Thursday's missed.
        rows = [
            *_big_week(),
            _row("run_threshold_test", 55.0, "2026-05-14", session_id=777),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="test", duration_minutes=55)
        assert result["status"] == "error"
        assert "Threshold Run Test on Thursday 14 May, missed" in result["message"]
        assert (
            'move_planned_session(session_id=777, date="2026-05-21")'
            in result["message"]
        )

    def test_a_done_test_leaves_the_booking_to_the_other_guards(self):
        rows = [
            *_big_week(),
            _row(
                "run_threshold_test",
                55.0,
                "2026-05-14",
                session_id=777,
                matched_activity_id="i1",
                status="completed",
            ),
        ]
        _, repos, _ = _harness(rows)
        result = _schedule(repos, intent="test", duration_minutes=55)
        assert "move_planned_session" not in result["message"]


class TestWeekBudget:
    def test_the_first_extra_fits_under_the_budget(self):
        rows = _big_week()
        planned = sum(float(row.target_tss) for row in rows)
        _, repos, _ = _harness(rows)
        result = _schedule(repos)
        assert result["status"] == "ok"
        assert result["workout"]["target_tss"] < planned * MAX_EXTRA_WEEK_TSS_FRACTION

    def test_an_extra_past_the_week_budget_is_refused_with_the_numbers(self):
        # One 60-minute easy run: 15% of it is nowhere near a 45-minute extra.
        rows = [_row("run_easy", 60.0, "2026-05-22")]
        planned = round(float(rows[0].target_tss), 1)
        _, repos, _ = _harness(rows)
        result = _schedule(repos)
        assert result["status"] == "error"
        assert f"sized at {planned} load" in result["message"]
        assert (
            f"leaves {round(planned * MAX_EXTRA_WEEK_TSS_FRACTION, 1)} load of room"
            in (result["message"])
        )
        assert "swap_planned_session" in result["message"]

    def test_a_refusal_offers_the_weeks_own_session_and_never_a_shorter_one(self):
        # Seen live: "make it shorter" became a 14-minute run.
        rows = [_row("run_easy", 60.0, "2026-05-22")]
        _, repos, _ = _harness(rows)
        message = _schedule(repos)["message"]
        assert "shorter" not in message
        assert (
            "Friday 22 May's Easy Run (60 min, session_id 501) with "
            "move_planned_session" in message
        )

    def test_already_booked_extras_eat_the_budget(self):
        rows = _big_week()
        _, repos, _ = _harness(rows)
        assert _schedule(repos)["status"] == "ok"
        # The booked extra is now a row of the week and pays for itself.
        assert sum(1 for row in rows if is_coach_extra(row)) == 1
        second = _schedule(repos)
        assert second["status"] == "error"
        # The booked extra shows up in what the week is still carrying.
        assert "still carrying" in second["message"]


# What the coach is told


class TestReport:
    def test_the_response_names_the_week_before_and_after(self):
        rows = _big_week()
        # Read before the call: the booking adds its own row to the week.
        planned_tss = round(sum(float(row.target_tss) for row in rows), 1)
        _, repos, _ = _harness(rows)
        result = _schedule(repos)
        week = result["week"]
        assert week["from"] == _MONDAY
        assert week["through"] == _SUNDAY
        assert week["before_tss"] == planned_tss
        assert week["after_tss"] == round(
            week["before_tss"] + result["workout"]["target_tss"], 1
        )
        assert week["tss_delta_percent"] > 0
        assert week["minutes_delta_percent"] > 0

    def test_a_day_that_already_has_a_planned_session_is_flagged(self):
        _, repos, _ = _harness(_big_week())
        result = _schedule(repos, duration_minutes=30)
        assert result["plan_collision"] == {
            "session_id": 501,
            "activity_key": "run_easy",
            "sport": "running",
        }
        assert (
            "Thursday 21 May already has your Easy Run. This is a second "
            "session that day, not a replacement for it."
        ) in result["caveats"]

    def test_an_empty_day_has_no_collision(self):
        _, repos, _ = _harness(_big_week())
        result = _schedule(repos, date_str="2026-05-20", duration_minutes=30)
        assert result["plan_collision"] is None


# What gets persisted


class TestApply:
    def test_the_extra_becomes_a_plan_row_inside_the_horizon(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        result = _schedule(repos)
        assert result["status"] == "ok"
        assert "confirm_required" not in result
        inserted = repos.plan.bulk_insert_sessions.call_args.args[1][0]
        assert inserted.slot_id == f"x1:extra_{_THURSDAY}_run_easy"
        assert inserted.date == _THURSDAY
        assert inserted.activity_key == "run_easy"
        assert inserted.target_tss == result["workout"]["target_tss"]
        assert inserted.duration_target_minutes == inserted.duration_minutes
        assert "coach_extra" not in json.loads(inserted.workout_json)
        assert result["plan_row"] == {
            "session_id": 900,
            "slot_id": inserted.slot_id,
            "week_index": inserted.week_index,
            "phase": "build",
        }
        assert result["message"].startswith("Easy Run added to your plan")

    def test_the_row_serializes_for_the_calendar_like_any_other(self):
        """It has no menu rationale — every reader must survive that."""
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        result = _schedule(repos)
        row = next(
            item for item in rows if item.slot_id == result["plan_row"]["slot_id"]
        )
        payload = serialize_plan_session_row(row, today=_TODAY)
        assert payload["why_details"] == []
        assert payload["description"] == "Easy Run"
        assert payload["status"] == "pending"
        assert payload["planned_metrics"]["tss"] == result["workout"]["target_tss"]
        assert "coach_extra" not in payload["workout"]

    def test_the_week_index_is_the_plans_own(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        _schedule(repos)
        inserted = repos.plan.bulk_insert_sessions.call_args.args[1][0]
        # 2026-03-09 is week 0; 2026-05-18 opens week 10.
        assert inserted.week_index == 10

    def test_a_week_with_no_rows_of_its_own_has_nothing_to_pay_with(self):
        """No planned load that week means no extras allowance either."""
        _, repos, _ = _harness([_row("run_easy", 60.0, "2026-06-01")])
        result = _schedule(repos, date_str="2026-05-27", duration_minutes=30)
        assert result["status"] == "error"
        assert "has no planned sessions to add an extra to" in result["message"]
        repos.plan.bulk_insert_sessions.assert_not_called()

    def test_a_second_extra_takes_the_next_slot_id(self):
        rows = _big_week()
        taken = _row(
            "run_easy",
            30.0,
            _THURSDAY,
            session_id=888,
            slot=f"x1:extra_{_THURSDAY}_run_easy",
        )
        rows.append(taken)
        _, repos, _ = _harness(rows, with_client=False)
        result = _schedule(repos, duration_minutes=30)
        assert result["plan_row"]["slot_id"] == f"x2:extra_{_THURSDAY}_run_easy"

    def test_the_solve_payload_and_weekly_summary_gain_the_extra(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False)
        result = _schedule(repos)
        mirrored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        slot = result["plan_row"]["slot_id"]
        entry = next(item for item in mirrored["sessions"] if item["slot_id"] == slot)
        assert entry["date"] == _THURSDAY
        assert "coach_extra" not in entry["workout"]
        assert [item["date"] for item in mirrored["sessions"]] == sorted(
            item["date"] for item in mirrored["sessions"]
        )
        week = next(
            item for item in mirrored["weekly_summary"] if item["week_index"] == 10
        )
        assert week["session_count"] == len(rows)
        assert slot in [
            session["slot_id"] for day in week["days"] for session in day["sessions"]
        ]

    def test_the_row_id_read_back_tolerates_a_miss(self):
        rows = _big_week()
        _, repos, _ = _harness(rows, with_client=False, reflect_inserts=False)
        result = _schedule(repos)
        assert result["status"] == "ok"
        assert result["plan_row"]["session_id"] is None

    def test_the_coach_plan_summary_is_rebuilt(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        coach = SimpleNamespace(runtime=SimpleNamespace(active_plan_summary="stale"))
        _schedule(repos, maybe_get_coach=lambda: coach)
        assert coach.runtime.active_plan_summary != "stale"


class TestPushPaths:
    def test_the_event_uses_the_plans_external_id_scheme(self):
        rows = _big_week()
        parent, repos, client = _harness(rows)
        result = _schedule(repos, client=client)
        assert result["push"]["status"] == "ok"
        slot = result["plan_row"]["slot_id"]
        events = client.bulk_upsert_events.call_args.args[0]
        assert [event["external_id"] for event in events] == [
            f"coachie:{_PLAN_ID}:{slot}"
        ]
        assert events[0]["start_date_local"] == f"{_THURSDAY}T00:00:00"
        names = _call_names(parent)
        assert names.index("repos.plan.update_pushed_events") < names.index(
            "client.bulk_upsert_events"
        )

    def test_a_failed_push_leaves_the_row_and_says_so(self):
        _, repos, client = _harness(_big_week())
        client.bulk_upsert_events.side_effect = RuntimeError("502")
        result = _schedule(repos, client=client)
        assert result["status"] == "ok"
        assert result["push"]["status"] == "error"
        repos.plan.bulk_insert_sessions.assert_called_once()
        assert any("Sync now" in text for text in result["caveats"])

    def test_without_a_client_nothing_is_pre_stored(self):
        _, repos, _ = _harness(_big_week(), with_client=False)
        result = _schedule(repos)
        assert result["status"] == "ok"
        assert result["push"]["status"] == "skipped"
        assert result["push"]["message"] == PUSH_SKIPPED_MESSAGE
        repos.plan.update_pushed_events.assert_not_called()
        repos.plan.bulk_insert_sessions.assert_called_once()


class TestCaveats:
    def test_the_caveats_speak_to_the_athlete_not_the_coach(self, monkeypatch):
        # Seen live: tool names and "Quote both numbers" reached the athlete.
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", _TODAY.isoformat())
        _, repos, _ = _harness(_big_week())
        caveats = _schedule(repos)["caveats"]
        assert any("on top of the week as planned" in text for text in caveats)
        assert not any("_" in text for text in caveats)
        # This week's extra is out of a replan's reach (it starts next Monday).
        assert not any("replan" in text for text in caveats)

    def test_an_extra_beyond_this_week_says_a_replan_drops_it(self, monkeypatch):
        monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-05-13")
        _, repos, _ = _harness(_big_week())
        caveats = _schedule(repos)["caveats"]
        assert (
            "A replan rebuilds the plan from next Monday and would drop this extra."
            in caveats
        )


class TestIsCoachExtra:
    @pytest.mark.parametrize(
        ("slot", "expected"),
        [
            ("x1:extra_2026-05-21_run_easy", True),
            ("x12:extra_2026-05-21_run_easy", True),
            ("menu:running:run_easy:w10:i1", False),
            ("r2:menu:running:run_easy:w10:i1", False),
            ("extra_2026-05-21_run_easy", False),
        ],
    )
    def test_only_the_extra_slot_pattern_counts(self, slot, expected):
        assert is_coach_extra(SimpleNamespace(slot_id=slot)) is expected

    def test_a_row_without_a_slot_id_is_not_an_extra(self):
        assert is_coach_extra(SimpleNamespace()) is False


class TestConstants:
    def test_the_easy_families_are_the_registry_ones(self):
        assert {family.value for family in guards.EASY_FAMILIES} == {
            "easy",
            "recovery",
        }

    @pytest.mark.parametrize(
        ("intent", "needs_allow_hard"),
        [
            ("threshold", True),
            ("vo2", True),
            ("long", True),
            ("brick", True),
            ("race_pace", True),
            ("easy", False),
            ("endurance", False),
            ("recovery", False),
            ("strides", False),
            ("test", True),
        ],
    )
    def test_every_intent_word_is_classified_as_the_spec_names_it(
        self, intent, needs_allow_hard
    ):
        """Drift guard: the five hard intents, and only those, need allow_hard."""
        labelled = {
            guards.hard_extra_label(activity) is not None
            for sport in SportType
            if (activity := pick_for_intent(sport, intent)) is not None
        }
        assert labelled == {needs_allow_hard}

    def test_the_phase_fallback_is_a_real_phase(self):
        assert PhaseType.BASE.value == "base"
