"""The plan table every contestant is graded on, and its vocabulary.

A plan is weeks of sessions; a session is a day, a sport, a type label,
minutes (metres for swims) and its steps, each step a zone and a duration.
Labels are the plan's own: the readings say which rules trust them and which
read the steps instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SPORTS: tuple[str, ...] = ("run", "bike", "swim")
KINDS: tuple[str, ...] = (
    "easy",
    "recovery",
    "long",
    "tempo",
    "threshold",
    "intervals",
    "race_pace",
    "test",
)
PHASES: tuple[str, ...] = ("base", "build", "peak", "taper")
MODES: tuple[str, ...] = ("load", "recovery", "taper", "race")
#: Every race goal a rule can cover. The benchmark's own scenarios ask for
#: the marathon, the half-Ironman and the Ironman; the others are graded on
#: the plans of a contestant that writes them.
GOALS: tuple[str, ...] = (
    "running_marathon",
    "half_ironman",
    "ironman",
    "running_5k",
    "running_10k",
    "running_half_marathon",
    "sprint_triathlon",
    "olympic_triathlon",
    "trail_20k",
    "trail_50k",
    "trail_100k",
)
TIERS: tuple[str, ...] = ("basic", "moderate", "competitive")
DAYS: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
ZONE_MIN, ZONE_MAX = 1, 5

SPORT_ALIASES: dict[str, str] = {
    "running": "run",
    "cycling": "bike",
    "cycle": "bike",
    "ride": "bike",
    "swimming": "swim",
    "brick": "run",
}
KIND_ALIASES: dict[str, str | None] = {
    "interval": "intervals",
    "vo2max": "intervals",
    "vo2": "intervals",
    "speed": "intervals",
    "hills": "intervals",
    "hill_repeats": "intervals",
    "fartlek": "intervals",
    "steady": "easy",
    "endurance": "easy",
    "aerobic": "easy",
    "base": "easy",
    "moderate": "easy",
    "brick": "easy",
    "strides": "easy",
    "drills": "easy",
    "drill": "easy",
    "technique": "easy",
    "sweet_spot": "tempo",
    "sweetspot": "tempo",
    "marathon_pace": "race_pace",
    "racepace": "race_pace",
    "mp": "race_pace",
    "time_trial": "test",
    "ftp_test": "test",
    "long_run": "long",
    "long_ride": "long",
    "long_swim": "long",
    "rest": None,
    "off": None,
}
PHASE_ALIASES: dict[str, str] = {
    "foundation": "base",
    "general": "base",
    "prep": "base",
    "preparation": "base",
    "specific": "build",
    "race_specific": "build",
    "competition": "peak",
    "sharpening": "peak",
    "race": "taper",
    "race_week": "taper",
}
MODE_ALIASES: dict[str, str] = {
    "normal": "load",
    "build": "load",
    "loading": "load",
    "deload": "recovery",
    "rest": "recovery",
    "easy": "recovery",
    "down": "recovery",
    "race_week": "race",
    "racing": "race",
}


@dataclass
class Step:
    """One segment of a workout: a zone and its length in minutes.

    Swim steps are given in metres by the author and scaled to minutes by
    the parser, so every step carries minutes; ``role`` says whether the
    step is a straight segment or the work or rest part of a repeat block.
    """

    zone: int
    minutes: float
    role: str = "straight"


@dataclass
class Repeat:
    """A repeat block as the author wrote it, kept for work-to-rest readings."""

    reps: int
    on_zone: int
    on_minutes: float
    off_zone: int | None
    off_minutes: float


@dataclass
class Session:
    """One workout."""

    day: int | None
    sport: str
    kind: str
    minutes: float
    metres: float | None = None
    steps: list[Step] = field(default_factory=list)
    repeats: list[Repeat] = field(default_factory=list)
    notes: str = ""

    @property
    def hours(self) -> float:
        """Session length in hours."""
        return self.minutes / 60.0

    def zone_minutes(self, low: int, high: int = ZONE_MAX) -> float:
        """Minutes of the session's steps in zones ``low`` to ``high``."""
        return sum(s.minutes for s in self.steps if low <= s.zone <= high)


@dataclass
class Week:
    """One week, labelled as its author labelled it."""

    week: int
    phase: str
    mode: str
    stated_total_min: float | None = None
    sessions: list[Session] = field(default_factory=list)

    @property
    def hours(self) -> float:
        """Training hours of the week; the race itself is never a session."""
        return sum(s.hours for s in self.sessions)


@dataclass
class Plan:
    """A whole plan, weeks in order, with the scenario it answers."""

    weeks: list[Week]
    goal: str
    tier: str
    horizon: int

    @property
    def sessions(self) -> list[tuple[Week, Session]]:
        """Every session with its week."""
        return [(w, s) for w in self.weeks for s in w.sessions]


@dataclass
class Structure:
    """What an answer looked like before any band was read."""

    weeks_asked: int
    weeks_delivered: int
    numbering_ok: bool
    race_week_last: bool
    stated_total_mismatches: int
    unknown_labels: int
    sessions: int
    sessions_without_steps: int
    step_sum_mismatches: int
    malformed_steps: int
    defects: list[str]

    @property
    def valid(self) -> bool:
        """Whether the answer is a complete plan: every week, in order, with sessions.

        Step defects are counted, not disqualifying; a plan that stops after
        one week is not a plan and is never graded.
        """
        return (
            self.weeks_delivered == self.weeks_asked
            and self.numbering_ok
            and self.sessions > 0
        )
