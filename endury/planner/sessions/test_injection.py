"""Fitness-test placement for the planner session-menu pipeline.

Injects fixed-duration ``PlacedSession`` entries for periodic fitness
tests (``bike_ftp_test``, ``run_threshold_test``, ``swim_css_test``)
based on a user-supplied :class:`TestProtocol`. Test sessions are:

* Marked with ``origin=SessionOrigin.TEST``
* Locked at a canonical duration (``min_hours == target_hours ==
  max_hours``) so the macro QP cannot resize them
* Pre-assigned to a canonical day-of-week per sport — Tue/Wed/Fri (or
  user-specified days); the day assigner pins TEST sessions to their
  preset day, never evicting them

Placement is **replacement-based**: for every test session added to a
week, one regular (``origin=ANCHOR``) same-sport session is dropped
from that week. This preserves per-week session counts (a recovery
week with tests has the *same* total session count as a recovery week
without tests).

The stage runs *after* :mod:`endury.planner.sessions.session_generator` and
*before* :mod:`endury.planner.macro.macro_planner`.
"""

from __future__ import annotations

from collections import defaultdict

from endury.planner.foundations.types import (
    ActivityKey,
    CapacityProjection,
    PhasePlan,
    PlacedSession,
    SessionOrigin,
    SportType,
    TestProtocol,
    WeekMode,
)

# ---------------------------------------------------------------------------
# Canonical placement constants
# ---------------------------------------------------------------------------

#: Default day-of-week per sport (0=Mon..6=Sun).
#: Tue bike / Wed run / Fri swim — avoids Thursday (a common rest day).
_DEFAULT_DAY_BY_SPORT: dict[SportType, int] = {
    SportType.CYCLING: 1,
    SportType.RUNNING: 2,
    SportType.SWIMMING: 4,
}

#: Fixed test-session duration in hours, per sport — the actual card
#: duration of each fixed protocol (warmup + strides + protocol +
#: cooldown), so the locked QP bounds match the workout the athlete
#: executes: FTP test 55 min, threshold run test 55 min, CSS test
#: ≈46.0 min at the default CSS (the swim card scales with the
#: athlete's CSS; the default-CSS booking is the same approximation the
#: session generator's floor probe uses). Drift-guarded against the
#: activity registry by tests/planner/test_test_injection.py.
_TEST_HOURS: dict[SportType, float] = {
    SportType.CYCLING: 55.0 / 60.0,
    SportType.RUNNING: 55.0 / 60.0,
    SportType.SWIMMING: 46.0 / 60.0,
}

_TEST_ACTIVITY_KEY: dict[SportType, ActivityKey] = {
    SportType.CYCLING: ActivityKey.BIKE_FTP_TEST,
    SportType.RUNNING: ActivityKey.RUN_THRESHOLD_TEST,
    SportType.SWIMMING: ActivityKey.SWIM_CSS_TEST,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_test_weeks(
    phase_plan: PhasePlan,
    protocol: TestProtocol,
) -> list[int]:
    """Resolve a :class:`TestProtocol` to concrete week indices.

    Resolution rules:

    1. If ``protocol.weeks`` is set, use it verbatim (after dropping any
       week indices that fall outside ``phase_plan.weeks``).
    2. Otherwise, if ``protocol.every_n_recovery_weeks`` is set, walk
       the plan's timeline (``prior_weeks`` then ``weeks``) and pick every
       Nth RECOVERY week still ahead. With ``include_baseline=True`` also
       include week 0 when it opens the plan (no ``prior_weeks``).
    3. Otherwise return an empty list (no tests scheduled).

    Args:
        phase_plan: Phase plan produced by L1.
        protocol: User-supplied test protocol.

    Returns:
        Sorted, de-duplicated list of 0-indexed week offsets.
    """
    valid_weeks = {ws.week for ws in phase_plan.weeks}

    if protocol.weeks is not None:
        return sorted({w for w in protocol.weeks if w in valid_weeks})

    if protocol.every_n_recovery_weeks is None:
        return []

    cadence = protocol.every_n_recovery_weeks
    selected: set[int] = set()

    # A replan's trained weeks count: the cadence runs on the plan's own
    # timeline, and its baseline week has passed.
    recovery_index = 0
    for ws in [*phase_plan.prior_weeks, *phase_plan.weeks]:
        if ws.mode == WeekMode.RECOVERY:
            recovery_index += 1
            if recovery_index % cadence == 0 and ws.week in valid_weeks:
                selected.add(ws.week)

    if protocol.include_baseline and not phase_plan.prior_weeks and 0 in valid_weeks:
        selected.add(0)

    return sorted(selected)


def inject_test_sessions(
    phase_plan: PhasePlan,
    sessions: list[PlacedSession],
    protocol: TestProtocol | None,
    capacity: CapacityProjection | None = None,
) -> list[PlacedSession]:
    """Inject test sessions into scheduled weeks.

    Displaces same-sport regular sessions so per-week counts stay stable.
    For every test session added to a week, **one regular
    (``origin=ANCHOR``) same-sport session is removed** from that week.
    Removal prefers the shortest same-sport session in the week so the
    higher-volume anchors (long rides, long runs, endurance swims) are
    preserved.

    If no same-sport session exists to displace, the test is still
    added (no matching regular session to remove).

    When *capacity* is given, a week whose battery would push the
    week's minimum hours above ``capacity.weekly_ceiling`` (a
    low-capacity athlete's tightly-bound entry weeks have no slack left
    to absorb the tests' locked duration) is deferred to the nearest
    later LOAD/RECOVERY week with room, rather than injected as-is
    (making the macro QP infeasible) or silently dropped. A week with no
    feasible later candidate keeps its original slot, surfacing the
    infeasibility downstream instead of masking it.

    Args:
        phase_plan: Phase plan produced by L1.
        sessions: Regular sessions from :func:`session_generator`.
        protocol: User-supplied test protocol, or ``None`` to disable.
        capacity: Per-week hours ceiling used to defer a battery that
            would otherwise be infeasible. ``None`` disables the
            feasibility check, injecting at the protocol's exact weeks.

    Returns:
        New list with displaced regulars removed and test sessions added.
        When ``protocol`` is None or empty, returns ``list(sessions)``.
    """
    if protocol is None or not protocol.sports:
        return list(sessions)

    weeks = resolve_test_weeks(phase_plan, protocol)
    if not weeks:
        return list(sessions)

    placement_weeks = _resolve_placement_weeks(
        phase_plan, sessions, protocol, weeks, capacity
    )
    test_sessions = _build_test_sessions(placement_weeks, protocol)

    # How many regular sessions to drop per (week, sport).
    drop_targets: dict[tuple[int, SportType], int] = defaultdict(int)
    for t in test_sessions:
        drop_targets[(t.week, t.sport)] += 1

    # Identify the specific session indices to drop: shortest first,
    # anchor-origin only, per (week, sport).
    drop_indices: set[int] = set()
    for (week, sport), n_drop in drop_targets.items():
        candidates = [
            (i, s)
            for i, s in enumerate(sessions)
            if s.week == week and s.sport == sport and s.origin == SessionOrigin.ANCHOR
        ]
        # Prefer dropping the shortest sessions so we preserve anchors.
        candidates.sort(key=lambda ix_s: ix_s[1].target_hours)
        for idx, _ in candidates[:n_drop]:
            drop_indices.add(idx)

    result: list[PlacedSession] = [
        s for i, s in enumerate(sessions) if i not in drop_indices
    ]
    result.extend(test_sessions)
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _build_test_sessions(
    weeks: list[int],
    protocol: TestProtocol,
) -> list[PlacedSession]:
    """Build the raw list of test ``PlacedSession`` entries at *weeks*.

    Args:
        weeks: Placement week for each battery, one entry per resolved
            test week (already deferred to a feasible week when a
            ``capacity`` projection was supplied to
            :func:`inject_test_sessions`).
        protocol: User-supplied test protocol.

    Returns:
        One ``PlacedSession`` per (week, sport) pair.
    """
    day_by_sport = {**_DEFAULT_DAY_BY_SPORT, **dict(protocol.day_by_sport)}

    sessions: list[PlacedSession] = []
    for week in weeks:
        for sport in protocol.sports:
            hours = _TEST_HOURS[sport]
            sessions.append(
                PlacedSession(
                    slot_id=f"test_{sport.value}_wk{week}",
                    activity_key=_TEST_ACTIVITY_KEY[sport],
                    sport=sport,
                    origin=SessionOrigin.TEST,
                    week=week,
                    day_of_week=day_by_sport[sport],
                    target_hours=hours,
                    min_hours=hours,
                    max_hours=hours,
                )
            )
    return sessions


def _resolve_placement_weeks(
    phase_plan: PhasePlan,
    sessions: list[PlacedSession],
    protocol: TestProtocol,
    weeks: list[int],
    capacity: CapacityProjection | None,
) -> list[int]:
    """Map each resolved test week to its actual (possibly deferred) week.

    Returns *weeks* unchanged when *capacity* is ``None``. Otherwise, a
    week whose battery would push the week's minimum hours above
    ``capacity.weekly_ceiling`` is deferred to the nearest later
    LOAD/RECOVERY week that is not already claimed by another battery in
    *weeks* and does not already carry a menu-authored test for one of
    *protocol.sports* (some recovery-week menus schedule their own
    periodic fitness test independently of any ``TestProtocol`` — see
    ``session_generator``) — never into TAPER/RACE/RACE_RECOVERY, where
    a fitness test would defeat the point. A week with no feasible
    later candidate keeps its original slot.

    Args:
        phase_plan: Phase plan produced by L1.
        sessions: Regular sessions from :func:`session_generator`.
        protocol: User-supplied test protocol.
        weeks: Resolved test weeks (see :func:`resolve_test_weeks`).
        capacity: Per-week hours ceiling, or ``None`` to disable.

    Returns:
        Placement week per entry in *weeks*, same order and length.
    """
    if capacity is None:
        return list(weeks)

    deferrable_weeks = sorted(
        ws.week
        for ws in phase_plan.weeks
        if ws.mode in (WeekMode.LOAD, WeekMode.RECOVERY)
    )
    reserved = set(weeks)

    placement: list[int] = []
    for week in weeks:
        target = week
        while not _battery_fits(
            sessions, target, protocol.sports, capacity.weekly_ceiling.get(target)
        ):
            later = [
                w
                for w in deferrable_weeks
                if w > target
                and w not in reserved
                and not _has_native_test(sessions, w, protocol.sports)
            ]
            if not later:
                target = week  # give up; keep the original slot
                break
            target = later[0]
        placement.append(target)
        reserved.add(target)
    return placement


def _has_native_test(
    sessions: list[PlacedSession],
    week: int,
    sports: tuple[SportType, ...],
) -> bool:
    """Return whether *week* already carries a menu-authored test session.

    Args:
        sessions: Regular sessions from :func:`session_generator`, which
            may itself schedule ``origin=TEST`` sessions on recovery
            weeks independently of any ``TestProtocol``.
        week: Week to check.
        sports: Sports a deferred battery would test.

    Returns:
        ``True`` if *week* already has an ``origin=TEST`` session for
        one of *sports*.
    """
    return any(
        s.week == week and s.origin == SessionOrigin.TEST and s.sport in sports
        for s in sessions
    )


def _battery_fits(
    sessions: list[PlacedSession],
    week: int,
    sports: tuple[SportType, ...],
    ceiling: float | None,
) -> bool:
    """Return whether testing every sport in *sports* fits *week*'s ceiling.

    Mirrors the drop-shortest-same-sport-anchor rule the real injection
    applies, so the projected minimum matches what actually happens.

    Args:
        sessions: Regular sessions from :func:`session_generator`.
        week: Candidate placement week.
        sports: Sports to be tested that week.
        ceiling: The week's total hours ceiling, or ``None`` if unknown.

    Returns:
        ``True`` if the resulting minimum hours stay within *ceiling*
        (or *ceiling* is ``None``, i.e. unconstrained).
    """
    if ceiling is None:
        return True

    week_sessions = [s for s in sessions if s.week == week]
    total_min = sum(s.min_hours for s in week_sessions)
    for sport in sports:
        total_min += _TEST_HOURS[sport]
        anchors = sorted(
            (
                s
                for s in week_sessions
                if s.sport == sport and s.origin == SessionOrigin.ANCHOR
            ),
            key=lambda s: s.target_hours,
        )
        if anchors:
            total_min -= anchors[0].min_hours
    return total_min <= ceiling


__all__ = [
    "inject_test_sessions",
    "resolve_test_weeks",
]
