"""Calendar, ad-hoc workouts, activity debrief/trace/pairing, and inputs."""

from __future__ import annotations

import httpx
import pytest

from endury.browser import CRASH_MESSAGE, api
from endury.intervals.client import IntervalsApiError
from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalRow,
    IntervalsActivity,
    IntervalsActivityDetail,
    SportSettingsRow,
)
from endury.planning.pair_activity import PairingNotRecorded
from endury.store import takes as store_takes
from tests.browser.helpers import app, boot, call, commit_ten_k, ok
from tests.store.helpers import STORE_ID

RUN = IntervalsActivity(
    id="i42", start_date_local="2026-10-06T07:00:00", type="Run", moving_time=2400
)


def _with_run(fake) -> None:
    fake.activities.append(RUN)
    fake.details["i42"] = IntervalsActivityDetail(
        id="i42",
        type="Run",
        start_date_local="2026-10-06T07:00:00",
        moving_time=2400,
        icu_intervals=[IntervalRow(type="WORK", elapsed_time=2400)],
    )
    fake.streams["i42"] = [
        ActivityStream(type="velocity_smooth", data=[3.0] * 200),
        ActivityStream(type="heartrate", data=[140.0] * 200),
    ]


# calendar


async def test_calendar_data_carries_the_plan_and_the_activities(fake):
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()

    payload = await ok("calendar_data", start_date="2026-10-05", end_date="2026-10-18")

    assert payload["active_plan"]["id"] == plan_id
    assert [a["id"] for a in payload["activities"]] == ["i42"]


async def test_calendar_data_refuses_a_malformed_date(fake):
    await boot(fake)

    envelope = await call("calendar_data", start_date="05/10/2026", end_date="x")

    assert envelope["error_code"] == "invalid_request"
    assert envelope["error"] == (
        "Invalid start_date: expected YYYY-MM-DD, got '05/10/2026'"
    )


async def test_calendar_data_answers_an_empty_payload_when_it_fails(fake, monkeypatch):
    await boot(fake)

    def boom(*_args):
        raise RuntimeError("calendar down")

    monkeypatch.setattr(api, "build_calendar_data", boom)

    payload = await ok("calendar_data", start_date="2026-10-05", end_date="2026-10-11")

    assert payload["error"] == CRASH_MESSAGE
    assert payload["compliance_unavailable"] is True
    assert payload["plans"] == []


async def test_calendar_data_keeps_the_reason_intervals_gave(fake, monkeypatch):
    await boot(fake)

    def boom(*_args):
        raise IntervalsApiError(503, "busy")

    monkeypatch.setattr(api, "build_calendar_data", boom)

    payload = await ok("calendar_data", start_date="2026-10-05", end_date="2026-10-11")

    assert payload["error"] == "intervals.icu API error 503: busy"


async def test_delete_ad_hoc_workout_reaches_only_ad_hoc_events(fake):
    fake.events["coachie:adhoc:x1"] = {
        "id": 7,
        "external_id": "coachie:adhoc:x1",
        "category": "WORKOUT",
        "start_date_local": "2026-10-08T00:00:00",
    }
    await boot(fake)

    refused = await ok("delete_ad_hoc_workout", external_id="coachie:1:w0_mon")
    deleted = await ok("delete_ad_hoc_workout", external_id="coachie:adhoc:x1")

    assert refused == {"status": "error", "message": "Not an ad-hoc workout."}
    assert deleted == {"status": "ok", "deleted": 1}
    assert "coachie:adhoc:x1" not in fake.events


# activities


async def test_debrief_and_trace_read_the_activity(fake):
    _with_run(fake)
    await boot(fake)

    debrief = await ok("activity_debrief", activity_id="i42")
    trace = await ok("activity_trace", activity_id="i42")

    assert debrief["activity_id"] == "i42"
    assert debrief["duration_minutes"] == 40
    assert trace["activity_id"] == "i42"


async def test_the_debrief_of_a_race_names_the_race(fake):
    _with_run(fake)
    await boot(fake)
    debrief = await ok("activity_debrief", activity_id="i42")
    assert "race" not in debrief
    app().repos.races.upsert(
        name="Club 10K", race_date="2026-10-06", goal_type="running_10k", priority="C"
    )

    debrief = await ok("activity_debrief", activity_id="i42")

    assert debrief["race"] == {"name": "Club 10K", "priority": "C"}


async def test_a_missing_activity_is_not_found(fake):
    await boot(fake)

    envelope = await call("activity_debrief", activity_id="i404")

    assert envelope["error_code"] == "not_found"
    assert envelope["error"] == "This activity was not found on intervals.icu."


async def test_an_intervals_error_on_an_activity_is_upstream(fake):
    _with_run(fake)
    await boot(fake)
    fake.fail_next(400, "GET")

    envelope = await call("activity_trace", activity_id="i42")

    assert envelope["error_code"] == "upstream_error"
    assert envelope["error"].startswith("intervals.icu error:")


async def test_an_unreachable_intervals_is_upstream(fake, monkeypatch):
    await boot(fake)

    def unreachable(*_args):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(api, "debrief_view", unreachable)

    envelope = await call("activity_debrief", activity_id="i42")

    assert envelope["error_code"] == "upstream_error"
    assert envelope["error"] == "Could not reach intervals.icu: no route"


async def test_a_saved_take_reaches_its_own_note_and_the_next_boot(fake):
    await boot(fake)
    assert (await ok("activity_takes"))["takes"] == []

    envelope = await call(
        "save_activity_take", activity_id="i42", depth="quick", text=" Did its job. "
    )

    assert envelope["ok"] is True
    assert envelope["unsaved"] is False
    [take] = envelope["result"]["takes"]
    assert (take["activity_id"], take["depth"], take["text"]) == (
        "i42",
        "quick",
        "Did its job.",
    )
    assert app().rev == 0
    assert STORE_ID not in fake.events

    await boot(fake)

    assert (await ok("activity_takes"))["takes"] == [take]


@pytest.mark.parametrize(
    ("depth", "text"), [("medium", "Fine."), ("quick", "  ")], ids=["depth", "text"]
)
async def test_a_take_without_a_known_depth_or_a_text_is_refused(fake, depth, text):
    await boot(fake)

    envelope = await call(
        "save_activity_take", activity_id="i42", depth=depth, text=text
    )

    assert envelope["error_code"] == "invalid_request"
    assert store_takes.TAKES_ID not in fake.events


async def test_a_failed_take_upload_stays_unsaved_until_flush(fake):
    await boot(fake)
    await ok("activity_takes")
    fake.fail_next(400, "POST")

    envelope = await call(
        "save_activity_take", activity_id="i42", depth="full", text="Ok"
    )

    assert envelope["ok"] is True
    assert envelope["unsaved"] is True
    assert store_takes.TAKES_ID not in fake.events

    retry = await call("flush")

    assert retry["unsaved"] is False
    assert [row["text"] for row in store_takes.load(fake.client())] == ["Ok"]


async def test_a_read_only_boot_never_writes_the_takes_note(fake):
    await boot(fake, read_only=True)

    envelope = await call(
        "save_activity_take", activity_id="i42", depth="quick", text="Ok"
    )

    assert envelope["unsaved"] is False
    assert store_takes.TAKES_ID not in fake.events


async def test_pair_activity_with_an_event_id(fake):
    _with_run(fake)
    await boot(fake)

    paired = await ok("pair_activity", activity_id="i42", event_id=1234)

    assert paired == {"status": "ok", "activity_id": "i42", "event_id": 1234}
    assert fake.activities[0].paired_event_id == 1234


async def test_pair_activity_with_a_plan_session(fake):
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()
    row = app().repos.plan.get_sessions(plan_id)[0]
    event = fake.events[f"coachie:{plan_id}:{row.slot_id}"]

    paired = await ok("pair_activity", activity_id="i42", session_id=row.id)

    assert paired["event_id"] == event["id"]


def _run_session_after(plan_id: int, day: str):
    """The first running session of the plan dated after ``day``."""
    return next(
        row
        for row in sorted(app().repos.plan.get_sessions(plan_id), key=lambda r: r.date)
        if row.sport == "running" and row.date > day
    )


def _row(plan_id: int, session_id: int):
    return next(r for r in app().repos.plan.get_sessions(plan_id) if r.id == session_id)


async def test_a_session_done_on_another_day_moves_there_and_links(fake):
    """The run of Sunday's session on Tuesday: one tap makes the plan say so."""
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()
    row = _run_session_after(plan_id, "2026-10-07")
    planned_on = row.date

    linked = await ok("pair_activity", activity_id="i42", session_id=row.id)

    moved = _row(plan_id, row.id)
    event = fake.events[f"coachie:{plan_id}:{row.slot_id}"]
    assert linked["moved_from"] == planned_on
    assert (moved.date, moved.status, moved.matched_activity_id) == (
        "2026-10-06",
        "completed",
        "i42",
    )
    assert event["start_date_local"].startswith("2026-10-06")
    assert fake.activities[0].paired_event_id == event["id"]
    debrief = await ok("activity_debrief", activity_id="i42")
    assert debrief["plan_link"]["linked"]["session_id"] == row.id


async def test_linking_takes_the_activity_off_the_session_that_held_it(fake):
    """One activity completes one session, never two."""
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()
    first = _run_session_after(plan_id, "2026-10-05")
    second = _run_session_after(plan_id, first.date)
    await ok("pair_activity", activity_id="i42", session_id=first.id)

    linked = await ok("pair_activity", activity_id="i42", session_id=second.id)

    assert linked["unlinked"] == [first.id]
    assert _row(plan_id, first.id).matched_activity_id is None
    assert _row(plan_id, first.id).status == "unlinked"
    assert _row(plan_id, second.id).matched_activity_id == "i42"


async def test_unlink_activity_leaves_its_session_not_done(fake):
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()
    row = _run_session_after(plan_id, "2026-10-05")
    await ok("pair_activity", activity_id="i42", session_id=row.id)

    unlinked = await ok("unlink_activity", activity_id="i42")

    assert unlinked["session_id"] == row.id
    assert _row(plan_id, row.id).matched_activity_id is None
    assert _row(plan_id, row.id).status == "unlinked"


async def test_the_debrief_offers_the_session_the_watch_ran(fake):
    """The watch names the activity after its workout: that session leads."""
    _with_run(fake)
    await boot(fake)
    plan_id = await commit_ten_k()
    row = _run_session_after(plan_id, "2026-10-07")
    title = fake.events[f"coachie:{plan_id}:{row.slot_id}"]["name"]
    fake.details["i42"] = fake.details["i42"].model_copy(
        update={"name": f"Hillside - {title}"}
    )

    link = (await ok("activity_debrief", activity_id="i42"))["plan_link"]

    assert link["linked"] is None
    assert link["looks_like"] == row.id
    assert link["candidates"][0] == {
        "session_id": row.id,
        "title": title,
        "date": row.date,
        "duration_minutes": row.duration_minutes,
    }


async def test_pair_activity_needs_exactly_one_target(fake):
    await boot(fake)

    both = await call("pair_activity", activity_id="i42", event_id=1, session_id=2)
    neither = await call("pair_activity", activity_id="i42")

    assert both["error_code"] == "invalid_request"
    assert neither["error_code"] == "invalid_request"


async def test_pair_activity_with_no_such_session_is_not_found(fake):
    await boot(fake)

    envelope = await call("pair_activity", activity_id="i42", session_id=9)

    assert envelope["error_code"] == "not_found"


async def test_a_pairing_intervals_did_not_record_is_upstream(fake, monkeypatch):
    await boot(fake)

    def unrecorded(*_args):
        raise PairingNotRecorded("intervals.icu did not record the pairing.")

    monkeypatch.setattr(api, "pair_command", unrecorded)

    envelope = await call("pair_activity", activity_id="i42", event_id=1)

    assert envelope["error_code"] == "upstream_error"


async def test_put_activity_verdict_writes_feel_and_effort_separately(fake):
    _with_run(fake)
    await boot(fake)

    first = await ok("put_activity_verdict", activity_id="i42", feel=2)
    second = await ok("put_activity_verdict", activity_id="i42", icu_rpe=7)

    assert first == {"feel": 2, "rpe": None}
    assert second == {"feel": 2, "rpe": 7}
    assert (fake.activities[0].feel, fake.activities[0].icu_rpe) == (2, 7)


@pytest.mark.parametrize(
    "fields", [{}, {"feel": 0}, {"feel": 6}, {"icu_rpe": 0}, {"icu_rpe": 11}]
)
async def test_put_activity_verdict_refuses_a_value_out_of_range(fake, fields):
    _with_run(fake)
    await boot(fake)

    envelope = await call("put_activity_verdict", activity_id="i42", **fields)

    assert envelope["error_code"] == "invalid_request"
    assert fake.activities[0].feel is None


async def test_put_activity_verdict_on_a_missing_activity_is_not_found(fake):
    await boot(fake)

    envelope = await call("put_activity_verdict", activity_id="nope", feel=3)

    assert envelope["error_code"] == "not_found"


async def test_get_inputs_reads_the_account(fake):
    await boot(fake)

    inputs = await ok("get_inputs")

    assert inputs["available"] is True


async def test_put_thresholds_writes_every_ride_row_and_answers_fresh_inputs(fake):
    fake.athlete = AthleteProfile(
        id="i1",
        sportSettings=[
            SportSettingsRow(id=1, types=["Run"], lthr=165),
            SportSettingsRow(id=2, types=["Ride"], ftp=240),
            SportSettingsRow(id=3, types=["VirtualRide"], ftp=230),
        ],
    )
    await boot(fake)

    result = await ok("put_thresholds", sport="cycling", ftp_watts=255)

    assert result["types"] == ["Ride", "VirtualRide"]
    assert [row.ftp for row in fake.athlete.sport_settings or []] == [None, 255, 255]
    assert result["inputs"]["sports"]["cycling"]["thresholds"]["ftp"] == 255


async def test_put_thresholds_refuses_a_threshold_the_sport_does_not_use(fake):
    await boot(fake)

    envelope = await call("put_thresholds", sport="swimming", ftp_watts=255)

    assert envelope["error_code"] == "invalid_request"
