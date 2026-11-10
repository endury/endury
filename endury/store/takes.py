"""The coach's saved takes and briefs, in their own hidden NOTE.

A take is the coach's answer to an activity's Quick take or Full debrief
button; a brief is the answer to a coming session's or race's Quick brief or
Full brief button, kept under its subject handle (``session:412``,
``race:2026-10-24``) in the ``activity_id`` field. Either is kept so the
sheet shows it again without another AI model call. Like the chat
(:mod:`endury.store.chat`), takes live outside the revisioned store note:
saving one never moves the store's revision.

Wire format mirrors :mod:`endury.store.chat`: the event ``description`` is a
fence line, then ``base64(zlib(json))`` of ``{"schema", "saved_at",
"takes": [{"activity_id", "depth", "text", "created_at", "generated_by"[,
"kind"]}, ...]}``,
then a closing fence. Only a brief carries ``kind``; a row without it is a
take. :data:`KEEP` full debriefs compress to a few hundred thousand
characters, below the size the store note needs chunking at.
"""

from __future__ import annotations

import base64
import json
import logging
import zlib
from datetime import date
from typing import Any

from endury.common import clock
from endury.intervals.client import IntervalsClient
from endury.store import note

logger = logging.getLogger(__name__)

#: The note's ``external_id``. Like ``coachie:chat`` it must never match
#: ``sync._is_chunk``, so a takes save can never be mistaken for a store chunk.
TAKES_ID = "coachie:takes"
#: The note's ``name`` on the calendar.
TAKES_NAME = "Endury session takes (do not edit)"
#: The two depths an activity can have a take at.
DEPTHS = ("quick", "full")
#: What a row can be: a take on a done activity or a brief on a coming subject.
KINDS = ("take", "brief")
#: How many of the newest takes the note keeps: at least 100 activities.
KEEP = 200
#: The fence line opening the takes blob.
FENCE = "```coachie-takes"
#: The blob's ``schema`` field: the layout of the JSON, not of the rows.
SCHEMA = 1

Row = dict[str, str]


def _event(description: str) -> dict[str, Any]:
    """Build the takes note's event payload."""
    return {
        "external_id": TAKES_ID,
        "category": "NOTE",
        "name": TAKES_NAME,
        "description": description,
        "start_date_local": note.STORE_DATE,
        "hide_from_athlete": True,
    }


def _encode(takes: list[Row]) -> str:
    """Render the note body: fence, compressed JSON, closing fence."""
    state = {"schema": SCHEMA, "saved_at": clock.now().isoformat(), "takes": takes}
    packed = zlib.compress(json.dumps(state, separators=(",", ":")).encode())
    return f"{FENCE}\n{base64.b64encode(packed).decode()}\n```"


def _decode(description: str) -> list[Row]:
    """Reverse :func:`_encode`; raises on anything malformed."""
    at = description.index(FENCE)
    start = description.index("\n", at) + 1
    blob = description[start : description.rindex("```")]
    state: dict[str, Any] = json.loads(zlib.decompress(base64.b64decode(blob)))
    return [dict(row) for row in state["takes"]]


def load(client: IntervalsClient) -> list[Row]:
    """Return the note's takes, oldest first; ``[]`` when there is none.

    A note that cannot be decoded is logged and treated as empty, like a
    broken chat note; the next :func:`save` overwrites it.

    Args:
        client: The athlete's intervals.icu client.
    """
    day = date.fromisoformat(note.STORE_DATE[:10])
    for event in client.list_events(day, day, category="NOTE"):
        if event.external_id == TAKES_ID:
            try:
                return _decode(event.description or "")
            except (LookupError, ValueError, zlib.error) as exc:
                logger.warning(
                    "Could not read the takes note (%s: %s)", type(exc).__name__, exc
                )
            break
    return []


def _key(row: Row) -> tuple[str, str, str]:
    """The identity of a row: its kind, subject and depth."""
    return (row.get("kind", "take"), row["activity_id"], row["depth"])


def put(
    takes: list[Row], activity_id: str, depth: str, text: str, kind: str = "take"
) -> list[Row]:
    """Return ``takes`` with this subject's row at ``depth`` replaced by ``text``.

    The new take goes last whatever the clock says: a redo on this device
    always replaces the take it was asked for.

    Args:
        takes: The takes so far, oldest first.
        activity_id: The intervals.icu activity id, or a brief's subject handle.
        depth: One of :data:`DEPTHS`.
        text: The coach's reply.
        kind: One of :data:`KINDS`; only ``brief`` is written to the row.
    """
    key = (kind, activity_id, depth)
    kept = [row for row in takes if _key(row) != key]
    take = {
        "activity_id": activity_id,
        "depth": depth,
        "text": text,
        "created_at": clock.now().isoformat(),
        "generated_by": "ai",
    }
    if kind == "brief":
        take["kind"] = kind
    return [*kept, take]


def merge(remote: list[Row], local: list[Row]) -> list[Row]:
    """Union ``remote`` and ``local``, one row per kind, subject and depth.

    Args:
        remote: What the note currently holds, oldest first.
        local: This device's takes, oldest first.

    Returns:
        The newer row of each ``(kind, activity_id, depth)``, oldest first,
        capped at the last :data:`KEEP`.
    """
    newest: dict[tuple[str, str, str], Row] = {}
    for row in [*remote, *local]:
        key = _key(row)
        if key not in newest or row["created_at"] >= newest[key]["created_at"]:
            newest[key] = row
    merged = sorted(newest.values(), key=lambda row: row["created_at"])
    return merged[-KEEP:]


def save(client: IntervalsClient, takes: list[Row]) -> list[Row]:
    """Merge this device's takes into the note and upload it.

    Args:
        client: The athlete's intervals.icu client.
        takes: This device's takes, oldest first.

    Returns:
        What the note now holds, other devices' takes included.
    """
    merged = merge(load(client), takes)
    client.bulk_upsert_events([_event(_encode(merged))])
    return merged
