"""SQLite session and bootstrap helpers.

Import from this package when wiring the application's concrete persistence
layer. It exposes the session object and a convenience helper for
initializing the local database.
"""

from endury.database.sqlite.session import DatabaseSession, get_default_db_path


def init_db() -> None:
    """Initialize the database schema (create tables if they don't exist)."""
    session = DatabaseSession(get_default_db_path())
    session.init_schema()
    session.close()


__all__ = [
    "DatabaseSession",
    "get_default_db_path",
    "init_db",
]
