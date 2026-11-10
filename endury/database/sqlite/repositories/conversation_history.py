"""SQLite repository for storing full chat transcripts.

This repository persists raw user and assistant messages so sessions can be
replayed, inspected, or rehydrated later. It is intentionally separate from
long-term memory storage, which keeps only selected summaries or facts.
"""

from endury.common import clock
from endury.database.sqlite.session import DatabaseSession
from endury.domain.models.memory import ConversationHistoryEntry


class ConversationHistoryRepository:
    """Repository for storing full conversation history."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def add(self, role: str, content: str) -> int | None:
        """Insert a conversation entry and return its row ID."""
        created_at = clock.now().isoformat()
        with self._session.cursor() as cursor:
            cursor.execute(
                "INSERT INTO conversation_history "
                "(role, content, created_at) "
                "VALUES (?, ?, ?)",
                (role, content, created_at),
            )
            return cursor.lastrowid

    def get_recent(self, limit: int) -> list[ConversationHistoryEntry]:
        """Return the most recent conversation entries in chronological order."""
        if limit <= 0:
            return []
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT id, role, content, created_at "
                "FROM conversation_history "
                "ORDER BY id DESC "
                "LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            ConversationHistoryEntry(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in reversed(rows)
        ]

    def get_by_date_range(
        self, start_date: str, end_date: str
    ) -> list[ConversationHistoryEntry]:
        """Return conversation entries within a date range."""
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT id, role, content, created_at "
                "FROM conversation_history "
                "WHERE created_at BETWEEN ? AND ? "
                "ORDER BY created_at ASC",
                (start_date, end_date),
            )
            rows = cursor.fetchall()
        return [
            ConversationHistoryEntry(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
