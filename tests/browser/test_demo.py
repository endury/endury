"""The demo athlete: booting with ``{"demo": "1"}`` and no intervals.icu key."""

from __future__ import annotations

import os
from datetime import timedelta

from endury.browser import demo, rpc
from endury.common import clock
from tests.browser.helpers import app, ok
from tests.doubles import FakeLLM
from tests.store.helpers import SIM_TODAY, this_monday

#: The Monday the demo plan started: four weeks before the pinned today's.
PLAN_START = this_monday(SIM_TODAY) - timedelta(weeks=4)


async def _boot_demo(**settings: str) -> dict:
    envelope = await rpc.boot({"demo": "1", **settings})
    assert envelope["ok"] is True, envelope
    return envelope["result"]


async def _first_four_weeks() -> dict:
    return await ok(
        "calendar_data",
        start_date=PLAN_START.isoformat(),
        end_date=(PLAN_START + timedelta(weeks=4, days=-1)).isoformat(),
    )


async def test_demo_boots_without_a_key():
    result = await _boot_demo()

    assert result["coach_ready"] is False
    assert result["read_only"] is False
    assert result["rev"] == 1
    assert (await rpc.dispatch("health"))["unsaved"] is False


async def test_demo_leaves_the_clock_and_environment_alone():
    environ = dict(os.environ)

    await _boot_demo()

    assert clock.today() == SIM_TODAY
    assert dict(os.environ) == environ


async def test_demo_plan_is_a_half_ironman_four_weeks_in():
    await _boot_demo()

    active = (await ok("planner_active"))["active_plan"]

    assert active["name"] == demo.PLAN_NAME
    assert active["goal_type"] == "half_ironman"
    assert active["start_date"] == PLAN_START.isoformat()


async def test_demo_plan_builds_whatever_is_on_offer(offered_as_shipped):
    await _boot_demo()

    assert (await ok("planner_active"))["active_plan"]["goal_type"] == "half_ironman"


async def test_demo_past_weeks_are_completed_but_one():
    await _boot_demo()

    calendar = await _first_four_weeks()

    statuses = [session["status"] for session in calendar["plans"]]
    assert statuses.count("missed") == 1
    assert statuses.count("completed") == len(statuses) - 1
    paired = {session["matched_activity_id"] for session in calendar["plans"]}
    unplanned = [a for a in calendar["activities"] if a["id"] not in paired]
    assert [a["sport"] for a in unplanned] == ["cycling"] * 4


async def test_demo_today_is_still_planned():
    await _boot_demo()

    calendar = await ok(
        "calendar_data",
        start_date=SIM_TODAY.isoformat(),
        end_date=SIM_TODAY.isoformat(),
    )

    statuses = [session["status"] for session in calendar["plans"]]
    assert statuses
    assert set(statuses) == {"pending"}


async def _completed(sport: str) -> str:
    """The id of a completed activity of ``sport`` in the plan's first weeks."""
    calendar = await _first_four_weeks()
    return next(
        session["matched_activity_id"]
        for session in calendar["plans"]
        if session["status"] == "completed" and session["sport"] == sport
    )


async def test_demo_run_has_a_debrief_and_a_trace_from_streams():
    await _boot_demo()
    activity_id = await _completed("running")

    debrief = await ok("activity_debrief", activity_id=activity_id)
    trace = await ok("activity_trace", activity_id=activity_id)

    assert debrief["sport"] == "running"
    assert debrief["lthr"] == 168
    assert sum(debrief["hr_zone_minutes"]) > 0
    assert trace["source"] == "streams"
    assert trace["metric"] == "pace"
    assert len(trace["pct"]) == 96
    assert "elevation" in trace


async def test_demo_pool_swim_draws_from_its_lengths():
    await _boot_demo()
    activity_id = await _completed("swimming")

    debrief = await ok("activity_debrief", activity_id=activity_id)
    trace = await ok("activity_trace", activity_id=activity_id)

    assert debrief["sport"] == "swimming"
    assert debrief["css_min_100m"] == "1:50"
    assert debrief["intervals"]
    assert all(row["distance_km"] > 0 for row in debrief["intervals"])
    assert trace["source"] == "intervals"
    assert trace["metric"] == "pace"
    assert len(trace["pct"]) == len(debrief["intervals"])


async def test_demo_history_is_measured():
    await _boot_demo()

    athlete = (await ok("planner_bootstrap"))["athlete_data"]
    inputs = await ok("get_inputs")

    assert athlete["history_sufficient"] is True
    assert athlete["measured_sports"] == ["cycling", "running", "swimming"]
    assert inputs["upload_destinations"] == ["Garmin"]


async def test_demo_is_the_same_on_the_same_day():
    def sessions() -> list[tuple[str, str, float]]:
        repos = app().repos
        plan = repos.plan.get_active()
        assert plan is not None
        return [
            (row.date, row.activity_key, row.target_tss)
            for row in repos.plan.get_sessions(plan.id)
        ]

    await _boot_demo()
    first = sessions()
    await _boot_demo()

    assert sessions() == first


async def test_demo_ignores_intervals_settings_and_keeps_the_coach():
    envelope = await rpc.boot(
        {"demo": "1", "intervals_api_key": "real-key", "intervals_athlete_id": "i9"},
        llm=FakeLLM(),
    )

    assert envelope["ok"] is True, envelope
    assert envelope["result"]["coach_ready"] is True
    assert app().config.get_intervals_api_key() == "demo"
    assert app().config.intervals_athlete_id != "i9"


async def test_a_failed_demo_seed_fails_the_boot(monkeypatch):
    def refuse(*_args: object) -> None:
        raise RuntimeError("no plan")

    monkeypatch.setattr(demo, "seed", refuse)

    envelope = await rpc.boot({"demo": "1"})

    assert envelope["ok"] is False
    assert envelope["error_code"] == "internal_error"
    assert rpc._app is None
