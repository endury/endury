"""Tests for the _strip_echo_opening post-processing function."""

from endury.coach.reply.postprocess import _strip_echo_opening


def test_explicit_so_youve_been_phrase_stripped():
    """An opening that parrots the athlete with an echo phrase is cut."""
    content = (
        "So you've been sleeping badly and your legs feel heavy. "
        "Your RHR is up 4 beats — rest day, no arguments."
    )
    result = _strip_echo_opening(content)
    assert "Your RHR is up 4 beats" in result
    assert "So you've been sleeping badly" not in result


def test_plain_opening_unchanged():
    """An opening without an echo phrase is left alone."""
    content = (
        "Your body battery is at 28 and RHR is trending up. "
        "That tracks with the sleep scores I'm seeing. Rest day."
    )
    assert _strip_echo_opening(content) == content


def test_explicit_so_youre_saying_stripped():
    """Explicit 'So you're saying' phrase is stripped."""
    content = (
        "So you're saying your run went south at mile 8. "
        "Looking at the data, your glycogen was probably depleted — let's fix your fueling."
    )
    result = _strip_echo_opening(content)
    assert "So you're saying" not in result
    assert "glycogen" in result


def test_explicit_i_hear_you_stripped():
    """Explicit 'I hear you' phrase is stripped."""
    content = (
        "I hear you — work plus training is a lot. "
        "Your stress scores confirm it. Dial back intensity this week."
    )
    result = _strip_echo_opening(content)
    assert "I hear you" not in result
    assert "stress scores" in result


def test_empty_content_returns_empty():
    """Empty content returns empty."""
    assert _strip_echo_opening("") == ""


def test_short_emotional_ack_preserved():
    """Short emotional acknowledgments like 'That's rough.' are not echoes."""
    content = "That's rough. Your sleep score is 38 — rest day, non-negotiable."
    assert _strip_echo_opening(content) == content


def test_stripping_would_leave_too_short():
    """Don't strip if remainder would be <=20 chars."""
    content = "So you've been sleeping badly and your legs feel heavy. Got it."
    # "Got it." is only 6 chars — too short, so don't strip
    assert _strip_echo_opening(content) == content


def test_multiple_sentences_only_first_is_echo():
    """When only the first sentence echoes, strip only the opening."""
    content = (
        "It sounds like your swim was rough and you felt slow. "
        "Your CSS pace has actually improved 3 sec/100m this month. "
        "Don't read too much into one session."
    )
    result = _strip_echo_opening(content)
    assert "CSS pace" in result
    assert "It sounds like your swim was rough" not in result


def test_no_sentence_boundary_no_stripping():
    """Content without sentence-ending punctuation followed by space is unchanged."""
    # Single sentence with no period/space boundary — _split_first_sentence returns
    # (full text, "")
    content = "Your legs are tired and heavy today"
    assert _strip_echo_opening(content) == content


def test_direct_answer_sharing_the_questions_words_is_kept():
    """Seen live: the answer sentence to "What should target tsb for
    saturday?" was cut as an echo by word overlap, and the reply opened
    mid-thought. A direct answer repeats the question's words."""
    content = (
        "Aim for a TSB of +5 to +15 on Saturday. "
        "That's a fresh-but-not-flat state for a 37 km race with 1300 m of climbing."
    )
    assert _strip_echo_opening(content) == content


def test_a_yes_that_restates_the_question_is_kept():
    content = (
        "Yes, keep all three sessions and execute them easy. "
        "Each one is Zone 1-2 throughout, with no surges or threshold reps."
    )
    assert _strip_echo_opening(content) == content
