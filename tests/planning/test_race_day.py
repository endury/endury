"""Tests for race_day: which activity was a race, and closing a run race."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from endury.domain.models.race import AthleteRace
from endury.intervals.models import IntervalsActivity
from endury.planning.race_day import (
    complete_raced_races,
    race_activity_ids,
    race_of_activity,
)

_DAY = "2026-10-24"


def _race(**overrides) -> AthleteRace:
    fields = {
        "id": 2,
        "name": "Ridge Marathon",
        "race_date": _DAY,
        "goal_type": "trail_50k",
        "priority": "A",
    }
    fields.update(overrides)
    return AthleteRace(**fields)


def _activity(id_: str, type_: str, minutes: int, day: str = _DAY) -> IntervalsActivity:
    return IntervalsActivity(
        id=id_, type=type_, start_date_local=f"{day}T10:00:00", moving_time=minutes * 60
    )


def test_a_run_race_is_the_days_longest_run():
    day = [
        _activity("warmup", "Run", 15),
        _activity("race", "TrailRun", 228),
        _activity("spin", "Ride", 60),
        _activity("yesterday", "TrailRun", 300, day="2026-10-23"),
    ]

    assert race_activity_ids(_race(), day) == {"race"}
    assert race_activity_ids(_race(), [_activity("spin", "Ride", 60)]) == set()


def test_every_leg_of_a_multisport_race_is_the_race():
    day = [_activity("swim", "Swim", 30), _activity("bike", "Ride", 150)]

    assert race_activity_ids(_race(goal_type="half_ironman"), day) == {"swim", "bike"}


def test_race_of_activity_names_the_race_only_for_its_activity():
    races = MagicMock()
    races.get_by_date.return_value = _race()
    client = MagicMock()
    client.list_activities.return_value = [
        _activity("warmup", "Run", 15),
        _activity("race", "TrailRun", 228),
    ]

    assert race_of_activity(races, client, "race", _DAY) == _race()
    assert race_of_activity(races, client, "warmup", _DAY) is None
    races.get_by_date.return_value = _race(status="dns")
    assert race_of_activity(races, client, "race", _DAY) is None
    races.get_by_date.return_value = None
    assert race_of_activity(races, client, "race", _DAY) is None


def test_race_of_activity_reads_a_failed_day_as_training():
    races = MagicMock()
    races.get_by_date.return_value = _race()
    client = MagicMock()
    client.list_activities.side_effect = RuntimeError("down")

    assert race_of_activity(races, client, "race", _DAY) is None


def test_a_planned_race_with_its_activity_is_completed():
    run = _race()
    ahead = _race(id=3, race_date="2026-11-01")
    missed = _race(id=4, race_date="2026-10-18")
    races = MagicMock()
    races.list.return_value = [run, ahead, missed]
    races.upsert.side_effect = lambda **fields: fields
    client = MagicMock()
    client.list_activities.return_value = [_activity("race", "TrailRun", 228)]

    done = complete_raced_races(races, client=client, today=date(2026, 10, 24))

    assert [row["race_id"] for row in done] == [2]
    assert done[0]["status"] == "completed"
    assert done[0]["climb_m"] is None
    client.list_activities.assert_called_once_with(
        date(2026, 10, 18), date(2026, 10, 24)
    )


def test_completing_races_never_raises_and_skips_without_work():
    races = MagicMock()
    races.list.return_value = [_race()]
    client = MagicMock()
    client.list_activities.side_effect = RuntimeError("down")

    assert complete_raced_races(races, client=client, today=date(2026, 10, 24)) == []
    assert complete_raced_races(races, client=None, today=date(2026, 10, 24)) == []
    races.upsert.assert_not_called()
