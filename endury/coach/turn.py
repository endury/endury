"""The coach turn loop.

One coroutine, ``run_turn``, drives a single coaching turn:

    classify → build system context → completion/tool rounds → reply

It returns a :class:`TurnResult`: the reply text, the tools that ran, the
proposals (Apply cards) those tools previewed, and whether the plan changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from endury.coach.context import (
    _LANGUAGE_INSTRUCTIONS,
    PROFILE_NUDGE_MAX_TURNS,
    build_experience_fallback,
    build_no_plan_note,
    build_plan_anchor,
    build_races_section,
    build_sport_doctrine,
    build_system_context,
)
from endury.coach.dates import label_dates, resolve_date_args
from endury.coach.messages import (
    SENT_AT_KEY,
    Message,
    build_tool_summary,
    has_tool_calls,
    prepare_messages_for_llm,
    prune_tool_history,
    select_recent_messages,
    stamp_user_messages,
)
from endury.coach.proposals import (
    PLAN_WRITE_TOOLS,
    Proposal,
    card_key,
    find_tool,
    is_gated,
    proposal_from_result,
    run_tool,
)
from endury.coach.reply.postprocess import (
    clean_coach_response,
    is_complete_reply,
    is_meaningful_response_text,
    strip_leaked_tool_calls,
)
from endury.coach.tools import offered_tools
from endury.coach.tools.analysis_tools import ANALYSIS_TOOL, take_charts
from endury.coach.tools.registry import (
    execute_tool_async,
    to_openai_tools,
    with_reply_contract,
)
from endury.common import clock
from endury.common.exceptions import EnduryError

if TYPE_CHECKING:
    from endury.coach.llm import OpenRouterClient
    from endury.coach.runtime import CoachRuntime

logger = logging.getLogger(__name__)


# Heuristic turn classifier — decides only whether tool grounding is likely

# The "I cannot train" words (CO-illness), kept in their own frozenset since
# the nudge suppression below needs them apart from the rest: an athlete
# reporting illness is not a turn to ask intake questions on. Concrete
# symptom words are included alongside the abstract ones, so a report naming
# only symptoms is still recognised. Both English and German, since a
# bilingual athlete may type either.
_PAUSE_KEYWORDS: frozenset[str] = frozenset(
    {
        "sick",
        "ill",
        "unwell",
        "injured",
        "injury",
        "skip",
        "reschedule",
        "postpone",
        "pause",
        "cancel",
        "fever",
        "cough",
        "coughing",
        "throat",
        "flu",
        "cold",
        "congested",
        "congestion",
        "mucus",
        "phlegm",
        "dizzy",
        "dizziness",
        "nausea",
        "headache",
        "symptoms",
        "krank",
        "erkältet",
        "erkältung",
        "verschleimt",
        "fieber",
        "husten",
        "halsschmerzen",
        "schwindel",
    }
)

_TOOL_KEYWORDS: set[str] = {
    "activity",
    "activities",
    "workout",
    "workouts",
    "session",
    "sessions",
    "run",
    "ride",
    "swim",
    "pace",
    "speed",
    "heart rate",
    "hr",
    "sleep",
    "zones",
    "zone",
    "ftp",
    "lthr",
    "css",
    "threshold",
    "plan",
    "training plan",
    "intervals",
    "interval",
    "splits",
    "laps",
    "rep",
    "reps",
    "debrief",
    "calendar",
    "swap",
    "replace",
    "last week",
    "this week",
    "yesterday",
    "today",
    # Day words: "what's on tomorrow?" must read the plan, not reuse the
    # one session the "## Today" anchor names.
    "tomorrow",
    "tonight",
    "next week",
    "weekend",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "heute",
    "morgen",
    "gestern",
    "übermorgen",
    "wochenende",
    "race",
    "tss",
    "ctl",
    "atl",
    "fitness",
    "fatigue",
    "profile",
    "weight",
    "trends",
    "health",
    "wellness",
    "hrv",
    "stress",
    # Judgment questions. Ambient words ("form", "ready", "rest",
    # "eat") ride in anchored phrases so "the rest of the week" or "fill in
    # the form" can't trip the nudge.
    "my form",
    "readiness",
    "am i ready",
    "taper",
    "recover",
    "recovery",
    "rest day",
    "should i rest",
    "fatigued",
    "tired",
    "burnt out",
    "burnout",
    "fuel",
    "fueling",
    "nutrition",
    "what should i eat",
} | _PAUSE_KEYWORDS

_TOOL_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _TOOL_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

_PAUSE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _PAUSE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def requires_tools(user_message: str) -> bool:
    """Return whether the message likely needs data grounding from tools."""
    return bool(_TOOL_RE.search(user_message))


# Asked to see something: the coach model answered "show me my pace" from a
# text tool alone, so the turn says out loud that a chart is due.
# English and German, like the pause words.
_CHART_RE = re.compile(
    r"\b(?:plot(?:s|ted|ting)?|charts?|graphs?|visuali[sz]e|draw|show me"
    r"|diagramm?s?|grafik|zeig(?:e|en|t)?|darstell(?:en|ung)?)\b",
    re.IGNORECASE,
)

_CHART_NOTE = (
    "## 📈 Chart Requested\n"
    "The athlete asked to see this. Draw it before you write the reply, with "
    "`chart=true` on a tool that offers it or with `run_analysis` "
    "(`ana.chart`); the chart appears under it."
)


# An analysis that drew no chart: in live checks the coach model charted
# none of 8 trend and comparison answers unasked, and a note inside the tool
# result was skipped in 2 of 5 runs, so the turn itself asks.
_CHART_CHECK_NOTE = (
    "## 📈 Chart Check\n"
    "Your analysis drew no chart. If its numbers are a series or a "
    "comparison (by date, week, session, climb, lap or split), draw them "
    "with `run_analysis` (`ana.chart`, `ana.chart_route`) before you reply; "
    "the chart appears under it."
)


def _analysis_ran(result: str) -> bool:
    """Return whether an analysis result came back without an error."""
    try:
        parsed = json.loads(result)
    except ValueError:
        return False
    return isinstance(parsed, dict) and "output" in parsed and not parsed.get("error")


def asks_for_chart(user_message: str) -> bool:
    """Return whether the athlete asked to see, show or plot something."""
    return bool(_CHART_RE.search(user_message))


def nudge_profile(
    history: list[Message], user_message: str, *, tool_rounds: int
) -> bool:
    """Return whether this round may carry a profile-intake question.

    The nudge belongs to the opening conversation only: never mid-tool-loop
    (the athlete asked for data, not an interview), never on a turn where
    they report illness or injury, and never once the conversation is well
    under way.

    Args:
        history: Prior conversation messages.
        user_message: The athlete's message this turn.
        tool_rounds: Number of tool rounds already executed this turn.
    """
    return (
        sum(1 for message in history if message.get("role") == "assistant")
        < PROFILE_NUDGE_MAX_TURNS
        and tool_rounds == 0
        and not _PAUSE_RE.search(user_message)
    )


# Shipped when the tool budget runs out with the model still calling tools and
# nothing usable said — an honest dead end beats the generic empty-reply
# fallback below.
_TOOL_BUDGET_MESSAGE = (
    "I got stuck looking things up and ran out of steps before I could answer. "
    "Ask me again — one thing at a time works best."
)

# The last resort when a turn produced no words at all. The text the athlete
# reads is the text `session.py` persists — a turn whose history says "" is a
# turn the next one cannot see happened.
EMPTY_REPLY_MESSAGE = (
    "I couldn't put an answer together for that one. "
    "Try asking again in different words."
)

# Sent on the single re-ask after an empty final message on a turn that ran
# tools. The empty assistant message is dropped from the request first: a
# trailing finished assistant turn is exactly what makes the next round come
# back empty again (see `_build_llm_input`).
_EMPTY_REPLY_NOTE = (
    "## ⛔ Empty Reply\n"
    "Your last message came back empty. Tools already ran this turn and "
    "their results are above — write the reply to the athlete now, grounded "
    "in those results."
)


@dataclass
class TurnResult:
    """What one coaching turn produced.

    Attributes:
        reply: The coach's reply text (chips marker included).
        tools_used: The name of every tool called this turn, in call order.
        proposals: The Apply cards this turn's previews offer — at most one
            per tool and target (``proposals.card_key``), the last preview
            winning.
        plan_changed: Whether a tool wrote the plan or the calendar this
            turn (a preview never does).
        reason: ``"tool_budget_exhausted"`` or ``"completed_write_relayed"``
            when the reply is a deterministic notice rather than the coach's
            own words, else ``""``.
        charts: The chart specs this turn's analyses attached, at most
            :data:`MAX_TURN_CHARTS`, drawn under the reply.
    """

    reply: str
    tools_used: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    plan_changed: bool = False
    reason: str = ""
    charts: list[dict[str, Any]] = field(default_factory=list)


#: The most charts one reply carries.
MAX_TURN_CHARTS = 3
#: The analysis protocol's marker: the analysis is done, ask for the reply.
DONE_MARKER = "DONE"
#: Text beside a tool call longer than this is a drafted reply.
DRAFT_CHARS = 200
#: The round that writes an analysis turn's reply, offered no tools.
WRITE_REPLY_NOTE: Message = {
    "role": "user",
    "content": (
        "(Analysis done. Write your whole reply to the athlete now, from the "
        "results above; it replaces anything you wrote before, which they "
        "have not seen.)"
    ),
}


def _said_done(assistant: Message) -> bool:
    """Whether the model closed its analysis with the DONE marker."""
    text = str(assistant.get("content") or "").strip().lstrip("*").strip()
    return not has_tool_calls(assistant) and text.upper().startswith(DONE_MARKER)


def _drafted_beside_calls(turn_log: list[Message]) -> bool:
    """Whether the model wrote a reply-sized text beside a tool call this turn."""
    return any(
        message.get("role") == "assistant"
        and has_tool_calls(message)
        and len(str(message.get("content") or "").strip()) > DRAFT_CHARS
        for message in turn_log
    )


def _chart_note(asked: bool, analysed: bool, charts: list[dict[str, Any]]) -> str:
    """The chart note for the next round: asked for, or after a chartless analysis."""
    if charts:
        return ""
    if asked:
        return _CHART_NOTE
    return _CHART_CHECK_NOTE if analysed else ""


def _merge_charts(
    charts: list[dict[str, Any]], new: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Add a script's charts; a redrawn title replaces the older chart."""
    titles = {chart["title"] for chart in new}
    kept = [chart for chart in charts if chart["title"] not in titles] + new
    return kept[-MAX_TURN_CHARTS:]


# System context assembly


def _build_system_message(
    runtime: CoachRuntime,
    turn_messages: list[Message],
    *,
    tool_rounds: int,
    requires_tools: bool,
    nudge_profile: bool = True,
    empty_reply: bool = False,
    chart_note: str = "",
) -> Message:
    """Assemble the system prompt for one LLM round.

    Args:
        runtime: The coach runtime (context, tools, repos).
        turn_messages: Messages created so far this turn (for the tool summary).
        tool_rounds: Number of tool rounds already executed this turn.
        requires_tools: Whether the turn was classified as needing grounding.
        nudge_profile: Whether this round may carry a profile-intake question.
        empty_reply: Whether this round is the single re-ask after the model
            ended a tool-running turn with an empty message.
        chart_note: The note asking for a chart this round, if any: the
            athlete asked to see something, or an analysis drew none.
    """
    config = runtime.config
    # The athlete-memory document is read fresh every turn (a one-row SQLite
    # read, like the plan anchor) so an update_memory rewrite — or an edit the
    # athlete made in the UI — is visible on the very next round.
    memory = runtime.memory_repo.get()
    context = build_system_context(
        config,
        runtime.athlete_profile,
        memory.content,
        recent_health=runtime.recent_health_summary,
        form=runtime.form_summary,
        memory_written=(
            date.fromisoformat(memory.updated_at[:10]) if memory.updated_at else None
        ),
        active_plan_summary=(
            runtime.active_plan_summary or build_no_plan_note(runtime.repos)
        ),
        macro_plan_summary=runtime.macro_plan_summary,
        plan_anchor=build_plan_anchor(runtime.repos),
        latest_session=runtime.latest_session_summary,
        scheduled_workouts=runtime.scheduled_workouts_summary,
        races=build_races_section(runtime.repos),
        sport_doctrine=build_sport_doctrine(runtime.repos),
        nudge_profile=nudge_profile,
        experience_fallback=build_experience_fallback(runtime.repos),
    )
    sections = [context]

    if tool_rounds >= config.max_tool_rounds:
        # The last round the athlete will see: further tool calls are
        # dropped, so demand prose now instead of letting the turn end on
        # an unanswerable tool call.
        sections.append(
            "## ⛔ No Tool Budget Left\n"
            "The tool budget for this turn is spent. Answer the athlete "
            "now, using only what you already have. Do NOT call any more "
            "tools — further tool calls are dropped and the athlete gets "
            "no reply at all."
        )
    elif tool_rounds > 0:
        sections.append(
            "## 🔍 Tool Loop Note\n"
            "You are currently in a tool-calling loop. "
            "If you are still missing information to answer the athlete's "
            "request, call another tool. Do NOT ask the athlete for "
            "information that a tool could provide."
        )

    if empty_reply:
        sections.append(_EMPTY_REPLY_NOTE)

    if chart_note:
        sections.append(chart_note)

    if requires_tools:
        sections.append(
            "## 🔧 Tool Requirement\n"
            "This turn requires data grounding from tools before final advice. "
            "Call relevant tools when needed and avoid unsupported factual claims."
        )

    tool_summary = build_tool_summary(turn_messages)
    if tool_summary:
        sections.append(tool_summary)

    lang_code = (
        runtime.athlete_profile.language if runtime.athlete_profile else None
    ) or "en"
    if lang_code != "en":
        lang_reminder = _LANGUAGE_INSTRUCTIONS.get(lang_code, "")
        if lang_reminder:
            sections.append(
                "## ⚠️ LANGUAGE REMINDER\n"
                f"{lang_reminder} Your response MUST be entirely in this language."
            )

    # Sent as two text parts that read as the one prompt they always were.
    # The context block is identical on every round of a turn, so it carries
    # the cache breakpoint — through OpenRouter, Gemini's prompt cache only
    # engages behind an explicit one. The per-round notes stay outside it, or
    # each round would bust the cached prefix.
    parts: list[dict[str, Any]] = [
        {"type": "text", "text": context, "cache_control": {"type": "ephemeral"}}
    ]
    if len(sections) > 1:
        parts.append({"type": "text", "text": "\n\n" + "\n\n".join(sections[1:])})
    return {"role": "system", "content": parts}


def _build_llm_input(
    runtime: CoachRuntime,
    history: list[Message],
    turn_messages: list[Message],
    *,
    tool_rounds: int,
    requires_tools: bool,
    empty_reply: bool = False,
    chart_note: str = "",
) -> list[Message]:
    """Build the full message list (system + windowed history) for one LLM round.

    The re-ask after an empty reply drops the empty assistant message at its
    call site: left in place it is the trailing message of the request, which
    asks the model to continue a turn it already considers finished — the
    shape that makes the next round come back empty again.
    """
    user_text = str(turn_messages[0].get("content") or "")
    system_msg = _build_system_message(
        runtime,
        turn_messages,
        tool_rounds=tool_rounds,
        requires_tools=requires_tools,
        nudge_profile=nudge_profile(history, user_text, tool_rounds=tool_rounds),
        empty_reply=empty_reply,
        chart_note=chart_note,
    )
    # The budget covers PRIOR conversation only. Windowed together with this
    # turn, a tool-heavy round (one assistant message plus a result per call,
    # up to max_tool_rounds) would evict the very conversation it is about.
    windowed = (
        select_recent_messages(
            history,
            runtime.config.max_message_history_messages,
        )
        + turn_messages
    )
    windowed = prepare_messages_for_llm(prune_tool_history(windowed))
    # The one place the LLM input is assembled, so the one place the private
    # `sent_at` key is rendered into the content and dropped.
    return [system_msg, *stamp_user_messages(windowed)]


# The turn loop


def _tool_result_texts(turn_log: list[Message]) -> list[str]:
    """Collect this turn's raw tool payloads.

    Args:
        turn_log: Every message created this turn.

    Returns:
        The content of each tool message, in call order.
    """
    return [str(m.get("content") or "") for m in turn_log if m.get("role") == "tool"]


def _completed_write_message(turn_log: list[Message]) -> str:
    """Return the last completed write's own message from this turn.

    What an empty reply after a landed write may ship instead of asking the
    athlete to send the request again. A preview and a refusal both report
    something other than a completed write and are skipped.

    Args:
        turn_log: Every message created this turn.

    Returns:
        The ``message`` of the last tool payload that is a JSON object with
        ``status`` "ok" and a non-empty ``message``, else ``""``.
    """
    message = ""
    for raw in _tool_result_texts(turn_log):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if parsed.get("status") == "ok" and isinstance(parsed.get("message"), str):
            message = parsed["message"].strip() or message
    return message


def _last_prose(
    turn_log: list[Message], tool_parameters: dict[str, frozenset[str]]
) -> str:
    """Return the last meaningful thing the model said this turn, else ``""``.

    The prose the model wrote alongside a tool call counts: a final message
    that came back empty after "Easy 30 minutes then." still has a reply.

    Args:
        turn_log: Every message created this turn.
        tool_parameters: Every registered tool's name mapped to its parameter
            names, used to discount a leaked tool call as "text".
    """
    for message in reversed(turn_log):
        if message.get("role") != "assistant":
            continue
        text = strip_leaked_tool_calls(
            str(message.get("content") or ""), tool_parameters
        ).strip()
        if is_meaningful_response_text(text):
            return text
    return ""


def _with_built_outline(prose: str, turn_log: list[Message]) -> str:
    """Append the workout outline a tool built this turn, unless already quoted.

    Prose recovered from a tool round is often a lead-in ("Here is a
    60-minute ride."); the workout it introduces is the newest ``outline``
    a tool returned.

    Args:
        prose: The recovered reply.
        turn_log: Every message created this turn.
    """
    for message in reversed(turn_log):
        if message.get("role") != "tool":
            continue
        try:
            outline = json.loads(str(message.get("content") or "")).get("outline")
        except (ValueError, AttributeError):
            continue
        if isinstance(outline, str) and outline:
            return prose if outline in prose else f"{prose}\n\n{outline}"
    return prose


def _tool_parameter_names(
    tools_wire: list[dict[str, Any]],
) -> dict[str, frozenset[str]]:
    """Map every registered tool's name to its parameter names.

    What the leak stripper needs to recognise a tool call the model typed
    out as text instead of calling.

    Args:
        tools_wire: This turn's tool registry in the OpenAI wire schema.

    Returns:
        One entry per tool: its name mapped to its parameter names.
    """
    return {
        tool["function"]["name"]: frozenset(
            tool["function"]["parameters"]["properties"]
        )
        for tool in tools_wire
    }


def _parse_call_args(call: dict[str, Any]) -> dict[str, Any]:
    """Return one tool call's arguments, or ``{}`` when they are not JSON."""
    try:
        args = json.loads(call["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


def _has_active_plan(runtime: CoachRuntime) -> bool:
    """Return whether the athlete has an active plan; True when unreadable.

    An unreadable plan keeps every tool on offer: a plan tool that refuses
    costs a round, a missing one costs the athlete their plan edits.
    """
    try:
        return runtime.repos.plan.get_active() is not None
    except Exception:
        logger.debug("Could not read the active plan for the tool list", exc_info=True)
        return True


class ModelCallError(EnduryError):
    """The AI model's service failed a call; the message is its reason."""


async def _ask_model(
    llm: OpenRouterClient, messages: list[Message], tools: list[dict[str, Any]] | None
) -> Message:
    """Call the AI model, raising its failure as a :class:`ModelCallError`.

    The athlete reads the reason (a rejected key, no credit), so it must not
    pass for a crash of Endury's own.
    """
    try:
        return await llm.complete(messages, tools=tools)
    except Exception as exc:
        raise ModelCallError(str(exc)) from exc


async def run_turn(
    runtime: CoachRuntime,
    history: list[Message],
    user_message: str,
    *,
    llm: OpenRouterClient,
    turn_log: list[Message],
) -> TurnResult:
    """Drive one coaching turn.

    Args:
        runtime: The coach runtime (context, tools, repos).
        history: Prior conversation messages (text turns, plus a leading welcome).
        user_message: The athlete's message for this turn.
        llm: The LLM client used for every round of this turn.
        turn_log: Mutable list; every message created this turn (user, assistant,
            tool) is appended for the finalizer to inspect — even when the
            turn raises part-way.

    Returns:
        The turn's reply, tools, proposals and plan-changed flag.
    """
    config = runtime.config
    # A preview never creates a plan, so the offer holds for the whole turn.
    tools_wire = to_openai_tools(
        offered_tools(runtime.tools, has_plan=_has_active_plan(runtime))
    )
    # Every registered tool, offered or not: a leaked call is a leak either way.
    tool_parameters = _tool_parameter_names(to_openai_tools(runtime.tools))
    needs_tools = requires_tools(user_message)
    chart_asked = asks_for_chart(user_message) and any(
        tool.name == ANALYSIS_TOOL for tool in runtime.tools
    )

    # Stamped like every replayed message, so this turn's stamp sits next to
    # the previous one and a day change between them is unmissable.
    user_msg: Message = {
        "role": "user",
        "content": user_message,
        SENT_AT_KEY: clock.now().isoformat(),
    }
    turn_log.append(user_msg)

    tool_rounds = 0
    tools_used: list[str] = []
    charts: list[dict[str, Any]] = []
    analysed = False
    proposals: dict[tuple[str, Any], Proposal] = {}
    plan_changed = False
    tool_budget_spent = False
    done_reason = ""
    empty_reply = False
    empty_reply_reasked = False
    assistant: Message = {"role": "assistant", "content": ""}

    while True:
        llm_input = _build_llm_input(
            runtime,
            history,
            turn_log,
            tool_rounds=tool_rounds,
            requires_tools=needs_tools,
            empty_reply=empty_reply,
            chart_note=_chart_note(chart_asked, analysed, charts),
        )
        # The note belongs to the re-ask round alone.
        empty_reply = False
        assistant = await _ask_model(llm, llm_input, tools_wire)
        turn_log.append(assistant)

        if has_tool_calls(assistant):
            if tool_rounds >= config.max_tool_rounds:
                tool_budget_spent = True
                break
            for call in assistant["tool_calls"]:
                name = call["function"]["name"]
                # The model may say "tomorrow" for a date; resolving it here
                # also keeps a card from applying it a day late.
                args = resolve_date_args(_parse_call_args(call), clock.today())
                tool = find_tool(runtime.tools, name)
                if tool is not None and tool.is_async:
                    # The analysis awaits its sandbox; it previews nothing.
                    result = await execute_tool_async(runtime.tools, name, args)
                else:
                    # Tool bodies are synchronous and can be CPU/IO-heavy
                    # (intervals.icu reads, the replan QP solve) — run them
                    # off the event loop so other requests stay responsive.
                    result = await asyncio.to_thread(
                        run_tool, runtime.tools, name, args
                    )
                # Any tool may draw a chart under the reply.
                if name == ANALYSIS_TOOL:
                    analysed = analysed or _analysis_ran(result)
                result, new_charts = take_charts(result)
                charts = _merge_charts(charts, new_charts)
                result = label_dates(result, clock.today())
                tools_used.append(name)
                turn_log.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": with_reply_contract(tool, result),
                    }
                )
                proposal = proposal_from_result(
                    runtime.tools, runtime.repos, name, args, result
                )
                if proposal is not None:
                    # Only the last preview per tool and target stands: the
                    # model may refine its arguments across rounds, and the
                    # athlete gets one card for each thing it changes.
                    proposals[card_key(name, args)] = proposal
                if name in PLAN_WRITE_TOOLS and tool is not None and not is_gated(tool):
                    plan_changed = True
            tool_rounds += 1
            continue
        if (
            not empty_reply_reasked
            and not _last_prose(turn_log, tool_parameters)
            and any(message.get("role") == "tool" for message in turn_log)
        ):
            # Tools ran — one of them may have written the plan — and the
            # model said nothing. Ask once more, without the empty message
            # trailing the request, before falling back to a notice.
            logger.warning(
                "Empty reply after %d tool round(s); re-asking once",
                tool_rounds,
            )
            empty_reply = True
            empty_reply_reasked = True
            turn_log.pop()
            continue
        break

    if ANALYSIS_TOOL in tools_used and (
        _said_done(assistant) or _drafted_beside_calls(turn_log)
    ):
        # An analysis turn writes its reply in a round of its own: the model
        # wrote answers beside its tool calls and then closed on "my earlier
        # answer stands" or a correction to a message the athlete never saw
        # (3 of 16 smoke turns, with DONE asked for).
        llm_input = _build_llm_input(
            runtime,
            history,
            turn_log,
            tool_rounds=tool_rounds,
            requires_tools=needs_tools,
            empty_reply=False,
        )
        assistant = await _ask_model(llm, [*llm_input, WRITE_REPLY_NOTE], None)
        turn_log.append(assistant)

    # Whatever ships, ships without a tool call written out as text.
    draft = str(assistant.get("content", ""))
    response_text = clean_coach_response(
        strip_leaked_tool_calls(draft, tool_parameters)
    )
    if not is_meaningful_response_text(response_text):
        # The final message came back empty (e.g. the model said everything
        # in a pre-tool round): recover the prose it did produce.
        prose = _last_prose(turn_log, tool_parameters)
        if prose:
            logger.warning(
                "Final message %r not meaningful; recovering earlier prose",
                response_text,
            )
            response_text = _with_built_outline(prose, turn_log)
        elif tool_budget_spent:
            logger.warning(
                "Tool budget (%d rounds) spent with the model still calling "
                "tools; shipping the budget notice",
                config.max_tool_rounds,
            )
            response_text = _TOOL_BUDGET_MESSAGE
            done_reason = "tool_budget_exhausted"
        else:
            # Still nothing after the re-ask. A write that landed speaks for
            # itself; asking the athlete to send the request again would ask
            # them to make it happen twice.
            write_message = _completed_write_message(turn_log)
            logger.warning(
                "Turn produced no reply [write_relayed=%s]",
                bool(write_message),
            )
            if write_message:
                response_text = write_message
                done_reason = "completed_write_relayed"
            else:
                response_text = EMPTY_REPLY_MESSAGE
    elif not is_complete_reply(response_text):
        # The completion was cut off mid-sentence (max_tokens); nothing
        # whole to fall back on, so it ships with a trace in the log.
        logger.warning(
            "Reply ends mid-sentence; shipping %r",
            response_text[-60:],
        )
    return TurnResult(
        reply=response_text,
        tools_used=tools_used,
        proposals=list(proposals.values()),
        plan_changed=plan_changed,
        reason=done_reason,
        charts=charts,
    )
