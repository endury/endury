"""Tests for the deterministic coach brief (coach_brief)."""

from __future__ import annotations

from datetime import date, timedelta

from endury.planner.foundations.request import AthleteState, PlanRequest, SportState
from endury.planner.foundations.types import (
    ActivityFamily,
    AmbitionLevel,
    GoalType,
    PhasePlan,
    PhaseRequest,
    PhaseType,
    RaceGoal,
    SportType,
    WeekMode,
    WeekSpec,
)
from endury.planner.phase.phase_planner import plan_phases
from endury.planner.pipeline import default_profile_for, generate_plan
from endury.planning.coach_brief import build_coach_brief
from endury.planning.materialize_plan import materialize_sessions

_MONDAY = date(2026, 1, 5)


def _marathon_plan_with_b_race() -> PhasePlan:
    goals = [
        RaceGoal(
            goal_type=GoalType.RUNNING_MARATHON,
            race_date=_MONDAY + timedelta(weeks=24),
            priority="A",
        ),
        RaceGoal(
            goal_type=GoalType.RUNNING_10K,
            race_date=_MONDAY + timedelta(weeks=12),
            priority="B",
        ),
    ]
    return plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))


def _sessions(minutes_by_week: dict[int, float]) -> list[dict]:
    return [
        {"week_index": week, "duration_minutes": minutes}
        for week, minutes in minutes_by_week.items()
    ]


class TestBuildCoachBrief:
    def test_full_plan_brief_mentions_all_sections(self) -> None:
        plan = _marathon_plan_with_b_race()
        brief = build_coach_brief(
            plan,
            _sessions({0: 300.0, 10: 720.0, 20: 480.0}),
            ambition=AmbitionLevel.MODERATE,
        )
        assert f"{len(plan.weeks)}-week marathon plan" in brief
        assert "moderate ambition" in brief
        assert plan.a_goal.race_date.isoformat() in brief
        assert "Phase arc:" in brief
        assert "base" in brief
        assert "taper" in brief.lower()
        assert "Tune-up races: 10K (B-race) in week 13." in brief
        assert "Volume peaks around 12.0 h in week 11." in brief
        assert "recovery week" in brief

    def test_phase_arc_labels_tune_up_race_weeks(self) -> None:
        """A B/C race week reads as that tune-up, not a stray phase."""
        plan = _marathon_plan_with_b_race()
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.MODERATE)
        assert "(tune-up: 10K, B-race)" in brief
        # The A-goal phases stay unlabelled.
        arc = brief.split("Phase arc: ")[1]
        a_taper = arc.rstrip(".").split(", ")[-1]
        assert "tune-up" not in a_taper

    def test_minimal_plan_without_optional_sections(self) -> None:
        """A bare hand-built plan exercises every skipped-section branch."""
        a_goal = RaceGoal(
            goal_type=GoalType.RUNNING_5K, race_date=_MONDAY, priority="A"
        )
        weeks = [
            WeekSpec(
                week=0,
                phase=PhaseType.BUILD,
                mode=WeekMode.LOAD,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_5K,
                owning_goal=None,
            )
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=a_goal)
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.BASIC)
        assert brief.startswith("1-week 5K plan at basic ambition")
        assert "Phase arc: build week 1." in brief
        assert "Tune-up" not in brief
        assert "Volume peaks" not in brief
        assert "recovery" not in brief
        assert "taper" not in brief

    def test_single_recovery_week_uses_singular(self) -> None:
        a_goal = RaceGoal(
            goal_type=GoalType.RUNNING_10K, race_date=_MONDAY, priority="A"
        )
        weeks = [
            WeekSpec(
                week=0,
                phase=PhaseType.BASE,
                mode=WeekMode.RECOVERY,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_10K,
                owning_goal=None,
            )
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=a_goal)
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.BASIC)
        assert "1 scheduled recovery week keeps" in brief
        assert "weeks keep" not in brief

    def test_prior_weeks_shifts_numbering_to_the_full_plan_frame(self) -> None:
        """A replan fragment must read as the remainder of the whole plan."""
        a_goal = RaceGoal(
            goal_type=GoalType.RUNNING_10K,
            race_date=_MONDAY + timedelta(weeks=12),
            priority="A",
        )
        weeks = [
            WeekSpec(
                week=0,
                phase=PhaseType.BUILD,
                mode=WeekMode.RECOVERY,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_10K,
                owning_goal=None,
            ),
            WeekSpec(
                week=1,
                phase=PhaseType.TAPER,
                mode=WeekMode.LOAD,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_10K,
                owning_goal=None,
            ),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=a_goal)
        brief = build_coach_brief(
            plan,
            _sessions({0: 300.0, 1: 120.0}),
            ambition=AmbitionLevel.MODERATE,
            prior_weeks=10,
        )
        assert brief.startswith("12-week 10K plan")
        assert "Resumed mid-plan at week 11." in brief
        assert "Phase arc: build week 11, taper week 12." in brief
        assert "Volume peaks around 5.0 h in week 11." in brief
        assert "1 remaining recovery week keeps" in brief

    def test_unknown_goal_label_falls_back_to_slug(self) -> None:
        from endury.planning.coach_brief import _goal_label

        assert _goal_label("running_marathon") == "marathon"
        assert _goal_label("future_goal") == "future goal"


def _athlete_state(
    hours: dict[str, float], sources: dict[str, str] | None = None
) -> dict:
    """Minimal ``athlete_state`` payload shape for coach_brief tests."""
    if sources is None:
        sources = dict.fromkeys(hours, "history")
    return {
        "sources": sources,
        "sports": {sport: {"recent_weekly_hours": h} for sport, h in hours.items()},
    }


class TestBuiltFromInputs:
    """The "Built at ... from ..." inputs sentence."""

    def test_maintenance_brief_names_the_tier_and_reads_above(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.RUNNING_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.MODERATE,
            athlete_state=_athlete_state({"running": 2.4}),
        )
        assert (
            "Built at moderate ambition from 2.4 h/week of measured running "
            "(intervals.icu history)" in brief
        )
        assert "the block holds the tier's 4.5 h/week" in brief
        assert "above what you currently train. Phase arc:" in brief

    def test_maintenance_brief_reads_about_within_ten_percent(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.RUNNING_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.MODERATE,
            athlete_state=_athlete_state({"running": 4.3}),
        )
        assert "about what you currently train" in brief

    def test_maintenance_brief_reads_below_when_tier_is_lighter(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.RUNNING_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.MODERATE,
            athlete_state=_athlete_state({"running": 6.0}),
        )
        assert "below what you currently train" in brief

    def test_maintenance_brief_skips_comparison_with_no_measured_hours(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.RUNNING_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.MODERATE,
            athlete_state=_athlete_state({"running": 0.0}),
        )
        assert "0.0 h/week of measured running" in brief
        assert "the block holds the tier's" not in brief

    def test_race_brief_carries_the_ramp_to_peak(self) -> None:
        a_goal = RaceGoal(
            goal_type=GoalType.RUNNING_10K,
            race_date=_MONDAY + timedelta(weeks=1),
            priority="A",
        )
        weeks = [
            WeekSpec(
                week=0,
                phase=PhaseType.BASE,
                mode=WeekMode.LOAD,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_10K,
                owning_goal=None,
            ),
            WeekSpec(
                week=1,
                phase=PhaseType.BUILD,
                mode=WeekMode.LOAD,
                sport_split={SportType.RUNNING: 1.0},
                effective_goal=GoalType.RUNNING_10K,
                owning_goal=None,
            ),
        ]
        plan = PhasePlan(weeks=weeks, race_events=[], a_goal=a_goal)
        brief = build_coach_brief(
            plan,
            _sessions({0: 180.0, 1: 300.0}),
            ambition=AmbitionLevel.MODERATE,
            athlete_state=_athlete_state(
                {"running": 7.0}, sources={"running": "self_report"}
            ),
        )
        assert (
            "Built at moderate ambition from 7.0 h/week of running (self-reported)"
            in brief
        )
        assert (
            "weekly volume ramps from 3.0 h in week 1 to the peak. Phase arc:" in brief
        )
        assert "Volume peaks around 5.0 h in week 2." in brief

    def test_multi_sport_same_source_uses_one_combined_clause(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.TRIATHLON_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.COMPETITIVE,
            athlete_state=_athlete_state(
                {"swimming": 1.0, "cycling": 3.0, "running": 2.0}
            ),
        )
        assert (
            "6.0 h/week of measured swimming, cycling, running "
            "(intervals.icu history)" in brief
        )

    def test_multi_sport_mixed_sources_get_a_clause_each(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.TRIATHLON_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.COMPETITIVE,
            athlete_state=_athlete_state(
                {"swimming": 1.0, "cycling": 3.0, "running": 2.0},
                sources={
                    "swimming": "default",
                    "cycling": "history",
                    "running": "self_report",
                },
            ),
        )
        assert "1.0 h/week of swimming (goal default)" in brief
        assert "3.0 h/week of measured cycling (intervals.icu history)" in brief
        assert "2.0 h/week of running (self-reported)" in brief

    def test_no_sports_in_state_appends_no_inputs_sentence(self) -> None:
        plan = _marathon_plan_with_b_race()
        brief = build_coach_brief(
            plan,
            [],
            ambition=AmbitionLevel.MODERATE,
            athlete_state={"sources": {}, "sports": {}},
        )
        assert "Built at" not in brief

    def test_no_athlete_state_leaves_existing_brief_unchanged(self) -> None:
        plan = _marathon_plan_with_b_race()
        brief = build_coach_brief(
            plan,
            _sessions({0: 300.0, 10: 720.0, 20: 480.0}),
            ambition=AmbitionLevel.MODERATE,
        )
        assert "Built at" not in brief


class TestTrailClimbGuidance:
    def test_trail_race_brief_carries_the_peak_climb_band(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.TRAIL_20K,
                race_date=_MONDAY + timedelta(weeks=12, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.MODERATE)
        assert "Weekly climb guidance: roughly 1200-1800 m" in brief
        assert "at peak" in brief

    def test_trail_maintenance_brief_holds_a_steady_climb_band(self) -> None:
        goals = [
            RaceGoal(
                goal_type=GoalType.TRAIL_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.BASIC)
        assert "Weekly climb guidance: roughly 450-750 m" in brief
        assert "steady" in brief

    def test_road_brief_carries_no_climb_guidance(self) -> None:
        plan = _marathon_plan_with_b_race()
        brief = build_coach_brief(plan, [], ambition=AmbitionLevel.MODERATE)
        assert "climb guidance" not in brief


class TestTrailClimbBandScalesWithPeak:
    """D6: the climb band follows the plan's own peak week, not the tier's."""

    def test_a_squeezed_plans_band_scales_down(self) -> None:
        """The athlete: competitive trail 50K, joins week 8 of 12, 3.53 h/wk
        (tests/planner/test_menu_contract_survives_sizing.py's fixture).

        Its own peak week is about 4 h. The competitive 50K band's
        peak-hours anchor is 9.0 h (docs/research/trail_running.md §13.4,
        "Peak weekly hours by tier"), so the band 2800-3500 m scales by
        share = peak hours / 9.0, each end rounded to the nearest 100 m.
        The plan's exact minutes move with the planner; the rule does not.
        """
        athlete = AthleteState(
            sports={
                SportType.RUNNING: SportState(
                    recent_weekly_hours=3.53,
                    longest_recent_min={ActivityFamily.LONG: 230},
                    threshold_pace_sec_per_km=250,
                )
            }
        )
        result = generate_plan(
            PlanRequest(
                goals=[RaceGoal(GoalType.TRAIL_50K, date(2026, 10, 24), "A")],
                training_start=date(2026, 9, 21),
                ambition=AmbitionLevel.COMPETITIVE,
                athlete=athlete,
                prior_training_weeks=7,
            )
        )
        # The booked plan: the brief never reads an optional session.
        optional = {s.slot_id for s in result.sessions if s.optional_reason}
        sessions = [
            s
            for s in materialize_sessions(result, None)
            if s["slot_id"] not in optional
        ]
        weeks = {s["week_index"] for s in sessions}
        peak = max(
            sum(s["duration_minutes"] for s in sessions if s["week_index"] == week)
            for week in weeks
        )
        share = peak / 60.0 / 9.0
        assert 0.35 < share < 0.5  # a peak week of 3.2 to 4.5 h
        brief = build_coach_brief(
            result.phase_plan,
            sessions,
            ambition=AmbitionLevel.COMPETITIVE,
            prior_weeks=7,
        )
        low, high = round(2800 * share, -2), round(3500 * share, -2)
        assert f"Weekly climb guidance: roughly {low:.0f}-{high:.0f} m" in brief
        assert high < 2800  # well under the tier's unscaled band

    def test_default_plans_at_16_weeks_scale_per_tier(self) -> None:
        """BASIC (peak 5.6 h) and MODERATE (peak 8.5 h) both clear their
        tier's own anchor (5.0 h, 7.0 h) so their band is unscaled, same as
        before this change. COMPETITIVE peaks at 8.63 h on its cards (its
        recurring sessions rotate their sets), just under its 9.0 h anchor:
        share = 8.63 / 9.0 = 0.959; 2800 * 0.959 = 2685 -> 2700,
        3500 * 0.959 = 3356 -> 3400.
        """
        start = date(2026, 9, 7)
        expected = {
            AmbitionLevel.BASIC: "1500-2000",
            AmbitionLevel.MODERATE: "2000-2800",
            AmbitionLevel.COMPETITIVE: "2700-3400",
        }
        for ambition, band in expected.items():
            result = generate_plan(
                PlanRequest(
                    goals=[
                        RaceGoal(
                            GoalType.TRAIL_50K,
                            start + timedelta(weeks=16, days=-1),
                            "A",
                        )
                    ],
                    training_start=start,
                    ambition=ambition,
                    athlete=default_profile_for(GoalType.TRAIL_50K, ambition),
                )
            )
            # The booked plan: the brief never reads an optional session.
            sessions = [
                s for s in materialize_sessions(result, None) if not s["optional"]
            ]
            brief = build_coach_brief(result.phase_plan, sessions, ambition=ambition)
            assert f"Weekly climb guidance: roughly {band} m" in brief

    def test_maintenance_band_stays_unscaled_with_real_sessions(self) -> None:
        """No maintenance row exists in the peak-hours table, so even a real,
        non-empty week of sessions never scales the steady band.
        """
        goals = [
            RaceGoal(
                goal_type=GoalType.TRAIL_MAINTENANCE,
                race_date=_MONDAY + timedelta(weeks=8, days=-1),
                priority="A",
            )
        ]
        plan = plan_phases(PhaseRequest(goals=goals, training_start=_MONDAY))
        brief = build_coach_brief(
            plan, _sessions({0: 1000.0, 1: 1200.0}), ambition=AmbitionLevel.BASIC
        )
        assert "Weekly climb guidance: roughly 450-750 m" in brief
