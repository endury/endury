"""The note's wire format: the memory markdown above one fenced, compressed blob.

A note's ``description`` is the athlete-memory document — canonical for
``athlete_memory.content``, so the athlete can read it on intervals.icu —
followed by the LAST ```` ```coachie-state ```` fence, which holds
``base64(zlib(json))`` of the state (``{schema, rev, saved_at,
db_user_version, tables}``). Splitting on the last fence lets the memory
itself quote one.

A body over :data:`MAX_CHUNK_CHARS` is sliced into chunks stored as
``coachie:store``, ``coachie:store:2`` … ``:n``; the first chunk's fence
line records the count (```` ```coachie-state chunks=3 ````), and the
chunks concatenate back into the single-note body.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

#: The note's ``external_id``; chunks 2..n append ``:n`` (:func:`chunk_id`).
STORE_ID = "coachie:store"
#: Where ``save`` writes the note (its ``start_date_local``).
STORE_DATE = "2099-12-31T00:00:00"
#: Every date ``load`` searches: the preferred one first, then the date the
#: live API was already proven to accept.
STORE_DATE_CANDIDATES = (STORE_DATE, "2030-12-31T00:00:00")
#: The note's ``name`` on the calendar.
STORE_NAME = "Endury data (do not edit)"
#: The state's ``schema`` field: the layout of the JSON, not of the tables.
SCHEMA = 1
#: The fence line opening the state blob.
FENCE = "```coachie-state"
#: Longest chunk description: the live API round-trips 1,000,000 chars.
MAX_CHUNK_CHARS = 900_000

_CHUNKS_KEY = " chunks="


def chunk_id(index: int) -> str:
    """Return the ``external_id`` of chunk ``index`` (1-based).

    Args:
        index: The chunk's position, starting at 1.
    """
    return STORE_ID if index == 1 else f"{STORE_ID}:{index}"


def encode(memory: str, state: dict[str, Any]) -> list[str]:
    """Render the note and slice it into chunk descriptions, first chunk first.

    Args:
        memory: The athlete-memory markdown, written above the fence.
        state: The JSON-serializable state, compressed into the fence.
    """
    packed = zlib.compress(json.dumps(state, separators=(",", ":")).encode())
    body = f"{memory}\n{FENCE}\n{base64.b64encode(packed).decode()}\n```"
    chunks = [
        body[start : start + MAX_CHUNK_CHARS]
        for start in range(0, len(body), MAX_CHUNK_CHARS)
    ]
    if len(chunks) > 1:
        # The memory is capped far below one chunk, so its last fence — the
        # real one, whatever the memory quotes — sits in the first chunk.
        at = chunks[0].rindex(FENCE) + len(FENCE)
        chunks[0] = f"{chunks[0][:at]}{_CHUNKS_KEY}{len(chunks)}{chunks[0][at:]}"
    return chunks


def chunk_count(first_chunk: str) -> int:
    """Return how many chunks the note's first chunk says it spans.

    Args:
        first_chunk: The description stored under ``coachie:store``.
    """
    at = first_chunk.rindex(FENCE)
    suffix = first_chunk[at + len(FENCE) : first_chunk.index("\n", at)]
    return int(suffix.removeprefix(_CHUNKS_KEY)) if suffix else 1


def decode(body: str) -> tuple[str, dict[str, Any]]:
    """Split a reassembled note into its memory markdown and its state.

    Args:
        body: Every chunk's description, concatenated in order.
    """
    at = body.rindex(FENCE)
    start = body.index("\n", at) + 1
    blob = body[start : body.rindex("```")]
    state: dict[str, Any] = json.loads(zlib.decompress(base64.b64decode(blob)))
    return body[:at].removesuffix("\n"), state
