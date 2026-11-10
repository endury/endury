"""Tests for the ordered PRAGMA user_version schema-migration runner."""

import sqlite3
from collections.abc import Callable

import pytest

from endury.database.sqlite.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
    Migration,
    _seed_athlete_memory,
    get_schema_version,
    run_migrations,
)
from endury.database.sqlite.repositories.athlete import AthleteProfileRepository
from endury.database.sqlite.repositories.athlete_memory import (
    MEMORY_CHAR_CAP,
    AthleteMemoryRepository,
)
from endury.database.sqlite.repositories.plan import PlanRepository
from endury.database.sqlite.repositories.write_models import SessionInsertRow
from endury.database.sqlite.session import DatabaseSession

# Helpers


def _recorder(order: list[int], version: int) -> Callable[[sqlite3.Connection], None]:
    """Return a callable step that records the version it ran at."""

    def _step(conn: sqlite3.Connection) -> None:
        assert conn.in_transaction, "steps must run inside a transaction"
        order.append(version)

    return _step


def _boom(conn: sqlite3.Connection) -> None:
    """A step that must never run."""
    raise AssertionError("this migration step must not run")


def _legacy_conn() -> sqlite3.Connection:
    """In-memory database with tables but no version stamp (user_version 0)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, note TEXT)")
    conn.execute("INSERT INTO probe (id, note) VALUES (1, 'kept')")
    conn.commit()
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of one table."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _schema_version(session: DatabaseSession) -> int:
    """Return the session database's PRAGMA user_version."""
    with session.cursor() as cur:
        cur.execute("PRAGMA user_version")
        return int(cur.fetchone()[0])


def _table_names(db_path: str) -> set[str]:
    """Return the non-internal table names of a database file."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _strip_version_stamp(db_path: str) -> None:
    """Reset user_version to 0, reproducing a database that predates the first migration."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
    finally:
        conn.close()


def _make_version_1_schema(db_path: str, memories: tuple[str, ...] = ()) -> None:
    """Roll a freshly-created database back to the version-1 baseline.

    ``init_schema`` writes today's DDL, which already contains every
    migrated column; a database that predates the migration runner carries
    the version-1 shape instead. Drop what each later migration adds — and
    put back what migration 3 removed — so the fixture is the state those
    migrations actually meet.

    Args:
        db_path: The database file to roll back.
        memories: ``conversation_memory`` rows the old database held.
    """
    conn = sqlite3.connect(db_path)
    try:
        # Migration 2.
        conn.execute("ALTER TABLE plan_sessions DROP COLUMN pushed_event_json")
        # Migration 4.
        conn.execute("ALTER TABLE plan_sessions DROP COLUMN optional")
        # Migration 3.
        conn.execute("DROP TABLE athlete_memory")
        conn.execute("DROP TABLE athlete_memory_history")
        conn.execute(
            "CREATE TABLE conversation_memory ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "content TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        for content in memories:
            conn.execute(
                "INSERT INTO conversation_memory (content, created_at) "
                "VALUES (?, '2026-01-01T07:00:00')",
                (content,),
            )
        conn.commit()
    finally:
        conn.close()


def _populate(session: DatabaseSession) -> None:
    """Write one row through every repository that owns a table."""
    AthleteProfileRepository(session).update(
        {"name": "Sam", "primary_sport": "triathlon"}
    )

    plan_repo = PlanRepository(session)
    plan_id = plan_repo.create(
        name="Ironman prep",
        goal_type="ironman",
        goal_date="2024-09-01",
        plan_start="2024-03-04",
        solve_result_json="{}",
        solver_version="planner_v2_session_menu_v1",
        input_hash="hash-1",
    )
    plan_repo.bulk_insert_sessions(
        plan_id,
        [
            SessionInsertRow(
                slot_id="w0-mon-run",
                date="2024-03-04",
                week_index=0,
                phase_type="base",
                sport="running",
                activity_key="easy_run",
                family="easy",
                target_tss=45.0,
                achieved_tss=45.0,
                duration_minutes=60.0,
                workout_json="{}",
            )
        ],
    )


def _assert_round_trip(session: DatabaseSession) -> None:
    """Read every populated row back through its repository."""
    profile = AthleteProfileRepository(session).get()
    assert profile is not None
    assert profile.name == "Sam"

    # The memory document survived the table swap and is readable.
    assert AthleteMemoryRepository(session).get().content == ""

    plan_repo = PlanRepository(session)
    plan = plan_repo.get_active()
    assert plan is not None
    assert plan.goal_type == "ironman"
    sessions = plan_repo.get_sessions(plan.id)
    assert [s.slot_id for s in sessions] == ["w0-mon-run"]


# The MIGRATIONS list itself


def test_migrations_are_numbered_from_one_without_gaps() -> None:
    versions = [m.version for m in MIGRATIONS]
    assert versions == list(range(1, len(MIGRATIONS) + 1))
    assert versions[-1] == LATEST_VERSION


def test_baseline_migration_has_no_steps() -> None:
    """Migration 1 only stamps: it describes the schema init_schema creates."""
    assert MIGRATIONS[0].version == 1
    assert MIGRATIONS[0].steps == ()


# Starting state (a): a brand-new, empty database


def test_fresh_database_is_stamped_to_the_latest_version() -> None:
    session = DatabaseSession(":memory:")
    session.init_schema()
    assert _schema_version(session) == LATEST_VERSION
    session.close()


def test_fresh_database_skips_every_migration_step() -> None:
    """init_schema creates the current schema, so migrations must not replay."""
    conn = sqlite3.connect(":memory:")
    migrations = (
        Migration(version=1, description="baseline"),
        Migration(version=2, description="destructive", steps=(_boom,)),
    )

    assert run_migrations(conn, migrations) == 2
    assert get_schema_version(conn) == 2
    conn.close()


# Starting state (b): a populated database that predates this module


def test_existing_database_is_stamped_in_place_without_data_loss(tmp_path) -> None:
    """The live endury.db: version-1 schema, user_version 0, real rows.

    Only migration 3's deliberate table swap changes the table set; the
    plan and profile rows are read back unchanged.
    """
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(db_path)
    tables_before = _table_names(db_path)

    migrated = DatabaseSession(db_path)
    migrated.init_schema()

    assert _schema_version(migrated) == LATEST_VERSION
    assert _table_names(db_path) == (tables_before - {"conversation_memory"}) | {
        "athlete_memory",
        "athlete_memory_history",
    }
    _assert_round_trip(migrated)
    migrated.close()


def test_pushed_event_json_reaches_a_pre_migration_database(tmp_path) -> None:
    """Migration 2 adds the intervals.icu push column to an existing plan table."""
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(db_path)

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    with migrated.cursor() as cur:
        cur.execute("PRAGMA table_info(plan_sessions)")
        columns = {row[1] for row in cur.fetchall()}
    migrated.close()

    assert "pushed_event_json" in columns


def test_optional_reaches_a_pre_migration_database(tmp_path) -> None:
    """Migration 4 adds the optional flag, off for every stored session."""
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(db_path)

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    with migrated.cursor() as cur:
        cur.execute("SELECT optional FROM plan_sessions")
        flags = [row[0] for row in cur.fetchall()]
    migrated.close()

    assert flags
    assert set(flags) == {0}


def test_conversation_memory_rows_seed_the_memory_document(tmp_path) -> None:
    """Migration 3 folds the old memory rows into one document, then drops them."""
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(
        db_path,
        memories=(
            "Constraint: no running for six weeks",
            "User: training   for Boston",
            "   ",
        ),
    )

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    document = AthleteMemoryRepository(migrated).get().content
    migrated.close()

    assert (
        "## Constraints (injury, availability)\n- no running for six weeks" in document
    )
    assert "- training for Boston" in document
    assert document.startswith("## Goals & races")
    # The fold only knew the "Constraint:" prefix, so it says so in the document.
    assert "Imported from the old memory list on upgrade" in document
    assert "conversation_memory" not in _table_names(db_path)


def test_a_long_memory_history_is_seeded_within_the_document_cap(tmp_path) -> None:
    """The seed is injected into every prompt, so it obeys the same ceiling."""
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(
        db_path,
        memories=(
            "Constraint: no running for six weeks",
            *(f"note {index} {'x' * 200}" for index in range(60)),
        ),
    )

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    document = AthleteMemoryRepository(migrated).get().content
    migrated.close()

    assert 0 < len(document) <= MEMORY_CHAR_CAP
    # Constraints outlive notes, and the newest notes outlive the oldest.
    assert "- no running for six weeks" in document
    assert "note 59" in document
    assert "note 0 " not in document


def test_constraints_are_dropped_too_when_they_alone_overflow_the_cap() -> None:
    """The cap is absolute: the oldest constraints go once no notes are left."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE conversation_memory (id INTEGER PRIMARY KEY, content TEXT)"
    )
    conn.execute(
        "CREATE TABLE athlete_memory (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "content TEXT NOT NULL DEFAULT '', updated_at TEXT)"
    )
    for index in range(60):
        conn.execute(
            "INSERT INTO conversation_memory (content) VALUES (?)",
            (f"Constraint: {index} {'x' * 200}",),
        )

    _seed_athlete_memory(conn)
    document = conn.execute(
        "SELECT content FROM athlete_memory WHERE id = 1"
    ).fetchone()[0]
    conn.close()

    assert len(document) <= MEMORY_CHAR_CAP
    assert "- 59 " in document
    assert "- 0 " not in document


def test_a_legacy_database_without_memories_keeps_an_empty_document(tmp_path) -> None:
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)
    _make_version_1_schema(db_path)

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    memory = AthleteMemoryRepository(migrated).get()
    migrated.close()

    assert memory.content == ""


def test_legacy_database_migrates_and_round_trips_the_repositories(tmp_path) -> None:
    """A legacy fixture DB survives a migration that rewrites a live table."""
    db_path = str(tmp_path / "legacy.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    _populate(session)
    session.close()
    _strip_version_stamp(db_path)

    migrations = (
        Migration(version=1, description="baseline"),
        Migration(
            version=2,
            description="add a plan_sessions column",
            steps=("ALTER TABLE plan_sessions ADD COLUMN notes TEXT",),
        ),
    )
    conn = sqlite3.connect(db_path)
    try:
        assert run_migrations(conn, migrations) == 2
        assert "notes" in _columns(conn, "plan_sessions")
    finally:
        conn.close()

    migrated = DatabaseSession(db_path)
    migrated.init_schema()
    _assert_round_trip(migrated)
    migrated.close()


def test_pending_migrations_apply_in_order() -> None:
    conn = _legacy_conn()
    order: list[int] = []
    migrations = (
        Migration(version=1, description="baseline"),
        Migration(
            version=2,
            description="add a column",
            steps=("ALTER TABLE probe ADD COLUMN extra TEXT", _recorder(order, 2)),
        ),
        Migration(
            version=3,
            description="add a table",
            steps=(_recorder(order, 3), "CREATE TABLE added (id INTEGER)"),
        ),
    )

    assert run_migrations(conn, migrations) == 3
    assert order == [2, 3]
    assert get_schema_version(conn) == 3
    assert "extra" in _columns(conn, "probe")
    assert conn.execute("SELECT note FROM probe").fetchone()[0] == "kept"
    conn.close()


def test_migrations_at_or_below_the_current_version_are_skipped() -> None:
    conn = _legacy_conn()
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    order: list[int] = []
    migrations = (
        Migration(version=1, description="baseline", steps=(_boom,)),
        Migration(version=2, description="already applied", steps=(_boom,)),
        Migration(version=3, description="pending", steps=(_recorder(order, 3),)),
    )

    assert run_migrations(conn, migrations) == 3
    assert order == [3]
    conn.close()


def test_run_migrations_is_a_noop_at_the_latest_version() -> None:
    conn = _legacy_conn()
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    migrations = (Migration(version=1, description="baseline", steps=(_boom,)),)

    assert run_migrations(conn, migrations) == 1
    conn.close()


def test_a_failing_migration_rolls_back_and_keeps_the_previous_version() -> None:
    conn = _legacy_conn()
    migrations = (
        Migration(version=1, description="baseline"),
        Migration(
            version=2,
            description="half-broken",
            steps=(
                "ALTER TABLE probe ADD COLUMN extra TEXT",
                "DROP TABLE does_not_exist",
            ),
        ),
    )

    with pytest.raises(sqlite3.OperationalError):
        run_migrations(conn, migrations)

    assert get_schema_version(conn) == 1
    assert "extra" not in _columns(conn, "probe")
    conn.close()
