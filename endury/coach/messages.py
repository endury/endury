"""Message helpers for the coach turn loop.

All coach messages are OpenAI chat-completions dicts:

* ``{"role": "user"|"assistant", "content": str}``
* the turn's system message: ``{"role": "system", "content": [text parts]}``,
  the first part carrying the prompt-cache breakpoint
* assistant tool calls: ``{"role": "assistant", "content": str,
  "tool_calls": [{"id", "type": "function", "function": {"name", "arguments"}}]}``
* tool results: ``{"role": "tool", "tool_call_id": str, "name": str,
  "content": str}``

A user or assistant message may also carry a private ``sent_at`` key. It is
app-internal and must never reach the LLM API: :func:`stamp_user_messages`
renders it into the content as a ``[weekday date time]`` prefix and drops the
key, on the way out to the model only.

This module holds the windowing, pruning, stamping, and tool-summary logic,
operating directly on those dicts.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

# Constants

TOOL_SUMMARY_BUDGET = 12
MAX_TOOL_SUMMARY_CHARS = 1200
MAX_MESSAGE_HISTORY = 100

#: Private key carrying when a message was sent. It never reaches
#: the LLM API — :func:`stamp_user_messages` renders and strips it.
SENT_AT_KEY = "sent_at"
#: The same format the system context's "Current date and time" line uses,
#: so a stamp and "now" read as one clock.
SENT_AT_FORMAT = "%A %Y-%m-%d %H:%M"

Message = dict[str, Any]


# Small shared helpers


def has_tool_calls(msg: Message) -> bool:
    """Return whether an assistant message carries tool calls."""
    return bool(msg.get("tool_calls"))


def dedupe_lines(lines: list[str]) -> list[str]:
    """Remove duplicate lines (case-insensitive) while preserving order."""
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        key = line.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(line)
    return deduped


def find_last_by_role(
    messages: list[Message],
    role: str,
    *,
    exclude_tool_calls: bool = False,
) -> Message | None:
    """Return the last message with *role*, scanning backwards.

    Args:
        messages: Message list to search.
        role: The role to match (``"user"``, ``"assistant"``, ``"tool"``).
        exclude_tool_calls: If True, skip assistant messages carrying tool calls.

    Returns:
        The last matching message, or None.
    """
    for msg in reversed(messages):
        if msg.get("role") != role:
            continue
        if exclude_tool_calls and has_tool_calls(msg):
            continue
        return msg
    return None


# Message windowing and pruning


def select_recent_messages(messages: list[Message], max_messages: int) -> list[Message]:
    """Return the last *max_messages* messages, preserving tool call/result pairing."""
    if max_messages <= 0:
        return []
    start = max(len(messages) - max_messages, 0)

    changed = True
    while changed:
        changed = False
        tool_indices = [
            idx
            for idx in range(start, len(messages))
            if messages[idx].get("role") == "tool"
        ]
        if not tool_indices:
            break
        earliest_tool_index = tool_indices[0]
        paired_index = None
        for idx in range(earliest_tool_index - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") == "assistant" and has_tool_calls(msg):
                paired_index = idx
                break
        if paired_index is not None and paired_index < start:
            start = paired_index
            changed = True

    return messages[start:]


def _tool_call_ids(msg: Message) -> list[str]:
    """Return the tool-call ids attached to an assistant message."""
    ids: list[str] = []
    for call in msg.get("tool_calls") or []:
        call_id = call.get("id")
        if call_id and isinstance(call_id, str):
            ids.append(call_id)
    return ids


def prune_tool_history(messages: list[Message]) -> list[Message]:
    """Remove mismatched tool call/result entries to keep history consistent."""
    pruned: list[Message] = []
    idx = 0
    while idx < len(messages):
        msg = messages[idx]
        if msg.get("role") == "assistant" and has_tool_calls(msg):
            tool_ids = _tool_call_ids(msg)
            if not tool_ids:
                idx += 1
                continue
            remaining = set(tool_ids)
            matched: list[Message] = []
            look_ahead = idx + 1
            while look_ahead < len(messages) and remaining:
                next_msg = messages[look_ahead]
                if next_msg.get("role") != "tool":
                    break
                tool_id = next_msg.get("tool_call_id")
                if tool_id in remaining:
                    matched.append(next_msg)
                    remaining.remove(tool_id)
                look_ahead += 1
            if remaining:
                idx += 1
                continue
            pruned.append(msg)
            pruned.extend(matched)
            idx = look_ahead
            continue
        if msg.get("role") == "tool":
            idx += 1
            continue
        pruned.append(msg)
        idx += 1
    return pruned


def prepare_messages_for_llm(recent_messages: list[Message]) -> list[Message]:
    """Prepare message history for LLM input.

    * If the last message is a **tool** result: keep every completed prior
      turn's user/plain-assistant text, plus the *entire* in-progress current
      turn — every tool call/result round since the last plain message, not
      just the most recent one. Pruning drops stale tool residue across
      turns; it never drops a tool round from within the current turn.
    * If the last message is a **user** message: strip orphaned tool messages
      and tool-call assistant messages that would confuse the LLM.

    Otherwise the list is returned unchanged.

    Args:
        recent_messages: The windowed, pruned message list.

    Returns:
        A possibly-modified message list ready to be prepended with a system
        prompt and sent to the LLM.
    """
    if not recent_messages:
        return recent_messages

    last_role = recent_messages[-1].get("role")

    if last_role == "tool":
        context_messages: list[Message] = [
            msg
            for msg in recent_messages
            if msg.get("role") == "user"
            or (msg.get("role") == "assistant" and not has_tool_calls(msg))
        ]

        boundary = -1
        for idx, msg in enumerate(recent_messages):
            if msg.get("role") == "user" or (
                msg.get("role") == "assistant" and not has_tool_calls(msg)
            ):
                boundary = idx
        current_turn = prune_tool_history(recent_messages[boundary + 1 :])

        return context_messages + current_turn

    if last_role == "user":
        return [
            msg
            for msg in recent_messages
            if msg.get("role") != "tool" and not has_tool_calls(msg)
        ]

    return recent_messages


def stamp_user_messages(messages: list[Message]) -> list[Message]:
    """Prefix each replayed message with when it was actually sent.

    The window spans days, so "tomorrow" said last night — by the athlete or
    by the coach — is read as still tomorrow unless the model is told when it
    was said. The stamp is
    rendered here, at the LLM boundary alone — stored content and
    :attr:`CoachSession.history` stay raw, because the finalizer parses the
    athlete's own words and the UI shows them.

    Args:
        messages: The windowed message list. A message carrying the private
            ``sent_at`` key (a naive local ISO timestamp) is stamped;
            everything else passes through.

    Returns:
        A new list. Stamped messages are copies without ``sent_at`` — the
        input dicts live in session history and are never mutated.
    """
    stamped: list[Message] = []
    for msg in messages:
        sent_at = msg.get(SENT_AT_KEY)
        if not sent_at:
            stamped.append(msg)
            continue
        copy = {key: value for key, value in msg.items() if key != SENT_AT_KEY}
        when = datetime.fromisoformat(str(sent_at)).strftime(SENT_AT_FORMAT)
        copy["content"] = f"[{when}] {msg.get('content') or ''}"
        stamped.append(copy)
    return stamped


# Tool summary registry


def _parse_tool_json(content: str) -> dict[str, Any] | list[Any] | None:
    """Try to parse a tool message's content as JSON, returning None on failure."""
    try:
        result: dict[str, Any] | list[Any] = json.loads(content)
        return result
    except Exception:
        return None


def _summarize_get_recent_activities(name: str, parsed: Any) -> str | None:
    """Summarize get_recent_activities result."""
    if not isinstance(parsed, dict):
        return None
    return (
        f"get_recent_activities: {parsed.get('count')} activities "
        f"({parsed.get('start_date')}..{parsed.get('end_date')})."
    )


def _summarize_get_activity_detail(name: str, parsed: Any) -> str | None:
    """Summarize get_activity_detail result (single view or a date's list)."""
    if not isinstance(parsed, dict):
        return None
    views = parsed.get("activities") if "activities" in parsed else [parsed]
    if not isinstance(views, list):
        return None
    parts = [
        f"{v.get('date', '?')} {v.get('sport', '?')} "
        f"({len(v.get('intervals') or [])} intervals)"
        for v in views
        if isinstance(v, dict) and (v.get("activity_id") or v.get("intervals"))
    ]
    return f"get_activity_detail: {'; '.join(parts) or 'no activity found'}."


def _summarize_get_weekly_training_summary(name: str, parsed: Any) -> str | None:
    """Summarize get_weekly_training_summary result."""
    if not isinstance(parsed, dict):
        return None
    weeks = parsed.get("weeks")
    if not isinstance(weeks, list):
        return None
    hours = sum(float(w.get("hours") or 0.0) for w in weeks if isinstance(w, dict))
    return f"get_weekly_training_summary: {len(weeks)} weeks, {round(hours, 1)}h total."


def _summarize_get_athlete_thresholds(name: str, parsed: Any) -> str | None:
    """Summarize get_athlete_thresholds result."""
    if not isinstance(parsed, list):
        return None
    sports = [str(entry.get("sport")) for entry in parsed if isinstance(entry, dict)]
    return (
        f"get_athlete_thresholds: {len(parsed)} sport(s) "
        f"({', '.join(sports) or 'n/a'})."
    )


def _summarize_set_athlete_thresholds(name: str, parsed: Any) -> str | None:
    """Summarize set_athlete_thresholds result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"set_athlete_thresholds: {status} — {parsed.get('message')}"
    verb = "would set" if status == "preview" else "set"
    changes = ", ".join(
        f"{change.get('label')} {change.get('current')} -> {change.get('proposed')}"
        for change in parsed.get("changes") or []
    )
    return f"set_athlete_thresholds: {verb} {parsed.get('sport')} {changes}."


def _summarize_estimate_threshold_from_race(name: str, parsed: Any) -> str | None:
    """Summarize estimate_threshold_from_race result."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") != "ok":
        return f"estimate_threshold_from_race: {parsed.get('message')}"
    return (
        "estimate_threshold_from_race: threshold pace "
        f"{parsed.get('threshold_pace_min_km')}/km."
    )


def _summarize_update_memory(name: str, parsed: Any) -> str | None:
    """Summarize update_memory result."""
    if not isinstance(parsed, dict):
        return None
    if not parsed.get("success"):
        return f"update_memory: rejected — {parsed.get('message')}"
    return f"update_memory: document rewritten ({parsed.get('characters')} chars)."


def _summarize_get_planned_sessions(name: str, parsed: Any) -> str | None:
    """Summarize get_planned_sessions result."""
    if not isinstance(parsed, dict):
        return None
    sessions = parsed.get("sessions")
    if sessions is None:
        return None
    plan = parsed.get("plan", "?")
    return f"get_planned_sessions: {len(sessions)} sessions in plan '{plan}'."


def _summarize_get_active_plan_overview(name: str, parsed: Any) -> str | None:
    """Summarize get_active_plan_overview result."""
    if not isinstance(parsed, dict) or not parsed.get("name"):
        return None
    return (
        f"get_active_plan_overview: {parsed.get('name')} "
        f"(goal {parsed.get('goal_type')}, {parsed.get('goal_date')})."
    )


def _summarize_get_session_menu_catalog(name: str, parsed: Any) -> str | None:
    """Summarize get_session_menu_catalog result."""
    if not isinstance(parsed, dict):
        return None
    phases = parsed.get("phases")
    if phases is None:
        return None
    return f"get_session_menu_catalog: {parsed.get('goal', '?')}, {len(phases)} phases."


def _summarize_get_training_load_status(name: str, parsed: Any) -> str | None:
    """Summarize get_training_load_status result."""
    if not isinstance(parsed, dict):
        return None
    current = parsed.get("current") or {}
    return (
        f"get_training_load_status: fitness={current.get('ctl')} "
        f"fatigue={current.get('atl')} form={current.get('tsb')}."
    )


def _summarize_recommend_todays_session(name: str, parsed: Any) -> str | None:
    """Summarize recommend_todays_session result."""
    if not isinstance(parsed, dict):
        return None
    tsb = (parsed.get("signals") or {}).get("tsb")
    return (
        f"recommend_todays_session({parsed.get('date')}): "
        f"verdict={parsed.get('verdict')} (form={tsb})."
    )


def _summarize_get_wellness_trend(name: str, parsed: Any) -> str | None:
    """Summarize get_wellness_trend result."""
    if not isinstance(parsed, dict) or not isinstance(parsed.get("metrics"), dict):
        return None
    labels = ", ".join(
        f"{metric} {entry.get('trend')}"
        for metric, entry in parsed["metrics"].items()
        if isinstance(entry, dict)
    )
    return f"get_wellness_trend: {labels or 'no wellness data'}."


def _summarize_predict_race_time(name: str, parsed: Any) -> str | None:
    """Summarize predict_race_time result."""
    if not isinstance(parsed, dict):
        return None
    if "predictions" not in parsed:
        return f"predict_race_time: {parsed.get('message')}"
    basis = parsed.get("basis") or {}
    rows = [parsed["target"]] if parsed.get("target") else parsed["predictions"]
    times = ", ".join(
        f"{row.get('distance')} {row.get('predicted_time')} "
        f"({row.get('confidence', '').split(' —')[0]} confidence)"
        for row in rows
        if isinstance(row, dict)
    )
    return (
        f"predict_race_time: from {basis.get('distance_m')} m in "
        f"{basis.get('time')} — {times}."
    )


def _summarize_get_plan_compliance(name: str, parsed: Any) -> str | None:
    """Summarize get_plan_compliance result."""
    if not isinstance(parsed, dict):
        return None
    return (
        f"get_plan_compliance: {parsed.get('completed')}/"
        f"{parsed.get('total_workouts')} completed "
        f"({parsed.get('completion_rate_pct')}%), planned "
        f"{parsed.get('planned_tss')} load vs delivered "
        f"{parsed.get('delivered_tss')} load."
    )


def _summarize_get_session_vs_actual(name: str, parsed: Any) -> str | None:
    """Summarize get_session_vs_actual result."""
    if not isinstance(parsed, dict):
        return None
    sessions = parsed.get("sessions") or []
    matched = sum(1 for s in sessions if isinstance(s, dict) and s.get("delivered"))
    return (
        f"get_session_vs_actual: {parsed.get('date', '?')}, "
        f"{matched}/{len(sessions)} session(s) matched to actual."
    )


def _summarize_get_race_day_outlook(name: str, parsed: Any) -> str | None:
    """Summarize get_race_day_outlook result."""
    if not isinstance(parsed, dict):
        return None
    tsb_points = parsed.get("aggregate_tsb") or []
    ctl_points = parsed.get("aggregate_ctl") or []
    race_tsb = tsb_points[-1].get("value") if tsb_points else None
    race_ctl = ctl_points[-1].get("value") if ctl_points else None
    return (
        f"get_race_day_outlook: {parsed.get('goal_date', '?')} — "
        f"projected fitness={race_ctl}, form={race_tsb}."
    )


def _summarize_get_brief(name: str, parsed: Any) -> str | None:
    """Summarize get_brief result."""
    if not isinstance(parsed, dict):
        return None
    subject = parsed.get("subject")
    if not isinstance(subject, dict):
        return f"get_brief: {parsed.get('message', 'nothing to brief')}"
    return (
        f"get_brief: {subject.get('title') or subject.get('handle')}, "
        f"{subject.get('date', '?')} — sheet read."
    )


def _summarize_set_session_status(name: str, parsed: Any) -> str | None:
    """Summarize set_session_status result."""
    if not isinstance(parsed, dict):
        return None
    return (
        f"set_session_status: session {parsed.get('session_id')} -> "
        f"{parsed.get('new_status')}."
    )


def _summarize_skip_planned_session(name: str, parsed: Any) -> str | None:
    """Summarize skip_planned_session result: a card, a skip or a refusal."""
    if not isinstance(parsed, dict):
        return None
    session = parsed.get("session") or {}
    return (
        f"skip_planned_session: session {session.get('session_id')} "
        f"{session.get('date')} -> {parsed.get('status')}."
    )


def _summarize_move_planned_session(name: str, parsed: Any) -> str | None:
    """Summarize move_planned_session result."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") != "ok":
        return f"move_planned_session: refused — {parsed.get('message')}"
    warning = parsed.get("warning")
    line = (
        f"move_planned_session: session {parsed.get('session_id')} -> "
        f"{parsed.get('date')}."
    )
    return f"{line} Warning: {warning}" if warning else line


def _summarize_replan_active_plan(name: str, parsed: Any) -> str | None:
    """Summarize replan_active_plan result."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") == "preview":
        edited = len(parsed.get("edited_workouts") or [])
        note = f", overwriting {edited} edited workout(s)" if edited else ""
        return (
            f"replan_active_plan: would regenerate from next Monday to race day{note}."
        )
    if parsed.get("status") == "error":
        # A curated run_replan reason, relayed verbatim by the tool.
        return f"replan_active_plan: error — {parsed.get('message')}"
    return f"replan_active_plan: {parsed.get('status')} — {parsed.get('action')}."


def _summarize_adjust_current_week(name: str, parsed: Any) -> str | None:
    """Summarize adjust_current_week result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"adjust_current_week: {status} — {parsed.get('message')}"
    totals = parsed.get("totals") or {}
    verb = "would trim" if status == "preview" else "trimmed"
    return (
        f"adjust_current_week: {verb} {len(parsed.get('sessions') or [])} session(s), "
        f"{totals.get('before_minutes')} -> {totals.get('after_minutes')} min "
        f"({totals.get('actual_percent')}% off)."
    )


def _summarize_pause_training(name: str, parsed: Any) -> str | None:
    """Summarize pause_training result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status == "empty" and not parsed.get("no_plan"):
        # Nothing to skip and nothing booked — but with an active plan an
        # applied pause still records the window as unavailable, which is
        # what the card offers until it is applied.
        line = f"pause_training: {parsed.get('message')}"
        if parsed.get("confirm_required") is not False:
            line += " Applying records the window as unavailable."
        return line
    if status not in ("preview", "ok"):
        return f"pause_training: {status} — {parsed.get('message')}"
    sessions = parsed.get("sessions") or []
    if not sessions:
        # Booking-only outcome: no plan session was touched, so nothing was
        # replanned either — a "resumes X" would claim a replan that never
        # happened. Name the bookings instead: with an active plan they sit
        # in the sweep's own block, a plan-less pause reports them flat.
        ad_hoc = parsed.get("ad_hoc") or {}
        workouts = ad_hoc.get("workouts") or parsed.get("workouts") or []
        names = ", ".join(f"{w.get('date')} {w.get('name')}" for w in workouts)
        verb = "would remove" if status == "preview" else "removed"
        return f"pause_training: {verb} {len(workouts)} booking(s): {names}."
    verb = "would skip" if status == "preview" else "skipped"
    window = parsed.get("window") or {}
    return (
        f"pause_training: {verb} {len(sessions)} session(s) "
        f"{window.get('from')} through {window.get('through')}; resumes "
        f"{parsed.get('resume_date')}."
    )


def _summarize_swap_planned_session(name: str, parsed: Any) -> str | None:
    """Summarize swap_planned_session result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"swap_planned_session: {status} — {parsed.get('message')}"
    current = parsed.get("current") or {}
    replacement = parsed.get("replacement") or {}
    week = parsed.get("week") or {}
    verb = "would replace" if status == "preview" else "replaced"
    return (
        f"swap_planned_session: {verb} {current.get('activity_key')} on "
        f"{parsed.get('date')} with {replacement.get('workout_name')} "
        f"({replacement.get('duration_minutes')} min, "
        f"~{replacement.get('achieved_tss')} load); week "
        f"{week.get('before_tss')} -> {week.get('after_tss')} load "
        f"({week.get('tss_delta_percent')}%)."
    )


def _summarize_schedule_workout(name: str, parsed: Any) -> str | None:
    """Summarize a schedule_workout or schedule_workout_override result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status != "ok":
        return f"{name}: {status} — {parsed.get('message')}"
    workout = parsed.get("workout") or {}
    week = parsed.get("week") or {}
    # A coach extra joins the plan, so the week it grows is part of the news.
    plan_note = (
        f" Plan extra: week {week.get('before_tss')} -> {week.get('after_tss')} load."
        if parsed.get("plan_row")
        else ""
    )
    return (
        f"{name}: scheduled {workout.get('workout_name')} on "
        f"{parsed.get('date')} ({workout.get('duration_minutes')} min, "
        f"~{workout.get('achieved_tss')} load).{plan_note}"
    )


def _summarize_unschedule_workout(name: str, parsed: Any) -> str | None:
    """Summarize unschedule_workout result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"unschedule_workout: {status} — {parsed.get('message')}"
    workouts = parsed.get("workouts") or []
    dates = ", ".join(str(w.get("date")) for w in workouts)
    verb = "would remove" if status == "preview" else "removed"
    return f"unschedule_workout: {verb} {len(workouts)} workout(s) ({dates})."


def _summarize_build_workout(name: str, parsed: Any) -> str | None:
    """Summarize build_workout result."""
    if not isinstance(parsed, dict):
        return None
    return (
        f"build_workout: {parsed.get('workout_name')}, "
        f"{parsed.get('duration_minutes')} min, ~{parsed.get('achieved_tss')} load."
    )


def _summarize_create_plan(name: str, parsed: Any) -> str | None:
    """Summarize create_plan result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"create_plan: {status} — {parsed.get('message')}"
    verb = "would create" if status == "preview" else "created"
    return (
        f'create_plan: {verb} "{parsed.get("plan_name")}" '
        f"({parsed.get('weeks')} weeks from {parsed.get('plan_start')})."
    )


def _summarize_set_plan_directives(name: str, parsed: Any) -> str | None:
    """Summarize set_plan_directives result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"set_plan_directives: {status} — {parsed.get('message')}"
    if status == "preview":
        removed = len(parsed.get("removes") or [])
        return (
            f"set_plan_directives: would apply directives, dropping "
            f"{removed} upcoming session(s)."
        )
    return f"set_plan_directives: applied — {parsed.get('reasoning')}"


def _summarize_upsert_race(name: str, parsed: Any) -> str | None:
    """Summarize upsert_race result."""
    if not isinstance(parsed, dict):
        return None
    if parsed.get("status") != "ok":
        return f"upsert_race: {parsed.get('status')} — {parsed.get('message')}"
    race = parsed.get("race") or {}
    verb = "created" if parsed.get("created") else "updated"
    suggested = parsed.get("suggested_trail_goal_type")
    suggestion_note = (
        f" km-effort {parsed.get('km_effort')} suggests {suggested} if this is "
        "a trail race."
        if suggested is not None
        else ""
    )
    caveat_note = "".join(f" {caveat}" for caveat in parsed.get("caveats") or [])
    return (
        f'upsert_race: {verb} "{race.get("name")}" on '
        f"{race.get('race_date')}.{suggestion_note}{caveat_note}"
    )


def _summarize_cancel_or_move_race(name: str, parsed: Any) -> str | None:
    """Summarize cancel_or_move_race result."""
    if not isinstance(parsed, dict):
        return None
    return f"cancel_or_move_race: {parsed.get('status')}."


def _summarize_list_races(name: str, parsed: Any) -> str | None:
    """Summarize list_races result."""
    if not isinstance(parsed, dict):
        return None
    return f"list_races: {parsed.get('count')} race(s)."


def _summarize_add_race_to_plan(name: str, parsed: Any) -> str | None:
    """Summarize add_race_to_plan result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status not in ("preview", "ok"):
        return f"add_race_to_plan: {status} — {parsed.get('message')}"
    fits_as = parsed.get("fits_as")
    if fits_as is None:
        return f"add_race_to_plan: does not fit — {parsed.get('reason')}"
    verb = "would fit" if status == "preview" else "added"
    return f"add_race_to_plan: {verb} as priority {fits_as}."


def _summarize_record_self_assessment(name: str, parsed: Any) -> str | None:
    """Summarize record_self_assessment result."""
    if not isinstance(parsed, dict):
        return None
    status = parsed.get("status")
    if status != "ok":
        return f"record_self_assessment: {status} — {parsed.get('message')}"
    return (
        f"record_self_assessment: stored {parsed.get('sport')} "
        f"({parsed.get('weekly_hours')} h/wk, "
        f"{parsed.get('longest_session_min')} min longest)."
    )


# Registry: tool_name → summarizer(name, parsed) → str | None
# A summarizer returns None to fall back to the default "{name}: result received."
# Coverage of this registry against the live tool registry is enforced by
# test_tool_summarizer_registry_covers_every_registered_tool.
_TOOL_SUMMARIZERS: dict[str, Callable[[str, Any], str | None]] = {
    "get_recent_activities": _summarize_get_recent_activities,
    "get_activity_detail": _summarize_get_activity_detail,
    "get_weekly_training_summary": _summarize_get_weekly_training_summary,
    "get_athlete_thresholds": _summarize_get_athlete_thresholds,
    "set_athlete_thresholds": _summarize_set_athlete_thresholds,
    "estimate_threshold_from_race": _summarize_estimate_threshold_from_race,
    "get_athlete_profile": lambda n, p: "get_athlete_profile: profile loaded.",
    "update_athlete_profile": lambda n, p: "update_athlete_profile: profile updated.",
    "update_memory": _summarize_update_memory,
    "get_planned_sessions": _summarize_get_planned_sessions,
    "get_active_plan_overview": _summarize_get_active_plan_overview,
    "get_session_menu_catalog": _summarize_get_session_menu_catalog,
    "get_training_load_status": _summarize_get_training_load_status,
    "recommend_todays_session": _summarize_recommend_todays_session,
    "get_wellness_trend": _summarize_get_wellness_trend,
    "predict_race_time": _summarize_predict_race_time,
    "get_plan_compliance": _summarize_get_plan_compliance,
    "get_session_vs_actual": _summarize_get_session_vs_actual,
    "get_race_day_outlook": _summarize_get_race_day_outlook,
    "get_brief": _summarize_get_brief,
    "set_session_status": _summarize_set_session_status,
    "skip_planned_session": _summarize_skip_planned_session,
    "move_planned_session": _summarize_move_planned_session,
    "replan_active_plan": _summarize_replan_active_plan,
    "adjust_current_week": _summarize_adjust_current_week,
    "pause_training": _summarize_pause_training,
    "swap_planned_session": _summarize_swap_planned_session,
    "schedule_workout": _summarize_schedule_workout,
    "schedule_workout_override": _summarize_schedule_workout,
    "unschedule_workout": _summarize_unschedule_workout,
    "build_workout": _summarize_build_workout,
    "create_plan": _summarize_create_plan,
    "record_self_assessment": _summarize_record_self_assessment,
    "set_plan_directives": _summarize_set_plan_directives,
    "upsert_race": _summarize_upsert_race,
    "cancel_or_move_race": _summarize_cancel_or_move_race,
    "list_races": _summarize_list_races,
    "add_race_to_plan": _summarize_add_race_to_plan,
}


def summarize_tool_result(name: str, content: Any) -> str:
    """Return the one-line summary of one tool's raw result.

    Args:
        name: The tool's name.
        content: Its raw result (a JSON string for every registered tool).

    Returns:
        ``"<name> failed: …"`` for an error payload, the tool's own
        summarizer line when it has one, else ``"<name>: result received."``.
    """
    parsed = _parse_tool_json(content) if isinstance(content, str) else None
    if isinstance(parsed, dict) and parsed.get("error"):
        return f"{name} failed: {parsed.get('message') or parsed['error']}"
    summarizer = _TOOL_SUMMARIZERS.get(name)
    line = summarizer(name, parsed) if summarizer else None
    return line or f"{name}: result received."


def build_tool_summary(messages: list[Message]) -> str | None:
    """Summarize recent tool results for the LLM to avoid dumping raw tables.

    Args:
        messages: Message list containing tool result dicts.

    Returns:
        A formatted tool summary string, or None if no tool messages exist.
    """
    tool_messages = [msg for msg in messages if msg.get("role") == "tool"]
    if not tool_messages:
        return None

    lines = [
        summarize_tool_result(msg.get("name") or "tool", msg.get("content"))
        for msg in tool_messages[-TOOL_SUMMARY_BUDGET:]
    ]
    lines = dedupe_lines(lines)
    summary = "Tool summary (use this, do not repeat raw tables): " + " ".join(lines)
    return summary[:MAX_TOOL_SUMMARY_CHARS]
