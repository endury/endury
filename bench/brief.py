"""The one request every model gets: the athlete, the race, the plan shape.

Nothing in the blind brief names a band. The ``bands`` arm appends the
rulebook's bands, to measure instruction following apart from knowledge.
"""

from __future__ import annotations

from bench.rules import Rulebook
from bench.scenarios import GOAL_TEXT, START, TIER_TEXT, Scenario

SYSTEM_PROMPT = (
    "You are an experienced endurance coach who writes complete, periodised "
    "training plans with fully specified workouts."
)
ARMS: tuple[str, ...] = ("blind", "bands")
SHAPE = (
    '{"weeks":[{"week":1,"phase":"base","mode":"load","total_minutes":300,"sessions":['
    '{"day":"tue","sport":"bike","type":"easy","minutes":60,"steps":[[1,10],[2,45],[1,5]]},'
    '{"day":"wed","sport":"run","type":"intervals","minutes":50,'
    '"steps":[[1,15],{"reps":5,"on":[5,3],"off":[1,3]},[1,5]]},'
    '{"day":"fri","sport":"swim","type":"long","minutes":60,"metres":2800,'
    '"steps":[[1,400],[2,2000],[1,400]],"notes":"steady, bilateral breathing"}]}]}'
)
ZONE_TEXT = (
    "1 recovery (under 55 % of FTP, under 68 % of threshold HR, very easy pace); "
    "2 endurance (56-75 % FTP, 69-83 % LTHR, easy conversational pace); "
    "3 tempo (76-90 % FTP, 84-94 % LTHR, marathon-to-half-marathon pace); "
    "4 threshold (91-105 % FTP, 95-105 % LTHR, 10 km-to-hour pace); "
    "5 VO2max (106-120 % FTP, over 105 % LTHR, 3-5 km pace)."
)


def athlete_lines(scenario: Scenario) -> str:
    """The athlete's recent training, one line per sport."""
    a = scenario.athlete
    return "\n".join(
        f"  - {sport}: {hours:.1f} h/week; longest recent session {a.longest_min[sport]} min"
        for sport, hours in a.hours.items()
    )


def build_brief(
    scenario: Scenario, rulebook: Rulebook | None = None, arm: str = "blind"
) -> str:
    """The user message for a scenario.

    Args:
        scenario: The cell.
        rulebook: Needed for the ``bands`` arm.
        arm: ``blind`` (nothing about the bands) or ``bands`` (the bands appended).

    Returns:
        The brief.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    weeks = scenario.weeks
    sports = " | ".join(f'"{s}"' for s in scenario.athlete.hours)
    cap = (
        f"Keep every training week at or under {scenario.weekly_cap_hours:g} hours."
        if scenario.weekly_cap_hours is not None
        else "none"
    )
    text = f"""Write a complete {weeks}-week training plan for the athlete below: every week, every session, every session's steps.

Goal: {GOAL_TEXT[scenario.goal]}, the season's A race, on Sunday {scenario.race_day.isoformat()}.
Training starts Monday {START.isoformat()} (week 1). The race is on the Sunday of week {weeks}.
Ambition: {TIER_TEXT[scenario.tier]}
Current training (the last few weeks):
{athlete_lines(scenario)}
Preferences: {cap}. No strength or mobility sessions. A day off is simply a day with no session.

Answer with ONE JSON object and nothing else (no prose, no code fences), in exactly this shape:
{SHAPE}

Field rules:
- "week": 1..{weeks}; every week present exactly once, in order.
- "phase": "base" | "build" | "peak" | "taper".
- "mode": "load" (a normal training week) | "recovery" (a deliberately lighter week) | "taper" (a pre-race week) | "race" (the race week itself, week {weeks} only).
- "total_minutes": the sum of that week's session minutes.
- "day": "mon".."sun". "sport": {sports}.
- "type": "easy" | "recovery" | "long" (the week's long session) | "tempo" | "threshold" | "intervals" (VO2max-type hard intervals) | "race_pace" (a race-pace rehearsal) | "test" (a fitness test).
- "minutes": an integer, the whole session including warm-up and cool-down. Swims also give "metres". One session is one workout; two workouts on the same day are two sessions.
- "steps": the workout in order. A step is [zone, minutes] (swim steps: [zone, metres]). A repeat block is {{"reps": N, "on": [zone, minutes], "off": [zone, minutes]}} (swim: metres). The steps sum to the session's minutes (swim: metres).
- Zones: {ZONE_TEXT}
- "notes": optional, one short line (purpose, fuelling), at most 12 words.
- Do not list the race itself."""
    if arm == "bands":
        if rulebook is None:
            raise ValueError("the bands arm needs the rulebook")
        lines = [
            f"- {rule.name}: {rule.reads}; target {band.low:g} to {band.high:g}"
            for rule, band in rulebook.applicable(scenario.goal, scenario.tier)
            if rule.grade != "S"
        ]
        text += "\n\nThe plan will be checked against these targets:\n" + "\n".join(
            lines
        )
    return text
