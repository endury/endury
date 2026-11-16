"""Tests for the set_plan_directives coach tool.

Everything ``set_plan_directives`` calls into (``_load_replan_inputs``,
``replan_readback_diff``, ``run_replan``) is monkeypatched at the module
level, the same idiom ``test_tools_plan_build.py`` uses for a use case —
here the use case lives in the tool itself (there is no separate ``planning/``
module for it), so the module under test is
patched directly rather than an imported use-case function.
"""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from endury.coach.tools import plan_directive_tools as mod
from endury.coach.tools.plan_directive_tools import make_plan_directive_tools
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE

_TODAY = date(2026, 5, 20)  # a Wednesday
_CUTOFF = date(2026, 5, 25)  # next Monday


def _get(tools, name):
    return next(t for t in tools if t.name == name)


def _row(
    day: str, activity_key: str = "run_easy", sport: str = "running", slot: str = "s1"
):
    return SimpleNamespace(
        date=day, activity_key=activity_key, sport=sport, slot_id=slot
    )


def _active_and_solve(directives: dict | None = None) -> tuple[SimpleNamespace, dict]:
    request_block: dict = {
        "races": [
            {
                "goal_type": "running_marathon",
                "race_date": "2026-11-08",
                "priority": "A",
            }
        ],
        "training_start": "2026-03-09",
        "ambition_level": "moderate",
        "accept_short_horizon": True,
    }
    if directives:
        request_block["directives"] = directives
    return SimpleNamespace(id=1), {"sessions": [], "request": request_block}


_TRIMMED = {
    "status": "ok",
    "window": {"from": "2026-06-05", "through": "2026-06-11"},
    "reduce_percent": 30,
    "totals": {},
    "sessions": [],
    "unchanged": [],
    "untouched": [],
    "push": {"status": "ok"},
    "message": "Trimmed 3 session(s) by 30% in your first week back.",
}


def _tool(
    monkeypatch,
    *,
    active_solve=None,
    diff=None,
    replan_result=None,
    rows=None,
    trim=None,
):
    active, solve = active_solve or _active_and_solve()
    monkeypatch.setattr(mod, "_load_replan_inputs", lambda repos: (active, solve))
    monkeypatch.setattr(mod, "replan_readback_diff", lambda **kwargs: diff or [])
    monkeypatch.setattr(
        mod, "trim_first_week", MagicMock(return_value=trim or _TRIMMED)
    )
    replan_spy = MagicMock(
        return_value=replan_result
        or {
            "status": "ok",
            "action": "replanned",
            "reasoning": "Regenerated upcoming sessions.",
            "push": {"status": "ok"},
        }
    )
    monkeypatch.setattr(mod, "run_replan", replan_spy)
    repos = MagicMock()
    repos.plan.get_sessions.return_value = rows or []
    tools = make_plan_directive_tools(repos, lambda: None, None)
    return _get(tools, "set_plan_directives"), repos, solve, replan_spy


class TestNoDirectiveGiven:
    def test_refuses_naming_replan_active_plan(self, monkeypatch):
        def _boom(repos):
            raise AssertionError("must not touch the active plan")

        monkeypatch.setattr(mod, "_load_replan_inputs", _boom)
        tools = make_plan_directive_tools(MagicMock(), lambda: None, None)
        result = json.loads(_get(tools, "set_plan_directives").invoke({}))
        assert result["status"] == "error"
        assert "replan_active_plan" in result["message"]

    def test_refuses_a_bare_confirm_with_nothing_new_to_say(self, monkeypatch):
        def _boom(repos):
            raise AssertionError("must not touch the active plan")

        monkeypatch.setattr(mod, "_load_replan_inputs", _boom)
        tools = make_plan_directive_tools(MagicMock(), lambda: None, None)
        result = json.loads(
            _get(tools, "set_plan_directives").invoke({"confirm": True})
        )
        assert result["status"] == "error"
        assert "replan_active_plan" in result["message"]


class TestNoActivePlan:
    def test_no_active_plan_is_refused(self, monkeypatch):
        def _raise(repos):
            raise ValueError(NO_ACTIVE_PLAN_MESSAGE)

        monkeypatch.setattr(mod, "_load_replan_inputs", _raise)
        monkeypatch.setattr(mod, "replan_readback_diff", lambda **kwargs: [])
        monkeypatch.setattr(mod, "run_replan", MagicMock())
        tools = make_plan_directive_tools(MagicMock(), lambda: None, None)
        result = json.loads(
            _get(tools, "set_plan_directives").invoke({"resume_date": "2026-06-05"})
        )
        assert result["status"] == "error"
        assert result["message"] == NO_ACTIVE_PLAN_MESSAGE
        assert "create_plan" in result["message"]


class TestPreview:
    def test_preview_names_directives_before_and_after(self, monkeypatch):
        tool, repos, _solve, _spy = _tool(
            monkeypatch, active_solve=_active_and_solve({"weekly_hours_cap": 8.0})
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-06-05"}))
        assert result["status"] == "preview"
        assert result["confirm_required"] is True
        assert result["directives"]["before"] == {"weekly_hours_cap": 8.0}
        assert result["directives"]["after"] == {
            "weekly_hours_cap": 8.0,
            "resume_date": "2026-06-05",
        }
        repos.plan.update_solve_result_json.assert_not_called()

    def test_preview_reports_sessions_the_directives_would_drop(self, monkeypatch):
        rows = [
            _row("2026-05-26", slot="before"),
            _row("2026-06-10", slot="after"),
        ]
        tool, _repos, _solve, _spy = _tool(monkeypatch, rows=rows)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-06-05"}))
        assert [r["date"] for r in result["removes"]] == ["2026-05-26"]

    def test_preview_warns_when_the_directives_empty_the_plan(self, monkeypatch):
        rows = [_row("2026-05-26", slot="a"), _row("2026-05-27", slot="b")]
        tool, _repos, _solve, _spy = _tool(monkeypatch, rows=rows)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-12-01"}))
        assert len(result["removes"]) == 2
        assert any("would be left empty" in caveat for caveat in result["caveats"])

    def test_preview_relays_edited_workouts_as_a_caveat(self, monkeypatch):
        diff = [{"external_id": "x", "date": "2026-06-01", "name": "Long Run"}]
        tool, _repos, _solve, _spy = _tool(monkeypatch, diff=diff)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-06-05"}))
        assert result["edited_workouts"] == diff
        assert any("edited on intervals.icu" in c for c in result["caveats"])

    def test_always_carries_the_current_week_caveat(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-06-05"}))
        assert any("never change this week" in c for c in result["caveats"])


class TestClearing:
    def test_empty_string_clears_a_stored_field(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(
            monkeypatch, active_solve=_active_and_solve({"resume_date": "2026-06-01"})
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": ""}))
        assert result["directives"]["after"] == {}

    def test_none_clears_it_too(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(
            monkeypatch, active_solve=_active_and_solve({"unavailable": [["a", "b"]]})
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"unavailable": "none"}))
        assert result["directives"]["after"] == {}


class TestValidation:
    def test_refuses_an_invalid_date(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "not-a-date"}))
        assert result["status"] == "error"

    def test_refuses_malformed_unavailable_json(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"unavailable": "not json"}))
        assert result["status"] == "error"

    def test_accepts_an_already_parsed_unavailable_list(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"unavailable": json.dumps([["2026-06-01", "2026-06-05"]])})
            )
        assert result["status"] == "preview"
        assert result["directives"]["after"]["unavailable"] == [
            ["2026-06-01", "2026-06-05"]
        ]

    def test_accepts_an_already_parsed_unavailable_list_object(self, monkeypatch):
        # Some models send an already-parsed list despite the string schema.
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.fn(unavailable=[["2026-06-01", "2026-06-05"]]))
        assert result["status"] == "preview"
        assert result["directives"]["after"]["unavailable"] == [
            ["2026-06-01", "2026-06-05"]
        ]

    def test_refuses_a_resume_date_inside_the_current_week(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-05-22"}))
        assert result["status"] == "error"
        assert "pause_training" in result["message"]

    def test_a_past_resume_date_is_inert_not_refused(self, monkeypatch):
        # Stale/already-elapsed, not "inside the current week" — apply_directives
        # already treats it as inert; the tool must not refuse it either.
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2020-01-01"}))
        assert result["status"] == "preview"

    def test_a_resume_date_on_next_monday_is_allowed(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": _CUTOFF.isoformat()}))
        assert result["status"] == "preview"


class TestConfirm:
    def test_confirm_persists_directives_and_calls_run_replan(self, monkeypatch):
        tool, repos, _solve, spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert result["status"] == "ok"
        assert result["action"] == "replanned"
        assert result["push"] == {"status": "ok"}
        repos.plan.update_solve_result_json.assert_called_once()
        plan_id, stored_json = repos.plan.update_solve_result_json.call_args.args
        assert plan_id == 1
        assert json.loads(stored_json)["request"]["directives"] == {
            "resume_date": "2026-06-05"
        }
        spy.assert_called_once()

    def test_confirm_updates_the_flat_weekly_hours_cap_too(self, monkeypatch):
        tool, repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            tool.invoke({"weekly_hours_cap": 9.5, "confirm": True})
        stored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        assert stored["request"]["weekly_hours_cap"] == 9.5
        assert stored["request"]["directives"] == {"weekly_hours_cap": 9.5}

    def test_confirm_with_no_directives_left_clears_the_stored_key(self, monkeypatch):
        tool, repos, _solve, _spy = _tool(
            monkeypatch, active_solve=_active_and_solve({"resume_date": "2026-06-01"})
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            tool.invoke({"resume_date": "", "confirm": True})
        stored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        assert "directives" not in stored["request"]

    def test_run_replan_value_error_is_relayed(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        monkeypatch.setattr(
            mod,
            "run_replan",
            MagicMock(side_effect=ValueError("The goal race has passed.")),
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert result["status"] == "error"
        assert result["message"] == "The goal race has passed."

    def test_a_refused_replan_puts_the_stored_request_back(self, monkeypatch):
        tool, repos, _solve, _spy = _tool(monkeypatch)
        monkeypatch.setattr(
            mod, "run_replan", MagicMock(side_effect=ValueError("Race week."))
        )
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert result["status"] == "error"
        # Written once with the directives, once back without them: a plan
        # nothing was regenerated around must not carry them into the next
        # replan.
        assert repos.plan.update_solve_result_json.call_count == 2
        restored = json.loads(repos.plan.update_solve_result_json.call_args.args[1])
        assert "directives" not in restored["request"]


class TestFirstWeekBack:
    def test_confirm_trims_the_seven_days_from_a_future_resume_date(self, monkeypatch):
        tool, repos, _solve, _spy = _tool(monkeypatch)
        trim = MagicMock(return_value=_TRIMMED)
        monkeypatch.setattr(mod, "trim_first_week", trim)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert trim.call_args.kwargs["resume"] == date(2026, 6, 5)
        assert trim.call_args.kwargs["repos"] is repos
        assert result["first_week"] == _TRIMMED
        assert _TRIMMED["message"] in result["caveats"]

    def test_no_resume_date_trims_nothing(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        trim = MagicMock(return_value=_TRIMMED)
        monkeypatch.setattr(mod, "trim_first_week", trim)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"weekly_hours_cap": 9.0, "confirm": True}))
        trim.assert_not_called()
        assert result["first_week"] is None

    def test_a_past_resume_date_trims_nothing(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        trim = MagicMock(return_value=_TRIMMED)
        monkeypatch.setattr(mod, "trim_first_week", trim)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2020-01-01", "confirm": True})
            )
        trim.assert_not_called()
        assert result["first_week"] is None

    def test_a_refused_trim_is_relayed_and_the_replan_stands(self, monkeypatch):
        refusal = {"status": "error", "message": "No sessions in that week."}
        tool, _repos, _solve, _spy = _tool(monkeypatch, trim=refusal)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert result["status"] == "ok"
        assert any("full plan volume" in caveat for caveat in result["caveats"])

    def test_a_failed_trim_push_is_relayed(self, monkeypatch):
        partial = {
            **_TRIMMED,
            "push": {"status": "error", "message": "intervals.icu rejected 2 events."},
        }
        tool, _repos, _solve, _spy = _tool(monkeypatch, trim=partial)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(
                tool.invoke({"resume_date": "2026-06-05", "confirm": True})
            )
        assert "intervals.icu rejected 2 events." in result["caveats"]

    def test_the_preview_announces_the_trim(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2026-06-05"}))
        assert any("2026-06-05 come back" in caveat for caveat in result["caveats"])

    def test_the_preview_says_a_past_resume_date_changes_nothing(self, monkeypatch):
        tool, _repos, _solve, _spy = _tool(monkeypatch)
        with patch("endury.common.clock.today", return_value=_TODAY):
            result = json.loads(tool.invoke({"resume_date": "2020-01-01"}))
        assert any("already behind us" in caveat for caveat in result["caveats"])


class TestErrorHandling:
    def test_swallows_unexpected_errors(self, monkeypatch):
        def _raise(repos):
            raise RuntimeError("boom")

        monkeypatch.setattr(mod, "_load_replan_inputs", _raise)
        tools = make_plan_directive_tools(MagicMock(), lambda: None, None)
        result = json.loads(
            _get(tools, "set_plan_directives").invoke({"resume_date": "2026-06-05"})
        )
        assert "error" in result
        assert "boom" not in json.dumps(result)
