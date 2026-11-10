"""Propose a tidied athlete-memory document.

The "Tidy up" button in the memory panel asks the coach model for a cleaned
proposal of the current document. The model is held to the same
:data:`~endury.common.memory_notes.MEMORY_DOCUMENT_RULES` the coach's
``update_memory`` tool follows, plus cleanup-specific instructions, and its
reply is validated against the same shape before being handed back — this
module never stores the document; the proposal is only ever previewed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from endury.common.memory_notes import (
    MEMORY_CHAR_CAP,
    MEMORY_DOCUMENT_RULES,
    MEMORY_HEADINGS,
    REVIEW_DAYS,
    bullets_under,
    undated_constraints,
)

#: The heading whose bullets a tidy-up keeps verbatim.
_GOALS_HEADING = MEMORY_HEADINGS[0]

#: Cleanup-specific instructions layered on top of MEMORY_DOCUMENT_RULES.
CLEANUP_INSTRUCTIONS = (
    "Propose a tidied version of the athlete-memory document below. "
    "Today's date is given below. Copy every bullet under \"## Goals & "
    'races" exactly as it is: you cannot reach the race table, so moving '
    'a race there is the coach\'s job, never yours. Drop "Next check-in" '
    "lines whose date is before today. Only one plan is active at a time, "
    'so keep only the latest "committed", "replanned" or "deleted" plan '
    "note and drop the earlier ones, as well as Notes about plans a later "
    "note says were replaced, archived or deleted. Merge duplicate or "
    "overlapping Notes "
    "and Preferences into one bullet. Keep the athlete's own wording where "
    "it still holds. Never remove a constraint — merge or reword only — "
    'and give one with no review date "(review <today + '
    f'{REVIEW_DAYS} days>)". Keep every heading even when it has no '
    "bullets. Aim well under the cap.\n\n"
    "Output ONLY the document — no preamble, no commentary, no code "
    "fences."
)


class MemoryCleanupError(Exception):
    """The cleanup proposal could not be produced or failed validation."""


def build_cleanup_messages(document: str, today: date) -> list[dict[str, Any]]:
    """Build the chat messages asking the model to tidy the document.

    Args:
        document: The current memory document, verbatim.
        today: The date the model should treat as "now" for check-ins.

    Returns:
        A ``[system, user]`` message pair in OpenAI chat-completions format.
    """
    system = (
        f"{MEMORY_DOCUMENT_RULES}\n\n{CLEANUP_INSTRUCTIONS}\n\n"
        f"Today is {today.isoformat()}."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": document},
    ]


def validate_memory_document(content: str, original: str = "") -> str:
    """Validate a proposed document and return its cleaned text.

    Args:
        content: The model's raw reply.
        original: The document it tidies; every one of its "Goals & races"
            bullets must survive verbatim.

    Returns:
        *content* stripped of surrounding whitespace and any code fence.

    Raises:
        MemoryCleanupError: When the reply is empty, drops one of
            :data:`MEMORY_HEADINGS` (each must be a line of its own),
            reorders them, puts commentary before the first one, leaves a
            constraint without its review date, loses a "Goals & races"
            bullet of *original*, or exceeds :data:`MEMORY_CHAR_CAP`.
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text:
        raise MemoryCleanupError("The AI model returned an empty document.")

    lines = [line.strip() for line in text.split("\n")]
    positions: list[int] = []
    for heading in MEMORY_HEADINGS:
        if heading not in lines:
            raise MemoryCleanupError(f'The model dropped the "{heading}" heading.')
        positions.append(lines.index(heading))
    if positions != sorted(positions):
        raise MemoryCleanupError("The AI model reordered the document's headings.")
    if positions[0] != 0:
        raise MemoryCleanupError("The AI model added text before the document.")

    kept = set(bullets_under(text, _GOALS_HEADING))
    lost = [b for b in bullets_under(original, _GOALS_HEADING) if b not in kept]
    if lost:
        raise MemoryCleanupError(f"The AI model dropped or changed a goal: {lost[0]}")

    undated = undated_constraints(text)
    if undated:
        raise MemoryCleanupError(
            f"The AI model left a constraint without its review date: {undated[0]}"
        )

    if len(text) > MEMORY_CHAR_CAP:
        raise MemoryCleanupError(
            f"The proposal is {len(text)} characters, over the "
            f"{MEMORY_CHAR_CAP}-character cap."
        )
    return text


async def propose_memory_cleanup(llm: Any, document: str, today: date) -> str:
    """Ask the coach model to propose a tidied document.

    Args:
        llm: An object exposing an async ``complete(messages)`` method (an
            :class:`~endury.coach.llm.OpenRouterClient` or test double).
        document: The current memory document, verbatim.
        today: The date the model should treat as "now" for check-ins.

    Returns:
        The validated, cleaned proposal text.

    Raises:
        MemoryCleanupError: When the model call raises, or the reply is
            missing, empty, or fails :func:`validate_memory_document`.
    """
    try:
        reply = await llm.complete(build_cleanup_messages(document, today))
    except Exception as exc:
        raise MemoryCleanupError(f"The AI model call failed: {exc}") from exc

    content = reply.get("content")
    if not isinstance(content, str) or not content.strip():
        raise MemoryCleanupError("The AI model returned no content.")
    return validate_memory_document(content, document)
