"""Deterministic "why this session" composition for materialized plans.

The athlete-facing counterpart to the session brief: where the brief says
what to do, these paragraphs say why the session exists, where it sits in
its progression, and what the surrounding week is for. Everything here is
a fixed template plus computed numbers — no free composition, no LLM.

Three paragraphs, in order, after the note of an optional session
(``PlacedSession.optional_reason``, R-SESS-037) when it has one:

1. the authored ``Rationale.athlete_why`` of the menu slot/variant that
   produced the session (family fallback when unauthored);
2. the session's position in its progression block, when the block has
   more than one session;
3. the phase context of the owning week.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta

from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    PhaseType,
    PlacedSession,
    SessionOrigin,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.pipeline import PlanResult
from endury.planner.sessions.menus import BLUEPRINTS
from endury.planner.sessions.menus.types import PlanBlueprint, Variant

#: Per-family fallback "why" — the sentence a session inherits when its
#: menu slot carries no authored ``athlete_why`` (and the whole answer for
#: injected fitness tests and non-menu sessions).
FAMILY_WHY: dict[ActivityFamily, str] = {
    ActivityFamily.EASY: (
        "Aerobic maintenance — conversational effort, keep it genuinely easy."
    ),
    ActivityFamily.LONG: (
        "The week's endurance cornerstone — builds durability and fuel economy."
    ),
    ActivityFamily.RECOVERY: (
        "Active recovery — promote blood flow without adding training stress."
    ),
    ActivityFamily.TEMPO: (
        "Sustained steady-state work — raises the pace you can hold for hours."
    ),
    ActivityFamily.THRESHOLD: (
        "Threshold development — extends time near your sustainable red line."
    ),
    ActivityFamily.INTERVAL: (
        "High-intensity intervals — sharpens top-end fitness and economy."
    ),
    ActivityFamily.TEST: (
        "Fitness test — pace it honestly so your zones stay calibrated."
    ),
}

#: Paragraph 1 for a hard run or bike slot the generator eased in the days
#: after a tune-up race (``session_generator.POST_RACE_EASY_DAYS``): the
#: slot's authored why describes the session it replaced.
EASED_AFTER_RACE_WHY = (
    "Easy instead of the usual hard session: the race is still in your "
    "legs, and the quality work waits until they have recovered."
)

#: Week modes that resolve to the ``"recovery"`` menu (mirrors
#: ``session_generator._RECOVERY_MODES``).
_RECOVERY_MODES = frozenset({WeekMode.RECOVERY, WeekMode.RACE_RECOVERY})

#: Minimum first→last spread before a progression paragraph claims the
#: block is building: minutes for time-based sports, metres for swimming.
_VARY_MINUTES = 5.0
_VARY_METERS = 200.0


def compose_why_details(
    result: PlanResult,
    materialized: Mapping[str, tuple[float, float]],
) -> dict[str, list[str]]:
    """Compose the "why this session" paragraphs for every assigned session.

    Args:
        result: Pipeline output (phase plan, sized sessions, assignments).
        materialized: ``slot_id -> (duration_minutes, total_meters)`` from
            the workouts already built for those sessions.

    Returns:
        ``slot_id -> paragraphs`` (plain text, at most four entries).
    """
    ensure_registry_loaded()
    by_slot = {session.slot_id: session for session in result.sessions}
    weeks = result.phase_plan.weeks
    assignments = sorted(result.assignments, key=lambda a: (a.date, a.slot_id))
    if not assignments:
        return {}
    first = assignments[0]
    week_zero_monday = first.date - timedelta(
        days=7 * by_slot[first.slot_id].week + first.date.weekday()
    )

    matched: dict[str, Variant | None] = {}
    whys: dict[str, str] = {}
    for assignment in assignments:
        placed = by_slot[assignment.slot_id]
        variant, why = _authored_why(
            placed, assignment.activity_key, weeks[placed.week]
        )
        matched[assignment.slot_id] = variant
        whys[assignment.slot_id] = (
            why or FAMILY_WHY[get_activity(assignment.activity_key).family]
        )

    positions = _progression_positions(
        [(a.slot_id, by_slot[a.slot_id], matched[a.slot_id]) for a in assignments],
        materialized,
    )

    details: dict[str, list[str]] = {}
    for assignment in assignments:
        placed = by_slot[assignment.slot_id]
        paragraphs = [whys[assignment.slot_id]]
        if placed.optional_reason is not None:
            paragraphs.insert(0, placed.optional_reason)
        position = positions.get(assignment.slot_id)
        if position is not None:
            paragraphs.append(position)
        paragraphs.append(_phase_paragraph(weeks, placed.week, week_zero_monday))
        details[assignment.slot_id] = paragraphs
    return details


# ---------------------------------------------------------------------------
# Paragraph 1 — the authored why
# ---------------------------------------------------------------------------


def _mode_key(blueprint: PlanBlueprint, week_spec: WeekSpec) -> str:
    """Return the menu ``mode_key`` for a week (mirrors session_generator)."""
    if week_spec.mode is WeekMode.RACE:
        if (week_spec.phase, "race") in blueprint.phases:
            return "race"
        return "recovery"
    if week_spec.mode in _RECOVERY_MODES:
        return "recovery"
    if week_spec.mode is WeekMode.TAPER:
        return "taper"
    return "load"


def _authored_why(
    placed: PlacedSession,
    activity_key: ActivityKey,
    week_spec: WeekSpec,
) -> tuple[Variant | None, str]:
    """Look up the menu variant that produced a session and its authored why.

    Variant rationale wins over slot rationale, taking the first non-empty
    ``athlete_why`` of the two. Fitness tests and non-menu sessions carry
    no slot provenance and resolve to ``(None, "")``; a slot whose variants
    do not list the emitted key was eased after a tune-up race and resolves
    to ``(None, EASED_AFTER_RACE_WHY)``.

    Args:
        placed: The placed session (slot provenance, origin).
        activity_key: The emitted activity key (post-rotation).
        week_spec: The owning week's skeleton row.

    Returns:
        ``(variant, athlete_why)`` — either may be ``None``/empty.
    """
    blueprint = BLUEPRINTS.get(week_spec.effective_goal)
    if (
        blueprint is None
        or placed.slot_name is None
        or placed.origin is SessionOrigin.TEST
    ):
        return None, ""
    menu = blueprint.phases.get((week_spec.phase, _mode_key(blueprint, week_spec)))
    if menu is None:
        return None, ""
    slot = next((s for s in menu.slots if s.name == placed.slot_name), None)
    if slot is None:
        return None, ""
    variant = next(
        (
            v
            for v in slot.variants
            if v.activity_key is activity_key or activity_key in v.rotation
        ),
        None,
    )
    if variant is None:
        return None, EASED_AFTER_RACE_WHY
    candidates = [variant.rationale, slot.rationale]
    whys = [c.athlete_why for c in candidates if c is not None and c.athlete_why]
    return variant, whys[0] if whys else ""


# ---------------------------------------------------------------------------
# Paragraph 2 — position within the progression block
# ---------------------------------------------------------------------------


def _progression_positions(
    entries: list[tuple[str, PlacedSession, Variant | None]],
    materialized: Mapping[str, tuple[float, float]],
) -> dict[str, str]:
    """Build the per-slot progression sentence for every multi-session block.

    Args:
        entries: ``(slot_id, placed, variant)`` in calendar order.
        materialized: ``slot_id -> (duration_minutes, total_meters)``.

    Returns:
        ``slot_id -> sentence`` for slots whose block has two or more
        sessions; blocks of one are omitted.
    """
    groups: dict[tuple, list[tuple[str, PlacedSession]]] = {}
    for slot_id, placed, variant in entries:
        if variant is not None and variant.progression is not None:
            key: tuple = (placed.sport, variant.progression.name)
        else:
            key = (placed.sport, placed.slot_name, placed.activity_key)
        groups.setdefault(key, []).append((slot_id, placed))

    sentences: dict[str, str] = {}
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda m: m[1].week)
        total = len(ordered)
        swim = ordered[0][1].sport is SportType.SWIMMING
        clause = _building_clause(
            materialized.get(ordered[0][0], (0.0, 0.0)),
            materialized.get(ordered[-1][0], (0.0, 0.0)),
            swim=swim,
        )
        for index, (slot_id, _placed) in enumerate(ordered, start=1):
            sentences[slot_id] = f"Session {index} of {total} in this block{clause}."
    return sentences


def _building_clause(
    first: tuple[float, float],
    last: tuple[float, float],
    *,
    swim: bool,
) -> str:
    """Return the " — building X → Y across it" clause, or "" when flat.

    Args:
        first: ``(minutes, meters)`` of the block's first session.
        last: ``(minutes, meters)`` of the block's last session.
        swim: Whether the block is a swim block (metres, not minutes).

    Returns:
        The clause, or an empty string when the trajectory barely moves.
    """
    start, end = (first[1], last[1]) if swim else (first[0], last[0])
    threshold = _VARY_METERS if swim else _VARY_MINUTES
    if abs(end - start) < threshold:
        return ""
    unit = "m" if swim else "min"
    return f" — building {round(start)} → {round(end)} {unit} across it"


# ---------------------------------------------------------------------------
# Paragraph 3 — the phase context of the owning week
# ---------------------------------------------------------------------------


def _phase_paragraph(
    weeks: list[WeekSpec],
    week_index: int,
    week_zero_monday: date,
) -> str:
    """Return the fixed phase sentence for the week a session sits in.

    Args:
        weeks: The plan's week skeleton.
        week_index: 0-indexed week of the session.
        week_zero_monday: Calendar Monday of plan week 0.

    Returns:
        One sentence (two for a load week that has a recovery week ahead).
    """
    week_spec = weeks[week_index]
    number = f"{week_index + 1} of {len(weeks)}"
    if week_spec.phase is PhaseType.MAINTAIN:
        return f"Maintenance week {number} — holding the fitness you have."
    if week_spec.mode is WeekMode.RACE:
        return (
            f"Race week {number} — everything here serves race day: stay "
            "sharp, stay fresh, take no risks."
        )
    if week_spec.mode is WeekMode.RACE_RECOVERY:
        return (
            f"Recovery week {number}, straight after a race — let the damage "
            "repair before the next block of work."
        )
    if week_spec.mode is WeekMode.TAPER:
        return (
            f"Taper week {number} — volume comes down while sharpness stays; "
            "the fitness is already banked."
        )
    if week_spec.mode is WeekMode.RECOVERY:
        return (
            f"Recovery week {number} — absorb the last block; hold back even "
            "if the legs feel good."
        )
    next_recovery = next(
        (
            index
            for index in range(week_index + 1, len(weeks))
            if weeks[index].mode in _RECOVERY_MODES
        ),
        None,
    )
    if next_recovery is None:
        return f"Build week {number}."
    starts = week_zero_monday + timedelta(days=7 * next_recovery)
    return f"Build week {number}. Next recovery week starts {starts:%b} {starts.day}."


__all__ = ["FAMILY_WHY", "compose_why_details"]
