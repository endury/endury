"""The brief: ``session_brief``, briefs in the takes note, race facts."""

from __future__ import annotations

import pytest

from endury.browser import api
from endury.store import takes as store_takes
from tests.browser.helpers import app, boot, call, commit_ten_k, ok

SHEET = {"subject": {"handle": "session:7"}, "thin": []}


@pytest.fixture
def seen(monkeypatch):
    """Stub the sheet builder; the list collects the keyword arguments it got."""
    calls: list[dict] = []

    def build(**kwargs):
        calls.append(kwargs)
        return SHEET

    monkeypatch.setattr(api, "build_brief", build)
    return calls


async def test_session_brief_serves_the_sheet_for_a_session(fake, seen):
    await boot(fake)

    assert await ok("session_brief", session_id=7) == SHEET
    assert seen[0]["session_id"] == 7
    assert seen[0]["race_date"] is None
    assert seen[0]["repos"] is app().repos


async def test_session_brief_serves_the_sheet_for_a_race(fake, seen):
    await boot(fake)

    assert await ok("session_brief", race_date="2026-12-20") == SHEET
    assert seen[0]["race_date"] == "2026-12-20"


@pytest.mark.parametrize(
    "params", [{}, {"session_id": 7, "race_date": "2026-12-20"}], ids=["none", "both"]
)
async def test_session_brief_needs_exactly_one_subject(fake, seen, params):
    await boot(fake)

    envelope = await call("session_brief", **params)

    assert envelope["error_code"] == "invalid_request"
    assert seen == []


async def test_an_unknown_subject_is_not_found(fake, monkeypatch):
    await boot(fake)

    def unknown(**_kwargs):
        raise ValueError("No planned session 99.")

    monkeypatch.setattr(api, "build_brief", unknown)

    envelope = await call("session_brief", session_id=99)

    assert envelope["error_code"] == "not_found"
    assert envelope["error"] == "No planned session 99."


async def test_a_brief_and_a_take_share_a_handle_and_survive_a_boot(fake):
    await boot(fake)
    await call(
        "save_activity_take", activity_id="session:7", depth="quick", text="Hold 150."
    )
    await call(
        "save_activity_take",
        activity_id="session:7",
        depth="quick",
        text="Hold 150 early.",
        kind="brief",
    )

    await boot(fake)
    rows = (await ok("activity_takes"))["takes"]

    assert [(r.get("kind", "take"), r["text"]) for r in rows] == [
        ("take", "Hold 150."),
        ("brief", "Hold 150 early."),
    ]
    assert store_takes.TAKES_ID in fake.events


async def test_an_unknown_kind_is_refused(fake):
    await boot(fake)

    envelope = await call(
        "save_activity_take",
        activity_id="session:7",
        depth="quick",
        text="Ok",
        kind="plan",
    )

    assert envelope["error_code"] == "invalid_request"
    assert store_takes.TAKES_ID not in fake.events


async def test_race_rows_carry_the_table_facts(fake):
    await boot(fake)
    await commit_ten_k()
    repos = app().repos
    repos.races.upsert(
        name="Autumn 10K",
        race_date="2026-12-20",
        goal_type="running_10k",
        distance_km=10.0,
        climb_m=85.0,
        notes="Start 9:00, bag drop at the school.",
    )
    repos.races.upsert(
        name="Local 5K", race_date="2026-11-08", priority="C", distance_km=5.0
    )

    payload = await ok("calendar_data", start_date="2026-11-02", end_date="2026-12-27")

    by_date = {race["race_date"]: race for race in payload["races"]}
    goal = by_date["2026-12-20"]
    assert (goal["goal_type"], goal["distance_km"], goal["climb_m"], goal["notes"]) == (
        "running_10k",
        10.0,
        85.0,
        "Start 9:00, bag drop at the school.",
    )
    tune_up = by_date["2026-11-08"]
    assert (tune_up["distance_km"], tune_up["climb_m"], tune_up["notes"]) == (
        5.0,
        None,
        None,
    )
    assert tune_up["goal_type"] is None


async def test_the_demo_athlete_gets_a_real_sheet_for_a_session_and_the_race():
    """End to end, no stub: the demo's plan and history build both sheets."""
    from datetime import timedelta

    from endury.browser import rpc
    from tests.store.helpers import SIM_TODAY

    envelope = await rpc.boot({"demo": "1"})
    assert envelope["ok"] is True, envelope
    calendar = await ok(
        "calendar_data",
        start_date=(SIM_TODAY + timedelta(days=1)).isoformat(),
        end_date=(SIM_TODAY + timedelta(days=7)).isoformat(),
    )
    upcoming = next(s for s in calendar["plans"] if s["status"] == "pending")

    sheet = await ok("session_brief", session_id=upcoming["id"])

    assert sheet["subject"]["handle"] == f"session:{upcoming['id']}"
    assert sheet["subject"]["date"] == upcoming["date"]
    assert sheet["state"]["ctl"] is not None
    assert sheet["hold"]["card"]
    assert isinstance(sheet["rubric"], list)

    race_date = (await ok("planner_active"))["active_plan"]["goal_date"]
    race = await ok("session_brief", race_date=race_date)

    assert race["subject"]["kind"] == "race"
    assert race["subject"]["handle"] == f"race:{race_date}"
    assert race["fuel"] is None or race["fuel"]["carbs_g_per_h"]
