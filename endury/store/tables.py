"""The durable tables as rows: dump, replace, the plan-id seed, dirty tracking.

Every statement here is generic over :data:`DURABLE_TABLES` — rows travel
verbatim, all columns, ids included — with three exceptions the note format
needs: ``athlete_memory`` leaves its ``content`` to the markdown above the
fence, ``athlete_memory_history`` keeps only its last :data:`HISTORY_KEEP`
rows, and archived plans stay behind (:data:`_LIVE_FILTERS`). The dynamic
SQL interpolates nothing but the table names authored below.
"""

# S608 (string-built SQL): every interpolated identifier is a constant of
# this module, never input — see the docstring.
# ruff: noqa: S608

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from endury.database.sqlite.session import DatabaseSession

Row = dict[str, Any]
Tables = dict[str, list[Row]]

#: Everything the store mirrors, parents before children: inserted in this
#: order, deleted in reverse (foreign keys are on).
DURABLE_TABLES = (
    "macro_plans",
    "plans",
    "plan_sessions",
    "athlete_goals",
    "athlete_self_assessment",
    "athlete_profile",
    "athlete_memory",
    "athlete_memory_history",
)
#: The pseudo-table carrying the durable tables' AUTOINCREMENT counters.
SEQUENCE_TABLE = "sqlite_sequence"
#: The table whose ids the empty-store seed starts above.
PLAN_TABLE = "plans"
MEMORY_TABLE = "athlete_memory"
MEMORY_COLUMN = "content"
HISTORY_TABLE = "athlete_memory_history"
#: Superseded memory versions kept in the note (the panel's "Restore previous").
HISTORY_KEEP = 10

#: The TEMP table counting durable writes; TEMP objects are never dumped.
_DIRTY_TABLE = "endury_store_dirty"
_DIRTY_ACTIONS = ("INSERT", "UPDATE", "DELETE")


#: Archived plans (and their sessions and macro rows) stay out of the note:
#: nothing reads them once archived, and carrying every past plan would
#: grow each upload without bound. The id counters still travel, so ids
#: never repeat.
_LIVE_PLANS = "SELECT id FROM plans WHERE status != 'archived'"
_LIVE_FILTERS = {
    "plans": "WHERE status != 'archived'",
    "plan_sessions": f"WHERE plan_id IN ({_LIVE_PLANS})",
    "macro_plans": (
        "WHERE status != 'archived' OR id IN "
        "(SELECT macro_plan_id FROM plans WHERE status != 'archived')"
    ),
}


def _select(table: str) -> str:
    """Return the dump query for ``table``."""
    if table == HISTORY_TABLE:
        return (
            f"SELECT * FROM (SELECT * FROM {table} ORDER BY id DESC "
            f"LIMIT {HISTORY_KEEP}) ORDER BY id"
        )
    return f"SELECT * FROM {table} {_LIVE_FILTERS.get(table, '')} ORDER BY rowid"


def _marks(count: int) -> str:
    """Return ``count`` comma-separated bind markers."""
    return ", ".join("?" * count)


def _rows(cursor: sqlite3.Cursor, sql: str, params: Sequence[Any] = ()) -> list[Row]:
    """Run one read and return its rows as plain dicts."""
    return [dict(row) for row in cursor.execute(sql, params).fetchall()]


def _set_sequences(
    cursor: sqlite3.Cursor, names: Iterable[str], rows: Iterable[Row]
) -> None:
    """Replace the ``sqlite_sequence`` rows of ``names`` with ``rows``.

    SQLite reads the first row it finds per name, so a counter is always
    deleted before it is written.
    """
    names = tuple(names)
    cursor.execute(
        f"DELETE FROM {SEQUENCE_TABLE} WHERE name IN ({_marks(len(names))})", names
    )
    cursor.executemany(
        f"INSERT INTO {SEQUENCE_TABLE} (name, seq) VALUES (?, ?)",
        [(row["name"], row["seq"]) for row in rows],
    )


def dump(session: DatabaseSession) -> tuple[str, Tables]:
    """Read every durable table.

    Args:
        session: The database to read.

    Returns:
        The memory markdown and the tables' rows — ``athlete_memory``
        without its content, which the markdown carries — plus the
        durable tables' ``sqlite_sequence`` rows under that name.
    """
    with session.cursor() as cursor:
        tables = {table: _rows(cursor, _select(table)) for table in DURABLE_TABLES}
        tables[SEQUENCE_TABLE] = _rows(
            cursor,
            f"SELECT name, seq FROM {SEQUENCE_TABLE} "
            f"WHERE name IN ({_marks(len(DURABLE_TABLES))})",
            DURABLE_TABLES,
        )
    # The one memory row's content, or "" when the row is gone.
    memory: str = next((row.pop(MEMORY_COLUMN) for row in tables[MEMORY_TABLE]), "")
    return memory, tables


def replace(
    session: DatabaseSession, memory: str, tables: Mapping[str, list[Row]]
) -> None:
    """Replace the durable tables with ``tables``, ids and counters included.

    Runs inside the caller's transaction. A column the schema lacks is
    dropped from its row and a table it lacks is skipped, so a note from
    a newer bundle still restores what this one understands.

    Args:
        session: The database to write.
        memory: The memory markdown, restored as ``athlete_memory.content``.
        tables: Rows per table, as :func:`dump` returned them.
    """
    with session.cursor() as cursor:
        for table in reversed(DURABLE_TABLES):
            cursor.execute(f"DELETE FROM {table}")
        for table in DURABLE_TABLES:
            columns = {
                row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")
            }
            for row in tables.get(table, []):
                values = {name: value for name, value in row.items() if name in columns}
                if table == MEMORY_TABLE:
                    values[MEMORY_COLUMN] = memory
                cursor.execute(
                    f"INSERT INTO {table} ({', '.join(values)}) "
                    f"VALUES ({_marks(len(values))})",
                    list(values.values()),
                )
        _set_sequences(cursor, DURABLE_TABLES, tables.get(SEQUENCE_TABLE, []))


def seed_plan_ids(session: DatabaseSession, seed: int) -> None:
    """Start ``plans`` ids above ``seed``.

    A fresh store must never hand out a plan id whose calendar events may
    still exist from an earlier database, so the counter starts at the
    current unix seconds rather than at zero.

    Args:
        session: The database to write.
        seed: The value the next plan id is one above.
    """
    with session.cursor() as cursor:
        _set_sequences(cursor, (PLAN_TABLE,), [{"name": PLAN_TABLE, "seq": seed}])


def install_dirty_tracking(session: DatabaseSession) -> None:
    """Count durable writes in a TEMP table through TEMP triggers; idempotent.

    ``conversation_history`` has no trigger, so a chat line never counts
    as a store change; the chat note (:mod:`endury.store.chat`) saves it
    separately.

    Args:
        session: The database to instrument.
    """
    with session.cursor() as cursor:
        cursor.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS {_DIRTY_TABLE} "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), writes INTEGER NOT NULL)"
        )
        cursor.execute(
            f"INSERT OR IGNORE INTO {_DIRTY_TABLE} (id, writes) VALUES (1, 0)"
        )
        for table in DURABLE_TABLES:
            for action in _DIRTY_ACTIONS:
                cursor.execute(
                    "CREATE TEMP TRIGGER IF NOT EXISTS "
                    f"{_DIRTY_TABLE}_{table}_{action} AFTER {action} ON {table} "
                    f"BEGIN UPDATE {_DIRTY_TABLE} SET writes = writes + 1; END"
                )


def is_dirty(session: DatabaseSession) -> bool:
    """Return whether a durable table changed since :func:`mark_clean`.

    Args:
        session: An instrumented database (see :func:`install_dirty_tracking`).
    """
    with session.cursor() as cursor:
        row = cursor.execute(f"SELECT writes FROM {_DIRTY_TABLE}").fetchone()
    return int(row["writes"]) > 0


def mark_clean(session: DatabaseSession) -> None:
    """Take the dirty baseline: nothing is pending upload from here on.

    Args:
        session: An instrumented database (see :func:`install_dirty_tracking`).
    """
    with session.cursor() as cursor:
        cursor.execute(f"UPDATE {_DIRTY_TABLE} SET writes = 0")
