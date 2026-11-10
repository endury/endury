"""Revisions: every save bumps the rev, a stale rev is a conflict, dirt survives failure."""

from __future__ import annotations

import pytest

from endury.intervals.client import IntervalsApiError
from endury.store import StoreConflict, is_dirty, load, remote_rev, save
from tests.doubles import FakeIntervals
from tests.store.helpers import STORE_ID, boot, decode_note


@pytest.fixture
def fake() -> FakeIntervals:
    return FakeIntervals()


def test_each_save_increments_the_rev(fake):
    device = boot(fake)
    assert device.rev == 0
    assert remote_rev(fake.client()) == 0

    revs = []
    for n in range(3):
        device.container.memory.set(f"## Notes\n- save {n}")
        revs.append(device.save())

    assert revs == [1, 2, 3]
    assert remote_rev(fake.client()) == 3
    snapshot = load(fake.client())
    assert snapshot is not None
    assert snapshot.rev == 3
    assert not snapshot.read_only
    assert decode_note(fake.events[STORE_ID]["description"])[1]["rev"] == 3


def test_a_save_marks_the_session_clean(fake):
    device = boot(fake)
    device.container.memory.set("## Notes\n- dirty")
    assert is_dirty(device.session)

    device.save()

    assert not is_dirty(device.session)


def test_a_stale_rev_is_a_conflict_and_writes_nothing(fake):
    device_a = boot(fake)
    device_a.container.memory.set("## Notes\n- from A")
    device_a.save()
    device_b = boot(fake)
    device_b.container.memory.set("## Notes\n- from B")
    device_b.save()
    device_a.container.memory.set("## Notes\n- A again, unaware of B")
    before = dict(fake.events[STORE_ID])

    with pytest.raises(StoreConflict):
        save(device_a.client, device_a.session, expected_rev=1)

    assert fake.events[STORE_ID] == before
    assert remote_rev(fake.client()) == 2
    assert is_dirty(device_a.session)


def test_two_first_boots_cannot_both_create_the_store(fake):
    device_a = boot(fake)
    device_b = boot(fake)
    device_a.container.memory.set("## Notes\n- A was first")
    device_a.save()
    device_b.container.memory.set("## Notes\n- B thought it was first")

    with pytest.raises(StoreConflict):
        save(device_b.client, device_b.session, expected_rev=0)

    assert decode_note(fake.events[STORE_ID]["description"])[1]["rev"] == 1
    assert is_dirty(device_b.session)


def test_a_failed_upload_keeps_the_session_dirty(fake):
    device = boot(fake)
    device.container.memory.set("## Notes\n- not uploaded yet")
    fake.fail_next(400, method="POST")

    with pytest.raises(IntervalsApiError):
        save(device.client, device.session, expected_rev=0)

    assert is_dirty(device.session)
    assert remote_rev(fake.client()) == 0
    assert device.save() == 1
    assert not is_dirty(device.session)
