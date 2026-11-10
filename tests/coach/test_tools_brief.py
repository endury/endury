"""The ``get_brief`` coach tool: argument resolution, contract, errors."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.tools.brief_tools import make_brief_tools
from endury.coach.tools.registry import execute_tool, with_reply_contract

pytestmark = pytest.mark.coach

SHEET = {"subject": {"handle": "session:5"}, "thin": []}


def _repos(*, active=True, sessions=()):
    repos = MagicMock()
    repos.plan.get_active.return_value = SimpleNamespace(id=1) if active else None
    repos.plan.get_sessions_by_date_range.return_value = list(sessions)
    return repos


def _row(row_id, minutes, key="run_easy"):
    return SimpleNamespace(id=row_id, duration_minutes=minutes, activity_key=key)


def _tool(repos):
    [tool] = make_brief_tools(repos, MagicMock())
    return tool


def test_a_session_id_goes_straight_to_the_builder():
    repos = _repos()
    with patch("endury.coach.tools.brief_tools.build_brief", return_value=SHEET) as b:
        result = json.loads(_tool(repos).invoke({"session_id": 5}))

    assert result == SHEET
    assert b.call_args.kwargs["session_id"] == 5
    assert b.call_args.kwargs["race_date"] is None


def test_a_race_date_goes_straight_to_the_builder():
    with patch("endury.coach.tools.brief_tools.build_brief", return_value=SHEET) as b:
        _tool(_repos()).invoke({"race_date": "2026-10-24"})

    assert b.call_args.kwargs["race_date"] == "2026-10-24"
    assert b.call_args.kwargs["session_id"] is None


def test_a_date_briefs_the_longest_session_and_names_the_others():
    repos = _repos(sessions=[_row(1, 45, "run_easy"), _row(2, 150, "run_long")])
    with patch("endury.coach.tools.brief_tools.build_brief", return_value=SHEET) as b:
        result = json.loads(_tool(repos).invoke({"date": "2026-10-25"}))

    assert b.call_args.kwargs["session_id"] == 2
    assert result["other_sessions_that_day"] == ["run_easy"]
    assert repos.plan.get_sessions_by_date_range.call_args.args == (
        1,
        "2026-10-25",
        "2026-10-25",
    )


@pytest.mark.parametrize("active", [True, False], ids=["rest day", "no plan"])
def test_a_date_with_nothing_planned_is_a_message(active):
    with patch("endury.coach.tools.brief_tools.build_brief") as b:
        result = json.loads(_tool(_repos(active=active)).invoke({"date": "2026-10-25"}))

    assert result == {"message": "No session is planned on 2026-10-25."}
    b.assert_not_called()


@pytest.mark.parametrize(
    "args", [{}, {"session_id": 5, "date": "2026-10-25"}], ids=["none", "two"]
)
def test_exactly_one_subject_is_needed(args):
    with patch("endury.coach.tools.brief_tools.build_brief") as b:
        result = json.loads(_tool(_repos()).invoke(args))

    assert "exactly one" in result["message"]
    b.assert_not_called()


def test_an_unknown_subject_is_a_message():
    with patch(
        "endury.coach.tools.brief_tools.build_brief",
        side_effect=ValueError("No planned session 9."),
    ):
        result = json.loads(_tool(_repos()).invoke({"session_id": 9}))

    assert result == {"message": "No planned session 9."}


def test_a_failure_is_the_standard_error_response():
    with patch(
        "endury.coach.tools.brief_tools.build_brief", side_effect=RuntimeError("boom")
    ):
        result = json.loads(
            execute_tool([_tool(_repos())], "get_brief", {"session_id": 5})
        )

    assert result["error"] == "Failed to build the brief."


def test_the_reply_contract_rides_on_the_result():
    tool = _tool(_repos())
    with patch("endury.coach.tools.brief_tools.build_brief", return_value=SHEET):
        result = json.loads(with_reply_contract(tool, tool.invoke({"session_id": 5})))

    contract = " ".join(result["reply_contract"])
    assert "three sentences" in contract
    assert "**Where you stand**" in contract
    assert "swap_planned_session" in contract
    assert "Reply contract" not in tool.description
    assert tool.parameters["properties"].keys() == {"session_id", "race_date", "date"}
