"""A model's answer, as text, into the plan table, recording every defect.

The parser is lenient about labels and strict about arithmetic: unknown
labels are mapped or counted, steps that do not sum to the session's minutes
are scaled to it and counted, and nothing is invented for a session that came
back without steps.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bench.schema import (
    DAYS,
    KIND_ALIASES,
    KINDS,
    MODE_ALIASES,
    MODES,
    PHASE_ALIASES,
    PHASES,
    SPORT_ALIASES,
    SPORTS,
    ZONE_MAX,
    ZONE_MIN,
    Plan,
    Repeat,
    Session,
    Step,
    Structure,
    Week,
)

#: Stated weekly totals within this many minutes of the sessions' sum agree.
TOTAL_TOLERANCE_MIN = 5.0
#: Step sums within this share of the session's minutes agree.
STEP_TOLERANCE = 0.10
#: A comma before a closing bracket: the one JSON slip the parser repairs.
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(text: str) -> tuple[Any, str | None]:
    """Return the answer's JSON object, tolerating fences and prose around it.

    Returns ``(data, message)``: the message is an error when ``data`` is
    ``None`` and a repair note when a trailing comma had to be removed.
    """
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
    if fence:
        body = fence.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None, "no JSON object in the answer"
    body = body[start : end + 1]
    try:
        return json.loads(body), None
    except json.JSONDecodeError as exc:
        error = f"invalid JSON: {exc.msg} at char {exc.pos}"
    repaired = _TRAILING_COMMA.sub(r"\1", body)
    if repaired != body:
        try:
            return json.loads(repaired), f"json repaired: trailing commas ({error})"
        except json.JSONDecodeError:
            pass
    return None, error


def _normalise(
    value: object, aliases: dict[str, Any], allowed: tuple[str, ...]
) -> tuple[str | None, bool]:
    """Map a free-text label onto the vocabulary; ``(label, unknown)``."""
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in allowed:
        return raw, False
    if raw in aliases:
        return aliases[raw], False
    for key in allowed:
        if key in raw:
            return key, False
    return None, True


def _zone(value: object) -> int | None:
    try:
        zone = int(float(str(value)))
    except (TypeError, ValueError):
        return None
    return zone if ZONE_MIN <= zone <= ZONE_MAX else None


def _pair(value: object) -> tuple[int, float] | None:
    """A ``[zone, amount]`` pair, or None when malformed."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    zone = _zone(value[0])
    try:
        amount = float(value[1])
    except (TypeError, ValueError):
        return None
    if zone is None or amount < 0:
        return None
    return zone, amount


def _parse_steps(raw: object, session: Session, where: str, defects: list[str]) -> int:
    """Fill the session's steps and repeats; returns the malformed-step count."""
    if not raw:
        return 0
    if not isinstance(raw, list):
        defects.append(f"{where}: steps is not a list")
        return 1
    bad = 0
    for item in raw:
        if isinstance(item, dict):
            on = _pair(item.get("on"))
            off = _pair(item.get("off")) if item.get("off") is not None else None
            try:
                reps = int(item.get("reps", 1))
            except (TypeError, ValueError):
                reps = 0
            if on is None or reps <= 0 or (item.get("off") is not None and off is None):
                bad += 1
                defects.append(f"{where}: malformed repeat {item!r}"[:160])
                continue
            session.repeats.append(
                Repeat(
                    reps, on[0], on[1], off[0] if off else None, off[1] if off else 0.0
                )
            )
            for _ in range(reps):
                session.steps.append(Step(on[0], on[1], "on"))
                if off:
                    session.steps.append(Step(off[0], off[1], "off"))
        else:
            pair = _pair(item)
            if pair is None:
                bad += 1
                defects.append(f"{where}: malformed step {item!r}"[:160])
                continue
            session.steps.append(Step(pair[0], pair[1]))
    return bad


def _scale_steps(session: Session, where: str, defects: list[str]) -> bool:
    """Scale the steps to the session's minutes; True when they disagreed.

    Swim steps are written in metres and always scaled to minutes; other
    steps are scaled only when their sum is off by more than the tolerance,
    and that is a defect of the answer.
    """
    total = sum(s.minutes for s in session.steps)
    if not session.steps or total <= 0 or session.minutes <= 0:
        return False
    in_metres = session.sport == "swim" and session.metres
    if (
        not in_metres
        and abs(total - session.minutes) <= STEP_TOLERANCE * session.minutes
    ):
        return False
    factor = session.minutes / total
    for step in session.steps:
        step.minutes *= factor
    for rep in session.repeats:
        rep.on_minutes *= factor
        rep.off_minutes *= factor
    if in_metres:
        return False
    defects.append(
        f"{where}: steps sum to {total:.0f} min, session says {session.minutes:.0f}"
    )
    return True


def _parse_session(
    raw: dict[str, Any], week_no: int, defects: list[str]
) -> tuple[Session | None, int, int, bool]:
    """One session; ``(session, unknown labels, malformed steps, step mismatch)``."""
    unknown = 0
    sport, bad = _normalise(raw.get("sport"), SPORT_ALIASES, SPORTS)
    if bad or sport is None:
        defects.append(f"week {week_no}: unknown sport {raw.get('sport')!r}")
        return None, 1, 0, False
    kind, bad = _normalise(raw.get("type"), KIND_ALIASES, KINDS)
    if bad:
        unknown += 1
        defects.append(f"week {week_no}: unknown session type {raw.get('type')!r}")
        kind = "easy"
    if kind is None:  # an explicit rest entry
        return None, unknown, 0, False
    day = DAYS.get(str(raw.get("day", "")).strip().lower()[:3])
    if day is None:
        unknown += 1
        defects.append(f"week {week_no}: unknown day {raw.get('day')!r}")
    try:
        minutes = float(raw.get("minutes") or 0)
    except (TypeError, ValueError):
        minutes, unknown = 0.0, unknown + 1
    metres: float | None
    try:
        metres = float(raw["metres"]) if raw.get("metres") is not None else None
    except (TypeError, ValueError):
        metres, unknown = None, unknown + 1
    if minutes <= 0:
        return None, unknown, 0, False
    session = Session(
        day, sport, kind, minutes, metres, notes=str(raw.get("notes") or "")
    )
    where = f"week {week_no} {raw.get('day', '?')} {sport} {kind}"
    malformed = _parse_steps(raw.get("steps"), session, where, defects)
    mismatch = _scale_steps(session, where, defects)
    return session, unknown, malformed, mismatch


def _parse_week(
    raw: dict[str, Any], position: int, defects: list[str]
) -> tuple[Week, int, int, int]:
    """One week; ``(week, unknown labels, malformed steps, step mismatches)``."""
    unknown = malformed = mismatches = 0
    try:
        num = int(raw.get("week", position))
    except (TypeError, ValueError):
        num, unknown = position, 1
    phase, bad = _normalise(raw.get("phase"), PHASE_ALIASES, PHASES)
    if bad:
        unknown += 1
        defects.append(f"week {num}: unknown phase {raw.get('phase')!r}")
    mode, bad = _normalise(raw.get("mode"), MODE_ALIASES, MODES)
    if bad:
        unknown += 1
        defects.append(f"week {num}: unknown mode {raw.get('mode')!r}")
    stated: float | None
    try:
        stated = (
            float(raw["total_minutes"])
            if raw.get("total_minutes") is not None
            else None
        )
    except (TypeError, ValueError):
        stated = None
    week = Week(num, phase or "build", mode or "load", stated)
    for rs in raw.get("sessions") or []:
        if not isinstance(rs, dict):
            unknown += 1
            continue
        session, bad_labels, bad_steps, mismatch = _parse_session(rs, num, defects)
        unknown += bad_labels
        malformed += bad_steps
        mismatches += int(mismatch)
        if session is not None:
            week.sessions.append(session)
    return week, unknown, malformed, mismatches


def parse_plan(
    text: str, goal: str, tier: str, weeks_asked: int
) -> tuple[Plan | None, Structure]:
    """Parse an answer into the plan table.

    Args:
        text: The model's whole answer.
        goal: The scenario's goal, carried on the plan.
        tier: The scenario's tier, carried on the plan.
        weeks_asked: The plan length the brief asked for.

    Returns:
        The plan (``None`` when nothing gradable came back) and what the
        answer looked like structurally. The final week is always graded as
        the race week, whatever the author labelled it.
    """
    defects: list[str] = []
    data, message = extract_json(text)
    raw_weeks = data.get("weeks") if isinstance(data, dict) else None
    if data is None or not isinstance(raw_weeks, list):
        defects.append(message or "no 'weeks' list")
        return None, Structure(weeks_asked, 0, False, False, 0, 0, 0, 0, 0, 0, defects)
    if message:
        defects.append(message)

    unknown = malformed = mismatches = totals_off = 0
    weeks: list[Week] = []
    for i, raw in enumerate(raw_weeks):
        if not isinstance(raw, dict):
            unknown += 1
            defects.append(f"week entry {i + 1} is not an object")
            continue
        week, bad, bad_steps, bad_sums = _parse_week(raw, i + 1, defects)
        unknown += bad
        malformed += bad_steps
        mismatches += bad_sums
        if (
            week.stated_total_min is not None
            and abs(week.stated_total_min - week.hours * 60.0) > TOTAL_TOLERANCE_MIN
        ):
            totals_off += 1
            defects.append(
                f"week {week.week}: stated total {week.stated_total_min:.0f} min, "
                f"sessions sum to {week.hours * 60:.0f}"
            )
        weeks.append(week)

    weeks.sort(key=lambda w: w.week)
    numbering_ok = [w.week for w in weeks] == list(range(1, weeks_asked + 1))
    if not numbering_ok:
        defects.append(f"weeks delivered: {len(weeks)} of {weeks_asked}")
    race_modes = [w.week for w in weeks if w.mode == "race"]
    race_last = bool(weeks) and race_modes == [weeks[-1].week]
    if not race_last:
        defects.append(
            f"race-mode weeks: {race_modes} (expected only week {weeks_asked})"
        )
    if weeks:
        weeks[-1].mode = "race"
    all_sessions = [s for w in weeks for s in w.sessions]
    structure = Structure(
        weeks_asked,
        len(weeks),
        numbering_ok,
        race_last,
        totals_off,
        unknown,
        len(all_sessions),
        sum(1 for s in all_sessions if not s.steps),
        mismatches,
        malformed,
        defects,
    )
    plan = Plan(weeks, goal, tier, weeks_asked) if weeks else None
    return plan, structure
