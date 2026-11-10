"""Assembly of the brief sheet for a coming planned session or race.

The sheet is deterministic: the athlete's intervals.icu history, their form
and wellness, the plan's card and the YAML bands of this package, no AI
model. Its JSON shape is the one contract between this package, the RPC
method that serves it to the page and the coach tool that reads it: every
key is always present, a section that does not apply is ``None``, numbers
are rounded for display, and every string is athlete-facing. A missing
input leaves its field ``None`` and a ``thin`` line, never an invented
number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from endury.common import clock
from endury.intervals.fitness import advance_training_load
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    IntervalsActivity,
    WellnessDay,
)
from endury.planner.foundations.types import SportType
from endury.planning.brief.fuel import (
    fuel_plan,
    kit_lines,
    span,
    typical_race_minutes,
)
from endury.planning.brief.history import (
    HOLD_BUCKETS,
    POWER_BUCKETS,
    best_by_bucket,
    climb_rate_m_per_h,
    hold_curve,
    power_curve,
    recent_sessions,
    recent_temp_c,
    routes_like,
    trail_estimate,
)
from endury.planning.race_day import race_sport
from endury.planning.wire.sessions import serialize_plan_session_row

#: Sessions shorter than this are briefed by their card alone.
_ANCHOR_MIN_MINUTES = 75

#: Sessions at least this long are judged on how they finished.
_FINISH_FEEL_MINUTES = 150

_HILLY_KEYS = ("trail", "vert", "hill")

_RACE_INTENT = {
    "A": "Your A race; the plan tapers into it.",
    "B": "A tune-up: a hard day inside a training week, not the goal.",
    "C": "A training race: a hard day inside a normal week, not the goal.",
}

_HOURS = {60: "an hour", 120: "two hours", 180: "three hours", 240: "four hours"}

_STEP_WORDS = {
    "warmup": "Warm-up",
    "cooldown": "Cool-down",
    "interval": "Main",
    "recovery": "Easy",
    "rest": "Rest",
}
_REP_WORDS = {"interval": "Rep", "recovery": "Recover", "rest": "Rest"}


@dataclass
class _Subject:
    """What the sheet is about, resolved from a session row or a race."""

    kind: str
    handle: str
    title: str
    sport: SportType | None
    sport_label: str | None
    day: date
    duration_min: int | None
    purpose: dict[str, Any]
    distance_km: float | None = None
    climb_m: int | None = None
    priority: str | None = None
    load: int | None = None
    optional: bool = False
    hilly: bool = False
    steps: list[dict[str, Any]] = field(default_factory=list)
    notes: str | None = None
    goal_type: str | None = None


def build_brief(
    *,
    repos: Any,
    client: Any,
    session_id: int | None = None,
    race_date: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Build the brief sheet for one planned session or one race.

    Args:
        repos: The repository container (``plan`` and ``races``).
        client: The athlete's intervals.icu client.
        session_id: The ``plan_sessions`` row id of the session to brief.
        race_date: The ISO date of the race to brief.
        today: The day the sheet is built for; defaults to the clock.

    Returns:
        The sheet, in the shape the web and the coach read.

    Raises:
        ValueError: When not exactly one subject is given, or it is unknown.
    """
    if (session_id is None) == (race_date is None):
        raise ValueError("Brief either a planned session or a race.")
    today = today or clock.today()
    if session_id is not None:
        subject = _session_subject(repos, session_id, today)
    else:
        subject = _race_subject(repos, str(race_date))
    # The coach's readers are imported here, not at module level: the coach's
    # tool package imports this one, so a top-level import would be a cycle.
    from endury.coach.wellness_context import WELLNESS_BASELINE_DAYS, fetch_wellness

    activities = client.list_activities(today - timedelta(days=365), today)
    wellness = fetch_wellness(client, today, days=WELLNESS_BASELINE_DAYS)
    thin: list[str] = []
    estimate = _estimate(subject, activities, today, thin)
    if estimate is not None:
        low, high = estimate["range_min"]
        subject.duration_min = round((low + high) / 2)
    temps = recent_temp_c(activities, today=today)
    fuel = _fuel(subject, wellness, temps, thin)
    hold = _hold(subject, activities, client, thin)
    return {
        "subject": _subject_payload(subject, today),
        "purpose": subject.purpose,
        "state": _state(subject, repos, client, activities, wellness, today, thin),
        "hold": hold,
        "fuel": fuel,
        "conditions": _conditions(subject, temps, thin),
        "routes": _routes(subject, activities, today),
        "estimate": estimate,
        "logistics": {"notes": subject.notes} if subject.notes else None,
        "rubric": _rubric(subject, hold, fuel),
        "thin": thin,
    }


# -- Subject ---------------------------------------------------------------


def _sport(value: str | None) -> SportType | None:
    """Return the planner sport for a stored sport string, or None."""
    try:
        return SportType(value or "")
    except ValueError:
        return None


def _session_subject(repos: Any, session_id: int, today: date) -> _Subject:
    """Resolve a planned session row into the sheet's subject.

    Raises:
        ValueError: When no session has that id.
    """
    row = repos.plan.get_session_by_id(session_id)
    if row is None:
        raise ValueError(f"No planned session with id {session_id}")
    payload = serialize_plan_session_row(row, today=today)
    distance_m = payload["planned_metrics"]["distance_m"]
    minutes = payload["duration_minutes"]
    return _Subject(
        kind="session",
        handle=f"session:{session_id}",
        title=payload["description"],
        sport=_sport(payload["sport"]),
        sport_label=payload["sport"],
        day=date.fromisoformat(str(payload["date"])),
        duration_min=round(float(minutes)) if minutes is not None else None,
        purpose={
            "intent": payload["coaching_intent"] or None,
            "why": payload["why_details"],
            "cues": payload["coaching_notes"],
        },
        distance_km=round(distance_m / 1000.0, 1) if distance_m else None,
        load=round(payload["target_tss"]),
        optional=payload["optional"],
        hilly=any(key in payload["activity_key"] for key in _HILLY_KEYS),
        steps=list(payload["workout"].get("steps") or []),
    )


def _race_sport(goal_type: str | None) -> tuple[SportType | None, str | None]:
    """Return the sport a race goal is run in, and its label."""
    if goal_type is None:
        return None, None
    sport = race_sport(goal_type)
    return sport, sport.value if sport is not None else "triathlon"


def _race_subject(repos: Any, race_date: str) -> _Subject:
    """Resolve a race by its date: the race table first, then the plan's goal.

    Raises:
        ValueError: When neither holds a race on that date.
    """
    race = next(
        (
            r
            for r in repos.races.list(include_past_days=0)
            if r.race_date == race_date and r.status != "cancelled"
        ),
        None,
    )
    plan = repos.plan.get_active()
    if race is not None:
        name, goal_type, priority = race.name, race.goal_type, race.priority
        distance, climb, notes = race.distance_km, race.climb_m, race.notes
    elif plan is not None and str(plan.goal_date) == race_date:
        name, goal_type, priority = plan.name, plan.goal_type, "A"
        distance, climb, notes = None, None, None
    else:
        raise ValueError(f"No race on {race_date}")
    sport, label = _race_sport(goal_type)
    return _Subject(
        kind="race",
        handle=f"race:{race_date}",
        title=name,
        sport=sport,
        sport_label=label,
        day=date.fromisoformat(race_date),
        duration_min=None,
        purpose={"intent": _RACE_INTENT[priority], "why": [], "cues": []},
        distance_km=round(distance, 1) if distance is not None else None,
        climb_m=round(climb) if climb is not None else None,
        priority=priority,
        hilly=str(goal_type).startswith("trail_") or (climb or 0) >= 300,
        notes=notes or None,
        goal_type=str(goal_type) if goal_type else None,
    )


def _subject_payload(subject: _Subject, today: date) -> dict[str, Any]:
    """Return the sheet's ``subject`` block."""
    return {
        "kind": subject.kind,
        "handle": subject.handle,
        "title": subject.title,
        "sport": subject.sport_label,
        "date": subject.day.isoformat(),
        "days_away": (subject.day - today).days,
        "duration_min": subject.duration_min,
        "distance_km": subject.distance_km,
        "climb_m": subject.climb_m,
        "priority": subject.priority,
        "load": subject.load,
        "optional": subject.optional,
    }


# -- State going in --------------------------------------------------------


def _state(
    subject: _Subject,
    repos: Any,
    client: Any,
    activities: list[IntervalsActivity],
    wellness: list[WellnessDay],
    today: date,
    thin: list[str],
) -> dict[str, Any]:
    """Return the ``state`` block: form today and on the day, wellness, recent."""
    from endury.coach.form_context import fetch_form
    from endury.coach.wellness_context import wellness_flags

    form, _ = fetch_form(client, today, weeks_back=4)
    measured = bool(form.by_sport)
    if not measured:
        thin.append("No training load is recorded yet, so there is no form reading.")
    settled = _settled(wellness)
    return {
        "as_of": today.isoformat(),
        "ctl": round(form.ctl, 1) if measured else None,
        "atl": round(form.atl, 1) if measured else None,
        "tsb": form.tsb if measured else None,
        "tsb_read": _form_words(form.tsb) if measured else None,
        "projected": (
            _projected(repos, form.ctl, form.atl, today, subject.day)
            if measured
            else None
        ),
        "wellness": _wellness(settled),
        "flags": [_capitalized(flag) for flag in wellness_flags(settled)],
        "recent": recent_sessions(activities, subject.sport),
    }


def _capitalized(text: str) -> str:
    """Return the text with its first letter upper-cased."""
    return text[:1].upper() + text[1:]


def _hours_words(minutes: int) -> str:
    """Say a length in hours and minutes: "5 hours 45 minutes"."""
    hours, rest = divmod(int(minutes), 60)
    if not hours:
        return f"{rest} minutes"
    words = "1 hour" if hours == 1 else f"{hours} hours"
    return words if not rest else f"{words} {rest} minutes"


def _form_words(tsb: float) -> str:
    """Say what the form figure means, in the athlete's words.

    The bands are the coach's ``tsb_zone_read`` bands (Coggan's PMC
    convention, docs/research/endurance_methodology.md); the words are for
    a sheet the athlete reads, so they name training states, never health.
    """
    if tsb > 25:
        return "Very fresh, more than a race needs"
    if tsb >= 15:
        return "Race fresh"
    if tsb >= 5:
        return "Fresh: ready for a key day"
    if tsb >= -10:
        return "Steady: carrying normal training"
    if tsb >= -30:
        return "Carrying a heavy load"
    return "Deep in fatigue: go easy"


def _settled(rows: list[WellnessDay]) -> list[WellnessDay]:
    """Drop a trailing day whose night is not in yet.

    A watch writes today's resting heart rate hours before its HRV and sleep
    arrive, and that half-filled row reads as a spike. The reading and its
    flags end on the last day that carries HRV or sleep.
    """
    settled = list(rows)
    while settled and settled[-1].hrv is None and not settled[-1].sleep_secs:
        settled.pop()
    return settled


def _projected(
    repos: Any, ctl: float, atl: float, today: date, day: date
) -> dict[str, Any] | None:
    """Advance today's form through the plan's sessions to the subject's morning.

    Optional and skipped sessions add no load; the subject's own day does
    not count, so the result is the form the athlete starts it with.
    """
    if day <= today:
        return None
    plan = repos.plan.get_active()
    rows = (
        repos.plan.get_sessions_by_date_range(
            plan.id,
            (today + timedelta(days=1)).isoformat(),
            (day - timedelta(days=1)).isoformat(),
        )
        if plan is not None
        else []
    )
    by_day: dict[str, float] = {}
    for row in rows:
        if not getattr(row, "optional", False) and row.status != "skipped":
            by_day[str(row.date)] = by_day.get(str(row.date), 0.0) + float(
                row.target_tss
            )
    current = today + timedelta(days=1)
    while current < day:
        ctl, atl = advance_training_load(ctl, atl, by_day.get(current.isoformat(), 0.0))
        current += timedelta(days=1)
    return {"date": day.isoformat(), "ctl": round(ctl, 1), "tsb": round(ctl - atl, 1)}


def _wellness(rows: list[WellnessDay]) -> dict[str, Any] | None:
    """Return the latest settled wellness reading with the 28-day HRV baseline."""
    latest = rows[-1] if rows else None
    if latest is None:
        return None
    hrvs = [row.hrv for row in rows[-28:] if row.hrv is not None]
    return {
        "hrv": round(latest.hrv) if latest.hrv is not None else None,
        "hrv_baseline": round(sum(hrvs) / len(hrvs)) if hrvs else None,
        "resting_hr": latest.resting_hr,
        "sleep_h": round(latest.sleep_secs / 3600.0, 1) if latest.sleep_secs else None,
    }


def _weight(rows: list[WellnessDay]) -> float | None:
    """Return the athlete's latest logged weight, or None."""
    return next((row.weight for row in reversed(rows) if row.weight), None)


# -- What to hold ----------------------------------------------------------


def _step_seconds(step: dict[str, Any]) -> float:
    """Return a step's total seconds, a repeat block's reps included."""
    if step.get("type") == "repeat":
        children = sum(_step_seconds(child) for child in step.get("steps") or [])
        return float(step.get("iterations") or 1) * children
    return float(step.get("duration_seconds") or 0.0)


def _main_step(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the card's main step: its longest step that is not a warm-up."""
    work = [s for s in steps if s.get("type") not in ("warmup", "cooldown")]
    return max(work, key=_step_seconds, default=None)


def _card_band(
    step: dict[str, Any] | None, low_key: str, high_key: str
) -> tuple[int | None, int | None]:
    """Return a step's first band carrying ``high_key`` (bpm or watts)."""
    if step is None:
        return None, None
    targets = (
        step.get("target"),
        step.get("secondary_target"),
        *(step.get("additional_targets") or []),
    )
    for target in targets:
        if isinstance(target, dict) and target.get(high_key) is not None:
            return target.get(low_key), int(target[high_key])
    return None, None


def _card_lines(steps: list[dict[str, Any]], prefix: str = "") -> list[str]:
    """Render the card one line per step, in the coach's own band wording."""
    from endury.coach.tools.prescription_summary import summarize_prescribed_steps

    lines: list[str] = []
    for step in steps:
        if step.get("type") == "repeat" and step.get("steps"):
            reps = int(step.get("iterations") or 1)
            lines.extend(_card_lines(step["steps"], f"{reps}x "))
            continue
        word, _, rest = summarize_prescribed_steps([step])[0].partition(" ")
        metres = float(step.get("duration_meters") or 0.0)
        seconds = float(step.get("duration_seconds") or 0.0)
        if not seconds and metres:
            rest = f"{metres:g} m" + rest.removeprefix("0 min")
        elif 0 < seconds < 60:
            # A wall rest reads as seconds, not "0.25 min".
            rest = f"{seconds:g} s" + rest.partition(" min")[2]
        words = _REP_WORDS if prefix else _STEP_WORDS
        lines.append(f"{prefix}{words.get(word, word.title())} {rest}")
    return lines


def _cap(
    best: dict[int, IntervalsActivity],
    field_name: str,
    minutes: float | None,
    buckets: tuple[int, ...],
) -> tuple[int | None, bool]:
    """Return the proven value for the smallest bucket that covers ``minutes``.

    Returns:
        The value (None when no bucket has a winner) and whether it fell
        back to a shorter bucket than the session needs.
    """
    if not best:
        return None, False
    wanted = next((b for b in buckets if minutes is not None and b >= minutes), None)
    if wanted is not None and wanted in best:
        return int(getattr(best[wanted], field_name)), False
    longest = max(best)
    return int(getattr(best[longest], field_name)), wanted is not None


def _band_keys(prefix: str, low: int | None, high: int | None) -> dict[str, int]:
    """Return ``{prefix_min, prefix_max}`` for the bounds that are known."""
    keys: dict[str, int] = {}
    if low is not None:
        keys[f"{prefix}_min"] = low
    if high is not None:
        keys[f"{prefix}_max"] = high
    return keys


def _first_then(
    first: int | None,
    then: tuple[int, int] | None,
    band: tuple[int | None, int | None],
) -> tuple[int | None, tuple[int | None, int | None] | None]:
    """Clip the proven first-hour ceiling and the "then" band to the card's band.

    The card is the session the plan sized: when the athlete's proven band
    and the card's do not overlap, the card's band stands.
    """
    low_b, high_b = band
    ceiling = first if high_b is None else min(first, high_b) if first else high_b
    if then is None:
        return ceiling, (band if high_b is not None else None)
    low = then[0] if low_b is None else max(then[0], low_b)
    high = then[1] if high_b is None else min(then[1], high_b)
    return ceiling, ((low, high) if low <= high else band)


def _session_anchors(
    subject: _Subject,
    hr_cap: int | None,
    w_cap: int | None,
    main: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the first-hour and "then" anchors for a long steady session."""
    anchors: list[dict[str, Any]] = [{"label": "First hour"}, {"label": "Then"}]
    hr_first, hr_then = _first_then(
        hr_cap - 5 if hr_cap else None,
        (hr_cap - 6, hr_cap) if hr_cap else None,
        _card_band(main, "low_bpm", "high_bpm"),
    )
    if subject.sport is SportType.CYCLING:
        # Watts move further than beats: the same margins, as 5 % and 6 %.
        w_first, w_then = _first_then(
            round(w_cap * 0.95) if w_cap else None,
            (round(w_cap * 0.94), w_cap) if w_cap else None,
            _card_band(main, "low_watts", "high_watts"),
        )
        anchors[0].update(_band_keys("w", None, w_first))
        anchors[1].update(_band_keys("w", *(w_then or (None, None))))
    anchors[0].update(_band_keys("hr", None, hr_first))
    anchors[1].update(_band_keys("hr", *(hr_then or (None, None))))
    if subject.hilly and hr_cap and subject.sport is SportType.RUNNING:
        anchors.append(
            {"label": "Hike anything that pushes you over", "hr_max": hr_cap + 2}
        )
    return [anchor for anchor in anchors if len(anchor) > 1]


def _race_anchors(hr_cap: int | None) -> list[dict[str, Any]]:
    """Return the race's anchors by thirds, the last one open."""
    if hr_cap is None:
        return []
    return [
        {"label": "First third", "hr_max": hr_cap - 5},
        {"label": "Middle third", "hr_min": hr_cap - 4, "hr_max": hr_cap + 2},
        {"label": "Last third", "open": True},
    ]


def _effort_noun(sport: SportType | None) -> str:
    """Return the athlete's word for one effort of a sport."""
    return {SportType.CYCLING: "ride", SportType.SWIMMING: "swim"}.get(
        sport or SportType.RUNNING, "run"
    )


def _is_interval(main: dict[str, Any] | None) -> bool:
    """Whether the card's main step is a block of repeats."""
    return main is not None and main.get("type") == "repeat"


def _steady(subject: _Subject) -> bool:
    """Whether the subject is a race or a long steady session.

    Anything else, a short session or a block of repeats, is briefed by its
    card: the hours the athlete has held, the routes and the kit are for
    the long day, not for two twenties at sweet spot.
    """
    return subject.kind == "race" or (
        (subject.duration_min or 0) >= _ANCHOR_MIN_MINUTES
        and not _is_interval(_main_step(subject.steps))
    )


def _hold(
    subject: _Subject,
    activities: list[IntervalsActivity],
    client: Any,
    thin: list[str],
) -> dict[str, Any]:
    """Return the ``hold`` block: anchors, the card, proven efforts, climb, power."""
    sport = subject.sport
    steady = _steady(subject)
    proven = hold_curve(activities, sport) if steady and sport is not None else []
    main = _main_step(subject.steps)
    best = best_by_bucket(activities, sport, "average_heartrate") if sport else {}
    hr_cap, short = _cap(best, "average_heartrate", subject.duration_min, HOLD_BUCKETS)
    power = None
    w_cap = None
    if sport is SportType.CYCLING:
        power = {**power_curve(activities), "ftp": _ftp(client)}
        watts = best_by_bucket(
            activities, sport, "icu_weighted_avg_watts", POWER_BUCKETS
        )
        w_cap, _ = _cap(
            watts, "icu_weighted_avg_watts", subject.duration_min, POWER_BUCKETS
        )
    anchored = sport in (SportType.RUNNING, SportType.CYCLING) and steady
    anchors: list[dict[str, Any]] = []
    if anchored:
        anchors = (
            _race_anchors(hr_cap)
            if subject.kind == "race"
            else _session_anchors(subject, hr_cap, w_cap, main)
        )
        _hold_thin(subject, hr_cap, short, thin)
    hilly_run = subject.hilly and sport is SportType.RUNNING
    climb = climb_rate_m_per_h(activities) if hilly_run else None
    if hilly_run and climb is None:
        thin.append(
            "Fewer than three long hilly runs in the last twelve months, so there "
            "is no climbing rate yet."
        )
    return {
        "anchors": anchors,
        "card": _card_lines(subject.steps),
        "proven": proven,
        "climb_rate_m_per_h": climb,
        "power": power,
    }


def _hold_thin(
    subject: _Subject, hr_cap: int | None, short: bool, thin: list[str]
) -> None:
    """Say what the hold line could not rest on."""
    noun = _effort_noun(subject.sport)
    if hr_cap is None:
        rest = (
            "has no hold line" if subject.kind == "race" else "follows the card alone"
        )
        thin.append(
            f"No continuous {noun} of an hour or more with heart rate in the last "
            f"twelve months, so the brief {rest}."
        )
    elif short:
        wanted = next(b for b in HOLD_BUCKETS if b >= (subject.duration_min or 0))
        thin.append(
            f"No {noun} of {_HOURS[wanted]} or more in the last twelve months, so "
            "the hold line rests on shorter efforts."
        )


def _ftp(client: Any) -> int | None:
    """Return the athlete's cycling FTP from their intervals.icu settings."""
    for row in client.get_athlete().sport_settings or []:
        for type_name in row.types or []:
            if type_name in LOAD_ONLY_ACTIVITY_TYPES:
                continue
            if SPORT_TYPE_MAP.get(type_name) is SportType.CYCLING and row.ftp:
                return int(row.ftp)
    return None


# -- Estimate, fuel, conditions, routes ------------------------------------


def _estimate(
    subject: _Subject,
    activities: list[IntervalsActivity],
    today: date,
    thin: list[str],
) -> dict[str, Any] | None:
    """Return a race's finish-time range, or None with a ``thin`` line."""
    if subject.kind != "race":
        return None
    if subject.sport is not SportType.RUNNING:
        thin.append(
            "The finish-time range covers run and trail races; for a triathlon, "
            "race to your plan's cards."
        )
        return None
    if subject.distance_km is None:
        thin.append("The race has no distance yet, so there is no finish-time range.")
        return None
    estimate = trail_estimate(
        activities, subject.distance_km, float(subject.climb_m or 0), today=today
    )
    if estimate is None:
        thin.append(
            "Fewer than two hilly runs of 90 minutes or more in the last four "
            "months, so there is no finish-time range yet."
        )
    return estimate


def _fuel(
    subject: _Subject,
    wellness: list[WellnessDay],
    temps: list[int] | None,
    thin: list[str],
) -> dict[str, Any] | None:
    """Return the ``fuel`` block, or None when the length is unknown.

    A race with no finish-time range of its own is fuelled for the typical
    length of its goal, and ``thin`` says which figure that was.
    """
    minutes = subject.duration_min
    if minutes is None and subject.kind == "race":
        minutes = typical_race_minutes(subject.goal_type)
        if minutes is not None:
            thin.append(
                f"The fuel plan assumes about {_hours_words(minutes)} for this "
                "race; your own time may differ."
            )
    if minutes is None:
        return None
    return fuel_plan(
        minutes,
        weight_kg=_weight(wellness),
        is_race=subject.kind == "race",
        temp_c=temps[1] if temps else None,
    )


def _conditions(
    subject: _Subject, temps: list[int] | None, thin: list[str]
) -> dict[str, Any] | None:
    """Return the ``conditions`` block; a swim or a card-only session has none."""
    if subject.sport is SportType.SWIMMING or not _steady(subject):
        return None
    if temps is None:
        thin.append(
            "No recent outdoor session carries a temperature, so there is no kit "
            "line yet."
        )
        return {"recent_temp_c": None, "kit": []}
    kit = kit_lines(subject.sport, temps[0]) if subject.sport else []
    return {"recent_temp_c": temps, "kit": kit}


def _routes(
    subject: _Subject, activities: list[IntervalsActivity], today: date
) -> list[dict[str, Any]]:
    """Return the routes that fit a long run or ride session; a race has none."""
    if (
        subject.kind != "session"
        or subject.sport not in (SportType.RUNNING, SportType.CYCLING)
        or not subject.duration_min
        or not _steady(subject)
    ):
        return []
    return routes_like(activities, subject.sport, subject.duration_min, today=today)


# -- What success looks like -----------------------------------------------


def _rubric(
    subject: _Subject, hold: dict[str, Any], fuel: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Return up to four checks the debrief grades the session against."""
    items: list[dict[str, Any]] = []
    first = hold["anchors"][0] if hold["anchors"] else None
    if first is not None and "hr_max" in first:
        items.append(
            {
                "label": f"{first['label']} under {first['hr_max']} bpm",
                "metric": "hr_first_third_max",
                "value": first["hr_max"],
            }
        )
    if fuel is not None and fuel["carbs_g_per_h"]:
        low, high = fuel["carbs_g_per_h"]
        items.append(
            {
                "label": f"{span(low, high)} g of carbs an hour",
                "metric": "carbs_g_per_h",
                "value": [low, high],
            }
        )
    if subject.kind == "session" and _is_interval(_main_step(subject.steps)):
        items.append(
            {
                "label": "Last rep within 5 % of the first",
                "metric": "rep_fade_pct",
                "value": 5,
            }
        )
    elif subject.kind == "race" or (subject.duration_min or 0) >= _FINISH_FEEL_MINUTES:
        label = (
            "Finish with the last third your strongest"
            if subject.kind == "race"
            else "Finish feeling you had more in you"
        )
        items.append({"label": label, "metric": "finish_feel", "value": "in hand"})
    return items[:4]


__all__ = ["build_brief"]
