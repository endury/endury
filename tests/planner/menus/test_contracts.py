"""Tests for endury.planner.sessions.menus.contracts."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planner.sessions.menus.contracts import (
    ContractViolation,
    MenuContractError,
    sport_of,
    validate_all,
    validate_blueprint,
)
from endury.planner.sessions.menus.types import (
    MenuContract,
    PhaseMenu,
    PlanBlueprint,
    Slot,
    Variant,
    Weekday,
)
from tests.planner.menus.helpers import session


def _minimal_load_slots() -> tuple[Slot, ...]:
    """Return a small but realistic IM-LOAD slot set used by several tests."""
    return (
        session(
            "long_bike",
            Weekday.SAT,
            ActivityKey.BIKE_LONG,
            (180, 240),
        ),
        session(
            "brick",
            Weekday.SAT,
            ActivityKey.RUN_BRICK_SHORT,
            (15, 25),
            is_brick_pair=True,
        ),
        session(
            "long_swim",
            Weekday.FRI,
            ActivityKey.SWIM_LONG,
            (2500, 4000),
        ),
        # Wed (not the canonical Sun): _full_coverage reuses this slot set
        # for the (TAPER, "race") cell too, where a Sunday slot would trip
        # the race_menu_sunday lint.
        session(
            "long_run",
            Weekday.WED,
            ActivityKey.RUN_LONG,
            (60, 110),
        ),
    )


def _full_coverage(menu_factory) -> dict[tuple[PhaseType, str], PhaseMenu]:
    """Build a full coverage dict where every (phase, mode) shares a menu."""
    keys = (
        (PhaseType.BASE, "load"),
        (PhaseType.BASE, "recovery"),
        (PhaseType.BUILD, "load"),
        (PhaseType.BUILD, "recovery"),
        (PhaseType.PEAK, "load"),
        (PhaseType.PEAK, "recovery"),
        (PhaseType.TAPER, "taper"),
        (PhaseType.TAPER, "race"),
    )
    return {key: menu_factory(*key) for key in keys}


def test_sport_of_returns_expected_sport():
    assert sport_of(ActivityKey.RUN_LONG) == SportType.RUNNING
    assert sport_of(ActivityKey.BIKE_LONG) == SportType.CYCLING
    assert sport_of(ActivityKey.SWIM_LONG) == SportType.SWIMMING
    assert sport_of(ActivityKey.RUN_THRESHOLD_TEST) == SportType.RUNNING


def test_validate_blueprint_passes_for_minimal_complete_blueprint():
    def make(phase, mode):
        return PhaseMenu(
            phase=phase,
            mode_key=mode,
            slots=_minimal_load_slots(),
        )

    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases=_full_coverage(make),
    )
    assert validate_blueprint(blueprint) == []


def test_a_load_swim_under_the_tier_floor_is_a_violation():
    short = session("short_swim", Weekday.TUE, ActivityKey.SWIM_CSS, (1600, 2400))

    def make(phase, mode):
        return PhaseMenu(
            phase=phase, mode_key=mode, slots=(*_minimal_load_slots(), short)
        )

    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(
            PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
        )
    floors = {
        (v.mode_key, v.ambition)
        for v in exc_info.value.violations
        if v.rule == "swim_floor"
    }
    # 1600 m clears BASIC's 1500; recovery and race weeks carry no floor.
    assert floors == {
        (mode, tier)
        for mode in ("load", "taper")
        for tier in (AmbitionLevel.MODERATE, AmbitionLevel.COMPETITIVE)
    }


def test_drills_and_tests_carry_no_swim_floor():
    extra = (
        session("drill", Weekday.TUE, ActivityKey.SWIM_DRILL, (800, 1200)),
        session("test", Weekday.THU, ActivityKey.SWIM_CSS_TEST, (1000, 1000)),
    )

    def make(phase, mode):
        return PhaseMenu(
            phase=phase, mode_key=mode, slots=(*_minimal_load_slots(), *extra)
        )

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    assert validate_blueprint(blueprint) == []


def test_validate_blueprint_raises_on_missing_coverage():
    def make(phase, mode):
        return PhaseMenu(phase=phase, mode_key=mode, slots=_minimal_load_slots())

    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases={(PhaseType.BASE, "load"): make(PhaseType.BASE, "load")},
    )
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    msg = str(exc_info.value)
    assert "missing" in msg


def test_validate_blueprint_raises_on_unexpected_phase_mode_key():
    def make(phase, mode):
        return PhaseMenu(phase=phase, mode_key=mode, slots=_minimal_load_slots())

    phases = _full_coverage(make)
    phases[(PhaseType.BASE, "fantasy")] = PhaseMenu(
        phase=PhaseType.BASE,
        mode_key="fantasy",
        slots=_minimal_load_slots(),
    )
    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=phases)
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    assert "unexpected" in str(exc_info.value)


def test_validate_blueprint_detects_phase_mismatch():
    def make(phase, mode):
        if phase == PhaseType.BUILD and mode == "load":
            return PhaseMenu(
                phase=PhaseType.PEAK,
                mode_key=mode,
                slots=_minimal_load_slots(),
            )
        return PhaseMenu(phase=phase, mode_key=mode, slots=_minimal_load_slots())

    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases=_full_coverage(make),
    )
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    assert "mismatch" in str(exc_info.value)


def test_validate_blueprint_must_contain_violation_lists_offender():
    def make(phase, mode):
        slots = _minimal_load_slots()
        contract = (
            MenuContract(
                must_contain=frozenset({ActivityKey.RUN_VO2MAX}),
            )
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    violations = exc_info.value.violations
    assert any(v.rule == "must_contain" for v in violations)


def test_validate_blueprint_must_not_contain_violation():
    forbidden = ActivityKey.RUN_5K_RACE_PACE

    def make(phase, mode):
        slots = _minimal_load_slots()
        if (phase, mode) == (PhaseType.BUILD, "load"):
            slots = (
                *slots,
                session(
                    "evil",
                    Weekday.WED,
                    forbidden,
                    (30, 45),
                ),
            )
        contract = (
            MenuContract(must_not_contain=frozenset({forbidden}))
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    assert any(v.rule == "must_not_contain" for v in exc_info.value.violations)


def test_validate_blueprint_must_contain_via_rotation_passes():
    """must_contain inspects Variant.rotation as well as activity_key."""
    rot_slot = session(
        "rot",
        Weekday.MON,
        ActivityKey.SWIM_CSS,
        (2000, 2400),
        rotation=(ActivityKey.SWIM_CSS, ActivityKey.SWIM_THRESHOLD),
    )

    def make(phase, mode):
        slots = (*_minimal_load_slots(), rot_slot)
        contract = (
            MenuContract(
                must_contain=frozenset({ActivityKey.SWIM_THRESHOLD}),
            )
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    # Should pass because the rotation reaches SWIM_THRESHOLD.
    assert validate_blueprint(blueprint) == []


def test_validate_blueprint_min_sport_sessions_violation():
    def make(phase, mode):
        slots = _minimal_load_slots()
        contract = (
            MenuContract(
                min_sport_sessions=MappingProxyType({SportType.RUNNING: 5}),
            )
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    rules = {v.rule for v in exc_info.value.violations}
    assert "min_sport_sessions" in rules


def test_validate_blueprint_bricks_per_week_violation():
    def make(phase, mode):
        slots = _minimal_load_slots()  # 1 brick
        contract = (
            MenuContract(bricks_per_week=(2, 3))
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    assert any(v.rule == "bricks_per_week" for v in exc_info.value.violations)


def test_validate_blueprint_weekly_quality_sessions_violation():
    """Z4+ session count is enforced via ActivityFamily classifier."""

    def make(phase, mode):
        # Easy-only menu fails a "needs at least one quality session" gate.
        easy_slots = (
            session("e1", Weekday.MON, ActivityKey.RUN_EASY, (30, 45)),
            session("e2", Weekday.TUE, ActivityKey.RUN_EASY, (30, 45)),
        )
        contract = (
            MenuContract(weekly_quality_sessions=(1, 3))
            if (phase, mode) == (PhaseType.BUILD, "load")
            else MenuContract()
        )
        return PhaseMenu(
            phase=phase, mode_key=mode, slots=easy_slots, contract=contract
        )

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    rules = {v.rule for v in exc_info.value.violations}
    assert "weekly_quality_sessions" in rules


def test_validate_blueprint_weekly_quality_sessions_passes_with_threshold_slot():
    """A menu with a threshold session inside the (1, 3) range passes."""

    def make(phase, mode):
        slots = (
            session("e1", Weekday.MON, ActivityKey.RUN_EASY, (30, 45)),
            session("q1", Weekday.WED, ActivityKey.RUN_THRESHOLD, (40, 55)),
        )
        contract = MenuContract(weekly_quality_sessions=(1, 3))
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    assert validate_blueprint(blueprint) == []


def test_validate_blueprint_bricks_per_week_within_range_passes():
    def make(phase, mode):
        slots = _minimal_load_slots()
        contract = MenuContract(bricks_per_week=(1, 2))
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    assert validate_blueprint(blueprint) == []


def test_contract_violation_formatter():
    v = ContractViolation(
        goal=GoalType.IRONMAN,
        phase=PhaseType.BASE,
        mode_key="load",
        ambition=AmbitionLevel.MODERATE,
        rule="rule",
        detail="d",
    )
    assert "ironman" in v.format()
    assert "moderate" in v.format()
    star = ContractViolation(
        goal=GoalType.IRONMAN,
        phase=PhaseType.BASE,
        mode_key="load",
        ambition=None,
        rule="r",
        detail="d",
    )
    assert " *]" in star.format()


def test_validate_all_runs_every_blueprint_and_collects_violations():
    def make(phase, mode):
        return PhaseMenu(phase=phase, mode_key=mode, slots=_minimal_load_slots())

    good = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))

    def make_bad(phase, mode):
        slots = _minimal_load_slots()
        contract = MenuContract(must_contain=frozenset({ActivityKey.RUN_VO2MAX}))
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots, contract=contract)

    bad = PlanBlueprint(goal=GoalType.HALF_IRONMAN, phases=_full_coverage(make_bad))

    with pytest.raises(MenuContractError) as exc_info:
        validate_all({GoalType.IRONMAN: good, GoalType.HALF_IRONMAN: bad})
    # Every (phase, mode) of the bad blueprint contributes a violation.
    assert len(exc_info.value.violations) >= 8


def test_validate_all_empty_returns_empty_list():
    assert validate_all({}) == []


def test_validate_blueprint_aggregates_only_per_tier_present():
    """When a Variant has per-tier bounds, only present tiers are checked."""

    def make(phase, mode):
        # Slot with only MODERATE-targeted Variant
        slot = Slot(
            name="mod_only",
            day=Weekday.MON,
            variants=(
                Variant(
                    activity_key=ActivityKey.RUN_EASY,
                    bounds={AmbitionLevel.MODERATE: (30, 45)},
                ),
            ),
        )
        contract = MenuContract(
            min_sport_sessions=MappingProxyType({SportType.RUNNING: 1})
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=(slot,), contract=contract)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    # MODERATE passes (1 run). BASIC and COMPETITIVE not in any bounds dict,
    # so they're skipped.
    assert validate_blueprint(blueprint) == []


def test_validate_blueprint_raises_on_duplicate_slot_names():
    def make(phase, mode):
        return PhaseMenu(
            phase=phase,
            mode_key=mode,
            slots=(
                *_minimal_load_slots(),
                session("long_bike", Weekday.TUE, ActivityKey.BIKE_ENDURANCE, (45, 60)),
            ),
        )

    blueprint = PlanBlueprint(
        goal=GoalType.IRONMAN,
        phases=_full_coverage(make),
    )
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    violations = [v for v in exc_info.value.violations if v.rule == "slot_name_unique"]
    assert violations
    assert "'long_bike'" in violations[0].detail


def _blueprint_with_build_load(slots: tuple) -> PlanBlueprint:
    """Full-coverage blueprint whose BUILD/load cell carries *slots*."""

    def make(phase, mode):
        cell = (
            slots
            if (phase, mode) == (PhaseType.BUILD, "load")
            else (_minimal_load_slots())
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=cell)

    return PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))


def _lint_rules(blueprint: PlanBlueprint) -> set[str]:
    with pytest.raises(MenuContractError) as exc_info:
        validate_blueprint(blueprint)
    return {v.rule for v in exc_info.value.violations}


def test_day_capacity_violation_on_three_a_day():
    blueprint = _blueprint_with_build_load(
        (
            session("a", Weekday.TUE, ActivityKey.RUN_EASY, (30, 45)),
            session("b", Weekday.TUE, ActivityKey.BIKE_ENDURANCE, (45, 60)),
            session("c", Weekday.TUE, ActivityKey.SWIM_ENDURANCE, (1500, 2000)),
        )
    )
    assert "day_capacity" in _lint_rules(blueprint)


def test_same_sport_same_day_violation():
    blueprint = _blueprint_with_build_load(
        (
            session("r1", Weekday.THU, ActivityKey.RUN_EASY, (30, 45)),
            session("r2", Weekday.THU, ActivityKey.RUN_LONG, (60, 90)),
        )
    )
    assert "same_sport_same_day" in _lint_rules(blueprint)


def test_same_sport_same_day_fires_at_inclusive_frac_boundary():
    """The marathon-Thu defect shape: touching inclusive frac windows.

    Two same-day run slots with windows (0, 0.5) and (0.5, 1.0) both
    fire at phase_frac exactly 0.5 when they are NOT slot-grouped — the
    breakpoint sampling must catch this co-fire.
    """
    blueprint = _blueprint_with_build_load(
        (
            session(
                "early",
                Weekday.THU,
                ActivityKey.RUN_THRESHOLD,
                (40, 55),
                frac=(0.0, 0.5),
            ),
            session(
                "late",
                Weekday.THU,
                ActivityKey.RUN_FULL_MP,
                (50, 70),
                frac=(0.5, 1.0),
            ),
        )
    )
    assert "same_sport_same_day" in _lint_rules(blueprint)


def test_slot_grouped_same_day_alternatives_pass():
    """Slot-grouped frac alternatives resolve to one winner — no lint."""
    blueprint = _blueprint_with_build_load(
        (
            session(
                "early",
                Weekday.THU,
                ActivityKey.RUN_THRESHOLD,
                (40, 55),
                frac=(0.0, 0.5),
                slot_group="thu_quality",
            ),
            session(
                "late",
                Weekday.THU,
                ActivityKey.RUN_FULL_MP,
                (50, 70),
                frac=(0.5, 1.0),
                slot_group="thu_quality",
            ),
        )
    )
    assert validate_blueprint(blueprint) == []


def test_hard_adjacent_days_violation():
    blueprint = _blueprint_with_build_load(
        (
            session("q1", Weekday.TUE, ActivityKey.RUN_THRESHOLD, (40, 55)),
            session("q2", Weekday.WED, ActivityKey.RUN_VO2MAX, (30, 40)),
        )
    )
    assert "hard_adjacent_days" in _lint_rules(blueprint)


def test_hard_adjacent_days_is_rotation_aware():
    """A rotation member that is hard counts — rotated-in keys fire on
    real weeks."""
    blueprint = _blueprint_with_build_load(
        (
            session("q1", Weekday.TUE, ActivityKey.RUN_THRESHOLD, (40, 55)),
            session(
                "easy_rotating_hard",
                Weekday.WED,
                ActivityKey.RUN_EASY,
                (30, 45),
                rotation=(ActivityKey.RUN_EASY, ActivityKey.RUN_VO2MAX),
            ),
        )
    )
    assert "hard_adjacent_days" in _lint_rules(blueprint)


def test_brick_partner_day_violation():
    blueprint = _blueprint_with_build_load(
        (
            session("ride", Weekday.SAT, ActivityKey.BIKE_LONG, (180, 240)),
            session(
                "orphan_brick",
                Weekday.SUN,
                ActivityKey.RUN_BRICK_SHORT,
                (15, 25),
                is_brick_pair=True,
            ),
        )
    )
    assert "brick_partner_day" in _lint_rules(blueprint)


def test_race_menu_sunday_violation():
    def make(phase, mode):
        slots = _minimal_load_slots()
        if (phase, mode) == (PhaseType.TAPER, "race"):
            slots = (
                session("race_tue_easy", Weekday.TUE, ActivityKey.RUN_EASY, (25, 25)),
                session(
                    "race_sun_oops", Weekday.SUN, ActivityKey.RUN_STRIDES, (15, 15)
                ),
            )
        return PhaseMenu(phase=phase, mode_key=mode, slots=slots)

    blueprint = PlanBlueprint(goal=GoalType.IRONMAN, phases=_full_coverage(make))
    rules = _lint_rules(blueprint)
    assert "race_menu_sunday" in rules


def test_hard_adjacent_days_wraps_sunday_to_monday():
    """Consecutive LOAD weeks repeat the layout, so hard Sun + hard Mon
    are calendar-adjacent across the week boundary."""
    blueprint = _blueprint_with_build_load(
        (
            session("q_sun", Weekday.SUN, ActivityKey.RUN_VO2MAX, (30, 40)),
            session("q_mon", Weekday.MON, ActivityKey.RUN_THRESHOLD, (40, 55)),
        )
    )
    assert "hard_adjacent_days" in _lint_rules(blueprint)


def test_lint_samples_interior_slot_group_voronoi_boundaries():
    """A 3-member slot group's middle member can win only strictly inside
    every frac window (between midpoint-Voronoi boundaries): windows
    (0.2,0.6)/(0.28,0.6)/(0.36,0.6) give the middle member the win only
    on frac (0.42,0.46). A violation it authors must still be caught."""
    group = "vor"
    blueprint = _blueprint_with_build_load(
        (
            session(
                "x_easy",
                Weekday.WED,
                ActivityKey.RUN_EASY,
                (30, 45),
                frac=(0.2, 0.6),
                slot_group=group,
            ),
            session(
                "y_hard",
                Weekday.WED,
                ActivityKey.RUN_VO2MAX,
                (30, 40),
                frac=(0.28, 0.6),
                slot_group=group,
            ),
            session(
                "z_easy",
                Weekday.WED,
                ActivityKey.RUN_LONG,
                (60, 90),
                frac=(0.36, 0.6),
                slot_group=group,
            ),
            session("q_thu", Weekday.THU, ActivityKey.RUN_THRESHOLD, (40, 55)),
        )
    )
    assert "hard_adjacent_days" in _lint_rules(blueprint)


def test_all_real_blueprints_pass_day_layout_lints():
    """The shipped blueprints satisfy every day-layout lint.

    Import already proves this (each goal module validates at import
    bottom), but the explicit pass documents the invariant and fails
    with a readable violation list if a menu edit breaks it.
    """
    from endury.planner.sessions.menus import BLUEPRINTS

    assert validate_all(BLUEPRINTS) == []


def _blueprint_for_goal(goal: GoalType, slots: tuple) -> PlanBlueprint:
    """Like _blueprint_with_build_load but with an explicit goal."""

    def make(phase, mode):
        cell = (
            slots
            if (phase, mode) == (PhaseType.BUILD, "load")
            else (_minimal_load_slots())
        )
        return PhaseMenu(phase=phase, mode_key=mode, slots=cell)

    return PlanBlueprint(goal=goal, phases=_full_coverage(make))


def test_run_long_after_hard_run_day_violation():
    """Triathlon goals: a long run must not follow an intensive run day."""
    blueprint = _blueprint_with_build_load(
        (
            session("q", Weekday.TUE, ActivityKey.RUN_THRESHOLD, (40, 55)),
            session("lr", Weekday.WED, ActivityKey.RUN_LONG, (70, 90)),
        )
    )
    assert "run_long_after_hard_run_day" in _lint_rules(blueprint)


def test_run_long_after_tempo_run_counts_as_intensive():
    """Tempo is moderate for alternation but still gates the long run."""
    blueprint = _blueprint_with_build_load(
        (
            session("t", Weekday.TUE, ActivityKey.RUN_AEROBIC_TEMPO, (40, 55)),
            session("lr", Weekday.WED, ActivityKey.RUN_LONG, (70, 90)),
        )
    )
    rules = _lint_rules(blueprint)
    assert "run_long_after_hard_run_day" in rules
    assert "hard_adjacent_days" not in rules


def test_run_long_after_hard_run_wraps_sunday_to_monday():
    """Consecutive LOAD weeks repeat, so Sunday intensity gates Monday."""
    blueprint = _blueprint_with_build_load(
        (
            session("q", Weekday.SUN, ActivityKey.RUN_THRESHOLD, (40, 55)),
            session("lr", Weekday.MON, ActivityKey.RUN_LONG, (70, 90)),
        )
    )
    assert "run_long_after_hard_run_day" in _lint_rules(blueprint)


def test_run_long_after_hard_run_is_rotation_aware():
    blueprint = _blueprint_with_build_load(
        (
            session(
                "easy_rotating_hard",
                Weekday.TUE,
                ActivityKey.RUN_EASY,
                (30, 45),
                rotation=(ActivityKey.RUN_EASY, ActivityKey.RUN_SPEED),
            ),
            session("lr", Weekday.WED, ActivityKey.RUN_LONG, (70, 90)),
        )
    )
    assert "run_long_after_hard_run_day" in _lint_rules(blueprint)


def test_run_long_after_a_brick_run_is_the_weekends_own_fatigue():
    """A brick run off the long ride does not gate the Sunday long run.

    The same Saturday tempo run as a standalone session still does.
    """
    ride = session("ride", Weekday.SAT, ActivityKey.BIKE_LONG, (180, 240))
    long_run = session("lr", Weekday.SUN, ActivityKey.RUN_LONG, (70, 90))
    brick = session(
        "brick",
        Weekday.SAT,
        ActivityKey.RUN_BRICK_HIM_TEMPO,
        (33, 44),
        is_brick_pair=True,
    )
    standalone = session("tempo", Weekday.SAT, ActivityKey.RUN_TEMPO, (40, 55))

    assert validate_blueprint(_blueprint_with_build_load((ride, brick, long_run))) == []
    assert "run_long_after_hard_run_day" in _lint_rules(
        _blueprint_with_build_load((ride, standalone, long_run))
    )


def test_run_only_goals_may_stack_quality_before_the_long_run():
    """Run-only plans use accumulated-fatigue stacking deliberately."""
    blueprint = _blueprint_for_goal(
        GoalType.RUNNING_10K,
        (
            session("q", Weekday.SAT, ActivityKey.RUN_THRESHOLD, (40, 55)),
            session("lr", Weekday.SUN, ActivityKey.RUN_LONG, (70, 90)),
        ),
    )
    assert validate_blueprint(blueprint) == []


def test_day_layout_lints_replay_each_cadence_firing():
    # The test shares Thursday with the easy run only on its cadence weeks.
    blueprint = _blueprint_with_build_load(
        (
            session("r1", Weekday.THU, ActivityKey.RUN_EASY, (30, 45)),
            session(
                "r2",
                Weekday.THU,
                ActivityKey.RUN_THRESHOLD_TEST,
                (44, 44),
                cadence_weeks=8,
            ),
        )
    )
    assert "same_sport_same_day" in _lint_rules(blueprint)
