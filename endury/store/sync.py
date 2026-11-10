"""``load`` / ``restore`` / ``save``: the durable tables ⇄ the intervals.icu note.

The note is a snapshot of the tables, never a rebuild from the plan
request: a rebuild would silently change a stored plan whenever a menu is
edited. Revisions are optimistic — ``save`` reads the remote rev first and
refuses to overwrite one it did not build on.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass
from datetime import date
from typing import Any

from endury.common import clock
from endury.common.exceptions import EnduryError
from endury.database.sqlite.migrations import LATEST_VERSION
from endury.database.sqlite.session import DatabaseSession
from endury.intervals.client import IntervalsClient
from endury.store import note, tables

logger = logging.getLogger(__name__)


class StoreError(EnduryError):
    """The store refused an operation."""


class StoreConflict(StoreError):
    """The remote note moved since this session last read it."""


class StoreReadOnly(StoreError):
    """A newer bundle wrote the remote note; saving over it would drop columns."""


class StoreCorrupt(StoreError):
    """The remote note exists but cannot be decoded (edited or truncated)."""


@dataclass(frozen=True)
class Snapshot:
    """One decoded note: what :func:`restore` writes into the tables."""

    rev: int
    db_user_version: int
    memory: str
    tables: dict[str, list[dict[str, Any]]]

    @property
    def read_only(self) -> bool:
        """Whether a newer schema wrote the note, so this bundle must not save."""
        return self.db_user_version > LATEST_VERSION


@dataclass(frozen=True)
class Remote:
    """One read of the calendar: the decoded note and every chunk id found."""

    snapshot: Snapshot | None
    chunk_ids: frozenset[str]

    @property
    def rev(self) -> int:
        """The note's revision; 0 without one."""
        return 0 if self.snapshot is None else self.snapshot.rev

    @property
    def read_only(self) -> bool:
        """Whether the note refuses saves (see :attr:`Snapshot.read_only`)."""
        return self.snapshot is not None and self.snapshot.read_only


def _is_chunk(external_id: str) -> bool:
    """Whether ``external_id`` names the note or one of its chunks."""
    return external_id == note.STORE_ID or external_id.startswith(f"{note.STORE_ID}:")


def _decode(chunks: dict[str, str]) -> Snapshot:
    """Reassemble the chunks the first one counts and decode them."""
    try:
        count = note.chunk_count(chunks[note.STORE_ID])
        body = "".join(chunks[note.chunk_id(index)] for index in range(1, count + 1))
        memory, state = note.decode(body)
        return Snapshot(
            rev=int(state["rev"]),
            db_user_version=int(state["db_user_version"]),
            memory=memory,
            tables=state["tables"],
        )
    except (KeyError, TypeError, ValueError, zlib.error) as exc:
        raise StoreCorrupt(
            "The Endury data note on intervals.icu cannot be read "
            f"({type(exc).__name__}: {exc})"
        ) from exc


def read(client: IntervalsClient) -> Remote:
    """Read the note once: its decoded snapshot, revision and chunk ids.

    A caller that checks the revision before writing can hand this read to
    :func:`save`, so a write costs one download of the note, not two.

    Args:
        client: The athlete's intervals.icu client.
    """
    return _read(client)


def delete(client: IntervalsClient) -> int:
    """Delete the note and every chunk of it, on both store dates.

    Args:
        client: The athlete's intervals.icu client.

    Returns:
        How many events were deleted.
    """
    stale = set()
    for when in note.STORE_DATE_CANDIDATES:
        day = date.fromisoformat(when[:10])
        for event in client.list_events(day, day, category="NOTE"):
            if (external_id := event.external_id) and _is_chunk(external_id):
                stale.add(external_id)
    return client.bulk_delete_events(sorted(stale)) if stale else 0


def _read(client: IntervalsClient) -> Remote:
    """Read the note from the first store date holding its first chunk."""
    for when in note.STORE_DATE_CANDIDATES:
        day = date.fromisoformat(when[:10])
        chunks = {
            external_id: event.description or ""
            for event in client.list_events(day, day, category="NOTE")
            if (external_id := event.external_id) and _is_chunk(external_id)
        }
        if note.STORE_ID in chunks:
            return Remote(_decode(chunks), frozenset(chunks))
    return Remote(None, frozenset())


def _event(external_id: str, description: str) -> dict[str, Any]:
    """Build one chunk's NOTE event payload."""
    return {
        "external_id": external_id,
        "category": "NOTE",
        "name": note.STORE_NAME,
        "description": description,
        "start_date_local": note.STORE_DATE,
        "hide_from_athlete": True,
    }


def load(client: IntervalsClient) -> Snapshot | None:
    """Read the store note; ``None`` when the account holds none.

    Args:
        client: The athlete's intervals.icu client.
    """
    return _read(client).snapshot


def remote_rev(client: IntervalsClient) -> int:
    """Return the note's revision; 0 without one.

    Args:
        client: The athlete's intervals.icu client.
    """
    return _read(client).rev


def restore(session: DatabaseSession, snapshot: Snapshot | None) -> None:
    """Bring the session's durable tables to ``snapshot``, in one transaction.

    ``None`` — no note yet — keeps the ``init_schema`` defaults and seeds
    the ``plans`` counter with the current unix seconds; this must land
    before any write, since the first commit pushes ``coachie:{plan_id}:*``
    events and the calendar may still carry an earlier database's. Either
    way the dirty-tracking triggers are installed; the caller takes the
    clean baseline afterwards (:func:`mark_clean`).

    Args:
        session: The database to write.
        snapshot: What :func:`load` returned.
    """
    with session.transaction():
        if snapshot is None:
            tables.seed_plan_ids(session, int(clock.now().timestamp()))
        else:
            tables.replace(session, snapshot.memory, snapshot.tables)
        tables.install_dirty_tracking(session)


def save(
    client: IntervalsClient,
    session: DatabaseSession,
    expected_rev: int,
    remote: Remote | None = None,
) -> int:
    """Upload the durable tables as revision ``expected_rev + 1``.

    The dirty flag only clears once the upload succeeded: a failure leaves
    it set for the next attempt.

    Args:
        client: The athlete's intervals.icu client.
        session: The database to dump.
        expected_rev: The revision this session last loaded or saved.
        remote: A :func:`read` taken earlier in the same call, reused
            instead of downloading the note again.

    Returns:
        The new revision.

    Raises:
        StoreReadOnly: The remote note was written by a newer schema.
        StoreConflict: The remote note is not at ``expected_rev``.
    """
    remote = remote if remote is not None else _read(client)
    if remote.read_only:
        raise StoreReadOnly("A newer version of Endury wrote this store")
    if remote.rev != expected_rev:
        raise StoreConflict(
            f"the store is at revision {remote.rev}, not {expected_rev}"
        )
    memory, rows = tables.dump(session)
    state = {
        "schema": note.SCHEMA,
        "rev": expected_rev + 1,
        "saved_at": clock.now().isoformat(),
        "db_user_version": LATEST_VERSION,
        "tables": rows,
    }
    chunks = note.encode(memory, state)
    ids = [note.chunk_id(index) for index in range(1, len(chunks) + 1)]
    client.bulk_upsert_events(
        [
            _event(external_id, body)
            for external_id, body in zip(ids, chunks, strict=True)
        ]
    )
    # The upload landed: the remote is now at expected_rev + 1, so the
    # session must be too even if tidying the leftover chunks fails — a
    # stale chunk is ignored by the chunk count and removed next save.
    tables.mark_clean(session)
    leftovers = sorted(remote.chunk_ids.difference(ids))
    if leftovers:
        try:
            client.bulk_delete_events(leftovers)
        except Exception:
            logger.warning(
                "Could not delete leftover store chunks %s", leftovers, exc_info=True
            )
    return expected_rev + 1
