"""``endury/store/chat.py``: the last 50 conversation rows, in their own NOTE."""

from __future__ import annotations

import random
import string

import pytest

from endury.database.sqlite.session import DatabaseSession
from endury.store import chat
from endury.store import delete as store_delete
from endury.wiring.container import RepositoryContainer
from tests.doubles import FakeIntervals
from tests.store.helpers import STORE_ID
from tests.store.helpers import boot as boot_store


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


def _session() -> DatabaseSession:
    """A fresh in-memory database, schema only — no store boot involved."""
    return RepositoryContainer.in_memory().session


def _insert(session: DatabaseSession, role: str, content: str, created_at: str) -> None:
    """Insert one ``conversation_history`` row with an explicit ``created_at``."""
    with session.cursor() as cursor:
        cursor.execute(
            "INSERT INTO conversation_history (role, content, created_at) "
            "VALUES (?, ?, ?)",
            (role, content, created_at),
        )


def _incompressible(chars: int) -> str:
    """Random text zlib cannot shrink, to force the store note into chunks."""
    return "".join(random.Random(chars).choices(string.ascii_letters, k=chars))


def test_load_without_a_note_is_empty(fake):
    assert chat.load(fake.client()) == []


def test_save_then_load_round_trips_verbatim(fake):
    session = _session()
    _insert(session, "user", "Can I run tomorrow?", "2026-10-06T21:15:00")
    _insert(session, "assistant", "Yes, easy.", "2026-10-06T21:15:05")

    chat.save(fake.client(), session)

    assert chat.load(fake.client()) == [
        {
            "role": "user",
            "content": "Can I run tomorrow?",
            "created_at": "2026-10-06T21:15:00",
        },
        {
            "role": "assistant",
            "content": "Yes, easy.",
            "created_at": "2026-10-06T21:15:05",
        },
    ]
    event = fake.events[chat.CHAT_ID]
    assert event["category"] == "NOTE"
    assert event["name"] == chat.CHAT_NAME
    assert event["hide_from_athlete"] is True
    assert event["start_date_local"] == "2099-12-31T00:00:00"


def test_only_the_last_fifty_rows_are_kept(fake):
    session = _session()
    for n in range(60):
        _insert(session, "user", f"msg {n}", f"2026-10-06T00:00:{n:02d}")

    chat.save(fake.client(), session)

    rows = chat.load(fake.client())
    assert len(rows) == chat.KEEP
    assert rows[0]["content"] == "msg 10"
    assert rows[-1]["content"] == "msg 59"


def test_a_reset_marker_wipes_rows_before_it(fake):
    session = _session()
    _insert(session, "user", "before", "2026-10-06T21:00:00")
    _insert(session, "assistant", "before reply", "2026-10-06T21:00:05")
    _insert(session, "reset", "", "2026-10-06T21:05:00")
    _insert(session, "user", "after", "2026-10-06T21:06:00")

    chat.save(fake.client(), session)

    assert chat.load(fake.client()) == [
        {"role": "reset", "content": "", "created_at": "2026-10-06T21:05:00"},
        {"role": "user", "content": "after", "created_at": "2026-10-06T21:06:00"},
    ]


def test_a_stale_device_cannot_resurrect_a_reset_conversation(fake):
    device_a = _session()
    _insert(device_a, "user", "before", "2026-10-06T21:00:00")
    chat.save(fake.client(), device_a)

    # Device B boots before the reset and still holds the old row locally.
    device_b = _session()
    chat.restore(device_b, chat.load(fake.client()))

    # Device A resets the conversation and saves.
    _insert(device_a, "reset", "", "2026-10-06T21:05:00")
    chat.save(fake.client(), device_a)

    # Device B, unaware of the reset, saves afterwards.
    chat.save(fake.client(), device_b)

    assert chat.load(fake.client()) == [
        {"role": "reset", "content": "", "created_at": "2026-10-06T21:05:00"}
    ]


def test_two_devices_saving_interleave_by_time(fake):
    device_a = _session()
    device_b = _session()
    _insert(device_a, "user", "from A", "2026-10-06T21:00:05")
    _insert(device_b, "user", "from B", "2026-10-06T21:00:00")

    chat.save(fake.client(), device_a)
    chat.save(fake.client(), device_b)

    assert chat.load(fake.client()) == [
        {"role": "user", "content": "from B", "created_at": "2026-10-06T21:00:00"},
        {"role": "user", "content": "from A", "created_at": "2026-10-06T21:00:05"},
    ]


@pytest.mark.parametrize(
    "description",
    ["cleared by hand", f"{chat.FENCE}\nnot base64 at all\n```"],
    ids=["fence-gone", "blob-garbled"],
)
def test_a_corrupt_note_loads_empty_and_is_overwritten(fake, description):
    session = _session()
    _insert(session, "user", "hi", "2026-10-06T21:00:00")
    chat.save(fake.client(), session)
    fake.events[chat.CHAT_ID]["description"] = description

    assert chat.load(fake.client()) == []

    _insert(session, "assistant", "fresh", "2026-10-06T21:01:00")
    chat.save(fake.client(), session)

    assert chat.load(fake.client()) == [
        {"role": "user", "content": "hi", "created_at": "2026-10-06T21:00:00"},
        {"role": "assistant", "content": "fresh", "created_at": "2026-10-06T21:01:00"},
    ]


def test_store_save_and_delete_leave_the_chat_note_alone(fake):
    device = boot_store(fake)
    _insert(device.session, "user", "hi", "2026-10-06T21:00:00")
    chat.save(device.client, device.session)

    # Force the store note into several chunks, then shrink it back — the
    # leftover-chunk cleanup a plain save runs must not touch the chat note.
    race = device.container.races.upsert(
        name="Diary", race_date="2027-04-25", notes=_incompressible(2_200_000)
    )
    device.save()
    assert f"{STORE_ID}:2" in fake.events
    assert chat.CHAT_ID in fake.events

    device.container.races.upsert(
        race_id=race.id, name="Diary", race_date="2027-04-25", notes="short again"
    )
    device.save()

    assert f"{STORE_ID}:2" not in fake.events
    assert chat.CHAT_ID in fake.events

    store_delete(device.client)

    assert chat.CHAT_ID in fake.events
    assert STORE_ID not in fake.events
