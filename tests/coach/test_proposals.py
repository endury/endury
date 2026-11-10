"""Tests for endury.coach.proposals — Apply cards."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from endury.coach.proposals import (
    PLAN_WRITE_TOOLS,
    ApplyResult,
    Proposal,
    apply_proposal,
    is_gated,
    is_stale,
    plan_stamp,
    proposal_from_result,
    refresh_proposal,
    run_tool,
)
from endury.coach.tools.registry import coach_tool

_TODAY = "2026-09-23"


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", _TODAY)


# Stand-in tools

calls: list[dict] = []


@coach_tool
def pause_training(through_date: str, confirm: bool = False) -> str:
    """A gated write: previews, then applies."""
    calls.append({"through_date": through_date, "confirm": confirm})
    if not confirm:
        return json.dumps(
            {
                "status": "preview",
                "confirm_required": True,
                "window": {"from": _TODAY, "through": through_date},
                "sessions": [{"session_id": 1}],
                "resume_date": "2026-09-30",
                "message": "Nothing has changed yet.",
            }
        )
    return json.dumps({"status": "ok", "message": f"Paused through {through_date}."})


@coach_tool
def create_plan(
    races: str, confirm: bool = False, expected_input_hash: str | None = None
) -> str:
    """A gated write with a freshness hash."""
    calls.append({"races": races, "confirm": confirm, "hash": expected_input_hash})
    if not confirm or expected_input_hash != "hash-1":
        return json.dumps(
            {
                "status": "preview",
                "confirm_required": True,
                "input_hash": "hash-1",
                "plan_name": "Marathon",
                "goal": "marathon",
                "races": [
                    {
                        "goal_type": "running_marathon",
                        "race_date": "2026-12-13",
                        "priority": "A",
                    }
                ],
                "weeks": 12,
                "plan_start": "2026-09-21",
                "message": "Nothing has been created yet.",
            }
        )
    return json.dumps({"status": "ok", "message": 'Created "Marathon".'})


@coach_tool
def schedule_workout(date: str) -> str:
    """An immediate write: no confirm parameter."""
    calls.append({"date": date})
    return json.dumps({"status": "ok", "message": f"Booked {date}."})


@coach_tool
def refusing_tool(confirm: bool = False) -> str:
    """A gated tool that refuses."""
    return json.dumps({"status": "refused", "message": "No."})


@coach_tool
def prose_tool(confirm: bool = False) -> str:
    """A gated tool whose payload is not JSON."""
    return "not json"


TOOLS = [pause_training, create_plan, schedule_workout, refusing_tool, prose_tool]


@pytest.fixture(autouse=True)
def _clear_calls():
    calls.clear()


def _session(session_id=1, status="pending", day="2026-09-24"):
    return MagicMock(
        id=session_id, date=day, status=status, activity_key="run_easy", target_tss=40.0
    )


def _repos(plan_id=7, updated_at="2026-09-23T10:00:00", sessions=None):
    repos = MagicMock()
    if plan_id is None:
        repos.plan.get_active.return_value = None
    else:
        repos.plan.get_active.return_value = MagicMock(
            id=plan_id, updated_at=updated_at
        )
    repos.plan.get_sessions.return_value = (
        [_session()] if sessions is None else sessions
    )
    return repos


def _revision(repos):
    return plan_stamp(repos)[1]


def _preview(tool="pause_training", args=None, repos=None):
    args = args if args is not None else {"through_date": "2026-09-28"}
    repos = repos or _repos()
    return proposal_from_result(TOOLS, repos, tool, args, run_tool(TOOLS, tool, args))


# run_tool — the dispatcher never confirms


def test_is_gated_reads_the_signature():
    assert is_gated(pause_training) is True
    assert is_gated(schedule_workout) is False


def test_run_tool_forces_a_gated_tool_to_preview_whatever_the_model_sent():
    result = json.loads(
        run_tool(
            TOOLS, "pause_training", {"through_date": "2026-09-28", "confirm": True}
        )
    )
    assert result["status"] == "preview"
    assert calls == [{"through_date": "2026-09-28", "confirm": False}]


def test_run_tool_drops_the_apps_own_hash_argument():
    run_tool(TOOLS, "create_plan", {"races": "[]", "expected_input_hash": "stale"})
    assert calls == [{"races": "[]", "confirm": False, "hash": None}]


def test_run_tool_runs_an_ungated_tool_as_called():
    result = json.loads(run_tool(TOOLS, "schedule_workout", {"date": "2026-09-25"}))
    assert result["status"] == "ok"
    assert calls == [{"date": "2026-09-25"}]


def test_run_tool_passes_an_unknown_tool_through_to_the_registry_error():
    assert "Unknown tool" in run_tool(TOOLS, "nope", {})


# proposal_from_result — when a result becomes a card


def test_a_preview_becomes_a_stamped_proposal():
    proposal = _preview()
    assert proposal is not None
    assert proposal.tool == "pause_training"
    assert proposal.args == {"through_date": "2026-09-28"}
    assert proposal.date == _TODAY
    assert proposal.plan_id == 7
    assert proposal.plan_revision == _revision(_repos())
    assert proposal.id
    assert proposal.preview_summary == (
        "Pause training from Wednesday 23 September to Monday 28 September "
        "(1 session skipped) and resume on Wednesday 30 September."
    )


def test_the_previews_input_hash_rides_on_the_card_for_create_plan():
    proposal = _preview("create_plan", {"races": "[]"})
    assert proposal is not None
    assert proposal.args == {"races": "[]", "expected_input_hash": "hash-1"}


def test_the_confirm_argument_never_rides_on_the_card():
    proposal = _preview(args={"through_date": "2026-09-28", "confirm": True})
    assert proposal is not None
    assert "confirm" not in proposal.args


def test_an_ungated_tool_never_yields_a_proposal():
    result = run_tool(TOOLS, "schedule_workout", {"date": "2026-09-25"})
    assert (
        proposal_from_result(TOOLS, _repos(), "schedule_workout", {"date": "x"}, result)
        is None
    )


def test_a_refusal_and_a_non_json_payload_never_yield_a_proposal():
    assert _preview("refusing_tool", {}) is None
    assert _preview("prose_tool", {}) is None


def test_a_preview_that_offers_nothing_to_apply_yields_no_proposal():
    no_fit = json.dumps({"status": "preview", "confirm_required": False})
    assert proposal_from_result(TOOLS, _repos(), "pause_training", {}, no_fit) is None


def test_a_pause_with_nothing_pending_but_an_active_plan_is_still_a_card():
    # Applying it records the window as unavailable on the plan.
    empty = json.dumps(
        {"status": "empty", "window": {"from": _TODAY, "through": "2026-09-28"}}
    )
    proposal = proposal_from_result(TOOLS, _repos(), "pause_training", {}, empty)
    assert proposal is not None
    assert proposal.preview_summary == (
        "Mark yourself unavailable from Wednesday 23 September to Monday "
        "28 September. Nothing is planned then, so no sessions change."
    )


def test_a_plan_less_empty_pause_is_no_card():
    empty = json.dumps({"status": "empty", "no_plan": True})
    assert proposal_from_result(TOOLS, _repos(), "pause_training", {}, empty) is None


def test_plan_stamp_without_a_plan_or_with_an_unreadable_one_is_blank():
    assert plan_stamp(_repos(plan_id=None)) == (None, None)
    broken = MagicMock()
    broken.plan.get_active.side_effect = RuntimeError("db gone")
    assert plan_stamp(broken) == (None, None)


def test_proposal_round_trips_through_its_wire_dict():
    proposal = _preview()
    assert proposal is not None
    assert Proposal.from_dict(proposal.to_dict()) == proposal
    assert Proposal.from_dict(json.loads(json.dumps(proposal.to_dict()))) == proposal


# is_stale


def test_a_fresh_stamp_is_not_stale():
    assert is_stale(_preview(), _repos()) is False


def test_another_day_is_stale(monkeypatch):
    proposal = _preview()
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-24")
    assert is_stale(proposal, _repos()) is True


def test_a_plan_written_since_is_stale():
    proposal = _preview()
    assert is_stale(proposal, _repos(updated_at="2026-09-23T11:00:00")) is True


def test_a_skipped_or_moved_session_is_stale():
    proposal = _preview()
    assert is_stale(proposal, _repos(sessions=[_session(status="skipped")])) is True
    assert is_stale(proposal, _repos(sessions=[_session(day="2026-09-25")])) is True


def test_a_replaced_plan_is_stale():
    proposal = _preview()
    assert is_stale(proposal, _repos(plan_id=8)) is True


# apply_proposal


def test_apply_runs_the_write_with_confirm_true_and_the_cards_arguments():
    proposal = _preview()
    calls.clear()
    outcome = apply_proposal(TOOLS, _repos(), proposal)
    assert outcome.status == "applied"
    assert outcome.message == "Paused through 2026-09-28."
    assert outcome.plan_changed is True
    assert outcome.proposal is None
    assert calls == [{"through_date": "2026-09-28", "confirm": True}]


def test_apply_passes_the_cards_input_hash_to_create_plan():
    proposal = _preview("create_plan", {"races": "[]"})
    calls.clear()
    outcome = apply_proposal(TOOLS, _repos(), proposal)
    assert outcome.status == "applied"
    assert calls == [{"races": "[]", "confirm": True, "hash": "hash-1"}]


def test_a_stale_card_re_previews_and_comes_back_as_a_replacement(monkeypatch):
    proposal = _preview()
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-24")
    calls.clear()
    outcome = apply_proposal(TOOLS, _repos(), proposal)
    assert outcome.status == "preview"
    assert outcome.reason == "stale"
    assert outcome.plan_changed is False
    assert outcome.proposal is not None
    assert outcome.proposal.date == "2026-09-24"
    assert outcome.proposal.id != proposal.id
    # Nothing was confirmed.
    assert calls == [{"through_date": "2026-09-28", "confirm": False}]


def test_refreshing_a_current_card_runs_nothing():
    proposal = _preview()
    calls.clear()
    outcome = refresh_proposal(TOOLS, _repos(), proposal)
    assert outcome.status == "current"
    assert outcome.proposal is None
    assert calls == []


def test_refreshing_a_card_another_write_left_stale_re_previews_it():
    # The athlete applied another card of the same reply: the plan moved, so
    # this one is brought up to date at once, and one tap then applies it.
    proposal = _preview()
    moved = _repos(updated_at="2026-09-23T11:00:00")
    calls.clear()
    outcome = refresh_proposal(TOOLS, moved, proposal)
    assert outcome.status == "preview"
    assert outcome.reason == "stale"
    assert outcome.proposal is not None
    assert calls == [{"through_date": "2026-09-28", "confirm": False}]
    calls.clear()
    assert apply_proposal(TOOLS, moved, outcome.proposal).status == "applied"
    assert calls == [{"through_date": "2026-09-28", "confirm": True}]


def test_a_stale_card_whose_re_preview_refuses_reports_the_refusal():
    proposal = Proposal(
        id="x",
        tool="refusing_tool",
        args={},
        preview_summary="...",
        date="2020-01-01",
        plan_id=None,
        plan_revision=None,
    )
    outcome = apply_proposal(TOOLS, _repos(), proposal)
    assert outcome.status == "refused"
    assert outcome.reason == "stale"
    assert outcome.message == "No."
    assert outcome.proposal is None


def test_a_stale_empty_pause_card_comes_back_as_a_replacement():
    # An "empty" pause still records the window on an active plan, so its
    # stale re-preview must be a card again, not a dead end.
    @coach_tool
    def pause_training(through_date: str, confirm: bool = False) -> str:
        """Pause whose window has nothing pending."""
        return json.dumps(
            {
                "status": "empty",
                "window": {"from": _TODAY, "through": "2026-09-28"},
                "message": "Nothing to skip.",
            }
        )

    stale = Proposal(
        "p", "pause_training", {"through_date": "x"}, "", "2020-01-01", 7, "old"
    )
    outcome = apply_proposal([pause_training], _repos(), stale)
    assert outcome.status == "preview"
    assert outcome.reason == "stale"
    assert outcome.proposal is not None


def test_a_preview_that_offers_nothing_on_apply_is_a_refusal():
    # add_race_to_plan when the race no longer fits: a "preview" with
    # confirm_required False has no card to hand back.
    @coach_tool
    def add_race_to_plan(race_date: str, confirm: bool = False) -> str:
        """Race that no longer fits."""
        return json.dumps(
            {
                "status": "preview",
                "confirm_required": False,
                "message": "No longer fits.",
            }
        )

    repos = _repos()
    card = Proposal(
        "p", "add_race_to_plan", {"race_date": "x"}, "", _TODAY, 7, _revision(repos)
    )
    outcome = apply_proposal([add_race_to_plan], repos, card)
    assert outcome.status == "refused"
    assert outcome.proposal is None
    assert outcome.message == "No longer fits."


def test_a_confirm_that_declines_to_commit_becomes_a_replacement_card():
    # create_plan's freshness check: the training data moved, so it hands
    # back a fresh preview instead of committing.
    proposal = _preview("create_plan", {"races": "[]"})
    stale_hash = Proposal.from_dict(
        {**proposal.to_dict(), "args": {"races": "[]", "expected_input_hash": "old"}}
    )
    calls.clear()
    outcome = apply_proposal(TOOLS, _repos(), stale_hash)
    assert outcome.status == "preview"
    assert outcome.reason == ""
    assert outcome.proposal is not None
    assert outcome.proposal.args["expected_input_hash"] == "hash-1"
    assert calls == [{"races": "[]", "confirm": True, "hash": "old"}]


def test_an_applied_pause_with_nothing_pending_still_counts_as_applied():
    @coach_tool
    def pause_training(through_date: str, confirm: bool = False) -> str:
        """Pause whose window is empty but gets recorded."""
        return json.dumps({"status": "empty", "message": "Recorded as unavailable."})

    repos = _repos()
    proposal = Proposal(
        "p", "pause_training", {"through_date": "x"}, "", _TODAY, 7, _revision(repos)
    )
    outcome = apply_proposal([pause_training], repos, proposal)
    assert outcome.status == "applied"
    assert outcome.message == "Recorded as unavailable."


def test_a_confirm_whose_payload_is_not_json_reports_an_error():
    repos = _repos()
    proposal = Proposal("p", "prose_tool", {}, "", _TODAY, 7, _revision(repos))
    outcome = apply_proposal([prose_tool], repos, proposal)
    assert outcome.status == "error"
    assert outcome.message == "not json"
    assert outcome.result is None


def test_an_unknown_tool_on_the_card_is_an_error():
    proposal = Proposal("p", "gone", {}, "", _TODAY, None, None)
    outcome = apply_proposal(TOOLS, _repos(plan_id=None), proposal)
    assert outcome.status == "error"
    assert "Unknown tool" in outcome.message


def test_apply_result_wire_dict_carries_the_replacement_card():
    proposal = _preview()
    assert ApplyResult("preview", proposal=proposal).to_dict()["proposal"] == (
        proposal.to_dict()
    )
    assert ApplyResult("applied").to_dict()["proposal"] is None


def test_plan_write_tools_cover_every_plan_or_calendar_writer():
    expected = {
        "create_plan",
        "set_plan_directives",
        "add_race_to_plan",
        "set_session_status",
        "skip_planned_session",
        "move_planned_session",
        "replan_active_plan",
        "adjust_current_week",
        "swap_planned_session",
        "pause_training",
        "schedule_workout",
        "schedule_workout_override",
        "unschedule_workout",
        "upsert_race",
        "cancel_or_move_race",
        "set_athlete_thresholds",
    }
    assert set(PLAN_WRITE_TOOLS) == expected
