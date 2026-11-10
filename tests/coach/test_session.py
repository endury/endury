"""Tests for CoachSession lifecycle, chat, Apply cards, and history persistence."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.proposals import ApplyResult, Proposal
from endury.coach.runtime import CoachRuntime
from endury.coach.session import CoachSession
from endury.coach.tools.registry import coach_tool
from endury.coach.turn import TurnResult
from endury.wiring.config import CoachConfig
from endury.wiring.container import RepositoryContainer
from tests.doubles import FakeLLM, assistant_message, tool_call


@pytest.fixture
def container():
    return RepositoryContainer.in_memory()


def _runtime(container, responses=None):
    return CoachRuntime(
        config=CoachConfig(),
        llm=FakeLLM(responses or ["Coach reply."]),
        repos=container,
    )


# Full-stack happy paths (real runtime + FakeLLM)


@pytest.mark.asyncio
async def test_initialize_returns_welcome_and_loads_profile(container):
    container.athlete.update({"name": "Sam", "primary_sport": "triathlon"})
    session = _runtime(container).create_session()

    welcome = await session.initialize()

    assert session._initialized is True
    assert isinstance(welcome, str)
    assert welcome
    assert session.runtime.athlete_profile.name == "Sam"


@pytest.mark.asyncio
async def test_chat_returns_the_turn_and_persists_history(container):
    session = _runtime(container, ["Easy aerobic day."]).create_session()
    await session.initialize()

    result = await session.chat("What should I do today?")

    assert result == TurnResult(reply="Easy aerobic day.")
    persisted = container.history.get_recent(10)
    roles = [(e.role, e.content) for e in persisted]
    assert ("user", "What should I do today?") in roles
    assert ("assistant", "Easy aerobic day.") in roles


@pytest.mark.asyncio
async def test_chat_initializes_lazily(container):
    session = _runtime(container, ["Hi."]).create_session()
    result = await session.chat("hello")
    assert session._initialized is True
    assert result.reply == "Hi."


@pytest.mark.asyncio
async def test_history_resumes_across_sessions(container):
    first = _runtime(container, ["First answer."]).create_session()
    await first.initialize()
    await first.chat("first question")

    second = _runtime(container, ["Second answer."]).create_session()
    await second.initialize()

    contents = [m["content"] for m in second.history]
    assert "first question" in contents
    assert "First answer." in contents


@pytest.mark.asyncio
async def test_reset_marker_clears_prior_history(container):
    first = _runtime(container, ["Answer one."]).create_session()
    await first.initialize()
    await first.chat("remembered question")

    fresh = _runtime(container, ["Answer two."]).create_session()
    await fresh.initialize(reset=True)

    assert fresh.history == []


@pytest.mark.asyncio
async def test_initialize_flags_memory_empty_when_document_blank(container):
    session = _runtime(container).create_session()
    with patch(
        "endury.coach.session.generate_welcome_message", return_value="hey"
    ) as welcome_builder:
        await session.initialize()
    assert welcome_builder.call_args.kwargs["memory_empty"] is True


@pytest.mark.asyncio
async def test_initialize_flags_memory_not_empty_when_document_has_content(container):
    container.memory.set("## Goals\n- Kraichgau 70.3")
    session = _runtime(container).create_session()
    with patch(
        "endury.coach.session.generate_welcome_message", return_value="hey"
    ) as welcome_builder:
        await session.initialize()
    assert welcome_builder.call_args.kwargs["memory_empty"] is False


@pytest.mark.asyncio
async def test_first_turn_carries_no_synthetic_welcome_message(container):
    # The welcome is UI chrome, not a conversation turn: fed back as an
    # assistant message, it makes the coach re-introduce itself every time.
    session = _runtime(container, ["r1"]).create_session()
    await session.initialize()

    seen: list[list[dict]] = []

    async def _fake_turn(runtime_, history, user, *, llm, turn_log):
        seen.append(list(history))
        return TurnResult(reply="r1")

    with patch("endury.coach.session.run_turn", _fake_turn):
        await session.chat("turn one")

    assert seen == [[]]


# Apply cards, end to end: a model-called write previews, the athlete applies

_PREVIEW = {
    "status": "preview",
    "confirm_required": True,
    "window": {"from": "2026-09-23", "through": "2026-09-28"},
    "sessions": [{"session_id": 1}],
    "resume_date": "2026-09-30",
    "message": "Nothing has changed yet.",
}

writes: list[dict] = []


@coach_tool
def pause_training(through_date: str, confirm: bool = False) -> str:
    """A previewing plan write standing in for the real one."""
    writes.append({"through_date": through_date, "confirm": confirm})
    if confirm:
        return json.dumps(
            {"status": "ok", "message": f"Paused through {through_date}."}
        )
    return json.dumps(_PREVIEW)


@pytest.fixture(autouse=True)
def _clear_writes():
    writes.clear()


def _card_session(container, monkeypatch, responses):
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-23")
    runtime = _runtime(container, responses)
    runtime.tools = [pause_training]
    return runtime.create_session()


@pytest.mark.asyncio
async def test_a_model_called_write_yields_a_card_and_writes_nothing(
    container, monkeypatch
):
    session = _card_session(
        container,
        monkeypatch,
        [
            assistant_message(
                "",
                [
                    tool_call(
                        "c1",
                        "pause_training",
                        {"through_date": "2026-09-28", "confirm": True},
                    )
                ],
            ),
            "Here's the pause.",
        ],
    )
    await session.initialize()

    result = await session.chat("pause me through Sunday")

    assert writes == [{"through_date": "2026-09-28", "confirm": False}]
    assert result.plan_changed is False
    [card] = result.proposals
    assert card.tool == "pause_training"
    assert card.args == {"through_date": "2026-09-28"}


@pytest.mark.asyncio
async def test_apply_proposal_writes_and_tells_the_model(container, monkeypatch):
    session = _card_session(
        container,
        monkeypatch,
        [
            assistant_message(
                "", [tool_call("c1", "pause_training", {"through_date": "2026-09-28"})]
            ),
            "Here's the pause.",
        ],
    )
    await session.initialize()
    [card] = (await session.chat("pause me")).proposals
    writes.clear()

    outcome = await session.apply_proposal(card.to_dict())

    assert outcome.status == "applied"
    assert outcome.plan_changed is True
    assert writes == [{"through_date": "2026-09-28", "confirm": True}]
    note = "[Applied: Paused through 2026-09-28.]"
    assert session.history[-1]["role"] == "user"
    assert session.history[-1]["content"] == note
    assert session.history[-1]["sent_at"]
    assert ("user", note) in [
        (e.role, e.content) for e in container.history.get_recent(10)
    ]


@pytest.mark.asyncio
async def test_a_stale_card_re_previews_instead_of_writing(container, monkeypatch):
    session = _card_session(
        container,
        monkeypatch,
        [
            assistant_message(
                "", [tool_call("c1", "pause_training", {"through_date": "2026-09-28"})]
            ),
            "Here's the pause.",
        ],
    )
    await session.initialize()
    [card] = (await session.chat("pause me")).proposals
    writes.clear()
    history_before = list(session.history)

    # The athlete taps Apply the next morning.
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-24")
    outcome = await session.apply_proposal(card)

    assert outcome.status == "preview"
    assert outcome.reason == "stale"
    assert outcome.proposal is not None
    assert outcome.proposal.date == "2026-09-24"
    assert writes == [{"through_date": "2026-09-28", "confirm": False}]
    # No note: nothing happened, the athlete has the fresh card.
    assert session.history == history_before


@pytest.mark.asyncio
async def test_dismiss_proposal_tells_the_model(container, monkeypatch):
    session = _card_session(container, monkeypatch, ["x"])
    await session.initialize()
    card = Proposal(
        "p1", "pause_training", {}, "Pause training today.", "2026-09-23", None, None
    )

    await session.dismiss_proposal(card)

    assert session.history[-1]["content"] == "[Dismissed: Pause training today.]"
    assert writes == []


@pytest.mark.asyncio
async def test_apply_proposal_falls_back_to_the_summary_when_the_tool_has_no_message():
    runtime = MagicMock()
    runtime.repos.history.add.return_value = 1
    session = CoachSession(runtime=runtime)
    card = Proposal(
        "p1", "pause_training", {}, "Pause training today.", "2026-09-23", None, None
    )
    with patch(
        "endury.coach.session.run_apply_proposal",
        return_value=ApplyResult("applied", ""),
    ):
        await session.apply_proposal(card)
    assert session.history[-1]["content"] == "[Applied: Pause training today.]"


# Edge branches with a mocked runtime


def _mock_runtime():
    runtime = MagicMock()
    runtime.config = CoachConfig()
    runtime.coach_llm.return_value = FakeLLM(["x"])
    return runtime


@pytest.mark.asyncio
async def test_aclose_is_noop():
    session = CoachSession(runtime=_mock_runtime())
    assert await session.aclose() is None


def test_load_history_swallows_repo_error():
    runtime = _mock_runtime()
    runtime.repos.history.get_recent.side_effect = RuntimeError("db down")
    session = CoachSession(runtime=runtime)
    assert session._load_history() == []


def test_load_history_filters_and_honors_reset():
    runtime = _mock_runtime()
    runtime.repos.history.get_recent.return_value = [
        MagicMock(role="user", content="old", created_at="2026-09-19T08:00:00"),
        MagicMock(role="reset", content=""),
        MagicMock(role="user", content="new", created_at="2026-09-20T18:44:10"),
        MagicMock(role="assistant", content="reply", created_at="2026-09-20T18:44:30"),
        MagicMock(role="system", content="ignored"),
    ]
    session = CoachSession(runtime=runtime)
    history = session._load_history()
    # The coach's own replies carry sent_at too: last night's "tomorrow you
    # have the long run" must not read as still tomorrow this morning.
    assert history == [
        {"role": "user", "content": "new", "sent_at": "2026-09-20T18:44:10"},
        {"role": "assistant", "content": "reply", "sent_at": "2026-09-20T18:44:30"},
    ]


@pytest.mark.asyncio
async def test_initialize_reset_marker_error_is_swallowed():
    runtime = _mock_runtime()
    runtime.repos.history.add.side_effect = RuntimeError("write failed")
    runtime.repos.history.get_recent.return_value = []
    runtime.athlete_profile = None
    runtime.recent_health_summary = None
    session = CoachSession(runtime=runtime)
    with patch("endury.coach.session.generate_welcome_message", return_value="hey"):
        welcome = await session.initialize(reset=True)
    assert welcome == "hey"


@pytest.mark.asyncio
async def test_persist_error_is_swallowed():
    runtime = _mock_runtime()
    runtime.repos.history.add.side_effect = RuntimeError("db down")
    session = CoachSession(runtime=runtime)
    session._persist("user", "hi")  # must not raise


@pytest.mark.asyncio
async def test_chat_records_interrupted_note_and_finalizes_on_failure():
    # A failure lands *after* write tools already hit SQLite and
    # intervals.icu. Losing the turn record orphans those changes, so a
    # deterministic note keeps the completed tool rounds attached to a turn,
    # and the finalizer still runs.
    runtime = _mock_runtime()
    runtime.repos.history.add.return_value = 1
    session = CoachSession(runtime=runtime)
    session._initialized = True
    session._llm = FakeLLM(["x"])

    async def _boom(runtime_, history, user, *, llm, turn_log):
        turn_log.append({"role": "user", "content": user})
        raise RuntimeError("model dropped")

    session.finalizer = MagicMock()
    with (
        patch("endury.coach.session.run_turn", _boom),
        pytest.raises(RuntimeError),
    ):
        await session.chat("swap tomorrow")

    role, content = runtime.repos.history.add.call_args_list[-1].args
    assert role == "assistant"
    assert "interrupted" in content.lower()
    assert session.history[-2:] == [
        {
            "role": "user",
            "content": "swap tomorrow",
            "sent_at": session.history[-2]["sent_at"],
        },
        {
            "role": "assistant",
            "content": content,
            "sent_at": session.history[-1]["sent_at"],
        },
    ]
    session.finalizer.finalize.assert_called_once()


@pytest.mark.asyncio
async def test_chat_persists_and_finalizes():
    runtime = _mock_runtime()
    runtime.repos.history.add.return_value = 1
    session = CoachSession(runtime=runtime)
    session._initialized = True
    session._llm = FakeLLM(["done text"])

    async def _fake_turn(runtime_, history, user, *, llm, turn_log):
        turn_log.append({"role": "user", "content": user})
        return TurnResult(reply="final")

    session.finalizer = MagicMock()
    with patch("endury.coach.session.run_turn", _fake_turn):
        result = await session.chat("hello")

    assert result.reply == "final"
    assert session.history[-1] == {
        "role": "assistant",
        "content": "final",
        "sent_at": session.history[-1]["sent_at"],
    }
    session.finalizer.finalize.assert_called_once()


@pytest.mark.asyncio
async def test_chat_stamps_the_turns_user_message_with_its_send_time(monkeypatch):
    # The turn's own message gets the same sent_at the next load will read
    # off `created_at`, so Sunday's and Monday's stamps sit adjacent.
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-21")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "17:23:00")
    runtime = _mock_runtime()
    runtime.repos.history.add.return_value = 1
    session = CoachSession(runtime=runtime)
    session._initialized = True
    session._llm = FakeLLM(["x"])

    async def _fake_turn(runtime_, history, user, *, llm, turn_log):
        return TurnResult(reply="final")

    session.finalizer = MagicMock()
    with patch("endury.coach.session.run_turn", _fake_turn):
        await session.chat("Alright! I am readyyyyyyy")

    assert session.history[-2] == {
        "role": "user",
        "content": "Alright! I am readyyyyyyy",
        "sent_at": "2026-09-21T17:23:00",
    }
    assert session.history[-1] == {
        "role": "assistant",
        "content": "final",
        "sent_at": "2026-09-21T17:23:00",
    }


@pytest.mark.asyncio
async def test_an_isolated_turn_sees_no_history_and_leaves_none(container):
    # A take or a brief asked from a sheet: the model answers from the
    # athlete's data and the request alone, and the chat keeps nothing of it.
    from tests.doubles import CapturingLLM

    llm = CapturingLLM(["Easy aerobic day.", "Quick take: it did its job."])
    session = CoachRuntime(
        config=CoachConfig(), llm=llm, repos=container
    ).create_session()
    await session.initialize()
    await session.chat("What should I do today?")
    before = list(session.history)
    persisted_before = [(e.role, e.content) for e in container.history.get_recent(10)]

    result = await session.chat("Quick take on my run (activity 1).", isolated=True)

    assert result.reply == "Quick take: it did its job."
    seen = [m["content"] for m in llm.invocations[-1] if m["role"] == "user"]
    assert len(seen) == 1
    assert seen[0].endswith("Quick take on my run (activity 1).")
    assert "What should I do today?" not in "".join(seen)
    assert session.history == before
    assert [(e.role, e.content) for e in container.history.get_recent(10)] == (
        persisted_before
    )
