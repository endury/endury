"""The last 50 conversation rows, mirrored to their own hidden intervals.icu NOTE.

Chat lives outside the revisioned store note (``coachie:store``) on
purpose: chatting is the most frequent action in the app, and re-uploading
the whole store on every message — and bumping its revision — would make
other devices hit "reload to continue" conflicts routinely.
This note, ``coachie:chat``, is saved independently of the store's
revision and never moves it.

Wire format mirrors :mod:`endury.store.note`: the event ``description`` is
a fence line, then ``base64(zlib(json))`` of ``{"schema", "saved_at",
"messages": [{"role", "content", "created_at"}, ...]}``, then a closing
fence. Fifty rows sit far below the size the store note needs chunking at,
so there is none here.
"""

from __future__ import annotations

import base64
import json
import logging
import zlib
from datetime import date
from typing import Any

from endury.common import clock
from endury.database.sqlite.session import DatabaseSession
from endury.intervals.client import IntervalsClient
from endury.store import note

logger = logging.getLogger(__name__)

#: The note's ``external_id``, with the ``coachie:`` prefix every id Endury
#: writes keeps. It must never match ``sync._is_chunk`` (``coachie:store``
#: or ``coachie:store:N`` only), so a chat save can never be mistaken for a
#: store chunk.
CHAT_ID = "coachie:chat"
#: The note's ``name`` on the calendar.
CHAT_NAME = "Endury chat (do not edit)"
#: How many of the newest rows the note keeps.
KEEP = 50
#: The fence line opening the message blob.
FENCE = "```coachie-chat"
#: The blob's ``schema`` field: the layout of the JSON, not of the rows.
SCHEMA = 1

Row = dict[str, str]


def _event(description: str) -> dict[str, Any]:
    """Build the chat note's event payload."""
    return {
        "external_id": CHAT_ID,
        "category": "NOTE",
        "name": CHAT_NAME,
        "description": description,
        "start_date_local": note.STORE_DATE,
        "hide_from_athlete": True,
    }


def _encode(messages: list[Row]) -> str:
    """Render the note body: fence, compressed JSON, closing fence."""
    state = {
        "schema": SCHEMA,
        "saved_at": clock.now().isoformat(),
        "messages": messages,
    }
    packed = zlib.compress(json.dumps(state, separators=(",", ":")).encode())
    return f"{FENCE}\n{base64.b64encode(packed).decode()}\n```"


def _decode(description: str) -> list[Row]:
    """Reverse :func:`_encode`; raises on anything malformed."""
    at = description.index(FENCE)
    start = description.index("\n", at) + 1
    blob = description[start : description.rindex("```")]
    state: dict[str, Any] = json.loads(zlib.decompress(base64.b64decode(blob)))
    return [dict(row) for row in state["messages"]]


def load(client: IntervalsClient) -> list[Row]:
    """Return the note's messages, oldest first; ``[]`` when there is none.

    A note that cannot be decoded is logged and treated as empty — a
    broken chat note must never block boot; the next :func:`save`
    overwrites it.

    Args:
        client: The athlete's intervals.icu client.
    """
    day = date.fromisoformat(note.STORE_DATE[:10])
    for event in client.list_events(day, day, category="NOTE"):
        if event.external_id == CHAT_ID:
            try:
                return _decode(event.description or "")
            except (LookupError, ValueError, zlib.error) as exc:
                logger.warning(
                    "Could not read the chat note (%s: %s)", type(exc).__name__, exc
                )
            break
    return []


def restore(session: DatabaseSession, messages: list[Row]) -> None:
    """Insert ``messages`` verbatim into ``conversation_history``, in order.

    Args:
        session: The database to write.
        messages: What :func:`load` returned.
    """
    if not messages:
        return
    with session.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO conversation_history (role, content, created_at) "
            "VALUES (?, ?, ?)",
            [(row["role"], row["content"], row["created_at"]) for row in messages],
        )


def last_id(session: DatabaseSession) -> int:
    """Return the highest ``conversation_history`` id, or 0 when it is empty.

    Local rows are only ever inserted — a reset is itself an inserted
    marker row — so a row with id greater than the last saved id means
    unsaved chat; no dirty triggers are needed.

    Args:
        session: The database to read.
    """
    with session.cursor() as cursor:
        row = cursor.execute(
            "SELECT COALESCE(MAX(id), 0) AS id FROM conversation_history"
        ).fetchone()
    return int(row["id"])


def merge(remote: list[Row], local: list[Row]) -> list[Row]:
    """Union ``remote`` and ``local``, then cut to what the note should keep.

    Args:
        remote: What the note currently holds, oldest first.
        local: This device's rows, oldest first (may repeat rows the note
            already holds, from an earlier save).

    Returns:
        Remote rows first, then local rows not already present by
        ``(created_at, role, content)``, sorted stably by ``created_at``,
        cut to start at the newest ``reset`` row (kept, so a stale device
        cannot resurrect what it marks wiped) and capped at the last
        :data:`KEEP`.
    """
    seen = {(row["created_at"], row["role"], row["content"]) for row in remote}
    merged = list(remote)
    for row in local:
        key = (row["created_at"], row["role"], row["content"])
        if key not in seen:
            seen.add(key)
            merged.append(row)
    merged.sort(key=lambda row: row["created_at"])
    last_reset = max(
        (index for index, row in enumerate(merged) if row["role"] == "reset"),
        default=None,
    )
    if last_reset is not None:
        merged = merged[last_reset:]
    return merged[-KEEP:]


def save(client: IntervalsClient, session: DatabaseSession) -> int:
    """Merge this device's unsaved rows into the note and upload it.

    Args:
        client: The athlete's intervals.icu client.
        session: The database to read local rows from.

    Returns:
        The ``conversation_history`` id this save covers — the new
        ``chat_saved`` mark.
    """
    mark = last_id(session)
    with session.cursor() as cursor:
        local = [
            dict(row)
            for row in cursor.execute(
                "SELECT role, content, created_at FROM conversation_history ORDER BY id"
            ).fetchall()
        ]
    merged = merge(load(client), local)
    client.bulk_upsert_events([_event(_encode(merged))])
    return mark
