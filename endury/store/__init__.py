"""The durable tables mirrored to one hidden intervals.icu NOTE.

Browser sessions keep the database in memory; everything worth keeping —
plans, sessions, macro plans, races, self-assessment, profile, the memory
document and its last versions — lives in one NOTE event on the athlete's
own intervals.icu calendar. Boot is ``load`` → ``restore`` → ``mark_clean``;
every durable write flips the dirty flag and ``save`` uploads the next
revision. ``conversation_history`` never counts as a durable write — it is
saved separately, to its own NOTE, by :mod:`endury.store.chat`, and the
coach's saved takes on activities to a third, by :mod:`endury.store.takes`.
"""

from endury.store import chat, takes
from endury.store.note import STORE_DATE
from endury.store.sync import (
    Remote,
    Snapshot,
    StoreConflict,
    StoreCorrupt,
    StoreError,
    StoreReadOnly,
    delete,
    load,
    read,
    remote_rev,
    restore,
    save,
)
from endury.store.tables import DURABLE_TABLES, is_dirty, mark_clean

__all__ = [
    "DURABLE_TABLES",
    "STORE_DATE",
    "Remote",
    "Snapshot",
    "StoreConflict",
    "StoreCorrupt",
    "StoreError",
    "StoreReadOnly",
    "chat",
    "delete",
    "is_dirty",
    "load",
    "mark_clean",
    "read",
    "remote_rev",
    "restore",
    "save",
    "takes",
]
