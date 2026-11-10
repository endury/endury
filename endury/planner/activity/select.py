"""Resolve a plain-language workout intent to one registry activity.

Shared by the two verbs that let the athlete ask for "a threshold run"
instead of naming an activity key — the ad-hoc workout builder and the
planned-session swap — so the same words always land on the same
canonical activity.
"""

from __future__ import annotations

from endury.planner.activity.capabilities import ActivityStimulusTag
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import query_activities
from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.foundations.types import ActivityFamily, SportType

#: Intent names resolved via ``ActivityFamily`` (a single, unambiguous
#: family per intent). "endurance" is a synonym for "easy" — the steady
#: aerobic session literally named "Endurance ..." in every sport
#: (bike_endurance, swim_endurance) — NOT for "long", the sport's key long
#: session (run_long / bike_long / swim_long).
_FAMILY_INTENTS: dict[str, ActivityFamily] = {
    "threshold": ActivityFamily.THRESHOLD,
    "long": ActivityFamily.LONG,
    "easy": ActivityFamily.EASY,
    "endurance": ActivityFamily.EASY,
    "recovery": ActivityFamily.RECOVERY,
    # The sport's fitness test (threshold run, FTP ride, CSS swim): the
    # athlete asks for "a threshold test", which "threshold" would build as
    # intervals.
    "test": ActivityFamily.TEST,
}

#: Intent names resolved via an ``ActivityStimulusTag`` (family alone is too
#: coarse — e.g. VO2max work spans several families).
_STIMULUS_INTENTS: dict[str, ActivityStimulusTag] = {
    "vo2": ActivityStimulusTag.VO2,
}

_BRICK_INTENT = "brick"

#: Continuous race-pace/tempo run: warmup + ONE sub-threshold main block +
#: cooldown. Resolved as its own special case (family=TEMPO plus the
#: RACE_SPECIFIC tag, minus brick variants) rather than through
#: ``_STIMULUS_INTENTS`` — a bare RACE_SPECIFIC tag query would also catch
#: rep-based race-pace intervals and brick runs, which aren't this shape.
_RACE_PACE_INTENT = "race_pace"

#: Easy session with short neuromuscular touches (relaxed strides / an
#: opener burst), not a family or stimulus-tag intent on its own — family
#: EASY plus the NEUROMUSCULAR tag together pick it out (e.g. run_strides,
#: bike_opener) without pulling in the VO2/interval sprint work that also
#: carries NEUROMUSCULAR.
_STRIDES_INTENT = "strides"

#: Sweet spot, just under FTP: the tempo-family session that also carries
#: the threshold stimulus (bike_sweet_spot), not the plain tempo ride or the
#: threshold intervals either side of it.
_SWEET_SPOT_INTENT = "sweet_spot"

#: A run on trails, steered by heart rate: the easy trail run up to about
#: 75 minutes, the long run over hilly ground beyond (picked by length) —
#: not the road runs the plain "easy" and "long" intents give.
_TRAIL_INTENT = "trail"

#: How far a built session may miss the length asked for and still count as
#: building to it: 10 % of the request, never under 5 minutes.
_LENGTH_TOLERANCE = 0.10
_LENGTH_TOLERANCE_FLOOR_MIN = 5.0

#: Every intent word the callers accept, sorted for stable error options.
VALID_INTENTS: list[str] = sorted(
    {
        *_FAMILY_INTENTS,
        *_STIMULUS_INTENTS,
        _BRICK_INTENT,
        _RACE_PACE_INTENT,
        _STRIDES_INTENT,
        _SWEET_SPOT_INTENT,
        _TRAIL_INTENT,
    }
)

#: Tags marking a "specialty" variant (race rehearsal / progression) of an
#: otherwise generic family or stimulus match. Excluded from the default
#: pick so a plain "give me a threshold run" request doesn't land on, say,
#: a marathon-pace long run just because it also carries a matching tag.
_SPECIALTY_EXCLUDE_TAGS = frozenset(
    {
        ActivityStimulusTag.RACE_SPECIFIC,
        ActivityStimulusTag.PROGRESSION,
        ActivityStimulusTag.MIXED,
    }
)


def _stimulus_tags(activity: ActivityDefinition) -> set[ActivityStimulusTag]:
    """Return an activity's stimulus tags, treating a missing profile as empty.

    ``ActivityDefinition.capability_profile`` is optional at the type level
    but always populated by ``__post_init__``; this keeps the callers below
    honest about that without asserting on registry internals.

    Args:
        activity: The activity to read tags from.

    Returns:
        The activity's stimulus tags, or an empty set if it has no profile.
    """
    profile = activity.capability_profile
    return set(profile.stimulus_tags) if profile is not None else set()


def _query_intent_candidates(
    sport: SportType, intent_lower: str
) -> list[ActivityDefinition]:
    """Return registry activities matching *sport* and *intent_lower*.

    Args:
        sport: Sport to restrict the search to.
        intent_lower: Lower-cased, already-validated intent name.

    Returns:
        Matching activity definitions (possibly empty).
    """
    if intent_lower == _BRICK_INTENT:
        brick_tags = {
            ActivityStimulusTag.BRICK_SUPPORT,
            ActivityStimulusTag.RACE_SPECIFIC,
        }
        return [
            a for a in query_activities(sport=sport) if brick_tags <= _stimulus_tags(a)
        ]
    if intent_lower == _RACE_PACE_INTENT:
        return [
            a
            for a in query_activities(sport=sport, family=ActivityFamily.TEMPO)
            if ActivityStimulusTag.RACE_SPECIFIC in _stimulus_tags(a)
            and ActivityStimulusTag.BRICK_SUPPORT not in _stimulus_tags(a)
        ]
    if intent_lower == _STRIDES_INTENT:
        return [
            a
            for a in query_activities(sport=sport, family=ActivityFamily.EASY)
            if ActivityStimulusTag.NEUROMUSCULAR in _stimulus_tags(a)
        ]
    if intent_lower == _SWEET_SPOT_INTENT:
        return [
            a
            for a in query_activities(sport=sport, family=ActivityFamily.TEMPO)
            if ActivityStimulusTag.THRESHOLD in _stimulus_tags(a)
        ]
    if intent_lower == _TRAIL_INTENT:
        return [
            a
            for family in (ActivityFamily.EASY, ActivityFamily.LONG)
            for a in query_activities(sport=sport, family=family)
            if ActivityStimulusTag.HILL in _stimulus_tags(a)
        ]
    if intent_lower in _FAMILY_INTENTS:
        return query_activities(sport=sport, family=_FAMILY_INTENTS[intent_lower])
    stimulus_tag = _STIMULUS_INTENTS[intent_lower]
    return query_activities(sport=sport, stimulus_tags={stimulus_tag})


def _length_miss(activity: ActivityDefinition, minutes: float) -> float:
    """Return how many minutes *activity* built for *minutes* misses them by."""
    target_tss = assignment_target_tss(activity, minutes / 60.0, None)
    return abs(activity.duration_minutes_exact(target_tss) - minutes)


def _pick_activity(
    candidates: list[ActivityDefinition], minutes: float | None
) -> ActivityDefinition | None:
    """Pick the single canonical activity from *candidates*.

    Drops race-rehearsal, progression and mixed-intensity (over-under)
    specialty variants when a plainer
    match exists (falling back to the full candidate set if that would
    empty the pool). With a length asked for, keeps the activities that
    build to it, or the one that comes closest when none does. Then breaks
    ties by cheapest ``tss_min``, preferring an aerobic-tagged activity, and
    finally by key name for determinism.

    Args:
        candidates: Activities matching the requested sport/intent.
        minutes: The session length asked for, or None.

    Returns:
        The chosen activity, or ``None`` if *candidates* is empty.
    """
    if not candidates:
        return None
    filtered = [
        a for a in candidates if not (_stimulus_tags(a) & _SPECIALTY_EXCLUDE_TAGS)
    ]
    pool = filtered or candidates
    if minutes is not None:
        tolerance = max(_LENGTH_TOLERANCE * minutes, _LENGTH_TOLERANCE_FLOOR_MIN)
        misses = {a.key: _length_miss(a, minutes) for a in pool}
        pool = [a for a in pool if misses[a.key] <= tolerance] or [
            min(pool, key=lambda a: misses[a.key])
        ]

    def _sort_key(a: ActivityDefinition) -> tuple[float, int, str]:
        is_aerobic = ActivityStimulusTag.AEROBIC in _stimulus_tags(a)
        return (a.tss_min, 0 if is_aerobic else 1, a.key.value)

    return min(
        pool,
        key=_sort_key,
    )


def pick_for_intent(
    sport: SportType, intent_lower: str, minutes: float | None = None
) -> ActivityDefinition | None:
    """Resolve one sport + intent word to the canonical registry activity.

    Args:
        sport: Sport to restrict the search to.
        intent_lower: Lower-cased intent name, one of :data:`VALID_INTENTS`.
        minutes: The session length asked for, or None. A 75-min VO2 ride
            is then the 4-min reps, not the 30/15 sets that end at an hour.

    Returns:
        The chosen activity, or ``None`` when the sport has none for that
        intent.
    """
    return _pick_activity(_query_intent_candidates(sport, intent_lower), minutes)


__all__ = ["VALID_INTENTS", "pick_for_intent"]
