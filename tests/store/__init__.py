"""Tests for ``endury.store``: the durable tables mirrored to one intervals.icu NOTE.

The contract these tests pin (``endury.store``'s public surface):

* ``load(client) -> Snapshot | None`` — read the ``coachie:store`` note
  (searched at both ``2099-12-31T00:00:00`` and ``2030-12-31T00:00:00``,
  chunks ``coachie:store:2..n`` reassembled); ``None`` when there is none.
  A snapshot carries ``rev`` and ``read_only`` (its ``db_user_version`` is
  newer than ``migrations.LATEST_VERSION``).
* ``restore(session, snapshot)`` — one transaction. ``None`` keeps the
  ``init_schema`` defaults and seeds ``sqlite_sequence('plans')`` with the
  current unix seconds; a snapshot replaces the durable tables row for row
  (ids and ``sqlite_sequence`` preserved, ``athlete_memory.content`` from
  the note's markdown). Also installs the TEMP dirty-tracking triggers.
* ``save(client, session, expected_rev) -> int`` — refuses with
  ``StoreConflict`` when the remote rev is not ``expected_rev`` and with
  ``StoreReadOnly`` when the remote note is from a newer schema; otherwise
  writes rev + 1, deletes chunks the new body no longer needs, marks the
  session clean and returns the new rev.
* ``remote_rev(client) -> int`` — the note's rev, 0 without one.
* ``is_dirty(session)`` / ``mark_clean(session)`` — the durable-write flag.
* ``STORE_DATE`` — where ``save`` writes the note.

The note's wire format is pinned too (``helpers.decode_note``): the
athlete-memory markdown first, then the LAST ```` ```coachie-state ````
fence holding ``base64(zlib(json))`` of ``{schema: 1, rev, saved_at,
db_user_version, tables: {name: [row dict, ...]}}``.
"""
