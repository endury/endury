"""Tests for the athlete-memory notes a plan commit and a plan delete leave."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from endury.common import clock
from endury.database.sqlite.repositories.athlete_memory import MEMORY_CHAR_CAP
from endury.planning.commit_plan import (
    delete_active_plan,
    update_coach_plan_context,
    update_coach_replan_context,
)
from endury.planning.wire.requests import GenerateSuccess
from endury.wiring.container import RepositoryContainer


def _response(**macro) -> GenerateSuccess:
    payload = {
        "goal_type": "ironman",
        "goal_date": "2027-06-12",
        "total_weeks": 24,
        "ambition_level": "MODERATE",
    }
    payload.update(macro)
    return GenerateSuccess(coach_brief="Build the engine.", macro_plan=payload)


def _coach(memory_repo):
    runtime = SimpleNamespace(active_plan_summary=None, memory_repo=memory_repo)
    return SimpleNamespace(runtime=runtime)


@pytest.fixture
def repos():
    container = RepositoryContainer.in_memory()
    yield container
    container.session.close()


def test_commit_notes_the_plan_in_the_memory_document(repos):
    coach = _coach(repos.memory)

    update_coach_plan_context(
        _response(), "Road to Roth", maybe_get_coach=lambda: coach
    )

    content = repos.memory.get().content
    assert "## Notes" in content
    assert (
        f"- {clock.today().isoformat()}: committed full-distance triathlon plan, "
        "moderate tier, 24 weeks to 2027-06-12"
    ) in content
    assert coach.runtime.active_plan_summary.startswith("Plan: Road to Roth")


def test_a_replan_notes_itself_as_replanned(repos):
    coach = _coach(repos.memory)

    update_coach_replan_context(
        _response(), "Road to Roth", maybe_get_coach=lambda: coach
    )

    content = repos.memory.get().content
    assert "replanned full-distance triathlon plan, moderate tier" in content
    assert "committed" not in content


def test_deleting_the_plan_notes_it_and_drops_the_coach_plan_context(repos):
    """Else the commit note in memory reads as a plan still running."""
    coach = _coach(repos.memory)
    coach.runtime.active_plan_summary = "Plan: Road to Roth"
    coach.runtime.macro_plan_summary = "Goal: ironman, 24 weeks total"
    plans = MagicMock()
    plans.plan.get_active.return_value = SimpleNamespace(
        id=7,
        goal_type="ironman",
        goal_date="2027-06-12",
        solve_result_json="{}",
        athlete_state_json=None,
    )
    plans.plan.get_macro_for_plan.return_value = None
    plans.session.transaction.return_value = nullcontext()

    delete_active_plan(get_repos=lambda: plans, maybe_get_coach=lambda: coach)

    assert (
        f"- {clock.today().isoformat()}: deleted full-distance triathlon plan to 2027-06-12"
    ) in repos.memory.get().content
    assert coach.runtime.active_plan_summary is None
    assert coach.runtime.macro_plan_summary is None


def test_commit_note_keeps_the_document_under_the_cap(repos):
    filler = "\n".join(f"- 2020-01-01: old note {n}" for n in range(300))
    repos.memory.set(f"## Notes\n{filler}"[:MEMORY_CHAR_CAP])

    update_coach_plan_context(
        _response(), "Road to Roth", maybe_get_coach=lambda: _coach(repos.memory)
    )

    content = repos.memory.get().content
    assert len(content) <= MEMORY_CHAR_CAP
    assert "old note 0" not in content
    assert "committed full-distance triathlon plan" in content


def test_commit_note_is_skipped_when_the_macro_payload_names_no_goal(repos):
    update_coach_plan_context(
        GenerateSuccess(coach_brief=""),
        "Road to Roth",
        maybe_get_coach=lambda: _coach(repos.memory),
    )

    assert repos.memory.get().content == ""


def test_a_memory_failure_never_fails_the_commit(caplog):
    runtime = SimpleNamespace(active_plan_summary=None)
    coach = SimpleNamespace(runtime=runtime)

    update_coach_plan_context(
        _response(), "Road to Roth", maybe_get_coach=lambda: coach
    )

    assert runtime.active_plan_summary is not None
    assert "athlete memory" in caplog.text
