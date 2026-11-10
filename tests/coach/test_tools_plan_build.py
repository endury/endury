"""Tests for the plan-building coach tools (create_plan, record_self_assessment).

The underlying use case is monkeypatched: these tests check the tool's own
contract (races parsing, dependency binding, result relay, error
swallowing). The use case's own preview/refusal shapes are covered in
``tests/planning/test_create_plan.py``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from endury.coach.tools.plan_build_tools import make_plan_build_tools


def _get(tools, name):
    return next(t for t in tools if t.name == name)


def _noop_coach():
    return None


def _tools(repos=None, maybe_get_coach=_noop_coach, intervals_client=None):
    return make_plan_build_tools(
        repos or MagicMock(), maybe_get_coach, intervals_client
    )


def _create_plan_tool(monkeypatch, result, **tool_args):
    """Bind the tool over a monkeypatched use case and return (tool, spy)."""
    spy = MagicMock(return_value=result)
    monkeypatch.setattr("endury.coach.tools.plan_build_tools.run_create_plan", spy)
    return _get(_tools(**tool_args), "create_plan"), spy


_RACES = [{"goal_type": "running_marathon", "race_date": "2026-11-08", "priority": "A"}]

_PREVIEW = {
    "status": "preview",
    "confirm_required": True,
    "plan_name": "Marathon — 2026-11-08",
    "goal": "marathon",
    "races": _RACES,
    "plan_start": "2026-05-18",
    "weeks": 25,
    "phases": [
        {"phase": "base", "from": "2026-05-18", "through": "2026-08-02", "weeks": 11}
    ],
    "weekly": [
        {
            "week": 1,
            "from": "2026-05-18",
            "hours": 6.0,
            "tss": 210.0,
            "by_sport": {"running": 6.0},
        }
    ],
    "first_days": [
        {
            "date": "2026-05-18",
            "weekday": "Mon",
            "sport": "running",
            "workout_name": "Easy Run",
            "duration_minutes": 40.0,
            "target_tss": 30.0,
        }
    ],
    "warning": None,
    "athlete_state_sources": {"running": "history"},
    "replaces": None,
    "caveats": [],
    "message": "Nothing has been created yet. Confirm with the athlete.",
}


# create_plan


def test_create_plan_schema_exposes_races_as_a_string():
    tool = _get(_tools(), "create_plan")
    properties = tool.parameters["properties"]
    assert properties["races"] == {"type": "string"}
    # The app-only parameters never reach the model.
    assert "confirm" not in properties
    assert "expected_input_hash" not in properties
    assert tool.parameters["required"] == ["races"]


def test_create_plan_docstring_states_the_contract():
    tool = _get(_tools(), "create_plan")
    assert "Runs as a preview" in tool.description
    assert "athlete_data_required" in tool.description
    assert "horizon_too_short" in tool.description
    assert "record_self_assessment" in tool.description


def test_create_plan_preview_is_lean_and_names_the_phases(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result == _PREVIEW
    assert result["phases"][0]["phase"] == "base"
    assert "sessions" not in result
    assert spy.call_args.kwargs["confirm"] is False


def test_create_plan_preview_lists_only_the_first_ten_days(monkeypatch):
    tool, _ = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result["first_days"] == _PREVIEW["first_days"]


def test_create_plan_defaults_training_start_to_this_weeks_monday(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke({"races": json.dumps(_RACES)})
    assert spy.call_args.kwargs["training_start"] is None


def test_create_plan_refuses_a_non_monday_start(monkeypatch):
    refusal = {
        "status": "error",
        "message": "2026-05-19 is a Tuesday, and plans must start on a Monday.",
    }
    tool, spy = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(
        tool.invoke({"races": json.dumps(_RACES), "training_start": "2026-05-19"})
    )
    assert result == refusal
    assert spy.call_args.kwargs["training_start"] == "2026-05-19"


def test_create_plan_passes_the_directive_args_through(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke(
        {
            "races": json.dumps(_RACES),
            "resume_date": "2026-06-05",
            "suppress_tests_until": "2026-07-01",
        }
    )
    assert spy.call_args.kwargs["resume_date"] == "2026-06-05"
    assert spy.call_args.kwargs["suppress_tests_until"] == "2026-07-01"


def test_create_plan_directive_args_are_optional_strings(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    properties = tool.parameters["properties"]
    assert properties["resume_date"] == {"type": "string"}
    assert properties["suppress_tests_until"] == {"type": "string"}
    assert properties["unavailable"] == {"type": "string"}
    assert "resume_date" not in tool.parameters["required"]
    tool.invoke({"races": json.dumps(_RACES)})
    assert spy.call_args.kwargs["resume_date"] is None
    assert spy.call_args.kwargs["suppress_tests_until"] is None
    assert spy.call_args.kwargs["unavailable"] is None


def test_create_plan_passes_unavailable_through(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke(
        {
            "races": json.dumps(_RACES),
            "unavailable": '[["2026-10-01", "2026-10-05"]]',
        }
    )
    assert spy.call_args.kwargs["unavailable"] == '[["2026-10-01", "2026-10-05"]]'


def test_create_plan_passes_expected_input_hash_through(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke(
        {
            "races": json.dumps(_RACES),
            "confirm": True,
            "expected_input_hash": "abc123",
        }
    )
    assert spy.call_args.kwargs["expected_input_hash"] == "abc123"


def test_create_plan_parses_a_json_string_and_a_list_of_races(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke({"races": json.dumps(_RACES)})
    assert spy.call_args.kwargs["races"] == _RACES

    # Some models send an already-parsed list despite the string schema.
    tool.fn(races=_RACES)
    assert spy.call_args.kwargs["races"] == _RACES


def test_create_plan_rejects_unparsable_json(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    result = json.loads(tool.invoke({"races": "not json"}))
    assert result["status"] == "error"
    spy.assert_not_called()


def test_create_plan_rejects_a_single_race_object(monkeypatch):
    # Valid JSON, wrong shape: the object must be refused, never echoed
    # back as if it were this tool's own result.
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES[0])}))
    assert result["status"] == "error"
    assert "JSON array" in result["message"]
    spy.assert_not_called()


def test_create_plan_refuses_when_a_plan_is_active(monkeypatch):
    refusal = {
        "status": "error",
        "message": 'A plan is already active ("Marathon Block").',
        "replaces": {"plan_id": 1, "name": "Marathon Block", "goal_date": "2026-10-01"},
    }
    tool, _ = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result == refusal


def test_create_plan_replace_active_names_what_it_replaces(monkeypatch):
    preview = {
        **_PREVIEW,
        "replaces": {"plan_id": 1, "name": "Marathon Block", "goal_date": "2026-10-01"},
    }
    tool, spy = _create_plan_tool(monkeypatch, preview, repos=MagicMock())
    result = json.loads(
        tool.invoke({"races": json.dumps(_RACES), "replace_active": True})
    )
    assert result["replaces"]["plan_id"] == 1
    assert spy.call_args.kwargs["replace_active"] is True


def test_create_plan_passes_accept_band_mismatch_through(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke({"races": json.dumps(_RACES), "accept_band_mismatch": True})
    assert spy.call_args.kwargs["accept_band_mismatch"] is True


def test_create_plan_accept_band_mismatch_defaults_to_false(monkeypatch):
    tool, spy = _create_plan_tool(monkeypatch, _PREVIEW, repos=MagicMock())
    tool.invoke({"races": json.dumps(_RACES)})
    assert spy.call_args.kwargs["accept_band_mismatch"] is False


def test_create_plan_relays_a_trail_band_mismatch(monkeypatch):
    refusal = {
        "status": "error",
        "error_code": "trail_band_mismatch",
        "message": (
            "Challenge Walchsee on 2026-09-06 is 25 km / 900 m = 34 "
            "km-effort, which is Trail 20K, not Trail 50K. Call again with "
            "goal_type trail_20k — or with accept_band_mismatch=true if the "
            "athlete explicitly wants the Trail 50K plan."
        ),
        "suggested_goal_type": "trail_20k",
    }
    tool, _ = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result == refusal


def test_create_plan_docstring_names_the_trail_band_backstop():
    tool = _get(_tools(), "create_plan")
    description = tool.description
    assert "trail_band_mismatch" in description
    assert "suggested_goal_type" in description
    assert "accept_band_mismatch=true" in description


def test_create_plan_docstring_names_the_directives_echo():
    tool = _get(_tools(), "create_plan")
    assert "directives" in tool.description


def test_create_plan_relays_athlete_data_required(monkeypatch):
    refusal = {
        "status": "error",
        "message": "intervals.icu history can't measure cycling.",
        "error_code": "athlete_data_required",
        "missing_sports": ["cycling"],
    }
    tool, _ = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result["error_code"] == "athlete_data_required"
    assert result["missing_sports"] == ["cycling"]


def test_create_plan_relays_horizon_too_short_with_the_week_counts(monkeypatch):
    refusal = {
        "status": "error",
        "message": "Only 8 weeks to race day.",
        "error_code": "horizon_too_short",
        "min_weeks": 12,
        "actual_weeks": 8,
        "deficit_weeks": 4,
    }
    tool, _ = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result["min_weeks"] == 12
    assert result["actual_weeks"] == 8
    assert result["deficit_weeks"] == 4


def test_create_plan_relays_an_infeasible_solve(monkeypatch):
    refusal = {
        "status": "error",
        "message": "This plan can't be built with the current race date.",
    }
    tool, _ = _create_plan_tool(monkeypatch, refusal, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert result == refusal


def test_create_plan_confirm_commits_through_run_commit(monkeypatch):
    ok = {
        "status": "ok",
        "plan_id": 42,
        "plan_name": _PREVIEW["plan_name"],
        "goal": "marathon",
        "plan_start": "2026-05-18",
        "weeks": 25,
        "push": {"status": "ok", "pushed": 40},
        "caveats": [],
        "message": 'Created "Marathon" — 25 weeks starting 2026-05-18.',
    }
    repo = MagicMock()
    client = MagicMock()
    coach_accessor = _noop_coach
    tool, spy = _create_plan_tool(
        monkeypatch,
        ok,
        repos=repo,
        maybe_get_coach=coach_accessor,
        intervals_client=client,
    )
    result = json.loads(tool.invoke({"races": json.dumps(_RACES), "confirm": True}))
    assert result["status"] == "ok"
    assert result["plan_id"] == 42
    kwargs = spy.call_args.kwargs
    assert kwargs["repos"] is repo
    assert kwargs["client"] is client
    assert kwargs["maybe_get_coach"] is coach_accessor
    assert kwargs["confirm"] is True


def test_create_plan_confirm_reports_a_failed_push(monkeypatch):
    ok_with_failed_push = {
        "status": "ok",
        "plan_id": 7,
        "plan_name": _PREVIEW["plan_name"],
        "goal": "marathon",
        "plan_start": "2026-05-18",
        "weeks": 25,
        "push": {
            "status": "error",
            "message": "The plan was saved, but pushing it to intervals.icu failed.",
        },
        "caveats": ["The plan was saved, but pushing it to intervals.icu failed."],
        "message": 'Created "Marathon" — 25 weeks starting 2026-05-18.',
    }
    tool, _ = _create_plan_tool(monkeypatch, ok_with_failed_push, repos=MagicMock())
    result = json.loads(tool.invoke({"races": json.dumps(_RACES), "confirm": True}))
    assert result["push"]["status"] == "error"
    assert result["caveats"]


def test_create_plan_swallows_unexpected_errors(monkeypatch):
    monkeypatch.setattr(
        "endury.coach.tools.plan_build_tools.run_create_plan",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    tool = _get(_tools(repos=MagicMock()), "create_plan")
    result = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    assert "error" in result
    assert "boom" not in json.dumps(result)


# create_plan — the experimental_plan confirmation

_EXPERIMENTAL_REFUSAL = {
    "status": "error",
    "message": (
        "This plan type is built from training research and has not yet been "
        "through the checks our other plans have passed. You can still build "
        "it. Confirm to go ahead."
    ),
    "error_code": "experimental_plan",
    "reasons": ["goal_experimental"],
}


def _refuse_until_accepted(**kwargs):
    """The use case: refuses experimental_plan until the flag is passed."""
    if not kwargs["accept_experimental"]:
        return _EXPERIMENTAL_REFUSAL
    if kwargs["confirm"]:
        return {"status": "ok", "message": 'Created "10K".'}
    return {**_PREVIEW, "maturity": "experimental", "input_hash": "hash-1"}


def test_create_plan_docstring_names_the_experimental_confirmation():
    description = _get(_tools(), "create_plan").description
    assert "experimental_plan" in description
    assert "accept_experimental=true" in description


def test_create_plan_relays_experimental_plan_and_passes_the_flag_only_when_asked(
    monkeypatch,
):
    spy = MagicMock(side_effect=_refuse_until_accepted)
    monkeypatch.setattr("endury.coach.tools.plan_build_tools.run_create_plan", spy)
    tool = _get(_tools(), "create_plan")

    first = json.loads(tool.invoke({"races": json.dumps(_RACES)}))
    second = json.loads(
        tool.invoke({"races": json.dumps(_RACES), "accept_experimental": True})
    )

    assert first == _EXPERIMENTAL_REFUSAL
    assert [call.kwargs["accept_experimental"] for call in spy.call_args_list] == [
        False,
        True,
    ]
    assert second["status"] == "preview"


def test_a_confirmed_experimental_preview_becomes_an_apply_card(monkeypatch):
    from endury.coach.proposals import apply_proposal, proposal_from_result, run_tool

    spy = MagicMock(side_effect=_refuse_until_accepted)
    monkeypatch.setattr("endury.coach.tools.plan_build_tools.run_create_plan", spy)
    tools = _tools()
    repos = MagicMock()
    repos.plan.get_active.return_value = None
    refused_args = {"races": json.dumps(_RACES)}
    agreed_args = {**refused_args, "accept_experimental": True}

    refusal = run_tool(tools, "create_plan", refused_args)
    no_card = proposal_from_result(tools, repos, "create_plan", refused_args, refusal)
    preview = run_tool(tools, "create_plan", agreed_args)
    card = proposal_from_result(tools, repos, "create_plan", agreed_args, preview)

    assert no_card is None
    assert card is not None
    assert card.args["accept_experimental"] is True
    outcome = apply_proposal(tools, repos, card)
    assert outcome.status == "applied"
    applied = spy.call_args.kwargs
    assert applied["confirm"] is True
    assert applied["accept_experimental"] is True
    assert applied["expected_input_hash"] == "hash-1"


# record_self_assessment


def test_record_self_assessment_stores_one_sport():
    repo = MagicMock()
    repo.self_assessment.get_all.return_value = {
        "running": SimpleNamespace(weekly_hours=6.0, longest_session_min=90)
    }
    tool = _get(_tools(repos=repo), "record_self_assessment")
    result = json.loads(
        tool.invoke(
            {"sport": "running", "weekly_hours": 6.0, "longest_session_min": 90}
        )
    )
    assert result["status"] == "ok"
    assert result["sport"] == "running"
    assert result["stored"]["running"] == {
        "weekly_hours": 6.0,
        "longest_session_min": 90,
    }
    repo.self_assessment.upsert_many.assert_called_once_with({"running": (6.0, 90)})


def test_record_self_assessment_rejects_an_unknown_sport():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "record_self_assessment")
    result = json.loads(
        tool.invoke({"sport": "rowing", "weekly_hours": 5.0, "longest_session_min": 60})
    )
    assert result["status"] == "error"
    assert "options" in result
    repo.self_assessment.upsert_many.assert_not_called()


def test_record_self_assessment_rejects_out_of_bounds_values():
    repo = MagicMock()
    tool = _get(_tools(repos=repo), "record_self_assessment")
    result = json.loads(
        tool.invoke(
            {"sport": "running", "weekly_hours": 999, "longest_session_min": 60}
        )
    )
    assert result["status"] == "error"
    repo.self_assessment.upsert_many.assert_not_called()


def test_record_self_assessment_swallows_unexpected_errors():
    repo = MagicMock()
    repo.self_assessment.upsert_many.side_effect = RuntimeError("boom")
    tool = _get(_tools(repos=repo), "record_self_assessment")
    result = json.loads(
        tool.invoke(
            {"sport": "running", "weekly_hours": 5.0, "longest_session_min": 60}
        )
    )
    assert "error" in result
    assert "boom" not in json.dumps(result)
