"""Injectable SQLite database session.

Pass one session to all repositories sharing the same SQLite file.

Usage:
    session = DatabaseSession("endury.db")
    session.init_schema()
    repo = PlanRepository(session)
"""

import contextlib
import logging
import os
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from endury.database.sqlite.migrations import run_migrations

logger = logging.getLogger(__name__)

_default_db_path: str | None = None


def set_default_db_path(path: str) -> None:
    """Set the default database path used by get_default_db_path()."""
    global _default_db_path
    _default_db_path = path


def get_default_db_path() -> str:
    """Return the configured default database path.

    Resolution order: explicit ``set_default_db_path``, then the
    ``ENDURY_DB_PATH`` environment variable (how a subprocess is
    pointed at a scratch DB), then the
    project-root ``endury.db``.
    """
    global _default_db_path
    if _default_db_path is None:
        env_path = os.getenv("ENDURY_DB_PATH")
        if env_path:
            _default_db_path = env_path
        else:
            project_root = Path(__file__).parents[3]
            _default_db_path = str(project_root / "endury.db")
    return _default_db_path


class DatabaseSession:
    """Injectable SQLite database session.

    Wraps a sqlite3.Connection and provides a cursor() context manager that
    auto-commits on success and auto-rolls back on error.

    The connection is shared across threads (``check_same_thread=False``) and
    so is its single implicit transaction. Transaction nesting is therefore
    tracked per thread, and ``cursor()``/``transaction()`` hold a reentrant
    lock for the whole block: a bare write on one thread waits for another
    thread's open transaction instead of joining it — and being rolled back
    with it.

    Call init_schema() once after construction to create all tables.
    """

    def __init__(self, db_path: str) -> None:
        """Open a connection to the given SQLite file (or ':memory:').

        Args:
            db_path: Path to the SQLite database file, or ':memory:'.
        """
        self._path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(
            db_path,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
            check_same_thread=False,
        )
        self._local = threading.local()
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

        # WAL mode is not supported for :memory: databases
        if db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA cache_size = -64000")  # 64 MB

    @contextmanager
    def cursor(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager that yields a cursor and commits/rolls back.

        Holds the session lock for the whole block, so the commit decision is
        made against a connection no other thread can be mid-transaction on.

        Yields:
            sqlite3.Cursor
        """
        from endury.common.exceptions import RepositoryError

        c: sqlite3.Cursor | None = None
        with self._lock:
            transaction_depth = getattr(self._local, "transaction_depth", 0)
            try:
                c = self._conn.cursor()
                yield c
                if transaction_depth == 0:
                    self._conn.commit()
            except sqlite3.Error as e:
                if transaction_depth == 0:
                    with contextlib.suppress(Exception):
                        self._conn.rollback()
                raise RepositoryError(f"Database operation failed: {e}") from e
            except Exception:
                if transaction_depth == 0:
                    with contextlib.suppress(Exception):
                        self._conn.rollback()
                raise
            finally:
                if c is not None:
                    c.close()

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Run operations in one atomic transaction across repositories.

        Nested transactions on the same thread are supported; only the
        outermost context opens and commits/rolls back the underlying SQLite
        transaction. Another thread's work is excluded for the whole block
        rather than swept into this transaction.
        """
        with self._lock:
            depth = getattr(self._local, "transaction_depth", 0)
            is_outermost = depth == 0
            if is_outermost:
                self._conn.execute("BEGIN IMMEDIATE")
            self._local.transaction_depth = depth + 1
            try:
                yield
                if is_outermost:
                    self._conn.commit()
            except Exception:
                if is_outermost:
                    with contextlib.suppress(Exception):
                        self._conn.rollback()
                raise
            finally:
                self._local.transaction_depth = max(
                    self._local.transaction_depth - 1, 0
                )

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def init_schema(self) -> None:
        """Create all tables and indexes. Safe to call multiple times.

        Applies any pending schema migrations first (see ``migrations.py``),
        so they run against the old shape, then creates the full schema.
        """
        run_migrations(self._conn)
        # executescript() implicitly commits before running — fine for DDL
        self._conn.executescript(_SCHEMA_SQL)
        logger.info("Database schema initialised: %s", self._path)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS athlete_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT,
    experience_level TEXT CHECK (
        experience_level IN ('beginner', 'intermediate', 'advanced', 'elite')
    ),
    primary_sport TEXT,
    -- coach_personality: dead column, kept only so existing databases don't
    -- need a migration to drop it; nothing reads or writes it.
    coach_personality TEXT CHECK (
        coach_personality IN ('motivational', 'analytical', 'blunt', 'supportive')
    ) DEFAULT 'motivational',
    communication_style TEXT CHECK (
        communication_style IN ('concise', 'detailed', 'brief', 'balanced')
    ) DEFAULT 'concise',
    language TEXT DEFAULT 'en',
    preferred_run_surface TEXT CHECK (
        preferred_run_surface IN ('road', 'trail', 'mixed')
    ),
    open_water_access INTEGER,
    race_location_access INTEGER,
    last_session_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO athlete_profile (id, created_at, updated_at)
    VALUES (1, datetime('now'), datetime('now'));

CREATE TABLE IF NOT EXISTS athlete_self_assessment (
    athlete_id INTEGER NOT NULL DEFAULT 1,
    sport TEXT NOT NULL CHECK (sport IN ('running','cycling','swimming')),
    weekly_hours REAL NOT NULL,
    longest_session_min INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (athlete_id, sport)
);

-- The athlete's races: the single home for goal-race data, upserted by
-- plan commits/replans (matched on race_date+goal_type) and directly by
-- the athlete/coach (upsert_race). No hard delete — a dropped race is
-- status='cancelled'.
CREATE TABLE IF NOT EXISTS athlete_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    athlete_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    race_date TEXT NOT NULL,
    goal_type TEXT,
    priority TEXT NOT NULL DEFAULT 'A' CHECK (priority IN ('A','B','C')),
    distance_km REAL,
    climb_m REAL,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (
        status IN ('planned','completed','dns','dnf','cancelled')
    ),
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_athlete_goals_date ON athlete_goals(race_date);

-- The athlete-memory document: one markdown text the coach maintains and
-- the athlete edits, injected whole into every system prompt.
CREATE TABLE IF NOT EXISTS athlete_memory (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    content TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);

INSERT OR IGNORE INTO athlete_memory (id, content, updated_at)
    VALUES (1, '', datetime('now'));

CREATE TABLE IF NOT EXISTS athlete_memory_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Planner tables (planner session-menu engine)
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    goal_type TEXT NOT NULL,
    goal_date TEXT NOT NULL,
    plan_start TEXT NOT NULL,
    athlete_state_json TEXT,
    solve_result_json TEXT NOT NULL,
    solver_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    macro_plan_id INTEGER REFERENCES macro_plans(id),
    revision INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
CREATE INDEX IF NOT EXISTS idx_plans_goal_date ON plans(goal_date);
CREATE INDEX IF NOT EXISTS idx_plans_macro_plan_id ON plans(macro_plan_id);

CREATE TABLE IF NOT EXISTS plan_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    slot_id TEXT NOT NULL,
    date TEXT NOT NULL,
    week_index INTEGER NOT NULL,
    phase_type TEXT NOT NULL,
    sport TEXT NOT NULL,
    activity_key TEXT NOT NULL,
    family TEXT NOT NULL,
    target_tss REAL NOT NULL,
    achieved_tss REAL NOT NULL,
    duration_minutes REAL,
    duration_target_minutes REAL,
    tid_fractions_json TEXT,
    workout_json TEXT NOT NULL,
    matched_activity_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    pushed_event_json TEXT,
    optional INTEGER NOT NULL DEFAULT 0,
    UNIQUE(plan_id, slot_id)
);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_plan_id ON plan_sessions(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_date ON plan_sessions(date);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_sport ON plan_sessions(plan_id, sport);
CREATE INDEX IF NOT EXISTS idx_plan_sessions_plan_date_slot
    ON plan_sessions(plan_id, date, slot_id);

CREATE TABLE IF NOT EXISTS macro_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_type TEXT NOT NULL,
    goal_date TEXT NOT NULL,
    plan_start TEXT NOT NULL,
    total_weeks INTEGER NOT NULL,
    ambition_level TEXT NOT NULL,
    weeks_json TEXT NOT NULL,
    coaching_brief TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    revision_reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_macro_plans_status ON macro_plans(status);

"""
