"""Rotating sets: a recurring session type cycles through its set schemes."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, timedelta

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.sets import SET_SCHEMES, set_occurrences, with_set
from endury.planner.activity.shapes.land import IntervalPiecewiseActivity
from endury.planner.foundations.types import ActivityAssignment, ActivityKey

ensure_registry_loaded()

#: A set keeps the session within this many minutes of its planned length.
_FILL_MINUTES = 8.0
#: ... and within this share of the first set's load per minute.
_LOAD_PER_MINUTE = 0.15
#: A set's shortest card is at most this much longer than the first set's,
#: so a session booked under the card's range still renders honestly: the
#: card-honesty grid's own margin over a floor booking (test_card_honesty).
_SHORTEST_MINUTES = 0.5

_SETS = [
    (key, number)
    for key, schemes in SET_SCHEMES.items()
    for number in range(1, len(schemes) + 1)
]


@pytest.mark.parametrize("key", list(SET_SCHEMES))
def test_every_override_is_a_field_of_its_card(key: ActivityKey) -> None:
    for scheme in SET_SCHEMES[key]:
        card = scheme.get("shape", type(get_activity(key)))
        overrides = set(scheme) - {"shape"}
        names = {f.name for f in fields(card)}
        assert overrides <= names, sorted(overrides - names)


def test_a_shaped_set_is_the_same_session_type_in_intervals() -> None:
    tempo = get_activity(ActivityKey.BIKE_TEMPO)
    for number in (1, 2):
        blocks = with_set(tempo, number)
        assert isinstance(blocks, IntervalPiecewiseActivity)
        assert (blocks.key, blocks.family, blocks.hardness) == (
            tempo.key,
            tempo.family,
            tempo.hardness,
        )


def test_the_first_occurrence_and_a_card_without_sets_keep_the_card() -> None:
    threshold = get_activity(ActivityKey.BIKE_THRESHOLD)
    easy = get_activity(ActivityKey.RUN_EASY)
    assert with_set(threshold, 0) is threshold
    assert with_set(easy, 5) is easy


def test_occurrences_cycle_through_every_set_and_back() -> None:
    threshold = get_activity(ActivityKey.RUN_THRESHOLD)
    schemes = SET_SCHEMES[ActivityKey.RUN_THRESHOLD]
    for number, scheme in enumerate(schemes, 1):
        varied = with_set(threshold, number)
        assert {name: getattr(varied, name) for name in scheme} == dict(scheme)
    assert with_set(threshold, len(schemes) + 1) is threshold


def test_occurrences_count_each_type_in_date_order() -> None:
    monday = date(2026, 1, 5)

    def at(day: int, slot: str, key: ActivityKey) -> ActivityAssignment:
        return ActivityAssignment(slot, monday + timedelta(days=day), key, 1.0)

    plan = [
        at(8, "c", ActivityKey.SWIM_CSS),
        at(1, "a", ActivityKey.SWIM_CSS),
        at(1, "b", ActivityKey.BIKE_THRESHOLD),
        at(15, "d", ActivityKey.SWIM_CSS),
    ]
    assert set_occurrences(plan) == {"a": 0, "b": 0, "c": 1, "d": 2}


@pytest.mark.parametrize(("key", "number"), _SETS)
def test_a_set_keeps_the_sessions_length_and_close_to_its_load(
    key: ActivityKey, number: int
) -> None:
    """Over the card's whole range, so a rotation never resizes a session."""
    first = get_activity(key)
    varied = with_set(first, number)
    low = first.duration_minutes_exact(first.tss_min)
    high = first.duration_minutes_exact(first.tss_max)
    minutes = low
    while minutes <= high:
        tss_first = assignment_target_tss(first, minutes / 60.0, None)
        tss_varied = assignment_target_tss(varied, minutes / 60.0, None)
        length = varied.duration_minutes_exact(tss_varied)
        assert abs(length - minutes) <= _FILL_MINUTES, (minutes, length)
        per_minute_first = first.achieved_tss(tss_first) / (
            first.duration_minutes_exact(tss_first)
        )
        per_minute_varied = varied.achieved_tss(tss_varied) / length
        drift = per_minute_varied / per_minute_first - 1.0
        assert abs(drift) <= _LOAD_PER_MINUTE, (minutes, round(drift, 3))
        minutes += 2.5


def _shortest_card(activity) -> float:
    return min(
        activity.duration_minutes_exact(
            assignment_target_tss(activity, minutes / 60.0, None)
        )
        for minutes in range(10, 120)
    )


@pytest.mark.parametrize(("key", "number"), _SETS)
def test_a_set_reaches_down_to_the_first_sets_shortest_card(
    key: ActivityKey, number: int
) -> None:
    """A low-volume booking below the card's range must not grow with the set."""
    first = get_activity(key)
    shortest = _shortest_card(with_set(first, number))
    assert shortest <= _shortest_card(first) + _SHORTEST_MINUTES, shortest
