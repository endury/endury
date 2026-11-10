"""Shared helpers for the store tests: devices, table dumps, the note's wire format."""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from endury.database.sqlite.session import DatabaseSession
from endury.intervals.client import IntervalsClient
from endury.intervals.models import AthleteProfile, SportSettingsRow
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    GenerateSuccess,
    RaceGoalModel,
)
from endury.store import load, mark_clean, restore, save
from endury.wiring.container import RepositoryContainer
from tests.doubles import FakeIntervals

#: The eight tables the store mirrors (PLAN "Store"): everything durable.
DURABLE_TABLES = (
    "plans",
    "plan_sessions",
    "macro_plans",
    "athlete_goals",
    "athlete_self_assessment",
    "athlete_profile",
    "athlete_memory",
    "athlete_memory_history",
)

STORE_ID = "coachie:store"
FENCE = "```coachie-state"

#: A Wednesday; the plan below starts the Monday before it.
SIM_TODAY = date(2026, 10, 7)


def run_athlete() -> AthleteProfile:
    """An intervals.icu athlete with running thresholds only."""
    return AthleteProfile(
        id="i1",
        sportSettings=[
            SportSettingsRow(
                types=["Run"], lthr=165, max_hr=190, threshold_pace=1000 / 270
            )
        ],
    )


@dataclass
class Device:
    """One browser session: its own in-memory DB, the shared fake account."""

    container: RepositoryContainer
    client: IntervalsClient
    rev: int

    @property
    def session(self) -> DatabaseSession:
        """The device's database session."""
        return self.container.session

    def save(self) -> int:
        """Save against the rev this device last saw, remembering the new one."""
        self.rev = save(self.client, self.session, expected_rev=self.rev)
        return self.rev


def boot(fake: FakeIntervals) -> Device:
    """Boot a fresh device from whatever the fake account holds.

    Mirrors the browser boot order: load, restore, then take the dirty
    baseline.
    """
    client = fake.client()
    container = RepositoryContainer.in_memory()
    container.bind_intervals_client(client)
    snapshot = load(client)
    restore(container.session, snapshot)
    mark_clean(container.session)
    return Device(container, client, snapshot.rev if snapshot is not None else 0)


def query(session: DatabaseSession, sql: str) -> list[dict[str, Any]]:
    """Run one read and return its rows as dicts."""
    with session.cursor() as cursor:
        cursor.execute(sql)
        return [dict(row) for row in cursor.fetchall()]


def execute(session: DatabaseSession, sql: str) -> None:
    """Run one write statement."""
    with session.cursor() as cursor:
        cursor.execute(sql)


def table_rows(session: DatabaseSession, table: str) -> list[str]:
    """Every row of ``table``, canonicalized and sorted (ids included)."""
    return sorted(
        json.dumps(row, sort_keys=True)
        for row in query(session, f"SELECT * FROM {table}")
    )


def sequences(session: DatabaseSession) -> dict[str, int]:
    """``sqlite_sequence`` for the durable tables."""
    return {
        row["name"]: row["seq"]
        for row in query(session, "SELECT name, seq FROM sqlite_sequence")
        if row["name"] in DURABLE_TABLES
    }


#: What the store carries of the plan tables: archived plans, their
#: sessions and their macro rows stay behind (endury/store/tables.py).
_LIVE_ROWS = {
    "plans": "SELECT * FROM plans WHERE status != 'archived'",
    "plan_sessions": (
        "SELECT * FROM plan_sessions WHERE plan_id IN "
        "(SELECT id FROM plans WHERE status != 'archived')"
    ),
    "macro_plans": (
        "SELECT * FROM macro_plans WHERE status != 'archived' OR id IN "
        "(SELECT macro_plan_id FROM plans WHERE status != 'archived')"
    ),
}


def durable_state(session: DatabaseSession) -> dict[str, Any]:
    """Every row the store carries, per durable table, plus their sequences."""
    state: dict[str, Any] = {
        table: sorted(
            json.dumps(row, sort_keys=True) for row in query(session, _LIVE_ROWS[table])
        )
        if table in _LIVE_ROWS
        else table_rows(session, table)
        for table in DURABLE_TABLES
    }
    state["sqlite_sequence"] = sequences(session)
    return state


def store_events(fake: FakeIntervals) -> dict[str, dict[str, Any]]:
    """The store's own events on the fake calendar, by external id."""
    return {
        key: event
        for key, event in fake.events.items()
        if key == STORE_ID or key.startswith(f"{STORE_ID}:")
    }


def _blob_span(description: str) -> tuple[int, int]:
    """Start/end offsets of the blob inside the LAST coachie-state fence."""
    fence = description.rindex(FENCE)
    start = description.index("\n", fence) + 1
    return start, description.index("```", start)


def decode_note(description: str) -> tuple[str, dict[str, Any]]:
    """Split a single-chunk note into (text above the fence, decoded state)."""
    start, end = _blob_span(description)
    blob = "".join(description[start:end].split())
    state = json.loads(zlib.decompress(base64.b64decode(blob)))
    return description[: description.rindex(FENCE)], state


def encode_state(description: str, state: dict[str, Any]) -> str:
    """Return ``description`` with its fenced blob replaced by ``state``."""
    start, end = _blob_span(description)
    blob = base64.b64encode(zlib.compress(json.dumps(state).encode())).decode()
    return description[:start] + blob + "\n" + description[end:]


def tamper_state(fake: FakeIntervals, mutate: Callable[[dict[str, Any]], None]) -> None:
    """Rewrite the stored note's state in place, as another bundle would."""
    note = fake.events[STORE_ID]
    _, state = decode_note(note["description"])
    mutate(state)
    note["description"] = encode_state(note["description"], state)


def this_monday(today: date) -> date:
    """The Monday of ``today``'s week."""
    return today - timedelta(days=today.weekday())


def commit_half_marathon(
    repos: RepositoryContainer, client: IntervalsClient, *, today: date
) -> int:
    """Generate and commit a half-marathon plan starting this Monday.

    Needs a stored running self-assessment (the fake account has no
    history). Returns the committed plan id.
    """
    start = this_monday(today)
    request = GenerateRequest(
        races=[
            RaceGoalModel(
                goal_type="running_half_marathon",
                race_date=(start + timedelta(weeks=16, days=6)).isoformat(),
            )
        ],
        training_start=start.isoformat(),
        accept_experimental=True,  # 17 weeks: beyond the checked horizons
        ambition_level="moderate",
    )
    result = run_generate(request, repos=repos, progress=lambda _payload: None)
    assert isinstance(result, GenerateSuccess), result
    outcome = run_commit(
        CommitRequest(
            name="Road to the half", generate_request=request, generate_result=result
        ),
        repos=repos,
        maybe_get_coach=lambda: None,
        client=client,
    )
    assert outcome["status"] == "ok", outcome
    assert outcome["push"]["status"] == "ok", outcome["push"]
    return int(outcome["plan_id"])
