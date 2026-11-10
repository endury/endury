"""The scenario matrix: who is training for what, over how long, under which limits.

The typical athlete of each goal and tier mirrors the profile Endury's
planner assumes when it knows nothing else, so the planner answers the same
brief as the models; the light and heavy athletes test whether a plan is
written for the person or for the race.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

START = date(2026, 1, 5)  # a Monday
GOAL_TEXT: dict[str, str] = {
    "running_marathon": "a road marathon (42.2 km)",
    "half_ironman": (
        "a half-distance triathlon (1.9 km swim, 90 km bike, 21.1 km run)"
    ),
    "ironman": (
        "a full-distance Ironman triathlon (3.8 km swim, 180 km bike, 42.2 km run)"
    ),
}
TIER_TEXT: dict[str, str] = {
    "basic": "Basic (conservative): health-focused or time-constrained athlete.",
    "moderate": (
        "Moderate (balanced): a committed age-grouper with a consistent "
        "training history."
    ),
    "competitive": (
        "Competitive (maximum): an experienced athlete pursuing a personal best."
    ),
}


@dataclass(frozen=True)
class Athlete:
    """What the brief says about the athlete's recent training."""

    label: str
    hours: dict[str, float]
    longest_min: dict[str, int]


@dataclass(frozen=True)
class Scenario:
    """One cell of the matrix."""

    label: str
    goal: str
    tier: str
    weeks: int
    athlete: Athlete
    weekly_cap_hours: float | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def race_day(self) -> date:
        """The Sunday of the last week."""
        return START + timedelta(weeks=self.weeks, days=-1)


def _typical(goal: str, tier: str) -> Athlete:
    hours = {
        ("running_marathon", "basic"): {"run": 2.9},
        ("running_marathon", "moderate"): {"run": 3.0},
        ("running_marathon", "competitive"): {"run": 3.5},
        ("half_ironman", "basic"): {"run": 2.54, "bike": 3.62, "swim": 1.09},
        ("half_ironman", "moderate"): {"run": 2.62, "bike": 3.75, "swim": 1.12},
        ("half_ironman", "competitive"): {"run": 3.06, "bike": 4.38, "swim": 1.31},
        ("ironman", "basic"): {"run": 2.25, "bike": 4.12, "swim": 1.12},
        ("ironman", "moderate"): {"run": 2.4, "bike": 4.4, "swim": 1.2},
        ("ironman", "competitive"): {"run": 2.77, "bike": 5.09, "swim": 1.39},
    }[goal, tier]
    longest = {sp: {"run": 60, "bike": 90, "swim": 30}[sp] for sp in hours}
    return Athlete("typical", hours, longest)


def _scaled(goal: str, tier: str, label: str, factor: float) -> Athlete:
    base = _typical(goal, tier)
    return Athlete(
        label,
        {sp: round(h * factor, 2) for sp, h in base.hours.items()},
        {sp: round(m * factor) for sp, m in base.longest_min.items()},
    )


def _cell(
    goal: str,
    tier: str,
    weeks: int,
    athlete: Athlete | None = None,
    cap: float | None = None,
    *tags: str,
) -> Scenario:
    athlete = athlete or _typical(goal, tier)
    short = {
        "running_marathon": "marathon",
        "half_ironman": "half",
        "ironman": "ironman",
    }
    label = f"{short[goal]}_{weeks}wk_{tier}"
    if athlete.label != "typical":
        label += f"_{athlete.label}"
    if cap is not None:
        label += f"_cap{cap:g}"
    return Scenario(label, goal, tier, weeks, athlete, cap, tags)


HEADLINE: tuple[Scenario, ...] = (
    _cell("running_marathon", "moderate", 18, None, None, "headline"),
    _cell("ironman", "competitive", 32, None, None, "headline"),
)
HORIZONS: tuple[Scenario, ...] = (
    _cell("running_marathon", "moderate", 12, None, None, "horizon"),
    _cell("running_marathon", "moderate", 24, None, None, "horizon"),
    _cell("ironman", "competitive", 22, None, None, "horizon"),
    _cell("ironman", "competitive", 24, None, None, "horizon"),
    _cell("ironman", "competitive", 28, None, None, "horizon"),
    _cell("half_ironman", "moderate", 24, None, None, "horizon"),
)
ATHLETES: tuple[Scenario, ...] = (
    _cell(
        "ironman",
        "competitive",
        32,
        _scaled("ironman", "competitive", "light", 0.6),
        None,
        "athlete",
    ),
    _cell(
        "ironman",
        "competitive",
        32,
        _scaled("ironman", "competitive", "heavy", 1.5),
        None,
        "athlete",
    ),
    _cell(
        "running_marathon",
        "moderate",
        18,
        _scaled("running_marathon", "moderate", "light", 0.6),
        None,
        "athlete",
    ),
    _cell(
        "running_marathon",
        "moderate",
        18,
        _scaled("running_marathon", "moderate", "heavy", 1.5),
        None,
        "athlete",
    ),
)
CAPS: tuple[Scenario, ...] = (
    _cell("ironman", "competitive", 32, None, 12.0, "cap"),
    _cell("running_marathon", "moderate", 18, None, 5.0, "cap"),
)
MATRIX: tuple[Scenario, ...] = HEADLINE + HORIZONS + ATHLETES + CAPS


def by_label(label: str) -> Scenario:
    """The scenario called ``label``."""
    try:
        return next(s for s in MATRIX if s.label == label)
    except StopIteration:
        raise KeyError(label) from None
