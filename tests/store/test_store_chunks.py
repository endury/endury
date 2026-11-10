"""Bodies over 900,000 chars spread over ``coachie:store:2..n``; leftovers are deleted."""

from __future__ import annotations

import random
import string

import pytest

from devtools.frontend.fakes import MAX_DESCRIPTION_CHARS
from tests.doubles import FakeIntervals
from tests.store.helpers import STORE_ID, boot, durable_state, store_events


def _incompressible(chars: int) -> str:
    """Random text zlib cannot shrink: its base64 is about as long as itself."""
    return "".join(
        random.Random(chars).choices(string.ascii_letters + string.digits, k=chars)
    )


def _chunk_ids(count: int) -> list[str]:
    return [STORE_ID] + [f"{STORE_ID}:{n}" for n in range(2, count + 1)]


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


@pytest.mark.parametrize(
    ("notes_chars", "chunks"),
    [(2_200_000, 3), (1_350_000, 2), (200, 1)],
    ids=["three-chunks", "two-chunks", "one-note"],
)
def test_a_big_store_round_trips_through_chunks(fake, notes_chars, chunks):
    device_a = boot(fake)
    device_a.container.memory.set("## Notes\n- a long race diary")
    device_a.container.races.upsert(
        name="Diary race", race_date="2027-04-25", notes=_incompressible(notes_chars)
    )

    device_a.save()

    events = store_events(fake)
    assert sorted(events) == sorted(_chunk_ids(chunks))
    assert all(
        len(event["description"]) <= MAX_DESCRIPTION_CHARS for event in events.values()
    )
    device_b = boot(fake)
    assert device_b.rev == 1
    assert durable_state(device_b.session) == durable_state(device_a.session)
    assert device_b.container.memory.get().content == "## Notes\n- a long race diary"


def test_chunks_the_body_no_longer_needs_are_deleted(fake):
    device = boot(fake)
    race = device.container.races.upsert(
        name="Diary race", race_date="2027-04-25", notes=_incompressible(2_200_000)
    )
    device.save()
    assert sorted(store_events(fake)) == sorted(_chunk_ids(3))

    for notes, chunks in ((_incompressible(1_350_000), 2), ("short again", 1)):
        device.container.races.upsert(
            race_id=race.id, name="Diary race", race_date="2027-04-25", notes=notes
        )
        device.save()

        assert sorted(store_events(fake)) == sorted(_chunk_ids(chunks))
        restored = boot(fake)
        assert restored.container.races.get(race.id).notes == notes


def test_a_failed_leftover_delete_still_advances_the_revision(fake, monkeypatch):
    device = boot(fake)
    race = device.container.races.upsert(
        name="Diary race", race_date="2027-04-25", notes=_incompressible(2_200_000)
    )
    device.save()
    device.container.races.upsert(
        race_id=race.id, name="Diary race", race_date="2027-04-25", notes="short"
    )

    def refuse(_ids):
        raise RuntimeError("intervals.icu hiccup")

    monkeypatch.setattr(device.client, "bulk_delete_events", refuse)
    assert device.save() == 2
    # Stale chunks are ignored, and the device's next save is not a conflict.
    assert boot(fake).rev == 2
    device.container.memory.set("## Notes\n- after the hiccup")
    monkeypatch.undo()
    assert device.save() == 3
    assert sorted(store_events(fake)) == [STORE_ID]
