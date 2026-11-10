"""Tests for reps recounted from the power stream (endury.intervals.reps)."""

from __future__ import annotations

from endury.intervals.fake import FakeIntervals
from endury.intervals.models import ActivityStream, IntervalRow, IntervalsActivityDetail
from endury.intervals.reps import fill_dropouts, planned_reps
from endury.planning.activity_views import activity_debrief

_FTP = 300
_DAY = "2026-10-12"

#: A planned 3 x 13 30/15 ride as intervals.icu parses it (``workout_doc``).
_SET = {
    "reps": 13,
    "steps": [
        {"duration": 30, "power": {"start": 112, "end": 118, "units": "%ftp"}},
        {"duration": 15, "power": {"start": 53, "end": 57, "units": "%ftp"}},
    ],
}
_EASY = {"duration": 180, "power": {"start": 53, "end": 57, "units": "%ftp"}}
_DOC = {"steps": [_EASY, _SET, _EASY, _SET, _EASY, _SET, _EASY]}


def _ride(dropouts: bool) -> list[ActivityStream]:
    """A 30/15 ride; with dropouts, every other rep loses 3 s to 0 W early on."""
    watts: list[float] = [160.0] * 180
    rep = 0
    for _ in range(3):
        for _ in range(13):
            lost = dropouts and rep % 2 == 1
            watts += [345.0] * 2 + [0.0] * 3 + [345.0] * 25 if lost else [345.0] * 30
            watts += [170.0] * 15
            rep += 1
        watts += [170.0] * 165
    n = len(watts)
    return [
        ActivityStream(type="time", data=[float(i) for i in range(n)]),
        ActivityStream(type="watts", data=watts),
        ActivityStream(type="cadence", data=[95.0] * n),
        ActivityStream(type="heartrate", data=[150.0] * n),
    ]


def _account(dropouts: bool, detected: int) -> FakeIntervals:
    """An account holding the paired event and the ride intervals.icu read."""
    detail = IntervalsActivityDetail(
        id="i9",
        type="VirtualRide",
        start_date_local=f"{_DAY}T18:00:00",
        icu_ftp=_FTP,
        paired_event_id=7,
        icu_power_zones=[55, 75, 90, 105, 120, 150, 999],
        icu_intervals=[IntervalRow(type="WORK", elapsed_time=30)] * detected,
        icu_groups=[IntervalRow(count=detected, elapsed_time=30, average_watts=345)],
        interval_summary=[f"{detected}x 30s 345w"],
    )
    fake = FakeIntervals(details={"i9": detail}, streams={"i9": _ride(dropouts)})
    fake.events["coachie:adhoc:x"] = {
        "id": 7,
        "category": "WORKOUT",
        "start_date_local": f"{_DAY}T00:00:00",
        "workout_doc": _DOC,
    }
    return fake


def test_a_30_15_ride_with_power_dropouts_reads_all_its_reps() -> None:
    """Dropouts split the reps intervals.icu missed; the stream holds 39."""
    fake = _account(dropouts=True, detected=29)

    view = activity_debrief(fake.client(), "i9")

    work = [row for row in view["intervals"] if row["type"] == "WORK"]
    assert view["groups"][0]["count"] == 39
    assert view["interval_summary"] == ["39x 30s 345w"]
    assert {row["duration"] for row in work} == {"0:30"}
    assert {row["avg_watts"] for row in work} == {345}
    assert "19 short power dropouts" in view["note"]
    assert "intervals.icu read 29 reps" in view["note"]


def test_a_ride_intervals_icu_read_right_fetches_no_streams() -> None:
    fake = _account(dropouts=True, detected=39)

    view = activity_debrief(fake.client(), "i9")

    assert view["interval_summary"] == ["39x 30s 345w"]
    assert not any(path.endswith("streams.json") for _, path in fake.calls)


def test_without_dropouts_intervals_icu_keeps_its_reading() -> None:
    """Two detectors disagreeing is no reason to replace intervals.icu's."""
    fake = _account(dropouts=False, detected=29)

    view = activity_debrief(fake.client(), "i9")

    assert view["interval_summary"] == ["29x 30s 345w"]
    assert "auto-detected by intervals.icu" in view["note"]


def test_a_dropout_is_short_and_taken_while_pedalling() -> None:
    watts = [200.0, 0.0, 0.0, 0.0, 300.0, 0.0, 0.0, 200.0, 0.0]
    cadence = [90.0, 90.0, 90.0, 90.0, 90.0, 0.0, 0.0, 90.0, 90.0]

    filled, count = fill_dropouts(watts, cadence)

    # The pedalled gap is filled; the coasting gap and the last sample are not.
    assert count == 1
    assert filled[1:4] == [225.0, 250.0, 275.0]
    assert filled[5:7] == [0.0, 0.0]
    assert filled[8] == 0.0


def test_planned_reps_sit_between_the_rep_and_recovery_targets() -> None:
    plan = planned_reps(_DOC, _FTP)

    assert plan is not None
    assert (plan.count, plan.seconds) == (39, 30.0)
    assert plan.threshold_watts == (336.0 + 171.0) / 2
    assert planned_reps({"steps": [_EASY]}, _FTP) is None
