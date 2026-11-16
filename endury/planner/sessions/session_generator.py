"""Session menu generator for the planner session-menu architecture.

Prescribes every training session in every week, emitted as a
``PlacedSession`` with ``(min_hours, target_hours, max_hours)`` bounds
that the L3 QP will optimise within.

The generator reads the per-goal menu blueprints in ``sessions/menus/``
(via ``get_session_menu``, keyed by goal/phase/mode/ambition) and applies:

* Phase-fraction filtering (sub-phase transitions)
* Progressive duration logic (long sessions grow from athlete's current)
* Capacity ceiling capping

Fitness-test sessions are injected between L2 and L3 by
``sessions/test_injection.py``.
"""

from __future__ import annotations

import math
from collections import defaultdict

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.config import DEFAULT_SWIM_CSS_SEC_PER_100M
from endury.planner.activity.materialize import (
    assignment_target_tss,
    materialized_swim_meters,
)
from endury.planner.activity.shapes.aquatic import SwimLinearDistanceActivity
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    CapacityProjection,
    GoalType,
    PhasePlan,
    PhaseType,
    PlacedSession,
    RaceGoal,
    SessionOrigin,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.sessions.menus import (
    MAINTENANCE_MENUS,
    SessionSpec,
    get_session_menu,
)
from endury.planner.sessions.menus.patterns import swim_floor_metres
from endury.planner.sessions.menus.resolve import active_specs
from endury.training.load import LoadMetricContext

# Progressive duration-step constants.
MAX_DURATION_STEP_FRACTION: float = 0.10  # max step as fraction of previous
PROGRESSIVE_INCREMENT_MIN: int = 10  # default per-phase-week duration increase

#: Activity keys that represent fixed-protocol fitness tests.
_TEST_KEYS: frozenset[ActivityKey] = frozenset(
    {
        ActivityKey.RUN_THRESHOLD_TEST,
        ActivityKey.BIKE_FTP_TEST,
        ActivityKey.SWIM_CSS_TEST,
    }
)

#: A session whose capacity-scaled prescription lands below this
#: fraction of the smallest workout its activity can build is dropped
#: rather than emitted — the card would render substantially larger
#: than the plan sized it.
FLOOR_DROP_TOLERANCE: float = 0.85

#: A session that is neither key nor on a ramp books at least this fraction of
#: its menu minimum. A squeezed week scales every minimum alike; a session the
#: scale takes under this stays whole and optional (R-SESS-037) rather than
#: booking a shrunk version of itself.
SQUEEZE_TOLERANCE: float = 0.95

#: The shortest session that is still the workout its card names, where the
#: card itself can build a shorter one: a steady run or a progression run
#: builds down to 21 minutes, which is a 10-minute warm-up and cool-down
#: around a minute of work. Under 30 minutes it is not the session the
#: athlete was promised, so a squeezed week keeps it whole and optional
#: (R-SESS-037) rather than booking a shell of it.
ACTIVITY_FLOOR_MINUTES: dict[ActivityKey, float] = {
    ActivityKey.RUN_AEROBIC_TEMPO: 30.0,
    ActivityKey.RUN_PROGRESSION: 30.0,
}

#: In a squeezed week with key sessions (R-SESS-033), the other sessions
#: share what the keys leave at one common scale; while that scale is
#: below this, the one that fits the week least drops and the week re-fits
#: (never the last one), so the week keeps fewer easy runs worth doing
#: rather than many snippets.
#: A hypothesis: no source states how short a squeezed easy run may get.
FILLER_MIN_SCALE: float = 0.5

#: A session that eases a progression (``SessionSpec.eases``, R-SESS-034)
#: is never longer than this fraction of the longest that progression has
#: reached, or of the athlete's recent longest before it starts. The top of
#: "the other long runs at 50-75 % of the maximum" (docs/research/
#: trail_running.md §5.3 and §13.5, the long-run frequency rule): a cap,
#: not a target, so a menu bound below it stands.
EASED_FRACTION: float = 0.75

#: A generator sizing entry: (spec, min_h, target_h, max_h, menu index,
#: effective activity key, floor_h, ceiling_h).
_Entry = tuple[SessionSpec, float, float, float, int, ActivityKey, float, float]

#: Cache for :func:`_activity_floor_hours` — floors are static per
#: activity and, for a swim, per CSS its card is probed at.
_FLOOR_HOURS_CACHE: dict[tuple[ActivityKey, float | None], float] = {}


def _floor_tolerance(key: ActivityKey) -> float:
    """How far under its floor a session may book: exact for a floor the table sets.

    A card's own floor is approximate at default thresholds, so a booking
    at ``FLOOR_DROP_TOLERANCE`` of it still renders honestly; a floor from
    :data:`ACTIVITY_FLOOR_MINUTES` is the shortest run worth booking and
    holds exactly (a progression run booked at 26 min, 2026-10-06).
    """
    return 1.0 if key in ACTIVITY_FLOOR_MINUTES else FLOOR_DROP_TOLERANCE


def _shortfall(entry: _Entry, scale: float) -> float:
    """How far (hours) the week's *scale* leaves *entry* under its card's floor."""
    return entry[6] * _floor_tolerance(entry[5]) - max(entry[2], entry[6]) * scale


def _squeezed(entry: _Entry, scale: float, recovery: bool) -> bool:
    """Whether the week's *scale* shrinks *entry* under its menu minimum.

    Only a session that is neither key, on a ramp nor a fixed test, and only
    in a maintenance block's down week (*recovery*) or when the scale is under
    ``FILLER_MIN_SCALE``: elsewhere a mild squeeze is the week fitting its
    hours, and dropping a session costs the week more than the shrink does.
    """
    if _protected(entry) or entry[5] in _TEST_KEYS:
        return False
    if not recovery and scale >= FILLER_MIN_SCALE:
        return False
    return entry[1] * scale < entry[1] * SQUEEZE_TOLERANCE - 1e-9


def _probe_resolver(swim_css: float | None) -> LoadMetricContext | None:
    """Return the resolver a card is probed with: a swimmer's CSS, else none."""
    if swim_css is None:
        return None
    return LoadMetricContext(swim_css_sec_per_100m=swim_css)


def _activity_floor_hours(key: ActivityKey, swim_css: float | None = None) -> float:
    """Return the smallest duration (hours) *key* can materialize as.

    The activity library builds workouts on a feasible lattice — rep
    counts have minimums, warmups/cooldowns are fixed, and test
    protocols never resize — so the smallest buildable workout can be
    substantially longer than a menu's authored lower bound (e.g.
    ``run_fartlek`` bottoms out at 38 min, ``run_threshold_test`` is
    always 55). The floor is probed through the real materialization
    bridge (what the card renders for a one-minute solved session), so
    generator bounds and rendered cards cannot drift apart; a card that
    builds shorter than the session it names is floored by
    :data:`ACTIVITY_FLOOR_MINUTES` instead. A swim's card swims metres, so
    it is probed at the swimmer's CSS: its clock runs at their pace.

    Args:
        key: Activity to probe.
        swim_css: The swimmer's CSS in sec/100m for a swim, else ``None``.

    Returns:
        Floor duration in hours.
    """
    if (key, swim_css) not in _FLOOR_HOURS_CACHE:
        ensure_registry_loaded()
        activity = get_activity(key)
        resolver = _probe_resolver(swim_css)
        floor_min = activity.duration_minutes_exact(
            assignment_target_tss(activity, 1.0 / 60.0, resolver), resolver=resolver
        )
        _FLOOR_HOURS_CACHE[key, swim_css] = (
            max(floor_min, ACTIVITY_FLOOR_MINUTES.get(key, 0.0)) / 60.0
        )
    return _FLOOR_HOURS_CACHE[key, swim_css]


def _spec_floor_hours(spec: SessionSpec, swim_css: float) -> float:
    """Return the floor of *spec*'s activity, a swim's at the swimmer's CSS."""
    css = swim_css if spec.sport == SportType.SWIMMING else None
    return _activity_floor_hours(spec.activity_key, css)


#: Cache for :func:`_activity_ceiling_hours` — same probe philosophy as
#: the floor cache.
_CEILING_HOURS_CACHE: dict[tuple[ActivityKey, float | None], float] = {}


def _activity_ceiling_hours(key: ActivityKey, swim_css: float | None = None) -> float:
    """Return the largest duration (hours) *key* can materialize as.

    The mirror of :func:`_activity_floor_hours`: lattices also top out
    (bounded strides counts, ``tss_max`` on continuous shapes), so a slot
    booked above the ceiling renders a card smaller than the plan sized
    it. Probed as the card rendered for an absurdly large solved
    duration, a swim at the swimmer's CSS.

    Args:
        key: Activity to probe.
        swim_css: The swimmer's CSS in sec/100m for a swim, else ``None``.

    Returns:
        Ceiling duration in hours.
    """
    if (key, swim_css) not in _CEILING_HOURS_CACHE:
        ensure_registry_loaded()
        activity = get_activity(key)
        resolver = _probe_resolver(swim_css)
        ceiling_min = activity.duration_minutes_exact(
            assignment_target_tss(activity, 100.0, resolver), resolver=resolver
        )
        _CEILING_HOURS_CACHE[key, swim_css] = ceiling_min / 60.0
    return _CEILING_HOURS_CACHE[key, swim_css]


# ---------------------------------------------------------------------------
# Progressive floor defaults (minutes)
# ---------------------------------------------------------------------------

_PROGRESSIVE_FLOOR: dict[SportType, int] = {
    SportType.RUNNING: 75,
    SportType.CYCLING: 90,
    SportType.SWIMMING: 30,
}

#: Per-sport absolute floor for the progression step (minutes). The 10%
#: fractional cap is the primary governor; this floor only binds when the
#: previous duration is small — a single global floor would impose an
#: oversized percentage step on shorter sessions (e.g. ~25% on a 60-minute
#: run versus ~12.5% on a 120-minute ride).
_STEP_FLOOR_MIN: dict[SportType, int] = {
    SportType.RUNNING: 8,
    SportType.CYCLING: 12,
    SportType.SWIMMING: 4,
}

# Week modes that map to "recovery" session menus.
_RECOVERY_MODES = frozenset({WeekMode.RECOVERY, WeekMode.RACE_RECOVERY})


#: A run or bike session that is hard physiologically (in the easier week
#: after a long B race, moderate too) and falls within this many days after a
#: B/C race becomes an easy session of the same length (R-SESS-031): the week
#: after a tune-up keeps its volume but the run and bike quality leaves its
#: first days. A fitness test there moves to the same day a week later: on
#: legs still tired from the race it would read low and set every later card's
#: targets low.
POST_RACE_EASY_DAYS: int = 4

#: The easy session an eased run or bike slot becomes.
_POST_RACE_EASY_KEY: dict[SportType, ActivityKey] = {
    SportType.RUNNING: ActivityKey.RUN_EASY,
    SportType.CYCLING: ActivityKey.BIKE_ENDURANCE,
}


#: The note a session carries when its week cannot fit it at its card
#: (R-SESS-037): it stays at its smallest card, optional, instead of
#: vanishing from the plan.
OPTIONAL_SHORT_WEEK_WHY = (
    "Optional this week: your training time this week does not stretch to "
    "this session alongside the others, so it is here at its shortest. Do "
    "it if you have the time."
)

#: The note of a ramp session whose smallest card is still longer than the
#: ramp has reached: booking it would jump past the athlete's build-up.
OPTIONAL_NOT_READY_WHY = (
    "Optional this week: even its shortest version is longer than your "
    "training has built up to so far. Do it only if you feel ready; the "
    "plan grows you toward it."
)

#: The same note for a taper or race week, where the shed protects
#: race-day freshness.
OPTIONAL_TAPER_WHY = (
    "Optional this week: the race is close and the week is already full. "
    "Do it if you feel fresh, or skip it and rest."
)

#: The note of a session the menu authors optional (R-SESS-040): an extra
#: on top of the plan, which never counts on it.
OPTIONAL_MENU_WHY = (
    "Optional: an extra on top of your plan, which does not count on it. Do "
    "it if you have the time and feel like it; skipping it takes nothing away."
)


#: Target lead time for the race-sim week: the big race-rehearsal
#: weekend lands on the PEAK LOAD week whose distance to the race week
#: is nearest this many weeks (docs/research/triathlon_long_course.md,
#: Long Session Benchmarks: the longest sessions should peak 4-6 weeks
#: before race day).
RACE_SIM_TARGET_WEEKS_OUT: int = 5

#: The most a rehearsal session on a ramp may outgrow the least its
#: progression was last booked, or the athlete's recent longest when longer
#: (R-SESS-023): it fires only while its minimum is at most a quarter longer,
#: else the week stays routine. A judgement on the measured steps:
#: rehearsals had jumped up to 114 % past the long run before them. A variant
#: may author its own step (``SessionSpec.max_step``: the
#: Ironman BASIC big ride 0.3).
REHEARSAL_STEP_FRACTION: float = 0.25

#: The most a taper week may book, as a share of the biggest load week the
#: plan booked before it (R-SESS-038): the top of the 50-75 % of peak taper weeks hold
#: at (docs/research/triathlon_long_course.md §7; trail_running.md §11.2,
#: taper weeks at 50-75 % of peak), under the 41-60 % total cut of a taper
#: (docs/research/endurance_methodology.md §6).
TAPER_WEEK_SHARE: float = 0.75

#: The families a taper week shortens; the others hold (volume falls,
#: intensity holds: docs/research/endurance_methodology.md §6).
_TAPER_VOLUME = frozenset(
    {ActivityFamily.EASY, ActivityFamily.RECOVERY, ActivityFamily.LONG}
)

#: The most a race week may book, as a share of the biggest load week the
#: plan booked before it (R-SESS-036): the top of each goal's band.
#: Road running (docs/research/endurance_methodology.md §6, Race Week in Road
#: Running: the taper literature) grows as the race
#: shortens. Triathlon takes the top of 20-40 %
#: (docs/research/triathlon_long_course.md §7: 19-40 % of peak;
#: docs/research/short_course.md §3 and §8: 20-40 % for
#: sprint and olympic). The trail 20K takes the top of its own band, bridged
#: between the 10K's and the marathon's (bench/rulebook.yaml,
#: race_week_volume): a basic athlete's race week is two or three short runs
#: of about 1.4 h, which 0.40 of a 3 h peak week cut to 16- and 19-minute
#: runs. The ultra trail races take the aggressive race-week
#: number, under 20 % (docs/research/trail_running.md §11.2: "≤2 h (<20% of
#: normal)", and a measured 24-h racer at ~18 %).
RACE_WEEK_SHARE: dict[GoalType, float] = defaultdict(
    lambda: 0.40,
    {
        GoalType.RUNNING_5K: 0.65,
        GoalType.RUNNING_10K: 0.60,
        GoalType.RUNNING_HALF_MARATHON: 0.55,
        GoalType.RUNNING_MARATHON: 0.45,
        GoalType.TRAIL_20K: 0.50,
        GoalType.TRAIL_50K: 0.20,
        GoalType.TRAIL_100K: 0.20,
    },
)


def _swim_minutes(
    activity_key: ActivityKey, metres: int, swim_css: float, *, within: bool = False
) -> float:
    """Return the minutes a swim slot of *metres* books.

    Every swim books its card's own wall clock at the swimmer's CSS, so the
    card built from the booked hours swims the authored metres. A
    continuous swim (long/endurance/recovery/pull/kick) inverts its card
    exactly; an interval or drill swim, whose rests and drill pace cover
    less distance per minute, is probed through its card: the shortest card
    covering *metres* for a lower bound, the longest within them for an
    upper one (*within*).

    Args:
        activity_key: The menu slot's activity key.
        metres: The authored distance.
        swim_css: Athlete critical swim speed in sec/100m.
        within: Book the longest card within *metres* instead.

    Returns:
        Minutes the slot books for the distance.
    """
    ensure_registry_loaded()
    activity = get_activity(activity_key)
    if isinstance(activity, SwimLinearDistanceActivity):
        return activity.minutes_for_meters(
            metres, LoadMetricContext(swim_css_sec_per_100m=swim_css)
        )
    return _swim_hours_for_metres(activity_key, metres, swim_css, within=within) * 60.0


def _swim_floor_hours(
    spec: SessionSpec,
    key: ActivityKey,
    mode_key: str,
    ambition: AmbitionLevel,
    swim_css: float,
) -> float:
    """Return the hours *key* needs to cover the tier's swim floor, or 0.0.

    A rotating slot takes the floor of any member that carries one, so the
    week that books the drill still books the swim the others would.

    Args:
        spec: The menu slot.
        key: The activity the week books for the slot.
        mode_key: The menu mode of the week.
        ambition: The athlete's tier.
        swim_css: Athlete critical swim speed in sec/100m.
    """
    metres = max(
        swim_floor_metres(spec.sport, member, mode_key, ambition)
        for member in (key, *spec.rotation)
    )
    if metres == 0:
        return 0.0
    return _swim_hours_for_metres(key, metres, swim_css)


#: Cache for :func:`_swim_hours_for_metres`, by its arguments.
_SWIM_HOURS_CACHE: dict[tuple[ActivityKey, int, float, bool], float] = {}


def _swim_hours_for_metres(
    key: ActivityKey, metres: int, swim_css: float, *, within: bool = False
) -> float:
    """Return the clock of the shortest card covering *metres* at the swimmer's CSS.

    Probed through the card builder, like :func:`_activity_floor_hours`:
    an interval swim's rests cover no distance, so hours at CSS pace alone
    would book a card short of the distance. Capped at the activity's
    ceiling, where the card stops growing. The card's own clock, not the
    bisected edge where the card switches, so a booking that lands a hair
    under it still builds that card. With *within*, the longest card that
    swims no more than *metres* (the shortest card when none does).

    Args:
        key: The swim activity.
        metres: The distance the card must cover.
        swim_css: The swimmer's CSS in sec/100m.
        within: Return the longest card within *metres* instead.
    """
    cache_key = (key, metres, swim_css, within)
    if cache_key not in _SWIM_HOURS_CACHE:
        ensure_registry_loaded()
        activity = get_activity(key)
        resolver = _probe_resolver(swim_css)
        lo, hi = 0.0, _activity_ceiling_hours(key, swim_css)
        for _ in range(30):
            mid = (lo + hi) / 2.0
            covered = materialized_swim_meters(activity, mid, resolver) or 0
            short = covered <= metres if within else covered < metres
            lo, hi = (mid, hi) if short else (lo, mid)
        card_tss = assignment_target_tss(activity, lo if within else hi, resolver)
        _SWIM_HOURS_CACHE[cache_key] = (
            activity.duration_minutes_exact(card_tss, resolver=resolver) / 60.0
        )
    return _SWIM_HOURS_CACHE[cache_key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_session_menu(
    phase_plan: PhasePlan,
    longest_recent_by_family: dict[tuple[SportType, ActivityFamily], int],
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
    capacity: CapacityProjection | None = None,
    swim_css_sec_per_100m: float | None = None,
    weekly_hours_cap: float | None = None,
) -> list[PlacedSession]:
    """Generate a complete session prescription for every week in the plan.

    Each week gets a session menu from the per-goal blueprints in
    ``sessions/menus/`` (via ``get_session_menu``).  Sessions are emitted as
    ``PlacedSession`` objects with ``min_hours`` / ``max_hours`` bounds that
    the L3 QP will optimise within.

    Swim specs encode bounds in metres; they are converted to minutes at
    the wall clock of the card that swims them (see ``_swim_minutes``),
    using *swim_css_sec_per_100m* when provided, else
    ``DEFAULT_SWIM_CSS_SEC_PER_100M``.

    Args:
        phase_plan: L1 output with phase/mode/goal per week.
        longest_recent_by_family: Athlete's current longest session per
            (sport, family) in minutes.
        ambition: Training ambition level (controls duration scaling).
        capacity: Per-week hours ceiling (optional, for capacity capping).
        swim_css_sec_per_100m: Athlete's critical swim speed pace (sec/100m).
            Drives metre→minute conversion for swim menu bounds.  Falls
            back to ``DEFAULT_SWIM_CSS_SEC_PER_100M`` when ``None``.
        weekly_hours_cap: The athlete's soft weekly-hours cap, or ``None``.
            A long session's minimum rises only within it (R-SESS-013).

    Returns:
        List of ``PlacedSession`` for every session in every week.
    """
    a_goal = phase_plan.a_goal
    swim_css = (
        float(swim_css_sec_per_100m)
        if swim_css_sec_per_100m and swim_css_sec_per_100m > 0
        else DEFAULT_SWIM_CSS_SEC_PER_100M
    )

    # Track progressive state across weeks.
    #
    # Every ramp opens from the athlete's longest recent session,
    # collapsed to a per-sport maximum across reported families
    # (R-SESS-012). ``_PROGRESSIVE_FLOOR`` seeds only a sport the athlete
    # reported nothing for: a first long session far past what the
    # athlete has done is the spike that predicts injury, so a measured
    # athlete's ramp opens at their longest even below the menu minimum.
    measured: dict[SportType, float] = {}
    for (sport, _family), dur in longest_recent_by_family.items():
        if dur > 0:
            measured[sport] = max(measured.get(sport, 0.0), float(dur))
    ramp_start: dict[SportType, float] = {
        sport: float(floor) for sport, floor in _PROGRESSIVE_FLOOR.items()
    } | measured
    last_duration: dict[tuple[SportType, str], float] = {}
    # The longest each progression has booked: the yardstick of R-SESS-034.
    longest: dict[tuple[SportType, str], float] = {}
    # The least each progression was last booked (R-SESS-013).
    booked_last: dict[tuple[SportType, str], float] = {}
    # Progressions that have reached their menu minimum at least once.
    in_range: set[tuple[SportType, str]] = set()
    weeks_elapsed: dict[tuple[SportType, str], int] = defaultdict(int)

    # The plan's timeline: the trained weeks a replan cut, then the weeks
    # to generate. Phase runs, race sims and cadences count on it, so a
    # replan resumes the plan instead of reopening it.
    timeline = [*phase_plan.prior_weeks, *phase_plan.weeks]

    # Compute phase runs for fraction calculation.
    phase_runs = _compute_phase_runs(timeline)
    week_to_run: dict[int, tuple[int, int]] = {}
    for run_start, run_length, _phase, _goal in phase_runs:
        for offset in range(run_length):
            week_to_run.setdefault(run_start + offset, (run_start, run_length))

    # Per-PEAK-run race-sim week: race_sim specs fire only here.
    sim_weeks = race_sim_weeks(timeline)

    # The weeks each cadence interval fires on (R-SESS-041), built on
    # first use: a menu names few intervals and most name none.
    cadence_cache: dict[int, frozenset[int]] = {}

    def cadence_fire(specs: list[SessionSpec], week: int) -> frozenset[int]:
        fire: set[int] = set()
        for every in {s.cadence_weeks for s in specs if s.cadence_weeks}:
            if every not in cadence_cache:
                cadence_cache[every] = cadence_weeks(timeline, every)
            if week in cadence_cache[every]:
                fire.add(every)
        return frozenset(fire)

    # Race day of each B/C race, keyed by the week after it (R-SESS-031).
    post_race_day = {
        e.week + 1: e.day_of_week
        for e in phase_plan.race_events
        if e.goal.priority != "A"
    } | {
        w.week + 1: w.owning_goal.race_date.weekday()
        for w in phase_plan.prior_weeks
        if w.owning_goal is not None
    }
    # A run or bike test eased after a race, owed to the same day of the
    # next week, by sport and day (R-SESS-031).
    owed_tests: dict[tuple[SportType, int], ActivityKey] = {}
    # The tests a tune-up's race week or easier week displaced, owed to the
    # same day of the first LOAD week after it outside PEAK and TAPER
    # (R-SESS-042).
    displaced_tests: dict[tuple[SportType, int], ActivityKey] = {}

    result: list[PlacedSession] = []
    # The biggest load week booked so far: the yardstick of R-SESS-036/038.
    peak_load = 0.0
    # The taper week booked last and its longest session per sport: the next
    # one books no more, nor a longer easy or long session (R-SESS-038).
    last_taper = math.inf
    taper_longest: dict[SportType, float] = {}

    for ws in phase_plan.weeks:
        week_idx = ws.week
        owed, owed_tests = owed_tests, {}
        mode = ws.mode
        effective_goal = ws.effective_goal

        # Determine mode key for menu lookup.
        if mode == WeekMode.RACE:
            # Race week: use dedicated race-week menu (locked durations).
            # Fall back to recovery menu for C-races (phase != TAPER).
            mode_key = "race"
            if not get_session_menu(effective_goal, ws.phase, mode_key, ambition):
                mode_key = "recovery"
        elif mode in _RECOVERY_MODES:
            mode_key = "recovery"
        elif mode == WeekMode.TAPER:
            mode_key = "taper"
        else:
            mode_key = "load"

        # A maintenance block's down week keeps its sessions whole (R-SESS-035).
        maintain_recovery = ws.phase == PhaseType.MAINTAIN and mode in _RECOVERY_MODES

        # A tune-up's race week, or the easier week after a long one,
        # displaces the tests the A goal's menu would have drawn; the first
        # LOAD week after it takes them, unless it is a PEAK or TAPER week
        # (R-SESS-042).
        if (mode == WeekMode.RACE and ws.owning_goal is not None) or (
            mode == WeekMode.RACE_RECOVERY
        ):
            displaced_tests |= _displaced_tests(week_idx, phase_runs, a_goal, ambition)
        displaced: dict[tuple[SportType, int], ActivityKey] = {}
        if mode == WeekMode.LOAD:
            if ws.phase not in (PhaseType.PEAK, PhaseType.TAPER):
                displaced = displaced_tests
            displaced_tests = {}

        # Load session menu.
        menu = list(get_session_menu(effective_goal, ws.phase, mode_key, ambition))

        # Cross-category maintenance: if effective_goal differs from
        # a_goal in sport coverage, add maintenance sessions.
        if effective_goal != a_goal.goal_type and a_goal.goal_type in MAINTENANCE_MENUS:
            a_sports = set(ws.sport_split.keys())
            b_sports = {s.sport for s in menu}
            missing = a_sports - b_sports
            if missing:
                menu.extend(
                    m_sess
                    for m_sess in MAINTENANCE_MENUS[a_goal.goal_type]
                    if m_sess.sport in missing
                )

        # Phase fraction for sub-phase filtering.
        run_start, run_length = week_to_run.get(week_idx, (week_idx, 1))
        offset = week_idx - run_start
        phase_frac = offset / max(run_length - 1, 1)
        # A load_weeks window reads the week's place among the run's LOAD
        # weeks instead, so a recovery week cannot move it: load week i of
        # n reads i/n (R-SESS-008).
        run_loads = [
            w.week
            for w in timeline
            if run_start <= w.week < run_start + run_length and w.mode == WeekMode.LOAD
        ]
        load_frac = (
            run_loads.index(week_idx) / len(run_loads)
            if mode == WeekMode.LOAD
            else None
        )

        # Capacity budget for this week (QP will enforce ceiling).
        week_ceiling = capacity.weekly_ceiling.get(week_idx, 99.0) if capacity else 99.0
        budget = week_ceiling * 0.98

        # Frac filter + slot-group resolution (race-sim specs bypass frac
        # geometry: they fire only on the per-PEAK-run race-sim week,
        # where the slot-group resolver picks them over short variants,
        # and only when that week can hold them).
        cadence = cadence_fire(menu, week_idx)
        is_race_sim_week = week_idx in sim_weeks and _holds_race_sim(
            active_specs(menu, phase_frac, True, cadence, load_frac),
            budget,
            ramp_start,
            booked_last,
            swim_css,
        )
        active_menu = active_specs(
            menu, phase_frac, is_race_sim_week, cadence, load_frac
        )

        # First pass: compute raw bounds for all sessions.
        raw_sessions: list[
            tuple[SessionSpec, float, float, float]
        ] = []  # (spec, min_h, target_h, max_h)
        # Per sport, the cap of the week's eased sessions (minutes).
        eased_cap: dict[SportType, float] = {}

        for spec in active_menu:
            if spec.sport == SportType.SWIMMING:
                # Swim menu bounds are card metres, converted at the wall
                # clock of the swim this week books (a rotating slot's
                # member, not its first activity).
                swim_key = (
                    spec.rotation[offset % len(spec.rotation)]
                    if spec.rotation
                    else spec.activity_key
                )
                min_min = _swim_minutes(swim_key, spec.min_meters, swim_css)
                max_min = max(
                    _swim_minutes(swim_key, spec.max_meters, swim_css, within=True),
                    min_min,
                )
            else:
                min_min = float(spec.min_minutes)
                max_min = float(spec.max_minutes)

            # Interpolate nominal duration across the phase. This reads
            # correctly for progressive phases but authors a TAPER run's
            # *first* week at every spec's minimum, since a taper
            # regresses; the taper menus compensate by inflating their
            # step-1 lower bounds, so making the interpolation direction
            # phase-aware would double-count that compensation. A
            # maintenance block holds rather than builds (R-SESS-035):
            # its menus author nothing progressive, so there is no
            # phase-length ramp to interpolate across — every session
            # opens at its authored maximum, and the capacity ceiling
            # (already flat at, or ramping to, the tier volume) is what
            # scales it down for a below-target athlete.
            is_maintain = ws.phase == PhaseType.MAINTAIN and not spec.progressive
            nominal = (
                max_min if is_maintain else min_min + (max_min - min_min) * phase_frac
            )

            # Progressive logic. Share state via ``progression_key`` so the
            # ramp carries across slot-group variants of the same logical
            # long session (e.g. RUN_LONG → RUN_LONG_PROGRESSION).
            prog_key = (
                spec.sport,
                spec.progression_key or spec.activity_key.value,
            )
            if spec.progressive and mode_key == "load":
                current = last_duration.get(prog_key, ramp_start.get(spec.sport, 60.0))
                elapsed = weeks_elapsed[prog_key]
                prog_dur = current + PROGRESSIVE_INCREMENT_MIN * (elapsed + 1)
                prog_dur = min(prog_dur, max_min)
                nominal = max(nominal, prog_dur)
                weeks_elapsed[prog_key] = elapsed + 1

            # Self-constrain the progression step. Non-progressive specs
            # use the floor-based fallback rather than the ramp seed:
            # letting them borrow the ramp's larger current value would
            # remove the step cap from fixed slots during a replan. A
            # maintenance block skips this: the step cap protects a
            # week-over-week ramp, and a maintenance week holds instead
            # (R-SESS-035) — applying it would collapse a big
            # non-progressive session (e.g. the maintenance long run,
            # authored well above the generic per-sport floor below) to
            # that floor every week, forever, since a non-progressive
            # spec never records a ``last_duration`` for the cap to grow
            # from.
            if is_maintain:
                nominal = min(
                    round(nominal / 5.0) * 5.0,
                    max_min if spec.sport == SportType.SWIMMING else math.inf,
                )
            else:
                prev_dur = last_duration.get(prog_key)
                if prev_dur is None:
                    prev_dur = (
                        ramp_start.get(spec.sport, 60.0)
                        if spec.progressive
                        else float(_PROGRESSIVE_FLOOR.get(spec.sport, 60))
                    )
                step_cap = float("inf")
                if nominal > prev_dur:
                    step_cap = prev_dur + max(
                        prev_dur * MAX_DURATION_STEP_FRACTION,
                        _STEP_FLOOR_MIN.get(spec.sport, 10),
                    )
                # Rounding to 5 min never carries a session past its step cap,
                # nor a swim past its authored metres: half a 5-minute step
                # is another 100 m on its card.
                nominal = min(
                    round(nominal / 5.0) * 5.0,
                    step_cap,
                    max_min if spec.sport == SportType.SWIMMING else math.inf,
                )

                if spec.progressive:
                    last_duration[prog_key] = nominal

            # Progressive sessions use `nominal` as the QP upper bound so
            # the coach's week-by-week ramp (not the phase-end maximum)
            # governs actual growth; otherwise the QP would push every
            # progressive session straight to its phase-end ceiling.
            effective_max_min = nominal if spec.progressive else max_min
            # A progressive session ramping up from the athlete's longest sits
            # below the menu minimum until it first gets there, and keeps the
            # step cap as its floor all plan long, so a phase whose menu starts
            # higher does not force a jump; the race-rehearsal weekend alone
            # books its menu minimum, which its step keeps within reach
            # (R-SESS-023).
            effective_min_min = min_min
            if spec.progressive and not spec.race_sim:
                if nominal >= min_min:
                    in_range.add(prog_key)
                else:
                    effective_min_min = nominal
            # An easier week's stand-in for a progression stays under the
            # longest it has reached (R-SESS-034); it reads the ramp, never
            # writes it, so the next load week grows from the last one. The
            # cap rounds down to the 5-minute grid the ramp is sized on.
            if spec.eases is not None:
                yardstick = longest.get(
                    (spec.sport, spec.eases), ramp_start.get(spec.sport, 60.0)
                )
                cap = 5.0 * math.floor(EASED_FRACTION * yardstick / 5.0)
                # Never under the card while the card is shorter than the
                # yardstick: a cap under the card would drop the long run.
                card = _spec_floor_hours(spec, swim_css) * 60.0
                if cap < card < yardstick:
                    cap = card
                effective_min_min = min(effective_min_min, cap)
                nominal = min(nominal, cap)
                effective_max_min = min(effective_max_min, cap)
                eased_cap[spec.sport] = min(eased_cap.get(spec.sport, cap), cap)
            raw_sessions.append(
                (
                    spec,
                    effective_min_min / 60.0,
                    nominal / 60.0,
                    effective_max_min / 60.0,
                )
            )
        # The minutes the eased long run gives up are not re-spent on the
        # week's easy runs: none outgrows the cap (R-SESS-034).
        raw_sessions = [_under_eased_cap(entry, eased_cap) for entry in raw_sessions]
        # A race week shrinks with the athlete: at most its goal's share of
        # the biggest load week booked before it (R-SESS-036).
        if mode_key == "race" and peak_load > 0.0:
            raw_sessions = _shape_race_week(
                raw_sessions, RACE_WEEK_SHARE[effective_goal] * peak_load, swim_css
            )
        elif mode_key == "taper" and peak_load > 0.0:
            # A taper week sheds volume with the athlete, and never grows.
            raw_sessions = _shape_taper_week(
                raw_sessions,
                min(TAPER_WEEK_SHARE * peak_load, last_taper),
                taper_longest,
                swim_css,
            )

        # Second pass: fit the week to the capacity ceiling and to each
        # activity's materializable floor.  Minimums are
        # scaled proportionally when their sum exceeds the ceiling (keeps
        # the QP feasible while preserving the session mix).  A session
        # is then dropped when it cannot be booked at at least
        # FLOOR_DROP_TOLERANCE of the smallest workout its activity can
        # build (rep lattices and fixed test protocols don't shrink) —
        # otherwise the card would render substantially larger than the
        # plan sized it.  Survivors get their minimums lifted toward the
        # floor, capped by the week's remaining headroom so the QP stays
        # feasible by construction.
        entries: list[
            tuple[SessionSpec, float, float, float, int, ActivityKey, float, float]
        ]
        entries = []
        # The swims that book at least their tier's floor (R-SESS-039).
        floored: set[int] = set()
        for idx, (spec, min_h, target_h, max_h) in enumerate(raw_sessions):
            # Resolve rotation: pick variant based on phase-run week offset.
            effective_key = spec.activity_key
            if spec.rotation:
                rot_idx = offset % len(spec.rotation)
                effective_key = spec.rotation[rot_idx]
            # A displaced test takes the slot of its sport on its weekday
            # (R-SESS-042), then meets the post-race easing like any test.
            if (spec.sport, spec.day_hint) in displaced and not spec.optional:
                effective_key = displaced.pop((spec.sport, spec.day_hint))
            race_day = post_race_day.get(week_idx)
            hardness = get_activity(effective_key).hardness
            if (
                race_day is not None
                and spec.sport in _POST_RACE_EASY_KEY
                and 0 <= spec.day_hint + 7 - race_day <= POST_RACE_EASY_DAYS
                and (
                    hardness.is_hard_physiologically
                    or (mode == WeekMode.RACE_RECOVERY and hardness.is_hard_overall)
                )
            ):
                if effective_key in _TEST_KEYS:
                    owed_tests[(spec.sport, spec.day_hint)] = effective_key
                effective_key = _POST_RACE_EASY_KEY[spec.sport]
            elif (
                (spec.sport, spec.day_hint) in owed
                and mode == WeekMode.LOAD
                and ws.phase not in (PhaseType.PEAK, PhaseType.TAPER)
                and get_activity(effective_key).hardness.is_hard_physiologically
            ):
                effective_key = owed.pop((spec.sport, spec.day_hint))
            # A swim the week cannot fit at its floor gives way rather
            # than shrinks under it (R-SESS-039).
            swim_floor_h = _swim_floor_hours(
                spec, effective_key, mode_key, ambition, swim_css
            )
            if swim_floor_h > 0.0:
                # Whatever the ramp or the taper gave it, the swim books
                # at least its floor.
                floored.add(idx)
                min_h = max(min_h, swim_floor_h)
                target_h = max(target_h, min_h)
                max_h = max(max_h, target_h)
            # A swim's card swims metres: its floor and ceiling run at the
            # swimmer's CSS (the swim test's fixed protocol included).
            card_css = swim_css if spec.sport == SportType.SWIMMING else None
            floor_h = max(_activity_floor_hours(effective_key, card_css), swim_floor_h)
            ceiling_h = _activity_ceiling_hours(effective_key, card_css)
            if effective_key in _TEST_KEYS:
                # Fixed protocols book exactly their card: the authored
                # slot duration is a stale booking; the protocol is the
                # protocol.
                min_h = target_h = max_h = floor_h
            entries.append(
                (spec, min_h, target_h, max_h, idx, effective_key, floor_h, ceiling_h)
            )

        # Sessions the week cannot fit keep their smallest card, optional
        # (R-SESS-037); only a partial fitness test and a brick without its
        # ride still leave the week.
        # Each with its own note, or None for the week's default note. The
        # menu's own optional extras never enter the fit (R-SESS-040).
        optional: list[tuple[_Entry, str | None]] = [
            (e, OPTIONAL_MENU_WHY) for e in entries if e[0].optional
        ]
        entries = [e for e in entries if not e[0].optional]
        # A key or ramp session the week cannot fit even with the others
        # optional stays booked at its card, over the ceiling.
        pinned: list[_Entry] = []
        while True:
            # A brick run cannot outlive its ride: brick co-location is
            # same-day (the brick_partner_day lint), so a brick whose
            # day keeps no cycling survivor drops with its ride — a
            # "brick" wired across days is not a brick, and a later-day
            # ride would leave the partner wiring below with nothing.
            # A brick follows its ride: optional with an optional ride.
            cycling_days = {
                e[0].day_hint
                for e in [*entries, *pinned]
                if e[0].sport == SportType.CYCLING
            }
            orphans = [
                e
                for e in entries
                if e[0].is_brick_pair and e[0].day_hint not in cycling_days
            ]
            entries = [e for e in entries if e not in orphans]
            optional_days = {
                e[0].day_hint for e, _ in optional if e[0].sport == SportType.CYCLING
            }
            optional += [
                (e, None)
                for e in orphans
                if e[0].day_hint in optional_days and _may_be_optional(e, mode)
            ]
            scale, sized, filler = _fit_scale(
                entries, budget, week_ceiling, mode == WeekMode.TAPER, floored
            )
            # Offenders leave one re-fit at a time, since each drop frees
            # scale for the rest; dropping them all against one scale left
            # squeezed recovery weeks empty once their fixed test was
            # counted. Unkeyed fixed protocols that miss go first and
            # together (they cannot shrink, and a squeezed week does not
            # test); a keyed test is the week's reason (R-SESS-B-000) and
            # gives way like any key session, booked over the ceiling when
            # nothing else can go. Then the largest-shortfall session, one
            # per re-fit; then the filler a squeezed keyed week gives up
            # (R-SESS-033). While a filler is named, only key sessions count
            # as offenders: the other sessions' scale is not final until the
            # fillers are done.
            offenders = [
                (e, short) for e in sized if (short := _shortfall(e, scale)) > 0.0
            ]
            tests = {e[4] for e, _ in offenders if e[5] in _TEST_KEYS and not e[0].key}
            if tests:
                entries = [e for e in entries if e[4] not in tests]
                continue
            if filler is not None:
                offenders = [el for el in offenders if el[0][0].key]
            worst = (
                _worst_offender(offenders, entries, mode == WeekMode.TAPER)
                if offenders
                else filler
            )
            if worst is None:
                # Nothing under its card: the shortest session the squeeze
                # shrinks under its menu minimum stays whole and optional.
                squeezed = [
                    (e, -e[1]) for e in sized if _squeezed(e, scale, maintain_recovery)
                ]
                if squeezed:
                    worst = _worst_offender(squeezed, entries, mode == WeekMode.TAPER)
            if worst is not None:
                entries, optional, pinned = _give_way(
                    worst, entries, optional, pinned, mode
                )
                continue
            # Lift minimums toward floors within the week's headroom.
            # Partial lifts (ratio < 1) leave bounded dishonesty; a
            # session still below TOL-honesty after its share hogs
            # headroom it cannot use — drop the largest-lift offender
            # and re-fit.
            lifts = [max(e[6] - e[1] * scale, 0.0) for e in sized]
            needed = sum(lifts)
            headroom = max(budget - sum(e[1] * scale for e in sized), 0.0)
            ratio = 1.0 if needed <= headroom else headroom / needed
            if ratio < 1.0:
                # Taper/race weeks shed sessions rather than carry
                # partially-lifted bookings: when headroom cannot fund
                # the full lifts, drop the largest-lift non-progressive,
                # non-test, non-key session outright and re-fit — freshness
                # beats a floor-inflated snippet this close to the race.
                if mode in (WeekMode.TAPER, WeekMode.RACE):
                    candidates = [
                        (e, lift)
                        for e, lift in zip(sized, lifts, strict=True)
                        if lift > 0.0
                        and not e[0].progressive
                        and not e[0].key
                        and e[0].eases is None
                        and e[5] not in _TEST_KEYS
                    ]
                    if candidates:
                        worst_c, _ = max(candidates, key=lambda el: el[1])
                        entries, optional, pinned = _give_way(
                            worst_c, entries, optional, pinned, mode
                        )
                        continue
                # A fixed test protocol cannot be partially lifted — the
                # athlete does the whole protocol or none of it — so its
                # required booking is the full card, not the tolerance.
                offenders = [
                    (e, lift)
                    for e, lift in zip(sized, lifts, strict=True)
                    if e[1] * scale + lift * ratio
                    < (
                        e[6] - 1e-9
                        if e[5] in _TEST_KEYS
                        else e[6] * _floor_tolerance(e[5])
                    )
                ]
                if offenders:
                    worst, _ = max(offenders, key=lambda el: el[1])
                    if worst[5] in _TEST_KEYS and not worst[0].key:
                        entries = [e for e in entries if e[4] != worst[4]]
                    else:
                        entries, optional, pinned = _give_way(
                            worst, entries, optional, pinned, mode
                        )
                    continue
            break

        week_start = len(result)
        last_bike_slot: str | None = None
        # Pre-pass: cycling slot ids by authored day, so a brick wires to
        # its SAME-DAY ride (the brick_partner_day lint guarantees one
        # exists) regardless of resolved slot order — slot-group winners
        # are appended after all ungrouped slots, so list order alone
        # would wire a late-BUILD Saturday brick to Sunday's endurance
        # ride instead.
        bike_slot_by_day: dict[int, str] = {
            spec.day_hint: _menu_slot_id(spec, week_idx, idx)
            for spec, _mh, _th, _xh, idx, _key, _fl, _cl in [
                *(e for e, _ in optional),
                *pinned,
                *sized,
            ]
            if spec.sport == SportType.CYCLING and spec.day_hint >= 0
        }
        # The hours the week's minimums leave under its budget and the
        # athlete's cap: the solver books a capped week at its minimums.
        spare = min(budget, weekly_hours_cap or budget) - sum(
            min(e[1] * scale + lift * ratio, e[7])
            for e, lift in zip(sized, lifts, strict=True)
        )
        # The long run takes the spare hours first, then the ride and the
        # swim: a squeezed triathlon week keeps its running ramp whole.
        bounds: dict[int, tuple[float, float, float]] = {}
        for (
            spec,
            min_h,
            target_h,
            max_h,
            idx,
            _key,
            floor_h,
            ceiling_h,
        ), lift in sorted(
            zip(sized, lifts, strict=True),
            key=lambda el: el[0][0].sport != SportType.RUNNING,
        ):
            # Ceiling clamp: a bound above the largest buildable workout
            # would render a card smaller than the booking (the mirror of
            # the floor lift), so all three bounds cap at the ceiling.
            scaled_min = min(min_h * scale + lift * ratio, ceiling_h)
            scaled_target = min(max(target_h * scale, scaled_min), ceiling_h)
            scaled_max = min(max(max_h, scaled_target), ceiling_h)
            if spec.progressive:
                # What the athlete is booked, not the ramp: a squeezed week
                # books its long run under the ramp value.
                booked = (spec.sport, spec.progression_key or spec.activity_key.value)
                # A ramp books at most one step over its last booking, and
                # its minimum rises to its target as far as the spare hours
                # allow, so the solver cannot book it shorter than the value
                # the next load week steps from (R-SESS-013).
                if not spec.race_sim:
                    prev = booked_last.get(booked)
                    if prev is not None:
                        step = max(
                            prev * MAX_DURATION_STEP_FRACTION,
                            _STEP_FLOOR_MIN[spec.sport],
                        )
                        # Never under the card: a session renders at least
                        # its workout's floor (R-SESS-030).
                        cap_h = max((prev + step) / 60.0, floor_h)
                        scaled_min = min(scaled_min, cap_h)
                        scaled_target = min(scaled_target, cap_h)
                        scaled_max = min(scaled_max, cap_h)
                    rise = min(scaled_target - scaled_min, max(spare, 0.0))
                    scaled_min += rise
                    spare -= rise
                    booked_last[booked] = scaled_min * 60.0
                longest[booked] = max(longest.get(booked, 0.0), scaled_target * 60.0)
            bounds[idx] = (scaled_min, scaled_target, scaled_max)

        for spec, _min_h, _target_h, _max_h, idx, effective_key, _fl, _cl in sized:
            scaled_min, scaled_target, scaled_max = bounds[idx]
            slot_id = _menu_slot_id(spec, week_idx, idx)

            # Wire brick partner: the SAME-DAY cycling session (day-first,
            # per the brick_partner_day lint), falling back to the most
            # recent cycling session in resolved order.
            brick_partner: str | None = None
            if spec.is_brick_pair:
                brick_partner = bike_slot_by_day.get(spec.day_hint, last_bike_slot)
                if brick_partner is None:  # pragma: no cover
                    msg = (  # pragma: no cover
                        f"Brick session {spec.activity_key.value} in "
                        f"week {week_idx} has no bike session in the menu"
                    )
                    raise ValueError(msg)  # pragma: no cover

            origin = (
                SessionOrigin.TEST
                if effective_key in _TEST_KEYS
                else SessionOrigin.ANCHOR
            )
            result.append(
                PlacedSession(
                    slot_id=slot_id,
                    activity_key=effective_key,
                    sport=spec.sport,
                    origin=origin,
                    week=week_idx,
                    day_of_week=spec.day_hint if spec.day_hint >= 0 else None,
                    target_hours=scaled_target,
                    min_hours=scaled_min,
                    max_hours=scaled_max,
                    brick_partner=brick_partner,
                    slot_name=spec.slot_name,
                )
            )

            if spec.sport == SportType.CYCLING:
                last_bike_slot = slot_id

        week_hours = min(sum(p.target_hours for p in result[week_start:]), budget)
        if mode == WeekMode.LOAD:
            # The solver books a load week at its targets, up to its budget.
            peak_load = max(peak_load, week_hours)
            last_taper = math.inf
            taper_longest = {}
        elif mode == WeekMode.TAPER:
            last_taper = week_hours
            taper_longest = {}
            for p in result[week_start:]:
                taper_longest[p.sport] = max(
                    taper_longest.get(p.sport, 0.0), p.target_hours
                )

        # The optional sessions, at their smallest card but not under their
        # authored minimum (a menu's optional extra at its target, R-SESS-040),
        # outside the load model; and the key sessions booked at their card
        # over the ceiling (R-SESS-037).
        pinned_idx = {e[4] for e in pinned}
        note = (
            OPTIONAL_TAPER_WHY
            if mode in (WeekMode.TAPER, WeekMode.RACE)
            else OPTIONAL_SHORT_WEEK_WHY
        )
        for (spec, min_h, th, _xh, idx, effective_key, floor_h, ceiling_h), why in [
            *optional,
            *((e, None) for e in pinned),
        ]:
            hours = min(max(floor_h, th if spec.optional else min_h), ceiling_h)
            result.append(
                PlacedSession(
                    slot_id=_menu_slot_id(spec, week_idx, idx),
                    activity_key=effective_key,
                    sport=spec.sport,
                    origin=SessionOrigin.ANCHOR,
                    week=week_idx,
                    day_of_week=spec.day_hint if spec.day_hint >= 0 else None,
                    target_hours=hours,
                    min_hours=hours,
                    max_hours=hours,
                    brick_partner=(
                        bike_slot_by_day.get(spec.day_hint, last_bike_slot)
                        if spec.is_brick_pair
                        else None
                    ),
                    slot_name=spec.slot_name,
                    optional_reason=None if idx in pinned_idx else why or note,
                    over_ceiling=idx in pinned_idx,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _displaced_tests(
    week_idx: int,
    phase_runs: list[tuple[int, int, str, GoalType]],
    a_goal: RaceGoal,
    ambition: AmbitionLevel,
) -> dict[tuple[SportType, int], ActivityKey]:
    """Return the fitness tests the A goal's load menu would have drawn in a week.

    The A-goal run spanning the week (R-SESS-032) names the load menu and
    the rotation offset (R-SESS-010); a race week no run spans displaces
    nothing (R-SESS-042).

    Args:
        week_idx: The race week, or the easier week after a long B race.
        phase_runs: The plan's phase runs (``_compute_phase_runs``).
        a_goal: The plan's A goal.
        ambition: The athlete's tier.

    Returns:
        The displaced test keys by ``(sport, day_hint)``.
    """
    spanning = next(
        (
            (start, length, phase)
            for start, length, phase, _goal in phase_runs
            if start <= week_idx < start + length and (start, length) != (week_idx, 1)
        ),
        None,
    )
    if spanning is None:
        return {}
    start, length, phase = spanning
    offset = week_idx - start
    menu = list(get_session_menu(a_goal.goal_type, PhaseType(phase), "load", ambition))
    displaced: dict[tuple[SportType, int], ActivityKey] = {}
    for spec in active_specs(menu, offset / max(length - 1, 1), False):
        key = (
            spec.rotation[offset % len(spec.rotation)]
            if spec.rotation
            else spec.activity_key
        )
        if key in _TEST_KEYS:
            displaced[(spec.sport, spec.day_hint)] = key
    return displaced


def _shape_race_week(
    sessions: list[tuple[SessionSpec, float, float, float]],
    cap: float,
    swim_css: float,
    *,
    least: list[float] | None = None,
) -> list[tuple[SessionSpec, float, float, float]]:
    """Shorten a race week's sessions alike until the week fits *cap* hours.

    A session counts at the length its card renders, at least its card's
    floor (``_activity_floor_hours``). Every session keeps its place and
    shrinks by one common scale, but never under its card nor under
    ``FILLER_MIN_SCALE`` of its authored length, the least a squeezed session
    is booked (R-SESS-033): a session the scale would take under that stays
    there, and the others share what is left. A week whose floors alone fill
    *cap* books every session at its floor and stays over it. A week already
    inside *cap* is unchanged.

    Args:
        sessions: The race week's ``(spec, min, target, max)`` hours.
        cap: The most the week may book, in hours.
        swim_css: The swimmer's CSS in sec/100m, for a swim's card floor.
        least: A floor per session over its own, in hours.

    Returns:
        The sessions with all three bounds at their shortened lengths.
    """
    cards = [_spec_floor_hours(spec, swim_css) for spec, *_ in sessions]
    targets = [max(t, c) for (_, _, t, _), c in zip(sessions, cards, strict=True)]
    floors = [
        max(c, FILLER_MIN_SCALE * t, x)
        for (_, _, t, _), c, x in zip(
            sessions, cards, least or [0.0] * len(sessions), strict=True
        )
    ]
    if sum(targets) <= cap:
        return sessions
    pinned: set[int] = set()
    while True:
        free = sum(t for i, t in enumerate(targets) if i not in pinned)
        room = cap - sum(floors[i] for i in pinned)
        scale = max(room, 0.0) / free if free else 0.0
        short = {
            i
            for i, t in enumerate(targets)
            if i not in pinned and t * scale < floors[i]
        }
        if not short:
            break
        pinned |= short
    return [
        (spec, hours, hours, hours)
        for i, (spec, *_) in enumerate(sessions)
        for hours in [floors[i] if i in pinned else targets[i] * scale]
    ]


def _shape_taper_week(
    sessions: list[tuple[SessionSpec, float, float, float]],
    cap: float,
    longest: dict[SportType, float],
    swim_css: float,
) -> list[tuple[SessionSpec, float, float, float]]:
    """Shorten a taper week's easy and long sessions until it fits *cap* hours.

    Volume falls, intensity holds: a tempo, threshold or interval session
    keeps its bounds and counts at the most the solver may book it, its upper
    bound (at least its card). The easy, recovery and long sessions share the
    rest as a race week does (``_shape_race_week``): one common scale, none
    under its card nor under ``FILLER_MIN_SCALE`` of its authored length, and
    a long session never under the least the week's longest quality session
    is booked. No easy or long session outgrows *longest* for its sport. A
    week whose floors cannot reach *cap* is otherwise unchanged: shortening
    it would only make room for a session the capacity fit drops.

    Args:
        sessions: The taper week's ``(spec, min, target, max)`` hours.
        cap: The most the week may book, in hours.
        longest: The previous taper week's longest session per sport, hours.
        swim_css: The swimmer's CSS in sec/100m, for a swim's card floor.

    Returns:
        The sessions, the easy and long ones capped at their shortened lengths.
    """
    families = [get_activity(spec.activity_key).family for spec, *_ in sessions]
    sessions = [
        (spec, min(min_h, most), min(target_h, most), min(max_h, most))
        if family in _TAPER_VOLUME
        else (spec, min_h, target_h, max_h)
        for (spec, min_h, target_h, max_h), family in zip(
            sessions, families, strict=True
        )
        for most in [longest.get(spec.sport, math.inf)]
    ]
    quality = [
        max(max_h, _spec_floor_hours(spec, swim_css))
        for (spec, _min_h, _target_h, max_h), family in zip(
            sessions, families, strict=True
        )
        if family not in _TAPER_VOLUME
    ]
    volume = [i for i, family in enumerate(families) if family in _TAPER_VOLUME]
    # The least the week's longest quality session is booked.
    top = max(
        (
            max(min_h, _spec_floor_hours(spec, swim_css))
            for (spec, min_h, *_), family in zip(sessions, families, strict=True)
            if family not in _TAPER_VOLUME
        ),
        default=0.0,
    )
    least = [top if families[i] == ActivityFamily.LONG else 0.0 for i in volume]
    floors = sum(
        max(_spec_floor_hours(spec, swim_css), FILLER_MIN_SCALE * target_h, x)
        for (spec, _min_h, target_h, _max_h), x in zip(
            (sessions[i] for i in volume), least, strict=True
        )
    )
    if sum(quality) + floors > cap:
        return sessions
    shaped = _shape_race_week(
        [sessions[i] for i in volume], cap - sum(quality), swim_css, least=least
    )
    # A cap, never a rise: the minimum and target only drop to the length.
    out = list(sessions)
    for i, (spec, _min_h, _target_h, hours) in zip(volume, shaped, strict=True):
        _spec, min_h, target_h, _max_h = sessions[i]
        out[i] = (spec, min(min_h, hours), min(target_h, hours), hours)
    return out


def _protected(entry: _Entry) -> bool:
    """Whether a session never goes optional: a key or ramp session."""
    return bool(entry[0].key) or entry[0].progressive


def _may_be_optional(entry: _Entry, mode: WeekMode) -> bool:
    """Whether a session the week cannot fit may stay, optional (R-SESS-037).

    In a taper or race week only an easy session may; a hard or long one
    leaves, since the week's job is to shed load.
    """
    return mode not in (WeekMode.TAPER, WeekMode.RACE) or get_activity(
        entry[5]
    ).family in (ActivityFamily.EASY, ActivityFamily.RECOVERY)


def _give_way(
    worst: _Entry,
    entries: list[_Entry],
    optional: list[tuple[_Entry, str | None]],
    pinned: list[_Entry],
    mode: WeekMode,
) -> tuple[list[_Entry], list[tuple[_Entry, str | None]], list[_Entry]]:
    """Take one session out of a week that cannot fit it (R-SESS-037).

    In a taper or race week a hard or long session leaves, key or not
    (``_may_be_optional``). A ramp session whose own ramp value is under its
    card is not ready for it: it stays optional, noted so, whatever the
    week's room. Another key
    or ramp session named to give way sends the week's biggest other
    session in its place (the one whose smallest honest booking is
    largest); with none left, it stays booked at its card, over the
    ceiling. The session that goes stays optional.

    Args:
        worst: The session named to give way.
        entries: The week's sessions still being sized.
        optional: The week's optional sessions so far.
        pinned: The week's sessions booked at their card over the ceiling.
        mode: The week's mode.

    Returns:
        ``(entries, optional, pinned)`` after the move.
    """
    if not _may_be_optional(worst, mode):
        return [e for e in entries if e[4] != worst[4]], optional, pinned
    if worst[0].progressive and worst[2] < worst[6] * FLOOR_DROP_TOLERANCE:
        entries = [e for e in entries if e[4] != worst[4]]
        return entries, [*optional, (worst, OPTIONAL_NOT_READY_WHY)], pinned
    if _protected(worst):
        others = [e for e in entries if not _protected(e) and e[5] not in _TEST_KEYS]
        if not others:
            return [e for e in entries if e[4] != worst[4]], optional, [*pinned, worst]
        if mode == WeekMode.TAPER:
            # A taper keeps its intensity (R-SESS-038): the key sends an
            # easy run in its place while the week holds one.
            easy = [
                e for e in others if get_activity(e[5]).family == ActivityFamily.EASY
            ]
            others = easy or others
        worst = max(others, key=lambda e: max(e[1] * FILLER_MIN_SCALE, e[6]))
    entries = [e for e in entries if e[4] != worst[4]]
    if not _may_be_optional(worst, mode):
        return entries, optional, pinned
    return entries, [*optional, (worst, None)], pinned


def _menu_slot_id(spec: SessionSpec, week_idx: int, idx: int) -> str:
    """Return the provenance slot id for a resolved menu session.

    The menu-position suffix ``i{idx}`` keeps slot ids unique when a single
    week has two specs sharing the same activity_key (e.g. PEAK Tue
    BIKE_THRESHOLD + Thu BIKE_THRESHOLD) — slot ids are provenance *and* the
    ``brick_partner`` reference, so collisions would wire bricks to the wrong
    bike.

    Args:
        spec: The resolved session spec.
        week_idx: Plan-relative week index.
        idx: Position of *spec* within the week's resolved menu.

    Returns:
        The slot id.
    """
    return f"menu:{spec.sport.value}:{spec.activity_key.value}:w{week_idx}:i{idx}"


def _under_eased_cap(
    entry: tuple[SessionSpec, float, float, float], eased_cap: dict[SportType, float]
) -> tuple[SessionSpec, float, float, float]:
    """Cap an easy run at its week's eased cap (R-SESS-034).

    In a week holding an eased session, a same-sport session that is not
    key, not eased itself and whose activity is in the easy family is
    never sized above the eased cap, so the week gets lighter instead of
    re-spending the minutes the long run gave up. Other entries pass
    through unchanged.

    Args:
        entry: ``(spec, min_h, target_h, max_h)`` from the first pass.
        eased_cap: The week's eased cap per sport, in minutes.

    Returns:
        The entry, its bounds capped when the rule applies.
    """
    spec, min_h, target_h, max_h = entry
    cap_min = eased_cap.get(spec.sport)
    ensure_registry_loaded()
    if (
        cap_min is None
        or spec.key
        or spec.eases is not None
        or get_activity(spec.activity_key).family != ActivityFamily.EASY
    ):
        return entry
    cap = cap_min / 60.0
    return spec, min(min_h, cap), min(target_h, cap), min(max_h, cap)


def _spared(entry: _Entry) -> bool:
    """Whether a squeezed week gives up its other sessions before *entry*.

    The week's long run, and any session that eases one, stay while the week
    holds another session to give up.
    """
    return (
        entry[0].eases is not None
        or get_activity(entry[5]).family == ActivityFamily.LONG
    )


def _worst_offender(
    offenders: list[tuple[_Entry, float]],
    entries: list[_Entry],
    keep_quality: bool = False,
) -> _Entry:
    """Pick the session a squeezed week drops next (R-SESS-030, R-SESS-034).

    The largest shortfall goes first. An eased session is the easier
    week's long run, and a long run is the week's anchor, so they go last:
    while the week holds another session, an offending one instead costs the
    week its shortest other session, which frees the scale it needs. With
    *keep_quality* (a taper, R-SESS-038) an easy-family offender goes
    before any other, so the taper sheds easy running and keeps its tempo.

    Args:
        offenders: ``(entry, shortfall)`` for each session under its floor.
        entries: The week's surviving sizing entries.
        keep_quality: A taper keeps its intensity and gives up easy running
            first.

    Returns:
        The entry to drop before the week re-fits.
    """
    if keep_quality:
        easy = [
            el
            for el in offenders
            if get_activity(el[0][5]).family == ActivityFamily.EASY
        ]
        if easy:
            return max(easy, key=lambda el: el[1])[0]
        # A quality session under its floor costs the taper an easy run
        # instead, while the week holds one that is neither key nor eased.
        easy_entries = [
            e
            for e in entries
            if get_activity(e[5]).family == ActivityFamily.EASY
            and not e[0].key
            and e[0].eases is None
        ]
        if easy_entries:
            return max(easy_entries, key=lambda e: e[1])
    plain = [el for el in offenders if not _spared(el[0])]
    if plain:
        return max(plain, key=lambda el: el[1])[0]
    others = [e for e in entries if not _spared(e)]
    if others:
        return min(others, key=lambda e: e[1])
    return max(offenders, key=lambda el: el[1])[0]


def _fit_scale(
    entries: list[_Entry],
    budget: float,
    ceiling: float,
    keep_quality: bool = False,
    floored: set[int] | None = None,
) -> tuple[float, list[_Entry], _Entry | None]:
    """Fit a week's minimums to its capacity budget (R-SESS-014, R-SESS-033).

    A key session's (``SessionSpec.key``) minimum is first raised to its
    activity's materializable floor. A week whose minimums fit its
    ceiling keeps them (scale 1). A squeezed week without key sessions
    scales every minimum alike. A squeezed week with key sessions books
    each key at its target, capped at its share of the budget; the other
    sessions share what is left at one common scale, and while that scale
    is below ``FILLER_MIN_SCALE`` the one that fits the week least is named
    to drop: the one whose smallest honest booking (``FILLER_MIN_SCALE`` of
    its minimum, at least its card floor) is largest; with *keep_quality*,
    an easy-family session goes before any other. The last one is never
    named. A week of keys only books each at most its share and is left
    light. When the keys alone fill the budget, every minimum scales alike
    again. A floored swim (R-SESS-039) books at its minimum the way a key
    books at its target, uncapped, so the squeeze falls on the others.

    Args:
        entries: The week's surviving sizing entries.
        budget: The week's sizing budget in hours.
        ceiling: The week's capacity ceiling in hours.
        keep_quality: A taper keeps its intensity and gives up easy running
            first.
        floored: Indices of the swims that never book under their floor.

    Returns:
        ``(scale, sized, filler)``: the scale every minimum is multiplied
        by; the entries with each booked key's minimum and target set so
        that ``minimum * scale`` is its booking; and the entry to drop
        before the week re-fits, or ``None``.
    """
    sized = [_at_floor(e) if e[0].key else e for e in entries]
    total = sum(e[1] for e in sized)
    if total <= ceiling:
        return 1.0, sized, None
    floored = floored or set()
    booked = {e[4]: min(e[2], e[0].key * budget) for e in sized if e[0].key}
    booked |= {e[4]: e[1] for e in sized if e[4] in floored}
    fillers = [e for e in sized if e[4] not in booked]
    rest = sum(e[1] for e in fillers)
    left = budget - sum(booked.values())
    if not booked or left <= 0.0:
        return budget / total, sized, None
    scale = min(left / rest, 1.0) if rest else 1.0
    sized = [
        (e[0], booked[e[4]] / scale, booked[e[4]] / scale, *e[3:])
        if e[4] in booked
        else e
        for e in sized
    ]
    if scale >= FILLER_MIN_SCALE or len(fillers) <= 1:
        return scale, sized, None

    def fits_least(e: _Entry) -> tuple[bool, float]:
        easy = get_activity(e[5]).family == ActivityFamily.EASY
        return easy or not keep_quality, max(e[1] * FILLER_MIN_SCALE, e[6])

    return scale, sized, max(fillers, key=fits_least)


def _at_floor(entry: _Entry) -> _Entry:
    """Return *entry* with its minimum, target and maximum at least its floor."""
    spec, min_h, target_h, max_h, idx, key, floor_h, ceiling_h = entry
    min_h = max(min_h, floor_h)
    target_h = max(target_h, min_h)
    return (spec, min_h, target_h, max(max_h, target_h), idx, key, floor_h, ceiling_h)


def _holds_race_sim(
    specs: list[SessionSpec],
    budget: float,
    ramp_start: dict[SportType, float],
    booked_last: dict[tuple[SportType, str], float],
    swim_css: float,
) -> bool:
    """Whether the race-sim week can hold its rehearsal at full size.

    A rehearsal is only a rehearsal at its authored length: every
    ``race_sim`` key spec's minimum, at least its materializable floor,
    must fit within its share of the week's budget, and every ``race_sim``
    spec on a ramp must be at most its step (``max_step``, else
    ``REHEARSAL_STEP_FRACTION``) longer than the least its progression was
    last booked (R-SESS-013: the solver books no shorter) or the athlete's
    recent longest, whichever is longer. Otherwise the week resolves its
    slot groups as a routine PEAK load week (R-SESS-023).

    Args:
        specs: The week's active specs with its race-sim variants firing.
        budget: The week's sizing budget in hours.
        ramp_start: Each sport's ramp start (minutes): the athlete's recent
            longest, or the sport's floor.
        booked_last: The least each progression was last booked (minutes).
        swim_css: The athlete's CSS (sec/100 m), for a swim's minutes.

    Returns:
        True when the race-sim variants fire.
    """
    for s in specs:
        if not s.race_sim:
            continue
        minutes = (
            _swim_minutes(s.activity_key, s.min_meters, swim_css)
            if s.sport == SportType.SWIMMING
            else float(s.min_minutes)
        )
        if s.key and max(minutes / 60.0, _spec_floor_hours(s, swim_css)) > (
            s.key * budget
        ):
            return False
        if not s.progressive:
            continue
        prog_key = (s.sport, s.progression_key or s.activity_key.value)
        yardstick = max(booked_last.get(prog_key, 0.0), ramp_start.get(s.sport, 60.0))
        step_fraction = s.max_step or REHEARSAL_STEP_FRACTION
        if minutes > yardstick * (1.0 + step_fraction) + 1e-9:
            return False
    return True


#: How far (in weeks) a cadence week may sit from its interval mark.
#: Two keeps the 3:1 cadence's recovery week one short of the mark
#: (plan week 8 for a mark at 9) and leaves the one four weeks after it
#: alone; a 2:1 or 4:1 cadence always has a recovery week within two.
CADENCE_TOLERANCE_WEEKS = 2


def cadence_weeks(weeks: list[WeekSpec], every: int) -> frozenset[int]:
    """Choose the weeks a ``cadence_weeks: every`` spec fires on.

    The first week of the plan's timeline opens the cadence (on a replan,
    the first trained week, numbered below 0). After it, each mark
    ``every`` plan weeks on takes the RECOVERY week nearest to it, at most
    ``CADENCE_TOLERANCE_WEEKS`` away (ties go to the later week), never
    a PEAK or TAPER week and never a tune-up race's own week. A mark
    with no such week fires nothing: the plan skips that occasion rather
    than moving it into the peak (R-SESS-041).

    Args:
        weeks: Ordered ``WeekSpec`` list of the plan's timeline.
        every: The interval in plan weeks.

    Returns:
        The week indices that fire.
    """
    if not weeks:
        return frozenset()
    chosen: set[int] = {weeks[0].week}
    candidates = [
        ws.week
        for ws in weeks
        if ws.mode == WeekMode.RECOVERY
        and ws.phase not in (PhaseType.PEAK, PhaseType.TAPER)
        and ws.owning_goal is None
    ]
    mark = weeks[0].week + every
    while mark <= weeks[-1].week + CADENCE_TOLERANCE_WEEKS:
        near = [
            w
            for w in candidates
            if abs(w - mark) <= CADENCE_TOLERANCE_WEEKS and w not in chosen
        ]
        if near:
            chosen.add(max(near, key=lambda w: (-abs(w - mark), w)))
        mark += every
    return frozenset(chosen)


def race_sim_weeks(weeks: list[WeekSpec]) -> frozenset[int]:
    """Choose exactly one race-sim LOAD week per contiguous PEAK run.

    The race-sim week hosts the menu's ``race_sim`` specs (the big
    race-rehearsal weekend). Per run, the winner is the PEAK LOAD week
    whose distance to the race week is nearest
    ``RACE_SIM_TARGET_WEEKS_OUT`` (ties go to the earlier week), never
    the final pre-taper LOAD week (the most-shed week) when the run has
    more than one LOAD week. A run with a single LOAD week uses that
    week, so the big weekend cannot silently vanish. The race week is
    the first ``RACE``-mode week after the run (falling back to the
    last plan week).

    Args:
        weeks: Ordered ``WeekSpec`` list from the phase plan.

    Returns:
        Week indices designated as race-sim weeks (one per PEAK run
        that has at least one LOAD week).
    """
    chosen: set[int] = set()
    n = len(weeks)
    i = 0
    while i < n:
        if weeks[i].phase != PhaseType.PEAK:
            i += 1
            continue
        j = i
        while j < n and weeks[j].phase == PhaseType.PEAK:
            j += 1
        load_weeks = [ws.week for ws in weeks[i:j] if ws.mode == WeekMode.LOAD]
        if load_weeks:
            race_week = next(
                (ws.week for ws in weeks[j:] if ws.mode == WeekMode.RACE),
                weeks[-1].week,
            )
            # Never the most-shed final pre-taper LOAD week (when avoidable).
            pool = load_weeks[:-1] if len(load_weeks) > 1 else load_weeks
            chosen.add(
                min(
                    pool,
                    key=lambda w: (
                        abs((race_week - w) - RACE_SIM_TARGET_WEEKS_OUT),
                        w,
                    ),
                )
            )
        i = j
    return frozenset(chosen)


def _compute_phase_runs(
    weeks: list[WeekSpec],
) -> list[tuple[int, int, str, GoalType]]:
    """Identify contiguous runs of the same (phase, effective_goal).

    A B/C race week (``owning_goal`` set) is a one-week run of its own and
    does not break the A-goal run around it: that run spans the race week,
    so the weeks after a tune-up keep their place in the phase instead of
    restarting it (R-SESS-032). A race week's run is listed before the run
    that spans it.

    Returns:
        List of (start_week, length, phase_value, effective_goal).
    """
    runs: list[tuple[int, int, str, GoalType]] = []
    current: tuple[int, int, str, GoalType] | None = None  # (start, end, ...)
    for ws in weeks:
        if ws.owning_goal is not None:
            runs.append((ws.week, 1, ws.phase.value, ws.effective_goal))
            continue
        if (
            current is not None
            and ws.phase.value == current[2]
            and ws.effective_goal == current[3]
        ):
            current = (current[0], ws.week, current[2], current[3])
            continue
        if current is not None:
            runs.append((current[0], current[1] - current[0] + 1, *current[2:]))
        current = (ws.week, ws.week, ws.phase.value, ws.effective_goal)
    if current is not None:
        runs.append((current[0], current[1] - current[0] + 1, *current[2:]))
    return runs


__all__ = [
    "CADENCE_TOLERANCE_WEEKS",
    "REHEARSAL_STEP_FRACTION",
    "cadence_weeks",
    "generate_session_menu",
    "race_sim_weeks",
]
