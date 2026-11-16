"""The coach and conversation methods: welcome, chat, Apply cards, memory."""

from __future__ import annotations

from typing import Any

import pytest

from endury.analysis.runner import run_local
from endury.browser import CHAT_DISABLED_MESSAGE, rpc
from endury.coach.turn import TurnResult
from endury.common.memory_notes import MEMORY_HEADINGS
from tests.browser.helpers import app, boot, call, commit_ten_k, ok
from tests.doubles import FakeLLM, assistant_message, tool_call

ADHOC_PREFIX = "coachie:adhoc:"


def _ad_hoc(fake) -> list[str]:
    return [key for key in fake.events if key.startswith(ADHOC_PREFIX)]


class LoopingLLM(FakeLLM):
    """Calls a read tool forever, so the turn spends its whole tool budget."""

    async def complete(self, messages, tools=None, model=None) -> dict[str, Any]:
        return assistant_message("", [tool_call("r", "list_races", {})])


class FailingLLM(FakeLLM):
    """An OpenRouter that is down."""

    async def complete(self, messages, tools=None, model=None) -> dict[str, Any]:
        raise RuntimeError("OpenRouter is down")


# welcome and conversation


async def test_welcome_without_a_coach(fake):
    await boot(fake)

    assert (await ok("welcome"))["message"]


async def test_welcome_with_a_coach_refreshes_the_profile(fake):
    await boot(fake, llm=FakeLLM())
    app().repos.athlete.update({"name": "Sam"})

    welcome = await ok("welcome")

    assert "Sam" in welcome["message"]
    assert app().runtime.athlete_profile.name == "Sam"


async def test_conversation_recent_windows_back_to_the_last_reset(fake):
    await boot(fake)
    history = app().repos.history
    history.add("user", "old question")
    history.add("reset", "")
    history.add("user", "new question")
    history.add("assistant", "")
    history.add("assistant", "new answer")

    recent = await ok("conversation_recent", limit=10)

    assert recent == {
        "messages": [
            {"role": "user", "content": "new question"},
            {"role": "assistant", "content": "new answer"},
        ]
    }


async def test_conversation_recent_bounds_its_limit(fake):
    await boot(fake)

    envelope = await call("conversation_recent", limit=0)

    assert envelope["error_code"] == "invalid_request"


async def test_reset_chat_starts_a_new_conversation(fake):
    await boot(fake, llm=FakeLLM())
    app().repos.history.add("user", "before the reset")

    reset = await ok("reset_chat")

    assert reset["message"]
    assert (await ok("conversation_recent"))["messages"] == []


async def test_reset_chat_does_not_upload_the_store(fake):
    await boot(fake, llm=FakeLLM())
    rev = app().rev

    envelope = await call("reset_chat")

    assert envelope["ok"] is True
    assert envelope["unsaved"] is False
    assert app().rev == rev


async def test_a_history_read_failure_is_an_empty_conversation(fake, monkeypatch):
    await boot(fake)

    def broken(_limit):
        raise RuntimeError("history unavailable")

    monkeypatch.setattr(app().repos.history, "get_recent", broken)

    assert (await ok("conversation_recent"))["messages"] == []


async def test_coach_methods_are_disabled_without_a_coach(fake):
    await boot(fake)

    for method, params in (
        ("reset_chat", {}),
        ("chat", {"message": "hi"}),
        ("apply_proposal", {"proposal": {}}),
        ("dismiss_proposal", {"proposal": {}}),
        ("refresh_proposal", {"proposal": {}}),
    ):
        envelope = await call(method, **params)
        assert envelope["error_code"] == "chat_disabled", method
        assert envelope["error"] == CHAT_DISABLED_MESSAGE


# chat and Apply cards


async def test_a_turn_books_at_once_and_offers_removal_as_a_card(fake):
    await boot(
        fake,
        llm=FakeLLM(
            [
                assistant_message(
                    "",
                    [
                        tool_call(
                            "c1",
                            "schedule_workout",
                            {
                                "duration_minutes": 40,
                                "date": "2026-10-08",
                                "sport": "running",
                                "intent": "easy",
                            },
                        )
                    ],
                ),
                "Booked an easy 40 for tomorrow.",
                assistant_message(
                    "", [tool_call("c2", "unschedule_workout", {"date": "2026-10-08"})]
                ),
                "Here is the removal.",
            ]
        ),
    )

    booked = await ok("chat", message="  Easy run tomorrow?  ")

    assert booked["type"] == "done"
    assert booked["text"] == "Booked an easy 40 for tomorrow."
    assert booked["tools_used"] == ["schedule_workout"]
    assert booked["plan_changed"] is True
    assert booked["proposals"] == []
    assert "reason" not in booked
    [event_id] = _ad_hoc(fake)

    offered = await ok("chat", message="Actually, cancel it.")

    [card] = offered["proposals"]
    assert card["tool"] == "unschedule_workout"
    assert offered["plan_changed"] is False
    assert _ad_hoc(fake) == [event_id]

    applied = await ok("apply_proposal", proposal=card)

    assert applied["status"] == "applied"
    assert applied["plan_changed"] is True
    assert _ad_hoc(fake) == []
    assert app().coach.history[-1]["content"].startswith("[Applied:")


async def test_two_swaps_in_one_reply_are_two_cards_each_applied_with_one_tap(fake):
    # Seen live: "run 45 minutes and ride shorter" became one card, the
    # second swap's, and the run was never changed.
    await boot(fake)
    plan_id = await commit_ten_k()
    first, second = [
        row
        for row in app().repos.plan.get_sessions(plan_id)
        if "2026-10-13" <= row.date <= "2026-10-18"
    ][:2]
    await boot(
        fake,
        llm=FakeLLM(
            [
                assistant_message(
                    "",
                    [
                        tool_call(
                            "c1",
                            "swap_planned_session",
                            {
                                "session_id": first.id,
                                "intent": "easy",
                                "duration_minutes": 45,
                            },
                        ),
                        tool_call(
                            "c2",
                            "swap_planned_session",
                            {
                                "session_id": second.id,
                                "intent": "easy",
                                "duration_minutes": 30,
                            },
                        ),
                    ],
                ),
                "Both changes are on the cards.",
            ]
        ),
    )

    offered = await ok(
        "chat", message="Run 45 minutes instead, and keep the other one short."
    )
    run_card, other_card = offered["proposals"]

    assert (await ok("apply_proposal", proposal=run_card))["status"] == "applied"
    # The applied swap moved the week the other card describes: the shell
    # brings it up to date at once, and one tap then applies it.
    refreshed = await ok("refresh_proposal", proposal=other_card)
    assert refreshed["status"] == "preview"
    assert refreshed["reason"] == "stale"
    applied = await ok("apply_proposal", proposal=refreshed["proposal"])
    assert applied["status"] == "applied"

    rows = {row.id: row for row in app().repos.plan.get_sessions(plan_id)}
    assert rows[first.id].duration_minutes == pytest.approx(45, abs=5)
    assert rows[second.id].duration_minutes == pytest.approx(30, abs=5)


async def test_dismiss_proposal_tells_the_coach(fake):
    await boot(fake, llm=FakeLLM())
    card = {
        "id": "p1",
        "tool": "unschedule_workout",
        "args": {"date": "2026-10-08"},
        "preview_summary": "Take the Easy Run booked for Thursday 8 October off the calendar.",
        "date": "2026-10-07",
        "plan_id": None,
        "plan_revision": None,
    }

    assert await ok("dismiss_proposal", proposal=card) == {"status": "ok"}
    assert app().coach.history[-1]["content"] == (
        "[Dismissed: Take the Easy Run booked for Thursday 8 October off the calendar.]"
    )


async def test_chat_refuses_an_empty_message(fake):
    await boot(fake, llm=FakeLLM())

    envelope = await call("chat", message="   ")

    assert envelope["error_code"] == "invalid_request"


async def test_a_turn_that_fails_is_an_error_envelope(fake):
    await boot(fake, llm=FailingLLM())

    envelope = await call("chat", message="hello")

    assert envelope["ok"] is False
    assert envelope["error_code"] == "upstream_error"
    assert envelope["error"] == "OpenRouter is down"
    assert app().coach.history[-1]["role"] == "assistant"


async def test_a_deterministic_reply_carries_its_reason(fake):
    await boot(fake, llm=LoopingLLM())

    event = await ok("chat", message="list my races, forever")

    assert event["reason"] == "tool_budget_exhausted"
    assert set(event["tools_used"]) == {"list_races"}


async def test_a_turn_result_is_answered_whole(fake, monkeypatch):
    await boot(fake, llm=FakeLLM())

    async def canned(_message, isolated=False):
        return TurnResult(reply="Done.", tools_used=["a", "b"], plan_changed=True)

    monkeypatch.setattr(app().coach, "chat", canned)

    assert await ok("chat", message="go") == {
        "type": "done",
        "text": "Done.",
        "proposals": [],
        "plan_changed": True,
        "tools_used": ["a", "b"],
        "charts": [],
    }


# memory


async def test_memory_reads_writes_and_history(fake):
    await boot(fake)

    empty = await ok("get_memory")
    await ok("put_memory", content="- One.")
    stored = await ok("put_memory", content="- Two.")
    history = await ok("get_memory_history")

    assert empty["content"] == ""
    assert stored["content"] == "- Two."
    assert stored["updated_at"]
    assert history["versions"][0]["content"] == "- One."


async def test_a_memory_over_the_cap_is_refused(fake):
    await boot(fake)

    envelope = await call("put_memory", content="x" * 7000)

    assert envelope["error_code"] == "invalid_request"
    assert envelope["unsaved"] is False


async def test_cleanup_of_an_empty_memory_asks_no_model(fake):
    await boot(fake)

    assert await ok("cleanup_memory") == {"content": ""}


async def test_cleanup_needs_the_coach(fake):
    await boot(fake)
    await ok("put_memory", content="- Runs.")

    envelope = await call("cleanup_memory")

    assert envelope["error_code"] == "chat_disabled"


async def test_cleanup_proposes_a_tidied_document(fake):
    tidied = "\n".join(
        f"{heading}\n- x (review 2027-01-01)" for heading in MEMORY_HEADINGS
    )
    await boot(fake, llm=FakeLLM([tidied]))
    await ok("put_memory", content="- Runs.\n- Runs in the morning.")

    proposal = await ok("cleanup_memory")

    assert proposal == {"content": tidied}
    assert (await ok("get_memory"))["content"] == "- Runs.\n- Runs in the morning."


async def test_a_failed_cleanup_is_an_upstream_error(fake):
    await boot(fake, llm=FailingLLM())
    await ok("put_memory", content="- Runs.")

    envelope = await call("cleanup_memory")

    assert envelope["error_code"] == "upstream_error"
    assert "model call failed" in envelope["error"]


async def test_a_chart_reaches_the_page_and_survives_a_reload(fake, monkeypatch):
    monkeypatch.setattr(rpc, "_analysis_executor", run_local)
    code = "ana.chart('bar', ['Mon', 'Tue'], [[1.0, 2.0]], 'Hours this week', unit='h')"
    await boot(
        fake,
        llm=FakeLLM(
            [
                assistant_message(
                    "", [tool_call("a1", "run_analysis", {"code": code})]
                ),
                "DONE",
                "Your week is in the chart below.",
            ]
        ),
    )

    done = await ok("chat", message="Plot my week")

    assert done["text"] == "Your week is in the chart below."
    assert done["tools_used"] == ["run_analysis"]
    assert [chart["title"] for chart in done["charts"]] == ["Hours this week"]
    stored = (await ok("conversation_recent", limit=5))["messages"][-1]["content"]
    assert stored.startswith("Your week is in the chart below.\n%%CHART%%{")
    model_view = app().coach.history[-1]["content"]
    assert model_view.endswith("[Chart shown under this reply: Hours this week]")


async def test_no_analysis_tool_without_an_executor(fake, monkeypatch):
    monkeypatch.setattr(rpc, "_analysis_executor", None)
    await boot(fake, llm=FakeLLM(["Hi."]))
    assert "run_analysis" not in [tool.name for tool in app().coach.runtime.tools]


async def test_an_isolated_turn_is_not_in_the_conversation(fake):
    await boot(fake, llm=FakeLLM(["Quick take: steady and done."]))

    event = await ok(
        "chat", message="Quick take on my run (activity 1).", isolated=True
    )

    assert event["text"] == "Quick take: steady and done."
    assert (await ok("conversation_recent"))["messages"] == []
