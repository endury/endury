"""Post-processing helpers for coach response text."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)


# Phrases that signal the coach is echoing the athlete
_ECHO_PHRASES = [
    r"so you(?:'re| are) saying",
    r"i hear you",
    r"it sounds like",
    r"you mentioned that",
    r"so you(?:'re| are) dealing with",
    r"so you(?:'ve| have) been",
    r"sounds like you(?:'re| are)",
    r"from what you(?:'re| are) telling me",
]
_ECHO_RE = re.compile("|".join(_ECHO_PHRASES), re.IGNORECASE)

# The %%REPLIES%% quick-reply marker (coach.js renders it as chips) trails
# the reply when the coach offers one — truncation below must not drop it.
_REPLIES_MARKER_RE = re.compile(r"%%REPLIES%%[^\n\r]*")


def _split_first_sentence(text: str) -> tuple[str, str]:
    """Split *text* after the first sentence boundary.

    Returns:
        A (first_sentence, remainder) tuple. If no sentence boundary is found,
        first_sentence is the full text and remainder is empty.
    """
    match = re.search(r"[.!?]\s+", text.strip())
    if match:
        split_pos = match.end()
        return text[:split_pos].strip(), text[split_pos:].strip()
    return text.strip(), ""


def _strip_echo_opening(content: str) -> str:
    """Remove an opening sentence that parrots the athlete ("So you're saying…").

    Only the explicit echo phrases count. A direct answer repeats the
    question's words ("Aim for a TSB of +5 on Saturday" to "what TSB for
    Saturday?"), so word overlap is not an echo and never cuts a reply.

    Args:
        content: The coach's draft response text.
    """
    if not content:
        return content
    first_sentence, remainder = _split_first_sentence(content)
    if _ECHO_RE.search(first_sentence) and remainder and len(remainder) > 20:
        logger.debug("ANTI-ECHO: stripped explicit echo phrase from opening")
        return remainder
    return content


def _enforce_one_question_rule(content: str) -> str:
    """Strip a trailing second question from the coach response.

    Scans the last 300 characters for two question marks. If the text between
    them starts with a follow-up opener (And, Also, What, How, ...), the
    second question is cut. This keeps the coach focused on one question per
    turn, preventing the athlete from being overwhelmed.
    """
    question_positions = [m.start() for m in re.finditer(r"\?", content)]
    if len(question_positions) < 2:
        return content

    tail_start = max(0, len(content) - 300)
    tail = content[tail_start:]
    tail_questions = [m.start() for m in re.finditer(r"\?", tail)]

    if len(tail_questions) < 2:
        return content

    first_q_in_tail = tail_questions[-2]
    second_q_in_tail = tail_questions[-1]
    between = tail[first_q_in_tail + 1 : second_q_in_tail + 1].strip()

    second_question_patterns = [
        r"^\s*And\s",
        r"^\s*Also[,\s]",
        r"^\s*Any\s",
        r"^\s*What\s",
        r"^\s*How\s",
        r"^\s*Are\s",
        r"^\s*Do\s",
        r"^\s*Is\s",
        r"^\s*Have\s",
        r"^\s*Would\s",
        r"^\s*Could\s",
        r"^\s*Or\s",
    ]

    for pattern in second_question_patterns:
        if re.match(pattern, between, re.IGNORECASE):
            cut_pos = tail_start + first_q_in_tail + 1
            removed = content[cut_pos:]
            content = content[:cut_pos].rstrip()
            # The truncation above can carry off a trailing %%REPLIES%%
            # chips line along with the second question — reattach it if it
            # was there.
            chips_match = _REPLIES_MARKER_RE.search(removed)
            if chips_match:
                content = f"{content}\n{chips_match.group(0)}"
            logger.debug("ONE QUESTION RULE: stripped second question from response")
            return content

    return content


_SYMBOL_ONLY_RESPONSE_RE = re.compile(r"^[\s\W_]+$")
_KNOWN_EMPTY_MARKERS = frozenset({"%%", "_", "!", "-", "...", "```"})


def is_meaningful_response_text(text: str) -> bool:
    """Return True when *text* is useful to show as a final assistant response."""
    stripped = text.strip()
    if not stripped:
        return False
    if stripped in _KNOWN_EMPTY_MARKERS:
        return False
    return not _SYMBOL_ONLY_RESPONSE_RE.match(stripped)


# A stream that dies mid-sentence leaves its last line hanging on a word, a
# number, a comma or a dash; a finished reply ends in terminal punctuation.
_DANGLING_TAIL_RE = re.compile(r"[\w,;:\-\u2013\u2014]$")
# Schedule bullets, table rows and bold-led workout lines end without
# terminal punctuation by design.
_STRUCTURAL_LINE_RE = re.compile(r"^\s*(?:[-*]\s|\d+\.\s|\||\*\*)")


def is_complete_reply(text: str) -> bool:
    """Return True when *text* ends where a finished reply ends.

    The chips marker is ignored: it trails a reply that was already whole.
    """
    body = _REPLIES_MARKER_RE.sub("", text).rstrip()
    if not body:
        return False
    last_line = body.splitlines()[-1]
    if _STRUCTURAL_LINE_RE.match(last_line):
        return True
    return not _DANGLING_TAIL_RE.search(last_line)


# Leaked tool calls: a tool call the model typed out as prose instead of
# invoking it. Two shapes carry such a leak — a fenced code block, and a bare
# JSON object the draft ends on — and either is only a leak when its content
# maps onto a REGISTERED tool: by argument names, by naming the tool, or by a
# code-style call of it. A markdown table, ordinary prose with braces, or a
# workout outline are all left alone.
_TOOL_NAME_KEYS = ("name", "tool", "function")
_FENCED_BLOCK_PATTERN = re.compile(r"```[^\n`]*\n(.*?)(?:```|\Z)", re.DOTALL)
_TRAILING_JSON_PATTERN = re.compile(r"^[ \t]*\{.*\}\s*\Z", re.DOTALL | re.MULTILINE)
# The chips line together with the newline before it: a trailing object is
# only trailing once the whole line is out of the way, and the seam is put
# back newline included.
_REPLIES_LINE_RE = re.compile(r"\n?%%REPLIES%%[^\n\r]*")


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    """Return *raw* parsed as a JSON object, else ``None``."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_tool_call_json(raw: str, tool_parameters: Mapping[str, frozenset[str]]) -> bool:
    """Return whether one JSON snippet is a registered tool's call.

    Args:
        raw: The snippet's text — a fenced block's body or a trailing object.
        tool_parameters: Every registered tool's name mapped to its parameter
            names.

    Returns:
        True when the snippet parses as a JSON object that either names a
        registered tool or whose keys are a non-empty subset of one tool's
        parameters.
    """
    parsed = _parse_json_object(raw)
    if parsed is None:
        return False
    named = (parsed.get(key) for key in _TOOL_NAME_KEYS)
    if any(isinstance(value, str) and value in tool_parameters for value in named):
        return True
    keys = frozenset(parsed)
    return bool(keys) and any(keys <= params for params in tool_parameters.values())


def _calls_tool_in_code(
    block: str, tool_parameters: Mapping[str, frozenset[str]]
) -> bool:
    """Return whether a fenced block writes a registered tool as a call.

    The other leak shape the model reaches for is code rather than JSON —
    ``print(default_api.update_memory(...))`` inside a ``tool_code`` fence.

    Args:
        block: The fenced block's body.
        tool_parameters: Every registered tool's name mapped to its parameter
            names.

    Returns:
        True when the block calls one of the registered tool names.
    """
    pattern = r"\b(?:" + "|".join(re.escape(n) for n in tool_parameters) + r")\s*\("
    return bool(re.search(pattern, block))


def _leaked_tool_call_spans(
    text: str, tool_parameters: Mapping[str, frozenset[str]]
) -> list[tuple[int, int]]:
    """Return the span of every tool call the draft wrote out as text.

    Fenced blocks win where the two shapes overlap: an unterminated fence
    runs to the end of the draft, so a trailing object inside one would
    otherwise be cut twice.

    Args:
        text: The assistant's draft reply, the %%REPLIES%% chips line already
            removed — a trailing object is only trailing without it.
        tool_parameters: Every registered tool's name mapped to its parameter
            names; no tools means nothing can be recognised as a leak.

    Returns:
        The non-overlapping (start, end) offsets of the leaked blocks, in
        draft order.
    """
    if not tool_parameters:
        return []
    spans = [
        match.span()
        for match in _FENCED_BLOCK_PATTERN.finditer(text)
        if _is_tool_call_json(match.group(1), tool_parameters)
        or _calls_tool_in_code(match.group(1), tool_parameters)
    ]
    # Only past the last fenced block: an object inside an unterminated fence
    # is already covered by that fence and must not be cut twice.
    trailing = _TRAILING_JSON_PATTERN.search(text, spans[-1][1] if spans else 0)
    if trailing is not None and _is_tool_call_json(trailing.group(0), tool_parameters):
        spans.append(trailing.span())
    return spans


def strip_leaked_tool_calls(
    text: str, tool_parameters: Mapping[str, frozenset[str]]
) -> str:
    """Return *text* with every leaked tool-call block cut out.

    Raw tool-call JSON must never reach the athlete or the conversation
    history. The %%REPLIES%% chips line is set aside first and put back
    after — a leak the chips line follows is still a leak, but chips with no
    reply left are not a reply.

    Args:
        text: A candidate reply.
        tool_parameters: Every registered tool's name mapped to its parameter
            names.

    Returns:
        The reply without the leaked blocks, trimmed at the seam; *text*
        unchanged when nothing leaked.
    """
    marker = _REPLIES_LINE_RE.search(text)
    prose = _REPLIES_LINE_RE.sub("", text)
    spans = _leaked_tool_call_spans(prose, tool_parameters)
    if not spans:
        return text
    kept: list[str] = []
    cursor = 0
    for start, end in spans:
        kept.append(prose[cursor:start])
        cursor = end
    kept.append(prose[cursor:])
    stripped = "".join(kept).strip()
    if not stripped or marker is None:
        return stripped
    return stripped + marker.group(0)


def clean_coach_response(content: str) -> str:
    """Clean the coach's raw LLM output for delivery to the athlete.

    Steps:
        1. Strip tool transparency brackets ([fetching...], [calling...]).
        2. Strip a copied ``[weekday date time]`` send stamp.
        3. Strip a copied ``[Chart shown under this reply: …]`` note.
        4. Strip echo openings that parrot the athlete's message back.
        5. Enforce the one-question-per-turn rule.

    Args:
        content: The coach's raw LLM response text.

    Returns:
        Cleaned response text ready to send to the athlete.
    """
    content = re.sub(
        r"\[(?:fetches?|checking|looking at|calling|using"
        r"|retrieving|pulling)[^\]]*\]\s*\n?",
        "",
        content,
        flags=re.IGNORECASE,
    )
    content = re.sub(
        r"\[call(?:ing)?\s+\w+[^\]]*\]\s*\n?", "", content, flags=re.IGNORECASE
    )
    # Replayed messages carry an app-added send stamp; one copied into the
    # reply is noise the athlete never sees anywhere else.
    content = re.sub(
        r"^\s*\[[A-Z][a-z]+ \d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*", "", content
    )
    # A replayed reply names its charts in such a note (charts_for_model);
    # the model copied it into a new reply, where the athlete read it under
    # the chart it named.
    content = re.sub(
        r"^[ \t]*\[Chart shown under this reply:[^\]\n]*\][ \t]*\n?",
        "",
        content,
        flags=re.IGNORECASE | re.MULTILINE,
    ).rstrip()
    content = re.sub(r"^[→\-►]\s*", "", content.lstrip("\n").lstrip())
    content = content.lstrip("\n").lstrip()
    content = _strip_echo_opening(content)
    content = _enforce_one_question_rule(content)
    return content
