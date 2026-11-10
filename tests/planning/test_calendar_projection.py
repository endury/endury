"""Tests for the intervals.icu calendar readback projection."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from endury.intervals.models import IntervalsActivity
from endury.planning.calendar_projection import (
    _activity_payload,
    _ad_hoc_payload,
    fetch_intervals_calendar,
)
from endury.training.load import LoadMetricContext


class _FakeIntervalsClient:
    """Stub matching IntervalsClient's read-method signatures (no network)."""

    def __init__(
        self,
        events: list[SimpleNamespace] | None = None,
        activities: list[SimpleNamespace] | None = None,
    ) -> None:
        self._events = events or []
        self._activities = activities or []

    def list_events(
        self, oldest: date, newest: date, category: str | None = None
    ) -> list[SimpleNamespace]:
        del oldest, newest, category
        return self._events

    def list_activities(self, oldest: date, newest: date) -> list[SimpleNamespace]:
        del oldest, newest
        return self._activities


class _RaisingIntervalsClient:
    """Stub whose reads always fail, simulating an unreachable API."""

    def list_events(
        self, oldest: date, newest: date, category: str | None = None
    ) -> list[SimpleNamespace]:
        raise ConnectionError("unreachable")

    def list_activities(self, oldest: date, newest: date) -> list[SimpleNamespace]:
        raise ConnectionError("unreachable")


class _PairingIntervalsClient(_FakeIntervalsClient):
    """Stub that also records pairing writes, which can fail or not stick."""

    def __init__(
        self,
        events: list[SimpleNamespace],
        activities: list[IntervalsActivity],
        *,
        fails: bool = False,
        sticks: bool = True,
    ) -> None:
        super().__init__(events, activities)  # type: ignore[arg-type]
        self.pairs: list[tuple[str, int]] = []
        self._fails = fails
        self._sticks = sticks

    def pair_activity(self, activity_id: str, event_id: int) -> IntervalsActivity:
        if self._fails:
            raise ConnectionError("unreachable")
        self.pairs.append((activity_id, event_id))
        return IntervalsActivity(
            id=activity_id,
            paired_event_id=event_id if self._sticks else None,
            compliance=120.0,
        )


def _event(external_id: str | None, event_id: int | None) -> SimpleNamespace:
    return SimpleNamespace(
        external_id=external_id, id=event_id, start_date_local=None, type=None
    )


def _workout(
    external_id: str,
    event_id: int,
    day: str = "2026-02-10",
    type_: str = "Run",
    name: str = "Easy Run",
) -> SimpleNamespace:
    return SimpleNamespace(
        external_id=external_id,
        id=event_id,
        start_date_local=f"{day}T00:00:00",
        type=type_,
        name=name,
    )


def _activity(
    activity_id: int | None,
    paired_event_id: int | None,
    compliance: float | None = None,
    type_: str = "Run",
    day: str = "2026-02-10",
    name: str = "Morning Run",
) -> IntervalsActivity:
    return IntervalsActivity(
        id=None if activity_id is None else str(activity_id),
        name=name,
        start_date_local=f"{day}T07:00:00",
        type=type_,
        moving_time=3600,
        icu_training_load=62.0,
        distance=12000.0,
        paired_event_id=paired_event_id,
        compliance=compliance,
    )


def _session(
    session_id: int,
    slot_id: str = "s1",
    status: str = "pending",
    matched_activity_id: int | str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=session_id,
        plan_id=7,
        slot_id=slot_id,
        date="2026-02-10",
        week_index=0,
        phase_type="base",
        sport="running",
        activity_key="run_easy",
        family="easy",
        target_tss=60.0,
        duration_minutes=60.0,
        workout_json=None,
        matched_activity_id=matched_activity_id,
        status=status,
    )


def test_activity_payload_maps_the_intervals_wire_shape() -> None:
    """Known sports map to planner names; unknown types pass through."""
    payload = _activity_payload(_activity(999, paired_event_id=None))
    assert payload == {
        "id": "999",
        "name": "Morning Run",
        "sport": "running",
        "type": "Run",
        "start_time": "2026-02-10T07:00:00",
        "distance": 12000.0,
        "duration": 3600,
        "tss": 62.0,
        "feel": None,
        "rpe": None,
        "source": None,
        "device_name": None,
        "hr_zone_times": None,
        "pace_zone_times": None,
        "average_speed_mps": None,
        "gap_mps": None,
        "average_hr": None,
        "max_hr": None,
        "average_cadence": None,
        "decoupling_pct": None,
        "average_power": None,
        "normalized_power": None,
        "intensity_factor": None,
        "elevation_gain_meters": None,
        "intervals_activity_url": "https://intervals.icu/activities/999",
    }

    unknown = _activity_payload(IntervalsActivity(id="1", type="Yoga"))
    assert unknown["sport"] == "Yoga"
    assert unknown["name"] == "Yoga"

    untyped = _activity_payload(IntervalsActivity(id="2"))
    assert untyped["sport"] == "other"
    assert untyped["name"] == "Activity"


def test_activity_payload_carries_the_recorded_numbers() -> None:
    """The session detail's Pace/Avg Power tile and Recorded block read
    these keys (data/adapters.js actualStatsFor / recordedExtrasFor)."""
    payload = _activity_payload(
        IntervalsActivity(
            id="7",
            type="Ride",
            average_speed=8.4,
            gap=8.6,
            average_heartrate=134,
            max_heartrate=171,
            average_cadence=88.4,
            decoupling=3.24,
            icu_average_watts=200,
            icu_weighted_avg_watts=257,
            icu_intensity=83.4567,
            total_elevation_gain=525.0,
        )
    )
    assert payload["average_speed_mps"] == 8.4
    assert payload["gap_mps"] == 8.6
    assert payload["average_hr"] == 134
    assert payload["max_hr"] == 171
    assert payload["average_cadence"] == 88.4
    assert payload["decoupling_pct"] == 3.24
    assert payload["average_power"] == 200
    assert payload["normalized_power"] == 257
    assert payload["intensity_factor"] == 0.83
    assert payload["elevation_gain_meters"] == 525.0


def test_activity_payload_carries_the_athletes_verdict_and_zone_times() -> None:
    """The calendar shows feel/RPE, the source and the time in zones."""
    payload = _activity_payload(
        IntervalsActivity(
            id="8",
            type="Run",
            source="GARMIN_CONNECT",
            feel=2,
            icu_rpe=6,
            icu_hr_zone_times=[600, 1800, 300],
            pace_zone_times=[400, 2000, 300],
        )
    )
    assert payload["feel"] == 2
    assert payload["rpe"] == 6
    assert payload["source"] == "GARMIN_CONNECT"
    assert payload["hr_zone_times"] == [600, 1800, 300]
    assert payload["pace_zone_times"] == [400, 2000, 300]


def test_activity_payload_quotes_the_same_pace_as_the_debrief() -> None:
    """Moving pace wins over raw average speed, as in the debrief's
    ``_primary_speed``: a pool swim rests at the wall, and the two windows
    must not show two different paces for one activity."""
    swim = IntervalsActivity(id="8", type="Swim", pace=0.93, average_speed=0.80)
    assert _activity_payload(swim)["average_speed_mps"] == 0.93
    no_pace = IntervalsActivity(id="9", type="Run", average_speed=3.2)
    assert _activity_payload(no_pace)["average_speed_mps"] == 3.2


def test_activity_payload_keeps_a_zero_intensity() -> None:
    """Same zero case as ``activity_tools._intensity_factor``."""
    payload = _activity_payload(IntervalsActivity(id="10", icu_intensity=0.0))
    assert payload["intensity_factor"] == 0.0


def test_fetch_intervals_calendar_pairs_event_and_activity() -> None:
    """A pushed event paired with a completed activity yields an overlay."""
    session = _session(session_id=1, slot_id="s1")
    client = _FakeIntervalsClient(
        events=[_event("coachie:7:s1", 555)],
        activities=[_activity(999, paired_event_id=555, compliance=87.5)],
    )

    activities, overlays, _ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[session],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
    )

    assert [a["id"] for a in activities] == ["999"]
    assert overlays == {
        1: {
            "matched_activity_id": "999",
            "compliance_pct": 87.5,
            "intervals_activity_url": "https://intervals.icu/activities/999",
        }
    }


def test_fetch_intervals_calendar_skips_unpaired_and_unpushed_sessions() -> None:
    """Sessions with no pushed event, or an event nobody completed, are absent."""
    unpushed = _session(session_id=1, slot_id="s1")
    pushed_incomplete = _session(session_id=2, slot_id="s2")
    client = _FakeIntervalsClient(
        events=[_event("coachie:7:s2", 111), _event(None, 222), _event("x", None)],
        activities=[
            _activity(1, paired_event_id=None),
            _activity(2, paired_event_id=999),
        ],
    )

    _activities, overlays, _ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[unpushed, pushed_incomplete],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
    )

    assert overlays == {}


def _fetch(client: _FakeIntervalsClient, *sessions: SimpleNamespace) -> dict:
    _activities, overlays, _ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=list(sessions),
        window=(date(2026, 2, 1), date(2026, 2, 28)),
    )
    return overlays


def test_the_one_activity_of_a_day_is_paired_with_its_one_open_session() -> None:
    """intervals.icu missed it, so the calendar pairs it there and shows it done."""
    client = _PairingIntervalsClient(
        events=[_workout("coachie:7:s1", 555)],
        activities=[
            _activity(999, paired_event_id=None),
            _activity(998, paired_event_id=None, type_="Ride"),
            _activity(997, paired_event_id=None, day="2026-02-11"),
        ],
    )

    overlays = _fetch(client, _session(1, slot_id="s1"))

    assert client.pairs == [("999", 555)]
    assert overlays[1]["matched_activity_id"] == "999"
    assert overlays[1]["compliance_pct"] == 120.0


def test_a_run_named_after_its_own_days_workout_pairs_whatever_its_length() -> None:
    """Planned sessions run two or three times long still pair by their title."""
    client = _PairingIntervalsClient(
        events=[
            _workout("coachie:7:s1", 555),
            _workout("coachie:7:s2", 556, day="2026-02-15", name="Long Trail Run"),
        ],
        activities=[_activity(999, None, name="Riverside - Easy Run")],
    )

    _fetch(client, _session(1, slot_id="s1"), _session(2, slot_id="s2"))

    assert client.pairs == [("999", 555)]


@pytest.mark.parametrize(
    ("events", "activities"),
    [
        pytest.param(
            [_workout("coachie:7:s1", 555)],
            [_activity(998, None), _activity(999, None, type_="TrailRun")],
            id="two-runs-that-day",
        ),
        pytest.param(
            [_workout("coachie:7:s1", 555), _workout("athletes-own", 556)],
            [_activity(999, None)],
            id="two-open-run-workouts-that-day",
        ),
        pytest.param(
            [_workout("coachie:7:s1", 555)],
            [_activity(999, None, type_="Hike")],
            id="a-hike-is-not-a-run",
        ),
        pytest.param(
            [_workout("coachie:7:s1", 555, type_="Ride")],
            [_activity(999, None)],
            id="another-sport",
        ),
        pytest.param(
            [_workout("coachie:7:s1", 555, day="2026-02-09")],
            [_activity(999, None)],
            id="another-day",
        ),
        pytest.param(
            [
                _workout("coachie:7:s1", 555),
                _workout("coachie:7:s2", 556, day="2026-02-15", name="Long Trail Run"),
            ],
            [_activity(999, None, name="Hillside - Long Trail Run")],
            id="the-watch-ran-another-days-workout",
        ),
    ],
)
def test_an_ambiguous_day_is_left_to_the_athlete(
    events: list[SimpleNamespace], activities: list[IntervalsActivity]
) -> None:
    client = _PairingIntervalsClient(events=events, activities=activities)

    overlays = _fetch(client, _session(1, slot_id="s1"))

    assert client.pairs == []
    assert overlays == {}


@pytest.mark.parametrize(
    "session",
    [
        pytest.param(
            _session(1, slot_id="s1", matched_activity_id="i999"),
            id="paired-once-then-unpaired",
        ),
        pytest.param(_session(1, slot_id="s1", status="unlinked"), id="unlinked"),
        pytest.param(_session(1, slot_id="s1", status="skipped"), id="skipped"),
    ],
)
def test_a_pairing_someone_undid_is_never_redone(session: SimpleNamespace) -> None:
    client = _PairingIntervalsClient(
        events=[_workout("coachie:7:s1", 555)],
        activities=[_activity(999, paired_event_id=None)],
    )

    _fetch(client, session)

    assert client.pairs == []


@pytest.mark.parametrize(
    "client",
    [
        pytest.param(
            _PairingIntervalsClient(
                [_workout("coachie:7:s1", 555)], [_activity(999, None)], fails=True
            ),
            id="write-fails",
        ),
        pytest.param(
            _PairingIntervalsClient(
                [_workout("coachie:7:s1", 555)], [_activity(999, None)], sticks=False
            ),
            id="write-not-recorded",
        ),
    ],
)
def test_a_pairing_that_did_not_land_leaves_the_session_open(
    client: _PairingIntervalsClient,
) -> None:
    assert _fetch(client, _session(1, slot_id="s1")) == {}


def _adhoc_event(
    event_id: int | None,
    external_id: str = "coachie:adhoc:2026-02-12:ab12cd34",
    type_: str = "Run",
    workout_doc: dict[str, object] | None = None,
    day: str = "2026-02-12",
) -> SimpleNamespace:
    return SimpleNamespace(
        external_id=external_id,
        id=event_id,
        start_date_local=f"{day}T00:00:00",
        type=type_,
        name="Easy Run",
        description="Keep it conversational.\n\n- 45m 65-78% LTHR",
        moving_time=2700,
        icu_training_load=38.0,
        workout_doc=workout_doc,
    )


def test_fetch_intervals_calendar_surfaces_ad_hoc_events() -> None:
    """A coach-scheduled ad-hoc event reaches the calendar wire shape."""
    client = _FakeIntervalsClient(
        events=[_event("coachie:7:s1", 555), _adhoc_event(556)],
    )

    _activities, _overlays, ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
    )

    assert ad_hoc == [
        {
            "id": 556,
            "external_id": "coachie:adhoc:2026-02-12:ab12cd34",
            "plan_id": None,
            "date": "2026-02-12",
            "sport": "running",
            "name": "Easy Run",
            "note": "Keep it conversational.",
            "duration": 2700,
            "tss": 38.0,
            "workout": None,
            "status": "adhoc",
        }
    ]


def test_fetch_intervals_calendar_pairs_a_completed_ad_hoc_and_drops_an_idless_one() -> (
    None
):
    """A paired ad-hoc event keeps its planned side; one with no id never surfaces."""
    client = _FakeIntervalsClient(
        events=[_adhoc_event(556), _adhoc_event(None)],
        activities=[_activity(999, paired_event_id=556, compliance=87.5)],
    )

    _activities, _overlays, ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
    )

    assert [(row["id"], row["status"]) for row in ad_hoc] == [(556, "completed")]
    assert ad_hoc[0]["matched_activity_id"] == "999"
    assert ad_hoc[0]["compliance_pct"] == 87.5
    assert ad_hoc[0]["intervals_activity_url"] == "https://intervals.icu/activities/999"


def test_fetch_intervals_calendar_surfaces_a_former_plans_finished_sessions() -> None:
    """A replaced or deleted plan's sessions come back from their events.

    Their events before today stay on intervals.icu (the athlete's record of
    what was planned), so the calendar reads them from there: paired ones
    done, unpaired past ones missed. The active plan's own events never
    surface this way — they overlay its session rows.
    """
    client = _FakeIntervalsClient(
        events=[
            _event("coachie:7:s1", 555),
            _adhoc_event(601, external_id="coachie:6:s1", day="2026-02-10"),
            _adhoc_event(602, external_id="coachie:6:s2", day="2026-02-12"),
            _adhoc_event(603, external_id="coachie:6:s3", day="2026-02-25"),
        ],
        activities=[_activity(999, paired_event_id=601, compliance=87.5)],
    )

    with patch(
        "endury.planning.calendar_projection.clock.today",
        return_value=date(2026, 2, 20),
    ):
        _activities, overlays, rows = fetch_intervals_calendar(
            client,
            plan_id=7,
            sessions=[],
            window=(date(2026, 2, 1), date(2026, 2, 28)),
        )

    assert overlays == {}
    assert [(row["external_id"], row["plan_id"], row["status"]) for row in rows] == [
        ("coachie:6:s1", 6, "completed"),
        ("coachie:6:s2", 6, "missed"),
        ("coachie:6:s3", 6, "pending"),
    ]
    assert rows[0]["matched_activity_id"] == "999"
    assert rows[0]["compliance_pct"] == 87.5
    assert "matched_activity_id" not in rows[1]
    assert rows[1]["name"] == "Easy Run"
    assert rows[1]["duration"] == 2700


def test_ad_hoc_payload_degrades_without_type_name_or_description() -> None:
    """Missing readback fields fall back rather than break the row."""
    payload = _ad_hoc_payload(
        SimpleNamespace(
            external_id="coachie:adhoc:2026-02-12:ab12cd34",
            id=556,
            start_date_local=None,
            type="Yoga",
            name=None,
            description=None,
            moving_time=None,
            icu_training_load=None,
            workout_doc=None,
        )
    )

    assert payload == {
        "id": 556,
        "external_id": "coachie:adhoc:2026-02-12:ab12cd34",
        "date": "",
        "sport": "Yoga",
        "name": "Workout",
        "note": None,
        "duration": None,
        "tss": None,
        "workout": None,
    }


def test_ad_hoc_payload_without_workout_doc_has_no_workout() -> None:
    """An ad-hoc event with no workout_doc (e.g. old readback) gets workout: None."""
    payload = _ad_hoc_payload(_adhoc_event(556))
    assert payload["workout"] is None


def test_ad_hoc_payload_projects_distance_steps_with_pace_percent() -> None:
    """Swim steps carry distance + a pace percent band -> distance duration."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Swim warm-up",
                    "distance": 400,
                    "duration": 571,
                    "pace": {"start": 73, "end": 77, "units": "%pace"},
                },
                {
                    "text": "freestyle",
                    "distance": 600,
                    "duration": 778,
                    "pace": {"start": 80, "end": 85, "units": "%pace"},
                },
                {
                    "text": "Swim cool-down",
                    "distance": 300,
                    "duration": 446,
                    "pace": {"start": 70, "end": 74, "units": "%pace"},
                },
            ]
        },
    )

    payload = _ad_hoc_payload(event)

    assert payload["workout"] == {
        "name": "Easy Run",
        "steps": [
            {
                "type": "warmup",
                "description": "Swim warm-up",
                "duration_seconds": 571.0,
                "duration_meters": 400.0,
                "duration": {
                    "duration_type": "distance",
                    "value_meters": 400.0,
                    "value_seconds": 571.0,
                },
                "target": {
                    "target_type": "threshold_percent",
                    "metric": "pace",
                    "low_percent": 73,
                    "high_percent": 77,
                },
                "secondary_target": None,
            },
            {
                "type": "interval",
                "description": "freestyle",
                "duration_seconds": 778.0,
                "duration_meters": 600.0,
                "duration": {
                    "duration_type": "distance",
                    "value_meters": 600.0,
                    "value_seconds": 778.0,
                },
                "target": {
                    "target_type": "threshold_percent",
                    "metric": "pace",
                    "low_percent": 80,
                    "high_percent": 85,
                },
                "secondary_target": None,
            },
            {
                "type": "cooldown",
                "description": "Swim cool-down",
                "duration_seconds": 446.0,
                "duration_meters": 300.0,
                "duration": {
                    "duration_type": "distance",
                    "value_meters": 300.0,
                    "value_seconds": 446.0,
                },
                "target": {
                    "target_type": "threshold_percent",
                    "metric": "pace",
                    "low_percent": 70,
                    "high_percent": 74,
                },
                "secondary_target": None,
            },
        ],
    }


def test_ad_hoc_payload_projects_time_steps_with_power_percent() -> None:
    """Bike steps carry no distance, a power percent band -> time duration."""
    event = _adhoc_event(
        556,
        type_="Ride",
        workout_doc={
            "steps": [
                {
                    "text": "Easy warm-up",
                    "duration": 480,
                    "power": {"start": 53, "end": 57, "units": "%ftp"},
                },
                {
                    "text": "Easy aerobic effort",
                    "duration": 1920,
                    "power": {"start": 63, "end": 67, "units": "%ftp"},
                },
                {
                    "text": "Easy cool-down",
                    "duration": 300,
                    "power": {"start": 44, "end": 46, "units": "%ftp"},
                },
            ]
        },
    )

    payload = _ad_hoc_payload(event)

    steps = payload["workout"]["steps"]
    assert [s["type"] for s in steps] == ["warmup", "interval", "cooldown"]
    assert steps[0]["duration"] == {"duration_type": "time", "value_seconds": 480.0}
    assert steps[0]["duration_meters"] == 0.0
    assert steps[1]["target"] == {
        "target_type": "threshold_percent",
        "metric": "power",
        "low_percent": 63,
        "high_percent": 67,
    }


def test_ad_hoc_payload_projects_hr_percent_and_recovery_typing() -> None:
    """An hr-targeted step types as recovery from its text, not a flag."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Recovery jog",
                    "duration": 120,
                    "hr": {"start": 50, "end": 60, "units": "%lthr"},
                },
            ]
        },
    )

    payload = _ad_hoc_payload(event)

    steps = payload["workout"]["steps"]
    assert steps[0]["type"] == "recovery"
    assert steps[0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "hr",
        "low_percent": 50,
        "high_percent": 60,
    }


def test_ad_hoc_payload_keeps_a_repeat_block_and_its_steps() -> None:
    """A repeat shows its reps and their targets, not one blank 1:17 row.

    The shape intervals.icu parsed from a coach-booked sweet spot ride.
    """
    event = _adhoc_event(
        556,
        type_="Ride",
        workout_doc={
            "steps": [
                {
                    "text": "3x",
                    "reps": 3,
                    "duration": 4590,
                    "distance": 0,
                    "steps": [
                        {
                            "text": "Sweet spot",
                            "duration": 1065,
                            "power": {"start": 89, "end": 95, "units": "%ftp"},
                        },
                        {
                            "text": "Active recovery",
                            "duration": 465,
                            "power": {"start": 58, "end": 62, "units": "%ftp"},
                        },
                    ],
                },
            ]
        },
    )

    payload = _ad_hoc_payload(event, LoadMetricContext(cycling_ftp_watts=310.0))

    (repeat,) = payload["workout"]["steps"]
    assert repeat["type"] == "repeat"
    assert repeat["iterations"] == 3
    work, rest = repeat["steps"]
    assert (work["type"], work["duration_seconds"]) == ("interval", 1065.0)
    assert work["target"] == {
        "target_type": "power",
        "low_watts": 276,
        "high_watts": 294,
    }
    assert (rest["type"], rest["duration_seconds"]) == ("recovery", 465.0)


def test_ad_hoc_payload_skips_malformed_steps() -> None:
    """A step with no duration (unrecognized shape) is skipped, never raises."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {"text": "Easy warm-up", "duration": 480},
                {"text": "no duration here"},
                "not even a dict",
                {"text": "Easy cool-down", "duration": 300},
            ]
        },
    )

    payload = _ad_hoc_payload(event)

    assert [s["description"] for s in payload["workout"]["steps"]] == [
        "Easy warm-up",
        "Easy cool-down",
    ]


def test_ad_hoc_payload_empty_steps_list_has_no_workout() -> None:
    """A workout_doc with an empty steps list still yields workout: None."""
    event = _adhoc_event(556, workout_doc={"steps": []})
    assert _ad_hoc_payload(event)["workout"] is None


def test_ad_hoc_payload_no_target_when_no_metric_present() -> None:
    """A step with a duration but no pace/power/hr band gets target: None."""
    event = _adhoc_event(
        556,
        workout_doc={"steps": [{"text": "Steady spin", "duration": 600}]},
    )

    payload = _ad_hoc_payload(event)

    assert payload["workout"]["steps"][0]["target"] is None


def test_ad_hoc_payload_no_target_when_band_missing_start_or_end() -> None:
    """A pace/power/hr band present but missing start/end also yields no target."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {"text": "Steady spin", "duration": 600, "pace": {"units": "%pace"}}
            ]
        },
    )

    payload = _ad_hoc_payload(event)

    assert payload["workout"]["steps"][0]["target"] is None


def test_ad_hoc_payload_converts_pace_percent_to_absolute_pace() -> None:
    """A running %pace band converts to real min/km bounds via the threshold."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": 63, "end": 67, "units": "%pace"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "pace",
        "low_min_per_km": 6.72,
        "high_min_per_km": 7.14,
    }


def test_ad_hoc_payload_converts_pace_percent_to_absolute_swim_pace() -> None:
    """A swim %pace band converts to sec/100m bounds via css_sec_per_100m."""
    event = _adhoc_event(
        556,
        type_="Swim",
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "distance": 400,
                    "duration": 600,
                    "pace": {"start": 85, "end": 91, "units": "%pace"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(swim_css_sec_per_100m=107.0)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "swim_pace",
        "low_sec_per_100m": 117.6,
        "high_sec_per_100m": 125.9,
    }


def test_ad_hoc_payload_converts_hr_percent_to_absolute_bpm() -> None:
    """A %LTHR band converts to bpm bounds via the sport's lthr_bpm."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Recovery jog",
                    "duration": 120,
                    "hr": {"start": 50, "end": 60, "units": "%lthr"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_lthr_bpm=168)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "heart_rate",
        "low_bpm": 84,
        "high_bpm": 101,
    }


def test_ad_hoc_payload_converts_power_percent_to_absolute_watts() -> None:
    """A %FTP band converts to watts bounds via the sport's ftp_watts.

    157.5 and 167.5 land exactly on Python's round-half-to-even boundary
    (158 and 168 are the even neighbours), so this also pins that banker's
    rounding is what the app actually ships.
    """
    event = _adhoc_event(
        556,
        type_="Ride",
        workout_doc={
            "steps": [
                {
                    "text": "Easy aerobic effort",
                    "duration": 1920,
                    "power": {"start": 63, "end": 67, "units": "%ftp"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(cycling_ftp_watts=250.0)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "power",
        "low_watts": 158,
        "high_watts": 168,
    }


def test_ad_hoc_payload_falls_back_to_percent_when_threshold_missing() -> None:
    """No threshold on file for that metric keeps the raw percent band."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": 63, "end": 67, "units": "%pace"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_lthr_bpm=168)  # no running pace threshold

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "pace",
        "low_percent": 63,
        "high_percent": 67,
    }


def test_ad_hoc_payload_falls_back_to_percent_when_units_not_percent() -> None:
    """A band whose units isn't a '%...' string is left in the percent shape."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": 63, "end": 67, "units": "min/km"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "pace",
        "low_percent": 63,
        "high_percent": 67,
    }


def test_ad_hoc_payload_falls_back_to_percent_when_a_bound_is_not_a_number() -> None:
    """A hand-edited band whose bound is text degrades instead of raising."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": "63", "end": "67", "units": "%pace"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "pace",
        "low_percent": "63",
        "high_percent": "67",
    }


def test_ad_hoc_payload_falls_back_to_percent_when_a_bound_is_zero() -> None:
    """A zero percent bound degrades instead of dividing by zero."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": 0, "end": 67, "units": "%pace"},
                },
            ]
        },
    )
    resolver = LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    payload = _ad_hoc_payload(event, resolver)

    assert payload["workout"]["steps"][0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "pace",
        "low_percent": 0,
        "high_percent": 67,
    }


def test_fetch_intervals_calendar_skips_thresholds_source_without_ad_hoc_events() -> (
    None
):
    """No ad-hoc events in the window means no threshold fetch at all."""
    calls: list[None] = []

    def _thresholds_source() -> LoadMetricContext | None:
        calls.append(None)
        return LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    session = _session(session_id=1, slot_id="s1")
    client = _FakeIntervalsClient(events=[_event("coachie:7:s1", 555)])

    fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[session],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
        thresholds_source=_thresholds_source,
    )

    assert calls == []


def test_fetch_intervals_calendar_calls_thresholds_source_once_for_ad_hoc_events() -> (
    None
):
    """Two ad-hoc events in the window trigger exactly one threshold fetch."""
    calls: list[None] = []

    def _thresholds_source() -> LoadMetricContext | None:
        calls.append(None)
        return LoadMetricContext(running_threshold_pace_min_per_km=4.5)

    pace_step = {
        "steps": [
            {
                "text": "Steady",
                "duration": 600,
                "pace": {"start": 63, "end": 67, "units": "%pace"},
            },
        ]
    }
    first = _adhoc_event(
        556,
        external_id="coachie:adhoc:2026-02-12:ab12cd34",
        workout_doc=pace_step,
    )
    second = _adhoc_event(
        557,
        external_id="coachie:adhoc:2026-02-13:cd34ef56",
        workout_doc=pace_step,
    )
    client = _FakeIntervalsClient(events=[first, second])

    _activities, _overlays, ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
        thresholds_source=_thresholds_source,
    )

    assert len(calls) == 1
    assert [row["workout"]["steps"][0]["target"]["target_type"] for row in ad_hoc] == [
        "pace",
        "pace",
    ]


def test_fetch_intervals_calendar_thresholds_source_returning_none_falls_back() -> None:
    """A thresholds_source that returns None degrades to the percent fallback."""
    event = _adhoc_event(
        556,
        workout_doc={
            "steps": [
                {
                    "text": "Steady",
                    "duration": 600,
                    "pace": {"start": 63, "end": 67, "units": "%pace"},
                },
            ]
        },
    )
    client = _FakeIntervalsClient(events=[event])

    _activities, _overlays, ad_hoc = fetch_intervals_calendar(
        client,
        plan_id=7,
        sessions=[],
        window=(date(2026, 2, 1), date(2026, 2, 28)),
        thresholds_source=lambda: None,
    )

    assert ad_hoc[0]["workout"]["steps"][0]["target"] == {
        "target_type": "threshold_percent",
        "metric": "pace",
        "low_percent": 63,
        "high_percent": 67,
    }
