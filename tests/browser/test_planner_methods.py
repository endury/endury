"""The planner methods: bootstrap, generate, commit, reads, edits, replan, delete."""

from __future__ import annotations

from datetime import timedelta

from endury.browser import rpc
from endury.intervals.models import IntervalsActivity
from endury.planner.foundations.types import GoalType, SportType
from endury.planning.athlete_state import resolve_athlete_state
from endury.planning.no_plan import NO_ACTIVE_PLAN_MESSAGE
from tests.browser.helpers import (
    RUNNING_VOLUME,
    TEN_K,
    app,
    boot,
    call,
    commit_ten_k,
    ok,
)
from tests.doubles import FakeLLM
from tests.store.helpers import SIM_TODAY, STORE_ID, decode_note


def _pushed(fake, plan_id: int) -> dict[str, dict]:
    """The plan's own workout events on the fake calendar."""
    prefix = f"coachie:{plan_id}:"
    return {k: v for k, v in fake.events.items() if k.startswith(prefix)}


def _lingering(fake, plan_id: int, replanned: dict) -> set[str]:
    """Plan workouts on the calendar that the replanned plan no longer has."""
    live = {
        f"coachie:{plan_id}:{session['slot_id']}"
        for session in replanned["plan_result"]["sessions"]
    }
    return {
        external_id
        for external_id, event in _pushed(fake, plan_id).items()
        if external_id not in live and not event["category"].startswith("RACE")
    }


def _future_session(plan_id: int):
    """A pending session after today, not on race day."""
    return next(
        row
        for row in app().repos.plan.get_sessions(plan_id)
        if row.date > "2026-10-08" and row.date < "2026-12-01"
    )


async def test_bootstrap_and_self_assessment(fake):
    await boot(fake)

    bootstrap = await ok("planner_bootstrap")
    stored = await ok("put_self_assessment", **RUNNING_VOLUME)

    goals = {goal["id"]: goal for goal in bootstrap["goal_types"]}
    assert goals["running_5k"]["maturity"] == "experimental"
    assert goals["running_marathon"]["maturity"] == "experimental"
    assert goals["running_marathon"]["maturity_note"] == (
        "This plan type is built from training research, and its sessions "
        "have not yet been reviewed one by one."
    )
    assert stored == {
        "status": "ok",
        "self_assessment": {
            "running": {"weekly_hours": 5.0, "longest_session_min": 100}
        },
    }


async def test_bootstrap_reads_history_once_and_sizes_hours_per_goal(fake):
    fake.activities = [
        IntervalsActivity(
            id=f"i{days}",
            start_date_local=(SIM_TODAY - timedelta(days=days)).isoformat(),
            type="Run",
            moving_time=3600,
        )
        for days in range(3, 60, 7)
    ]
    await boot(fake)
    fake.calls.clear()

    hours = (await ok("planner_bootstrap"))["athlete_data"]["measured_weekly_hours"]

    reads = [path for method, path in fake.calls if method == "GET"]
    assert sum(path.endswith("/activities") for path in reads) == 1
    for goal in (GoalType.RUNNING_5K, GoalType.RUNNING_MAINTENANCE):
        state = resolve_athlete_state(app().repos, goal).athlete.sports
        assert hours[goal.value] == {
            "running": round(state[SportType.RUNNING].recent_weekly_hours, 1)
        }
    assert hours["running_5k"] != hours["running_maintenance"]


async def test_generate_without_athlete_data_answers_the_refusal_event(fake):
    await boot(fake)

    event = await ok("generate_plan", **TEN_K)

    assert event["type"] == "result"
    assert event["source"] == "planner"
    assert event["status"] == "error"
    assert event["error_code"] == "athlete_data_required"
    assert event["missing_sports"] == ["running"]


async def test_generate_asks_before_building_an_experimental_plan(fake):
    """The wizard's generate call: the refusal, then the confirmed preview."""
    await boot(fake)
    await ok("put_self_assessment", **RUNNING_VOLUME)
    five_k = {**TEN_K, "races": [{**TEN_K["races"][0], "goal_type": "running_5k"}]}
    unconfirmed = {k: v for k, v in five_k.items() if k != "accept_experimental"}

    refused = await ok("generate_plan", **unconfirmed)
    built = await ok("generate_plan", **five_k)

    assert refused["type"] == "result"
    assert refused["status"] == "error"
    assert refused["error_code"] == "experimental_plan"
    assert refused["reasons"] == ["goal_experimental"]
    assert refused["error_message"].endswith(
        "You can still build it. Confirm to go ahead."
    )
    assert built["status"] == "ok"
    assert built["maturity"] == "experimental"
    assert built["maturity_reasons"] == ["goal_experimental"]
    assert built["maturity_note"] == (
        "This plan type is built from training research, and its sessions "
        "have not yet been reviewed one by one."
    )


async def test_generate_refuses_a_malformed_request(fake):
    await boot(fake)

    envelope = await call("generate_plan", races=[{"goal_type": "running_10k"}])

    assert envelope["error_code"] == "invalid_request"


async def test_commit_persists_pushes_and_saves_the_store(fake):
    await boot(fake, llm=FakeLLM())

    plan_id = await commit_ten_k()

    assert _pushed(fake, plan_id)
    active = await ok("planner_active")
    assert active["active_plan"]["id"] == plan_id
    macro = await ok("planner_macro")
    assert macro["macro_plan"]["goal_type"] == "running_10k"
    assert (await ok("get_races"))["races"][0]["goal_type"] == "running_10k"
    _, state = decode_note(fake.events[STORE_ID]["description"])
    assert [row["id"] for row in state["tables"]["plans"]] == [plan_id]
    assert app().runtime.active_plan_summary
    assert (await call("flush"))["unsaved"] is False


async def test_commit_without_the_preview_is_refused(fake):
    await boot(fake)

    outcome = await ok("planner_commit", name="x", generate_request=TEN_K)

    assert outcome == {
        "status": "error",
        "message": "Commit requires the previewed generate result.",
    }


async def test_push_and_sync_status_follow_the_calendar(fake):
    await boot(fake)
    plan_id = await commit_ten_k()
    one = next(iter(_pushed(fake, plan_id)))
    del fake.events[one]

    status = await ok("planner_sync_status")
    pushed = await ok("planner_push")

    assert status["available"] is True
    assert status["out_of_sync"] >= 1
    assert pushed["status"] == "ok"
    assert one in fake.events


async def test_reschedule_moves_the_session_and_its_event(fake):
    await boot(fake)
    plan_id = await commit_ten_k()
    row = _future_session(plan_id)
    new_date = "2026-10-31" if row.date != "2026-10-31" else "2026-10-30"

    moved = await ok("reschedule_session", session_id=row.id, date=new_date)

    assert moved["status"] == "ok"
    assert moved["date"] == new_date
    event = next(
        e
        for e in _pushed(fake, plan_id).values()
        if e["external_id"].endswith(row.slot_id)
    )
    assert event["start_date_local"].startswith(new_date)


async def test_reschedule_refuses_a_malformed_session_id(fake):
    await boot(fake)

    envelope = await call("reschedule_session", session_id="first", date="2026-10-20")

    assert envelope["error_code"] == "invalid_request"


async def test_replan_regenerates_from_next_monday(fake):
    await boot(fake)
    await commit_ten_k()

    event = await ok("replan")

    assert event["type"] == "result"
    assert event["status"] == "ok"
    assert event["action"] == "replanned"


async def test_replan_asks_before_overwriting_hand_edited_events(fake):
    await boot(fake)
    plan_id = await commit_ten_k()
    edited = next(
        e
        for e in _pushed(fake, plan_id).values()
        if e["start_date_local"] > "2026-10-20"
    )
    edited["name"] = "My own workout"

    refusal = await ok("replan")
    confirmed = await ok("replan", confirm=True)

    assert refusal["error_code"] == "confirm_required"
    assert refusal["diff"][0]["external_id"] == edited["external_id"]
    assert confirmed["type"] == "result"
    assert confirmed["status"] == "ok"


async def test_a_failed_replan_cleanup_is_reported_and_retried_by_sync(fake):
    await boot(fake)
    plan_id = await commit_ten_k()
    fake.fail_next(400, "PUT")  # the bulk-delete of the replaced workouts

    replanned = await ok("replan")
    lingering = _lingering(fake, plan_id, replanned)
    status = await ok("planner_sync_status")
    pushed = await ok("planner_push")
    healed = await ok("planner_sync_status")

    assert lingering
    assert replanned["push"]["status"] == "ok"
    assert replanned["push"]["leftover"] == len(lingering)
    assert status["summary"]["leftover"] == len(lingering)
    assert status["out_of_sync"] >= len(lingering)
    assert pushed["push"]["leftover"] == 0
    assert not lingering & set(fake.events)
    assert healed["summary"]["leftover"] == 0


async def test_the_next_replan_retries_a_failed_cleanup(fake):
    await boot(fake)
    plan_id = await commit_ten_k()
    fake.fail_next(400, "PUT")
    first = await ok("replan")

    second = await ok("replan")

    assert first["push"]["leftover"] > 0
    assert second["push"]["leftover"] == 0
    assert _lingering(fake, plan_id, second) == set()


async def test_replan_without_a_plan_is_an_error_envelope(fake):
    await boot(fake)

    envelope = await call("replan")

    assert envelope["ok"] is False
    assert envelope["error"] == NO_ACTIVE_PLAN_MESSAGE


async def test_delete_archives_the_plan_and_clears_its_events(fake):
    await boot(fake)
    plan_id = await commit_ten_k()

    outcome = await ok("planner_delete")

    assert outcome["status"] == "ok"
    assert outcome["plan_id"] == plan_id
    assert _pushed(fake, plan_id) == {}
    assert (await ok("planner_delete"))["status"] == "empty"


async def test_delete_leaves_the_workouts_before_today_on_the_calendar(
    fake, monkeypatch
):
    await boot(fake)
    plan_id = await commit_ten_k()
    monkeypatch.setenv("COACHING_BUDDY_SIM_DATE", "2026-10-21")
    before = _pushed(fake, plan_id)

    assert (await ok("planner_delete"))["status"] == "ok"

    past = {k for k, e in before.items() if e["start_date_local"] < "2026-10-21"}
    assert past
    assert set(_pushed(fake, plan_id)) == past
    # ... and the calendar still shows them, planned side and all.
    payload = await ok("calendar_data", start_date="2026-10-05", end_date="2026-10-20")
    assert payload["plans"] == []
    assert {r["external_id"] for r in payload["calendar_only"]} == past
    assert {(r["plan_id"], r["status"]) for r in payload["calendar_only"]} == {
        (plan_id, "missed")
    }


# The planner's progress frames, posted to the page while a call runs


async def test_generate_hands_the_planner_s_four_stages_to_the_progress_callback(fake):
    await boot(fake)
    await ok("put_self_assessment", **RUNNING_VOLUME)
    frames = []

    envelope = await rpc.dispatch("generate_plan", TEN_K, progress=frames.append)

    assert envelope["ok"] is True
    assert [f["step"] for f in frames] == [1, 2, 3, 4]
    assert {f["type"] for f in frames} == {"progress"}
    assert all(f["total"] == 4 and f["message"] for f in frames)


async def test_replan_hands_the_same_stages_to_the_progress_callback(fake):
    await boot(fake)
    await commit_ten_k()
    frames = []

    envelope = await rpc.dispatch("replan", {"confirm": True}, progress=frames.append)

    assert envelope["ok"] is True
    assert [f["step"] for f in frames] == [1, 2, 3, 4]


async def test_a_method_without_stages_ignores_the_progress_callback(fake):
    await boot(fake)
    frames = []

    envelope = await rpc.dispatch("planner_active", {}, progress=frames.append)

    assert envelope["ok"] is True
    assert frames == []
