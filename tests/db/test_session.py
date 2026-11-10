"""Tests for DatabaseSession."""

import threading

import pytest

from endury.database.sqlite import session as session_module
from endury.database.sqlite.session import (
    DatabaseSession,
    get_default_db_path,
    set_default_db_path,
)


def test_session_creates_in_memory() -> None:
    s = DatabaseSession(":memory:")
    with s.cursor() as c:
        c.execute("SELECT 1")
        assert c.fetchone()[0] == 1
    s.close()


def test_init_schema_creates_all_tables() -> None:
    s = DatabaseSession(":memory:")
    s.init_schema()

    tables = set()
    with s.cursor() as c:
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in c.fetchall()}

    expected = {
        "plans",
        "plan_sessions",
        "athlete_self_assessment",
        "athlete_profile",
        "athlete_memory",
        "athlete_memory_history",
        "conversation_history",
    }
    assert expected.issubset(tables)
    s.close()


def test_init_schema_idempotent() -> None:
    """Calling init_schema() twice should not raise."""
    s = DatabaseSession(":memory:")
    s.init_schema()
    s.init_schema()  # second call should be a no-op
    s.close()


def test_cursor_commits_on_success() -> None:
    s = DatabaseSession(":memory:")
    s.init_schema()

    with s.cursor() as c:
        c.execute(
            "INSERT INTO athlete_memory_history (content, created_at) VALUES (?, ?)",
            ("test", "2024-01-01"),
        )

    with s.cursor() as c:
        c.execute("SELECT COUNT(*) FROM athlete_memory_history")
        assert c.fetchone()[0] == 1
    s.close()


def _insert_and_raise(s: DatabaseSession) -> None:
    with s.cursor() as c:
        c.execute(
            "INSERT INTO athlete_memory_history (content, created_at) VALUES (?, ?)",
            ("will-be-rolled-back", "2024-01-01"),
        )
        raise RuntimeError("deliberate error")


def test_cursor_rolls_back_on_error() -> None:
    s = DatabaseSession(":memory:")
    s.init_schema()

    with pytest.raises(RuntimeError, match="deliberate error"):
        _insert_and_raise(s)

    with s.cursor() as c:
        c.execute("SELECT COUNT(*) FROM athlete_memory_history")
        assert c.fetchone()[0] == 0
    s.close()


def test_athlete_profile_seeded() -> None:
    """init_schema() seeds the athlete_profile row with id=1."""
    s = DatabaseSession(":memory:")
    s.init_schema()

    with s.cursor() as c:
        c.execute("SELECT id FROM athlete_profile WHERE id = 1")
        row = c.fetchone()
    assert row is not None
    s.close()


_INSERT = "INSERT INTO athlete_memory_history (content, created_at) VALUES (?, ?)"


def _contents(s: DatabaseSession) -> set[str]:
    with s.cursor() as c:
        c.execute("SELECT content FROM athlete_memory_history")
        return {row[0] for row in c.fetchall()}


def _transact_then_fail(
    s: DatabaseSession,
    in_transaction: threading.Event,
    write_done: threading.Event,
) -> None:
    with s.transaction():
        with s.cursor() as c:
            c.execute(_INSERT, ("transactional-write", "2024-01-01"))
        in_transaction.set()
        # Hand the writer its chance to interleave. Once the session is
        # thread-safe the writer blocks on the lock and this wait times out.
        write_done.wait(timeout=0.25)
        raise RuntimeError("deliberate error")


def test_bare_write_survives_another_threads_rollback() -> None:
    """A bare cursor() write must not join — and die with — another thread's transaction."""
    s = DatabaseSession(":memory:")
    s.init_schema()

    in_transaction = threading.Event()
    write_done = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        in_transaction.wait(timeout=5)
        try:
            with s.cursor() as c:
                c.execute(_INSERT, ("bare-write", "2024-01-01"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            write_done.set()

    thread = threading.Thread(target=writer)
    thread.start()

    with pytest.raises(RuntimeError, match="deliberate error"):
        _transact_then_fail(s, in_transaction, write_done)

    thread.join(timeout=5)
    assert not errors
    assert not thread.is_alive()
    assert _contents(s) == {"bare-write"}
    s.close()


def test_nested_transaction_commits_at_outermost_exit() -> None:
    """Nesting on one thread joins the outer transaction and commits once."""
    s = DatabaseSession(":memory:")
    s.init_schema()

    with s.transaction():
        with s.transaction(), s.cursor() as c:
            c.execute(_INSERT, ("inner", "2024-01-01"))
        with s.cursor() as c:
            c.execute(_INSERT, ("outer", "2024-01-01"))

    assert _contents(s) == {"inner", "outer"}
    s.close()


def _nested_insert_then_fail(s: DatabaseSession) -> None:
    with s.transaction():
        with s.transaction(), s.cursor() as c:
            c.execute(_INSERT, ("inner", "2024-01-01"))
        raise RuntimeError("boom")


def test_nested_transaction_rolls_back_from_outermost() -> None:
    """Leaving the inner context must not commit; the outer rollback wins."""
    s = DatabaseSession(":memory:")
    s.init_schema()

    with pytest.raises(RuntimeError, match="boom"):
        _nested_insert_then_fail(s)

    assert _contents(s) == set()
    s.close()


def test_default_path_management(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "_default_db_path", None)
    monkeypatch.delenv("ENDURY_DB_PATH", raising=False)
    assert "endury.db" in get_default_db_path()

    set_default_db_path("/tmp/custom.db")
    assert get_default_db_path() == "/tmp/custom.db"
