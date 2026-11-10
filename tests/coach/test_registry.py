"""Tests for the in-house coach tool registry."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from endury.coach.tools import NEEDS_ACTIVE_PLAN, make_tools, offered_tools
from endury.coach.tools.registry import (
    CoachTool,
    coach_tool,
    execute_tool,
    execute_tool_async,
    to_openai_tools,
    with_reply_contract,
)

# Schema generation via @coach_tool


def test_coach_tool_name_and_description_from_function():
    @coach_tool
    def sample_tool(x: str) -> str:
        """Do a thing with x."""
        return x

    assert sample_tool.name == "sample_tool"
    assert sample_tool.description == "Do a thing with x."


def test_coach_tool_no_docstring_gives_empty_description():
    @coach_tool
    def bare(x: str) -> str:
        return x

    assert bare.description == ""


@coach_tool
def documented(day: str, confirm: bool = False) -> str:
    """Pause through a day.

    Runs as a preview.

    Reply contract — when relaying the result:
    * Name every session in ``sessions``, each
      with its reason.
    * Relay every string in ``caveats`` verbatim.

    Args:
        day: Last day out,
            YYYY-MM-DD.
        confirm: Filled in by the app when the athlete applies the
            card; not part of your call.

    Returns:
        JSON string: the preview or the applied outcome.
    """
    return json.dumps({"status": "preview", "day": day})


@coach_tool
def app_only(confirm: bool = False) -> str:
    """Replan from next Monday.

    Args:
        confirm: Filled in by the app when the athlete applies the
            card; not part of your call.

    Returns:
        JSON string with the outcome.
    """
    return json.dumps({"status": "preview"})


def test_description_is_the_docstring_the_model_needs_every_round():
    # The result section, the app's own parameters and the relay rules cost
    # tokens on every round of every turn and help on none of them.
    assert documented.description == (
        "Pause through a day.\n\nRuns as a preview.\n\n"
        "Args:\n    day: Last day out,\n        YYYY-MM-DD."
    )


def test_description_drops_an_args_section_left_empty():
    assert app_only.description == "Replan from next Monday."
    assert app_only.reply_contract == ()


def test_reply_contract_is_one_line_per_rule():
    assert documented.reply_contract == (
        "Name every session in ``sessions``, each with its reason.",
        "Relay every string in ``caveats`` verbatim.",
    )


def test_reply_contract_rides_on_the_result():
    result = json.loads(
        with_reply_contract(documented, documented.invoke({"day": "x"}))
    )
    assert result["status"] == "preview"
    assert result["reply_contract"] == list(documented.reply_contract)


def test_reply_contract_leaves_other_results_alone():
    assert with_reply_contract(app_only, '{"status": "ok"}') == '{"status": "ok"}'
    assert with_reply_contract(None, '{"status": "ok"}') == '{"status": "ok"}'
    assert with_reply_contract(documented, "not json") == "not json"
    assert with_reply_contract(documented, "[1]") == "[1]"


def _all_tools():
    return make_tools(
        athlete_repo=MagicMock(),
        memory_repo=MagicMock(),
        repos=MagicMock(),
        intervals_client=MagicMock(),
    )


def test_no_real_description_carries_what_the_registry_strips():
    for tool in _all_tools():
        assert "Returns:" not in tool.description, tool.name
        assert "Reply contract" not in tool.description, tool.name
        assert "confirm:" not in tool.description, tool.name
        assert "expected_input_hash" not in tool.description, tool.name


def test_the_apply_card_rule_is_stated_once_in_the_prompt():
    # Each previewing tool says that it previews; what a preview is, and that
    # only the athlete applies it, is the prompt's "Plan writes" section.
    for tool in _all_tools():
        assert "tap Apply" not in tool.description, tool.name
        assert "Apply and Dismiss" not in tool.description, tool.name


def test_the_intent_list_lives_in_build_workout_alone():
    by_name = {tool.name: tool for tool in _all_tools()}
    assert "never add strides" in by_name["build_workout"].description
    for name in ("schedule_workout", "swap_planned_session"):
        assert "as for ``build_workout``" in by_name[name].description
        assert "never add strides" not in by_name[name].description


def test_build_workout_relay_rules_ride_on_its_result():
    by_name = {tool.name: tool for tool in _all_tools()}
    contract = " ".join(by_name["build_workout"].reply_contract)
    assert "Quote ``outline`` line for line" in contract
    assert "say the session builds to that length" in contract


# Tools offered by plan state


def test_every_plan_tool_named_is_a_registered_tool():
    assert {tool.name for tool in _all_tools()} >= NEEDS_ACTIVE_PLAN


def test_without_a_plan_the_plan_tools_are_not_offered():
    tools = _all_tools()
    offered = {tool.name for tool in offered_tools(tools, has_plan=False)}
    assert offered.isdisjoint(NEEDS_ACTIVE_PLAN)
    # What an athlete without a plan can still ask for.
    assert {
        "create_plan",
        "schedule_workout",
        "unschedule_workout",
        "pause_training",
        "build_workout",
        "upsert_race",
        "get_session_menu_catalog",
    } <= offered
    assert len(offered) == len(tools) - len(NEEDS_ACTIVE_PLAN)


def test_with_a_plan_every_tool_is_offered():
    tools = _all_tools()
    assert offered_tools(tools, has_plan=True) == tools


def test_schema_primitive_types():
    @coach_tool
    def typed(a: str, b: int, c: float, d: bool) -> str:
        """Typed params."""
        return ""

    props = typed.parameters["properties"]
    assert props["a"]["type"] == "string"
    assert props["b"]["type"] == "integer"
    assert props["c"]["type"] == "number"
    assert props["d"]["type"] == "boolean"
    assert set(typed.parameters["required"]) == {"a", "b", "c", "d"}


def test_schema_optional_and_default_not_required():
    @coach_tool
    def mixed(required_arg: str, opt: str | None = None, num: int = 5) -> str:
        """Mixed params."""
        return ""

    assert mixed.parameters["required"] == ["required_arg"]
    assert mixed.parameters["properties"]["opt"]["type"] == "string"
    assert mixed.parameters["properties"]["num"]["type"] == "integer"


def test_schema_optional_bool_type_unwrapped():
    @coach_tool
    def flagged(flag: bool | None = None) -> str:
        """Optional bool."""
        return ""

    assert flagged.parameters["properties"]["flag"]["type"] == "boolean"
    assert "required" not in flagged.parameters


def test_schema_no_params():
    @coach_tool
    def noargs() -> str:
        """No args."""
        return "ok"

    assert noargs.parameters == {"type": "object", "properties": {}}


def test_schema_hides_the_apps_own_parameters_from_the_model():
    # `confirm` is set only when the athlete applies a card and
    # `expected_input_hash` comes from the preview it was built on — both
    # stay callable and out of the schema the model reasons over.
    @coach_tool
    def gated(
        through_date: str, confirm: bool = False, expected_input_hash: str | None = None
    ) -> str:
        """A previewing write."""
        return f"{through_date}:{confirm}:{expected_input_hash}"

    assert gated.parameters == {
        "type": "object",
        "properties": {"through_date": {"type": "string"}},
        "required": ["through_date"],
    }
    assert gated.invoke({"through_date": "d", "confirm": True}) == "d:True:None"


def test_schema_unknown_type_falls_back_to_string():
    @coach_tool
    def weird(payload: dict) -> str:
        """Unknown-typed param."""
        return ""

    assert weird.parameters["properties"]["payload"]["type"] == "string"


# invoke + execute_tool


def test_invoke_calls_underlying_function():
    @coach_tool
    def echo(value: str) -> str:
        """Echo value."""
        return f"got {value}"

    assert echo.invoke({"value": "hi"}) == "got hi"


def test_invoke_no_args():
    @coach_tool
    def ping() -> str:
        """Ping."""
        return "pong"

    assert ping.invoke() == "pong"


def test_to_openai_tools_wire_shape():
    @coach_tool
    def get_thing(name: str) -> str:
        """Fetch a thing."""
        return ""

    wire = to_openai_tools([get_thing])
    assert wire[0]["type"] == "function"
    assert wire[0]["function"]["name"] == "get_thing"
    assert wire[0]["function"]["description"] == "Fetch a thing."
    assert wire[0]["function"]["parameters"]["required"] == ["name"]


def test_execute_tool_dispatches_by_name():
    @coach_tool
    def alpha(x: str) -> str:
        """Alpha."""
        return f"alpha:{x}"

    @coach_tool
    def beta() -> str:
        """Beta."""
        return "beta"

    assert execute_tool([alpha, beta], "alpha", {"x": "1"}) == "alpha:1"
    assert execute_tool([alpha, beta], "beta", {}) == "beta"


def test_execute_tool_unknown_returns_error_json():
    result = execute_tool([], "missing", {})
    assert "Unknown tool 'missing'" in result


def test_execute_tool_reports_bad_arguments_as_an_argument_error():
    # A malformed call must not read like an intervals.icu outage: the model
    # has to see that *its arguments* were wrong so it can correct them.
    @coach_tool
    def strict(required_arg: str) -> str:
        """Strict."""
        return required_arg

    payload = json.loads(execute_tool([strict], "strict", {"wrong": "kwarg"}))
    assert "Invalid arguments" in payload["error"]
    assert "strict" in payload["error"]
    assert "wrong" in payload["message"]
    assert "Please try again later" not in payload["message"]


def test_execute_tool_real_failure_keeps_the_athlete_legible_message():
    def boom(x: str) -> str:
        raise RuntimeError("intervals down")

    tool = CoachTool(name="boom", description="", parameters={}, fn=boom)
    payload = json.loads(execute_tool([tool], "boom", {"x": "1"}))
    assert payload["error"] == "Failed to run tool boom."
    assert "Please try again later" in payload["message"]


def test_execute_tool_catches_tool_exception():
    def boom(x: str) -> str:
        raise RuntimeError("kaboom")

    tool = CoachTool(name="boom", description="", parameters={}, fn=boom)
    result = execute_tool([tool], "boom", {"x": "1"})
    assert "Failed to run tool boom" in result


# Smoke: real coach tools produce sane schemas


def test_real_tools_expose_expected_schema():
    from endury.coach.tools.activity_tools import make_activity_tools

    tools = make_activity_tools(MagicMock())
    by_name = {t.name: t for t in tools}
    assert "get_recent_activities" in by_name
    # Every param is defaulted, so nothing is required.
    params = by_name["get_recent_activities"].parameters
    assert "required" not in params
    assert params["properties"]["days"]["type"] == "integer"
    weekly = by_name["get_weekly_training_summary"].parameters
    assert weekly["properties"]["weeks"]["type"] == "integer"


def test_real_profile_tools_optional_and_bool_params():
    from endury.coach.tools.profile_tools import make_profile_tools

    tools = make_profile_tools(MagicMock(), MagicMock())
    by_name = {t.name: t for t in tools}
    update = by_name["update_athlete_profile"].parameters
    assert "required" not in update  # all fields optional
    assert update["properties"]["open_water_access"]["type"] == "boolean"
    assert by_name["update_memory"].parameters["required"] == ["content"]


async def test_an_async_tool_is_awaited_with_the_same_errors():
    @coach_tool
    async def slow(value: str) -> str:
        """Answer after a wait."""
        return json.dumps({"value": value})

    assert slow.is_async
    assert json.loads(await execute_tool_async([slow], "slow", {"value": "x"})) == {
        "value": "x"
    }
    bad = json.loads(await execute_tool_async([slow], "slow", {"nope": 1}))
    assert bad["error"] == "Invalid arguments for tool 'slow'."
    assert (
        "Unknown tool"
        in json.loads(await execute_tool_async([slow], "other", {}))["error"]
    )
