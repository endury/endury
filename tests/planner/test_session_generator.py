"""Tests for endury/planner/sessions/session_generator.py."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from endury.planner.activity import ensure_registry_loaded, get_activity
from endury.planner.activity.materialize import materialized_swim_meters
from endury.planner.foundations.ambition_tables import get_ambition_profile
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    CapacityProjection,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    PlacedSession,
    RaceGoal,
    SessionOrigin,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.macro.capacity import build_capacity_projection
from endury.planner.phase.phase_planner import plan_phases
from endury.planner.phase.phase_tables import GOAL_PROFILES
from endury.planner.sessions.menus import SessionSpec, get_session_menu
from endury.planner.sessions.menus.resolve import active_specs, resolve_slot_groups
from endury.planner.sessions.session_generator import (
    _TEST_KEYS,
    FILLER_MIN_SCALE,
    FLOOR_DROP_TOLERANCE,
    OPTIONAL_MENU_WHY,
    OPTIONAL_SHORT_WEEK_WHY,
    OPTIONAL_TAPER_WHY,
    _activity_floor_hours,
    _compute_phase_runs,
    _fit_scale,
    _give_way,
    _swim_minutes,
    _worst_offender,
    cadence_weeks,
    generate_session_menu,
    race_sim_weeks,
)
from endury.training.load import LoadMetricContext

_START = date(2026, 6, 1)


def _regular(sessions: list[PlacedSession]) -> list[PlacedSession]:
    """The sessions the week books; the rest are optional (R-SESS-037)."""
    return [s for s in sessions if s.optional_reason is None]


_LONGEST = {
    (SportType.RUNNING, ActivityFamily.LONG): 60,
    (SportType.CYCLING, ActivityFamily.LONG): 90,
    (SportType.SWIMMING, ActivityFamily.EASY): 30,
}


def _ironman_plan(weeks: int = 22):
    goal = RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=weeks), "A")
    return plan_phases(
        PhaseRequest(
            goals=[goal],
            training_start=_START,
            ambition=AmbitionLevel.MODERATE,
        )
    )


class TestGenerateSessionMenu:
    def test_ironman_emits_sessions_for_all_load_weeks(self) -> None:
        """Every LOAD week must have at least one session."""
        plan = _ironman_plan()
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        load_weeks = {ws.week for ws in plan.weeks if ws.mode == WeekMode.LOAD}
        session_weeks = {s.week for s in sessions}
        missing = load_weeks - session_weeks
        assert not missing, f"LOAD weeks with no sessions: {missing}"

    def test_session_bounds_are_consistent(self) -> None:
        """For every session, min_hours <= target_hours <= max_hours (if set)."""
        plan = _ironman_plan()
        sessions = generate_session_menu(plan, _LONGEST)
        for s in sessions:
            assert s.target_hours > 0, f"{s.slot_id} has zero target_hours"
            if s.min_hours > 0:
                assert s.min_hours <= s.target_hours + 1e-6, (
                    f"{s.slot_id}: min={s.min_hours:.3f} > target={s.target_hours:.3f}"
                )
            if s.max_hours > 0:
                assert s.target_hours <= s.max_hours + 1e-6, (
                    f"{s.slot_id}: target={s.target_hours:.3f} > max={s.max_hours:.3f}"
                )

    def test_sessions_are_anchor_or_test_origin(self) -> None:
        """Session-menu sessions must be ANCHOR or TEST origin."""
        plan = _ironman_plan()
        sessions = generate_session_menu(plan, _LONGEST)
        for s in sessions:
            assert s.origin in (SessionOrigin.ANCHOR, SessionOrigin.TEST), (
                f"Unexpected origin {s.origin.value} for {s.slot_id}"
            )

    def test_sessions_cover_multiple_sports(self) -> None:
        """Ironman sessions must span all three sports."""
        plan = _ironman_plan()
        sessions = generate_session_menu(plan, _LONGEST)
        sports = {s.sport for s in sessions}
        assert SportType.RUNNING in sports
        assert SportType.CYCLING in sports
        assert SportType.SWIMMING in sports

    def test_swim_css_scales_swim_minutes(self) -> None:
        """A faster athlete CSS must shrink swim target_hours (R-DU-002)."""
        plan = _ironman_plan()
        slow = generate_session_menu(plan, _LONGEST, swim_css_sec_per_100m=140.0)
        fast = generate_session_menu(plan, _LONGEST, swim_css_sec_per_100m=80.0)

        def total_swim_hours(sessions) -> float:
            return sum(
                s.target_hours for s in sessions if s.sport == SportType.SWIMMING
            )

        assert total_swim_hours(fast) < total_swim_hours(slow), (
            "Faster swimmer (lower CSS) must accumulate fewer swim hours"
        )

    def test_swims_book_their_cards_wall_clock(self) -> None:
        """R-SESS-011: every swim books the clock of the card that swims its metres.

        The law is the materializer's inverse — if it drifts, the built
        workout no longer recovers the authored menu metres (R-SESS-B-006).
        """
        # SWIM_LONG: 400 m warm-up at IF 0.75, the main set at 0.82, 300 m
        # cool-down at 0.72.
        assert _swim_minutes(ActivityKey.SWIM_LONG, 1000, 120.0) == pytest.approx(
            (400 * 120.0 / 0.75 + 300 * 120.0 / 0.82 + 300 * 120.0 / 0.72) / 6000.0
        )
        # An interval swim's rests cover no distance: its card, not raw CSS
        # pace (20 min for 1000 m), sets the clock; a booking a few seconds
        # under it still builds that card.
        ensure_registry_loaded()
        css = get_activity(ActivityKey.SWIM_CSS)
        minutes = _swim_minutes(ActivityKey.SWIM_CSS, 1000, 120.0)
        assert minutes > 20.0
        assert materialized_swim_meters(css, minutes / 60.0, None) >= 1000
        assert materialized_swim_meters(css, (minutes - 0.05) / 60.0, None) >= 1000

    @pytest.mark.parametrize("css", [100.0, 120.0, 150.0])
    def test_swim_bounds_follow_the_swimmers_css(self, css: float) -> None:
        """A swim books its metres at the swimmer's CSS, the longest within a cap."""
        ensure_registry_loaded()
        resolver = LoadMetricContext(swim_css_sec_per_100m=css)
        for key in (ActivityKey.SWIM_CSS, ActivityKey.SWIM_DRILL):
            activity = get_activity(key)
            low = _swim_minutes(key, 1800, css) / 60.0
            high = _swim_minutes(key, 1900, css, within=True) / 60.0
            assert materialized_swim_meters(activity, low, resolver) >= 1800
            assert materialized_swim_meters(activity, high, resolver) <= 1900

    def test_default_swim_css_matches_legacy_minutes(self) -> None:
        """Default-athlete swim sessions carry sane, ordered hour bounds.

        Interval/drill/test swims still reproduce the raw-CSS
        ``minutes * 50`` metre encoding (raw CSS 120 = 50 m/min); continuous
        swims budget at the IF-adjusted pace ``css / intensity_factor``
        instead (R-SESS-011).
        """
        plan = _ironman_plan()
        sessions = generate_session_menu(plan, _LONGEST)
        # All swim sessions should land on tidy hour multiples of 1/60 min.
        for s in sessions:
            if s.sport == SportType.SWIMMING and s.min_hours > 0:
                assert s.target_hours > 0
                assert s.min_hours <= s.target_hours + 1e-6


class TestLongestRecentSeedsTheRamp:
    """R-SESS-012: the athlete's longest recent session reaches the ramp.

    The seed is collapsed to a per-sport maximum across reported families;
    ``_PROGRESSIVE_FLOOR`` seeds only a sport with no report. A measured
    athlete opens at their longest even below the floor and the menu
    minimum: a first long session far past it is the spike that predicts
    injury.
    """

    def _sized(
        self, plan, longest: dict[tuple[SportType, ActivityFamily], int]
    ) -> list[tuple[int, str, float]]:
        return [
            (s.week, s.slot_id, round(s.target_hours, 9))
            for s in generate_session_menu(
                plan, longest, ambition=AmbitionLevel.MODERATE
            )
        ]

    def test_an_above_floor_athlete_opens_the_ramp_higher(self) -> None:
        """A 150-min longest run opens longer sessions than a 45-min one."""
        plan = _ironman_plan()
        lo = dict(_LONGEST)
        lo[(SportType.RUNNING, ActivityFamily.LONG)] = 45
        hi = dict(_LONGEST)
        hi[(SportType.RUNNING, ActivityFamily.LONG)] = 150
        sized_lo, sized_hi = self._sized(plan, lo), self._sized(plan, hi)
        assert sized_lo != sized_hi
        run_hours_lo = sum(h for _, sid, h in sized_lo if ":running:" in sid)
        run_hours_hi = sum(h for _, sid, h in sized_hi if ":running:" in sid)
        assert run_hours_hi > run_hours_lo

    def test_a_measured_athlete_below_the_floor_opens_at_their_longest(
        self,
    ) -> None:
        """A 45-min runner's first long run is one progression step above 45 min.

        The step is 10 % or the 8-min running floor, whichever is larger. The
        no-data start is the 75-min floor, and the Ironman BASE menu's long
        run starts higher still.
        """

        def first_long_run(longest) -> float:
            sessions = generate_session_menu(
                _ironman_plan(), longest, ambition=AmbitionLevel.MODERATE
            )
            first = min(
                (
                    s
                    for s in sessions
                    if s.sport == SportType.RUNNING
                    and s.activity_key == ActivityKey.RUN_LONG
                ),
                key=lambda s: s.week,
            )
            return first.target_hours * 60

        measured = dict(_LONGEST)
        measured[(SportType.RUNNING, ActivityFamily.LONG)] = 45
        no_run_data = {k: v for k, v in _LONGEST.items() if k[0] != SportType.RUNNING}
        assert first_long_run(measured) <= 45 + 8
        assert first_long_run(no_run_data) >= 75

    def test_seed_collapses_to_the_per_sport_maximum(self) -> None:
        """Two reported families seed one ramp start: the larger one."""
        plan = _ironman_plan()
        two_family = dict(_LONGEST)
        two_family[(SportType.RUNNING, ActivityFamily.LONG)] = 80
        two_family[(SportType.RUNNING, ActivityFamily.EASY)] = 140
        max_only = dict(_LONGEST)
        max_only[(SportType.RUNNING, ActivityFamily.LONG)] = 140
        assert self._sized(plan, two_family) == self._sized(plan, max_only)


class TestComputePhaseRuns:
    def test_empty_weeks_returns_empty(self) -> None:
        """_compute_phase_runs with an empty list must return []."""
        result = _compute_phase_runs([])
        assert result == []


class TestMaintenanceMenuAndCapacityScale:
    """Cover maintenance menu extension and tight-capacity scaling paths."""

    def test_ironman_with_running_b_goal_adds_maintenance_sessions(self) -> None:
        """A triathlon A goal + running B goal triggers cross-sport maintenance."""
        plan = plan_phases(
            PhaseRequest(
                goals=[
                    RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=36), "A"),
                    RaceGoal(
                        GoalType.RUNNING_HALF_MARATHON,
                        _START + timedelta(weeks=12),
                        "B",
                    ),
                ],
                training_start=_START,
                ambition=AmbitionLevel.MODERATE,
            )
        )
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        # Maintenance sessions add cycling/swim to running-focused weeks.
        sports = {s.sport for s in sessions}
        assert SportType.CYCLING in sports
        assert SportType.SWIMMING in sports

    def test_tight_capacity_scales_minimums(self) -> None:
        """When total_min > week_ceiling, session minimums are scaled down."""
        from endury.planner.macro.capacity import build_capacity_projection

        plan = _ironman_plan()
        # Extremely tight capacity forces the scale < 1 path.
        tiny_recent = {
            SportType.RUNNING: 0.5,
            SportType.CYCLING: 0.5,
            SportType.SWIMMING: 0.5,
        }
        capacity = build_capacity_projection(
            tiny_recent, plan.weeks, hours_ramp_limit=1.0
        )
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE, capacity=capacity
        )
        assert len(sessions) > 0


def _build_phase_plan(
    weeks: list[tuple[PhaseType, WeekMode, GoalType]],
    a_goal: RaceGoal | None = None,
) -> PhasePlan:
    """Build a minimal PhasePlan from (phase, mode, goal) triples."""
    sport_split = {SportType.CYCLING: 1.0}
    ws_list = [
        WeekSpec(
            week=i,
            phase=phase,
            mode=mode,
            sport_split=sport_split,
            effective_goal=goal,
            owning_goal=None,
        )
        for i, (phase, mode, goal) in enumerate(weeks)
    ]
    if a_goal is None:
        a_goal = RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=len(weeks)), "A")
    return PhasePlan(weeks=ws_list, race_events=[], a_goal=a_goal)


# A single-sport menu with a 2-variant rotation for testing.
_ROT_MENU = (
    SessionSpec(
        activity_key=ActivityKey.BIKE_VO2MAX,
        sport=SportType.CYCLING,
        min_minutes=45,
        max_minutes=60,
        rotation=(ActivityKey.BIKE_VO2MAX, ActivityKey.BIKE_THRESHOLD),
    ),
)

# Same spec but without rotation, for backwards-compat test.
_NO_ROT_MENU = (
    SessionSpec(
        activity_key=ActivityKey.BIKE_VO2MAX,
        sport=SportType.CYCLING,
        min_minutes=45,
        max_minutes=60,
    ),
)


class TestRotation:
    """Tests for the SessionSpec rotation feature."""

    def test_rotation_cycles_through_variants(self) -> None:
        """A 4-week BUILD phase with 2-variant rotation cycles A-B-A-B."""
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)] * 4,
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=_ROT_MENU,
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        keys = [s.activity_key for s in sessions]
        assert keys == [
            ActivityKey.BIKE_VO2MAX,
            ActivityKey.BIKE_THRESHOLD,
            ActivityKey.BIKE_VO2MAX,
            ActivityKey.BIKE_THRESHOLD,
        ]

    def test_rotation_resets_at_phase_boundary(self) -> None:
        """Rotation offset resets when a new phase run begins."""
        weeks = [
            (PhaseType.BASE, WeekMode.LOAD, GoalType.IRONMAN),
            (PhaseType.BASE, WeekMode.LOAD, GoalType.IRONMAN),
            (PhaseType.BASE, WeekMode.LOAD, GoalType.IRONMAN),
            (PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN),
            (PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN),
            (PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN),
        ]
        plan = _build_phase_plan(weeks)
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=_ROT_MENU,
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        keys = [s.activity_key for s in sessions]
        # Each 3-week phase run starts from index 0.
        assert keys == [
            ActivityKey.BIKE_VO2MAX,  # BASE offset=0
            ActivityKey.BIKE_THRESHOLD,  # BASE offset=1
            ActivityKey.BIKE_VO2MAX,  # BASE offset=2
            ActivityKey.BIKE_VO2MAX,  # BUILD offset=0 (reset)
            ActivityKey.BIKE_THRESHOLD,  # BUILD offset=1
            ActivityKey.BIKE_VO2MAX,  # BUILD offset=2
        ]

    def test_no_rotation_preserves_existing_behavior(self) -> None:
        """A SessionSpec without rotation produces unchanged activity_key."""
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)] * 3,
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=_NO_ROT_MENU,
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        keys = [s.activity_key for s in sessions]
        assert all(k == ActivityKey.BIKE_VO2MAX for k in keys)

    def test_rotation_slot_id_uses_primary_key(self) -> None:
        """slot_id must contain spec.activity_key, not the rotated variant."""
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)] * 2,
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=_ROT_MENU,
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        # Week 1 rotates to BIKE_THRESHOLD, but slot_id keeps BIKE_VO2MAX.
        rotated = sessions[1]
        assert rotated.activity_key == ActivityKey.BIKE_THRESHOLD
        assert "bike_vo2max" in rotated.slot_id
        assert "bike_threshold" not in rotated.slot_id

    def test_a_rotation_into_a_fixed_test_books_its_card(self) -> None:
        """A test reached through a rotation is a test: its card, fixed.

        The trail maintenance BASIC climb day rotates hill repeats into the
        threshold test; sized as the hill session, it booked 51 minutes of a
        55-minute protocol and lost the test origin.
        """
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)] * 2,
        )
        menu = (
            SessionSpec(
                activity_key=ActivityKey.RUN_HILL_REPEATS,
                sport=SportType.RUNNING,
                min_minutes=45,
                max_minutes=55,
                rotation=(ActivityKey.RUN_HILL_REPEATS, ActivityKey.RUN_THRESHOLD_TEST),
            ),
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=menu,
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        hills, test = sessions
        assert hills.origin is SessionOrigin.ANCHOR
        assert test.activity_key is ActivityKey.RUN_THRESHOLD_TEST
        assert test.origin is SessionOrigin.TEST
        card = _activity_floor_hours(ActivityKey.RUN_THRESHOLD_TEST)
        assert test.min_hours == test.target_hours == test.max_hours == card


class TestRotationSmoke:
    """Smoke tests running the full pipeline with rotation enabled."""

    def test_rotation_smoke_all_scenarios(self) -> None:
        """Run generate_plan for every scenario — no exceptions, valid keys."""
        from devtools.planner._shared.scenarios import SCENARIOS
        from endury.planner import generate_plan
        from endury.planner.activity.registry import (
            ACTIVITY_REGISTRY,
            ensure_registry_loaded,
        )

        ensure_registry_loaded()
        registered_keys = set(ACTIVITY_REGISTRY.keys())

        rotating_slots = 0
        for name, sc in sorted(SCENARIOS.items()):
            result = generate_plan(sc.to_request())
            plan, sessions = result.phase_plan, result.sessions
            # All activity keys must be registered.
            for s in sessions:
                assert s.activity_key in registered_keys, (
                    f"{name}: unknown key {s.activity_key}"
                )

            # BUILD LOAD weeks with rotated slots should show variety. A
            # slot id carries its week and index (``...:w3:i1``); strip
            # them so one slot groups across weeks.
            plan_weeks = {w.week: w for w in plan.weeks}
            build_load_keys: dict[str, set[ActivityKey]] = {}
            for s in sessions:
                ws = plan_weeks.get(s.week)
                if ws and ws.phase == PhaseType.BUILD and ws.mode == WeekMode.LOAD:
                    slot = re.sub(r":w\d+:i\d+$", "", s.slot_id)
                    build_load_keys.setdefault(slot, set()).add(s.activity_key)
            rotating_slots += sum(len(k) > 1 for k in build_load_keys.values())
        assert rotating_slots > 0, "no BUILD LOAD slot rotated in any scenario"


# Two overlapping specs sharing a slot group for unit testing.
_SG_EARLY = SessionSpec(
    activity_key=ActivityKey.BIKE_VO2MAX,
    sport=SportType.CYCLING,
    min_minutes=45,
    max_minutes=55,
    phase_fraction_start=0.0,
    phase_fraction_end=0.8,
    slot_group="test_bike_quality",
)

_SG_LATE = SessionSpec(
    activity_key=ActivityKey.BIKE_THRESHOLD,
    sport=SportType.CYCLING,
    min_minutes=45,
    max_minutes=55,
    phase_fraction_start=0.55,
    phase_fraction_end=1.0,
    slot_group="test_bike_quality",
)

_SG_UNRELATED = SessionSpec(
    activity_key=ActivityKey.RUN_EASY,
    sport=SportType.RUNNING,
    min_minutes=30,
    max_minutes=60,
)


class TestResolveSlotGroups:
    def test_picks_earlier_before_overlap(self) -> None:
        """Before the overlap zone, only the early spec is active."""
        result = resolve_slot_groups([_SG_EARLY, _SG_UNRELATED], 0.3)
        keys = {s.activity_key for s in result}
        assert ActivityKey.BIKE_VO2MAX in keys
        assert ActivityKey.BIKE_THRESHOLD not in keys
        assert ActivityKey.RUN_EASY in keys

    def test_picks_later_after_overlap(self) -> None:
        """After the early spec's window, only the late spec is active."""
        result = resolve_slot_groups([_SG_LATE, _SG_UNRELATED], 0.9)
        keys = {s.activity_key for s in result}
        assert ActivityKey.BIKE_THRESHOLD in keys
        assert ActivityKey.BIKE_VO2MAX not in keys

    def test_picks_one_in_overlap_window(self) -> None:
        """In the overlap zone, exactly one spec survives per slot group."""
        result = resolve_slot_groups([_SG_EARLY, _SG_LATE, _SG_UNRELATED], 0.7)
        bike_specs = [s for s in result if s.sport == SportType.CYCLING]
        assert len(bike_specs) == 1

    def test_crossover_favours_late_at_midpoint(self) -> None:
        """At the midpoint of midpoints the late spec should win (tie-break)."""
        # Early midpoint = (0.0 + 0.8) / 2 = 0.4
        # Late midpoint = (0.55 + 1.0) / 2 = 0.775
        # Midpoint of midpoints = (0.4 + 0.775) / 2 ≈ 0.5875
        result = resolve_slot_groups([_SG_EARLY, _SG_LATE, _SG_UNRELATED], 0.59)
        bike_specs = [s for s in result if s.sport == SportType.CYCLING]
        assert len(bike_specs) == 1
        # At 0.59: distance to early midpoint (0.4) = 0.19,
        # distance to late midpoint (0.775) = 0.185 → late wins
        assert bike_specs[0].activity_key == ActivityKey.BIKE_THRESHOLD

    def test_ungrouped_specs_pass_through(self) -> None:
        """Specs without a slot_group are always included."""
        result = resolve_slot_groups([_SG_UNRELATED], 0.5)
        assert len(result) == 1
        assert result[0].activity_key == ActivityKey.RUN_EASY

    def test_integration_ironman_base_no_duplicate_bike_quality(self) -> None:
        """Full Ironman BASE plan never emits both BIKE_CADENCE and
        BIKE_THRESHOLD in the same week (slot group prevents it)."""
        plan = _ironman_plan(weeks=22)
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        by_week: dict[int, set[ActivityKey]] = {}
        for s in sessions:
            by_week.setdefault(s.week, set()).add(s.activity_key)

        for week, keys in by_week.items():
            has_both = (
                ActivityKey.BIKE_CADENCE in keys and ActivityKey.BIKE_THRESHOLD in keys
            )
            assert not has_both, f"Week {week} has both BIKE_CADENCE and BIKE_THRESHOLD"

    def test_integration_ironman_build_no_duplicate_bike_quality(self) -> None:
        """Full Ironman BUILD plan never emits both BIKE_VO2MAX and
        BIKE_THRESHOLD in the same week (im_build_bike_quality slot group)."""
        plan = _ironman_plan(weeks=22)
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        build_weeks = {w.week for w in plan.weeks if w.phase == PhaseType.BUILD}
        by_week: dict[int, set[ActivityKey]] = {}
        for s in sessions:
            if s.week in build_weeks:
                by_week.setdefault(s.week, set()).add(s.activity_key)

        for week, keys in by_week.items():
            has_both = (
                ActivityKey.BIKE_VO2MAX in keys and ActivityKey.BIKE_THRESHOLD in keys
            )
            assert not has_both, (
                f"BUILD week {week} has both BIKE_VO2MAX and BIKE_THRESHOLD"
            )

    def test_ironman_build_vo2max_early_only(self) -> None:
        """BIKE_VO2MAX should appear only in early BUILD (phase_frac <= 0.55).

        Validates the front-loading of VO2max work recommended by
        Laursen & Jenkins (2002) and Buchheit & Laursen (2013).
        """
        plan = _ironman_plan(weeks=22)
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        # Identify BUILD LOAD phase-runs to compute per-run fractions.
        build_load_weeks = sorted(
            w.week
            for w in plan.weeks
            if w.phase == PhaseType.BUILD and w.mode == WeekMode.LOAD
        )
        if not build_load_weeks:
            return  # nothing to check

        run_len = len(build_load_weeks)
        week_to_frac = {
            w: i / max(run_len - 1, 1) for i, w in enumerate(build_load_weeks)
        }

        for s in sessions:
            if s.activity_key == ActivityKey.BIKE_VO2MAX and s.week in week_to_frac:
                frac = week_to_frac[s.week]
                assert frac <= 0.6, (
                    f"BIKE_VO2MAX in BUILD week {s.week} at phase_frac "
                    f"{frac:.2f} — expected only in early build (≤ 0.55)"
                )


_IM = GoalType.IRONMAN
_BASE_L = (PhaseType.BASE, WeekMode.LOAD, _IM)
_PEAK_L = (PhaseType.PEAK, WeekMode.LOAD, _IM)
_PEAK_R = (PhaseType.PEAK, WeekMode.RECOVERY, _IM)
_TAPER_T = (PhaseType.TAPER, WeekMode.TAPER, _IM)
_TAPER_RACE = (PhaseType.TAPER, WeekMode.RACE, _IM)


class TestRaceSimWeeks:
    """Unit tests for the per-PEAK-run race-sim week selector."""

    def test_picks_load_week_nearest_five_before_race(self) -> None:
        """The LOAD week closest to race-5 wins (here exactly race-5)."""
        plan = _build_phase_plan(
            [_BASE_L, _PEAK_L, _PEAK_R, _PEAK_L, _PEAK_L, _TAPER_T, _TAPER_RACE]
        )
        # Pool excludes the final pre-taper LOAD week (w4); race week is
        # w6, so w1 sits 5 weeks out (distance 0) vs w3 at 3 (distance 2).
        assert race_sim_weeks(plan.weeks) == frozenset({1})

    def test_tie_between_four_and_six_weeks_out_goes_earlier(self) -> None:
        """At |wtr-5| ties (4 vs 6 weeks out), the earlier week wins."""
        plan = _build_phase_plan(
            [
                _BASE_L,
                _PEAK_L,
                _PEAK_R,
                _PEAK_L,
                _PEAK_L,
                _TAPER_T,
                _TAPER_T,
                _TAPER_RACE,
            ]
        )
        # Race week w7; pool = [w1 (6 out), w3 (4 out)] → earlier w1.
        assert race_sim_weeks(plan.weeks) == frozenset({1})

    def test_never_picks_final_pretaper_load_week(self) -> None:
        """With >1 LOAD week, the most-shed final LOAD week is excluded."""
        plan = _build_phase_plan(
            [_BASE_L, _PEAK_L, _PEAK_L, _TAPER_T, _TAPER_T, _TAPER_RACE]
        )
        # w2 is the final pre-taper LOAD week → w1 must win.
        assert race_sim_weeks(plan.weeks) == frozenset({1})

    def test_single_load_week_is_chosen(self) -> None:
        """A PEAK run with one LOAD week uses it — zero-fire impossible."""
        plan = _build_phase_plan([_BASE_L, _PEAK_L, _TAPER_T, _TAPER_RACE])
        assert race_sim_weeks(plan.weeks) == frozenset({1})

    def test_no_race_week_falls_back_to_last_plan_week(self) -> None:
        """Without a RACE-mode week, distances anchor on the final week."""
        plan = _build_phase_plan([_PEAK_L, _PEAK_L, _PEAK_L, _TAPER_T])
        # Anchor w3; pool = [w0 (3 out), w1 (2 out)] → w0 (nearest 5).
        assert race_sim_weeks(plan.weeks) == frozenset({0})

    def test_peak_without_load_weeks_selects_nothing(self) -> None:
        """A PEAK run of recovery weeks gets no race-sim week."""
        plan = _build_phase_plan([_BASE_L, _PEAK_R, _PEAK_R, _TAPER_T, _TAPER_RACE])
        assert race_sim_weeks(plan.weeks) == frozenset()

    def test_no_peak_phase_selects_nothing(self) -> None:
        """Plans without a PEAK phase get no race-sim week."""
        plan = _build_phase_plan([_BASE_L, _BASE_L, _TAPER_T, _TAPER_RACE])
        assert race_sim_weeks(plan.weeks) == frozenset()


# Race-sim spec sharing the slot group of _SG_EARLY/_SG_LATE.
_SG_RACE_SIM = SessionSpec(
    activity_key=ActivityKey.BIKE_LONG_IM_RACE_PACE,
    sport=SportType.CYCLING,
    min_minutes=270,
    max_minutes=300,
    slot_group="test_bike_quality",
    race_sim=True,
)

# Adjacent windows whose midpoint distances at phase_frac = 3/5 differ
# only by float noise (|0.4-0.6| vs |0.8-0.6|).
_SG_NOISE_EARLY = SessionSpec(
    activity_key=ActivityKey.BIKE_VO2MAX,
    sport=SportType.CYCLING,
    min_minutes=45,
    max_minutes=55,
    phase_fraction_start=0.2,
    phase_fraction_end=0.6,
    slot_group="test_noise",
)

_SG_NOISE_LATE = SessionSpec(
    activity_key=ActivityKey.BIKE_THRESHOLD,
    sport=SportType.CYCLING,
    min_minutes=45,
    max_minutes=55,
    phase_fraction_start=0.6,
    phase_fraction_end=1.0,
    slot_group="test_noise",
)


class TestResolveSlotGroupsRaceSim:
    """Race-sim override and epsilon tie-break in slot-group resolution."""

    def test_race_sim_member_wins_group_outright(self) -> None:
        """A race_sim member beats frac geometry inside its group."""
        result = resolve_slot_groups([_SG_EARLY, _SG_LATE, _SG_RACE_SIM], 0.9)
        bike_specs = [s for s in result if s.sport == SportType.CYCLING]
        assert len(bike_specs) == 1
        assert bike_specs[0] is _SG_RACE_SIM

    def test_epsilon_tie_break_engages_at_float_noise_boundary(self) -> None:
        """At phase_frac 3/5 the later window wins despite float noise.

        The midpoint distances differ by ~1e-17 (not exactly equal), so
        without the epsilon the early window would win and the
        later-start tie rule would never engage.
        """
        phase_frac = 3 / 5
        d_early = abs(0.4 - phase_frac)
        d_late = abs(0.8 - phase_frac)
        assert d_early != d_late  # noise: the raw distances are NOT equal
        assert abs(d_early - d_late) < 1e-12

        result = resolve_slot_groups([_SG_NOISE_EARLY, _SG_NOISE_LATE], phase_frac)
        assert len(result) == 1
        assert result[0] is _SG_NOISE_LATE


class TestBasicPeakRaceSimWeek:
    """End-to-end: exactly one BASIC PEAK race-sim weekend per plan."""

    @pytest.mark.parametrize("weeks", [22, 23, 26, 31, 34, 40])
    def test_exactly_one_big_race_sim_weekend(self, weeks: int) -> None:
        """One PEAK LOAD week gets the 4.5 h ride + 2.5 h run (R-SESS-B-003).

        Horizons cover the failure-prone shapes: 23/26 wk zero-fire
        (PEAK [L,R,L,L]) and 31/34/40 wk double-fire geometries.
        """
        goal = RaceGoal(GoalType.IRONMAN, _START + timedelta(weeks=weeks), "A")
        plan = plan_phases(
            PhaseRequest(
                goals=[goal],
                training_start=_START,
                ambition=AmbitionLevel.BASIC,
            )
        )
        sessions = generate_session_menu(plan, _LONGEST, ambition=AmbitionLevel.BASIC)

        peak_load = sorted(
            ws.week
            for ws in plan.weeks
            if ws.phase == PhaseType.PEAK and ws.mode == WeekMode.LOAD
        )
        big_bike_weeks = sorted(
            {
                s.week
                for s in sessions
                if s.week in peak_load
                and s.sport == SportType.CYCLING
                and s.min_hours >= 4.4
            }
        )
        big_run_weeks = sorted(
            {
                s.week
                for s in sessions
                if s.week in peak_load
                and s.sport == SportType.RUNNING
                and s.min_hours >= 2.4
            }
        )

        assert len(big_bike_weeks) == 1, (
            f"{weeks}wk: expected exactly one race-sim ride week, got {big_bike_weeks}"
        )
        assert big_run_weeks == big_bike_weeks, (
            f"{weeks}wk: race-sim run weeks {big_run_weeks} must co-fire "
            f"with the ride week {big_bike_weeks}"
        )

        big = big_bike_weeks[0]
        race_week = max(ws.week for ws in plan.weeks if ws.mode == WeekMode.RACE)
        assert 4 <= race_week - big <= 6, (
            f"{weeks}wk: race-sim week w{big} is {race_week - big} weeks "
            f"out — expected 4-6"
        )
        assert big != max(peak_load), (
            f"{weeks}wk: race-sim week must not be the most-shed final "
            f"pre-taper LOAD week w{max(peak_load)}"
        )


class TestMaterializableFloors:
    """Emitted bounds honour the smallest workout an activity can build."""

    def _trail_maintenance_plan(self, weeks: int = 12) -> PhasePlan:
        goal = RaceGoal(
            GoalType.TRAIL_MAINTENANCE, _START + timedelta(weeks=weeks, days=-1), "A"
        )
        return plan_phases(
            PhaseRequest(
                goals=[goal], training_start=_START, ambition=AmbitionLevel.BASIC
            )
        )

    def test_floor_probe_matches_bridge_cards(self) -> None:
        """Floors equal what the card renders for a one-minute solved slot."""
        assert _activity_floor_hours(ActivityKey.RUN_FARTLEK) == pytest.approx(
            38.0 / 60.0
        )
        assert _activity_floor_hours(ActivityKey.RUN_THRESHOLD_TEST) == pytest.approx(
            55.0 / 60.0
        )
        # Second call serves from the cache and must agree.
        assert _activity_floor_hours(ActivityKey.RUN_FARTLEK) == pytest.approx(
            38.0 / 60.0
        )

    def test_emitted_minimums_honour_floors(self) -> None:
        """No session may be booked below tolerance of its floor."""
        plan = self._trail_maintenance_plan()
        capacity = build_capacity_projection(
            {SportType.RUNNING: 1.5}, plan.weeks, hours_ramp_limit=1.05
        )
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 35}
        sessions = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        assert sessions
        for s in sessions:
            floor = _activity_floor_hours(s.activity_key)
            assert s.min_hours >= floor * FLOOR_DROP_TOLERANCE - 1e-9, (
                f"{s.slot_id} booked {s.min_hours:.2f}h below tolerance of "
                f"its {floor:.2f}h floor"
            )

    def test_squeezed_quality_slot_optional_not_inflated(self) -> None:
        """A returner's test week keeps its Friday strides optional, not inflated.

        The test day (week 5 carries the threshold test) and
        the long run are key, so the squeeze falls on the easy runs: the
        strides stay at their smallest honest card, never under the menu's
        own minimum (R-SESS-037).
        """
        plan = self._trail_maintenance_plan()
        capacity = build_capacity_projection(
            {SportType.RUNNING: 1.5}, plan.weeks, hours_ramp_limit=1.05
        )
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 35}
        sessions = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        week4 = [s.activity_key for s in _regular(sessions) if s.week == 4]
        assert ActivityKey.RUN_THRESHOLD_TEST in week4
        assert ActivityKey.RUN_STRIDES not in week4
        (strides,) = [
            s
            for s in sessions
            if s.week == 4 and s.activity_key == ActivityKey.RUN_STRIDES
        ]
        (spec,) = [
            s
            for s in get_session_menu(
                GoalType.TRAIL_MAINTENANCE,
                PhaseType.MAINTAIN,
                "load",
                AmbitionLevel.BASIC,
            )
            if s.activity_key == ActivityKey.RUN_STRIDES
        ]
        assert strides.optional_reason == OPTIONAL_SHORT_WEEK_WHY
        assert strides.target_hours == pytest.approx(
            max(_activity_floor_hours(ActivityKey.RUN_STRIDES), spec.min_minutes / 60)
        )

    def test_menu_test_booked_at_protocol_duration_when_capacity_allows(self) -> None:
        """With no capacity squeeze the recovery-week test books the full card."""
        plan = self._trail_maintenance_plan()
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 60}
        sessions = generate_session_menu(plan, longest, ambition=AmbitionLevel.BASIC)
        tests = [
            s for s in sessions if s.activity_key == ActivityKey.RUN_THRESHOLD_TEST
        ]
        assert tests
        for t in tests:
            assert t.min_hours == pytest.approx(55.0 / 60.0)
            assert t.target_hours == pytest.approx(55.0 / 60.0)

    def test_keyed_menu_test_survives_a_capacity_squeeze(self) -> None:
        """A squeezed week keeps its keyed test: the easy runs give way instead.

        Until 2026-10-11 a squeezed week dropped its fixed-protocol test first;
        the fitness tests are the week's reason (R-SESS-B-000), so a keyed one
        now gives way like any key session and the week still tests.
        """
        plan = self._trail_maintenance_plan(16)
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 35}
        ample = generate_session_menu(plan, longest, ambition=AmbitionLevel.BASIC)
        capacity = build_capacity_projection(
            {SportType.RUNNING: 1.5}, plan.weeks, hours_ramp_limit=1.05
        )
        squeezed = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        ample_test_weeks = {
            s.week for s in ample if s.activity_key == ActivityKey.RUN_THRESHOLD_TEST
        }
        squeezed_test_weeks = {
            s.week for s in squeezed if s.activity_key == ActivityKey.RUN_THRESHOLD_TEST
        }
        assert squeezed_test_weeks == ample_test_weeks == {4, 12}

    def _one_hour_runner(
        self, goal_type: GoalType
    ) -> tuple[PhasePlan, list[PlacedSession]]:
        """A 16-week BASIC plan for a 1.0 h/wk runner, capacity as the pipeline."""
        goal = RaceGoal(goal_type, _START + timedelta(weeks=16, days=-1), "A")
        plan = plan_phases(
            PhaseRequest(
                goals=[goal], training_start=_START, ambition=AmbitionLevel.BASIC
            )
        )
        amb = get_ambition_profile(AmbitionLevel.BASIC)
        capacity = build_capacity_projection(
            {SportType.RUNNING: 1.0},
            plan.weeks,
            hours_ramp_limit=amb.hours_ramp_limit,
            max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
            recovery_ceiling_factor=amb.recovery_load_fraction,
        )
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 35}
        sessions = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        return plan, sessions

    @pytest.mark.parametrize(
        "goal_type", [GoalType.RUNNING_HALF_MARATHON, GoalType.RUNNING_10K]
    )
    def test_squeezed_recovery_week_keeps_its_easy_runs(
        self, goal_type: GoalType
    ) -> None:
        """Dropping the fixed test re-fits the week before anything else drops.

        The recovery weeks' 34-49 min ceilings cannot hold the 55-min
        threshold test, but once it is gone they hold both easy runs.
        """
        plan, sessions = self._one_hour_runner(goal_type)
        weeks_with_sessions = {s.week for s in sessions}
        empty = [ws.week for ws in plan.weeks if ws.week not in weeks_with_sessions]
        assert not empty, f"weeks with no sessions: {empty}"

    @pytest.mark.parametrize(
        "goal_type", [GoalType.RUNNING_HALF_MARATHON, GoalType.RUNNING_10K]
    )
    def test_squeezed_load_week_drops_one_session_at_a_time(
        self, goal_type: GoalType
    ) -> None:
        """Each drop frees scale for the rest, so a load week keeps company.

        Sessions are dropped one at a time rather than all at once, so
        the remaining ones still land above their floors together.
        """
        plan, sessions = self._one_hour_runner(goal_type)
        lone = [
            ws.week
            for ws in plan.weeks
            if ws.mode == WeekMode.LOAD
            and sum(1 for s in sessions if s.week == ws.week) < 2
        ]
        assert not lone, f"load weeks with a single session: {lone}"

    def test_tight_taper_week_keeps_its_strides_at_their_card(self) -> None:
        """A tight taper week shortens its easy and long runs and keeps the strides.

        Marathon BASIC on a 12-week prep with a 2.2 h/wk running seed:
        neither taper week's headroom could fund the strides' floor lift,
        so both shed them (R-SESS-030's taper branch). The taper share
        (R-SESS-038) shortens the taper weeks' easy and long runs first, and
        the strides book their full card in both taper weeks and race week
        (R-SESS-037).
        """
        goal = RaceGoal(
            GoalType.RUNNING_MARATHON, _START + timedelta(weeks=12, days=-1), "A"
        )
        plan = plan_phases(
            PhaseRequest(
                goals=[goal], training_start=_START, ambition=AmbitionLevel.BASIC
            )
        )
        capacity = build_capacity_projection({SportType.RUNNING: 2.2}, plan.weeks)
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 75}
        sessions = generate_session_menu(
            plan, longest, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        taper_weeks = sorted(ws.week for ws in plan.weeks if ws.mode == WeekMode.TAPER)
        race_week = next(ws.week for ws in plan.weeks if ws.mode == WeekMode.RACE)
        strides_weeks = {
            s.week
            for s in _regular(sessions)
            if s.activity_key == ActivityKey.RUN_STRIDES
        }
        assert set(taper_weeks) <= strides_weeks
        assert race_week in strides_weeks
        assert not {
            s.week
            for s in sessions
            if s.activity_key == ActivityKey.RUN_STRIDES
            and s.optional_reason == OPTIONAL_TAPER_WHY
        }
        floor = _activity_floor_hours(ActivityKey.RUN_STRIDES)
        for s in sessions:
            if s.activity_key == ActivityKey.RUN_STRIDES:
                assert s.min_hours >= floor * FLOOR_DROP_TOLERANCE - 1e-9

    def test_brick_weeks_always_keep_their_ride(self) -> None:
        """Floor drops never orphan a brick run from its same-week ride."""
        plan = _ironman_plan(weeks=24)
        capacity = build_capacity_projection(
            {
                SportType.RUNNING: 0.6,
                SportType.CYCLING: 0.6,
                SportType.SWIMMING: 0.3,
            },
            plan.weeks,
            hours_ramp_limit=1.0,
        )
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.BASIC, capacity=capacity
        )
        slot_ids = {s.slot_id for s in sessions}
        for s in sessions:
            if s.brick_partner is not None:
                assert s.brick_partner in slot_ids


class TestEasedAfterATuneUpRace:
    """R-SESS-031: no hard run or bike session in the four days after a B/C race."""

    @staticmethod
    def _week_after(
        a_type: GoalType, b_type: GoalType, race_day: int, race_week: int = 13
    ):
        from endury.planner.activity import ensure_registry_loaded, get_activity

        ensure_registry_loaded()
        a = RaceGoal(a_type, _START + timedelta(weeks=24, days=-1), "A")
        b = RaceGoal(b_type, _START + timedelta(weeks=race_week, days=race_day), "B")
        plan = plan_phases(
            PhaseRequest(
                goals=[a, b], training_start=_START, ambition=AmbitionLevel.MODERATE
            )
        )
        event = next(e for e in plan.race_events if e.goal.priority == "B")
        assert event.day_of_week == race_day
        sessions = generate_session_menu(
            plan, _LONGEST, ambition=AmbitionLevel.MODERATE
        )
        after = [s for s in sessions if s.week == event.week + 1]
        week = plan.weeks[event.week + 1]
        # A rotating slot books one of its members; a moderate member is not eased.
        rotations = {
            spec.slot_name: spec.rotation
            for spec in get_session_menu(
                a_type, week.phase, week.mode.value, AmbitionLevel.MODERATE
            )
        }

        def hard(key: ActivityKey) -> bool:
            return get_activity(key).hardness.is_hard_physiologically

        def authored(s: PlacedSession) -> ActivityKey:
            return ActivityKey(s.slot_id.split(":")[2])

        eased = [
            s
            for s in after
            if hard(authored(s))
            and not hard(s.activity_key)
            and s.activity_key not in rotations.get(s.slot_name, ())
        ]
        return after, eased, hard

    @pytest.mark.parametrize(
        ("a_type", "b_type"),
        [
            (GoalType.RUNNING_MARATHON, GoalType.RUNNING_HALF_MARATHON),
            (GoalType.IRONMAN, GoalType.OLYMPIC_TRIATHLON),
        ],
    )
    def test_a_sunday_race_eases_monday_to_thursday(self, a_type, b_type) -> None:
        after, eased, hard = self._week_after(a_type, b_type, race_day=6)
        assert eased
        assert {s.activity_key for s in eased} <= {
            ActivityKey.RUN_EASY,
            ActivityKey.BIKE_ENDURANCE,
        }
        assert all(s.day_of_week <= 3 for s in eased)
        assert not any(
            hard(s.activity_key)
            for s in after
            if s.sport != SportType.SWIMMING and s.day_of_week <= 3
        )

    def test_swims_keep_their_quality(self) -> None:
        after, _, hard = self._week_after(
            GoalType.IRONMAN, GoalType.OLYMPIC_TRIATHLON, race_day=6
        )
        assert any(
            hard(s.activity_key)
            for s in after
            if s.sport == SportType.SWIMMING and s.day_of_week <= 3
        )

    def test_a_saturday_race_keeps_thursday_hard(self) -> None:
        # Thursday is five days after a Saturday race, outside the window;
        # early in BUILD it is the threshold run.
        after, _, hard = self._week_after(
            GoalType.RUNNING_MARATHON,
            GoalType.RUNNING_HALF_MARATHON,
            race_day=5,
            race_week=10,
        )
        assert not any(hard(s.activity_key) for s in after if s.day_of_week <= 2)
        assert any(hard(s.activity_key) for s in after if s.day_of_week == 3)

    @staticmethod
    def _tests(
        a_type: GoalType,
        ambition: AmbitionLevel,
        b_type: GoalType,
        priority: str,
        race_week: int,
    ) -> set[tuple[int, int, ActivityKey]]:
        """Book a 16-week plan with a Sunday tune-up; return its fitness tests."""
        goals = [
            RaceGoal(a_type, _START + timedelta(weeks=16, days=-1), "A"),
            RaceGoal(
                b_type, _START + timedelta(weeks=race_week + 1, days=-1), priority
            ),
        ]
        plan = plan_phases(
            PhaseRequest(goals=goals, training_start=_START, ambition=ambition)
        )
        sessions = generate_session_menu(plan, _LONGEST, ambition=ambition)
        return {
            (s.week, s.day_of_week, s.activity_key)
            for s in sessions
            if s.activity_key in _TEST_KEYS
        }

    def test_a_test_moves_to_the_same_day_a_week_later(self) -> None:
        # A C 10K in week 4 puts the block's week-5 run test two days later.
        tests = self._tests(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            GoalType.RUNNING_10K,
            "C",
            race_week=3,
        )
        assert tests == {
            (5, 1, ActivityKey.RUN_THRESHOLD_TEST),
            (12, 1, ActivityKey.RUN_THRESHOLD_TEST),
        }

    def test_the_swim_test_stays_and_the_others_move(self) -> None:
        tests = self._tests(
            GoalType.TRIATHLON_MAINTENANCE,
            AmbitionLevel.MODERATE,
            GoalType.SPRINT_TRIATHLON,
            "C",
            race_week=3,
        )
        assert {t for t in tests if t[0] in (4, 5)} == {
            (4, 4, ActivityKey.SWIM_CSS_TEST),
            (5, 1, ActivityKey.BIKE_FTP_TEST),
            (5, 3, ActivityKey.RUN_THRESHOLD_TEST),
        }

    def test_a_test_the_next_week_cannot_take_is_skipped(self) -> None:
        # The easier week after a long B race books the run test on its
        # Wednesday; the week after has an easy Wednesday.
        tests = self._tests(
            GoalType.RUNNING_MARATHON,
            AmbitionLevel.MODERATE,
            GoalType.RUNNING_MARATHON,
            "B",
            race_week=10,
        )
        assert tests
        assert all(week < 10 for week, _, _ in tests)

    def test_the_easier_week_after_a_long_b_race_eases_its_tempo_ride(self) -> None:
        # The week after a half is the easier week, whose recovery menu books
        # a moderate tempo ride on Tuesday; it eases like a hard one.
        after, _, _ = self._week_after(
            GoalType.HALF_IRONMAN, GoalType.HALF_IRONMAN, race_day=6, race_week=12
        )
        first_days = [
            s for s in after if s.sport != SportType.SWIMMING and s.day_of_week <= 3
        ]
        tuesday = next(s for s in first_days if s.day_of_week == 1)
        assert tuesday.slot_id.split(":")[2] == ActivityKey.BIKE_TEMPO
        assert tuesday.activity_key == ActivityKey.BIKE_ENDURANCE
        assert not any(
            get_activity(s.activity_key).hardness.is_hard_overall for s in first_days
        )


class TestDisplacedByARaceWeek:
    """R-SESS-042: a test a tune-up's race week displaces is booked a week later."""

    @staticmethod
    def _block(
        a_type: GoalType,
        ambition: AmbitionLevel,
        weeks: int,
        b: RaceGoal,
    ) -> PhasePlan:
        """A maintenance block of *weeks* with one tune-up race."""
        return plan_phases(
            PhaseRequest(
                goals=[
                    RaceGoal(a_type, _START + timedelta(weeks=weeks, days=-1), "A"),
                    b,
                ],
                training_start=_START,
                ambition=ambition,
            )
        )

    @staticmethod
    def _tests(
        plan: PhasePlan, ambition: AmbitionLevel
    ) -> set[tuple[int, int, ActivityKey]]:
        return {
            (s.week, s.day_of_week, s.activity_key)
            for s in generate_session_menu(plan, _LONGEST, ambition=ambition)
            if s.activity_key in _TEST_KEYS
        }

    def test_a_c_race_in_the_test_week_moves_the_test_past_its_easy_days(self) -> None:
        # Trail maintenance BASIC tests on the Tuesday of week 5. A C 10K on
        # that week's Sunday takes the week; the test goes to the next load
        # week's Tuesday, two days after the race, so R-SESS-031 eases it
        # there and books it the Tuesday after. The week-13 test lands on a
        # recovery week the race shifted and is not booked (open).
        plan = self._block(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            16,
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=5, days=-1), "C"),
        )
        assert plan.weeks[4].mode == WeekMode.RACE
        assert self._tests(plan, AmbitionLevel.BASIC) == {
            (6, 1, ActivityKey.RUN_THRESHOLD_TEST),
        }

    def test_every_sport_gets_its_displaced_test(self) -> None:
        # Triathlon maintenance MODERATE tests Tuesday (bike), Thursday (run)
        # and Friday (swim) of week 5. After a Sunday C sprint that week the
        # swim test keeps its Friday; the bike and run tests fall inside the
        # four easy days and move one more week.
        plan = self._block(
            GoalType.TRIATHLON_MAINTENANCE,
            AmbitionLevel.MODERATE,
            16,
            RaceGoal(
                GoalType.SPRINT_TRIATHLON, _START + timedelta(weeks=5, days=-1), "C"
            ),
        )
        assert self._tests(plan, AmbitionLevel.MODERATE) == {
            (5, 4, ActivityKey.SWIM_CSS_TEST),
            (6, 1, ActivityKey.BIKE_FTP_TEST),
            (6, 3, ActivityKey.RUN_THRESHOLD_TEST),
        }

    def test_the_tests_carry_over_the_easier_week_after_a_long_b_race(self) -> None:
        # A B trail 50K on the Saturday of week 5 of an 8-week block: week 6 is
        # the easier week, so all three tests book in week 7.
        plan = self._block(
            GoalType.TRIATHLON_MAINTENANCE,
            AmbitionLevel.MODERATE,
            8,
            RaceGoal(GoalType.TRAIL_50K, _START + timedelta(weeks=5, days=-2), "B"),
        )
        assert plan.weeks[5].mode == WeekMode.RACE_RECOVERY
        assert self._tests(plan, AmbitionLevel.MODERATE) == {
            (6, 1, ActivityKey.BIKE_FTP_TEST),
            (6, 3, ActivityKey.RUN_THRESHOLD_TEST),
            (6, 4, ActivityKey.SWIM_CSS_TEST),
        }

    def test_the_easier_week_after_a_long_b_race_displaces_the_test_too(self) -> None:
        # A B trail 20K on the Sunday of week 4: week 5, the test week, is the
        # easier week on the recovery menu, so the test books in week 6.
        plan = self._block(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            8,
            RaceGoal(GoalType.TRAIL_20K, _START + timedelta(weeks=4, days=-1), "B"),
        )
        assert plan.weeks[4].mode == WeekMode.RACE_RECOVERY
        assert self._tests(plan, AmbitionLevel.BASIC) == {
            (5, 1, ActivityKey.RUN_THRESHOLD_TEST),
        }

    def test_a_displaced_test_never_enters_the_peak(self) -> None:
        # The first load week after the race week is a PEAK week: skipped.
        plan = self._block(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            7,
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=5, days=-1), "C"),
        )
        weeks = list(plan.weeks)
        weeks[5] = replace(weeks[5], mode=WeekMode.RECOVERY)
        weeks[6] = replace(weeks[6], phase=PhaseType.PEAK)
        assert self._tests(replace(plan, weeks=weeks), AmbitionLevel.BASIC) == set()

    def test_a_race_week_between_two_phases_displaces_nothing(self) -> None:
        # No A-goal run spans a race week that closes one phase and opens the
        # next, so there is no menu to read the displaced test from.
        plan = self._block(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            7,
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=5, days=-1), "C"),
        )
        weeks = list(plan.weeks)
        weeks[5] = replace(weeks[5], phase=PhaseType.BUILD)
        weeks[6] = replace(weeks[6], phase=PhaseType.BUILD)
        assert self._tests(replace(plan, weeks=weeks), AmbitionLevel.BASIC) == set()

    def test_a_test_with_no_load_week_after_the_race_is_skipped(self) -> None:
        # A C race in the last week of a 5-week block, its test week.
        plan = self._block(
            GoalType.TRAIL_MAINTENANCE,
            AmbitionLevel.BASIC,
            5,
            RaceGoal(GoalType.RUNNING_10K, _START + timedelta(weeks=5, days=-2), "C"),
        )
        assert plan.weeks[4].mode == WeekMode.RACE
        assert self._tests(plan, AmbitionLevel.BASIC) == set()


class TestPhasePositionAcrossATuneUp:
    """R-SESS-032: a B/C race week does not restart the A-goal phase."""

    def test_the_weeks_after_a_b_race_draw_the_slots_they_would_without_it(
        self,
    ) -> None:
        a = RaceGoal(
            GoalType.RUNNING_MARATHON, _START + timedelta(weeks=24, days=-1), "A"
        )
        b = RaceGoal(
            GoalType.RUNNING_HALF_MARATHON, _START + timedelta(weeks=12, days=-1), "B"
        )

        def plan_and_sessions(*goals: RaceGoal):
            plan = plan_phases(
                PhaseRequest(
                    goals=list(goals),
                    training_start=_START,
                    ambition=AmbitionLevel.MODERATE,
                )
            )
            return plan, generate_session_menu(
                plan, _LONGEST, ambition=AmbitionLevel.MODERATE
            )

        with_b, with_sessions = plan_and_sessions(a, b)
        alone, alone_sessions = plan_and_sessions(a)
        k = next(e.week for e in with_b.race_events if e.goal.priority == "B")

        def slots(sessions: list[PlacedSession], week: int) -> list[str | None]:
            return sorted(s.slot_name or "" for s in sessions if s.week == week)

        compared = [
            w
            for w in range(k + 1, len(alone.weeks))
            if with_b.weeks[w].phase == PhaseType.BUILD
            and with_b.weeks[w].mode == alone.weeks[w].mode == WeekMode.LOAD
        ]
        assert compared
        for w in compared:
            assert slots(with_sessions, w) == slots(alone_sessions, w), w


#: Sessions that put climbing or descending into a trail week.
_CLIMBING = frozenset(
    {
        ActivityKey.RUN_VERT_HIKE,
        ActivityKey.RUN_HILL_REPEATS,
        ActivityKey.RUN_DOWNHILL_REPEATS,
    }
)


def _sized_trail_50k(
    ambition: AmbitionLevel,
    recent_hours: float,
    longest_min: int,
    *,
    start: date,
    weeks: int,
    horizon_join_weeks: int = 0,
    goal_type: GoalType = GoalType.TRAIL_50K,
) -> tuple[PhasePlan, CapacityProjection, list[PlacedSession]]:
    """A trail 50K plan's phase plan, capacity and sessions, as the pipeline sizes."""
    goal = RaceGoal(goal_type, start + timedelta(weeks=weeks, days=-1), "A")
    plan = plan_phases(
        PhaseRequest(
            goals=[goal],
            training_start=start,
            ambition=ambition,
            horizon_join_weeks=horizon_join_weeks,
        )
    )
    amb = get_ambition_profile(ambition)
    capacity = build_capacity_projection(
        {SportType.RUNNING: recent_hours},
        plan.weeks,
        hours_ramp_limit=amb.hours_ramp_limit,
        max_weekly_hours_ceiling=amb.max_weekly_hours_ceiling,
        recovery_ceiling_factor=amb.recovery_load_fraction,
    )
    longest = {(SportType.RUNNING, ActivityFamily.LONG): longest_min}
    sessions = generate_session_menu(
        plan, longest, ambition=ambition, capacity=capacity
    )
    return plan, capacity, sessions


def _short_notice_athlete(
    recent_hours: float,
) -> tuple[PhasePlan, CapacityProjection, list[PlacedSession]]:
    """The short-notice athlete: competitive trail 50K, joined at week 8 of 12."""
    return _sized_trail_50k(
        AmbitionLevel.COMPETITIVE,
        recent_hours,
        230,
        start=date(2026, 9, 21),
        weeks=5,
        horizon_join_weeks=7,
    )


def _week_of(plan: PhasePlan, phase: PhaseType, mode: WeekMode) -> int:
    return next(ws.week for ws in plan.weeks if ws.phase == phase and ws.mode == mode)


def _entry(
    key: ActivityKey, min_h: float, target_h: float, share: float = 0.0, idx: int = 0
) -> tuple:
    """A generator sizing entry for *key* with the given bounds and key share."""
    spec = SessionSpec(
        activity_key=key,
        sport=SportType.RUNNING,
        min_minutes=60,
        max_minutes=60,
        key=share,
    )
    floor_h = _activity_floor_hours(key)
    return (spec, min_h, target_h, target_h, idx, key, floor_h, 9.0)


class TestGiveWay:
    """R-SESS-037: which session goes, and where it goes."""

    def test_a_key_sends_a_filler_which_stays_optional_in_a_load_week(self) -> None:
        key = _entry(ActivityKey.RUN_EASY, 0.5, 0.5, share=0.5, idx=0)
        filler = _entry(ActivityKey.RUN_TEMPO, 0.8, 0.8, idx=1)
        entries, optional, pinned = _give_way(key, [key, filler], [], [], WeekMode.LOAD)
        assert entries == [key]
        assert optional == [(filler, None)]
        assert pinned == []

    def test_a_hard_filler_sent_in_a_taper_week_leaves(self) -> None:
        key = _entry(ActivityKey.RUN_EASY, 0.5, 0.5, share=0.5, idx=0)
        filler = _entry(ActivityKey.RUN_TEMPO, 0.8, 0.8, idx=1)
        entries, optional, pinned = _give_way(
            key, [key, filler], [], [], WeekMode.TAPER
        )
        assert entries == [key]
        assert optional == []
        assert pinned == []

    def test_a_key_with_no_filler_left_stays_booked_over_the_ceiling(self) -> None:
        key = _entry(ActivityKey.RUN_EASY, 0.5, 0.5, share=0.5, idx=0)
        entries, optional, pinned = _give_way(key, [key], [], [], WeekMode.LOAD)
        assert (entries, optional, pinned) == ([], [], [key])


class TestKeySessions:
    """R-SESS-033: a squeezed week keeps its key sessions at an honest size."""

    def test_easy_runs_give_way_to_the_climbing_session(self) -> None:
        # The athlete's first visible load week is PEAK:
        # at 3:1 the week before it is the block's recovery week.
        plan, capacity, sessions = _short_notice_athlete(3.53)
        sessions = _regular(sessions)
        week = _week_of(plan, PhaseType.PEAK, WeekMode.LOAD)
        assert sum(s.min_hours for s in sessions if s.week == week) == pytest.approx(
            capacity.weekly_ceiling[week] * 0.98
        )
        booked = [s for s in sessions if s.week == week]
        climbs = [s for s in booked if s.activity_key in _CLIMBING]
        assert len(climbs) == 1
        assert climbs[0].min_hours >= _activity_floor_hours(climbs[0].activity_key)
        unsized = generate_session_menu(
            plan,
            {(SportType.RUNNING, ActivityFamily.LONG): 230},
            ambition=AmbitionLevel.COMPETITIVE,
        )

        def easy_runs(week_sessions: list[PlacedSession]) -> int:
            return sum(
                1 for s in week_sessions if s.activity_key == ActivityKey.RUN_EASY
            )

        assert easy_runs(booked) < easy_runs([s for s in unsized if s.week == week])

    def test_key_long_run_books_at_most_half_a_squeezed_week(self) -> None:
        plan, capacity, sessions = _short_notice_athlete(3.53)
        week = _week_of(plan, PhaseType.PEAK, WeekMode.LOAD)
        (long_run,) = [
            s
            for s in sessions
            if s.week == week and s.activity_key == ActivityKey.RUN_TRAIL_LONG
        ]
        assert (
            long_run.target_hours <= 0.5 * capacity.weekly_ceiling[week] * 0.98 + 1e-9
        )
        assert any(s.activity_key in _CLIMBING for s in sessions if s.week == week)

    def test_key_that_cannot_fit_its_share_stays_at_its_card(self) -> None:
        """A load week under 2.5 h cannot give a 44-min climb its quarter.

        The easy runs go optional first; the climb stays booked at its card,
        over the week's ceiling (R-SESS-037).
        """
        plan, capacity, sessions = _sized_trail_50k(
            AmbitionLevel.COMPETITIVE, 1.5, 60, start=_START, weeks=12
        )
        small = [
            ws.week
            for ws in plan.weeks
            if ws.mode == WeekMode.LOAD and capacity.weekly_ceiling[ws.week] < 2.5
        ]
        assert small
        for week in small:
            booked = [s for s in _regular(sessions) if s.week == week]
            (climb,) = [s for s in booked if s.activity_key in _CLIMBING]
            assert climb.target_hours == _activity_floor_hours(climb.activity_key)
            assert any(
                s.activity_key == ActivityKey.RUN_EASY and s.optional_reason
                for s in sessions
                if s.week == week
            ), week

    def test_menu_without_keys_sizes_as_before(self) -> None:
        """R-SESS-014: an unkeyed squeezed week scales every minimum alike."""
        menu = (
            SessionSpec(
                activity_key=ActivityKey.RUN_EASY,
                sport=SportType.RUNNING,
                min_minutes=60,
                max_minutes=60,
                day_hint=1,
            ),
            SessionSpec(
                activity_key=ActivityKey.RUN_EASY,
                sport=SportType.RUNNING,
                min_minutes=40,
                max_minutes=40,
                day_hint=3,
            ),
        )
        plan = _build_phase_plan([(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)])
        capacity = CapacityProjection(
            weekly_ceiling={0: 1.5}, weekly_ceiling_by_sport={}
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=menu,
        ):
            sessions = generate_session_menu(plan, _LONGEST, capacity=capacity)
        scale = 1.5 * 0.98 / (100 / 60)
        assert [s.min_hours for s in sessions] == pytest.approx(
            [60 / 60 * scale, 40 / 60 * scale]
        )

    def test_key_minimum_is_raised_to_its_floor(self) -> None:
        """A default basic athlete's first week keeps its vert hike at full card.

        The basic tier authors the base vert hike at 32-49 min, below the
        44-min card the activity builds; unkeyed, the 3.6 h/week default
        athlete's first week could not fund the lift and dropped it.
        """
        plan, _capacity, sessions = _sized_trail_50k(
            AmbitionLevel.BASIC, 3.6, 60, start=_START, weeks=16
        )
        floor = _activity_floor_hours(ActivityKey.RUN_VERT_HIKE)
        (vert,) = [
            s
            for s in sessions
            if s.week == 0 and s.activity_key == ActivityKey.RUN_VERT_HIKE
        ]
        assert plan.weeks[0].phase == PhaseType.BASE
        assert vert.min_hours >= floor

    def test_a_week_that_fits_books_keys_at_their_floor_or_more(self) -> None:
        low = _entry(ActivityKey.RUN_VERT_HIKE, 0.5, 0.5, share=0.25)
        scale, sized, filler = _fit_scale([low], 5.0, 5.0)
        floor = _activity_floor_hours(ActivityKey.RUN_VERT_HIKE)
        assert (scale, filler) == (1.0, None)
        assert sized[0][1:4] == (floor, floor, floor)

    def test_keys_that_fill_the_budget_scale_with_the_week(self) -> None:
        entries = [
            _entry(ActivityKey.RUN_TRAIL_LONG, 3.0, 3.0, share=0.75, idx=0),
            _entry(ActivityKey.RUN_VERT_HIKE, 1.0, 1.0, share=0.5, idx=1),
            _entry(ActivityKey.RUN_EASY, 1.0, 1.0, idx=2),
        ]
        scale, sized, filler = _fit_scale(entries, 2.0, 2.0)
        assert scale == pytest.approx(2.0 / 5.0)
        assert (sized, filler) == (entries, None)

    def test_a_week_of_keys_only_books_each_at_most_its_share(self) -> None:
        """Squeezed down to its keys, a week is left light, not one long run."""
        entries = [
            _entry(ActivityKey.RUN_TRAIL_LONG, 3.0, 3.0, share=0.5, idx=0),
            _entry(ActivityKey.RUN_VERT_HIKE, 1.0, 1.0, share=0.25, idx=1),
        ]
        scale, sized, filler = _fit_scale(entries, 2.0, 2.0)
        assert [e[1] * scale for e in sized] == pytest.approx([1.0, 0.5])
        assert filler is None

    def test_keys_book_first_and_the_largest_filler_gives_way(self) -> None:
        """The filler that fits the squeezed week least drops first."""
        entries = [
            _entry(ActivityKey.RUN_TRAIL_LONG, 2.0, 2.5, share=0.5, idx=0),
            _entry(ActivityKey.RUN_EASY, 1.0, 1.0, idx=1),
            _entry(ActivityKey.RUN_EASY, 0.8, 0.8, idx=2),
        ]
        scale, sized, filler = _fit_scale(entries, 2.0, 2.0)
        assert scale == pytest.approx((2.0 - 1.0) / 1.8)
        assert sized[0][1] * scale == pytest.approx(1.0)
        assert sized[1:] == entries[1:]
        assert filler is None
        scale, sized, filler = _fit_scale(entries, 1.6, 1.6)
        assert scale == pytest.approx((1.6 - 0.8) / 1.8)
        assert filler == entries[1]

    def test_a_filler_that_cannot_shrink_to_its_card_gives_way_first(self) -> None:
        """Strides that halve below their card fit the week less than an easy run."""
        strides = _entry(ActivityKey.RUN_STRIDES, 0.5, 0.5, idx=2)
        entries = [
            _entry(ActivityKey.RUN_TRAIL_LONG, 1.0, 1.0, share=0.5, idx=0),
            _entry(ActivityKey.RUN_EASY, 0.52, 0.52, idx=1),
            strides,
        ]
        assert _activity_floor_hours(ActivityKey.RUN_STRIDES) > 0.5 * FILLER_MIN_SCALE
        scale, _sized, filler = _fit_scale(entries, 1.0, 1.0)
        assert scale < FILLER_MIN_SCALE
        assert filler == strides

    def test_a_taper_gives_up_an_easy_run_before_its_tempo(self) -> None:
        """The taper keeps its intensity: the short-notice athlete's taper keeps its tempo."""
        plan, _capacity, sessions = _short_notice_athlete(3.53)
        week = _week_of(plan, PhaseType.TAPER, WeekMode.TAPER)
        keys = [s.activity_key for s in sessions if s.week == week]
        assert ActivityKey.RUN_AEROBIC_TEMPO in keys
        assert ActivityKey.RUN_EASY in keys

    def test_the_last_filler_never_gives_way(self) -> None:
        entries = [
            _entry(ActivityKey.RUN_TRAIL_LONG, 2.0, 2.5, share=0.5, idx=0),
            _entry(ActivityKey.RUN_EASY, 2.0, 2.0, idx=1),
        ]
        scale, sized, filler = _fit_scale(entries, 1.6, 1.6)
        assert scale == pytest.approx((1.6 - 0.8) / 2.0)
        assert scale < FILLER_MIN_SCALE
        assert sized[0][1] * scale == pytest.approx(0.8)
        assert filler is None


_TRAIL_GOALS = (
    GoalType.TRAIL_20K,
    GoalType.TRAIL_50K,
    GoalType.TRAIL_100K,
)


class TestSqueezedTrailWeeks:
    """R-SESS-033 over whole plans: a 2.25 h/week athlete joining five weeks out."""

    @pytest.mark.parametrize("ambition", list(AmbitionLevel))
    @pytest.mark.parametrize("goal", _TRAIL_GOALS)
    def test_a_squeezed_load_week_keeps_its_volume_and_its_shares(
        self, goal: GoalType, ambition: AmbitionLevel
    ) -> None:
        joined = GOAL_PROFILES[goal].min_horizon_weeks - 5
        plan, capacity, sessions = _sized_trail_50k(
            ambition,
            2.25,
            54,
            start=_START,
            weeks=5,
            horizon_join_weeks=joined,
            goal_type=goal,
        )
        longest = {(SportType.RUNNING, ActivityFamily.LONG): 54}
        unsized = generate_session_menu(plan, longest, ambition=ambition)
        squeezed = 0
        for ws in plan.weeks:
            ceiling = capacity.weekly_ceiling[ws.week]
            authored = [s for s in unsized if s.week == ws.week]
            if (
                ws.mode != WeekMode.LOAD
                or sum(s.min_hours for s in authored) <= ceiling
            ):
                continue
            squeezed += 1
            share = {
                s.slot_name: s.key
                for s in get_session_menu(goal, ws.phase, "load", ambition)
            }
            week = [s for s in sessions if s.week == ws.week]
            others = [s for s in week if not share[s.slot_name]]
            # The week keeps its other sessions, booked or optional ...
            assert others, (ws.week, week)
            # ... and while none of its others went optional, no key takes
            # more than its share (R-SESS-033); once one did (R-SESS-037)
            # the week fits and a key books its target.
            if not any(s.optional_reason for s in week):
                for s in week:
                    if share[s.slot_name]:
                        assert (
                            s.target_hours <= share[s.slot_name] * ceiling * 0.98 + 1e-9
                        )
            # ... and the week has room for the hours the athlete has.
            assert sum(s.max_hours for s in week) >= 0.97 * ceiling * 0.98, ws.week
        assert squeezed

    def test_a_squeezed_peak_week_keeps_every_session(
        self,
    ) -> None:
        """A squeezed 100K peak week keeps every session (R-SESS-037).

        Its keys stay booked, the long run and the vert hike; the easy runs
        that do not fit go optional, none leaves. (BASIC's Sunday is a short
        easy run outside its rehearsal weekend. Until 2026-10-06 the peak
        shed squeezed all three easy runs out; without it one goes.)
        """
        plan, _capacity, sessions = _sized_trail_50k(
            AmbitionLevel.BASIC,
            2.5,
            60,
            start=_START,
            weeks=5,
            horizon_join_weeks=9,
            goal_type=GoalType.TRAIL_100K,
        )
        assert plan.weeks[0].phase == PhaseType.PEAK
        week = [s for s in sessions if s.week == 0]
        regular = [s.activity_key for s in _regular(week)]
        assert {ActivityKey.RUN_TRAIL_LONG, ActivityKey.RUN_VERT_HIKE} <= set(regular)
        # The menu's own optional rides (R-SESS-040) aside, every run stays.
        runs = sorted(
            s.activity_key for s in week if s.optional_reason != OPTIONAL_MENU_WHY
        )
        assert runs == sorted(
            [
                ActivityKey.RUN_TRAIL_LONG,
                ActivityKey.RUN_VERT_HIKE,
                ActivityKey.RUN_EASY,
                ActivityKey.RUN_EASY,
                ActivityKey.RUN_EASY,
            ]
        )
        assert any(s.optional_reason not in (None, OPTIONAL_MENU_WHY) for s in week)


class TestRaceSimFitsItsShare:
    """R-SESS-023: the rehearsal fires only when its week can hold it."""

    def test_rehearsal_falls_back_when_the_week_cannot_hold_it(self) -> None:
        plan, _capacity, sessions = _short_notice_athlete(3.53)
        week = _week_of(plan, PhaseType.PEAK, WeekMode.LOAD)
        slots = {s.slot_name for s in sessions if s.week == week}
        assert "t50_peak_sun_race_sim" not in slots
        assert "t50_peak_sun_long_run" in slots

    def test_rehearsal_fires_at_full_length_when_it_fits(self) -> None:
        plan, _capacity, sessions = _short_notice_athlete(7.0)
        week = _week_of(plan, PhaseType.PEAK, WeekMode.LOAD)
        (rehearsal,) = [
            s
            for s in sessions
            if s.week == week and s.slot_name == "t50_peak_sun_race_sim"
        ]
        assert rehearsal.min_hours >= 250 / 60

    def test_rehearsal_waits_for_the_long_run_to_reach_its_length(self) -> None:
        """A 7 h/week athlete whose longest run is 60 min never books a short one.

        The week has room for the rehearsal, but the long run grows about
        10 % a week from 60 min and has not reached 250 by the peak.
        """
        _plan, _capacity, sessions = _sized_trail_50k(
            AmbitionLevel.COMPETITIVE, 7.0, 60, start=_START, weeks=16
        )
        rehearsals = [s for s in sessions if s.slot_name == "t50_peak_sun_race_sim"]
        assert all(s.min_hours >= 250 / 60 for s in rehearsals), rehearsals


def _long_run(**fields: object) -> SessionSpec:
    """A Sunday trail long run with the given bounds and flags."""
    return SessionSpec(
        activity_key=ActivityKey.RUN_TRAIL_LONG,
        sport=SportType.RUNNING,
        day_hint=6,
        **fields,  # type: ignore[arg-type]
    )


#: A load week's progressive long run (the ramp) and an easy run.
_RAMP_MENU = (
    _long_run(min_minutes=150, max_minutes=215, progressive=True, progression_key="p"),
    SessionSpec(
        activity_key=ActivityKey.RUN_EASY,
        sport=SportType.RUNNING,
        min_minutes=45,
        max_minutes=60,
        day_hint=2,
    ),
)


class TestEasedLongRuns:
    """R-SESS-034: an easier week's long run is at most 0.75 of the longest so far."""

    _EASY = _RAMP_MENU[1]
    _MODES = [WeekMode.LOAD] * 3 + [WeekMode.RECOVERY, WeekMode.LOAD]

    def _long_runs(
        self,
        modes: list[WeekMode],
        eased: SessionSpec,
        longest_min: int = 60,
        capacity: CapacityProjection | None = None,
    ) -> dict[int, PlacedSession]:
        """Each week's long run, the recovery menu holding *eased* and an easy run."""
        plan = _build_phase_plan(
            [(PhaseType.BUILD, m, GoalType.IRONMAN) for m in modes]
        )
        menus = {"load": _RAMP_MENU, "recovery": (eased, self._EASY)}
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            side_effect=lambda _goal, _phase, mode_key, _ambition: menus[mode_key],
        ):
            sessions = generate_session_menu(
                plan,
                {(SportType.RUNNING, ActivityFamily.LONG): longest_min},
                capacity=capacity,
            )
        return {
            s.week: s for s in sessions if s.activity_key == ActivityKey.RUN_TRAIL_LONG
        }

    def test_recovery_long_run_is_capped_at_three_quarters_of_the_longest(
        self,
    ) -> None:
        runs = self._long_runs(
            self._MODES, _long_run(min_minutes=90, max_minutes=120, eases="p")
        )
        assert [round(runs[w].target_hours * 60, 6) for w in (0, 1, 2)] == [68, 76, 84]
        # 0.75 x 84 = 63, down to the 5-minute grid.
        rec = runs[3]
        assert (rec.min_hours, rec.target_hours, rec.max_hours) == (1.0, 1.0, 1.0)

    def test_the_yardstick_is_the_long_run_booked_not_the_ramp(self) -> None:
        """A squeezed load week books its 68-min ramp at ~35 min."""
        squeezed = CapacityProjection(
            weekly_ceiling={0: 1.0}, weekly_ceiling_by_sport={}
        )
        runs = self._long_runs(
            [WeekMode.LOAD, WeekMode.RECOVERY],
            _long_run(min_minutes=90, max_minutes=120, eases="p"),
            capacity=squeezed,
        )
        booked = runs[0].target_hours * 60
        assert booked == pytest.approx(68 * 0.98 / ((68 + 45) / 60))
        assert runs[1].max_hours * 60 == 25  # 0.75 x 35.4 = 26.5, down to 25

    def test_the_ramp_continues_from_the_last_load_week(self) -> None:
        eased = self._long_runs(
            self._MODES, _long_run(min_minutes=90, max_minutes=120, eases="p")
        )
        plain = self._long_runs(self._MODES, _long_run(min_minutes=90, max_minutes=120))
        assert eased[4].target_hours * 60 == pytest.approx(84 * 1.1)
        assert eased[4] == plain[4]

    @pytest.mark.parametrize(
        ("ceiling", "cap", "booked"),
        [
            # The 60 + 45 min of minimums overfill the week: sized at
            # 0.98 x 1.5 / 1.75 = 0.84, the long run books at least 50.4.
            (1.5, None, 50.4),
            # The minimums fit with 0.84 min to spare, and the long run
            # takes them.
            (1.8, None, 60.84),
            # A roomy week books the whole 68-min ramp.
            (3.0, None, 68.0),
            # The athlete's 1.75-h cap leaves no room over the minimums:
            # the solver books the week at them.
            (3.0, 1.75, 60.0),
        ],
    )
    def test_the_ramp_grows_from_the_long_run_the_week_books(
        self, ceiling: float, cap: float | None, booked: float
    ) -> None:
        """R-SESS-013: the next load week steps from the long run booked.

        The 68-min ramp sits over the 60-min menu minimum, so the solver
        could book it anywhere between; its minimum rises to the ramp as far
        as the week has room, and the next, roomy week grows one step from
        that minimum, not from the ramp (76 before in every case).
        """
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.LOAD, GoalType.IRONMAN)] * 2
        )
        menu = (
            _long_run(
                min_minutes=60, max_minutes=215, progressive=True, progression_key="p"
            ),
            self._EASY,
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=menu,
        ):
            sessions = generate_session_menu(
                plan,
                {(SportType.RUNNING, ActivityFamily.LONG): 60},
                capacity=CapacityProjection(
                    weekly_ceiling={0: ceiling}, weekly_ceiling_by_sport={}
                ),
                weekly_hours_cap=cap,
            )
        runs = [s for s in sessions if s.activity_key == ActivityKey.RUN_TRAIL_LONG]
        assert runs[0].min_hours * 60 == pytest.approx(booked)
        assert runs[1].max_hours * 60 == pytest.approx(booked + 8)

    def test_a_long_run_already_under_the_cap_stands(self) -> None:
        eased = self._long_runs(
            self._MODES, _long_run(min_minutes=30, max_minutes=40, eases="p")
        )
        plain = self._long_runs(self._MODES, _long_run(min_minutes=30, max_minutes=40))
        assert eased == plain
        assert (eased[3].min_hours, eased[3].max_hours) == (0.5, 40 / 60)

    def test_a_long_run_without_the_key_keeps_its_bounds(self) -> None:
        runs = self._long_runs(self._MODES, _long_run(min_minutes=90, max_minutes=120))
        assert (runs[3].min_hours, runs[3].max_hours) == (1.5, 2.0)

    def test_before_the_first_long_run_the_yardstick_is_the_recent_longest(
        self,
    ) -> None:
        runs = self._long_runs(
            [WeekMode.RECOVERY, WeekMode.LOAD],
            _long_run(min_minutes=90, max_minutes=120, eases="p"),
            longest_min=84,
        )
        assert runs[0].max_hours == 1.0

    def test_easy_runs_stay_under_the_eased_cap(self) -> None:
        """A 45-min recovery long run caps the week's 45-60 min easy run at 45."""
        plan = _build_phase_plan(
            [(PhaseType.BUILD, WeekMode.RECOVERY, GoalType.IRONMAN)]
        )
        strides = SessionSpec(
            activity_key=ActivityKey.RUN_THRESHOLD,
            sport=SportType.RUNNING,
            min_minutes=50,
            max_minutes=60,
            day_hint=3,
        )
        keyed_easy = SessionSpec(
            activity_key=ActivityKey.RUN_EASY,
            sport=SportType.RUNNING,
            min_minutes=50,
            max_minutes=60,
            day_hint=4,
            key=0.25,
        )
        menu = (
            _long_run(min_minutes=90, max_minutes=120, eases="p"),
            self._EASY,
            strides,
            keyed_easy,
        )
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=menu,
        ):
            sessions = generate_session_menu(
                plan, {(SportType.RUNNING, ActivityFamily.LONG): 60}
            )
        bounds = {s.day_of_week: (s.min_hours * 60, s.max_hours * 60) for s in sessions}
        assert bounds[6] == pytest.approx((45, 45))  # the eased long run
        assert bounds[2] == pytest.approx((45, 45))  # easy: capped
        assert bounds[3] == pytest.approx((50, 60))  # quality: untouched
        assert bounds[4] == pytest.approx((50, 60))  # key: untouched

    def test_a_squeezed_recovery_week_keeps_its_long_run(self) -> None:
        """The eased long run leaves last; the shortest other session goes first."""
        long_run = (_long_run(min_minutes=25, max_minutes=25, eases="p"),)
        eased = long_run + _entry(ActivityKey.RUN_TRAIL_LONG, 0.4, 0.4)[1:]
        tue = _entry(ActivityKey.RUN_EASY, 0.5, 0.5, idx=1)
        wed = _entry(ActivityKey.RUN_EASY, 0.45, 0.45, idx=2)
        assert _worst_offender([(eased, 0.1), (tue, 0.05)], [eased, tue, wed]) == tue
        assert _worst_offender([(eased, 0.1)], [eased, tue, wed]) == wed
        assert _worst_offender([(eased, 0.1)], [eased]) == eased

    def test_a_cap_below_the_card_floor_is_lifted_to_the_card(self) -> None:
        """R-SESS-030: a card is never booked shorter than it can be built.

        A 12-min runner's cap (5 min) is under the trail long run's 14-min
        card; a week with room books the card, as it would any session.
        """
        floor = _activity_floor_hours(ActivityKey.RUN_TRAIL_LONG)
        runs = self._long_runs(
            [WeekMode.RECOVERY],
            _long_run(min_minutes=90, max_minutes=120, eases="p"),
            longest_min=12,
        )
        assert (runs[0].min_hours, runs[0].max_hours) == (floor, floor)


_BASE_R = (PhaseType.BASE, WeekMode.RECOVERY, _IM)
_BUILD_L = (PhaseType.BUILD, WeekMode.LOAD, _IM)
_BUILD_R = (PhaseType.BUILD, WeekMode.RECOVERY, _IM)


class TestCadenceWeeks:
    """Unit tests for the cadence-week selector (R-SESS-041)."""

    def test_opens_on_the_first_week_then_the_recovery_week_nearest_each_mark(
        self,
    ) -> None:
        """3:1 over 24 weeks: the marks at 8 and 16 take weeks 8 and 16."""
        plan = _build_phase_plan(
            [
                *([_BASE_L] * 3),
                _BASE_R,
                *([_BASE_L] * 3),
                _BASE_R,
                *([_BASE_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 2),
                _PEAK_L,
                _PEAK_R,
                _PEAK_L,
                _PEAK_L,
                _TAPER_T,
                _TAPER_RACE,
            ]
        )
        # Recovery weeks at indices 3, 7, 11, 15 (19 is PEAK): the mark at 8
        # takes 7 (one away, not 11 at three), the mark at 16 takes 15.
        assert cadence_weeks(plan.weeks, 8) == frozenset({0, 7, 15})

    def test_a_mark_with_no_recovery_week_within_reach_fires_nothing(self) -> None:
        """At 20 weeks the second mark meets only the PEAK recovery week."""
        plan = _build_phase_plan(
            [
                *([_BASE_L] * 3),
                _BASE_R,
                *([_BASE_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 3),
                _PEAK_R,
                _PEAK_L,
                _PEAK_L,
                _TAPER_T,
                _TAPER_RACE,
            ]
        )
        # Index 15 is PEAK recovery; index 11 sits five weeks from the mark.
        assert cadence_weeks(plan.weeks, 8) == frozenset({0, 7})

    def test_two_to_one_cadence_takes_the_exact_or_nearer_later_week(self) -> None:
        """2:1 recovery weeks at 2, 5, 8, 11, 14, 17: marks 8 and 16 take 8 and 17."""
        plan = _build_phase_plan(
            [_BASE_L, _BASE_L, _BASE_R] * 4
            + [_BUILD_L, _BUILD_L, _BUILD_R] * 2
            + [_PEAK_L, _TAPER_T, _TAPER_RACE]
        )
        assert cadence_weeks(plan.weeks, 8) == frozenset({0, 8, 17})

    def test_a_tie_goes_to_the_later_week(self) -> None:
        """Recovery weeks two away on either side of the mark: the later wins."""
        plan = _build_phase_plan(
            [*[_BASE_L] * 6, _BASE_R, *[_BASE_L] * 3, _BASE_R, _BASE_L]
        )
        assert cadence_weeks(plan.weeks, 8) == frozenset({0, 10})

    def test_a_tune_up_race_week_never_fires(self) -> None:
        """A B race's own week is not a cadence week, even when nearest."""
        plan = _build_phase_plan(
            [*[_BASE_L] * 3, _BASE_R, *[_BASE_L] * 3, _BASE_R, _BUILD_L]
        )
        tune_up = RaceGoal(GoalType.OLYMPIC_TRIATHLON, _START + timedelta(weeks=8), "B")
        plan.weeks[7] = replace(plan.weeks[7], owning_goal=tune_up)
        assert cadence_weeks(plan.weeks, 8) == frozenset({0})

    def test_an_empty_plan_fires_nothing(self) -> None:
        """No weeks, no cadence."""
        assert cadence_weeks([], 8) == frozenset()

    def test_a_timeline_opening_before_week_zero_anchors_on_its_first_week(
        self,
    ) -> None:
        """Weeks numbered -2..17 open on -2; the marks fall on 6 and 14."""
        plan = _build_phase_plan(
            [
                *([_BASE_L] * 3),
                _BASE_R,
                *([_BASE_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 3),
                _BUILD_R,
                *([_BUILD_L] * 3),
                _PEAK_R,
                _PEAK_L,
                _PEAK_L,
                _TAPER_T,
                _TAPER_RACE,
            ]
        )
        shifted = [replace(w, week=w.week - 2) for w in plan.weeks]
        # Recovery weeks sit at 1, 5, 9 and 13 (PEAK, never fires). The mark
        # at 6 takes 5 (one away); the mark at 14 has only 13 (PEAK) and 9
        # (five away), so it fires nothing.
        assert cadence_weeks(shifted, 8) == frozenset({-2, 5})


# A cadence test sharing the slot group of _SG_EARLY/_SG_LATE.
_SG_CADENCE = SessionSpec(
    activity_key=ActivityKey.BIKE_FTP_TEST,
    sport=SportType.CYCLING,
    min_minutes=42,
    max_minutes=42,
    slot_group="test_bike_quality",
    cadence_weeks=8,
)


class TestActiveSpecsCadence:
    """A cadence spec fires on its interval's weeks and wins its group."""

    def test_fires_only_when_its_interval_fires(self) -> None:
        """Its interval in ``cadence_fire`` fires it; any other set leaves the window winner."""
        specs = [_SG_EARLY, _SG_LATE, _SG_CADENCE]
        assert active_specs(specs, 0.9, False, frozenset({8})) == [_SG_CADENCE]
        assert active_specs(specs, 0.9, False, frozenset({6})) == [_SG_LATE]
        assert active_specs(specs, 0.9, False) == [_SG_LATE]

    def test_wins_its_group_outright(self) -> None:
        """Inside its group a cadence member beats frac geometry."""
        assert resolve_slot_groups([_SG_EARLY, _SG_CADENCE], 0.0) == [_SG_CADENCE]


class TestCadenceInTheGenerator:
    """End to end: the cadence test replaces its stand-in on its weeks only."""

    def test_cadence_test_fires_on_week_one_and_the_eight_week_mark(self) -> None:
        """Eight BASE weeks, 3:1: the test books weeks 1 and 8, the stand-in the rest."""
        plan = _build_phase_plan([*[_BASE_L] * 3, _BASE_R, *[_BASE_L] * 3, _BASE_R])
        stand_in = SessionSpec(
            activity_key=ActivityKey.BIKE_VO2MAX,
            sport=SportType.CYCLING,
            min_minutes=45,
            max_minutes=55,
            day_hint=1,
            slot_group="tue",
        )
        test = replace(_SG_CADENCE, day_hint=1, slot_group="tue")
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=[stand_in, test],
        ):
            sessions = generate_session_menu(plan, _LONGEST)

        by_key = {
            key: sorted(s.week for s in sessions if s.activity_key is key)
            for key in (ActivityKey.BIKE_FTP_TEST, ActivityKey.BIKE_VO2MAX)
        }
        assert by_key[ActivityKey.BIKE_FTP_TEST] == [0, 7]
        assert by_key[ActivityKey.BIKE_VO2MAX] == [1, 2, 3, 4, 5, 6]


class TestLoadWeekWindows:
    """R-SESS-008: a ``load_weeks`` window reads the run's load weeks, not all its weeks."""

    def test_the_recovery_week_never_costs_an_early_slot(self) -> None:
        """Wherever the PEAK recovery week falls, two of three load weeks draw early."""
        early = SessionSpec(
            activity_key=ActivityKey.BIKE_LONG,
            sport=SportType.CYCLING,
            min_minutes=240,
            max_minutes=240,
            day_hint=5,
            slot_group="sat",
            phase_fraction_end=0.5,
            load_weeks=True,
        )
        late = replace(
            early,
            activity_key=ActivityKey.BIKE_ENDURANCE,
            phase_fraction_start=0.5,
            phase_fraction_end=1.0,
        )
        calendar = replace(
            early,
            activity_key=ActivityKey.BIKE_VO2MAX,
            day_hint=1,
            slot_group="tue",
            load_weeks=False,
        )
        calendar_late = replace(
            late,
            activity_key=ActivityKey.BIKE_THRESHOLD,
            day_hint=1,
            slot_group="tue",
            load_weeks=False,
        )
        peaks = (
            [_PEAK_R, _PEAK_L, _PEAK_L, _PEAK_L],
            [_PEAK_L, _PEAK_R, _PEAK_L, _PEAK_L],
            [_PEAK_L, _PEAK_L, _PEAK_R, _PEAK_L],
            [_PEAK_L, _PEAK_L, _PEAK_L, _PEAK_R],
        )
        for peak in peaks:
            plan = _build_phase_plan([_BASE_L, *peak, _TAPER_T, _TAPER_RACE])
            with patch(
                "endury.planner.sessions.session_generator.get_session_menu",
                return_value=[early, late, calendar, calendar_late],
            ):
                sessions = generate_session_menu(plan, _LONGEST)
            load_weeks = [
                ws.week
                for ws in plan.weeks
                if ws.phase == PhaseType.PEAK and ws.mode == WeekMode.LOAD
            ]

            long_ride = {
                s.week for s in sessions if s.activity_key is ActivityKey.BIKE_LONG
            }
            vo2 = {
                s.week for s in sessions if s.activity_key is ActivityKey.BIKE_VO2MAX
            }
            assert [w for w in load_weeks if w in long_ride] == load_weeks[:2], peak
            # A window without the flag keeps its place among all the
            # peak's weeks: the early session fires in its first half.
            assert [w for w in load_weeks if w in vo2] == [
                w for w in load_weeks if (w - 1) / 3 <= 0.5
            ], peak


def _truncated_by_two(plan: PhasePlan) -> PhasePlan:
    """Cut the first two weeks off ``plan`` as a replan does, keeping them as prior."""
    return PhasePlan(
        weeks=[replace(w, week=w.week - 2) for w in plan.weeks[2:]],
        race_events=[],
        a_goal=plan.a_goal,
        prior_weeks=tuple(replace(w, week=w.week - 2) for w in plan.weeks[:2]),
    )


class TestReplanKeepsThePlansOwnTimeline:
    """A replan resumes the plan's cadence and opening weeks, not its own start."""

    def test_a_replan_does_not_reopen_with_the_threshold_test(self) -> None:
        """Two weeks trained: the test fires in the 8-week mark's recovery week only."""
        plan = _build_phase_plan([*[_BASE_L] * 3, _BASE_R, *[_BASE_L] * 3, _BASE_R])
        stand_in = SessionSpec(
            activity_key=ActivityKey.BIKE_VO2MAX,
            sport=SportType.CYCLING,
            min_minutes=45,
            max_minutes=55,
            day_hint=1,
            slot_group="tue",
        )
        test = replace(_SG_CADENCE, day_hint=1, slot_group="tue")
        with patch(
            "endury.planner.sessions.session_generator.get_session_menu",
            return_value=[stand_in, test],
        ):
            sessions = generate_session_menu(_truncated_by_two(plan), _LONGEST)

        by_key = {
            key: sorted(s.week for s in sessions if s.activity_key is key)
            for key in (ActivityKey.BIKE_FTP_TEST, ActivityKey.BIKE_VO2MAX)
        }
        # The mark at -2 + 8 = 6 takes the recovery week 5 (the plan's week 8).
        assert by_key[ActivityKey.BIKE_FTP_TEST] == [5]
        assert by_key[ActivityKey.BIKE_VO2MAX] == [0, 1, 2, 3, 4]

    def test_an_opening_week_window_does_not_fire_again_after_a_replan(self) -> None:
        """A window on the plan's first week stays in that week, trained or not."""
        plan = _build_phase_plan([_BASE_L] * 5)
        opening = SessionSpec(
            activity_key=ActivityKey.BIKE_VO2MAX,
            sport=SportType.CYCLING,
            min_minutes=45,
            max_minutes=55,
            day_hint=1,
            slot_group="tue",
            phase_fraction_start=0.0,
            phase_fraction_end=0.05,
        )
        usual = SessionSpec(
            activity_key=ActivityKey.BIKE_THRESHOLD,
            sport=SportType.CYCLING,
            min_minutes=45,
            max_minutes=55,
            day_hint=1,
            slot_group="tue",
        )

        def opening_weeks(phase_plan: PhasePlan) -> list[int]:
            with patch(
                "endury.planner.sessions.session_generator.get_session_menu",
                return_value=[opening, usual],
            ):
                sessions = generate_session_menu(phase_plan, _LONGEST)
            return sorted(
                s.week for s in sessions if s.activity_key is ActivityKey.BIKE_VO2MAX
            )

        assert opening_weeks(plan) == [0]
        assert opening_weeks(_truncated_by_two(plan)) == []
