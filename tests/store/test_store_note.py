"""The note itself: event fields, memory above the blob, the JSON inside it."""

from __future__ import annotations

import pytest

from endury.database.sqlite.migrations import LATEST_VERSION
from endury.store import STORE_DATE, StoreCorrupt, load, remote_rev
from tests.doubles import FakeIntervals
from tests.store.helpers import (
    DURABLE_TABLES,
    FENCE,
    STORE_ID,
    boot,
    decode_note,
    query,
    store_events,
    table_rows,
)

#: Where the note may live: the preferred far-future date and the date the
#: live API was already proven to accept.
STORE_DATE_CANDIDATES = ("2099-12-31T00:00:00", "2030-12-31T00:00:00")


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


def test_save_writes_one_hidden_note_at_the_store_date(fake):
    device = boot(fake)
    device.container.memory.set("## Goals\n- Finish strong")

    device.save()

    assert list(store_events(fake)) == [STORE_ID]
    note = fake.events[STORE_ID]
    assert note["category"] == "NOTE"
    assert note["name"] == "Endury data (do not edit)"
    assert note["hide_from_athlete"] is True
    assert note["start_date_local"] == STORE_DATE
    assert STORE_DATE in STORE_DATE_CANDIDATES


def test_the_memory_sits_above_the_blob(fake):
    device = boot(fake)
    memory = "## Goals\n- Finish strong"
    device.container.memory.set(memory)

    device.save()

    description = fake.events[STORE_ID]["description"]
    assert description.startswith(memory)
    assert description.rindex(FENCE) >= len(memory)
    head, _ = decode_note(description)
    assert head.strip() == memory


def test_the_json_carries_schema_rev_version_and_the_durable_tables(fake):
    device = boot(fake)
    device.container.memory.set("## Notes\n- first")
    device.save()
    device.container.memory.set("## Notes\n- second")
    device.save()

    _, state = decode_note(fake.events[STORE_ID]["description"])

    assert state["schema"] == 1
    assert state["rev"] == 2
    assert state["db_user_version"] == LATEST_VERSION
    assert state["saved_at"]
    assert set(DURABLE_TABLES) <= set(state["tables"])
    assert set(state["tables"]) <= {*DURABLE_TABLES, "sqlite_sequence"}
    assert (
        state["tables"]["athlete_memory_history"][0]["content"] == "## Notes\n- first"
    )


def test_the_json_keeps_only_memory_metadata(fake):
    device = boot(fake)
    device.container.memory.set("## Notes\n- a line only the markdown carries")

    device.save()

    _, state = decode_note(fake.events[STORE_ID]["description"])
    assert "only the markdown carries" not in str(state["tables"]["athlete_memory"])


@pytest.mark.parametrize(
    "memory",
    [
        "",
        "## Goals\n- Sub-3 marathon",
        "ends with a newline\n",
        "## Notes\n- pasted a fake fence:\n```coachie-state\nbm90LWEtYmxvYg==\n```\n- and kept writing",
    ],
    ids=["empty", "plain", "trailing-newline", "fake-fence"],
)
def test_the_memory_round_trips_exactly(fake, memory):
    device_a = boot(fake)
    device_a.container.memory.set(memory)
    device_a.container.races.upsert(name="Marathon", race_date="2027-04-25")
    device_a.save()

    device_b = boot(fake)

    assert device_b.container.memory.get().content == memory
    assert table_rows(device_b.session, "athlete_goals") == table_rows(
        device_a.session, "athlete_goals"
    )


def test_the_markdown_above_the_fence_is_canonical_for_the_memory(fake):
    device_a = boot(fake)
    device_a.container.memory.set("## Goals\n- Sub-3 marathon")
    device_a.save()
    note = fake.events[STORE_ID]
    note["description"] = note["description"].replace(
        "Sub-3 marathon", "Sub-3 marathon, edited on intervals.icu"
    )

    device_b = boot(fake)

    memory = device_b.container.memory.get()
    assert memory.content == "## Goals\n- Sub-3 marathon, edited on intervals.icu"
    assert memory.updated_at == device_a.container.memory.get().updated_at


def test_only_the_last_ten_memory_versions_are_kept(fake):
    device_a = boot(fake)
    for n in range(13):
        device_a.container.memory.set(f"## Notes\n- version {n}")
    assert len(query(device_a.session, "SELECT id FROM athlete_memory_history")) == 12

    device_a.save()
    device_b = boot(fake)

    kept = query(
        device_b.session, "SELECT id, content FROM athlete_memory_history ORDER BY id"
    )
    assert [row["id"] for row in kept] == list(range(3, 13))
    assert kept[-1]["content"] == "## Notes\n- version 11"
    device_b.container.memory.set("## Notes\n- version 13")
    assert device_b.container.memory.history(limit=1)[0].id == 13


@pytest.mark.parametrize("when", STORE_DATE_CANDIDATES)
def test_load_finds_the_note_at_either_store_date(fake, when):
    device_a = boot(fake)
    device_a.container.memory.set("## Goals\n- Found me")
    device_a.save()
    fake.events[STORE_ID]["start_date_local"] = when
    fake.events["athlete:own-note"] = {
        "id": 1,
        "external_id": "athlete:own-note",
        "category": "NOTE",
        "name": "New Year",
        "description": "Not ours",
        "start_date_local": when,
    }

    snapshot = load(fake.client())

    assert snapshot is not None
    assert snapshot.rev == 1
    assert remote_rev(fake.client()) == 1
    device_b = boot(fake)
    assert device_b.container.memory.get().content == "## Goals\n- Found me"
    device_b.container.memory.set("## Goals\n- Moved back")
    assert device_b.save() == 2
    assert list(store_events(fake)) == [STORE_ID]
    assert fake.events["athlete:own-note"]["description"] == "Not ours"


def test_an_account_without_a_note_loads_as_empty(fake):
    fake.events["athlete:own-note"] = {
        "id": 1,
        "external_id": "athlete:own-note",
        "category": "NOTE",
        "name": "Somewhere else",
        "description": "Not ours",
        "start_date_local": STORE_DATE,
    }

    assert load(fake.client()) is None
    assert remote_rev(fake.client()) == 0


@pytest.mark.parametrize(
    "description",
    ["cleared by hand", "## Notes\n```coachie-state\nnot base64 at all\n```\n"],
    ids=["fence-gone", "blob-garbled"],
)
def test_an_unreadable_note_raises_store_corrupt(fake, description):
    device = boot(fake)
    device.container.memory.set("## Notes\n- one")
    device.save()
    fake.events[STORE_ID]["description"] = description

    with pytest.raises(StoreCorrupt):
        load(fake.client())
