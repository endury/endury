"""Tests for the deterministic "why this session" composer (sessions/why)."""

from __future__ import annotations

from datetime import date
from types import MappingProxyType

import pytest

from endury.planner.activity import ensure_registry_loaded
from endury.planner.foundations.types import (
    ActivityAssignment,
    ActivityKey,
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
from endury.planner.pipeline import PlanResult
from endury.planner.sessions import why as mod
from endury.planner.sessions.menus.types import (
    PhaseMenu,
    PlanBlueprint,
    Progression,
    Rationale,
    Slot,
    Variant,
    Weekday,
)

ensure_registry_loaded()

GOAL = GoalType.RUNNING_MARATHON
#: Plan week 0 opens on this Monday; every fixture date derives from it.
MONDAY = date(2026, 3, 2)


def _week(
    index: int,
    *,
    phase: PhaseType = PhaseType.BUILD,
    mode: WeekMode = WeekMode.LOAD,
) -> WeekSpec:
    return WeekSpec(
        week=index,
        phase=phase,
        mode=mode,
        sport_split={SportType.RUNNING: 1.0},
        effective_goal=GOAL,
        owning_goal=None,
    )


def _placed(
    slot_id: str,
    week: int,
    *,
    key: ActivityKey = ActivityKey.RUN_LONG,
    sport: SportType = SportType.RUNNING,
    slot_name: str | None = "long_run",
    origin: SessionOrigin = SessionOrigin.ANCHOR,
) -> PlacedSession:
    return PlacedSession(
        slot_id=slot_id,
        activity_key=key,
        sport=sport,
        origin=origin,
        week=week,
        day_of_week=6,
        target_hours=1.0,
        slot_name=slot_name,
    )


def _result(
    weeks: list[WeekSpec],
    sessions: list[PlacedSession],
    *,
    keys: dict[str, ActivityKey] | None = None,
) -> PlanResult:
    keys = keys or {}
    assignments = [
        ActivityAssignment(
            slot_id=s.slot_id,
            date=date.fromordinal(MONDAY.toordinal() + 7 * s.week + 6),
            activity_key=keys.get(s.slot_id, s.activity_key),
            target_hours=s.target_hours,
        )
        for s in sessions
    ]
    return PlanResult(
        phase_plan=PhasePlan(
            weeks=weeks,
            race_events=[],
            a_goal=RaceGoal(goal_type=GOAL, race_date=date(2026, 6, 7), priority="A"),
        ),
        sessions=sessions,
        assignments=assignments,
    )


def _blueprint(*slots: Slot, mode_key: str = "load") -> PlanBlueprint:
    return PlanBlueprint(
        goal=GOAL,
        phases=MappingProxyType(
            {
                (PhaseType.BUILD, mode_key): PhaseMenu(
                    phase=PhaseType.BUILD, mode_key=mode_key, slots=slots
                )
            }
        ),
    )


@pytest.fixture
def menu(monkeypatch) -> callable:
    """Install a synthetic BLUEPRINTS registry for the composer to read."""

    def install(*slots: Slot, mode_key: str = "load") -> None:
        monkeypatch.setattr(
            mod, "BLUEPRINTS", {GOAL: _blueprint(*slots, mode_key=mode_key)}
        )

    return install


class TestAuthoredWhy:
    def test_variant_rationale_wins_over_the_slot(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Variant why."),
                    ),
                ),
                rationale=Rationale(athlete_why="Slot why."),
            )
        )
        result = _result([_week(0)], [_placed("s0", 0)])
        assert mod.compose_why_details(result, {})["s0"][0] == "Variant why."

    def test_slot_rationale_fills_in_for_a_variant_without_one(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(intent="dev-only"),
                    ),
                ),
                rationale=Rationale(athlete_why="Slot why."),
            )
        )
        result = _result([_week(0)], [_placed("s0", 0)])
        assert mod.compose_why_details(result, {})["s0"][0] == "Slot why."

    def test_a_rotated_key_still_matches_its_variant(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rotation=(
                            ActivityKey.RUN_LONG,
                            ActivityKey.RUN_LONG_PROGRESSION,
                        ),
                        rationale=Rationale(athlete_why="Rotated why."),
                    ),
                ),
            )
        )
        result = _result(
            [_week(0)],
            [_placed("s0", 0)],
            keys={"s0": ActivityKey.RUN_LONG_PROGRESSION},
        )
        assert mod.compose_why_details(result, {})["s0"][0] == "Rotated why."

    def test_an_unauthored_slot_falls_back_to_the_family_sentence(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(activity_key=ActivityKey.RUN_LONG, bounds=(60, 120)),
                ),
            )
        )
        result = _result([_week(0)], [_placed("s0", 0)])
        assert (
            mod.compose_why_details(result, {})["s0"][0]
            == mod.FAMILY_WHY[mod.ActivityFamily.LONG]
        )

    def test_a_slot_eased_after_a_tune_up_race_says_so(self, menu) -> None:
        # R-SESS-031: the generator books an easy run in a threshold slot; the
        # slot's own why describes the hard session it replaced.
        menu(
            Slot(
                name="tue_threshold",
                day=Weekday.TUE,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_THRESHOLD,
                        bounds=(45, 60),
                        rationale=Rationale(athlete_why="Never rendered."),
                    ),
                ),
            )
        )
        placed = _placed("s0", 0, key=ActivityKey.RUN_EASY, slot_name="tue_threshold")
        result = _result([_week(0)], [placed])
        assert mod.compose_why_details(result, {})["s0"][0] == mod.EASED_AFTER_RACE_WHY

    def test_a_fitness_test_never_reads_the_menu(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_THRESHOLD_TEST,
                        bounds=(30, 60),
                        rationale=Rationale(athlete_why="Never rendered."),
                    ),
                ),
            )
        )
        placed = _placed(
            "s0", 0, key=ActivityKey.RUN_THRESHOLD_TEST, origin=SessionOrigin.TEST
        )
        result = _result([_week(0)], [placed])
        assert (
            mod.compose_why_details(result, {})["s0"][0]
            == mod.FAMILY_WHY[mod.ActivityFamily.TEST]
        )

    def test_a_non_menu_session_has_no_slot_provenance(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Never rendered."),
                    ),
                ),
            )
        )
        result = _result([_week(0)], [_placed("s0", 0, slot_name=None)])
        assert "Never rendered." not in mod.compose_why_details(result, {})["s0"][0]

    def test_a_goal_with_no_blueprint_falls_back(self, monkeypatch) -> None:
        monkeypatch.setattr(mod, "BLUEPRINTS", {})
        result = _result([_week(0)], [_placed("s0", 0)])
        assert (
            mod.compose_why_details(result, {})["s0"][0]
            == mod.FAMILY_WHY[mod.ActivityFamily.LONG]
        )

    def test_a_phase_mode_the_blueprint_does_not_cover_falls_back(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Never rendered."),
                    ),
                ),
            ),
            mode_key="taper",
        )
        result = _result([_week(0)], [_placed("s0", 0)])
        assert "Never rendered." not in mod.compose_why_details(result, {})["s0"][0]

    def test_an_unknown_slot_name_falls_back(self, menu) -> None:
        menu(
            Slot(
                name="other_slot",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Never rendered."),
                    ),
                ),
            )
        )
        result = _result([_week(0)], [_placed("s0", 0)])
        assert "Never rendered." not in mod.compose_why_details(result, {})["s0"][0]

    def test_a_race_week_without_a_race_menu_reads_the_recovery_menu(
        self, menu
    ) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Recovery-menu why."),
                    ),
                ),
            ),
            mode_key="recovery",
        )
        result = _result(
            [_week(0, mode=WeekMode.RACE)],
            [_placed("s0", 0)],
        )
        assert mod.compose_why_details(result, {})["s0"][0] == "Recovery-menu why."

    def test_a_race_week_with_a_race_menu_reads_it(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Race-menu why."),
                    ),
                ),
            ),
            mode_key="race",
        )
        result = _result([_week(0, mode=WeekMode.RACE)], [_placed("s0", 0)])
        assert mod.compose_why_details(result, {})["s0"][0] == "Race-menu why."

    def test_a_taper_week_reads_the_taper_menu(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        rationale=Rationale(athlete_why="Taper-menu why."),
                    ),
                ),
            ),
            mode_key="taper",
        )
        result = _result([_week(0, mode=WeekMode.TAPER)], [_placed("s0", 0)])
        assert mod.compose_why_details(result, {})["s0"][0] == "Taper-menu why."


class TestProgressionPosition:
    def _menu_with_progression(self, menu, name: str = "long_run_ramp") -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        progression=Progression(name=name),
                    ),
                    Variant(
                        activity_key=ActivityKey.RUN_LONG_PROGRESSION,
                        bounds=(60, 120),
                        progression=Progression(name=name),
                    ),
                ),
            )
        )

    def test_a_block_of_one_gets_no_position_paragraph(self, menu) -> None:
        self._menu_with_progression(menu)
        result = _result([_week(0)], [_placed("s0", 0)])
        assert len(mod.compose_why_details(result, {"s0": (60.0, 0.0)})["s0"]) == 2

    def test_a_shared_progression_groups_across_activity_keys(self, menu) -> None:
        self._menu_with_progression(menu)
        result = _result(
            [_week(0), _week(1)],
            [_placed("s0", 0), _placed("s1", 1)],
            keys={"s1": ActivityKey.RUN_LONG_PROGRESSION},
        )
        details = mod.compose_why_details(
            result, {"s0": (60.0, 0.0), "s1": (80.0, 0.0)}
        )
        assert (
            details["s0"][1] == "Session 1 of 2 in this block — building 60 → 80 "
            "min across it."
        )
        assert details["s1"][1].startswith("Session 2 of 2")

    def test_a_flat_trajectory_drops_the_building_clause(self, menu) -> None:
        self._menu_with_progression(menu)
        result = _result([_week(0), _week(1)], [_placed("s0", 0), _placed("s1", 1)])
        details = mod.compose_why_details(
            result, {"s0": (60.0, 0.0), "s1": (62.0, 0.0)}
        )
        assert details["s0"][1] == "Session 1 of 2 in this block."

    def test_swims_speak_metres(self, menu) -> None:
        menu(
            Slot(
                name="long_swim",
                day=Weekday.SUN,
                variants=(
                    Variant(activity_key=ActivityKey.SWIM_LONG, bounds=(2000, 3000)),
                ),
            )
        )
        sessions = [
            _placed(
                "s0",
                0,
                key=ActivityKey.SWIM_LONG,
                sport=SportType.SWIMMING,
                slot_name="long_swim",
            ),
            _placed(
                "s1",
                1,
                key=ActivityKey.SWIM_LONG,
                sport=SportType.SWIMMING,
                slot_name="long_swim",
            ),
        ]
        result = _result([_week(0), _week(1)], sessions)
        details = mod.compose_why_details(
            result, {"s0": (40.0, 2000.0), "s1": (55.0, 2800.0)}
        )
        assert "building 2000 → 2800 m across it" in details["s0"][1]

    def test_sessions_without_a_progression_group_on_slot_and_key(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(activity_key=ActivityKey.RUN_LONG, bounds=(60, 120)),
                ),
            )
        )
        result = _result([_week(0), _week(1)], [_placed("s0", 0), _placed("s1", 1)])
        details = mod.compose_why_details(
            result, {"s0": (60.0, 0.0), "s1": (90.0, 0.0)}
        )
        assert "Session 1 of 2 in this block" in details["s0"][1]

    def test_a_slot_missing_from_the_materialized_map_reads_as_flat(self, menu) -> None:
        self._menu_with_progression(menu)
        result = _result([_week(0), _week(1)], [_placed("s0", 0), _placed("s1", 1)])
        details = mod.compose_why_details(result, {})
        assert details["s0"][1] == "Session 1 of 2 in this block."


class TestPhaseParagraph:
    def _sentence(self, weeks: list[WeekSpec], week_index: int) -> str:
        result = _result(weeks, [_placed("s0", week_index, slot_name=None)])
        return mod.compose_why_details(result, {})["s0"][-1]

    def test_a_load_week_names_the_next_recovery_week(self) -> None:
        weeks = [_week(0), _week(1), _week(2, mode=WeekMode.RECOVERY)]
        assert self._sentence(weeks, 0) == (
            "Build week 1 of 3. Next recovery week starts Mar 16."
        )

    def test_a_load_week_with_no_recovery_ahead_says_only_the_number(self) -> None:
        assert self._sentence([_week(0), _week(1)], 1) == "Build week 2 of 2."

    def test_a_recovery_week(self) -> None:
        weeks = [_week(0), _week(1, mode=WeekMode.RECOVERY)]
        assert self._sentence(weeks, 1).startswith("Recovery week 2 of 2 — absorb")

    def test_a_taper_week(self) -> None:
        weeks = [_week(0), _week(1, phase=PhaseType.TAPER, mode=WeekMode.TAPER)]
        assert self._sentence(weeks, 1).startswith("Taper week 2 of 2 —")

    def test_a_race_week(self) -> None:
        weeks = [_week(0), _week(1, phase=PhaseType.TAPER, mode=WeekMode.RACE)]
        assert self._sentence(weeks, 1).startswith("Race week 2 of 2 —")

    def test_a_post_race_recovery_week(self) -> None:
        weeks = [_week(0), _week(1, mode=WeekMode.RACE_RECOVERY)]
        assert self._sentence(weeks, 1).startswith(
            "Recovery week 2 of 2, straight after a race —"
        )

    def test_a_maintenance_week_overrides_its_mode(self) -> None:
        weeks = [_week(0, phase=PhaseType.MAINTAIN)]
        # It never denies a race: the athlete may hold one inside the block.
        assert (
            self._sentence(weeks, 0)
            == "Maintenance week 1 of 1 — holding the fitness you have."
        )


class TestShape:
    def test_a_plan_with_no_assignments_composes_nothing(self) -> None:
        assert mod.compose_why_details(_result([_week(0)], []), {}) == {}

    def test_every_session_gets_at_most_three_paragraphs(self, menu) -> None:
        menu(
            Slot(
                name="long_run",
                day=Weekday.SUN,
                variants=(
                    Variant(
                        activity_key=ActivityKey.RUN_LONG,
                        bounds=(60, 120),
                        progression=Progression(name="ramp"),
                        rationale=Rationale(athlete_why="Authored."),
                    ),
                ),
            )
        )
        weeks = [_week(0), _week(1), _week(2, mode=WeekMode.RECOVERY)]
        result = _result(weeks, [_placed("s0", 0), _placed("s1", 1)])
        details = mod.compose_why_details(
            result, {"s0": (60.0, 0.0), "s1": (90.0, 0.0)}
        )
        assert details["s0"] == [
            "Authored.",
            "Session 1 of 2 in this block — building 60 → 90 min across it.",
            "Build week 1 of 3. Next recovery week starts Mar 16.",
        ]
