"""Dirty tracking: durable writes mark the session, the conversation never does."""

from __future__ import annotations

import pytest

from endury.store import is_dirty, mark_clean
from tests.doubles import FakeIntervals
from tests.store.helpers import (
    DURABLE_TABLES,
    STORE_ID,
    boot,
    decode_note,
    execute,
    query,
)

#: One insert, update and delete per durable table, in an order each table
#: accepts (the two single-row tables start from their seeded row).
_WRITES: dict[str, tuple[str, ...]] = {
    "macro_plans": (
        "INSERT INTO macro_plans (goal_type, goal_date, plan_start, total_weeks, "
        "ambition_level, weeks_json, coaching_brief, created_at, updated_at) "
        "VALUES ('running_10k', '2027-01-31', '2026-10-05', 17, 'MODERATE', '[]', "
        "'', 't', 't')",
        "UPDATE macro_plans SET revision = revision + 1",
        "DELETE FROM macro_plans",
    ),
    "plans": (
        "INSERT INTO plans (name, goal_type, goal_date, plan_start, "
        "solve_result_json, solver_version, input_hash, created_at, updated_at) "
        "VALUES ('p', 'running_10k', '2027-01-31', '2026-10-05', '{}', 'v', 'h', "
        "'t', 't')",
        "UPDATE plans SET status = 'archived'",
        "DELETE FROM plans",
    ),
    "plan_sessions": (
        "INSERT INTO plan_sessions (plan_id, slot_id, date, week_index, phase_type, "
        "sport, activity_key, family, target_tss, achieved_tss, workout_json) "
        "VALUES ((SELECT MAX(id) FROM plans), 's', '2026-10-07', 0, 'base', "
        "'running', 'run_easy', 'easy', 30, 30, '{}')",
        "UPDATE plan_sessions SET status = 'skipped'",
        "DELETE FROM plan_sessions",
    ),
    "athlete_goals": (
        "INSERT INTO athlete_goals (name, race_date, created_at, updated_at) "
        "VALUES ('r', '2027-01-31', 't', 't')",
        "UPDATE athlete_goals SET status = 'cancelled'",
        "DELETE FROM athlete_goals",
    ),
    "athlete_self_assessment": (
        "INSERT INTO athlete_self_assessment (sport, weekly_hours, "
        "longest_session_min, updated_at) VALUES ('running', 5, 90, 't')",
        "UPDATE athlete_self_assessment SET weekly_hours = 6",
        "DELETE FROM athlete_self_assessment",
    ),
    "athlete_profile": (
        "UPDATE athlete_profile SET name = 'Jordan'",
        "DELETE FROM athlete_profile",
        "INSERT INTO athlete_profile (id, created_at, updated_at) VALUES (1, 't', 't')",
    ),
    "athlete_memory": (
        "UPDATE athlete_memory SET content = 'x'",
        "DELETE FROM athlete_memory",
        "INSERT INTO athlete_memory (id, content, updated_at) VALUES (1, '', 't')",
    ),
    "athlete_memory_history": (
        "INSERT INTO athlete_memory_history (content, created_at) VALUES ('old', 't')",
        "UPDATE athlete_memory_history SET content = 'older'",
        "DELETE FROM athlete_memory_history",
    ),
}

#: The parent rows ``plan_sessions`` needs (foreign keys are on).
_PARENTS = (_WRITES["macro_plans"][0], _WRITES["plans"][0])


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


def test_every_durable_table_is_tracked():
    assert set(_WRITES) == set(DURABLE_TABLES)


def test_a_booted_device_starts_clean(fake):
    assert not is_dirty(boot(fake).session)


@pytest.mark.parametrize("table", DURABLE_TABLES)
def test_every_durable_write_marks_the_session_dirty(fake, table):
    device = boot(fake)
    if table == "plan_sessions":
        for sql in _PARENTS:
            execute(device.session, sql)
        mark_clean(device.session)

    for sql in _WRITES[table]:
        assert not is_dirty(device.session)
        execute(device.session, sql)
        assert is_dirty(device.session), sql
        mark_clean(device.session)


def test_repository_writes_mark_the_session_dirty(fake):
    device = boot(fake)
    repos = device.container
    writes = [
        lambda: repos.memory.set("## Notes\n- new"),
        lambda: repos.athlete.update({"name": "Jordan"}),
        lambda: repos.athlete.update_last_session_at("2026-10-07T08:00:00"),
        lambda: repos.self_assessment.upsert_many({"running": (5.0, 90)}),
        lambda: repos.races.upsert(name="Marathon", race_date="2027-04-25"),
    ]

    for write in writes:
        write()
        assert is_dirty(device.session)
        mark_clean(device.session)


def test_conversation_history_never_marks_the_session_dirty(fake):
    device = boot(fake)

    entry_id = device.container.history.add("user", "hello")
    execute(
        device.session,
        f"UPDATE conversation_history SET content = 'edited' WHERE id = {entry_id}",
    )
    execute(device.session, "DELETE FROM conversation_history")

    assert not is_dirty(device.session)


def test_the_dirty_tracking_is_never_uploaded(fake):
    device = boot(fake)
    device.container.memory.set("## Notes\n- tracked")
    temp_names = {
        row["name"]
        for row in query(device.session, "SELECT name FROM sqlite_temp_master")
    }
    assert temp_names

    device.save()

    _, state = decode_note(fake.events[STORE_ID]["description"])
    assert not temp_names & set(state["tables"])
