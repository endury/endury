"""Tests for SessionFinalizer (dict-message post-turn side effects)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from endury.coach.reply.finalizer import (
    SessionFinalizer,
    _asked_for_name,
    _extract_name,
)
from endury.domain.models.athlete import AthleteProfile
from tests.doubles import assistant_message, tool_call


def _user(text):
    return {"role": "user", "content": text}


def _assistant(text):
    return {"role": "assistant", "content": text}


def _tool(name, content="{}"):
    return {"role": "tool", "tool_call_id": "c1", "name": name, "content": content}


def _runtime():
    runtime = MagicMock()
    runtime.athlete_profile = None
    runtime.athlete_repo = MagicMock()
    runtime.memory_repo = MagicMock()
    runtime.refresh_athlete_profile = MagicMock()
    return runtime


# _asked_for_name / extract_response


def test_asked_for_name_from_last_ai():
    assert _asked_for_name([], _assistant("What should I call you?")) is True


def test_asked_for_name_scans_history():
    msgs = [_assistant("What's your name?"), _user("Sam")]
    assert _asked_for_name(msgs, None) is True


def test_asked_for_name_false_when_absent():
    assert _asked_for_name([_assistant("Nice run today.")], None) is False


def test_extract_response_returns_cleaned_text():
    msgs = [_user("hi"), _assistant("Your run was great.")]
    assert SessionFinalizer.extract_response(msgs) == "Your run was great."


def test_extract_response_skips_tool_call_messages():
    msgs = [assistant_message("", [tool_call("c1", "t", {})]), _assistant("Final.")]
    assert SessionFinalizer.extract_response(msgs) == "Final."


def test_extract_response_empty_without_assistant():
    assert SessionFinalizer.extract_response([_user("hi")]) == ""


def test_extract_response_skips_trailing_tool_call_message():
    # Last message is a tool-call assistant; the earlier plain assistant wins.
    msgs = [
        _assistant("earlier answer"),
        assistant_message("", [tool_call("c1", "t", {})]),
    ]
    assert SessionFinalizer.extract_response(msgs) == "earlier answer"


# _sync_profile_if_needed


def test_sync_profile_triggered_by_update_tool():
    runtime = _runtime()
    SessionFinalizer(runtime)._sync_profile_if_needed([_tool("update_athlete_profile")])
    runtime.refresh_athlete_profile.assert_called_once()


def test_sync_profile_skipped_without_update_tool():
    runtime = _runtime()
    SessionFinalizer(runtime)._sync_profile_if_needed([_tool("update_memory")])
    runtime.refresh_athlete_profile.assert_not_called()


def test_sync_profile_swallows_errors():
    runtime = _runtime()
    runtime.refresh_athlete_profile.side_effect = RuntimeError("sync failed")
    SessionFinalizer(runtime)._sync_profile_if_needed([_tool("update_athlete_profile")])
    runtime.refresh_athlete_profile.assert_called_once()


# _auto_save_name


def test_auto_save_name_success():
    runtime = _runtime()
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("Sam"), _assistant("What's your name?"))
    runtime.athlete_repo.update.assert_called_once_with({"name": "Sam"})


def test_auto_save_name_skips_when_name_known():
    runtime = _runtime()
    runtime.athlete_profile = AthleteProfile(name="Known")
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("New"), _assistant("What should I call you?"))
    runtime.athlete_repo.update.assert_not_called()


def test_auto_save_name_skips_invalid_inputs():
    runtime = _runtime()
    finalizer = SessionFinalizer(runtime)
    prompt = _assistant("What should I call you?")
    finalizer._auto_save_name([], _user("x" * 51), prompt)
    finalizer._auto_save_name([], _user("https://example.com"), prompt)
    finalizer._auto_save_name([], _user("Who am I?"), prompt)
    finalizer._auto_save_name([], None, prompt)
    runtime.athlete_repo.update.assert_not_called()


def test_auto_save_name_skips_when_not_asked():
    runtime = _runtime()
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("Sam"), _assistant("Nice work today."))
    runtime.athlete_repo.update.assert_not_called()


def test_auto_save_name_swallows_update_errors():
    runtime = _runtime()
    runtime.athlete_repo.update.side_effect = RuntimeError("db down")
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("Sam"), _assistant("What's your name?"))
    runtime.athlete_repo.update.assert_called_once()


# _extract_name


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Sam", "Sam"),
        ("  Sam  ", "Sam"),
        ("I'm Sam", "Sam"),
        ("i am Sam", "Sam"),
        ("It's Sam", "Sam"),
        ("its Sam", "Sam"),
        ("Call me Sam", "Sam"),
        ("My name is Sam", "Sam"),
        ("my name's Sam", "Sam"),
        ("This is Sam.", "Sam"),
        ("Sam!", "Sam"),
        ("Bond, James Bond", "Bond, James Bond"),
        ("O'Brien", "O'Brien"),
        ("im Sam", "Sam"),
    ],
)
def test_extract_name_accepts_plausible_names(reply, expected):
    assert _extract_name(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        "no",
        "No thanks",
        "nah",
        "nope",
        "why",
        "later",
        "maybe later",
        "I'd rather not say",
        "Runner42",
        "https://example.com",
        "see www.example.com",
        "kim@example.com",
        "I am going for a run this evening",
        "x" * 41,
        "...",
        "🤷",
        "I'm not telling you",
    ],
)
def test_extract_name_rejects_non_names(reply):
    assert _extract_name(reply) is None


def test_auto_save_name_strips_lead_in():
    runtime = _runtime()
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("I'm Sam"), _assistant("What's your name?"))
    runtime.athlete_repo.update.assert_called_once_with({"name": "Sam"})


def test_auto_save_name_saves_nothing_on_refusal():
    runtime = _runtime()
    finalizer = SessionFinalizer(runtime)
    finalizer._auto_save_name([], _user("no thanks"), _assistant("What's your name?"))
    runtime.athlete_repo.update.assert_not_called()
    runtime.refresh_athlete_profile.assert_not_called()


# finalize


def test_finalize_applies_profile_side_effects():
    runtime = _runtime()
    msgs = [
        _user("Sam"),
        _assistant("What's your name?"),
        _tool("update_athlete_profile"),
    ]
    SessionFinalizer(runtime).finalize(msgs)
    runtime.athlete_repo.update.assert_called_once_with({"name": "Sam"})
    runtime.refresh_athlete_profile.assert_called()


def test_finalize_no_longer_writes_athlete_memory():
    """Durable context is the coach's explicit update_memory call, not a guess."""
    runtime = _runtime()
    SessionFinalizer(runtime).finalize(
        [_user("I can only train on weekends"), _assistant("Noted.")]
    )
    runtime.memory_repo.set.assert_not_called()


def test_finalize_swallows_errors():
    """A malformed turn log must never take the finished response down with it."""
    runtime = _runtime()
    SessionFinalizer(runtime).finalize(["not a message dict"])
    runtime.athlete_repo.update.assert_not_called()
