"""Tests for the golden conversation replay infrastructure.

Tests both the replay harness itself and runs the golden conversation
fixtures through the coach graph.
"""

from pathlib import Path

import pytest

from tests.golden.replay import (
    GoldenConversation,
    GoldenSetup,
    GoldenTurn,
    discover_golden_conversations,
    replay_conversation,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "golden_conversations"


# GoldenConversation parsing tests


class TestGoldenConversationParsing:
    """Tests for JSONL parsing of golden conversations."""

    def test_parse_greeting_conversation(self):
        """Should parse the greeting golden conversation."""
        conv = GoldenConversation.from_jsonl(
            FIXTURES_DIR / "greeting_new_athlete.jsonl"
        )
        assert conv.name == "greeting_new_athlete"
        assert conv.setup.athlete["name"] == ""
        assert len(conv.turns) == 2

    def test_parse_profile_lookup_conversation(self):
        """Should parse conversation with tool call expectations."""
        conv = GoldenConversation.from_jsonl(FIXTURES_DIR / "profile_lookup.jsonl")
        assert conv.setup.athlete["name"] == "Alex"
        assert len(conv.turns) == 2
        assistant_turn = conv.turns[1]
        assert "get_athlete_profile" in assistant_turn.expect_tool_calls

    def test_parse_health_alert_conversation(self):
        """Should parse conversation with health alerts."""
        conv = GoldenConversation.from_jsonl(
            FIXTURES_DIR / "health_alert_response.jsonl"
        )
        assert conv.setup.health_summary is not None
        assert len(conv.setup.alerts) == 2
        assert "Sleep score" in conv.setup.alerts[0]

    def test_parse_german_conversation(self):
        """Should parse conversation with language setting."""
        conv = GoldenConversation.from_jsonl(FIXTURES_DIR / "german_language.jsonl")
        assert conv.setup.athlete["language"] == "de"

    def test_discover_all_conversations(self):
        """discover_golden_conversations should find all JSONL files."""
        conversations = discover_golden_conversations(FIXTURES_DIR)
        assert len(conversations) >= 4

    def test_discover_empty_directory(self, tmp_path):
        """Should return empty list for directory with no JSONL files."""
        conversations = discover_golden_conversations(tmp_path)
        assert conversations == []

    def test_discover_nonexistent_directory(self):
        """Should return empty list for nonexistent directory."""
        conversations = discover_golden_conversations(Path("/tmp/nonexistent_golden"))
        assert conversations == []


# GoldenSetup and GoldenTurn tests


class TestGoldenDataclasses:
    """Tests for golden conversation data structures."""

    def test_golden_setup_defaults(self):
        """GoldenSetup should have sensible defaults."""
        setup = GoldenSetup()
        assert setup.athlete == {}
        assert setup.health_summary is None
        assert setup.alerts == []
        assert setup.new_activities == []

    def test_golden_turn_defaults(self):
        """GoldenTurn should default to empty assertion lists."""
        turn = GoldenTurn(role="user", content="Hello")
        assert turn.expect_tool_calls == []
        assert turn.expect_contains == []
        assert turn.expect_not_contains == []


# Replay harness tests


class TestReplayHarness:
    """Tests for the replay_conversation function."""

    def test_replay_simple_greeting(self, coach_tools, mock_memory_repo):
        """Replay a simple greeting conversation."""
        conv = GoldenConversation.from_jsonl(
            FIXTURES_DIR / "greeting_new_athlete.jsonl"
        )
        results = replay_conversation(conv, coach_tools, mock_memory_repo)
        assert len(results) == 1
        assert results[0].user_content == "Hi, I'm new here!"
        assert results[0].errors == []

    def test_replay_with_tool_calls(self, coach_tools, mock_memory_repo):
        """Replay a conversation that expects tool calls."""
        conv = GoldenConversation.from_jsonl(FIXTURES_DIR / "profile_lookup.jsonl")
        results = replay_conversation(conv, coach_tools, mock_memory_repo)
        assert len(results) == 1
        assert "get_athlete_profile" in results[0].tool_calls_made
        assert results[0].errors == []

    def test_replay_with_health_alerts(self, coach_tools, mock_memory_repo):
        """Replay with health alerts in setup."""
        conv = GoldenConversation.from_jsonl(
            FIXTURES_DIR / "health_alert_response.jsonl"
        )
        results = replay_conversation(conv, coach_tools, mock_memory_repo)
        assert len(results) == 1
        # The harness should have injected alerts into the agent ref
        assert results[0].errors == []

    def test_replay_result_captures_invocations(self, coach_tools, mock_memory_repo):
        """ReplayResult should capture LLM invocations for inspection."""
        conv = GoldenConversation.from_jsonl(
            FIXTURES_DIR / "greeting_new_athlete.jsonl"
        )
        results = replay_conversation(conv, coach_tools, mock_memory_repo)
        assert len(results[0].llm_invocations) >= 1

    def test_replay_detects_missing_expected_content(
        self, coach_tools, mock_memory_repo
    ):
        """Should report error when expected content is missing."""
        conv = GoldenConversation(
            name="test",
            setup=GoldenSetup(athlete={"name": "Test", "primary_sport": "running"}),
            turns=[
                GoldenTurn(role="user", content="Hello"),
                GoldenTurn(
                    role="assistant",
                    content="Hi there!",
                    expect_contains=["this_will_not_match_xyz"],
                ),
            ],
        )
        results = replay_conversation(conv, coach_tools, mock_memory_repo)
        assert len(results[0].errors) > 0
        assert "this_will_not_match_xyz" in results[0].errors[0]


# Run all golden conversations (parametrized)


_golden_conversations = discover_golden_conversations(FIXTURES_DIR)


@pytest.mark.parametrize(
    "conversation",
    _golden_conversations,
    ids=[c.name for c in _golden_conversations],
)
def test_golden_conversation_replays_without_errors(
    conversation, coach_tools, mock_memory_repo
):
    """Each golden conversation should replay without assertion errors."""
    results = replay_conversation(conversation, coach_tools, mock_memory_repo)
    for result in results:
        assert result.errors == [], (
            f"Turn {result.turn_index} ('{result.user_content}'): {result.errors}"
        )
