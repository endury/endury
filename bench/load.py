"""The public load model: zones, session load, daily fitness and fatigue.

Load is priced from steps, minutes at each zone's midpoint intensity factor
squared, the way TSS is defined; a plan without steps falls back to a
per-kind session intensity. Fitness and fatigue follow the conventional
42-day and 7-day exponential averages on daily loads, so form on race day
and the lowest form of the plan are readable, and so are Foster's monotony
and strain.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from bench.schema import Plan, Session

#: Zone -> intensity factor at the zone midpoint (Coggan's power zones; the
#: intensity dossier's table). TSS per hour is IF^2 x 100.
ZONE_IF: dict[int, float] = {1: 0.475, 2: 0.655, 3: 0.83, 4: 0.98, 5: 1.13}
#: Zone -> upper intensity-factor bound, for mapping a step's IF to a zone.
ZONE_UPPER: tuple[tuple[int, float], ...] = (
    (1, 0.55),
    (2, 0.75),
    (3, 0.90),
    (4, 1.05),
    (5, math.inf),
)
#: Session-mean intensity factor per kind, for plans that carry no steps.
KIND_IF: dict[str, float] = {
    "easy": 0.66,
    "recovery": 0.50,
    "long": 0.68,
    "tempo": 0.80,
    "threshold": 0.86,
    "intervals": 0.88,
    "race_pace": 0.78,
    "test": 0.90,
}
CTL_DAYS, ATL_DAYS = 42.0, 7.0
CTL_DECAY = math.exp(-1.0 / CTL_DAYS)
ATL_DECAY = math.exp(-1.0 / ATL_DAYS)


def zone_of(intensity_factor: float) -> int:
    """The zone a constant intensity factor falls in."""
    for zone, upper in ZONE_UPPER:
        if intensity_factor <= upper:
            return zone
    return ZONE_UPPER[-1][0]  # pragma: no cover


def session_tss(session: Session) -> float:
    """Training stress of one session from its steps, or from its kind."""
    if session.steps:
        return sum(
            s.minutes / 60.0 * ZONE_IF[s.zone] ** 2 * 100.0 for s in session.steps
        )
    return session.hours * KIND_IF.get(session.kind, KIND_IF["easy"]) ** 2 * 100.0


def daily_loads(plan: Plan) -> list[float]:
    """One load per calendar day of the plan, Monday of week 1 first.

    A session with no day is spread over the week's seven days.
    """
    out: list[float] = []
    for week in plan.weeks:
        days = [0.0] * 7
        for s in week.sessions:
            tss = session_tss(s)
            if s.day is None:
                for d in range(7):
                    days[d] += tss / 7.0
            else:
                days[s.day] += tss
        out.extend(days)
    return out


@dataclass
class Trajectory:
    """Daily fitness (CTL), fatigue (ATL) and form (TSB), plus race-day values.

    Race day is the Sunday of the last week, read after that day's sessions
    and before the race, so form is what the athlete carries to the line.
    """

    ctl: list[float]
    atl: list[float]
    race_ctl: float
    race_tsb: float

    @property
    def tsb(self) -> list[float]:
        """Form per day, entering that day."""
        return [c - a for c, a in zip(self.ctl, self.atl, strict=True)]

    @property
    def min_tsb(self) -> float:
        """The lowest form of the plan, entering a day."""
        return min(self.tsb) if self.tsb else 0.0

    @property
    def min_tsb_7d(self) -> float:
        """The lowest trailing seven-day mean of form.

        A hard weekend dips daily form for a day or two, which is the
        overload a block is built on; a week of it is what the overreaching
        literature warns about, so the floor reads the weekly mean.
        """
        tsb = self.tsb
        if len(tsb) < 7:
            return min(tsb) if tsb else 0.0
        return min(sum(tsb[i - 6 : i + 1]) / 7.0 for i in range(6, len(tsb)))


def trajectory(plan: Plan, seed_ctl: float) -> Trajectory:
    """Run the fitness and fatigue recursion over the plan's daily loads.

    Args:
        plan: The plan.
        seed_ctl: Fitness and fatigue on the day before week 1, in daily
            TSS, from the athlete's recent training (:func:`seed_fitness`).

    Returns:
        The trajectory, one entry per day (the state entering that day).
    """
    ctl = atl = seed_ctl
    ctls: list[float] = []
    atls: list[float] = []
    for load in daily_loads(plan):
        ctls.append(ctl)
        atls.append(atl)
        ctl = ctl * CTL_DECAY + load * (1.0 - CTL_DECAY)
        atl = atl * ATL_DECAY + load * (1.0 - ATL_DECAY)
    return Trajectory(ctls, atls, ctl, ctl - atl)


def seed_fitness(plan: Plan, recent_hours: dict[str, float]) -> float:
    """Daily fitness the athlete brings to week 1.

    Recent weekly hours per sport priced at the plan's own first-week
    intensity for that sport (the plan's opening week is the closest thing
    to what the athlete has been doing), divided by seven.
    """
    if not plan.weeks:
        return 0.0
    first = plan.weeks[0]
    total = 0.0
    for sport, hours in recent_hours.items():
        sessions = [s for s in first.sessions if s.sport == sport]
        sport_hours = sum(s.hours for s in sessions)
        per_hour = (
            sum(session_tss(s) for s in sessions) / sport_hours
            if sport_hours > 0
            else KIND_IF["easy"] ** 2 * 100.0
        )
        total += hours * per_hour
    return total / 7.0


def monotony_and_strain(plan: Plan) -> dict[int, tuple[float, float]]:
    """Foster's monotony (mean over sd of the seven daily loads) and strain per week.

    Rest days count as zero load. A week with identical daily loads has no
    spread and reads as infinite monotony.
    """
    loads = daily_loads(plan)
    out: dict[int, tuple[float, float]] = {}
    for i, week in enumerate(plan.weeks):
        days = loads[i * 7 : (i + 1) * 7]
        mean = statistics.mean(days)
        sd = statistics.pstdev(days)
        mono = mean / sd if sd > 0 else (math.inf if mean > 0 else 0.0)
        out[week.week] = (mono, sum(days) * mono if math.isfinite(mono) else math.inf)
    return out
