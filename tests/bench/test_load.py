import math

import pytest

from bench.load import (
    ZONE_IF,
    daily_loads,
    monotony_and_strain,
    seed_fitness,
    session_tss,
    trajectory,
    zone_of,
)
from bench.schema import Plan, Session, Step, Week


def test_zone_boundaries_follow_the_power_zones():
    assert [zone_of(v) for v in (0.5, 0.55, 0.6, 0.75, 0.8, 0.9, 1.0, 1.05, 1.2)] == [
        1,
        1,
        2,
        2,
        3,
        3,
        4,
        4,
        5,
    ]


def test_session_tss_is_if_squared_per_hour_and_falls_back_to_the_kind():
    s = Session(0, "run", "easy", 60, steps=[Step(2, 60)])
    assert session_tss(s) == pytest.approx(ZONE_IF[2] ** 2 * 100)
    bare = Session(0, "run", "threshold", 60)
    assert session_tss(bare) == pytest.approx(0.86**2 * 100)


def test_trajectory_and_monotony_on_a_two_week_plan():
    w1 = Week(
        1,
        "base",
        "load",
        sessions=[
            Session(d, "run", "easy", 60, steps=[Step(2, 60)]) for d in (0, 2, 4)
        ],
    )
    w2 = Week(
        2,
        "base",
        "load",
        sessions=[Session(d, "run", "easy", 60, steps=[Step(2, 60)]) for d in range(7)],
    )
    plan = Plan([w1, w2], "running_marathon", "moderate", 2)
    loads = daily_loads(plan)
    assert len(loads) == 14
    assert loads[1] == 0
    assert loads[0] == pytest.approx(42.9, abs=0.1)
    seed = seed_fitness(plan, {"run": 3.0})
    assert seed == pytest.approx(3 * 42.9 / 7, abs=0.1)
    t = trajectory(plan, seed)
    assert len(t.ctl) == 14
    assert t.race_ctl > t.ctl[0]
    assert t.race_tsb < 0  # a week of daily training leaves the athlete fatigued
    ms = monotony_and_strain(plan)
    assert ms[1][0] < 2.0
    assert math.isinf(ms[2][0])  # seven identical days
