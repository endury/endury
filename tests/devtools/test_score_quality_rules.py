"""Behavioral checks for the score_quality rules and their bands.

Each rule must *discriminate*: fire on a plan that genuinely exhibits its
defect (or on a mechanism-level injection of it, where every current plan
is clean) and read 0.0 on the fixed plans. The bands themselves live in
the rulebook (``bench/rulebook.yaml``; its loader is tested in
``tests/bench/test_rulebook.py``), and the import-time registry check gets
its own test at the end.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bench.rules import Band, Rulebook
from devtools.planner._shared.metrics import PipelineResult
from devtools.planner._shared.pipeline import default_profile_for
from devtools.planner._shared.scenarios import SCENARIOS
from devtools.quality.planner.score_quality import (
    RULEBOOK,
    _ends_on_easy_block,
    build_high_volume_scenario,
    build_low_volume_scenario,
    check_rulebook,
    score,
)
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    AmbitionLevel,
    GoalType,
)


def _band(
    rule: str, goal: GoalType, tier: AmbitionLevel = AmbitionLevel.MODERATE
) -> Band:
    band = RULEBOOK.by_name(rule).band(goal.value, tier.value)
    assert band is not None, (rule, goal)
    return band


@pytest.mark.parametrize(
    ("builder", "fraction", "suffix"),
    [
        (build_low_volume_scenario, 0.85, "low"),
        (build_high_volume_scenario, 1.5, "high"),
    ],
)
def test_scaled_families_scale_the_tiers_default_athlete(builder, fraction, suffix):
    base = default_profile_for(GoalType.TRAIL_20K, AmbitionLevel.BASIC)
    scenario = builder(16, AmbitionLevel.BASIC, GoalType.TRAIL_20K)
    assert scenario.name == f"trail_20k_basic_16wk_{suffix}"
    for sport, state in base.sports.items():
        assert scenario.recent_hours[sport] == pytest.approx(
            state.recent_weekly_hours * fraction
        )
        for family, minutes in state.longest_recent_min.items():
            assert scenario.longest_recent[(sport, family)] == round(minutes * fraction)


def test_panel_authored_rules_are_registered() -> None:
    names = {r.name for r in RULEBOOK.rules}
    assert {
        "vo2max_peak_taper",
        "race_pace_long_bike_peak",
        "longest_long_session_in_peak",
        "build_brick_run_duration",
        "race_pace_ride_ends_at_effort",
        "run_long_fueling_cue_coverage",
        "vo2max_session_work_rest_ratio",
        "brick_pairs_with_key_ride",
        "rest_days",
        "cross_sport_hard_day_stacking",
    } <= names


def test_vo2max_peak_taper_discriminates() -> None:
    # Reads 0.0 on the fixed Ironman and half-Ironman plans: PEAK/TAPER
    # carries tempo/opener work, not INTERVAL. The rule covers LONG_TRI only,
    # so with both long-course goals clean it is a GUARD rather than an
    # active detector: no scenario exhibits the defect, so its discrimination
    # is asserted on the mechanism.
    assert score(SCENARIOS["ironman_moderate"]).per_rule["vo2max_peak_taper"] == 0.0
    assert score(SCENARIOS["ironman_competitive"]).per_rule["vo2max_peak_taper"] == 0.0
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["vo2max_peak_taper"] == 0.0
    )

    # Mechanism: inject structured intervals into PEAK and the rule fires,
    # one session at a time up to the 1.0 clip.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import _vo2max_peak_taper
    from endury.planner.foundations.types import PhaseType

    result = run_full_pipeline(SCENARIOS["half_ironman_moderate"])
    metrics = _compute_metrics(result)
    peak_weeks = {w.week for w in result.plan.weeks if w.phase == PhaseType.PEAK}
    victims = [i for i, s in enumerate(result.sessions) if s.week in peak_weeks][:2]
    assert len(victims) == 2

    for n, expected in ((1, 0.5), (2, 1.0)):
        sessions = list(result.sessions)
        for i in victims[:n]:
            sessions[i] = replace(sessions[i], activity_key=ActivityKey.RUN_VO2MAX)
        injected = replace(result, sessions=sessions)
        assert _vo2max_peak_taper(injected, metrics) == expected


def test_race_pace_long_bike_peak_discriminates() -> None:
    # Goal-scoped: half-Ironman is judged on its own key
    # (BIKE_LONG_HIM_RACE_PACE) and band, not the IM one. The fixed Ironman
    # tiers read 0.0 — including BASIC (ironman_recovery), whose PEAK
    # race-pace ride reaches 4.5 h.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import (
        _race_pace_long_bike_peak,
    )

    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["race_pace_long_bike_peak"]
        == 0.0
    )
    # Still discriminates: strip the race-pace rides and the missing-rehearsal
    # defect scores the full 1.0.
    result = run_full_pipeline(SCENARIOS["half_ironman_moderate"])
    metrics = _compute_metrics(result)
    stripped = replace(
        result,
        sessions=[
            s
            for s in result.sessions
            if s.activity_key != ActivityKey.BIKE_LONG_HIM_RACE_PACE
        ],
    )
    assert _race_pace_long_bike_peak(stripped, metrics) == 1.0
    assert (
        score(SCENARIOS["ironman_recovery"]).per_rule["race_pace_long_bike_peak"] == 0.0
    )
    assert (
        score(SCENARIOS["ironman_competitive"]).per_rule["race_pace_long_bike_peak"]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["race_pace_long_bike_peak"] == 0.0
    )


def test_longest_long_session_in_peak_discriminates(monkeypatch) -> None:
    # R-PP-002 detector: fires where the longest long session peaks before
    # PEAK and reads 0.0 on the fixed Ironman tiers — including BASIC
    # (ironman_recovery), whose longest bike/run/swim now land in PEAK. The
    # rule covers all three sports, so the ==0.0 assertions below also
    # require the longest SWIM to peak in PEAK.
    #
    # The positive control is running_returner_10k with the peak shed forced
    # before its two-week taper, the shape every short-taper plan had until
    # 2026-10-06: its last BUILD long run books 58 min, and its shed PEAK
    # weeks size their long run under it, so the longest run lands in BUILD.
    # The Ironman cells below taper three weeks and shed either way.
    from endury.planner.macro import capacity

    monkeypatch.setattr(capacity, "PEAK_SHED_MIN_TAPER_WEEKS", 0)
    assert (
        score(SCENARIOS["running_returner_10k"]).per_rule[
            "longest_long_session_in_peak"
        ]
        > 0.0
    )
    assert (
        score(SCENARIOS["ironman_recovery"]).per_rule["longest_long_session_in_peak"]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["longest_long_session_in_peak"]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_competitive"]).per_rule["longest_long_session_in_peak"]
        == 0.0
    )


def test_recovery_week_test_frequency_is_inert_without_tests() -> None:
    # A plan may legitimately prescribe no field tests at all (most plans do not), so
    # the rule must read 0.0 rather than scoring "0% of
    # recovery weeks tested" against a band derived from the plans that do.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import (
        _recovery_week_test_frequency,
    )

    ensure_registry_loaded()
    result = run_full_pipeline(SCENARIOS["ironman_moderate"])
    metrics = _compute_metrics(result)

    def is_test(key: ActivityKey) -> bool:
        return get_activity(key).family == ActivityFamily.TEST

    assert any(is_test(s.activity_key) for s in result.sessions)
    untested = replace(
        result, sessions=[s for s in result.sessions if not is_test(s.activity_key)]
    )
    assert _recovery_week_test_frequency(untested, metrics) == 0.0


def test_race_day_freshness_ratio_is_reachable_at_low_ctl() -> None:
    # The reason for a ratio rather than an absolute band: TSB <= CTL, so a
    # low-CTL athlete can never reach an absolute +15. The 5k plan is scored
    # against the shared fallback envelope and must be scoreable there, not
    # structurally condemned.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import (
        _race_day_freshness_ratio,
    )

    result = run_full_pipeline(SCENARIOS["5k_beginner"])
    metrics = _compute_metrics(result)
    # An absolute +15..+25 band is unreachable at this CTL; the ratio is not.
    assert metrics.race_day_s_tsb / 7.0 < 15.0
    assert _race_day_freshness_ratio(result, metrics) < 1.0


def test_race_day_freshness_ratio_discriminates_on_the_band_edges() -> None:
    # Both long-course tiers sit inside their goal bands, so — like
    # race_week_volume — this rule is a guard rather than an active
    # detector, and its discrimination is shown on the bands directly. Each
    # goal band must still catch a plan that arrives flat (under-tapered)
    # or detrained (over-tapered).
    for goal, mid in ((GoalType.IRONMAN, 0.41), (GoalType.HALF_IRONMAN, 0.35)):
        band = _band("race_day_freshness_ratio", goal)
        assert band.distance(mid) == 0.0, goal  # plans cluster here
        assert band.distance(0.05) > 0.5, goal  # arrives fatigued
        assert band.distance(0.75) > 0.5, goal  # arrives detrained


def test_load_pricing_agreement_is_registered_for_every_matrix_goal() -> None:
    from devtools.quality.planner.score_quality import GOAL_MATRIX_HORIZONS

    rule = RULEBOOK.by_name("load_pricing_agreement")
    for goal in GOAL_MATRIX_HORIZONS:
        assert goal.value in rule.goals, goal
        for tier in AmbitionLevel:
            assert _band("load_pricing_agreement", goal, tier), (goal, tier)


def test_load_pricing_agreement_reads_zero_on_the_fixed_plans() -> None:
    # Cards and QP sizing agree up to the linearisation at the menu target
    # and the 5-minute rounding of solved hours.
    for scenario in ("ironman_moderate", "marathon_competitive"):
        rep = score(SCENARIOS[scenario])
        assert rep.per_rule["load_pricing_agreement"] == 0.0, scenario


def _resize_session(
    result: PipelineResult, slot_id: str, hours: float
) -> PipelineResult:
    """Return *result* with one session's hours replaced, QP pricing untouched."""
    return replace(
        result,
        sessions=[
            replace(s, target_hours=hours) if s.slot_id == slot_id else s
            for s in result.sessions
        ],
    )


def test_load_pricing_agreement_catches_a_session_past_its_tss_max() -> None:
    # The AUTHORING.md trap: hours pushed past the activity's catalog TSS
    # window clamp the card at tss_max, so the card no longer carries the
    # load the QP sized.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import _load_pricing_agreement
    from endury.planner.activity.materialize import assignment_target_tss
    from endury.planner.foundations.types import WeekMode

    result = run_full_pipeline(SCENARIOS["marathon_competitive"])
    modes = {ws.week: ws.mode for ws in result.plan.weeks}
    long_run = max(
        (
            s
            for s in result.sessions
            if modes[s.week] == WeekMode.LOAD and s.activity_key == ActivityKey.RUN_LONG
        ),
        key=lambda s: s.target_hours,
    )
    hours = 4 * long_run.target_hours
    act = get_activity(long_run.activity_key)
    assert assignment_target_tss(act, hours, None) == act.tss_max  # clamped

    bad = _resize_session(result, long_run.slot_id, hours)
    assert _load_pricing_agreement(bad, _compute_metrics(bad)) == 1.0


def test_load_pricing_agreement_catches_a_session_below_its_shape_floor() -> None:
    # A shrunk run_long card follows its hours (the relaxed
    # _DURATION_FIRST_MIN_TSS floor), so the defect is shown on the
    # race-pace long ride, whose shape cannot build below its tss_min.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import _load_pricing_agreement
    from endury.planner.activity.materialize import assignment_target_tss
    from endury.planner.foundations.types import WeekMode

    result = run_full_pipeline(SCENARIOS["ironman_moderate"])
    modes = {ws.week: ws.mode for ws in result.plan.weeks}
    ride = max(
        (
            s
            for s in result.sessions
            if modes[s.week] == WeekMode.LOAD
            and s.activity_key == ActivityKey.BIKE_LONG_IM_RACE_PACE
        ),
        key=lambda s: s.target_hours,
    )
    hours = ride.target_hours / 20
    act = get_activity(ride.activity_key)
    assert assignment_target_tss(act, hours, None) >= act.tss_min  # clamped

    # The ride's 96-TSS floor against its 12-hour week is a 15 % gap, well
    # past the band's 0.11; the healthy plan reads 0.
    bad = _resize_session(result, ride.slot_id, hours)
    assert _load_pricing_agreement(result, _compute_metrics(result)) == 0.0
    assert _load_pricing_agreement(bad, _compute_metrics(bad)) > 0.3


def test_peak_weekly_hours_ambition_scaled() -> None:
    # The band scales with ambition (docs/research/triathlon_long_course.md,
    # Weekly Hours by Level): the BASIC recovery plan peaks ~11.8 h inside
    # the recreational 10-14 band, and the moderate plan ~13.4 h inside its
    # 12-16 band.
    assert score(SCENARIOS["ironman_recovery"]).per_rule["peak_weekly_hours"] == 0.0
    assert score(SCENARIOS["ironman_moderate"]).per_rule["peak_weekly_hours"] == 0.0
    # Discrimination is shown on the bands directly: a rule's discrimination
    # must not rest on another plan's rounding error.
    for goal in (GoalType.IRONMAN, GoalType.HALF_IRONMAN):
        bands = {tier: _band("peak_weekly_hours", goal, tier) for tier in AmbitionLevel}
        ranges = {tier: (band.low, band.high) for tier, band in bands.items()}
        for ambition, (low, high) in ranges.items():
            band = _band("peak_weekly_hours", goal, ambition)
            assert band.distance((low + high) / 2) == 0.0, (goal, ambition)
            # Half the band width under the floor / over the ceiling.
            span = high - low
            assert band.distance(low - span / 2) == 0.5, (goal, ambition)
            assert band.distance(high + span / 2) == 0.5, (goal, ambition)
        # Each tier's band sits strictly above the one below it.
        tiers = [ranges[a] for a in AmbitionLevel]
        assert [b[0] for b in tiers] == sorted({b[0] for b in tiers}), goal
        assert [b[1] for b in tiers] == sorted({b[1] for b in tiers}), goal


def test_recovery_cadence_ambition_scaled() -> None:
    # The band scales with ambition (docs/research/triathlon_long_course.md,
    # Recovery Cadence by Athlete Level): recreational BASIC trains a 2:1
    # load:recovery cadence (recovery share ~0.333), competitive/advanced
    # MODERATE/COMPETITIVE train 3:1 (share ~0.25), so BASIC needs its own
    # [0.27, 0.36] band rather than the shared 0.20-0.30 one.
    assert score(SCENARIOS["5k_beginner"]).per_rule["recovery_cadence"] == 0.0
    assert score(SCENARIOS["sprint_tri_beginner"]).per_rule["recovery_cadence"] == 0.0
    # MODERATE/COMPETITIVE keep the 3:1 band (0.20-0.30) and still read 0.0.
    assert score(SCENARIOS["ironman_moderate"]).per_rule["recovery_cadence"] == 0.0
    assert score(SCENARIOS["ironman_competitive"]).per_rule["recovery_cadence"] == 0.0
    # The IM-A/Oly-B multi-goal plan read ~0.167 (a 6:1 cadence) while its B
    # race took a five-week window; racing through, it keeps its 3:1 cadence.
    assert score(SCENARIOS["ironman_a_oly_b"]).per_rule["recovery_cadence"] == 0.0
    # Still discriminates: a 6:1 cadence (share ~0.143) is outside the band.
    assert _band("recovery_cadence", GoalType.IRONMAN).distance(1 / 7) > 0.0


def test_recovery_week_depth_discriminates() -> None:
    # Every long-course tier reads 0.0 since both long-course menus carry
    # phase-scaled recovery menus, and so does olympic since it gained them.
    # Discrimination is shown on a goal still sharing one recovery menu
    # across all phases — the 5K — plus the band.
    band = _band("recovery_week_depth", GoalType.IRONMAN)
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["recovery_week_depth"] == 0.0
    )
    assert (
        score(SCENARIOS["olympic_tri_moderate"]).per_rule["recovery_week_depth"] == 0.0
    )
    assert score(SCENARIOS["5k_beginner"]).per_rule["recovery_week_depth"] > 0.0
    assert band.distance(0.62) == 0.0  # healthy
    assert band.distance(0.30) == 1.0  # week off, not down
    assert band.distance(0.95) > 0.5  # no real recovery
    assert score(SCENARIOS["ironman_recovery"]).per_rule["recovery_week_depth"] == 0.0
    assert score(SCENARIOS["ironman_moderate"]).per_rule["recovery_week_depth"] == 0.0
    assert (
        score(SCENARIOS["ironman_competitive"]).per_rule["recovery_week_depth"] == 0.0
    )


def test_build_brick_run_duration_discriminates() -> None:
    # BUILD bricks must reach a durability-relevant off-the-bike run duration
    # (~33-42 min, the top of the docs/research/triathlon.md, Periodisation
    # of Bricks 20-40 min BUILD brick band). The fixed Ironman and
    # half-Ironman tiers read 0.0; olympic, still on a flat scaled(20, 30)
    # BUILD brick, holds the discrimination.
    assert (
        score(SCENARIOS["olympic_tri_moderate"]).per_rule["build_brick_run_duration"]
        > 0.0
    )
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["build_brick_run_duration"]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_recovery"]).per_rule["build_brick_run_duration"] == 0.0
    )
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["build_brick_run_duration"] == 0.0
    )
    assert (
        score(SCENARIOS["ironman_competitive"]).per_rule["build_brick_run_duration"]
        == 0.0
    )


def test_race_pace_ride_ends_at_effort_discriminates() -> None:
    # The IM race-pace long ride must finish AT race pace, not fade to its
    # easiest Z2 block (docs/research/triathlon_long_course.md, Training
    # Durability). The fixed Ironman tiers carry end_on_work=True on
    # BIKE_LONG_IM_RACE_PACE and read 0.0, as does the half-Ironman's own
    # race-pace ride.
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["race_pace_ride_ends_at_effort"]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_competitive"]).per_rule[
            "race_pace_ride_ends_at_effort"
        ]
        == 0.0
    )
    assert (
        score(SCENARIOS["ironman_recovery"]).per_rule["race_pace_ride_ends_at_effort"]
        == 0.0
    )
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule[
            "race_pace_ride_ends_at_effort"
        ]
        == 0.0
    )
    # Mechanism discriminates: the detector flags a ride that ends on recovery
    # (the pre-fix ordering) and clears one that ends on a work interval — the
    # only difference between the two is the end_on_work reorder.
    ensure_registry_loaded()
    activity = get_activity(ActivityKey.BIKE_LONG_IM_RACE_PACE)
    defect_ride = replace(activity, end_on_work=False).build(activity.tss_max)
    fixed_ride = replace(activity, end_on_work=True).build(activity.tss_max)
    assert _ends_on_easy_block(defect_ride) is True
    assert _ends_on_easy_block(fixed_ride) is False


def test_long_swim_peak_reaches_race_distance_on_all_ironman_tiers() -> None:
    # R-SESS-B-006: the MATERIALIZED (card) long swim is measured on the built
    # workout, not on QP hours, so any regression in the metres → hours →
    # workout round trip (pace-law drift, CSS mismatch, catalog clamp) fires
    # here. All three tiers reach the 3.8 km race distance, including BASIC
    # (peak_fri_swim_long floors at 3800 m).
    assert score(SCENARIOS["ironman_moderate"]).per_rule["long_swim_peak"] == 0.0
    assert score(SCENARIOS["ironman_competitive"]).per_rule["long_swim_peak"] == 0.0
    assert score(SCENARIOS["ironman_recovery"]).per_rule["long_swim_peak"] == 0.0


def test_taper_rules_are_registered() -> None:
    names = {r.name for r in RULEBOOK.rules}
    assert {
        "race_week_volume",
        "taper_session_frequency",
        "taper_quality_share",
    } <= names


def test_taper_rules_read_zero_on_the_fixed_ironman_tiers() -> None:
    # The bands hold 89-110% taper frequency, 50-75% taper volume and
    # 53-100% of peak week's quality minutes; all four taper rules read 0.0
    # on every Ironman tier.
    for scenario in ("ironman_recovery", "ironman_moderate", "ironman_competitive"):
        rep = score(SCENARIOS[scenario])
        assert rep.per_rule["taper_volume_drop"] == 0.0, scenario
        assert rep.per_rule["taper_session_frequency"] == 0.0, scenario
        assert rep.per_rule["race_week_volume"] == 0.0, scenario
        assert rep.per_rule["taper_quality_share"] == 0.0, scenario


def test_taper_session_frequency_discriminates() -> None:
    # Bosquet 2007: a taper cuts volume but holds frequency. The Ironman,
    # half-Ironman and marathon menus hold frequency, and the 10K taper,
    # which drops one session in five, sits on the band's floor. No scenario
    # fires, so the discrimination is shown on the band.
    band = _band("taper_session_frequency", GoalType.RUNNING_10K)
    assert band.distance(0.80) == 0.0  # one session in five dropped
    assert band.distance(0.60) > 0.5  # two in five: detraining
    assert score(SCENARIOS["10k_competitive"]).per_rule["taper_session_frequency"] == 0
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["taper_session_frequency"]
        == 0.0
    )
    assert (
        score(SCENARIOS["marathon_competitive"]).per_rule["taper_session_frequency"]
        == 0.0
    )


def test_taper_quality_share_discriminates() -> None:
    # Bosquet's third factor: a taper holds intensity while volume falls.
    # No scenario taper sheds most of its intensity, so the discrimination
    # is shown on the shared band.
    band = _band("taper_quality_share", GoalType.RUNNING_HALF_MARATHON)
    assert band.distance(0.75) == 0.0  # intensity held
    assert band.distance(0.03) > 0.9  # strides only
    # ...and reads 0.0 on half-Ironman too.
    assert (
        score(SCENARIOS["half_ironman_moderate"]).per_rule["taper_quality_share"] == 0.0
    )


def test_taper_quality_share_does_not_saturate() -> None:
    # Each goal's ramp is wide enough that its current plans do not pin at
    # the 1.0 clip — a saturated rule can't tell a bad taper from a worse
    # one within the goal.
    marathon = score(SCENARIOS["marathon_competitive"]).per_rule["taper_quality_share"]
    half_im = score(SCENARIOS["half_ironman_moderate"]).per_rule["taper_quality_share"]
    assert marathon < 1.0
    assert half_im < 1.0


def test_taper_rules_are_inert_without_a_taper_or_a_race() -> None:
    # taper_weeks=1 goals have no TAPER-mode week at all (the single reduced
    # week IS the race week). The rules must return 0.0 rather than divide
    # by an empty sequence.
    for scenario in ("5k_beginner", "sprint_tri_beginner"):
        rep = score(SCENARIOS[scenario])
        assert rep.per_rule["taper_volume_drop"] == 0.0, scenario
        assert rep.per_rule["taper_session_frequency"] == 0.0, scenario
        assert rep.per_rule["taper_quality_share"] == 0.0, scenario


def test_race_week_volume_discriminates_on_the_band_edges() -> None:
    # The rule reads 0.0 across the scenario set because no plan currently has
    # a defective race week — it is a guard, so its discrimination has to be
    # shown directly on the band (the band spans 19-40% of peak).
    band = _band("race_week_volume", GoalType.IRONMAN)
    assert band.distance(0.27) == 0.0  # healthy
    assert band.distance(0.09) == 0.5  # no activation work
    assert band.distance(0.65) == 1.0  # never tapered


def test_goal_coverage_is_reported_not_silently_mis_scored() -> None:
    # A running goal is scored only by the rules whose evidence covers it;
    # everything else is skipped and listed as uncovered. Without goal
    # scoping, a 5k plan would be scored against triathlon sport-mix and
    # Ironman long-session bands — bike share firing 1.0 on a plan with no
    # bike, which is noise, not signal.
    rep = score(SCENARIOS["5k_beginner"])
    assert "sport_mix_bike" in rep.uncovered
    assert "long_ride_peak" in rep.uncovered
    assert "race_pace_long_bike_peak" in rep.uncovered
    assert "peak_weekly_hours" in rep.uncovered
    assert "sport_mix_bike" not in rep.per_rule
    # Generic rules still cover it.
    assert "recovery_cadence" in rep.per_rule
    assert "form_floor" in rep.per_rule
    # Long-course goals are covered by every rule but the marathon's
    # race-pace rule and the trail ones.
    not_long_course = (
        "marathon_race_pace",
        "vert_session_coverage",
        "easier_week_long_run",
        "race_rehearsal_duration",
    )
    assert score(SCENARIOS["half_ironman_moderate"]).uncovered == not_long_course
    assert score(SCENARIOS["ironman_moderate"]).uncovered == not_long_course


def test_marathon_long_run_is_graded_and_its_peak_week_is_not() -> None:
    # The marathon's longest run has a band (2.5-3.5 h): the COMPETITIVE
    # plan's 3.17 h run sits inside, the beginner plan's ~2.1 h run under the
    # floor. Its peak week has none: the rulebook reports a marathon's hours
    # as a fact, as the benchmark always did, and the band (7.5-8.5 h) went with the
    # rulebook merge.
    competitive = score(SCENARIOS["marathon_competitive"]).per_rule
    assert competitive["long_run_peak"] == 0.0
    beginner = score(SCENARIOS["marathon_beginner"])
    assert beginner.per_rule["long_run_peak"] > 0.0
    assert "peak_weekly_hours" in beginner.uncovered
    # Other running goals stay uncovered — one reading is not a band.
    assert "long_run_peak" in score(SCENARIOS["hm_moderate"]).uncovered


def test_phase_share_bands_are_goal_scoped_and_discriminate() -> None:
    # The split has to *do* something: the half band must admit a shape the
    # Ironman band condemns, and both must still condemn a genuinely absurd
    # allocation. Without this the bands could be silently re-merged.
    half_base = _band("phase_share_base", GoalType.HALF_IRONMAN)
    im_base = _band("phase_share_base", GoalType.IRONMAN)
    half_build = _band("phase_share_build", GoalType.HALF_IRONMAN)
    im_build = _band("phase_share_build", GoalType.IRONMAN)
    assert half_base != im_base
    # docs/research/triathlon_long_course.md, Half-Distance vs. Full-Distance
    # — Summary Comparison: the half band permits MORE build and LESS base,
    # and never less build, than the Ironman band.
    assert half_base.low < im_base.low
    assert half_build.high > im_build.high
    assert half_build.low == im_build.low

    # An 18/55/27 allocation: the half band admits it, the Ironman band
    # does not.
    assert half_base.distance(0.182) == 0.0
    assert half_build.distance(0.545) == 0.0
    assert im_base.distance(0.182) > 0.5
    assert im_build.distance(0.545) > 0.4

    # An absurd allocation (all base, no build) still fires on BOTH goals:
    # widening the half band did not make it inert.
    for base, build in ((half_base, half_build), (im_base, im_build)):
        assert base.distance(1.0) == 1.0
        assert build.distance(0.0) == 1.0


# Near-maximal weekends (a measurement, not a rule)
#
# Measuring first found no defect to police here, so no "consecutive maximal
# weekends" rule was authored; the test below pins the measurement:
#
#   1. LITERATURE. docs/research/endurance_methodology.md, Mesocycle
#      Structure and Recovery Cadence, defines 3:1 as "three weeks of
#      progressive loading, one recovery week", and
#      docs/research/triathlon_long_course.md, Recovery Cadence by Athlete
#      Level, gives 3:1 with "occasional 4:1 blocks" for advanced athletes.
#      Three consecutive near-maximal loading weekends IS the prescription.
#   2. COMMON PRACTICE. Plans run 1-6 such weekends, ours 1-3, so any band
#      tight enough to fire on us would grade most plans defective.
#   3. STRUCTURE. The quantity is bounded by
#      ``AmbitionProfile.recovery_cadence`` (BASIC 2:1, MOD/COMP 3:1), a hard
#      post-condition enforced by ``validate_phase_plan`` rule 6.

_WEEKEND_DAYS = frozenset({5, 6})  # day_of_week 0 = Monday, so 5/6 = Sat/Sun


def _max_near_maximal_weekend_run(sessions, n_weeks: int, frac: float = 0.95) -> int:
    # Longest run of consecutive weekends whose combined Sat+Sun prescribed
    # hours reach *frac* of the plan's own biggest weekend. Prescribed
    # sessions only — race-event hours are excluded.
    weekend = [0.0] * n_weeks
    for session in sessions:
        day = session.day_of_week if session.day_of_week is not None else 0
        if day in _WEEKEND_DAYS and session.week < n_weeks:
            weekend[session.week] += session.target_hours
    peak = max(weekend, default=0.0)
    if peak <= 0.0:
        return 0
    threshold = frac * peak
    best = run = 0
    for hours in weekend:
        run = run + 1 if hours >= threshold else 0
        best = max(best, run)
    return best


def test_our_plans_stay_inside_the_envelope_at_odd_horizons() -> None:
    # The odd-horizon assertion path ``--matrix`` does not provide: it sweeps
    # Ironman 20/24/28/32 and half-Ironman 16/20/24/28, never odd geometries.
    # Every MOD/COMP cell here reads AT MOST 4 — the same reading as the swept
    # cells, so the unswept horizons hide nothing — and sits inside the 1-6 envelope.
    # The Ironman's 3:1 blocks read 3; the half-Ironman's
    # two-week taper lets the final block run the whole cadence, four load
    # weeks with no shed, before the taper (R-PHASE-015, R-PP-007,
    # 2026-10-06). Running the pipeline at these horizons at all also
    # asserts they stay QP-feasible.
    from devtools.planner._shared.metrics import run_full_pipeline
    from devtools.quality.planner.score_quality import (
        build_default_scenario,
    )

    for goal in (GoalType.IRONMAN, GoalType.HALF_IRONMAN):
        for ambition in (AmbitionLevel.MODERATE, AmbitionLevel.COMPETITIVE):
            for weeks in (23, 26, 31):
                result = run_full_pipeline(
                    build_default_scenario(weeks, ambition, goal)
                )
                run = _max_near_maximal_weekend_run(
                    result.sessions, len(result.plan.weeks)
                )
                assert run <= 4, (
                    f"{goal.value} {ambition.value} {weeks}wk runs {run} "
                    "consecutive weekends at >= 95% of its peak weekend; the "
                    "cadence caps a block at 4, so a longer run means the "
                    "recovery cadence broke"
                )


# Half-Ironman panel regression guards


def test_run_long_fueling_cue_coverage_discriminates() -> None:
    # Regression guard: the fueling cue must stay goal-agnostic. Gating it to
    # GoalType.IRONMAN would leave a half-Ironman's 90-112 min PEAK long run
    # with no cue while every 75'+ bike on the same plan carried one. Every
    # matrix goal reads 0.0 today, so the rule is a guard, asserted on the
    # mechanism.
    #
    # The mechanism is production's own gate, ``steps._LONG_RUN_FUELING_KEYS``.
    # Narrowing it is exactly the regression this rule exists to catch, and it
    # gives a three-point ramp: full set -> 0.0, {RUN_LONG} only -> the
    # fraction of qualifying runs left uncued, empty -> 1.0.
    from endury.planner.activity import steps

    for name in (
        "half_ironman_moderate",
        "ironman_moderate",
        "marathon_beginner",
        "sprint_tri_beginner",
    ):
        assert score(SCENARIOS[name]).per_rule["run_long_fueling_cue_coverage"] == 0.0

    original = steps._LONG_RUN_FUELING_KEYS
    try:
        # half_ironman_moderate's 7 qualifying runs are 6 run_long + 1
        # run_long_progression; ironman_moderate's 14 are 11 run_long + 2
        # run_long_progression + 1 run_brick_long.
        steps._LONG_RUN_FUELING_KEYS = frozenset({ActivityKey.RUN_LONG})
        assert score(SCENARIOS["half_ironman_moderate"]).per_rule[
            "run_long_fueling_cue_coverage"
        ] == pytest.approx(1 / 7)
        assert score(SCENARIOS["ironman_moderate"]).per_rule[
            "run_long_fueling_cue_coverage"
        ] == pytest.approx(3 / 14)

        steps._LONG_RUN_FUELING_KEYS = frozenset()
        assert (
            score(SCENARIOS["half_ironman_moderate"]).per_rule[
                "run_long_fueling_cue_coverage"
            ]
            == 1.0
        )
        # A trail plan's long runs qualify too: with no key cued, every one
        # of its 75'+ trail long runs reads as uncued.
        assert (
            score(SCENARIOS["trail_20k_basic"]).per_rule[
                "run_long_fueling_cue_coverage"
            ]
            == 1.0
        )
    finally:
        steps._LONG_RUN_FUELING_KEYS = original
    assert (
        score(SCENARIOS["trail_20k_basic"]).per_rule["run_long_fueling_cue_coverage"]
        == 0.0
    )


def test_vo2max_session_work_rest_ratio_discriminates() -> None:
    # Regression guard for the R-SC-015 extension: 30-90 s reps in the VO2
    # band must not read "Speed rep" — that would give RUN_SPEED's short
    # reps behind a fixed 3 min recovery no VO2max wording.
    #
    # Positive control: ironman_moderate genuinely prescribes BIKE_VO2MAX
    # (its VO2max work is on the bike only), so the detector engages there
    # and still reads 0.0 — a clean 0.0, not a vacuous one.
    from devtools.planner._shared.metrics import run_full_pipeline
    from devtools.quality.planner import score_quality

    ironman = run_full_pipeline(SCENARIOS["ironman_moderate"])
    assert ActivityKey.BIKE_VO2MAX in {s.activity_key for s in ironman.sessions}
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["vo2max_session_work_rest_ratio"]
        == 0.0
    )

    # Restore the pre-fix wording (via the phrase the rule looks for) and the
    # rule fires where RUN_SPEED is the interval slot:
    #
    # - olympic_tri_moderate keeps RUN_SPEED, whose 30 s reps behind a fixed
    #   3 min recovery are rest:work 6.0 — far past the 1.5 ceiling, so it
    #   clips at 1.0. half_ironman_moderate rotates RUN_SPEED with VO2max
    #   intervals, and its one early-build week books the VO2max run, so it
    #   carries no speed reps and reads 0.0.
    # - sprint_tri_beginner prescribes strides, not speed work, at BASIC —
    #   a vacuous 0.0 even with the phrase restored.
    original = score_quality._VO2MAX_STEP_PHRASE
    try:
        score_quality._VO2MAX_STEP_PHRASE = "Speed rep"
        for name, expected in (
            ("half_ironman_moderate", 0.0),
            ("sprint_tri_beginner", 0.0),
            ("olympic_tri_moderate", 1.0),
        ):
            assert score(SCENARIOS[name]).per_rule[
                "vo2max_session_work_rest_ratio"
            ] == pytest.approx(expected)
    finally:
        score_quality._VO2MAX_STEP_PHRASE = original


def test_brick_pairs_with_key_ride_discriminates() -> None:
    # Guards the brick chaining (menus + day-first generator wiring): every
    # half-Ironman BUILD/PEAK race-tempo brick names the week's longest ride
    # as its brick_partner, so all three tiers read 0.0. The Ironman tiers
    # read the same 0.0 — the Thursday slot is RUN_BRICK_SHORT, a transition
    # rehearsal, not a race-tempo brick.
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import (
        _brick_pairs_with_key_ride,
    )

    for name in (
        "half_ironman_basic",
        "half_ironman_moderate",
        "half_ironman_competitive",
    ):
        assert score(SCENARIOS[name]).per_rule["brick_pairs_with_key_ride"] == 0.0
    for name in ("ironman_competitive", "ironman_moderate"):
        assert score(SCENARIOS[name]).per_rule["brick_pairs_with_key_ride"] == 0.0

    # Mechanism: drop the chaining and the fixed half plan fires at the clip.
    result = run_full_pipeline(SCENARIOS["half_ironman_moderate"])
    metrics = _compute_metrics(result)
    unchained = replace(
        result, sessions=[replace(s, brick_partner=None) for s in result.sessions]
    )
    assert _brick_pairs_with_key_ride(unchained, metrics) == 1.0


def test_rest_days_holds_the_seven_day_defect() -> None:
    # A defect-holding rule. The half-Ironman tiers' menus lay out a Monday
    # rest day in every LOAD and RECOVERY week, so all three read 0.0.
    # Ironman COMPETITIVE still holds the defect on the Ironman side; Ironman
    # MODERATE sits inside the band at 0.0, and so does olympic since its
    # two-week taper stopped shedding the peak and lost the PEAK recovery
    # exemption (2026-10-06): its load and recovery weeks leave 0.75-2.25
    # days empty.
    # Sprint sits inside from the other side (2.444 empty days a week,
    # against the band's 2.25 ceiling): R-SESS-030 re-fits the beginner's
    # dropped tests with an easy run one at a time, so its recovery weeks
    # never empty past the ceiling and the rule reads 0.0.
    for name in (
        "half_ironman_basic",
        "half_ironman_moderate",
        "half_ironman_competitive",
    ):
        assert score(SCENARIOS[name]).per_rule["rest_days"] == 0.0
    assert score(SCENARIOS["ironman_competitive"]).per_rule["rest_days"] > 0.0
    assert score(SCENARIOS["ironman_moderate"]).per_rule["rest_days"] == 0.0
    assert score(SCENARIOS["sprint_tri_beginner"]).per_rule["rest_days"] == 0.0
    assert score(SCENARIOS["olympic_tri_moderate"]).per_rule["rest_days"] == 0.0


def test_cross_sport_hard_day_stacking_reads_the_half_layouts() -> None:
    # The hard_adjacent_days menu lint groups hard days BY SPORT, so it
    # cannot see two physiologically-hard sessions from different sports
    # landing on the same day. The BASIC half week puts the technique swim
    # in Tuesday's second slot and the CSS swim on the long-run day, so it
    # stacks no hard sessions across sports. The MODERATE half week pairs
    # the CSS swim with Wednesday's quality run on purpose (2026-10-09), so
    # its weeks stack whenever that run is physiologically hard: 4 of 16
    # weeks (0.25), inside the half band's 0.35, so it still reads 0.0. The
    # COMPETITIVE half week pairs the quality swim with Tuesday's quality
    # ride on purpose (the Ironman week, 2026-10-05), so it reads over the
    # half band. Our Ironman plans read inside their own wider row.
    for name in ("half_ironman_basic", "half_ironman_moderate"):
        assert score(SCENARIOS[name]).per_rule["cross_sport_hard_day_stacking"] == 0.0
    assert (
        score(SCENARIOS["half_ironman_competitive"]).per_rule[
            "cross_sport_hard_day_stacking"
        ]
        > 0.0
    )
    assert (
        score(SCENARIOS["ironman_moderate"]).per_rule["cross_sport_hard_day_stacking"]
        == 0.0
    )


# Trail 50K: coverage decisions, research bands and the trail rules
#
# Every band here comes from docs/research/trail_running.md (literature), except
# race_day_freshness_ratio's one measured reading.

_TRAIL_RULES = (
    "vert_session_coverage",
    "easier_week_long_run",
    "race_rehearsal_duration",
)


def _trail_50k_plan(
    weeks: int = 16, tier: AmbitionLevel = AmbitionLevel.MODERATE
) -> tuple[PipelineResult, object]:
    from devtools.planner._shared.metrics import _compute_metrics, run_full_pipeline
    from devtools.quality.planner.score_quality import build_default_scenario

    result = run_full_pipeline(build_default_scenario(weeks, tier, GoalType.TRAIL_50K))
    return result, _compute_metrics(result)


def test_trail_coverage_follows_the_trail_research() -> None:
    covers = {r.name: set(r.goals) for r in RULEBOOK.rules}
    trail = {
        g.value
        for g in GoalType
        if g.value.startswith("trail_") and "maint" not in g.value
    }
    # A BASE vert hike is aerobic climbing by design, not VO2max work, and
    # the test-frequency band rests on Ironman plans alone.
    for name in ("vo2max_in_base", "recovery_week_test_frequency"):
        assert not covers[name] & trail, name
        assert GoalType.RUNNING_MARATHON.value in covers[name], name
    # The trail research gives these a 50K number.
    for name in ("long_run_peak", "peak_weekly_hours"):
        assert GoalType.TRAIL_50K.value in covers[name], name
    for name in _TRAIL_RULES:
        assert covers[name] == {GoalType.TRAIL_50K.value}, name


def test_trail_50k_bands_are_the_research_numbers() -> None:
    # tr §13.5 / §5.1: a trail 50K's longest run is 3-4 h.
    assert _band("long_run_peak", GoalType.TRAIL_50K) == Band(3.0, 4.0, 1.5)
    # tr §13.4: peak weekly hours by tier.
    for tier, low, high in (
        (AmbitionLevel.BASIC, 5.0, 7.0),
        (AmbitionLevel.MODERATE, 7.0, 9.0),
        (AmbitionLevel.COMPETITIVE, 9.0, 12.0),
    ):
        band = _band("peak_weekly_hours", GoalType.TRAIL_50K, tier)
        assert (band.low, band.high) == (low, high), tier
    # tr §5.3 / §13.5: the other long runs sit at 50-75 % of the longest.
    easier = _band("easier_week_long_run", GoalType.TRAIL_50K)
    assert (easier.low, easier.high) == (0.5, 0.75)
    # tr §5.4: four hours is the least a fuelling rehearsal can be.
    rehearsal = _band("race_rehearsal_duration", GoalType.TRAIL_50K)
    assert (rehearsal.low, rehearsal.high) == (4.0, float("inf"))
    vert = _band("vert_session_coverage", GoalType.TRAIL_50K)
    assert (vert.low, vert.high) == (1.0, 1.0)


def test_vert_session_coverage_discriminates() -> None:
    from devtools.quality.planner.score_quality import _vert_session_coverage
    from endury.planner.foundations.types import WeekMode

    result, metrics = _trail_50k_plan()
    vert = {
        ActivityKey.RUN_VERT_HIKE,
        ActivityKey.RUN_HILL_REPEATS,
        ActivityKey.RUN_DOWNHILL_REPEATS,
    }
    race_week = max(r.week for r in result.plan.race_events)
    graded = sorted(
        ws.week
        for ws in result.plan.weeks
        if race_week - 8 <= ws.week < race_week
        and ws.mode in (WeekMode.LOAD, WeekMode.TAPER)
    )
    assert len(graded) >= 3

    # Satisfied: every graded week carries a vertical session.
    sessions = [s for s in result.sessions if s.activity_key not in vert]
    for week in graded:
        easy = next(
            i
            for i, s in enumerate(sessions)
            if s.week == week and s.activity_key == ActivityKey.RUN_EASY
        )
        sessions[easy] = replace(sessions[easy], activity_key=ActivityKey.RUN_VERT_HIKE)
    full = replace(result, sessions=sessions)
    assert _vert_session_coverage(full, metrics) == 0.0

    # Violated: one graded week loses its climbing, then every one does.
    one_short = replace(
        full,
        sessions=[
            s
            for s in full.sessions
            if not (s.week == graded[0] and s.activity_key in vert)
        ],
    )
    assert _vert_session_coverage(one_short, metrics) == pytest.approx(1 / len(graded))
    flat = replace(
        full, sessions=[s for s in full.sessions if s.activity_key not in vert]
    )
    assert _vert_session_coverage(flat, metrics) == 1.0

    # A recovery week inside the window has no climbing and is not graded:
    # the full plan above read 0.0 with it.
    recovery = next(
        ws.week
        for ws in result.plan.weeks
        if ws.mode == WeekMode.RECOVERY and race_week - 8 <= ws.week < race_week
    )
    assert all(s.activity_key not in vert for s in full.sessions if s.week == recovery)


def test_easier_week_long_run_discriminates() -> None:
    from devtools.quality.planner.score_quality import _easier_week_long_run
    from endury.planner.foundations.types import WeekMode

    ensure_registry_loaded()
    result, metrics = _trail_50k_plan()
    modes = {ws.week: ws.mode for ws in result.plan.weeks}

    def long_run(s) -> bool:
        return get_activity(s.activity_key).family == ActivityFamily.LONG

    def eased(ratio: float) -> PipelineResult:
        # Size each easier week's long run at *ratio* of the longest before it.
        sessions = sorted(result.sessions, key=lambda s: s.week)
        longest = 0.0
        out = []
        for s in sessions:
            easier = modes[s.week] in (WeekMode.RECOVERY, WeekMode.TAPER)
            if long_run(s) and easier and longest > 0:
                s = replace(s, target_hours=ratio * longest)
            if long_run(s):
                longest = max(longest, s.target_hours)
            out.append(s)
        return replace(result, sessions=out)

    easier_weeks = sorted(
        {
            s.week
            for s in result.sessions
            if long_run(s) and modes[s.week] in (WeekMode.RECOVERY, WeekMode.TAPER)
        }
    )
    assert len(easier_weeks) >= 2

    assert _easier_week_long_run(eased(0.6), metrics) == 0.0
    # One easier week as long as the longest before it: a quarter-width over
    # the 0.75 ceiling reads 1.0 for that week, averaged over the others.
    one_long = _easier_week_long_run(
        _with_one_long(eased(0.6), easier_weeks[-1], long_run), metrics
    )
    assert one_long == pytest.approx(1 / len(easier_weeks))
    # Every easier week at a third of the longest: short of the 0.50 floor.
    assert _easier_week_long_run(eased(0.3), metrics) > 0.5


def _with_one_long(result: PipelineResult, week: int, long_run) -> PipelineResult:
    """Return *result* with *week*'s long run as long as the longest before it."""
    longest = max(
        s.target_hours for s in result.sessions if long_run(s) and s.week < week
    )
    return replace(
        result,
        sessions=[
            replace(s, target_hours=longest) if s.week == week and long_run(s) else s
            for s in result.sessions
        ],
    )


def test_race_rehearsal_duration_discriminates() -> None:
    from devtools.quality.planner.score_quality import _race_rehearsal_duration
    from endury.planner.foundations.types import PhaseType, WeekMode

    ensure_registry_loaded()
    result, metrics = _trail_50k_plan()
    peak_load = {
        ws.week
        for ws in result.plan.weeks
        if ws.phase == PhaseType.PEAK and ws.mode == WeekMode.LOAD
    }
    # Stand one PEAK long run in as the rehearsal (the menu's race_sim slot),
    # whether or not the rehearsal gate fired on this plan.
    target = next(
        s
        for s in result.sessions
        if s.week in peak_load
        and get_activity(s.activity_key).family == ActivityFamily.LONG
    )
    others = [
        s
        for s in result.sessions
        if s.slot_name != "t50_peak_sun_race_sim" and s is not target
    ]

    def with_rehearsal(hours: float) -> PipelineResult:
        rehearsal = replace(
            target, slot_name="t50_peak_sun_race_sim", target_hours=hours
        )
        return replace(result, sessions=[*others, rehearsal])

    assert _race_rehearsal_duration(with_rehearsal(4.0), metrics) == 0.0
    assert _race_rehearsal_duration(with_rehearsal(4.5), metrics) == 0.0
    assert _race_rehearsal_duration(with_rehearsal(3.5), metrics) == 0.5
    assert _race_rehearsal_duration(with_rehearsal(3.0), metrics) == 1.0
    # No rehearsal at all reads as a rehearsal of three hours or less.
    assert _race_rehearsal_duration(replace(result, sessions=others), metrics) == 1.0
    # A long run under another slot name is not the rehearsal.
    renamed = replace(target, slot_name="t50_peak_sun_long_run", target_hours=4.5)
    assert (
        _race_rehearsal_duration(replace(result, sessions=[*others, renamed]), metrics)
        == 1.0
    )


# The registry check


def test_registry_check_catches_a_rule_and_an_evaluator_left_alone() -> None:
    check_rulebook(RULEBOOK)  # the real rulebook agrees with EVALUATORS

    def with_rules(*rules: object) -> Rulebook:
        return replace(RULEBOOK, rules=tuple(rules))

    # An evaluator whose rule left the rulebook.
    without = [r for r in RULEBOOK.rules if r.name != "load_pricing_agreement"]
    with pytest.raises(AssertionError, match="evaluators without a rule"):
        check_rulebook(with_rules(*without))
    # A rule the benchmark cannot read and nothing here measures.
    retired = replace(RULEBOOK.by_name("load_pricing_agreement"), name="retired_rule")
    with pytest.raises(AssertionError, match="rules without an evaluator"):
        check_rulebook(with_rules(*RULEBOOK.rules, retired))
