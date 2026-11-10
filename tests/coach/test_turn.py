"""Tests for the coach turn loop (endury.coach.turn)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from endury.coach.context import NO_ACTIVE_PLAN
from endury.coach.messages import has_tool_calls
from endury.coach.tools.registry import coach_tool
from endury.coach.turn import (
    TurnResult,
    _build_llm_input,
    _build_system_message,
    asks_for_chart,
    nudge_profile,
    requires_tools,
    run_turn,
)
from endury.domain.models.athlete import AthleteProfile
from endury.domain.models.memory import AthleteMemory
from endury.wiring.config import CoachConfig
from tests.doubles import CapturingLLM, FakeLLM, assistant_message, tool_call


@coach_tool
def echo_tool(value: str) -> str:
    """Echo the value back as JSON."""
    return json.dumps({"echoed": value})


@coach_tool
def update_memory(content: str) -> str:
    """Rewrite the athlete-memory document."""
    return json.dumps({"success": True, "characters": len(content)})


def _runtime(*, config=None, tools=None, profile=None):
    runtime = MagicMock()
    runtime.config = config or CoachConfig()
    runtime.memory_repo.get.return_value = AthleteMemory(content="")
    runtime.athlete_profile = profile or AthleteProfile(
        name="Sam", primary_sport="running", language="en"
    )
    runtime.recent_health_summary = None
    runtime.form_summary = None
    runtime.latest_session_summary = None
    runtime.scheduled_workouts_summary = None
    runtime.active_plan_summary = None
    runtime.macro_plan_summary = None
    runtime.tools = tools if tools is not None else [echo_tool]
    runtime.repos.plan.get_active.return_value = None
    return runtime


async def _drive(runtime, history, user, llm):
    turn_log: list[dict] = []
    result = await run_turn(
        runtime,
        history,
        user,
        llm=llm,
        turn_log=turn_log,
    )
    return result, turn_log


def _system_text(message):
    """The system prompt as one string, whatever parts it was sent in."""
    return "".join(part["text"] for part in message["content"])


def test_requires_tools_plain_message_false():
    assert requires_tools("just saying hello") is False


def test_requires_tools_tool_keyword_true():
    assert requires_tools("how was my run?") is True


@pytest.mark.parametrize(
    "message",
    [
        "what's on tomorrow?",
        "anything planned for tonight",
        "what does next week look like",
        "can I move it to Thursday",
        "plans for the weekend",
        "was steht morgen an",
        "was mache ich heute",
    ],
)
def test_requires_tools_day_words_true(message):
    """A question about a day needs the plan read, not the anchor guessed from."""
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "was the second interval too hard?",
        "show me the splits",
        "the last rep felt awful",
        "quick debrief please",
    ],
)
def test_requires_tools_session_debrief_phrases(message):
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "How's my form?",
        "Am I ready for my race?",
        "How should I taper?",
        "I'm feeling burnt out",
        "Should I rest today?",
        "What should I eat before a long run?",
        "Any nutrition advice for race day?",
        "How do I fuel during the marathon?",
        "I feel so tired lately",
        "Am I at risk of burnout?",
        "How's my recovery going?",
        "Do I need to recover more?",
    ],
)
def test_requires_tools_judgment_question_true(message):
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "can you swap that for something harder?",
        "replace tomorrow with a threshold effort",
    ],
)
def test_requires_tools_swap_phrases_true(message):
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "I'm sick",
        "I am ill and cannot train",
        "feeling unwell today",
        "I injured my calf",
        "can I pause everything for a few days?",
        "I need to skip the next few days",
        "let's cancel the rest of the week",
        "please reschedule my trainings until next Wednesday",
        "can we postpone until Friday?",
    ],
)
def test_requires_tools_pause_phrases_true(message):
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        # A vague symptom description with no keyword match must still be
        # grounded as a tools-required turn, not treated as small talk.
        "I am still a bit verschleimt. Also I can feel my throat.",
        "I have a fever and a sore throat",
        "still coughing a lot",
        "my nose is congested",
        "the congestion is worse today than yesterday",
        "lots of mucus and phlegm",
        "the flu knocked me flat",
        "I have a cold",
        "dizzy when I stand up",
        "the dizziness has passed",
        "nausea all morning",
        "a bad headache since the weekend",
        "the symptoms are still there",
        "ich bin krank",
        "Ich bin seit gestern erkältet",
        "Ich habe eine Erkältung",
        "leichtes Fieber und Husten",
        "Halsschmerzen seit heute",
        "Schwindel beim Aufstehen",
    ],
)
def test_requires_tools_illness_symptom_phrases_true(message):
    assert requires_tools(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "I'm still fine",
        "I'll be there",
    ],
)
def test_requires_tools_ill_lookalikes_stay_false(message):
    assert requires_tools(message) is False


@pytest.mark.parametrize(
    "message",
    [
        "thanks!",
        "good morning",
        "hello there",
        "have a great day",
    ],
)
def test_requires_tools_small_talk_false(message):
    assert requires_tools(message) is False


def test_requires_tools_no_information_substring_false():
    assert requires_tools("no information about that") is False


@pytest.mark.parametrize(
    "message",
    [
        "the rest of the week looks busy for me",
        "I'm ready, let's get started",
        "fill in the form first",
        "let's eat afterwards",
    ],
)
def test_requires_tools_ambient_words_stay_false(message):
    assert requires_tools(message) is False


# run_turn — happy path


@pytest.mark.asyncio
async def test_simple_text_turn_returns_the_reply():
    runtime = _runtime(tools=[])
    result, turn_log = await _drive(runtime, [], "hi", FakeLLM(["Hello there"]))

    assert result == TurnResult(reply="Hello there")
    assert turn_log[0]["role"] == "user"
    assert turn_log[0]["content"] == "hi"
    # Stamped for the LLM input; the raw content is what the log carries.
    assert turn_log[0]["sent_at"]


# run_turn — tool loop


@pytest.mark.asyncio
async def test_tool_loop_executes_and_continues():
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "hi"})]),
            "final answer",
        ]
    )
    runtime = _runtime()
    result, turn_log = await _drive(runtime, [], "run details", llm)

    assert result.reply == "final answer"
    assert result.tools_used == ["echo_tool"]
    assert result.proposals == []
    assert result.plan_changed is False
    # turn_log: user, assistant(tool_calls), tool result, assistant(final)
    roles = [m["role"] for m in turn_log]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert json.loads(turn_log[2]["content"]) == {"echoed": "hi"}
    assert turn_log[2]["tool_call_id"] == "c1"


@coach_tool
def day_tool(date: str) -> str:
    """Return the session planned on a date."""
    return json.dumps({"sessions": [{"date": date, "sport": "running"}]})


@pytest.mark.asyncio
async def test_tool_dates_are_resolved_going_in_and_labelled_coming_out(monkeypatch):
    """The model may say "tomorrow" and reads back a weekday: it never has to
    do the date arithmetic that mixed up today and tomorrow."""
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-26")
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "day_tool", {"date": "tomorrow"})]),
            "Long run tomorrow.",
        ]
    )
    runtime = _runtime(tools=[day_tool])
    _, turn_log = await _drive(runtime, [], "what's on tomorrow?", llm)

    assert json.loads(turn_log[2]["content"]) == {
        "sessions": [
            {"date": "2026-09-27", "day": "Sunday, tomorrow", "sport": "running"}
        ]
    }


@pytest.mark.asyncio
async def test_tools_used_lists_every_call_in_order():
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "a"})]),
            assistant_message("", [tool_call("c2", "echo_tool", {"value": "b"})]),
            "done now",
        ]
    )
    runtime = _runtime()
    result, _ = await _drive(runtime, [], "run details", llm)
    assert result.tools_used == ["echo_tool", "echo_tool"]


@pytest.mark.asyncio
async def test_tool_round_cap_stops_execution():
    config = CoachConfig(max_tool_rounds=1)
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "a"})]),
            assistant_message("", [tool_call("c2", "echo_tool", {"value": "b"})]),
        ]
    )
    runtime = _runtime(config=config)
    result, _ = await _drive(runtime, [], "run details", llm)
    # Only one tool executed; the capped second tool-call is not run.
    assert result.tools_used == ["echo_tool"]


@pytest.mark.asyncio
async def test_final_round_tells_the_model_the_tool_budget_is_gone():
    # The round that can no longer execute tools must say so, or the model
    # answers with another tool call and the athlete gets nothing.
    llm = CapturingLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "a"})]),
            "final answer",
        ]
    )
    runtime = _runtime(config=CoachConfig(max_tool_rounds=1))
    await _drive(runtime, [], "how was my run?", llm)

    final_system = _system_text(llm.invocations[1][0])
    assert "No Tool Budget Left" in final_system
    assert "Tool Loop Note" not in final_system


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_ships_a_distinct_reason():
    # The model keeps calling tools past the budget: the athlete gets a
    # legible explanation and the result carries a distinct reason, not the
    # generic empty-response fallback.
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "a"})]),
            assistant_message("", [tool_call("c2", "echo_tool", {"value": "b"})]),
        ]
    )
    runtime = _runtime(config=CoachConfig(max_tool_rounds=1))
    result, _ = await _drive(runtime, [], "run details", llm)

    assert result.reason == "tool_budget_exhausted"
    assert "ran out of steps" in result.reply


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_keeps_prose_the_model_did_write():
    llm = FakeLLM(
        [
            assistant_message(
                "Easy 40 minutes tomorrow.",
                [tool_call("c1", "echo_tool", {"value": "a"})],
            ),
            assistant_message("", [tool_call("c2", "echo_tool", {"value": "b"})]),
        ]
    )
    runtime = _runtime(config=CoachConfig(max_tool_rounds=1))
    result, _ = await _drive(runtime, [], "run details", llm)

    assert result.reply == "Easy 40 minutes tomorrow."
    assert result.reason == ""


# run_turn — an empty final message on a turn where tools ran


@coach_tool
def write_tool() -> str:
    """Stand in for a plan write that completed."""
    return json.dumps(
        {
            "status": "ok",
            "message": "Recovery Run replaced the planned run_easy on 2026-09-25.",
        }
    )


@coach_tool
def prose_tool() -> str:
    """Stand in for a tool whose payload is not JSON at all."""
    return "no structured payload here"


@coach_tool
def list_tool() -> str:
    """Stand in for a tool whose payload is JSON but not an object."""
    return json.dumps([{"status": "ok", "message": "not a write"}])


async def _drive_empty_after_write(responses, *, tools):
    llm = CapturingLLM(responses)
    runtime = _runtime(tools=tools)
    result, _ = await _drive(runtime, [], "book Thursday", llm)
    return llm, result


@pytest.mark.asyncio
async def test_empty_reply_after_a_tool_round_is_re_asked_once():
    # The model committed a write and then said nothing. Asking once more —
    # without the empty assistant message trailing the request — is what
    # gets the athlete an answer instead of "send that request again".
    llm, result = await _drive_empty_after_write(
        [
            assistant_message("", [tool_call("c1", "write_tool", {})]),
            assistant_message(""),
            "Thursday it is.",
        ],
        tools=[write_tool],
    )

    assert result.reply == "Thursday it is."
    assert len(llm.invocations) == 3
    re_ask = llm.invocations[2]
    assert "Empty Reply" in _system_text(re_ask[0])
    assert not [
        m
        for m in re_ask[1:]
        if m.get("role") == "assistant"
        and not m.get("content")
        and not m.get("tool_calls")
    ]


@pytest.mark.asyncio
async def test_a_still_empty_re_ask_ships_the_completed_write_message():
    llm, result = await _drive_empty_after_write(
        [
            assistant_message("", [tool_call("c1", "write_tool", {})]),
            assistant_message(""),
            assistant_message(""),
        ],
        tools=[write_tool],
    )

    assert result.reply == "Recovery Run replaced the planned run_easy on 2026-09-25."
    assert result.reason == "completed_write_relayed"
    # Bounded: one re-ask, not a loop.
    assert len(llm.invocations) == 3


@pytest.mark.asyncio
async def test_a_still_empty_re_ask_without_a_write_ships_the_generic_notice():
    # No write landed, so there is nothing honest to relay — but the reply
    # still must not be empty, or the session persists "".
    _, result = await _drive_empty_after_write(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "a"})]),
            assistant_message(""),
            assistant_message(""),
        ],
        tools=[echo_tool],
    )

    assert result.reply.startswith("I couldn't put an answer together")
    assert result.reason == ""


@pytest.mark.asyncio
async def test_a_tool_result_that_is_not_json_is_skipped_not_shipped():
    _, result = await _drive_empty_after_write(
        [
            assistant_message(
                "",
                [
                    tool_call("c1", "write_tool", {}),
                    tool_call("c2", "prose_tool", {}),
                    tool_call("c3", "list_tool", {}),
                ],
            ),
            assistant_message(""),
            assistant_message(""),
        ],
        tools=[write_tool, prose_tool, list_tool],
    )

    assert result.reply.startswith("Recovery Run replaced")


@pytest.mark.asyncio
async def test_an_empty_reply_with_no_tool_round_is_not_re_asked():
    llm = CapturingLLM([assistant_message("")])
    runtime = _runtime(tools=[])
    result, _ = await _drive(runtime, [], "hey there", llm)

    assert len(llm.invocations) == 1
    assert result.reply.startswith("I couldn't put an answer together")


@pytest.mark.asyncio
async def test_prose_written_before_the_tool_round_is_not_re_asked():
    llm = CapturingLLM(
        [
            assistant_message(
                "Easy 30 minutes then.", [tool_call("c1", "write_tool", {})]
            ),
            assistant_message(""),
        ],
    )
    runtime = _runtime(tools=[write_tool])
    result, _ = await _drive(runtime, [], "book Thursday", llm)

    assert len(llm.invocations) == 2
    assert result.reply == "Easy 30 minutes then."


@coach_tool
def outline_tool() -> str:
    """Stand in for build_workout: a built workout and its quotable outline."""
    return json.dumps(
        {"outline": "**Main** — 13x [30 s + 15 s recovery]\n**Total** — 60 min"}
    )


@pytest.mark.asyncio
async def test_a_recovered_lead_in_carries_the_workout_its_tool_built():
    # The athlete read only "...wrap those micro-bursts in an extended
    # endurance spin." — the lead-in, without the workout it introduced.
    llm = CapturingLLM(
        [
            assistant_message(
                "Here is a 60-minute VO2 ride.", [tool_call("c1", "outline_tool", {})]
            ),
            assistant_message(""),
        ],
    )
    runtime = _runtime(tools=[outline_tool])
    result, _ = await _drive(runtime, [], "60 min vo2 ride", llm)

    assert result.reply == (
        "Here is a 60-minute VO2 ride.\n\n**Main** — 13x [30 s + 15 s recovery]\n"
        "**Total** — 60 min"
    )


@pytest.mark.asyncio
async def test_a_recovered_lead_in_that_quotes_the_outline_is_kept_as_is():
    outline = "**Main** — 13x [30 s + 15 s recovery]\n**Total** — 60 min"
    llm = CapturingLLM(
        [
            assistant_message("", [tool_call("c1", "outline_tool", {})]),
            assistant_message(
                f"Here it is.\n\n{outline}", [tool_call("c2", "list_tool", {})]
            ),
            assistant_message(""),
        ],
    )
    runtime = _runtime(tools=[outline_tool, list_tool])
    result, _ = await _drive(runtime, [], "60 min vo2 ride", llm)

    assert result.reply == f"Here it is.\n\n{outline}"


@pytest.mark.asyncio
async def test_tool_call_bad_arguments_defaults_to_empty():
    llm = FakeLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", "not valid json")]),
            "recovered",
        ]
    )
    runtime = _runtime()
    result, turn_log = await _drive(runtime, [], "run details", llm)
    # The tool ran with {} and reported the missing argument; the turn went on.
    assert result.reply == "recovered"
    assert "Invalid arguments" in turn_log[2]["content"]


@pytest.mark.asyncio
async def test_a_leaked_tool_call_is_cut_from_the_reply():
    # A fenced tool-call payload leaking into the reply text must be cut,
    # not shipped to the athlete as raw JSON.
    llm = FakeLLM(['Noted.\n```json\n{"content": "- Sick through Sunday"}\n```'])
    runtime = _runtime(tools=[update_memory])
    result, _ = await _drive(runtime, [], "I'm sick", llm)
    assert result.reply == "Noted."


@pytest.mark.asyncio
async def test_a_reply_that_was_only_a_leak_falls_back_to_the_notice():
    llm = FakeLLM(['```json\n{"content": "- Sick through Sunday"}\n```'])
    runtime = _runtime(tools=[update_memory])
    result, _ = await _drive(runtime, [], "I'm sick", llm)
    assert result.reply.startswith("I couldn't put an answer together")


@pytest.mark.asyncio
async def test_a_reply_cut_off_mid_sentence_still_ships(caplog):
    llm = FakeLLM(["Your fitness is up, your fatigue is down, and"])
    runtime = _runtime(tools=[])
    result, _ = await _drive(runtime, [], "how's my form?", llm)
    assert result.reply == "Your fitness is up, your fatigue is down, and"
    assert "mid-sentence" in caplog.text


# run_turn — a previewing tool yields a card and writes nothing

gated_calls: list[dict] = []


@coach_tool
def pause_training(through_date: str, confirm: bool = False) -> str:
    """A previewing plan write standing in for the real one."""
    gated_calls.append({"through_date": through_date, "confirm": confirm})
    if confirm:
        return json.dumps({"status": "ok", "message": "Paused."})
    return json.dumps(
        {
            "status": "preview",
            "confirm_required": True,
            "window": {"from": "2026-09-23", "through": through_date},
            "sessions": [{"session_id": 1}],
            "resume_date": "2026-09-30",
            "message": "Nothing has changed yet.",
        }
    )


@coach_tool
def swap_planned_session(
    session_id: int, duration_minutes: float, confirm: bool = False
) -> str:
    """A previewing swap of one session, standing in for the real one."""
    gated_calls.append({"session_id": session_id, "confirm": confirm})
    return json.dumps(
        {
            "status": "preview",
            "confirm_required": True,
            "date": "2026-09-26",
            "current": {"activity_key": "run_brick_short", "date": "2026-09-26"},
            "replacement": {
                "duration_minutes": duration_minutes,
                "workout_name": "Easy Run",
            },
            "week": {"before_tss": 420, "after_tss": 450},
        }
    )


@coach_tool
def set_session_status(session_id: int, status: str) -> str:
    """An immediate plan write."""
    return json.dumps({"status": "ok", "session_id": session_id, "new_status": status})


@pytest.fixture(autouse=True)
def _clear_gated_calls():
    gated_calls.clear()


@pytest.mark.asyncio
async def test_a_model_called_confirm_tool_yields_a_proposal_and_writes_nothing():
    # A model-requested confirm=true must still only ever preview; only an
    # applied proposal card can pass confirm=True through.
    llm = FakeLLM(
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
            "Here's the pause — it's on the card.",
        ]
    )
    runtime = _runtime(tools=[pause_training])
    result, turn_log = await _drive(runtime, [], "pause me", llm)

    assert gated_calls == [{"through_date": "2026-09-28", "confirm": False}]
    assert json.loads(turn_log[2]["content"])["status"] == "preview"
    assert result.plan_changed is False
    [proposal] = result.proposals
    assert proposal.tool == "pause_training"
    assert proposal.args == {"through_date": "2026-09-28"}
    assert proposal.preview_summary.startswith(
        "Pause training from Wednesday 23 September"
    )


@pytest.mark.asyncio
async def test_only_the_last_preview_per_tool_becomes_a_card():
    llm = FakeLLM(
        [
            assistant_message(
                "", [tool_call("c1", "pause_training", {"through_date": "2026-09-27"})]
            ),
            assistant_message(
                "", [tool_call("c2", "pause_training", {"through_date": "2026-09-28"})]
            ),
            "Through Sunday then.",
        ]
    )
    runtime = _runtime(tools=[pause_training])
    result, _ = await _drive(runtime, [], "pause me", llm)
    assert [p.args for p in result.proposals] == [{"through_date": "2026-09-28"}]


@pytest.mark.asyncio
async def test_two_sessions_swapped_in_one_reply_get_a_card_each():
    # Seen live: "run 45 minutes and ride shorter" previewed two swaps,
    # and the ride's card replaced the run's before the athlete saw either.
    llm = FakeLLM(
        [
            assistant_message(
                "",
                [
                    tool_call(
                        "c1",
                        "swap_planned_session",
                        {"session_id": 41, "duration_minutes": 45},
                    ),
                    tool_call(
                        "c2",
                        "swap_planned_session",
                        {"session_id": 42, "duration_minutes": 100},
                    ),
                ],
            ),
            "Both changes are on the cards.",
        ]
    )
    runtime = _runtime(tools=[swap_planned_session])
    result, _ = await _drive(runtime, [], "run 45 and ride shorter", llm)
    assert [p.args["session_id"] for p in result.proposals] == [41, 42]


@pytest.mark.asyncio
async def test_a_session_previewed_twice_keeps_one_card():
    llm = FakeLLM(
        [
            assistant_message(
                "",
                [
                    tool_call(
                        "c1",
                        "swap_planned_session",
                        {"session_id": 41, "duration_minutes": 30},
                    )
                ],
            ),
            assistant_message(
                "",
                [
                    tool_call(
                        "c2",
                        "swap_planned_session",
                        {"session_id": 41, "duration_minutes": 45},
                    )
                ],
            ),
            "45 minutes then.",
        ]
    )
    runtime = _runtime(tools=[swap_planned_session])
    result, _ = await _drive(runtime, [], "make it 45", llm)
    assert [p.args for p in result.proposals] == [
        {"session_id": 41, "duration_minutes": 45}
    ]


@pytest.mark.asyncio
async def test_an_immediate_plan_write_flags_the_plan_as_changed():
    llm = FakeLLM(
        [
            assistant_message(
                "",
                [
                    tool_call(
                        "c1",
                        "set_session_status",
                        {"session_id": 3, "status": "skipped"},
                    )
                ],
            ),
            "Skipped.",
        ]
    )
    runtime = _runtime(tools=[set_session_status])
    result, _ = await _drive(runtime, [], "skip it", llm)
    assert result.plan_changed is True
    assert result.proposals == []


@pytest.mark.asyncio
async def test_a_preview_never_flags_the_plan_as_changed_even_with_prose():
    llm = FakeLLM(
        [
            assistant_message(
                "Pausing you.",
                [tool_call("c1", "pause_training", {"through_date": "2026-09-28"})],
            ),
            "",
        ]
    )
    runtime = _runtime(tools=[pause_training])
    result, _ = await _drive(runtime, [], "pause me", llm)
    assert result.plan_changed is False
    assert result.reply == "Pausing you."


# run_turn — system context and history


@pytest.mark.asyncio
async def test_system_prompt_carries_the_runtimes_form_line():
    """Seen live: three replies advised on a two-day-old TSB because
    the context carried no form; the runtime's dated line now rides on
    every round."""
    runtime = _runtime(tools=[])
    runtime.form_summary = (
        "As of Tuesday 2026-10-20, from completed sessions: form (TSB) +2.0"
    )
    capturing = CapturingLLM(["Ride 90 easy."])
    await _drive(runtime, [], "1h30 easy ride today?", capturing)
    assert "## Form today\nAs of Tuesday 2026-10-20" in capturing.last_system_prompt


@pytest.mark.asyncio
async def test_language_reminder_included_for_non_english():
    profile = AthleteProfile(name="Sam", primary_sport="running", language="de")
    runtime = _runtime(tools=[], profile=profile)
    capturing = CapturingLLM(["Hallo"])
    await _drive(runtime, [], "hallo", capturing)
    assert "LANGUAGE REMINDER" in capturing.last_system_prompt


@pytest.mark.asyncio
async def test_tool_summary_and_loop_note_in_system_after_tools():
    llm = CapturingLLM(
        [
            assistant_message("", [tool_call("c1", "echo_tool", {"value": "x"})]),
            "done",
        ]
    )
    runtime = _runtime()
    await _drive(runtime, [], "how was my run?", llm)
    # Second invocation (after the tool round) carries the loop note + summary.
    second_system = _system_text(llm.invocations[1][0])
    assert "Tool Loop Note" in second_system
    assert "Tool summary" in second_system
    assert "Tool Requirement" in second_system  # requires_tools from "run"


@pytest.mark.asyncio
async def test_multi_round_tool_results_all_retained_in_llm_input():
    # Two tool rounds in the same turn; round-1's numbers must survive
    # into the third LLM call's input, not just round-2's.
    llm = CapturingLLM(
        [
            assistant_message(
                "", [tool_call("c1", "echo_tool", {"value": "round1-marker"})]
            ),
            assistant_message(
                "", [tool_call("c2", "echo_tool", {"value": "round2-marker"})]
            ),
            "final answer",
        ]
    )
    runtime = _runtime()
    await _drive(runtime, [], "how's my form and what's on the plan?", llm)
    third_round_contents = [m.get("content", "") for m in llm.invocations[2]]
    combined = " ".join(str(c) for c in third_round_contents)
    assert "round1-marker" in combined
    assert "round2-marker" in combined


@pytest.mark.asyncio
async def test_history_included_in_llm_input():
    history = [
        {"role": "assistant", "content": "Earlier welcome"},
        {"role": "user", "content": "prior question"},
        {"role": "assistant", "content": "prior answer"},
    ]
    capturing = CapturingLLM(["ok"])
    runtime = _runtime(tools=[])
    await _drive(runtime, history, "new question", capturing)
    contents = [m["content"] for m in capturing.last_messages]
    assert "prior answer" in contents
    # This turn's own message is stamped like every athlete message.
    assert any(str(c).endswith("] new question") for c in contents)


class _ToolCapturingLLM(FakeLLM):
    """FakeLLM that keeps the tool schema handed to ``complete``."""

    def __init__(self, responses):
        super().__init__(responses)
        self.tools = None

    async def complete(self, messages, tools=None, model=None):
        self.tools = tools
        return await super().complete(messages, tools, model)


@pytest.mark.asyncio
async def test_the_tools_reach_the_model_without_the_apps_own_parameters():
    llm = _ToolCapturingLLM(["ok"])
    runtime = _runtime(tools=[pause_training])
    await _drive(runtime, [], "hi", llm)
    [tool] = llm.tools
    assert "confirm" not in tool["function"]["parameters"]["properties"]
    assert "through_date" in tool["function"]["parameters"]["properties"]


@coach_tool
def get_planned_sessions(start_date: str, end_date: str) -> str:
    """Stand in for a tool that needs an active plan."""
    return json.dumps({"sessions": []})


@pytest.mark.asyncio
async def test_a_plan_tool_is_offered_only_with_a_plan():
    runtime = _runtime(tools=[echo_tool, get_planned_sessions])
    llm = _ToolCapturingLLM(["ok"])
    await _drive(runtime, [], "hi", llm)
    assert [tool["function"]["name"] for tool in llm.tools] == ["echo_tool"]

    runtime.repos.plan.get_active.return_value = MagicMock()
    llm = _ToolCapturingLLM(["ok"])
    await _drive(runtime, [], "hi", llm)
    assert [tool["function"]["name"] for tool in llm.tools] == [
        "echo_tool",
        "get_planned_sessions",
    ]


@pytest.mark.asyncio
async def test_an_unreadable_plan_keeps_every_tool_on_offer():
    runtime = _runtime(tools=[echo_tool, get_planned_sessions])
    runtime.repos.plan.get_active.side_effect = RuntimeError("database is locked")
    llm = _ToolCapturingLLM(["ok"])
    await _drive(runtime, [], "hi", llm)
    assert len(llm.tools) == 2


@pytest.mark.asyncio
async def test_a_plan_tool_called_without_a_plan_still_runs():
    # The offer is what the model is shown; a call to a tool it remembers
    # from an earlier turn gets that tool's own answer, not "unknown tool".
    runtime = _runtime(tools=[echo_tool, get_planned_sessions])
    llm = FakeLLM(
        [
            assistant_message(
                "",
                [
                    tool_call(
                        "c1",
                        "get_planned_sessions",
                        {"start_date": "2026-09-28", "end_date": "2026-09-29"},
                    )
                ],
            ),
            "Nothing planned.",
        ]
    )
    result, turn_log = await _drive(runtime, [], "what's planned", llm)
    assert json.loads(turn_log[2]["content"]) == {"sessions": []}
    assert result.reply == "Nothing planned."


@coach_tool
def unschedule_workout(date: str, confirm: bool = False) -> str:
    """Stand in for a previewing tool with relay rules.

    Reply contract — when relaying the result:
    * Name every workout in ``workouts``.

    Args:
        date: The day to look on.
        confirm: Filled in by the app when the athlete applies the
            card; not part of your call.
    """
    return json.dumps(
        {"status": "preview", "workouts": [{"date": date, "name": "Easy Run"}]}
    )


@pytest.mark.asyncio
async def test_a_tool_result_reaches_the_model_with_its_reply_contract():
    call = tool_call("c1", "unschedule_workout", {"date": "2026-09-29"})
    llm = FakeLLM([assistant_message("", [call]), "It's on the card."])
    result, turn_log = await _drive(
        _runtime(tools=[unschedule_workout]), [], "cancel it", llm
    )
    content = json.loads(turn_log[2]["content"])
    assert content["reply_contract"] == ["Name every workout in ``workouts``."]
    assert content["workouts"][0]["name"] == "Easy Run"
    # The card is built from the tool's own result, as before.
    [proposal] = result.proposals
    assert proposal.tool == "unschedule_workout"
    assert "reply_contract" not in proposal.args


# The athlete-memory document


def test_system_message_passes_the_memory_document_read_this_turn():
    """The document is read fresh per turn and handed to the context builder."""
    runtime = _runtime()
    document = "## Constraints (injury, availability)\n- no running for six weeks"
    runtime.memory_repo.get.return_value = AthleteMemory(
        content=document, updated_at="2026-08-20T09:00:00"
    )

    with patch("endury.coach.turn.build_system_context", return_value="CTX") as ctx:
        message = _build_system_message(
            runtime,
            [],
            tool_rounds=0,
            requires_tools=False,
        )

    assert message["role"] == "system"
    assert ctx.call_args[0][2] == document
    runtime.memory_repo.get.assert_called_once_with()


def test_system_message_says_so_when_no_plan_is_active():
    """Without the line, a plan note in memory reads as a plan still running."""
    runtime = _runtime()

    with patch("endury.coach.turn.build_system_context", return_value="CTX") as ctx:
        _build_system_message(runtime, [], tool_rounds=0, requires_tools=False)

    assert ctx.call_args.kwargs["active_plan_summary"] == NO_ACTIVE_PLAN


def test_system_message_keeps_the_plan_summary_of_an_active_plan():
    runtime = _runtime()
    runtime.active_plan_summary = "Plan A"
    runtime.repos.plan.get_active.return_value = object()

    with patch("endury.coach.turn.build_system_context", return_value="CTX") as ctx:
        _build_system_message(runtime, [], tool_rounds=0, requires_tools=False)

    assert ctx.call_args.kwargs["active_plan_summary"] == "Plan A"


# Prompt caching — the context block carries the cache breakpoint


def test_system_message_marks_the_context_block_for_caching():
    """Gemini's prompt cache only engages behind an explicit breakpoint."""
    with patch("endury.coach.turn.build_system_context", return_value="CTX"):
        message = _build_system_message(
            _runtime(),
            [],
            tool_rounds=0,
            requires_tools=False,
        )

    assert message["content"] == [
        {"type": "text", "text": "CTX", "cache_control": {"type": "ephemeral"}}
    ]


def test_system_message_keeps_the_round_notes_outside_the_cached_block():
    """Per-round notes change every round, so they must not bust the prefix."""
    with patch("endury.coach.turn.build_system_context", return_value="CTX"):
        message = _build_system_message(
            _runtime(),
            [],
            tool_rounds=1,
            requires_tools=True,
        )

    context_part, notes_part = message["content"]
    assert context_part["text"] == "CTX"
    assert "cache_control" not in notes_part
    assert "Tool Loop Note" in notes_part["text"]
    # Sent as parts, read as one prompt: the text is what it always was.
    assert _system_text(message).startswith("CTX\n\n## ")


# nudge_profile — when a turn may carry an intake question


def test_nudge_profile_allows_an_early_plain_turn():
    assert nudge_profile([], "hey there", tool_rounds=0) is True


def test_nudge_profile_gives_up_after_six_assistant_turns():
    history = [{"role": "assistant", "content": "hi"}] * 6
    assert nudge_profile(history, "hey there", tool_rounds=0) is False


def test_nudge_profile_stays_out_of_a_tool_round():
    assert nudge_profile([], "hey there", tool_rounds=1) is False


def test_nudge_profile_stays_out_of_an_illness_turn():
    assert nudge_profile([], "I'm sick until Wednesday", tool_rounds=0) is False


@pytest.mark.parametrize(
    "message",
    [
        "I am still a bit verschleimt. Also I can feel my throat.",
        "Ich habe eine Erkältung",
    ],
)
def test_nudge_profile_stays_out_of_a_symptom_turn(message):
    assert nudge_profile([], message, tool_rounds=0) is False


@pytest.mark.asyncio
async def test_illness_turn_system_prompt_drops_the_profile_note():
    runtime = _runtime(tools=[])
    capturing = CapturingLLM(["Rest up."])
    await _drive(runtime, [], "I'm sick until Wednesday", capturing)
    assert "Profile Note" not in _system_text(capturing.invocations[0][0])


@pytest.mark.asyncio
async def test_early_plain_turn_keeps_the_profile_note():
    runtime = _runtime(tools=[])
    capturing = CapturingLLM(["Hey."])
    await _drive(runtime, [], "hey there", capturing)
    assert "Profile Note" in _system_text(capturing.invocations[0][0])


# Replayed athlete messages carry when they were sent


@pytest.mark.asyncio
async def test_replayed_user_messages_reach_the_llm_with_their_send_time(monkeypatch):
    # A reply arriving nearly a day after the question must not be read as
    # the same evening — each replayed message carries its own send time.
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-09-21")
    monkeypatch.setenv("COACHING_BUDDY_SIM_TIME", "17:23:00")
    history = [
        {
            "role": "user",
            "content": "If my throat is clear tomorrow I want to train.",
            "sent_at": "2026-09-20T18:44:10",
        },
        {"role": "assistant", "content": "Then start on the bike."},
    ]
    runtime = _runtime(tools=[])
    capturing = CapturingLLM(["Right — let's go."])
    await _drive(runtime, history, "Alright! I am readyyyyyyy", capturing)

    sent = capturing.invocations[0]
    contents = [msg.get("content") for msg in sent if msg.get("role") == "user"]
    assert contents == [
        "[Sunday 2026-09-20 18:44] If my throat is clear tomorrow I want to train.",
        "[Monday 2026-09-21 17:23] Alright! I am readyyyyyyy",
    ]
    # The coach's own turns stay unstamped — no pattern to imitate.
    assert {"role": "assistant", "content": "Then start on the bike."} in sent
    # The private key never leaves the app.
    assert not any("sent_at" in msg for msg in sent)
    # And the session's own history keeps the athlete's raw words.
    assert history[0]["content"] == "If my throat is clear tomorrow I want to train."


# The history budget covers prior conversation only


def _tool_round(index: int) -> list[dict]:
    """One assistant tool-call message plus its matching result."""
    call_id = f"call-{index}"
    return [
        assistant_message("", [tool_call(call_id, "echo_tool", {"value": "x"})]),
        {
            "role": "tool",
            "tool_call_id": call_id,
            "name": "echo_tool",
            "content": json.dumps({"echoed": "x"}),
        },
    ]


def test_a_tool_heavy_turn_does_not_push_the_conversation_out_of_the_window():
    # The budget is 30 prior messages. Windowing `history + turn_messages`
    # together would let one turn's twelve tool rounds evict the very
    # conversation the turn is about.
    config = CoachConfig(max_message_history_messages=30)
    runtime = _runtime(config=config, tools=[echo_tool])
    history = [
        {"role": "user" if index % 2 == 0 else "assistant", "content": f"h{index}"}
        for index in range(40)
    ]
    turn_messages: list[dict] = [{"role": "user", "content": "and now?"}]
    for index in range(10):
        turn_messages.extend(_tool_round(index))

    built = _build_llm_input(
        runtime,
        history,
        turn_messages,
        tool_rounds=10,
        requires_tools=True,
    )

    contents = [msg.get("content") for msg in built]
    budget = config.max_message_history_messages
    for entry in history[-budget:]:
        assert entry["content"] in contents
    for entry in history[:-budget]:
        assert entry["content"] not in contents
    # The whole current turn survives, tool call/result pairing intact.
    assert "and now?" in contents
    assert sum(1 for msg in built if msg.get("role") == "tool") == 10
    assert sum(1 for msg in built if has_tool_calls(msg)) == 10


# Analysis turns: the async tool, its charts, the round that writes the reply


class _OfferLLM(FakeLLM):
    """FakeLLM that records each round's system prompt and whether tools were offered."""

    def __init__(self, responses):
        super().__init__(responses)
        self.offered: list[bool] = []
        self.systems: list[str] = []

    async def complete(self, messages, tools=None, model=None):
        self.offered.append(tools is not None)
        self.systems.append(_system_text(messages[0]))
        return self._next()


CHART = {"kind": "bar", "title": "Hours", "x": ["a"], "ys": [[1.0]], "unit": "h"}


def _analysis_tool(charts=None):
    @coach_tool
    async def run_analysis(code: str, activity_ids: str | None = None) -> str:
        """Analyse the athlete's data."""
        return json.dumps({"output": "ok", "error": None, "charts": charts or []})

    return run_analysis


def _analysis_call():
    return assistant_message(
        tool_calls=[tool_call("c1", "run_analysis", {"code": "x"})]
    )


async def test_an_analysis_turn_writes_its_reply_after_done():
    llm = _OfferLLM([_analysis_call(), "DONE", "Here is your week."])
    runtime = _runtime(tools=[_analysis_tool([CHART])])

    result, log = await _drive(runtime, [], "Plot my week", llm)

    assert result.reply == "Here is your week."
    assert llm.offered == [True, True, False]
    assert [chart["title"] for chart in result.charts] == ["Hours"]
    tool_message = next(m for m in log if m["role"] == "tool")
    assert '"ys"' not in tool_message["content"]


async def test_a_reply_drafted_beside_a_call_is_written_again_whole():
    drafted = assistant_message(
        "Your week looked steady. " * 12,
        tool_calls=[tool_call("c1", "run_analysis", {"code": "x"})],
    )
    llm = _OfferLLM([drafted, "My earlier answer stands.", "The whole reply."])

    result, _ = await _drive(
        _runtime(tools=[_analysis_tool()]), [], "How was my week?", llm
    )

    assert result.reply == "The whole reply."
    assert llm.offered == [True, True, False]


async def test_an_analysis_reply_written_plainly_ships_as_it_is():
    llm = _OfferLLM([_analysis_call(), "Here is the answer."])

    result, _ = await _drive(
        _runtime(tools=[_analysis_tool()]), [], "How was my week?", llm
    )

    assert result.reply == "Here is the answer."
    assert llm.offered == [True, True]


async def test_the_chart_note_holds_until_a_chart_is_drawn():
    llm = _OfferLLM([_analysis_call(), "Here it is."])

    await _drive(_runtime(tools=[_analysis_tool([CHART])]), [], "Show me my pace", llm)

    assert "Chart Requested" in llm.systems[0]
    assert "Chart Requested" not in llm.systems[1]


async def test_no_chart_note_without_the_analysis_tool():
    llm = _OfferLLM(["Fine."])
    await _drive(_runtime(), [], "Plot my pace", llm)
    assert "Chart Requested" not in llm.systems[0]


def _scripted_analysis(*results):
    """An analysis tool that answers each call with the next result."""
    queue = list(results)

    @coach_tool
    async def run_analysis(code: str, activity_ids: str | None = None) -> str:
        """Analyse the athlete's data."""
        return json.dumps(queue.pop(0))

    return run_analysis


def _tool_contents(log):
    return [json.loads(m["content"]) for m in log if m["role"] == "tool"]


async def test_an_analysis_without_a_chart_is_asked_whether_it_wants_one():
    llm = _OfferLLM(
        [_analysis_call(), _analysis_call(), _analysis_call(), "Here it is."]
    )
    tool = _scripted_analysis(
        {"output": "weeks", "error": None, "charts": []},
        {"output": "weeks", "error": None, "charts": [CHART]},
        {"output": "more", "error": None, "charts": []},
    )

    result, _ = await _drive(_runtime(tools=[tool]), [], "How was my block?", llm)

    checked = ["Chart Check" in system for system in llm.systems]
    # Asked after the chartless script only; once drawn, never again.
    assert checked == [False, True, False, False]
    assert not any("Chart Requested" in system for system in llm.systems)
    assert [chart["title"] for chart in result.charts] == ["Hours"]


async def test_a_failed_analysis_is_not_asked_for_a_chart():
    llm = _OfferLLM([_analysis_call(), "Here it is."])
    tool = _scripted_analysis({"output": "", "error": "NameError: x", "charts": []})

    await _drive(_runtime(tools=[tool]), [], "How was my block?", llm)

    assert not any("Chart Check" in system for system in llm.systems)


async def test_a_chart_asked_for_keeps_its_own_note_after_an_analysis():
    llm = _OfferLLM([_analysis_call(), "Here it is."])
    tool = _scripted_analysis({"output": "weeks", "error": None, "charts": []})

    await _drive(_runtime(tools=[tool]), [], "Plot my block", llm)

    assert all("Chart Requested" in system for system in llm.systems)
    assert not any("Chart Check" in system for system in llm.systems)


async def test_a_standard_tool_draws_its_chart_under_the_reply():
    @coach_tool
    def get_weekly_training_summary(weeks: int = 4, chart: bool = False) -> str:
        """Summarize training per week."""
        return json.dumps({"weeks": [], "charts": [CHART] if chart else []})

    call = assistant_message(
        tool_calls=[tool_call("c1", "get_weekly_training_summary", {"chart": True})]
    )
    llm = _OfferLLM([call, "Here is your volume."])

    result, log = await _drive(
        _runtime(tools=[get_weekly_training_summary]), [], "How is my volume?", llm
    )

    assert [chart["title"] for chart in result.charts] == ["Hours"]
    (content,) = _tool_contents(log)
    assert content["charts_attached"] == ["Hours (bar, 1 series, 1 points)"]


@pytest.mark.parametrize(
    ("message", "asked"),
    [
        ("Plot my heart rate", True),
        ("Can you show me my splits?", True),
        ("Draw a chart of my week", True),
        ("Zeig mir meine Pace", True),
        ("How did my run go?", False),
        ("I want to improve my graphite bike", False),
    ],
)
def test_asks_for_chart(message, asked):
    assert asks_for_chart(message) is asked
