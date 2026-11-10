"""Synthetic plans for the benchmark tests: small, explicit, one feature at a time."""

from __future__ import annotations

import pytest

from bench.schema import Plan, Repeat, Session, Step, Week

EASY = [Step(1, 10), Step(2, 40), Step(1, 5)]


def session(
    day: int,
    sport: str = "run",
    kind: str = "easy",
    minutes: float = 55,
    steps: list[Step] | None = None,
    metres: float | None = None,
    notes: str = "",
    repeats: list[Repeat] | None = None,
) -> Session:
    steps = list(EASY) if steps is None else steps
    total = sum(s.minutes for s in steps)
    if total and abs(total - minutes) > 1e-6:
        steps = [Step(s.zone, s.minutes * minutes / total, s.role) for s in steps]
    return Session(day, sport, kind, minutes, metres, steps, repeats or [], notes)


def hard_run(day: int, kind: str = "threshold") -> Session:
    return session(day, "run", kind, 60, [Step(1, 15), Step(4, 30), Step(1, 15)])


def vo2_run(day: int) -> Session:
    steps = [Step(1, 15)] + [Step(5, 3, "on"), Step(1, 3, "off")] * 5 + [Step(1, 5)]
    return session(day, "run", "intervals", 50, steps, repeats=[Repeat(5, 5, 3, 1, 3)])


def long_run(day: int, minutes: float) -> Session:
    return session(
        day,
        "run",
        "long",
        minutes,
        [Step(1, 10), Step(2, minutes - 15), Step(1, 5)],
        notes="steady; fuel 60 g/h from the first hour",
    )


def marathon_plan(weeks: int = 12) -> Plan:
    """A tidy 12-week marathon: base, build, peak, two taper weeks, race week."""
    out: list[Week] = []
    for n in range(1, weeks + 1):
        if n == weeks:
            out.append(
                Week(
                    n,
                    "taper",
                    "race",
                    sessions=[session(1, minutes=30), session(3, minutes=25)],
                )
            )
            continue
        if n >= weeks - 2:
            phase, mode = "taper", "taper"
            hours = 3.5 if n == weeks - 2 else 3.0
        elif n % 4 == 0:
            phase, mode = ("base" if n <= 4 else "build"), "recovery"
            hours = 3.0
        else:
            phase = "base" if n <= 4 else ("build" if n <= 8 else "peak")
            mode = "load"
            hours = 3.5 + 0.3 * n
        long_min = {weeks - 2: 75, weeks - 1: 45}.get(
            n, 60 + 8 * n if mode == "load" else 60
        )
        sessions = [
            session(1, minutes=45),
            hard_run(2, "tempo") if mode != "recovery" else session(2, minutes=40),
            session(4, minutes=40, notes="easy, fuel with 40 g/h carbs"),
            long_run(6, long_min),
        ]
        rest = hours * 60 - sum(s.minutes for s in sessions)
        if rest > 20:
            sessions.append(session(5, minutes=rest, notes="fuel 60 g/h"))
        out.append(Week(n, phase, mode, sessions=sessions))
    return Plan(out, "running_marathon", "moderate", weeks)


@pytest.fixture
def marathon() -> Plan:
    return marathon_plan()
