"""``endury/store/takes.py``: the coach's saved takes, in their own NOTE."""

from __future__ import annotations

import pytest

from endury.store import delete as store_delete
from endury.store import takes
from tests.doubles import FakeIntervals
from tests.store.helpers import STORE_ID
from tests.store.helpers import boot as boot_store


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


def _take(activity_id: str, depth: str, text: str, created_at: str) -> takes.Row:
    return {
        "activity_id": activity_id,
        "depth": depth,
        "text": text,
        "created_at": created_at,
    }


def test_load_without_a_note_is_empty(fake):
    assert takes.load(fake.client()) == []


def test_save_then_load_round_trips_verbatim(fake):
    rows = [
        _take("i1", "quick", "Did its job.", "2026-10-06T21:15:00"),
        _take("i1", "full", "**Purpose** — aerobic base.", "2026-10-06T21:16:00"),
    ]

    assert takes.save(fake.client(), rows) == rows

    assert takes.load(fake.client()) == rows
    event = fake.events[takes.TAKES_ID]
    assert event["category"] == "NOTE"
    assert event["name"] == takes.TAKES_NAME
    assert event["hide_from_athlete"] is True
    assert event["start_date_local"] == "2099-12-31T00:00:00"


def test_put_replaces_the_take_at_the_same_depth_only():
    rows = [
        _take("i1", "quick", "old", "2026-10-06T21:00:00"),
        _take("i1", "full", "kept", "2026-10-06T21:01:00"),
    ]

    after = takes.put(rows, "i1", "quick", "new")

    assert [(row["depth"], row["text"]) for row in after] == [
        ("full", "kept"),
        ("quick", "new"),
    ]


def test_the_newer_take_wins_whichever_device_saves_last(fake):
    newer = _take("i1", "quick", "newer", "2026-10-06T21:05:00")
    older = _take("i1", "quick", "older", "2026-10-06T21:00:00")

    takes.save(fake.client(), [newer])
    takes.save(fake.client(), [older])

    assert takes.load(fake.client()) == [newer]


def test_two_devices_keep_each_others_takes(fake):
    from_a = _take("i1", "quick", "A", "2026-10-06T21:00:05")
    from_b = _take("i2", "full", "B", "2026-10-06T21:00:00")

    takes.save(fake.client(), [from_a])
    merged = takes.save(fake.client(), [from_b])

    assert merged == [from_b, from_a]
    assert takes.load(fake.client()) == [from_b, from_a]


def test_only_the_newest_takes_are_kept(fake):
    rows = [
        _take(f"i{n}", "quick", "ok", f"2026-10-06T21:{n // 60:02d}:{n % 60:02d}")
        for n in range(takes.KEEP + 10)
    ]

    takes.save(fake.client(), rows)

    kept = takes.load(fake.client())
    assert len(kept) == takes.KEEP
    assert kept[0]["activity_id"] == "i10"


@pytest.mark.parametrize(
    "description",
    ["cleared by hand", f"{takes.FENCE}\nnot base64 at all\n```"],
    ids=["fence-gone", "blob-garbled"],
)
def test_a_corrupt_note_loads_empty_and_is_overwritten(fake, description):
    first = _take("i1", "quick", "first", "2026-10-06T21:00:00")
    takes.save(fake.client(), [first])
    fake.events[takes.TAKES_ID]["description"] = description

    assert takes.load(fake.client()) == []

    fresh = _take("i2", "quick", "fresh", "2026-10-06T21:01:00")
    assert takes.save(fake.client(), [fresh]) == [fresh]
    assert takes.load(fake.client()) == [fresh]


def test_store_save_and_delete_leave_the_takes_note_alone(fake):
    device = boot_store(fake)
    takes.save(device.client, [_take("i1", "quick", "ok", "2026-10-06T21:00:00")])

    device.container.races.upsert(name="Ulm", race_date="2027-04-25")
    device.save()
    store_delete(device.client)

    assert takes.TAKES_ID in fake.events
    assert STORE_ID not in fake.events


def test_a_brief_and_a_take_on_one_id_and_depth_coexist(fake):
    rows = takes.put([], "session:7", "quick", "the take")
    rows = takes.put(rows, "session:7", "quick", "the brief", kind="brief")

    assert [row.get("kind", "take") for row in rows] == ["take", "brief"]
    assert "kind" not in rows[0]

    again = takes.put(rows, "session:7", "quick", "a newer brief", kind="brief")
    assert [row["text"] for row in again] == ["the take", "a newer brief"]


def test_a_brief_keeps_its_kind_through_the_note_and_a_merge(fake):
    local = takes.put([], "race:2026-10-24", "full", "Hold the first third.", "brief")
    local = takes.put(local, "race:2026-10-24", "full", "How it went.")

    saved = takes.save(fake.client(), local)

    assert takes.load(fake.client()) == saved
    assert {row.get("kind", "take") for row in saved} == {"take", "brief"}
    merged = takes.merge(saved, takes.put([], "i1", "quick", "x"))
    assert len(merged) == 3
