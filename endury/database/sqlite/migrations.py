"""Ordered SQLite schema migrations, versioned by ``PRAGMA user_version``.

``MIGRATIONS`` is an ordered list of numbered steps; :func:`run_migrations`
applies every step newer than the database's own ``user_version``, each in
its own transaction, before ``DatabaseSession.init_schema``'s
create-if-missing pass, so a migration always sees the previous schema.

A brand-new database has no tables to migrate and is stamped straight to
``LATEST_VERSION``, since ``init_schema`` is about to create the current
schema outright. A database with tables but ``user_version = 0`` is exactly
the version-1 baseline, so migration 1 is an empty stamp and only
migrations 2+ do real work on it.

To add a migration: append a :class:`Migration` with the next version
number and its statements, and update the DDL in ``session.py`` to match.

Every step stays in the list: an athlete's stored data can be at any
earlier version.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

MigrationStep = str | Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    """One numbered schema migration.

    Attributes:
        version: The ``user_version`` the database carries once applied.
        description: Short human-readable summary, logged when applied.
        steps: One SQL statement per string, or a callable taking the open
            connection (for data migrations), applied in order inside a
            single transaction.
    """

    version: int
    description: str
    steps: tuple[MigrationStep, ...] = ()


#: Lead bullet of a seeded document: the fold is best-effort, and only the
#: legacy ``Constraint:`` prefix marked a row as one — anything the old
#: ``memorize`` tool wrote raw lands under notes and needs a human eye.
_IMPORT_NOTE = (
    "Imported from the old memory list on upgrade — entries that carried no "
    "label landed here, so re-file anything that is still a hard constraint "
    "and delete whatever no longer applies."
)


def _seed_document(constraints: list[str], notes: list[str]) -> str:
    """Render the folded legacy memories as the seeded markdown document.

    Args:
        constraints: Bullets for the constraints heading.
        notes: Bullets for the notes heading, oldest first.

    Returns:
        The document, with the import note leading the notes section.
    """
    return "\n\n".join(
        "\n".join([heading, *(f"- {item}" for item in items)])
        for heading, items in (
            ("## Goals & races", []),
            ("## Constraints (injury, availability)", constraints),
            ("## Preferences", []),
            ("## Notes", [_IMPORT_NOTE, *notes]),
        )
    )


def _seed_athlete_memory(conn: sqlite3.Connection) -> None:
    """Fold the old ``conversation_memory`` rows into the memory document.

    Rows stored with the legacy ``Constraint:`` prefix become bullets under
    the constraints heading, everything else lands under notes. A database
    with no memories — or none of that table at all — keeps the empty
    document the DDL defaults to.

    The seed obeys the same character cap as every other write: the document
    is injected whole into every system prompt, so an install with years of
    memories drops its oldest rows — notes before constraints — until the
    fold fits.

    Args:
        conn: Open SQLite connection, mid-migration.
    """
    # Imported here: the repository imports DatabaseSession, which imports
    # this module.
    from endury.database.sqlite.repositories.athlete_memory import MEMORY_CHAR_CAP

    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'conversation_memory'"
    ).fetchone():
        return
    rows = conn.execute(
        "SELECT content FROM conversation_memory ORDER BY id"
    ).fetchall()
    constraints: list[str] = []
    notes: list[str] = []
    for row in rows:
        text = " ".join(str(row[0] or "").split())
        if not text:
            continue
        if text.startswith("Constraint:"):
            constraints.append(text[len("Constraint:") :].strip())
        else:
            notes.append(text.removeprefix("User:").strip())
    if not constraints and not notes:
        return

    document = _seed_document(constraints, notes)
    while len(document) > MEMORY_CHAR_CAP and (notes or constraints):
        (notes or constraints).pop(0)
        document = _seed_document(constraints, notes)
    conn.execute(
        "INSERT OR REPLACE INTO athlete_memory (id, content, updated_at) "
        "VALUES (1, ?, datetime('now'))",
        (document,),
    )


def _add_optional(conn: sqlite3.Connection) -> None:
    """Add ``plan_sessions.optional`` unless the table already has it."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(plan_sessions)")}
    if "optional" not in columns:
        conn.execute(
            "ALTER TABLE plan_sessions ADD COLUMN optional INTEGER NOT NULL DEFAULT 0"
        )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="baseline: the schema as created by init_schema",
        steps=(),
    ),
    Migration(
        version=2,
        description="plan_sessions.pushed_event_json: last pushed calendar event",
        steps=("ALTER TABLE plan_sessions ADD COLUMN pushed_event_json TEXT",),
    ),
    Migration(
        version=3,
        description="athlete_memory: one document, seeded from conversation_memory",
        steps=(
            "CREATE TABLE IF NOT EXISTS athlete_memory ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), "
            "content TEXT NOT NULL DEFAULT '', "
            "updated_at TEXT)",
            "CREATE TABLE IF NOT EXISTS athlete_memory_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, "
            "created_at TEXT NOT NULL)",
            _seed_athlete_memory,
            "DROP TABLE IF EXISTS conversation_memory",
        ),
    ),
    Migration(
        version=4,
        description="plan_sessions.optional: a session outside the load model",
        steps=(_add_optional,),
    ),
)

LATEST_VERSION = MIGRATIONS[-1].version


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the schema version the database currently carries.

    Args:
        conn: Open SQLite connection.

    Returns:
        The ``PRAGMA user_version`` value; 0 for a database that predates
        this module.
    """
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def run_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Bring one database up to the newest schema version.

    Args:
        conn: Open SQLite connection, before any create-if-missing pass.
        migrations: Ordered migrations to consider, newest last. Defaults to
            the module-level ``MIGRATIONS``.

    Returns:
        The schema version the database carries afterwards.
    """
    latest = migrations[-1].version
    current = get_schema_version(conn)
    if current >= latest:
        return current

    if not _has_tables(conn):
        _stamp(conn, latest)
        logger.info("New database stamped at schema version %d", latest)
        return latest

    for migration in migrations:
        if migration.version > current:
            _apply(conn, migration)
    return latest


def _has_tables(conn: sqlite3.Connection) -> bool:
    """Return True when the database already holds non-internal tables."""
    row = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return bool(row[0])


def _stamp(conn: sqlite3.Connection, version: int) -> None:
    """Record ``version`` as the database's schema version."""
    # PRAGMA arguments cannot be bound; versions are ints we author here.
    conn.execute(f"PRAGMA user_version = {int(version)}")
    conn.commit()


def _apply(conn: sqlite3.Connection, migration: Migration) -> None:
    """Apply one migration and bump ``user_version``, atomically."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for step in migration.steps:
            if callable(step):
                step(conn)
            else:
                conn.execute(step)
        conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info(
        "Applied schema migration %d (%s)", migration.version, migration.description
    )
