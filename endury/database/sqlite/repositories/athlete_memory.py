"""Repository for the athlete-memory document.

One markdown document per athlete (``athlete_memory``, single row), rewritten
whole by the coach's ``update_memory`` tool or by the athlete in the UI. Every
write that changes something snapshots the version it replaces into
``athlete_memory_history`` (a rewrite identical to the stored document is a
no-op), so a bad rewrite is never a silent loss:
:meth:`AthleteMemoryRepository.history`
reads the snapshots back, the ``get_memory_history`` RPC serves them, and the
memory panel's "Restore previous" loads one into the editor.
"""

from endury.common import clock
from endury.common.memory_notes import MEMORY_CHAR_CAP
from endury.database.sqlite.session import DatabaseSession
from endury.domain.models.memory import AthleteMemory, AthleteMemoryVersion

CAP_MESSAGE = (
    f"The memory document must stay under {MEMORY_CHAR_CAP} characters. "
    "Condense it — merge duplicates, drop stale notes — and write it again."
)


class AthleteMemoryRepository:
    """Repository for the single athlete-memory document (row ``id = 1``)."""

    def __init__(self, session: DatabaseSession) -> None:
        self._session = session

    def get(self) -> AthleteMemory:
        """Return the current document and the timestamp of its last write.

        The row is seeded by ``init_schema``; a missing row reads as
        "nothing remembered" until the next boot re-seeds it.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT content, updated_at FROM athlete_memory WHERE id = 1"
            )
            row = cursor.fetchone()
        if row is None:
            return AthleteMemory(content="")
        return AthleteMemory(content=row["content"], updated_at=row["updated_at"])

    def set(self, content: str) -> bool:
        """Replace the document, snapshotting the version it supersedes.

        A rewrite identical to what is stored is a no-op: nothing is written
        and nothing is snapshotted, so a coach turn that "rewrites" the
        document unchanged cannot push a real earlier version out of the
        history window.

        Args:
            content: The full new document.

        Returns:
            True when the document changed, False when it already read
            exactly this.

        Raises:
            ValueError: If *content* exceeds :data:`MEMORY_CHAR_CAP`.
        """
        if len(content) > MEMORY_CHAR_CAP:
            raise ValueError(CAP_MESSAGE)

        now = clock.now().isoformat()
        with self._session.cursor() as cursor:
            cursor.execute("SELECT content FROM athlete_memory WHERE id = 1")
            row = cursor.fetchone()
            previous = row["content"] if row is not None else ""
            if previous == content:
                return False
            if previous.strip():
                cursor.execute(
                    "INSERT INTO athlete_memory_history (content, created_at) "
                    "VALUES (?, ?)",
                    (previous, now),
                )
            cursor.execute(
                "INSERT OR REPLACE INTO athlete_memory (id, content, updated_at) "
                "VALUES (1, ?, ?)",
                (content, now),
            )
        return True

    def history(self, limit: int = 10) -> list[AthleteMemoryVersion]:
        """Return superseded versions of the document, newest first.

        Args:
            limit: Maximum number of versions to return.
        """
        with self._session.cursor() as cursor:
            cursor.execute(
                "SELECT id, content, created_at FROM athlete_memory_history "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        return [
            AthleteMemoryVersion(
                id=row["id"], content=row["content"], created_at=row["created_at"]
            )
            for row in rows
        ]
