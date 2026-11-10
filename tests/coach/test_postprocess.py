"""Tests for endury.coach.reply.postprocess."""

import pytest

from endury.coach.reply.postprocess import (
    _enforce_one_question_rule,
    clean_coach_response,
    is_complete_reply,
    is_meaningful_response_text,
    strip_leaked_tool_calls,
)

# _enforce_one_question_rule


def test_one_question_no_questions():
    text = "Here is your training plan for the week."
    assert _enforce_one_question_rule(text) == text


def test_one_question_single_question_unchanged():
    text = "How did your run feel today?"
    assert _enforce_one_question_rule(text) == text


def test_one_question_two_questions_strips_second():
    text = "How did your run feel today? Also, what are your goals?"
    result = _enforce_one_question_rule(text)
    assert result.count("?") == 1
    assert "How did your run feel today?" in result


def test_one_question_and_pattern_stripped():
    text = "Ready to train? And how is your sleep?"
    result = _enforce_one_question_rule(text)
    assert result.count("?") == 1


def test_one_question_what_pattern_stripped():
    text = "How was the run? What are your goals for next week?"
    result = _enforce_one_question_rule(text)
    assert result.count("?") == 1


def test_one_question_how_pattern_stripped():
    text = "Great effort today? How did that feel?"
    result = _enforce_one_question_rule(text)
    assert result.count("?") == 1


def test_one_question_preserves_first_question():
    text = "Are you ready? Also, how are you feeling?"
    result = _enforce_one_question_rule(text)
    assert "Are you ready?" in result


def test_one_question_no_strip_when_second_not_matching_pattern():
    # "12345" matches no known continuation pattern, so nothing is stripped.
    text = "How are you? 12345?"
    result = _enforce_one_question_rule(text)
    assert "12345?" in result


def test_one_question_questions_far_apart_not_stripped():
    # Two questions more than 300 chars apart fall outside the tail window
    # the rule scans, so both survive.
    text = "How are you? " + "x" * 350 + " What now?"
    result = _enforce_one_question_rule(text)
    assert "How are you?" in result
    assert "What now?" in result


def test_one_question_preserves_replies_chips_line():
    # The mandatory %%REPLIES%% chips line trails the second
    # question and must survive the truncation, not be discarded with it.
    text = (
        "How does that sound? And how's your sleep?\n"
        "%%REPLIES%%Sounds good|Tell me more|I feel fine"
    )
    result = _enforce_one_question_rule(text)
    assert result.count("?") == 1
    assert "How does that sound?" in result
    assert "%%REPLIES%%Sounds good|Tell me more|I feel fine" in result


# clean_coach_response


def test_clean_coach_response_strips_fetches_bracket():
    content = "[fetches activity data]\nHere is your summary."
    result = clean_coach_response(content)
    assert "[fetches" not in result
    assert "Here is your summary." in result


def test_clean_coach_response_strips_checking_bracket():
    content = "[checking your health data]\nYou slept well."
    result = clean_coach_response(content)
    assert "[checking" not in result
    assert "You slept well." in result


def test_clean_coach_response_strips_calling_bracket():
    content = "[calling get_activities]\nFound 5 activities."
    result = clean_coach_response(content)
    assert "[calling" not in result
    assert "Found 5 activities." in result


def test_clean_coach_response_strips_an_imitated_send_stamp():
    """Replayed messages carry an app stamp; a reply that copies one gets it
    removed, since the athlete never sees stamps."""
    raw = "[Saturday 2026-09-26 17:02] Easy run tomorrow, 45 minutes."
    assert clean_coach_response(raw) == "Easy run tomorrow, 45 minutes."


def test_clean_coach_response_keeps_a_bracket_that_is_not_a_stamp():
    assert clean_coach_response("[Sat] easy").startswith("[Sat]")


def test_clean_coach_response_strips_a_copied_chart_note():
    # Seen live: the model copied the note its history carries for a chart.
    raw = (
        "I left out Saturday's run, since it was a race-length effort.\n\n"
        "[Chart shown under this reply: Training trail runs: climbing speed by climb]"
    )
    assert clean_coach_response(raw) == (
        "I left out Saturday's run, since it was a race-length effort."
    )


def test_clean_coach_response_keeps_a_chart_mentioned_in_prose():
    text = "The chart below shows your climbs [by date]."
    assert clean_coach_response(text) == text


def test_clean_coach_response_strips_leading_arrow():
    content = "\u2192 Here is your plan."
    result = clean_coach_response(content)
    assert not result.startswith("\u2192")
    assert "Here is your plan." in result


def test_clean_coach_response_strips_leading_dash():
    content = "- Here is your plan."
    result = clean_coach_response(content)
    assert not result.startswith("-")


def test_clean_coach_response_strips_leading_newlines():
    content = "\n\nHere is your plan."
    result = clean_coach_response(content)
    assert not result.startswith("\n")


def test_clean_coach_response_applies_one_question_rule():
    content = "How was your run? Also, what are your goals?"
    result = clean_coach_response(content)
    assert result.count("?") == 1


def test_clean_coach_response_preserves_replies_chips_with_one_question_rule():
    # A chips-bearing reply must survive postprocess intact.
    content = (
        "How does that sound? And how's your sleep?\n"
        "%%REPLIES%%Sounds good|Tell me more|I feel fine"
    )
    result = clean_coach_response(content)
    assert result.count("?") == 1
    assert "%%REPLIES%%Sounds good|Tell me more|I feel fine" in result


def test_clean_coach_response_plain_text_unchanged():
    content = "Your run was excellent."
    result = clean_coach_response(content)
    assert result == content


# is_meaningful_response_text


@pytest.mark.parametrize("text", ["", "   ", "%%", "_", "!", "...", "```", "-- --"])
def test_meaningless_response_shapes(text):
    assert not is_meaningful_response_text(text)


@pytest.mark.parametrize("text", ["Rest today.", "  ok  ", "%%REPLIES%%Yes|No"])
def test_meaningful_response_shapes(text):
    assert is_meaningful_response_text(text)


# is_complete_reply


@pytest.mark.parametrize(
    "text",
    [
        "Hold the easy days easy so you absorb the work.",
        "How are the legs feeling today?",
        "Nice one!",
        "Rest today. %%REPLIES%%Yes|No",
        "Your CTL sits at 62 (up from 58).",
        "Here is the week:\n- Monday: 40 min easy\n- Wednesday: 60 min trail",
        "| Rep | Target | Actual |\n| 1 | 4:10 | 4:12 |",
        "**Warm-up** — 15 min easy (Z2)\n**Total** — 35 min · ~42 TSS",
    ],
)
def test_complete_reply_shapes(text):
    assert is_complete_reply(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "3,056 m the week of July 27, 3,122 m the week of August 3, and 3,041 m the",
        "Two things stand out:",
        "Your fitness is up, your fatigue is down, and",
        "It comes down to this —",
        "Hold it steady this week %%REPLIES%%Ok",
    ],
)
def test_incomplete_reply_shapes(text):
    assert not is_complete_reply(text)


# strip_leaked_tool_calls — the model can print a tool call as literal text
# instead of invoking it; that text must never reach the athlete or land in
# conversation history.

_TOOL_PARAMETERS = {
    "update_memory": frozenset({"content"}),
    "pause_training": frozenset({"through_date", "reason", "confirm"}),
    "get_planned_sessions": frozenset({"days_ahead"}),
    "adjust_current_week": frozenset({"direction", "confirm"}),
}

_LEAKED_MEMORY_CALL = (
    "Let's log this pause officially and update your memory so we protect "
    "your health for the Ridge Marathon.\n\n"
    '```json\n{"content": "## Goals & races\\n- 2026-09-19: Ridge Marathon"}\n```'
)

_LEAK_FREE_PROSE = (
    "Let's log this pause officially and update your memory so we protect "
    "your health for the Ridge Marathon."
)


def test_strip_leaked_tool_calls_keeps_the_prose():
    assert strip_leaked_tool_calls(_LEAKED_MEMORY_CALL, _TOOL_PARAMETERS) == (
        _LEAK_FREE_PROSE
    )


def test_strip_leaked_tool_calls_cuts_a_json_object_naming_a_registered_tool():
    text = (
        "Pausing you through Sunday.\n"
        '```\n{"name": "pause_training", "arguments": {"through_date": '
        '"2026-09-21"}}\n```'
    )
    assert (
        strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == "Pausing you through Sunday."
    )


def test_strip_leaked_tool_calls_cuts_a_code_style_tool_call_in_a_fence():
    text = (
        "Noting that now.\n"
        '```tool_code\nprint(default_api.update_memory(content="- Sick"))\n```'
    )
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == "Noting that now."


def test_strip_leaked_tool_calls_drops_a_trailing_object():
    text = 'Recording the illness constraint.\n{"content": "- Sick"}'
    assert (
        strip_leaked_tool_calls(text, _TOOL_PARAMETERS)
        == "Recording the illness constraint."
    )


def test_strip_leaked_tool_calls_drops_every_block():
    text = (
        'First.\n```json\n{"content": "a"}\n```\nThen.\n'
        '```json\n{"through_date": "2026-09-21"}\n```\nDone.'
    )
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == "First.\n\nThen.\n\nDone."


def test_strip_leaked_tool_calls_returns_a_clean_reply_unchanged():
    text = "Rest up — nothing is saved yet."
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) is text


@pytest.mark.parametrize(
    "text",
    [
        'Here is what intervals.icu returned:\n```json\n{"ftp": 240}\n```',
        'The raw row reads:\n{"ftp": 240, "lthr": 168}',
        # Every tool's parameters are a superset of no keys at all; only a
        # non-empty subset names a call.
        "Nothing to send:\n```json\n{}\n```",
        "Empty:\n{}",
        "| Rep | Target | Actual |\n| 1 | 4:10 | 4:12 |\n| 2 | 4:10 | 4:08 |",
        "Keep the effort where {your legs} say easy, not where the watch does.",
        "**Warm-up** — 8 min (HR Z1, under 143 bpm)\n**Total** — 20 min",
        "Rest up.\n%%REPLIES%%Pause training|Show my week",
    ],
)
def test_strip_leaked_tool_calls_leaves_ordinary_shapes_alone(text):
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) is text


def test_strip_leaked_tool_calls_is_silent_without_the_tool_parameters():
    assert strip_leaked_tool_calls(_LEAKED_MEMORY_CALL, {}) is _LEAKED_MEMORY_CALL


def test_strip_leaked_tool_calls_can_empty_a_block_only_reply():
    stripped = strip_leaked_tool_calls(
        '```json\n{"content": "a"}\n```', _TOOL_PARAMETERS
    )
    assert stripped == ""


def test_strip_leaked_tool_calls_drops_a_trailing_object_under_the_chips_line():
    # The chips line ends the draft, so a trailing object is no longer
    # trailing — it must still be cut.
    text = (
        'Recording the illness constraint.\n{"content": "- Sick"}\n'
        "%%REPLIES%%Pause training|Show my week"
    )
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == (
        "Recording the illness constraint.\n%%REPLIES%%Pause training|Show my week"
    )


def test_strip_leaked_tool_calls_drops_an_unterminated_fence_under_the_chips_line():
    text = (
        'Recording it.\n```json\n{"content": "- Sick"}\n'
        "%%REPLIES%%Pause training|Show my week"
    )
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == (
        "Recording it.\n%%REPLIES%%Pause training|Show my week"
    )


def test_strip_leaked_tool_calls_drops_the_chips_line_of_a_block_only_reply():
    # Chips with no reply are not a reply — the empty-text fallbacks in
    # run_turn have to see nothing here.
    text = '```json\n{"content": "a"}\n```\n%%REPLIES%%Pause training'
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == ""


def test_strip_leaked_tool_calls_drops_a_fenced_and_a_trailing_leak_together():
    text = (
        'Pausing you.\n```json\n{"through_date": "2026-09-21"}\n```\n'
        'And noting it.\n{"content": "- Sick"}'
    )
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == (
        "Pausing you.\n\nAnd noting it."
    )


def test_strip_leaked_tool_calls_cuts_an_unterminated_fence_once():
    text = 'Recording it.\n```json\n{"content": "- Sick"}'
    assert strip_leaked_tool_calls(text, _TOOL_PARAMETERS) == "Recording it."
