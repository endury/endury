"""Tests for endury.coach.messages — dict message helpers and tool summaries."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from endury.coach.messages import (
    _TOOL_SUMMARIZERS,
    MAX_TOOL_SUMMARY_CHARS,
    _parse_tool_json,
    _summarize_get_activity_detail,
    _summarize_get_recent_activities,
    _summarize_get_wellness_trend,
    build_tool_summary,
    dedupe_lines,
    find_last_by_role,
    has_tool_calls,
    prepare_messages_for_llm,
    prune_tool_history,
    select_recent_messages,
    stamp_user_messages,
)
from endury.coach.tools import make_tools
from tests.doubles import assistant_message, tool_call


def _ai_tool(call_id: str, name: str = "some_tool"):
    return assistant_message("", [tool_call(call_id, name, {})])


def _tool(call_id: str, content: str = "ok", name: str = "some_tool"):
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _user(text: str):
    return {"role": "user", "content": text}


def _assistant(text: str):
    return {"role": "assistant", "content": text}


def test_has_tool_calls():
    assert has_tool_calls(_ai_tool("c1")) is True
    assert has_tool_calls(_assistant("hi")) is False


def test_find_last_by_role_returns_last():
    msgs = [_user("first"), _user("second")]
    assert find_last_by_role(msgs, "user")["content"] == "second"


def test_find_last_by_role_none_when_absent():
    assert find_last_by_role([_user("hi")], "assistant") is None


def test_find_last_by_role_excludes_tool_calls():
    plain = _assistant("plain")
    msgs = [plain, _ai_tool("c1")]
    assert find_last_by_role(msgs, "assistant", exclude_tool_calls=True) is plain


def test_find_last_by_role_includes_tool_calls_by_default():
    tool_msg = _ai_tool("c1")
    msgs = [_assistant("plain"), tool_msg]
    assert find_last_by_role(msgs, "assistant") is tool_msg


def test_dedupe_lines_removes_duplicates():
    assert dedupe_lines(["a", "b", "a"]) == ["a", "b"]


def test_dedupe_lines_skips_empty():
    assert dedupe_lines(["", "x", "  "]) == ["x"]


def test_dedupe_lines_case_insensitive():
    assert dedupe_lines(["X", "x"]) == ["X"]


def test_parse_tool_json_valid_dict():
    assert _parse_tool_json('{"key": "value"}') == {"key": "value"}


def test_parse_tool_json_valid_list():
    assert _parse_tool_json("[1, 2, 3]") == [1, 2, 3]


def test_parse_tool_json_invalid_returns_none():
    assert _parse_tool_json("not json") is None


def test_parse_tool_json_empty_string_returns_none():
    assert _parse_tool_json("") is None


def test_select_recent_zero_returns_empty():
    assert select_recent_messages([_user("hi"), _assistant("hello")], 0) == []


def test_select_recent_returns_last_n():
    msgs = [_user(str(i)) for i in range(10)]
    result = select_recent_messages(msgs, 3)
    assert len(result) == 3
    assert result[-1]["content"] == "9"


def test_select_recent_all_when_n_exceeds_length():
    msgs = [_user("a"), _user("b")]
    assert len(select_recent_messages(msgs, 10)) == 2


def test_select_recent_pulls_back_assistant_paired_with_tool():
    ai = _ai_tool("call-1")
    tool = _tool("call-1")
    human = _user("follow up")
    result = select_recent_messages([ai, tool, human], 2)
    assert ai in result
    assert tool in result
    assert human in result


def test_select_recent_no_pullback_when_assistant_in_window():
    ai = _ai_tool("call-1")
    tool = _tool("call-1")
    assert select_recent_messages([ai, tool], 2) == [ai, tool]


def test_prune_empty():
    assert prune_tool_history([]) == []


def test_prune_no_tool_calls_unchanged():
    msgs = [_user("hi"), _assistant("hello")]
    assert prune_tool_history(msgs) == msgs


def test_prune_matched_pair_kept():
    ai = _ai_tool("call-1")
    tool = _tool("call-1")
    result = prune_tool_history([ai, tool])
    assert ai in result
    assert tool in result


def test_prune_unmatched_assistant_dropped():
    ai = _ai_tool("call-1")
    human = _user("what?")
    result = prune_tool_history([ai, human])
    assert ai not in result
    assert human in result


def test_prune_orphan_tool_dropped():
    tool = _tool("orphan-id")
    human = _user("hi")
    result = prune_tool_history([human, tool])
    assert tool not in result
    assert human in result


def test_prune_multiple_pairs_kept():
    ai1, t1 = _ai_tool("c1"), _tool("c1")
    ai2, t2 = _ai_tool("c2"), _tool("c2")
    result = prune_tool_history([ai1, t1, ai2, t2])
    assert all(m in result for m in (ai1, t1, ai2, t2))


def test_prune_tool_call_with_no_id_skipped():
    ai = assistant_message(
        "", [{"type": "function", "function": {"name": "t", "arguments": "{}"}}]
    )
    human = _user("after")
    result = prune_tool_history([ai, human])
    assert ai not in result
    assert human in result


def test_prepare_empty_list():
    assert prepare_messages_for_llm([]) == []


def test_prepare_last_user_strips_tool_residue():
    ai = _ai_tool("c1")
    tool = _tool("c1")
    user = _user("new question")
    prepared = prepare_messages_for_llm([ai, tool, user])
    assert prepared == [user]


def test_prepare_last_tool_rebuilds_with_pair():
    user = _user("q")
    ai = _ai_tool("c1")
    tool = _tool("c1")
    prepared = prepare_messages_for_llm([user, ai, tool])
    assert prepared == [user, ai, tool]


def test_prepare_last_tool_without_tool_call_message():
    # A tool message with no preceding assistant tool-call is dropped.
    user = _user("q")
    tool = _tool("orphan")
    prepared = prepare_messages_for_llm([user, tool])
    assert prepared == [user]


def test_prepare_drops_stale_tool_round_before_newer_user_message():
    # A tool round that never closed out before a newer user message arrived
    # is stale, prior-turn residue — it is pruned (across turns), while the
    # newer user message's own dangling tool result (an orphan, no matching
    # tool-call in its round) is dropped too.
    user1 = _user("Hello")
    tool_call_msg = _ai_tool("call-1", "lookup")
    first_tool = _tool("call-1", "ok", "lookup")
    user2 = _user("follow-up")
    trailing_tool = _tool("call-1", "stale", "lookup")
    prepared = prepare_messages_for_llm(
        [user1, tool_call_msg, first_tool, user2, trailing_tool]
    )
    assert prepared == [user1, user2]


def test_prepare_last_tool_keeps_every_round_of_the_current_turn():
    # Two tool rounds within the same turn must both survive — not
    # just the most recent one.
    user = _user("how's my form and what's on the plan?")
    ai1 = _ai_tool("c1", "get_training_load_status")
    tool1 = _tool("c1", '{"ctl": 55}', "get_training_load_status")
    ai2 = _ai_tool("c2", "get_planned_sessions")
    tool2 = _tool("c2", '{"sessions": []}', "get_planned_sessions")
    prepared = prepare_messages_for_llm([user, ai1, tool1, ai2, tool2])
    assert prepared == [user, ai1, tool1, ai2, tool2]


def test_prepare_returns_unchanged_when_last_is_assistant():
    msgs = [_user("hi"), _assistant("done")]
    assert prepare_messages_for_llm(msgs) == msgs


def test_summary_no_tool_messages_returns_none():
    assert build_tool_summary([_user("hi"), _assistant("hello")]) is None


def test_summary_get_recent_activities():
    content = '{"count": 4, "start_date": "2026-01-01", "end_date": "2026-01-14"}'
    result = build_tool_summary([_tool("c1", content, "get_recent_activities")])
    assert "4 activities" in result
    assert "2026-01-01..2026-01-14" in result


def test_summarize_get_recent_activities_non_dict_returns_none():
    assert _summarize_get_recent_activities("get_recent_activities", ["x"]) is None


def test_summary_get_activity_detail_single_and_by_date():
    single = json.dumps(
        {
            "activity_id": "i1",
            "date": "2026-01-06",
            "sport": "running",
            "intervals": [1, 2],
        }
    )
    result = build_tool_summary([_tool("c1", single, "get_activity_detail")])
    assert "2026-01-06 running (2 intervals)" in result

    by_date = json.dumps(
        {
            "date": "2026-01-06",
            "activities": [
                {"activity_id": "s1", "date": "2026-01-06", "sport": "swimming"},
                {
                    "activity_id": "r1",
                    "date": "2026-01-06",
                    "sport": "cycling",
                    "intervals": [1],
                },
            ],
        }
    )
    result = build_tool_summary([_tool("c1", by_date, "get_activity_detail")])
    assert "swimming (0 intervals); 2026-01-06 cycling (1 intervals)" in result

    empty = json.dumps({"date": "2026-01-06", "activities": []})
    result = build_tool_summary([_tool("c1", empty, "get_activity_detail")])
    assert "no activity found" in result


def test_summary_get_wellness_trend():
    content = json.dumps(
        {"metrics": {"hrv": {"trend": "declining"}, "resting_hr": {"trend": "rising"}}}
    )
    result = build_tool_summary([_tool("c1", content, "get_wellness_trend")])
    assert "hrv declining, resting_hr rising" in result
    empty = build_tool_summary([_tool("c1", '{"metrics": {}}', "get_wellness_trend")])
    assert "no wellness data" in empty
    assert _summarize_get_wellness_trend("get_wellness_trend", {"metrics": []}) is None


def test_summarize_get_activity_detail_non_dict_returns_generic():
    assert _summarize_get_activity_detail("get_activity_detail", ["x"]) is None
    assert (
        _summarize_get_activity_detail("get_activity_detail", {"activities": "x"})
        is None
    )


def test_summary_get_weekly_training_summary():
    content = '{"weeks": [{"hours": 5.5}, {"hours": 4.25}]}'
    result = build_tool_summary([_tool("c1", content, "get_weekly_training_summary")])
    assert "2 weeks" in result
    assert "9.8h total" in result


def test_summary_get_weekly_training_summary_without_weeks_falls_back():
    result = build_tool_summary(
        [_tool("c1", '{"note": "x"}', "get_weekly_training_summary")]
    )
    assert "get_weekly_training_summary: result received" in result


def test_summary_get_athlete_thresholds():
    content = '[{"sport": "running"}, {"sport": "cycling"}]'
    result = build_tool_summary([_tool("c1", content, "get_athlete_thresholds")])
    assert "2 sport(s)" in result
    assert "running, cycling" in result


def test_summary_get_athlete_thresholds_non_list_falls_back():
    result = build_tool_summary(
        [_tool("c1", '{"message": "none"}', "get_athlete_thresholds")]
    )
    assert "get_athlete_thresholds: result received" in result


def test_summary_set_athlete_thresholds_preview_and_error():
    content = (
        '{"status": "preview", "sport": "running", "changes": [{"label": '
        '"threshold heart rate", "current": "165 bpm", "proposed": "172 bpm"}]}'
    )
    result = build_tool_summary([_tool("c1", content, "set_athlete_thresholds")])
    assert "would set running threshold heart rate 165 bpm -> 172 bpm." in result
    error = '{"status": "error", "message": "Pass at least one threshold to set."}'
    result = build_tool_summary([_tool("c1", error, "set_athlete_thresholds")])
    assert "error — Pass at least one threshold" in result


def test_summary_estimate_threshold_from_race():
    content = '{"status": "ok", "threshold_pace_min_km": "5:03"}'
    result = build_tool_summary([_tool("c1", content, "estimate_threshold_from_race")])
    assert "threshold pace 5:03/km." in result
    refused = '{"status": "error", "message": "Use a race of 3 km or longer."}'
    result = build_tool_summary([_tool("c1", refused, "estimate_threshold_from_race")])
    assert "Use a race of 3 km or longer." in result


def test_summary_update_memory():
    content = '{"success": true, "characters": 412}'
    result = build_tool_summary([_tool("c1", content, "update_memory")])
    assert "update_memory: document rewritten (412 chars)." in result


def test_summary_update_memory_rejected():
    content = '{"success": false, "message": "too long — condense it"}'
    result = build_tool_summary([_tool("c1", content, "update_memory")])
    assert "update_memory: rejected — too long — condense it" in result


def test_summary_unknown_tool():
    result = build_tool_summary([_tool("c1", "anything", "my_custom_tool")])
    assert "my_custom_tool" in result


def test_summary_truncated_to_max():
    tools = [
        _tool(
            f"c{i}",
            f'{{"count": {i}, "start_date": "2026-01-01", "end_date": "2026-01-14"}}',
            "get_recent_activities",
        )
        for i in range(20)
    ]
    result = build_tool_summary(tools)
    assert len(result) <= MAX_TOOL_SUMMARY_CHARS


def test_summary_deduplicates_identical_lines():
    tools = [
        _tool("c1", '{"success": true, "characters": 12}', "update_memory"),
        _tool("c2", '{"success": true, "characters": 12}', "update_memory"),
    ]
    result = build_tool_summary(tools)
    assert result.count("update_memory: document rewritten (12 chars).") == 1


def test_summary_update_athlete_profile():
    result = build_tool_summary(
        [_tool("c1", '{"updated": true}', "update_athlete_profile")]
    )
    assert "update_athlete_profile" in result


def test_summary_get_athlete_profile():
    result = build_tool_summary([_tool("c1", "{}", "get_athlete_profile")])
    assert "get_athlete_profile" in result


def test_summary_planned_sessions():
    content = '{"plan": "Ironman", "sessions": [{"date": "x"}, {"date": "y"}]}'
    result = build_tool_summary([_tool("c1", content, "get_planned_sessions")])
    assert "2 sessions in plan 'Ironman'" in result


def test_summary_planned_sessions_missing_key_falls_back():
    result = build_tool_summary([_tool("c1", '{"plan": "x"}', "get_planned_sessions")])
    assert "get_planned_sessions" in result


def test_summary_active_plan_overview():
    content = (
        '{"name": "Ironman Build", "goal_type": "ironman", "goal_date": "2026-09-01"}'
    )
    result = build_tool_summary([_tool("c1", content, "get_active_plan_overview")])
    assert "Ironman Build" in result


def test_summary_active_plan_overview_no_name_falls_back():
    result = build_tool_summary(
        [_tool("c1", '{"message": "none"}', "get_active_plan_overview")]
    )
    assert "get_active_plan_overview" in result


def test_summary_session_menu_catalog():
    content = '{"goal": "ironman", "phases": [{"phase": "base"}, {"phase": "build"}]}'
    result = build_tool_summary([_tool("c1", content, "get_session_menu_catalog")])
    assert "ironman, 2 phases" in result


def test_summary_session_menu_catalog_without_phases_falls_back():
    result = build_tool_summary(
        [_tool("c1", '{"goal": "ironman"}', "get_session_menu_catalog")]
    )
    assert "get_session_menu_catalog: result received" in result


def test_summary_session_menu_catalog_error_falls_back():
    result = build_tool_summary(
        [_tool("c1", '{"error": "unknown"}', "get_session_menu_catalog")]
    )
    assert "get_session_menu_catalog" in result


@pytest.mark.parametrize(
    "name",
    [
        "get_recent_activities",
        "get_weekly_training_summary",
        "get_athlete_thresholds",
        "set_athlete_thresholds",
        "estimate_threshold_from_race",
        "get_planned_sessions",
        "get_session_menu_catalog",
        "update_memory",
        "get_training_load_status",
        "recommend_todays_session",
        "get_plan_compliance",
        "get_session_vs_actual",
        "get_race_day_outlook",
        "set_session_status",
        "move_planned_session",
        "replan_active_plan",
        "build_workout",
        "schedule_workout",
        "unschedule_workout",
    ],
)
def test_summary_non_dict_falls_back(name):
    result = build_tool_summary([_tool("c1", '"not a dict"', name)])
    assert name in result


def test_summary_get_training_load_status():
    content = '{"current": {"ctl": 55.2, "atl": 60.1, "tsb": -4.9}}'
    result = build_tool_summary([_tool("c1", content, "get_training_load_status")])
    assert "fitness=55.2" in result
    assert "fatigue=60.1" in result
    assert "form=-4.9" in result


def test_summary_recommend_todays_session():
    content = '{"date": "2026-09-27", "verdict": "keep", "signals": {"tsb": -8.0}}'
    result = build_tool_summary([_tool("c1", content, "recommend_todays_session")])
    assert "recommend_todays_session(2026-09-27): verdict=keep" in result
    assert "form=-8.0" in result


def test_summary_get_plan_compliance():
    content = (
        '{"completed": 10, "total_workouts": 12, "completion_rate_pct": 83.3, '
        '"planned_tss": 500, "delivered_tss": 470}'
    )
    result = build_tool_summary([_tool("c1", content, "get_plan_compliance")])
    assert "10/12 completed" in result
    assert "83.3%" in result


def test_summary_get_session_vs_actual():
    content = (
        '{"date": "2026-08-01", "sessions": ['
        '{"delivered": {"estimated_tss": 50}}, {"delivered": null}]}'
    )
    result = build_tool_summary([_tool("c1", content, "get_session_vs_actual")])
    assert "1/2 session(s) matched" in result


def test_summary_get_race_day_outlook():
    content = (
        '{"goal_date": "2026-09-01", '
        '"aggregate_ctl": [{"value": 80.0}], "aggregate_tsb": [{"value": 22.0}]}'
    )
    result = build_tool_summary([_tool("c1", content, "get_race_day_outlook")])
    assert "projected fitness=80.0, form=22.0" in result


def test_summary_set_session_status():
    content = '{"status": "ok", "session_id": 7, "new_status": "completed"}'
    result = build_tool_summary([_tool("c1", content, "set_session_status")])
    assert "session 7 -> completed" in result


def test_summary_set_session_status_error():
    result = build_tool_summary(
        [_tool("c1", '{"error": "Invalid status \'bogus\'."}', "set_session_status")]
    )
    assert "failed" in result


def test_summary_move_planned_session():
    content = '{"status": "ok", "session_id": 7, "date": "2026-08-20"}'
    result = build_tool_summary([_tool("c1", content, "move_planned_session")])
    assert "session 7 -> 2026-08-20" in result


def test_summary_move_planned_session_keeps_the_warning():
    content = (
        '{"status": "ok", "session_id": 7, "date": "2026-08-20", '
        '"warning": "the day now holds 2."}'
    )
    result = build_tool_summary([_tool("c1", content, "move_planned_session")])
    assert "Warning: the day now holds 2." in result


def test_summary_move_planned_session_refused():
    content = '{"status": "error", "message": "Cannot move a session into the past."}'
    result = build_tool_summary([_tool("c1", content, "move_planned_session")])
    assert "Cannot move a session into the past." in result


def test_summary_replan_active_plan():
    content = '{"status": "ok", "action": "regenerated", "reasoning": "..."}'
    result = build_tool_summary([_tool("c1", content, "replan_active_plan")])
    assert "ok — regenerated" in result


def test_summary_replan_active_plan_keeps_the_curated_reason():
    # replan_active_plan relays run_replan's ValueError text as
    # {"status": "error", "message": ...} — no "error" key, so the summary
    # must not fall through to "error — None".
    content = '{"status": "error", "message": "The goal race has passed."}'
    result = build_tool_summary([_tool("c1", content, "replan_active_plan")])
    assert "The goal race has passed." in result
    assert "None" not in result


def test_summary_replan_active_plan_preview():
    content = '{"status": "preview", "confirm_required": true, "edited_workouts": []}'
    result = build_tool_summary([_tool("c1", content, "replan_active_plan")])
    assert "would regenerate from next Monday" in result
    assert "overwriting" not in result


def test_summary_replan_active_plan_preview_counts_edited_workouts():
    content = json.dumps(
        {
            "status": "preview",
            "confirm_required": True,
            "edited_workouts": [{"external_id": "coachie:7:w03"}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "replan_active_plan")])
    assert "overwriting 1 edited workout(s)" in result


def test_summary_adjust_current_week_preview():
    content = json.dumps(
        {
            "status": "preview",
            "sessions": [{"session_id": 1}, {"session_id": 2}],
            "totals": {
                "before_minutes": 300.0,
                "after_minutes": 200.0,
                "actual_percent": 33.3,
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "adjust_current_week")])
    assert "would trim 2 session(s)" in result
    assert "300.0 -> 200.0 min" in result
    assert "33.3% off" in result


def test_summary_adjust_current_week_applied():
    content = json.dumps(
        {
            "status": "ok",
            "sessions": [{"session_id": 1}],
            "totals": {
                "before_minutes": 60.0,
                "after_minutes": 36.0,
                "actual_percent": 40.0,
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "adjust_current_week")])
    assert "trimmed 1 session(s)" in result


def test_summary_adjust_current_week_keeps_the_curated_refusal():
    # The use case's refusals carry {"status": "error", "message": ...} with
    # no "error" key, so the summary must relay the reason, not "None".
    content = '{"status": "empty", "message": "Nothing to shorten."}'
    result = build_tool_summary([_tool("c1", content, "adjust_current_week")])
    assert "Nothing to shorten." in result
    assert "None" not in result


def test_summary_adjust_current_week_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "adjust_current_week")])
    assert "adjust_current_week: result received." in result


def test_summary_pause_training_preview():
    content = json.dumps(
        {
            "status": "preview",
            "window": {"from": "2026-09-17", "through": "2026-09-23"},
            "resume_date": "2026-09-24",
            "sessions": [{"session_id": 1}, {"session_id": 2}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "would skip 2 session(s) 2026-09-17 through 2026-09-23" in result
    assert "resumes 2026-09-24." in result


def test_summary_pause_training_applied():
    content = json.dumps(
        {
            "status": "ok",
            "window": {"from": "2026-09-17", "through": "2026-09-23"},
            "resume_date": "2026-09-24",
            "sessions": [{"session_id": 1}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "skipped 1 session(s)" in result


def test_summary_pause_training_booking_only_names_the_bookings():
    content = json.dumps(
        {
            "status": "ok",
            "window": {"from": "2026-09-17", "through": "2026-09-23"},
            "resume_date": "2026-09-24",
            "sessions": [],
            "ad_hoc": {
                "status": "ok",
                "deleted": 1,
                "workouts": [{"date": "2026-09-18", "name": "Easy Shakeout"}],
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "removed 1 booking(s): 2026-09-18 Easy Shakeout." in result
    assert "resumes" not in result


def test_summary_pause_training_booking_only_preview_says_would_remove():
    content = json.dumps(
        {
            "status": "preview",
            "sessions": [],
            "ad_hoc": {
                "status": "preview",
                "workouts": [{"date": "2026-09-18", "name": "Easy Shakeout"}],
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "would remove 1 booking(s): 2026-09-18 Easy Shakeout." in result


def test_summary_pause_training_plan_less_pause_names_its_bookings():
    """`pause_without_plan`'s shape reports its bookings at the top level."""
    content = json.dumps(
        {
            "status": "preview",
            "no_plan": True,
            "window": {"from": "2026-09-19", "through": "2026-09-25"},
            "workouts": [{"date": "2026-09-20", "name": "Easy Shakeout"}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "would remove 1 booking(s): 2026-09-20 Easy Shakeout." in result


def test_summary_pause_training_keeps_the_curated_refusal():
    content = '{"status": "empty", "message": "Nothing is still pending."}'
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "Nothing is still pending." in result
    assert "None" not in result
    # An empty window is still a card with an active plan: applying it
    # records the window as unavailable.
    assert "Applying records the window as unavailable." in result


def test_summary_pause_training_applied_empty_window_carries_no_apply_note():
    content = json.dumps(
        {
            "status": "empty",
            "confirm_required": False,
            "message": "Nothing was pending — the window is recorded as unavailable.",
        }
    )
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "the window is recorded as unavailable." in result
    assert "Applying records" not in result


def test_summary_pause_training_plan_less_empty_window_stays_a_plain_status():
    content = '{"status": "empty", "no_plan": true, "message": "No plan."}'
    result = build_tool_summary([_tool("c1", content, "pause_training")])
    assert "pause_training: empty — No plan." in result


def test_summary_pause_training_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "pause_training")])
    assert "pause_training: result received." in result


def test_summary_swap_planned_session_preview():
    content = json.dumps(
        {
            "status": "preview",
            "date": "2026-05-21",
            "current": {"activity_key": "run_easy"},
            "replacement": {
                "workout_name": "Threshold Run",
                "duration_minutes": 70.0,
                "achieved_tss": 88.0,
            },
            "week": {
                "before_tss": 400.0,
                "after_tss": 440.0,
                "tss_delta_percent": 10.0,
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "swap_planned_session")])
    assert "would replace run_easy on 2026-05-21 with Threshold Run" in result
    assert "70.0 min" in result
    assert "week 400.0 -> 440.0 load (10.0%)" in result


def test_summary_swap_planned_session_applied():
    content = json.dumps(
        {
            "status": "ok",
            "date": "2026-05-21",
            "current": {"activity_key": "run_easy"},
            "replacement": {
                "workout_name": "Threshold Run",
                "duration_minutes": 70.0,
                "achieved_tss": 88.0,
            },
            "week": {},
        }
    )
    result = build_tool_summary([_tool("c1", content, "swap_planned_session")])
    assert "replaced run_easy on 2026-05-21" in result


def test_summary_swap_planned_session_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "Session 501 is already done."}'
    result = build_tool_summary([_tool("c1", content, "swap_planned_session")])
    assert "Session 501 is already done." in result
    assert "None" not in result


def test_summary_swap_planned_session_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "swap_planned_session")])
    assert "swap_planned_session: result received." in result


def test_summary_schedule_workout_applied():
    content = json.dumps(
        {
            "status": "ok",
            "date": "2026-06-01",
            "workout": {
                "workout_name": "Threshold Run",
                "duration_minutes": 45.0,
                "achieved_tss": 62.0,
            },
        }
    )
    result = build_tool_summary([_tool("c1", content, "schedule_workout")])
    assert "scheduled Threshold Run on 2026-06-01" in result
    assert "45.0 min" in result


def test_summary_schedule_workout_names_the_week_a_plan_extra_grows():
    content = json.dumps(
        {
            "status": "ok",
            "date": "2026-06-01",
            "workout": {
                "workout_name": "Easy Run",
                "duration_minutes": 45.0,
                "achieved_tss": 42.0,
            },
            "plan_row": {"session_id": 12, "slot_id": "x1:extra_2026-06-01_run_easy"},
            "week": {"before_tss": 400.0, "after_tss": 442.0},
        }
    )
    result = build_tool_summary([_tool("c1", content, "schedule_workout")])
    assert "scheduled Easy Run on 2026-06-01" in result
    assert "Plan extra: week 400.0 -> 442.0 load." in result


def test_summary_schedule_workout_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "2026-01-01 has passed."}'
    result = build_tool_summary([_tool("c1", content, "schedule_workout")])
    assert "2026-01-01 has passed." in result
    assert "None" not in result


def test_summary_schedule_workout_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "schedule_workout")])
    assert "schedule_workout: result received." in result


def test_summary_unschedule_workout_preview():
    content = json.dumps(
        {
            "status": "preview",
            "workouts": [{"date": "2026-06-01", "name": "Easy Run"}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "unschedule_workout")])
    assert "would remove 1 workout(s) (2026-06-01)" in result


def test_summary_unschedule_workout_applied():
    content = json.dumps(
        {
            "status": "ok",
            "workouts": [{"date": "2026-06-01", "name": "Easy Run"}],
        }
    )
    result = build_tool_summary([_tool("c1", content, "unschedule_workout")])
    assert "removed 1 workout(s) (2026-06-01)" in result


def test_summary_unschedule_workout_keeps_the_curated_refusal():
    content = '{"status": "not_found", "message": "No coach-scheduled workout."}'
    result = build_tool_summary([_tool("c1", content, "unschedule_workout")])
    assert "No coach-scheduled workout." in result


def test_summary_unschedule_workout_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "unschedule_workout")])
    assert "unschedule_workout: result received." in result


def test_summary_build_workout():
    content = (
        '{"workout_name": "Threshold Run", "duration_minutes": 45.0, '
        '"achieved_tss": 62.0}'
    )
    result = build_tool_summary([_tool("c1", content, "build_workout")])
    assert "Threshold Run" in result
    assert "45.0 min" in result


def test_summary_build_workout_error():
    result = build_tool_summary(
        [_tool("c1", '{"error": "Unknown sport \'foo\'."}', "build_workout")]
    )
    assert "failed" in result


def test_summary_create_plan_preview():
    content = json.dumps(
        {
            "status": "preview",
            "plan_name": "Marathon — 2026-11-08",
            "weeks": 25,
            "plan_start": "2026-05-18",
        }
    )
    result = build_tool_summary([_tool("c1", content, "create_plan")])
    assert 'would create "Marathon — 2026-11-08"' in result
    assert "25 weeks from 2026-05-18" in result


def test_summary_create_plan_applied():
    content = json.dumps(
        {
            "status": "ok",
            "plan_name": "Marathon — 2026-11-08",
            "weeks": 25,
            "plan_start": "2026-05-18",
        }
    )
    result = build_tool_summary([_tool("c1", content, "create_plan")])
    assert 'created "Marathon — 2026-11-08"' in result


def test_summary_create_plan_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "A plan is already active."}'
    result = build_tool_summary([_tool("c1", content, "create_plan")])
    assert "A plan is already active." in result
    assert "None" not in result


def test_summary_create_plan_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "create_plan")])
    assert "create_plan: result received." in result


def test_summary_record_self_assessment_applied():
    content = json.dumps(
        {
            "status": "ok",
            "sport": "running",
            "weekly_hours": 6.0,
            "longest_session_min": 90,
        }
    )
    result = build_tool_summary([_tool("c1", content, "record_self_assessment")])
    assert "stored running (6.0 h/wk, 90 min longest)." in result


def test_summary_record_self_assessment_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "Unknown sport \'rowing\'."}'
    result = build_tool_summary([_tool("c1", content, "record_self_assessment")])
    assert "Unknown sport 'rowing'." in result
    assert "None" not in result


def test_summary_record_self_assessment_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "record_self_assessment")])
    assert "record_self_assessment: result received." in result


def test_summary_set_plan_directives_preview():
    content = json.dumps(
        {"status": "preview", "confirm_required": True, "removes": [1, 2, 3]}
    )
    result = build_tool_summary([_tool("c1", content, "set_plan_directives")])
    assert "would apply directives, dropping 3 upcoming session(s)." in result


def test_summary_set_plan_directives_applied():
    content = json.dumps({"status": "ok", "reasoning": "Rebuilt from next Monday."})
    result = build_tool_summary([_tool("c1", content, "set_plan_directives")])
    assert "applied — Rebuilt from next Monday." in result


def test_summary_set_plan_directives_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "No active training plan."}'
    result = build_tool_summary([_tool("c1", content, "set_plan_directives")])
    assert "No active training plan." in result


def test_summary_set_plan_directives_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "set_plan_directives")])
    assert "set_plan_directives: result received." in result


def test_summary_upsert_race_created():
    content = json.dumps(
        {
            "status": "ok",
            "created": True,
            "race": {"name": "Berlin Marathon", "race_date": "2026-11-08"},
        }
    )
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert 'created "Berlin Marathon" on 2026-11-08.' in result


def test_summary_upsert_race_updated():
    content = json.dumps(
        {
            "status": "ok",
            "created": False,
            "race": {"name": "Berlin Marathon", "race_date": "2026-11-08"},
        }
    )
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert 'updated "Berlin Marathon" on 2026-11-08.' in result


def test_summary_upsert_race_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "Unknown goal_type \'x\'."}'
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert "Unknown goal_type 'x'." in result


def test_summary_upsert_race_carries_the_trail_suggestion():
    content = json.dumps(
        {
            "status": "ok",
            "created": True,
            "race": {"name": "Mont Blanc", "race_date": "2026-08-30"},
            "km_effort": 87.0,
            "suggested_trail_goal_type": "trail_50k",
        }
    )
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert "km-effort 87.0 suggests trail_50k if this is a trail race." in result


def test_summary_upsert_race_carries_caveats():
    content = json.dumps(
        {
            "status": "ok",
            "created": False,
            "race": {"name": "Mont Blanc", "race_date": "2026-08-30"},
            "km_effort": 87.0,
            "suggested_trail_goal_type": "trail_50k",
            "caveats": ["This race profile suggests Trail 50K; kept Trail 20K."],
        }
    )
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert "This race profile suggests Trail 50K; kept Trail 20K." in result


def test_summary_upsert_race_without_a_suggestion_has_no_trailing_note():
    content = json.dumps(
        {
            "status": "ok",
            "created": True,
            "race": {"name": "Berlin Marathon", "race_date": "2026-11-08"},
        }
    )
    result = build_tool_summary([_tool("c1", content, "upsert_race")])
    assert result.strip().endswith('created "Berlin Marathon" on 2026-11-08.')


def test_summary_upsert_race_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "upsert_race")])
    assert "upsert_race: result received." in result


def test_summary_list_races():
    result = build_tool_summary(
        [_tool("c1", json.dumps({"races": [], "count": 3}), "list_races")]
    )
    assert "list_races: 3 race(s)." in result


def test_summary_list_races_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "list_races")])
    assert "list_races: result received." in result


def test_summary_add_race_to_plan_preview_fits():
    content = json.dumps({"status": "preview", "fits_as": "B"})
    result = build_tool_summary([_tool("c1", content, "add_race_to_plan")])
    assert "would fit as priority B." in result


def test_summary_add_race_to_plan_applied():
    content = json.dumps({"status": "ok", "fits_as": "B"})
    result = build_tool_summary([_tool("c1", content, "add_race_to_plan")])
    assert "added as priority B." in result


def test_summary_add_race_to_plan_does_not_fit():
    content = json.dumps({"status": "preview", "fits_as": None, "reason": "too tight"})
    result = build_tool_summary([_tool("c1", content, "add_race_to_plan")])
    assert "does not fit — too tight" in result


def test_summary_add_race_to_plan_keeps_the_curated_refusal():
    content = '{"status": "error", "message": "No race with id 999."}'
    result = build_tool_summary([_tool("c1", content, "add_race_to_plan")])
    assert "No race with id 999." in result


def test_summary_add_race_to_plan_non_dict_falls_back():
    result = build_tool_summary([_tool("c1", "[1, 2]", "add_race_to_plan")])
    assert "add_race_to_plan: result received." in result


# Every registered tool must have a summarizer, or its results silently
# degrade to "X: result received."


def test_tool_summarizer_registry_covers_every_registered_tool():
    tools = make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
        repos=MagicMock(),
        intervals_client=MagicMock(),
    )
    tool_names = {tool.name for tool in tools}
    assert tool_names == set(_TOOL_SUMMARIZERS)
    assert len(_TOOL_SUMMARIZERS) == len(tools)


@pytest.mark.parametrize("name", sorted(_TOOL_SUMMARIZERS))
def test_summary_error_shape_never_fabricates_for_any_summarizer(name):
    # Every summarizer must be bypassed for an error-shaped tool
    # result (the standard tool_error_response shape) — no per-summarizer
    # key interpolation, no fabricated "None" values.
    content = json.dumps(
        {
            "error": "Failed to do the thing.",
            "message": "There was an error. Please try again later.",
        }
    )
    result = build_tool_summary([_tool("c1", content, name)])
    assert f"{name} failed: There was an error. Please try again later." in result
    assert "None" not in result


def test_summary_error_shape_falls_back_to_error_value_when_no_message():
    result = build_tool_summary([_tool("c1", '{"error": "Boom."}', "get_activities")])
    assert "get_activities failed: Boom." in result


def test_summary_non_string_content_uses_generic():
    # A tool message whose content is not a string cannot be JSON-parsed.
    result = build_tool_summary(
        [
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "update_memory",
                "content": None,
            }
        ]
    )
    assert "update_memory" in result


# stamp_user_messages: when the athlete actually sent each message.


def test_stamp_user_messages_prefixes_the_send_time_and_drops_the_key():
    # A message sent late one evening must not read as sent "tonight" when
    # replayed hours later on a different day.
    stamped = stamp_user_messages(
        [{"role": "user", "content": "I am ready", "sent_at": "2026-09-20T18:44:10"}]
    )
    assert stamped == [
        {"role": "user", "content": "[Sunday 2026-09-20 18:44] I am ready"}
    ]


def test_stamp_user_messages_leaves_unkeyed_messages_untouched():
    messages = [
        {"role": "assistant", "content": "Start on the bike."},
        {"role": "user", "content": "No stamp here"},
    ]
    assert stamp_user_messages(messages) == messages


def test_stamp_user_messages_never_mutates_its_input():
    # The dicts live in CoachSession.history and the DB-backed content must
    # stay raw — the finalizer parses it for a name answer.
    original = {"role": "user", "content": "raw", "sent_at": "2026-09-21T17:23:00"}
    messages = [original]
    stamp_user_messages(messages)
    assert original == {
        "role": "user",
        "content": "raw",
        "sent_at": "2026-09-21T17:23:00",
    }
    assert messages == [original]
