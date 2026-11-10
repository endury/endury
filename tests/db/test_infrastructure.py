"""Tests for db/session.py (file-based), db/config.py, and container.py."""

import os
from unittest.mock import patch

import pytest

from endury.database.sqlite.session import DatabaseSession
from endury.wiring.container import RepositoryContainer

# DatabaseSession — file-based (covers session.py:72, 84-86)


def test_session_creates_parent_directory(tmp_path):
    db_path = str(tmp_path / "subdir" / "nested" / "test.db")
    session = DatabaseSession(db_path)
    assert os.path.exists(db_path)
    session.close()


def test_session_file_based_init_schema(tmp_path):
    db_path = str(tmp_path / "test.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    # Should not raise; verify by querying schema_version
    with session.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
    assert "plan_sessions" in tables
    assert "athlete_profile" in tables
    session.close()


def test_session_file_wal_mode(tmp_path):
    db_path = str(tmp_path / "test.db")
    session = DatabaseSession(db_path)
    with session.cursor() as cur:
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    assert mode == "wal"
    session.close()


def test_session_migrate_columns_idempotent(tmp_path):
    """init_schema twice should not fail (covers _migrate_columns)."""
    db_path = str(tmp_path / "test.db")
    session = DatabaseSession(db_path)
    session.init_schema()
    session.init_schema()  # Second call should be idempotent
    session.close()


def test_session_cursor_rollback_on_error():
    """Cursor rolls back if body raises (covers session.py error path)."""
    session = DatabaseSession(":memory:")
    session.init_schema()

    with pytest.raises(ValueError, match="intentional error"), session.cursor():
        raise ValueError("intentional error")

    session.close()


def test_session_in_memory_no_wal():
    """In-memory sessions don't set WAL (wal only for file DBs)."""
    session = DatabaseSession(":memory:")
    with session.cursor() as cur:
        cur.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
    # In-memory mode stays as "memory" or "delete", not WAL
    assert mode != "wal"
    session.close()


# RepositoryContainer.from_path (covers container.py:40-42)


def test_container_from_path_creates_session(tmp_path):
    db_path = str(tmp_path / "test.db")
    container = RepositoryContainer.from_path(db_path)
    assert container is not None
    assert container.athlete is not None
    assert container.plan is not None
    container.session.close()


def test_container_from_path_initialises_schema(tmp_path):
    db_path = str(tmp_path / "test.db")
    container = RepositoryContainer.from_path(db_path)
    with container.session.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
    assert "plan_sessions" in tables
    container.session.close()


def test_container_in_memory_works():
    container = RepositoryContainer.in_memory()
    assert container is not None
    assert container.memory is not None


# init_db() convenience function (covers db/__init__.py:29-31)


def test_init_db_creates_schema(tmp_path):
    """Test init_db() initializes database schema."""
    from endury.database.sqlite import init_db

    db_path = str(tmp_path / "test.db")

    with patch("endury.database.sqlite.get_default_db_path", return_value=db_path):
        init_db()

    # Verify database was created and schema initialized
    session = DatabaseSession(db_path)
    with session.cursor() as cur:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
    assert "plan_sessions" in tables
    session.close()
