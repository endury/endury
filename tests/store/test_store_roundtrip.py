"""Device A edits everything, saves; a fresh device B restores it row for row."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from endury.coach.tools.plan_directive_tools import make_plan_directive_tools
from endury.planning.add_race import add_race_to_plan
from endury.planning.adjust_week import adjust_current_week
from endury.planning.commit_plan import reschedule_session
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.pause_training import pause_training
from endury.planning.replan_plan import run_replan
from endury.planning.schedule_workout import schedule_workout
from endury.planning.session_status import set_session_status
from endury.planning.swap_session import swap_planned_session
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    GenerateSuccess,
    RaceGoalModel,
    ScheduleRequest,
)
from endury.store import is_dirty
from tests.doubles import FakeIntervals
from tests.store.helpers import (
    DURABLE_TABLES,
    SIM_TODAY,
    STORE_ID,
    Device,
    boot,
    commit_half_marathon,
    decode_note,
    durable_state,
    query,
    run_athlete,
    sequences,
)

TODAY = SIM_TODAY


def _none() -> None:
    return None


def _ok(result: dict | str) -> dict:
    """Assert a planning call succeeded (tools answer JSON strings)."""
    outcome = json.loads(result) if isinstance(result, str) else result
    assert outcome.get("status") == "ok", outcome
    return outcome


def _commit_first_plan(device: Device) -> None:
    """Commit a 10K plan the half-marathon plan then supersedes (archived rows)."""
    request = GenerateRequest(
        races=[RaceGoalModel(goal_type="running_10k", race_date="2026-12-20")],
        training_start="2026-10-05",
        ambition_level="basic",
        accept_experimental=True,  # the 10K is Experimental
    )
    result = run_generate(request, repos=device.container, progress=lambda _p: None)
    assert isinstance(result, GenerateSuccess), result
    _ok(
        run_commit(
            CommitRequest(
                name="Autumn 10K", generate_request=request, generate_result=result
            ),
            repos=device.container,
            maybe_get_coach=_none,
            client=device.client,
        )
    )


def _edit_everything(device: Device) -> None:
    """Drive every durable write the app makes, through the real functions."""
    repos, client = device.container, device.client
    repos.self_assessment.upsert_many({"running": (5.0, 100), "cycling": (2.0, 90)})
    repos.athlete.update(
        {
            "name": "Jordan",
            "experience_level": "intermediate",
            "primary_sport": "running",
        }
    )
    tune_up = repos.races.upsert(
        name="Club 10K", race_date="2026-11-22", goal_type="running_10k", priority="B"
    )

    _commit_first_plan(device)
    commit_half_marathon(repos, client, today=TODAY)

    _ok(
        adjust_current_week(
            repos=repos, reduce_percent=20, confirm=True, client=client, today=TODAY
        )
    )
    _ok(
        pause_training(
            repos=repos,
            through_date=(TODAY + timedelta(days=1)).isoformat(),
            confirm=True,
            client=client,
            today=TODAY,
        )
    )
    [set_plan_directives] = make_plan_directive_tools(repos, _none, client)
    _ok(
        set_plan_directives.fn(
            unavailable='[["2026-11-09", "2026-11-11"]]',
            weekly_hours_cap=6.0,
            confirm=True,
        )
    )
    _ok(
        run_replan(
            repos=repos,
            progress=lambda _p: None,
            maybe_get_coach=_none,
            today=TODAY,
            client=client,
        )
    )
    _ok(
        add_race_to_plan(
            repos=repos,
            race_id=tune_up.id,
            priority="B",
            confirm=True,
            client=client,
            today=TODAY,
        )
    )

    plan = repos.plan.get_active()
    assert plan is not None
    by_date = {str(row.date): row for row in repos.plan.get_sessions(plan.id)}
    _ok(
        reschedule_session(
            by_date["2026-10-16"].id,
            ScheduleRequest(date="2026-10-17"),
            get_repos=lambda: repos,
            client=client,
        )
    )
    _ok(
        set_session_status(
            repos=repos,
            client=client,
            session_id=by_date["2026-10-22"].id,
            status="skipped",
        )
    )
    _ok(
        swap_planned_session(
            repos=repos,
            session_id=by_date["2026-10-20"].id,
            intent="recovery",
            confirm=True,
            client=client,
            today=TODAY,
        )
    )
    _ok(
        schedule_workout(
            repos=repos,
            duration_minutes=30,
            date_str="2026-10-24",
            sport="running",
            intent="easy",
            client=client,
            today=TODAY,
        )
    )
    repos.races.upsert(
        name="Spring marathon",
        race_date="2027-04-25",
        goal_type="running_marathon",
        priority="C",
        notes="Only if the half goes well.",
    )
    repos.memory.set("## Goals\n- Sub-1:45 half")
    repos.memory.set("## Goals\n- Sub-1:45 half\n\n## Constraints\n- No Tuesday runs")
    repos.history.add("user", "Chat line kept out of the store note")
    repos.history.add("assistant", "Noted.")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeIntervals:
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", TODAY.isoformat())
    return FakeIntervals(athlete=run_athlete())


def test_a_fresh_device_restores_every_durable_table_row_for_row(fake):
    device_a = boot(fake)
    _edit_everything(device_a)
    assert is_dirty(device_a.session)

    assert device_a.save() == 1
    device_b = boot(fake)

    assert device_b.rev == 1
    assert durable_state(device_b.session) == durable_state(device_a.session)
    assert not is_dirty(device_b.session)


def test_the_scenario_touched_every_durable_table(fake):
    device = boot(fake)
    _edit_everything(device)

    counts = {
        table: len(query(device.session, f"SELECT * FROM {table}"))
        for table in DURABLE_TABLES
    }
    assert all(counts.values()), counts
    plan_statuses = {
        row["status"] for row in query(device.session, "SELECT status FROM plans")
    }
    assert plan_statuses == {"active", "archived"}
    session_statuses = {
        row["status"]
        for row in query(device.session, "SELECT status FROM plan_sessions")
    }
    assert {"pending", "skipped"} <= session_statuses


def test_archived_plans_stay_out_of_the_note(fake):
    device_a = boot(fake)
    _edit_everything(device_a)
    device_a.save()

    _, state = decode_note(fake.events[STORE_ID]["description"])
    device_b = boot(fake)

    assert {row["status"] for row in state["tables"]["plans"]} == {"active"}
    assert {
        row["status"] for row in query(device_b.session, "SELECT status FROM plans")
    } == {"active"}
    # The id counters still travel, so an archived plan's id is never reused.
    assert sequences(device_b.session) == sequences(device_a.session)


def test_conversation_history_stays_out_of_the_store_note(fake):
    device_a = boot(fake)
    _edit_everything(device_a)
    device_a.save()

    device_b = boot(fake)

    assert query(device_b.session, "SELECT * FROM conversation_history") == []
    assert "kept out of the store note" not in fake.events[STORE_ID]["description"]
    _, state = decode_note(fake.events[STORE_ID]["description"])
    assert "conversation_history" not in state["tables"]


def test_the_restored_device_hands_out_the_ids_device_a_would_have(fake):
    device_a = boot(fake)
    _edit_everything(device_a)
    device_a.save()
    device_b = boot(fake)

    ids = [
        device.container.races.upsert(
            name="Parkrun", race_date="2026-12-05", goal_type="running_5k", priority="C"
        ).id
        for device in (device_a, device_b)
    ]

    assert ids[0] == ids[1]
