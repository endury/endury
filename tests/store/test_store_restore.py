"""Restore: the empty-store seed, one transaction, and schema versions."""

from __future__ import annotations

import sqlite3
import time

import pytest

from endury.common.exceptions import RepositoryError
from endury.database.sqlite.migrations import LATEST_VERSION
from endury.store import StoreReadOnly, is_dirty, load, mark_clean, restore, save
from endury.wiring.container import RepositoryContainer
from tests.doubles import FakeIntervals
from tests.store.helpers import (
    SIM_TODAY,
    STORE_ID,
    boot,
    commit_half_marathon,
    decode_note,
    durable_state,
    query,
    run_athlete,
    table_rows,
    tamper_state,
)


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals(athlete=run_athlete())


def _plans_sequence(container: RepositoryContainer) -> int | None:
    rows = query(
        container.session, "SELECT seq FROM sqlite_sequence WHERE name = 'plans'"
    )
    return rows[0]["seq"] if rows else None


def test_an_empty_store_seeds_the_plan_id_before_the_first_commit(fake, monkeypatch):
    monkeypatch.delenv("COACHING_BUDDY_SIM_DATE", raising=False)
    client = fake.client()
    container = RepositoryContainer.in_memory()
    container.bind_intervals_client(client)
    assert load(client) is None
    assert _plans_sequence(container) is None

    before = int(time.time())
    restore(container.session, None)
    after = int(time.time())

    seed = _plans_sequence(container)
    assert seed is not None
    assert before <= seed <= after
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", SIM_TODAY.isoformat())
    container.self_assessment.upsert_many({"running": (5.0, 100)})
    plan_id = commit_half_marathon(container, client, today=SIM_TODAY)
    assert plan_id >= seed
    pushed = [key for key in fake.events if key.startswith("coachie:")]
    assert pushed
    assert all(key.startswith(f"coachie:{plan_id}:") for key in pushed)


def test_an_empty_store_keeps_the_schema_defaults(fake):
    container = RepositoryContainer.in_memory()
    defaults = {
        table: table_rows(container.session, table)
        for table in ("athlete_profile", "athlete_memory")
    }

    restore(container.session, None)

    assert defaults["athlete_profile"]
    assert defaults["athlete_memory"]
    for table, rows in defaults.items():
        assert table_rows(container.session, table) == rows


def test_a_snapshot_replaces_whatever_a_booted_session_held(fake):
    device_a = boot(fake)
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.container.athlete.update({"name": "Jordan"})
    device_a.save()
    snapshot = load(fake.client())
    other = boot(FakeIntervals())
    other.container.races.upsert(name="Someone else's race", race_date="2027-01-01")
    other.container.memory.set("## Notes\n- someone else")

    restore(other.session, snapshot)

    assert durable_state(other.session) == durable_state(device_a.session)
    mark_clean(other.session)
    other.container.memory.set("## Notes\n- still tracked")
    assert is_dirty(other.session)


def test_a_failing_restore_leaves_the_session_untouched(fake):
    device_a = boot(fake)
    device_a.container.memory.set("## Notes\n- never lands")
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.save()

    def _break_a_row(state):
        good = state["tables"]["athlete_goals"][0]
        state["tables"]["athlete_goals"].append(
            {**good, "id": good["id"] + 1, "priority": "Z"}
        )

    tamper_state(fake, _break_a_row)
    container = RepositoryContainer.in_memory()
    before = durable_state(container.session)

    with pytest.raises((sqlite3.Error, RepositoryError)):
        restore(container.session, load(fake.client()))

    assert durable_state(container.session) == before
    assert container.memory.get().content == ""


def _from_a_newer_bundle(state):
    state["db_user_version"] = LATEST_VERSION + 1
    for row in state["tables"]["athlete_goals"]:
        row["future_column"] = "set by a newer bundle"
    state["tables"]["future_table"] = [{"id": 1, "anything": "new"}]


def test_a_newer_schema_loads_read_only(fake):
    device_a = boot(fake)
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.container.memory.set("## Notes\n- written by a newer bundle")
    device_a.save()
    tamper_state(fake, _from_a_newer_bundle)

    snapshot = load(fake.client())
    assert snapshot is not None
    assert snapshot.read_only

    container = RepositoryContainer.in_memory()
    restore(container.session, snapshot)
    assert table_rows(container.session, "athlete_goals") == table_rows(
        device_a.session, "athlete_goals"
    )
    assert container.memory.get().content == "## Notes\n- written by a newer bundle"


def test_a_newer_schema_refuses_to_save(fake):
    device_a = boot(fake)
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.save()
    tamper_state(fake, _from_a_newer_bundle)
    device_b = boot(fake)
    device_b.container.memory.set("## Notes\n- would drop the newer columns")
    before = dict(fake.events[STORE_ID])
    fake.calls.clear()

    with pytest.raises(StoreReadOnly):
        save(device_b.client, device_b.session, expected_rev=device_b.rev)

    assert fake.events[STORE_ID] == before
    assert all(method == "GET" for method, _path in fake.calls)


def test_an_older_schema_loads_and_saves_at_the_current_version(fake):
    device_a = boot(fake)
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.save()

    def _older(state):
        state["db_user_version"] = LATEST_VERSION - 1

    tamper_state(fake, _older)

    device_b = boot(fake)
    snapshot = load(fake.client())
    assert snapshot is not None
    assert not snapshot.read_only
    assert durable_state(device_b.session) == durable_state(device_a.session)
    device_b.container.memory.set("## Notes\n- upgraded")
    assert device_b.save() == 2
    _, state = decode_note(fake.events[STORE_ID]["description"])
    assert state["db_user_version"] == LATEST_VERSION
