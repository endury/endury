"""Golden conversation replay harness.

Loads curated conversation JSONL files and replays them through the
coach turn loop, verifying that tool calls and response content match
expected patterns.

Golden conversation format (JSONL):
    Line 1: {"setup": {"athlete": {...}, "health": "...", "alerts": [...]}}
    Line 2+: {"role": "user"|"assistant", ...}

User turns:
    {"role": "user", "content": "How was my run?"}

Expected assistant turns (matched against LLM output):
    {"role": "assistant", "content": "...",
     "expect_tool_calls": ["get_activities"],
     "expect_contains": ["run", "distance"],
     "expect_not_contains": ["error"]}

When expect_tool_calls is present, the harness verifies the LLM tried
to call those tools.  The actual tool execution uses the mock repos
from conftest.py fixtures.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from endury.coach.turn import TurnResult, run_turn
from endury.domain.models.athlete import AthleteProfile
from endury.wiring.config import CoachConfig
from tests.doubles import CapturingLLM, assistant_message, tool_call

logger = logging.getLogger(__name__)


# Data structures


@dataclass
class GoldenSetup:
    """Setup context for a golden conversation."""

    athlete: dict = field(default_factory=dict)
    health_summary: str | None = None
    alerts: list[str] = field(default_factory=list)
    new_activities: list[dict] = field(default_factory=list)


@dataclass
class GoldenTurn:
    """A single turn in a golden conversation."""

    role: str  # "user" or "assistant"
    content: str = ""
    expect_tool_calls: list[str] = field(default_factory=list)
    expect_contains: list[str] = field(default_factory=list)
    expect_not_contains: list[str] = field(default_factory=list)


@dataclass
class GoldenConversation:
    """A complete golden conversation with setup and turns."""

    name: str
    setup: GoldenSetup
    turns: list[GoldenTurn]

    @classmethod
    def from_jsonl(cls, path: Path) -> GoldenConversation:
        """Load a golden conversation from a JSONL file.

        Args:
            path: Path to the JSONL file.

        Returns:
            Parsed GoldenConversation.
        """
        lines = path.read_text().strip().split("\n")
        if not lines:
            raise ValueError(f"Empty golden conversation file: {path}")

        # First line: setup
        first = json.loads(lines[0])
        if "setup" not in first:
            raise ValueError(f"First line must have 'setup' key: {path}")

        setup_data = first["setup"]
        setup = GoldenSetup(
            athlete=setup_data.get("athlete", {}),
            health_summary=setup_data.get("health_summary"),
            alerts=setup_data.get("alerts", []),
            new_activities=setup_data.get("new_activities", []),
        )

        # Remaining lines: turns
        turns = []
        for line in lines[1:]:
            if not line.strip():
                continue
            data = json.loads(line)
            turns.append(
                GoldenTurn(
                    role=data["role"],
                    content=data.get("content", ""),
                    expect_tool_calls=data.get("expect_tool_calls", []),
                    expect_contains=data.get("expect_contains", []),
                    expect_not_contains=data.get("expect_not_contains", []),
                )
            )

        return cls(name=path.stem, setup=setup, turns=turns)


# Replay engine


@dataclass
class ReplayResult:
    """Result of replaying a single user turn."""

    turn_index: int
    user_content: str
    llm_invocations: list[list]
    tool_calls_made: list[str]
    final_content: str
    errors: list[str] = field(default_factory=list)


def replay_conversation(
    conversation: GoldenConversation,
    tools: list[Any],
    memory_repo: Any,
) -> list[ReplayResult]:
    """Replay a golden conversation and collect results.

    Runs a fresh turn per user message so each turn gets a clean
    CapturingLLM.  Tool calls from the LLM are verified against
    expect_tool_calls; response content is checked against
    expect_contains and expect_not_contains.

    Args:
        conversation: The golden conversation to replay.
        tools: Coach tools (typically from the coach_tools fixture).
        memory_repo: Mock athlete-memory repository (its ``get()`` supplies
            the document the system context renders).

    Returns:
        List of ReplayResult for each user turn.
    """
    # Build a runtime stand-in from the golden setup.
    athlete_data = conversation.setup.athlete
    profile = AthleteProfile(
        name=athlete_data.get("name", "Test"),
        experience_level=athlete_data.get("experience_level", "intermediate"),
        primary_sport=athlete_data.get("primary_sport", "running"),
        language=athlete_data.get("language", "en"),
    )
    runtime = SimpleNamespace(
        config=CoachConfig(),
        athlete_profile=profile,
        recent_health_summary=conversation.setup.health_summary,
        form_summary=None,
        latest_session_summary=None,
        scheduled_workouts_summary=None,
        active_plan_summary=None,
        macro_plan_summary=None,
        memory_repo=memory_repo,
        tools=tools,
        # Golden conversations have no training plan; the plan anchor reads
        # runtime.repos.plan and must resolve to "no active plan".
        repos=SimpleNamespace(plan=SimpleNamespace(get_active=lambda: None)),
    )

    results = []

    # Collect expected assistant turns paired with user turns
    paired: list[tuple[GoldenTurn, GoldenTurn | None]] = []
    i = 0
    while i < len(conversation.turns):
        turn = conversation.turns[i]
        if turn.role == "user":
            # Look ahead for expected assistant turn
            expected = None
            if (
                i + 1 < len(conversation.turns)
                and conversation.turns[i + 1].role == "assistant"
            ):
                expected = conversation.turns[i + 1]
                i += 2
            else:
                i += 1
            paired.append((turn, expected))
        else:
            i += 1

    for turn_idx, (user_turn, expected_turn) in enumerate(paired):
        # Build responses for this turn
        responses = []
        if expected_turn and expected_turn.expect_tool_calls:
            # First response: tool calls, second: final text
            tool_calls = [
                tool_call(f"call-{j}", name, {})
                for j, name in enumerate(expected_turn.expect_tool_calls)
            ]
            responses.append(assistant_message("", tool_calls))
            responses.append(assistant_message(expected_turn.content or "Done."))
        else:
            responses.append(
                assistant_message(expected_turn.content if expected_turn else "OK.")
            )

        llm = CapturingLLM(responses)

        async def _run(user_content: str, turn_llm: CapturingLLM) -> TurnResult:
            return await run_turn(
                runtime,
                [],
                user_content,
                llm=turn_llm,
                turn_log=[],
            )

        result = asyncio.run(_run(user_turn.content, llm))

        tool_calls_made = list(result.tools_used)
        final_content = result.reply

        # Check assertions
        errors: list[str] = []
        if expected_turn:
            errors.extend(
                f"Expected '{phrase}' in response but not found"
                for phrase in expected_turn.expect_contains
                if phrase.lower() not in final_content.lower()
            )
            errors.extend(
                f"Did not expect '{phrase}' in response but found it"
                for phrase in expected_turn.expect_not_contains
                if phrase.lower() in final_content.lower()
            )
            errors.extend(
                f"Expected tool call '{tool_name}' but it was not made"
                for tool_name in expected_turn.expect_tool_calls
                if tool_name not in tool_calls_made
            )

        results.append(
            ReplayResult(
                turn_index=turn_idx,
                user_content=user_turn.content,
                llm_invocations=llm.invocations,
                tool_calls_made=tool_calls_made,
                final_content=final_content,
                errors=errors,
            )
        )

    return results


# Discovery


def discover_golden_conversations(
    directory: Path | None = None,
) -> list[GoldenConversation]:
    """Find and load all golden conversation files in a directory.

    Args:
        directory: Directory to search. Defaults to
            tests/fixtures/golden_conversations/.

    Returns:
        List of parsed GoldenConversation objects.
    """
    if directory is None:
        directory = Path(__file__).parent / "fixtures" / "golden_conversations"
    if not directory.exists():
        return []
    conversations = []
    for path in sorted(directory.glob("*.jsonl")):
        try:
            conv = GoldenConversation.from_jsonl(path)
            conversations.append(conv)
        except Exception as exc:
            logger.warning("Failed to load golden conversation %s: %s", path, exc)
    return conversations
