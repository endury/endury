"""Rotating sets: the same session type with a different set each time it recurs.

An interval card turns a session's length into one structure, so a threshold
ride of the same length is always the same set. ``SET_SCHEMES`` gives a card
alternative rep envelopes, and :func:`with_set` returns the card for the
n-th time its session type appears in a plan: today's set first, then each
alternative in turn. An alternative keeps the card's intensities and changes
only reps, rep length and recovery, with recovery scaled to the reps so a
session keeps its length and close to its load. A continuous card has no
reps to change, so its alternatives name an interval ``shape`` and are built
as that card under the same session type: the tempo ride's and the tempo
runs' single block alternates with blocks at the same intensity. A
progression run varies its number of steps.

Only the cards a plan materializes read this (``endury.planning.materialize_plan``
and the benchmark's export). The planner sizes every session on the card's
first set, so plan hours never depend on it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import fields, replace
from typing import Any

from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.activity.shapes.land import IntervalPiecewiseActivity
from endury.planner.foundations.types import ActivityAssignment, ActivityKey
from endury.training.load import RUNNING_THRESHOLD_TSS_PER_HOUR

#: The tempo ride's intensities, with a shorter warm-up and cool-down.
_BIKE_TEMPO = {
    "warmup_min": 10.0,
    "cooldown_min": 5.0,
    "if_warmup": 0.55,
    "if_cooldown": 0.45,
    "if_work": 0.87,
    "if_rest": 0.55,
    "work_rpe": 6,
    "rest_rpe": 2,
}

#: The tempo runs' warm-up and cool-down, with a short jog between reps. A
#: shorter warm-up put too much of a short run at tempo pace (load per
#: minute up to 35 % over the continuous run).
_RUN_TEMPO = {
    "warmup_min": 10.0,
    "cooldown_min": 10.0,
    "if_warmup": 0.65,
    "if_cooldown": 0.55,
    "if_rest": 0.70,
    "rest_rpe": 3,
    "threshold_tss_per_hour": RUNNING_THRESHOLD_TSS_PER_HOUR,
}


def _blocks(intensities: Mapping[str, Any], **reps: float) -> dict[str, Any]:
    """A continuous card's alternative: its main block as reps."""
    return {"shape": IntervalPiecewiseActivity, **intensities, **reps}


#: Alternative sets per session type, as field overrides of its card, or as
#: a ``shape`` and its fields for a card built anew. The card's own fields
#: are the first set.
SET_SCHEMES: Mapping[ActivityKey, tuple[Mapping[str, Any], ...]] = {
    # One continuous block first, then 2-3 x 10-20 min, then 3-5 x 6-10 min.
    # A short booking keeps a single block (hence the 1-rep, 6-min floors and
    # the shorter warm-up and cool-down).
    ActivityKey.BIKE_TEMPO: (
        _blocks(
            _BIKE_TEMPO,
            rep_count_min=1,
            rep_count_max=3,
            rep_work_min=6.0,
            rep_work_max=20.0,
            rep_rest_min=5.0,
        ),
        _blocks(
            _BIKE_TEMPO,
            rep_count_min=1,
            rep_count_max=5,
            rep_work_min=6.0,
            rep_work_max=10.0,
            rep_rest_min=3.0,
        ),
    ),
    # One tempo block first, then 2-4 x 10-15 min, then 3-6 x 6-8 min at the
    # same pace, a short jog between. A booking at the card's floor keeps a
    # single short block (the 0.5-min floors), so the card renders no
    # longer than the continuous one there (test_card_honesty).
    ActivityKey.RUN_TEMPO: (
        _blocks(
            {**_RUN_TEMPO, "if_work": 0.94, "work_rpe": 6},
            rep_count_min=1,
            rep_count_max=5,
            rep_work_min=0.5,
            rep_work_max=15.0,
            rep_rest_min=0.5,
            rep_rest_max=2.0,
        ),
        _blocks(
            {**_RUN_TEMPO, "if_work": 0.94, "work_rpe": 6},
            rep_count_min=1,
            rep_count_max=10,
            rep_work_min=0.5,
            rep_work_max=8.0,
            rep_rest_min=0.5,
            rep_rest_max=1.5,
        ),
    ),
    # The same for the steady aerobic tempo run: 2-3 x 11-20 min, 3-6 x 7-8.
    ActivityKey.RUN_AEROBIC_TEMPO: (
        _blocks(
            {**_RUN_TEMPO, "if_work": 0.90, "work_rpe": 5},
            rep_count_min=1,
            rep_count_max=5,
            rep_work_min=0.5,
            rep_work_max=20.0,
            rep_rest_min=0.5,
            rep_rest_max=2.0,
        ),
        _blocks(
            {**_RUN_TEMPO, "if_work": 0.90, "work_rpe": 5},
            rep_count_min=1,
            rep_count_max=10,
            rep_work_min=0.5,
            rep_work_max=8.0,
            rep_rest_min=0.5,
            rep_rest_max=1.5,
        ),
    ),
    # Four steps from easy to threshold pace first, then three, then five.
    ActivityKey.RUN_PROGRESSION: (
        {"progression_steps": 3},
        {"progression_steps": 5},
    ),
    # Easy then race effort in three steps first, then two (the second half
    # at race effort), then four.
    ActivityKey.RUN_LONG_PROGRESSION: (
        {"progression_steps": 2},
        {"progression_steps": 4},
    ),
    # 2 x 16-20 min first, then 2-5 x 6-12 min, then 3-7 x 5-8 min cruise reps.
    ActivityKey.BIKE_THRESHOLD: (
        {
            "rep_count_min": 2,
            "rep_count_max": 5,
            "rep_work_min": 6.0,
            "rep_work_max": 12.0,
            "rep_rest_min": 3.0,
        },
        {
            "rep_count_min": 3,
            "rep_count_max": 7,
            "rep_work_min": 5.0,
            "rep_work_max": 8.0,
            "rep_rest_min": 2.0,
        },
    ),
    # 2 x 15-20 min first, then one or two long blocks, then 3-5 shorter ones.
    ActivityKey.BIKE_SWEET_SPOT: (
        {
            "rep_count_min": 1,
            "rep_count_max": 2,
            "rep_work_min": 20.0,
            "rep_work_max": 40.0,
            "rep_rest_min": 8.0,
            "rep_rest_max": 10.0,
        },
        {
            "rep_count_min": 3,
            "rep_count_max": 5,
            "rep_work_min": 8.0,
            "rep_work_max": 14.0,
            "rep_rest_min": 4.0,
            "rep_rest_max": 5.0,
        },
    ),
    # 3-4 x 18-20 min first, then 1-3 x 25-30 min, then 2-6 x 10-13 min.
    ActivityKey.BIKE_LONG_SWEET_SPOT: (
        {
            "rep_count_min": 1,
            "rep_count_max": 3,
            "rep_work_min": 25.0,
            "rep_work_max": 30.0,
            "rep_rest_min": 30.0,
            "rep_rest_max": 70.0,
        },
        {
            "rep_count_min": 2,
            "rep_count_max": 6,
            "rep_work_min": 10.0,
            "rep_work_max": 13.0,
            "rep_rest_min": 15.0,
            "rep_rest_max": 37.0,
        },
    ),
    # 4-6 x 3 min of fast pedalling first, then 6-12 x 1 min spin-ups.
    ActivityKey.BIKE_CADENCE: (
        {
            "rep_count_min": 6,
            "rep_count_max": 12,
            "rep_work_min": 1.0,
            "rep_work_max": 1.0,
            "rep_rest_min": 2.0,
        },
    ),
    # 4-6 x 3-5 min first, then 6-9 x 2-3 min with 2 min recovery.
    ActivityKey.BIKE_VO2MAX: (
        {
            "rep_count_min": 6,
            "rep_count_max": 9,
            "rep_work_min": 2.0,
            "rep_work_max": 3.0,
            "rep_rest_min": 2.0,
        },
    ),
    # 3-6 x 3-5 min climbs first, then 2-4 x 5-10 min.
    ActivityKey.BIKE_HILL_CLIMB: (
        {
            "rep_count_min": 2,
            "rep_count_max": 4,
            "rep_work_min": 5.0,
            "rep_work_max": 10.0,
            "rep_rest_min": 4.0,
        },
    ),
    # 9 min reps of 2 min under / 1 min over first, then 6 min reps. Longer
    # reps do not fit: the card's load ceiling stops them at three.
    ActivityKey.BIKE_OVER_UNDER: (
        {
            "rep_work_min": 6.0,
            "rep_work_max": 6.0,
            "rep_count_min": 3,
            "rep_count_max": 8,
            "rep_rest_min": 2.0,
        },
    ),
    # 4-6 x 5-6 min first, then 2-4 x 8-10 min, then 2-3 x 12-15 min.
    ActivityKey.RUN_THRESHOLD: (
        {
            "rep_count_min": 2,
            "rep_count_max": 4,
            "rep_work_min": 8.0,
            "rep_work_max": 10.0,
            "rep_rest_min": 3.0,
        },
        {
            "rep_count_min": 2,
            "rep_count_max": 3,
            "rep_work_min": 12.0,
            "rep_work_max": 15.0,
            "rep_rest_min": 3.0,
        },
    ),
    # 6-8 x 2-3 min hills first, then 4-6 x 3-4 min.
    ActivityKey.RUN_HILL_REPEATS: (
        {
            "rep_count_min": 4,
            "rep_count_max": 6,
            "rep_work_min": 3.0,
            "rep_work_max": 4.0,
            "rep_rest_min": 3.5,
        },
    ),
    # 200 m repeats first, then 100s on short rest, then 250-400s.
    ActivityKey.SWIM_CSS: (
        {
            "rep_count_min": 2,
            "rep_count_max": 20,
            "rep_meters_min": 100,
            "rep_meters_max": 100,
            "rep_rest_seconds": 15,
            "rep_rest_seconds_max": 15,
        },
        {
            "rep_count_min": 1,
            "rep_count_max": 6,
            "rep_meters_min": 250,
            "rep_meters_max": 400,
            "rep_rest_seconds": 30,
            "rep_rest_seconds_max": 30,
        },
    ),
    # 300-400 m repeats first, then 150-200s, then one to three 500-800s.
    ActivityKey.SWIM_THRESHOLD: (
        {
            "rep_count_min": 5,
            "rep_count_max": 12,
            "rep_meters_min": 150,
            "rep_meters_max": 200,
            "rep_rest_seconds": 20,
            "rep_rest_seconds_max": 20,
        },
        {
            "rep_count_min": 1,
            "rep_count_max": 3,
            "rep_meters_min": 500,
            "rep_meters_max": 800,
            "rep_rest_seconds": 45,
            "rep_rest_seconds_max": 60,
        },
    ),
}


def with_set(activity: ActivityDefinition, occurrence: int) -> ActivityDefinition:
    """Return the card for the ``occurrence``-th time its session type recurs.

    Args:
        activity: The registry card.
        occurrence: How many sessions of this type the plan held before this
            one (0 for the first).

    Returns:
        The card itself on its first set and for a type without
        alternatives; otherwise the card with that set's fields, or the
        set's shape built with the card's identity.
    """
    schemes = SET_SCHEMES.get(activity.key, ())
    index = occurrence % (len(schemes) + 1)
    if index == 0:
        return activity
    scheme = dict(schemes[index - 1])
    shape: type[ActivityDefinition] | None = scheme.pop("shape", None)
    if shape is None:
        return replace(activity, **scheme)
    identity = {f.name: getattr(activity, f.name) for f in fields(ActivityDefinition)}
    return shape(**identity, **scheme)


def set_occurrences(assignments: Iterable[ActivityAssignment]) -> dict[str, int]:
    """Number each session by how many of its type came before it in the plan.

    Args:
        assignments: The plan's day-assigned sessions.

    Returns:
        ``slot_id`` to occurrence index, counted per activity in date order.
    """
    seen: dict[ActivityKey, int] = {}
    occurrences: dict[str, int] = {}
    for a in sorted(assignments, key=lambda a: (a.date, a.slot_id)):
        occurrences[a.slot_id] = seen.get(a.activity_key, 0)
        seen[a.activity_key] = occurrences[a.slot_id] + 1
    return occurrences


__all__ = ["SET_SCHEMES", "set_occurrences", "with_set"]
