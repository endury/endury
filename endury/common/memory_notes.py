"""Conventions living under the athlete-memory document's "## Notes" heading.

The document is one markdown file the coach rewrites whole, but two entries
under its "## Notes" heading are written or read by the app itself:

* the dated bullet a plan commit or a plan delete appends, so what the
  athlete committed to, and that it ended, is remembered even when the coach
  never writes it down;
* ``- Next check-in YYYY-MM-DD: <why>``, a reminder the system context
  surfaces once that date has arrived.

Every bullet under "## Constraints" ends with ``(review YYYY-MM-DD)``. Once
that date passes the system context asks the coach to confirm the
constraint with the athlete and to stop advising from it until they do, so
an illness or an injury cannot sit in every prompt long after it healed.

Also the single home of the document's shape: the four required headings
(:data:`MEMORY_HEADINGS`), the character cap (:data:`MEMORY_CHAR_CAP`), and
the rules paragraph (:data:`MEMORY_DOCUMENT_RULES`) both the coach's
``update_memory`` tool and the "Tidy up" cleanup proposal are held to.

Pure text helpers over the document, shared by ``endury.coach.context`` and
the planning use cases; nothing here touches the database.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

#: The four required headings, in the order the document must keep them.
MEMORY_HEADINGS: tuple[str, ...] = (
    "## Goals & races",
    "## Constraints (injury, availability)",
    "## Preferences",
    "## Notes",
)

#: The heading both conventions live under.
NOTES_HEADING = MEMORY_HEADINGS[-1]

#: The heading whose bullets carry a review date.
CONSTRAINTS_HEADING = MEMORY_HEADINGS[1]

#: How far out an open-ended constraint is reviewed, and how long after the
#: document's last change an undated one falls due.
REVIEW_DAYS = 14

#: Hard ceiling on the document — it is injected whole into every system
#: prompt, so it must stay a page, not an archive.
MEMORY_CHAR_CAP = 6000

#: The rules governing the document's shape, shared verbatim by the coach's
#: ``update_memory`` tool description and the cleanup-proposal prompt.
MEMORY_DOCUMENT_RULES = (
    "Keep these headings, in this order: "
    + ", ".join(f'"{heading}"' for heading in MEMORY_HEADINGS)
    + ". One short bullet per fact. Every Constraints bullet ends with its "
    'review date, "(review YYYY-MM-DD)": the day after its end date, or '
    f'{REVIEW_DAYS} days out when it has none — so "- Sick, sore throat '
    '(review 2026-09-24)". A constraint leaves the document when the athlete '
    "says it no longer applies; asked at its review and still true, it gets "
    "a new review date. Date-stamp Notes entries "
    f"(YYYY-MM-DD). Keep the whole thing under {MEMORY_CHAR_CAP} characters "
    "— condense rather than let it sprawl.\n\n"
    "Races belong in the race table (upsert_race/list_races), not here — "
    '"Goals & races" is for the season\'s motivation and targets, not '
    "individual race bullets. If this document still lists a race the "
    "table lacks, add it there with upsert_race and drop the bullet here.\n\n"
    "A reminder is a Notes bullet of exactly this shape: "
    '"- Next check-in YYYY-MM-DD: <what to raise>" — so "remind me in '
    'March" becomes "- Next check-in 2027-03-01: pick a spring race". '
    "The app reads that line back to you on the first turn on or after "
    "that date."
)

_CHECK_IN_PATTERN = re.compile(
    r"^-\s*Next check-in\s+(\d{4}-\d{2}-\d{2})\s*:\s*(.+?)\s*$"
)

_REVIEW_PATTERN = re.compile(r"\(review\s+(\d{4}-\d{2}-\d{2})\)\s*$")

#: A bullet that only says there is nothing to record.
_EMPTY_BULLET = re.compile(r"^-\s*(none|n/a|nothing)?\.?\s*$", re.IGNORECASE)


def _notes_body_bounds(lines: list[str]) -> tuple[int, int] | None:
    """Return the half-open line bounds of the "## Notes" body.

    Args:
        lines: The document's lines.

    Returns:
        ``(start, end)`` line indices of the body under the heading, or
        ``None`` when the document has no "## Notes" heading.
    """
    for index, line in enumerate(lines):
        if line.strip() == NOTES_HEADING:
            end = index + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            return index + 1, end
    return None


def append_note(document: str, bullet: str, *, cap: int) -> str:
    """Return *document* with *bullet* appended under its "## Notes" heading.

    The heading is created at the end when the document has none. When the
    result would exceed *cap* characters the oldest bullets already under
    "## Notes" are dropped one at a time until it fits; the new bullet is
    never the one dropped.

    Args:
        document: The current memory document.
        bullet: The bullet line to append, e.g. ``"- 2026-09-14: ..."``.
        cap: Maximum length of the returned document.

    Returns:
        The new document, or the unchanged one when *bullet* cannot be made
        to fit even after dropping every older bullet.
    """
    lines = document.splitlines()
    bounds = _notes_body_bounds(lines)
    if bounds is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(NOTES_HEADING)
        start = end = len(lines)
    else:
        start, end = bounds
        while end > start and not lines[end - 1].strip():
            end -= 1
    lines.insert(end, bullet)

    index = start
    while len("\n".join(lines)) > cap and index < end:
        if lines[index].lstrip().startswith("-"):
            del lines[index]
            end -= 1
        else:
            index += 1
    text = "\n".join(lines)
    return text if len(text) <= cap else document


def due_check_ins(document: str, today: date) -> list[tuple[date, str]]:
    """Return the check-in bullets whose date has arrived, in document order.

    Args:
        document: The current memory document.
        today: The date to compare the bullets against.

    Returns:
        ``(date, why)`` pairs for every ``- Next check-in <date>: <why>``
        bullet dated on or before *today*.
    """
    due: list[tuple[date, str]] = []
    for line in document.splitlines():
        match = _CHECK_IN_PATTERN.match(line.strip())
        if match is None:
            continue
        try:
            when = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if when <= today:
            due.append((when, match.group(2)))
    return due


def bullets_under(document: str, heading: str) -> list[str]:
    """Return the bullets under *heading*, placeholders like "- None" left out.

    Args:
        document: The current memory document.
        heading: One of :data:`MEMORY_HEADINGS`.
    """
    bullets: list[str] = []
    inside = False
    for line in document.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            inside = stripped == heading
        elif inside and stripped.startswith("-") and not _EMPTY_BULLET.match(stripped):
            bullets.append(stripped)
    return bullets


def constraints(document: str) -> list[str]:
    """Return the bullets under "## Constraints".

    Args:
        document: The current memory document.
    """
    return bullets_under(document, CONSTRAINTS_HEADING)


def review_date(bullet: str) -> date | None:
    """Return a constraint bullet's ``(review YYYY-MM-DD)`` date, or None."""
    match = _REVIEW_PATTERN.search(bullet)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def undated_constraints(document: str) -> list[str]:
    """Return the constraint bullets that carry no review date.

    Args:
        document: The current memory document.
    """
    return [bullet for bullet in constraints(document) if review_date(bullet) is None]


def due_reviews(document: str, today: date, written: date | None) -> list[str]:
    """Return the constraint bullets whose review has come.

    A dated bullet is due on its review date. An undated one, from a
    document written before review dates existed, is due
    :data:`REVIEW_DAYS` after the document last changed, or at once when
    that is unknown.

    Args:
        document: The current memory document.
        today: The date to compare against.
        written: The day the document last changed, if known.
    """
    fallback = written + timedelta(days=REVIEW_DAYS) if written else today
    return [
        bullet
        for bullet in constraints(document)
        if (review_date(bullet) or fallback) <= today
    ]
