"""Running activity definitions for the planner.

Each ``tss_min`` to ``tss_max`` window spans the authored menu envelope, so
lattice projection never resizes a session (tests/planner/test_menu_envelope.py).
"""

from __future__ import annotations

from endury.planner.activity.capabilities import (
    ALL_GOAL_TYPES,
    ALL_PHASE_TYPES,
    ActivityCapabilityProfile,
    ActivityStimulusTag,
)
from endury.planner.activity.config import (
    EASY_HARDNESS,
    HARD_HARDNESS,
    LONG_EASY_HR_BAND,
    MODERATE_HARDNESS,
    RUNNING_THRESHOLD_TSS_PER_HOUR,
)
from endury.planner.activity.registry import register_activity
from endury.planner.activity.shapes.base import FixedProtocolActivity
from endury.planner.activity.shapes.land import (
    FixedBlockActivity,
    IntervalPiecewiseActivity,
    LinearDurationActivity,
    ProgressiveBlockActivity,
)
from endury.planner.foundations.types import (
    ActivityFamily,
    ActivityKey,
    GoalType,
    PhaseType,
    SportType,
    TargetPreference,
)


def _capability(
    *,
    family: ActivityFamily,
    stimulus_tags: tuple[ActivityStimulusTag, ...],
    goals: tuple[GoalType, ...] = ALL_GOAL_TYPES,
    phases: tuple[PhaseType, ...] = ALL_PHASE_TYPES,
    recovery_ok: bool,
    taper_ok: bool,
    earliest_phase_week: int = 0,
) -> ActivityCapabilityProfile:
    """Build explicit compatibility metadata for one running activity definition."""
    return ActivityCapabilityProfile(
        sport=SportType.RUNNING,
        family=family,
        stimulus_tags=stimulus_tags,
        goal_suitability=goals,
        phase_suitability=phases,
        recovery_ok=recovery_ok,
        taper_ok=taper_ok,
        earliest_phase_week=earliest_phase_week,
    )


register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_EASY,
        sport=SportType.RUNNING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.SUPPORT,
                ActivityStimulusTag.BRICK_SUPPORT,
            ),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes a prescribed session. The ceiling is set by the
        # trail 100K weekend slots, whose week-parity rotation prescribes
        # the same 2-3 h Sunday as a plain easy run on non-back-to-back
        # weeks; a lower ceiling would silently resize that card.
        # ``tss_max`` only clamps materialization — ``select.py`` ranks on
        # ``tss_min`` — so raising it cannot move any existing prescription.
        tss_min=10.0,
        tss_max=222.0,
        target_preference=TargetPreference.HR_FIRST,
        intensity_factor=0.75,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rpe=3,
        step_type="interval",
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_STRIDES,
        sport=SportType.RUNNING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.SPEED,
                ActivityStimulusTag.NEUROMUSCULAR,
            ),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=22.0,
        tss_max=55.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.60,
        if_work=1.15,
        if_rest=0.65,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=4,
        rep_count_max=8,
        rep_work_min=0.33,
        rep_work_max=0.5,
        rep_rest_min=1.5,
        work_rpe=9,
        rest_rpe=3,
        work_description=(
            "Stride — 20-30 s of relaxed acceleration at 5K/mile effort. "
            "Fast leg turnover, tall posture, smooth arms; float to the "
            "finish rather than sprinting all-out"
        ),
        rest_description=(
            "Easy jog between strides — fully recover before the next rep, "
            "shake out arms and reset posture"
        ),
    )
)

register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_LONG,
        sport=SportType.RUNNING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.LONG_ENDURANCE,
            ),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=22.0,
        tss_max=185.0,
        target_preference=TargetPreference.HR_FIRST,
        # Authored above the pace-Z1 floor (1/1.40 ≈
        # 0.7143) so the main step carries a pace-zone tag alongside its
        # pace band and HR Z1.
        intensity_factor=0.72,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
        rpe=4,
        step_type="interval",
    )
)

register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_RECOVERY,
        sport=SportType.RUNNING,
        family=ActivityFamily.RECOVERY,
        capability_profile=_capability(
            family=ActivityFamily.RECOVERY,
            stimulus_tags=(
                ActivityStimulusTag.RECOVERY,
                ActivityStimulusTag.LOW_COST,
                ActivityStimulusTag.BRICK_SUPPORT,
            ),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # The TSS window follows the IF, so a swap still spans 25-39 min.
        tss_min=15.5,
        tss_max=26.5,
        target_preference=TargetPreference.RPE_FIRST,
        # One band below run_easy, like a warm-up: priced at 0.55, a jog in
        # the top of Zone 1 read as 160 % of plan.
        intensity_factor=0.65,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rpe=2,
        step_type="recovery",
    )
)

register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_TEMPO,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(ActivityStimulusTag.TEMPO,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=15.0,
        tss_max=112.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_main=0.94,
        if_cooldown=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        main_rpe=6,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_THRESHOLD,
        sport=SportType.RUNNING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=40.0,
        tss_max=84.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=0.96,
        if_rest=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=4,
        rep_count_max=6,
        rep_work_min=5.0,
        rep_work_max=6.0,
        rep_rest_min=2.5,
        work_rpe=7,
        rest_rpe=2,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_VO2MAX,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2, ActivityStimulusTag.ANAEROBIC),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=35.0,
        tss_max=64.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=1.08,
        if_rest=0.50,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=4,
        rep_count_max=7,
        rep_work_min=3.0,
        rep_work_max=4.0,
        rep_rest_min=3.0,
        work_rpe=9,
        rest_rpe=2,
    )
)

# Billat 30/30 microinterval variant: high-density short reps at vVO2max
# for VO2max sharpening weeks. Complements RUN_VO2MAX (3-4 min long reps).
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_VO2MAX_SHORT,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2, ActivityStimulusTag.ANAEROBIC),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=25.0,
        tss_max=50.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=1.10,
        if_rest=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=20,
        rep_count_max=30,
        rep_work_min=0.5,
        rep_work_max=0.5,
        rep_rest_min=0.5,
        work_rpe=9,
        rest_rpe=3,
        work_description=(
            "Hard rep — quick and sharp but not all-out; hold the same pace on "
            "every rep."
        ),
    )
)

register_activity(
    ProgressiveBlockActivity(
        key=ActivityKey.RUN_PROGRESSION,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(ActivityStimulusTag.TEMPO, ActivityStimulusTag.PROGRESSION),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=16.0,
        tss_max=103.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        if_warmup=0.70,
        if_main_start=0.75,
        if_main_end=0.95,
        cooldown_min=10.0,
        if_cooldown=0.55,
        rpe_start=3,
        rpe_end=7,
        progression_steps=4,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_FARTLEK,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2, ActivityStimulusTag.FARTLEK),
            recovery_ok=True,
            taper_ok=True,
            earliest_phase_week=2,
        ),
        hardness=HARD_HARDNESS,
        tss_min=28.0,
        tss_max=67.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        cooldown_min=10.0,
        if_cooldown=0.55,
        if_work=1.05,
        if_rest=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=6,
        rep_count_max=10,
        rep_work_min=1.0,
        rep_work_max=3.0,
        rep_rest_min=2.0,
        work_rpe=9,
        rest_rpe=3,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_HILL_REPEATS,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.VO2,
                ActivityStimulusTag.STRENGTH,
                ActivityStimulusTag.HILL,
            ),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=2,
        ),
        hardness=HARD_HARDNESS,
        tss_min=35.0,
        # 65 (was 60): the trail 20K's peak climb is authored at 56-60 min,
        # which derives 64 TSS at the envelope's edge.
        tss_max=65.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        cooldown_min=10.0,
        if_cooldown=0.55,
        if_work=1.00,
        if_rest=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=6,
        rep_count_max=8,
        rep_work_min=2.0,
        rep_work_max=3.0,
        rep_rest_min=3.0,
        work_rpe=8,
        rest_rpe=2,
        # The lead clause is the watch's step label: "Uphill rep", not the
        # default "Threshold effort", on a step the hill sets the length of.
        work_description=(
            "Uphill rep — hard but controlled: tall through the hips, short "
            "quick steps; the hill sets the length, the effort is the target"
        ),
        rest_description=(
            "Jog or walk back down — easy; breathe and reset before the next climb"
        ),
    )
)

register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_HM_PACE,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.RUNNING_HALF_MARATHON,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=18.0,
        tss_max=100.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        if_main=0.91,
        cooldown_min=10.0,
        if_cooldown=0.55,
        main_rpe=6,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
    )
)

register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_FULL_MP,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.RUNNING_MARATHON,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=26.0,
        tss_max=115.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        if_main=0.85,
        cooldown_min=10.0,
        if_cooldown=0.55,
        main_rpe=5,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
    )
)

# Marathon-specific progressive long run: easy base (~0.72) → marathon pace
# (~0.85) in the final third.  Distinct from RUN_LONG_PROGRESSION (which
# tops out at IF 0.88 for HIM/IM) — this variant targets true full-
# marathon goals where the late-MP segment is the specific stimulus.
register_activity(
    ProgressiveBlockActivity(
        key=ActivityKey.RUN_LONG_WITH_MP,
        sport=SportType.RUNNING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.PROGRESSION,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.RUNNING_MARATHON,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=80.0,
        tss_max=170.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=15.0,
        if_warmup=0.70,
        if_main_start=0.72,
        if_main_end=0.85,
        cooldown_min=15.0,
        if_cooldown=0.55,
        rpe_start=3,
        rpe_end=6,
        progression_steps=3,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_5K_RACE_PACE,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.VO2,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.RUNNING_5K,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=30.0,
        tss_max=55.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=1.08,
        if_rest=0.50,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=5,
        rep_count_max=8,
        rep_work_min=2.0,
        rep_work_max=4.0,
        rep_rest_min=2.5,
        work_rpe=9,
        rest_rpe=2,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_10K_RACE_PACE,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.VO2,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.RUNNING_10K,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=30.0,
        tss_max=55.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=1.03,
        if_rest=0.50,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=4,
        rep_count_max=6,
        rep_work_min=4.0,
        rep_work_max=6.0,
        rep_rest_min=2.5,
        work_rpe=8,
        rest_rpe=2,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_SPEED,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.VO2,
                ActivityStimulusTag.SPEED,
                ActivityStimulusTag.NEUROMUSCULAR,
            ),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=30.0,
        tss_max=55.0,
        target_preference=TargetPreference.PACE_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        cooldown_min=10.0,
        if_cooldown=0.55,
        if_work=1.12,
        if_rest=0.50,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=8,
        rep_count_max=10,
        rep_work_min=0.5,
        rep_work_max=1.0,
        rep_rest_min=3.0,
        work_rpe=9,
        rest_rpe=2,
    )
)

register_activity(
    ProgressiveBlockActivity(
        key=ActivityKey.RUN_LONG_PROGRESSION,
        sport=SportType.RUNNING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.PROGRESSION,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(
                GoalType.RUNNING_HALF_MARATHON,
                GoalType.RUNNING_MARATHON,
                GoalType.HALF_IRONMAN,
                GoalType.IRONMAN,
            ),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=60.0,
        tss_max=210.0,
        target_preference=TargetPreference.HR_FIRST,
        work_target_preference=TargetPreference.PACE_FIRST,
        warmup_min=15.0,
        if_warmup=0.72,
        if_main_start=0.72,
        if_main_end=0.88,
        cooldown_min=15.0,
        if_cooldown=0.55,
        rpe_start=3,
        rpe_end=6,
        progression_steps=3,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
    )
)

register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_AEROBIC_TEMPO,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(ActivityStimulusTag.TEMPO, ActivityStimulusTag.AEROBIC),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes a prescribed session; the ceiling was sized for the
        # removed trail 100M's 98-min BASE Wednesday. ``tss_max`` only clamps
        # materialization, and no menu authors this key past 75 min, so the
        # spare room moves nothing.
        tss_min=14.0,
        tss_max=135.0,
        target_preference=TargetPreference.HR_FIRST,
        warmup_min=10.0,
        if_warmup=0.65,
        if_main=0.90,
        cooldown_min=10.0,
        if_cooldown=0.55,
        main_rpe=5,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
    )
)

# Brick runs (run immediately following a bike session): distinct from
# RUN_EASY because the physiological demand is elevated by the preceding
# cycling effort, and the training purpose is transition / race
# simulation, not baseline aerobic volume.

register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_BRICK_SHORT,
        sport=SportType.RUNNING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.BRICK_SUPPORT,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=9.0,
        tss_max=40.0,
        target_preference=TargetPreference.HR_FIRST,
        intensity_factor=0.74,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rpe=4,
        step_type="interval",
        step_description=(
            "Brick run off the bike — start steady, settle into running "
            "rhythm in the first 5 min, then hold easy aerobic effort. "
            "Expect heavy legs at first; cadence and posture cue the "
            "transition."
        ),
    )
)

# Long brick: race-sim endurance off the bike, lower IF to reflect
# IM/HIM race-effort running on fatigued legs (full-distance age-groupers
# typically run 0.68-0.74 for IM).
register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_BRICK_LONG,
        sport=SportType.RUNNING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.BRICK_SUPPORT,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.HALF_IRONMAN, GoalType.IRONMAN),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=28.0,
        tss_max=110.0,
        target_preference=TargetPreference.HR_FIRST,
        # Above the pace-Z1 floor — see RUN_LONG.
        intensity_factor=0.72,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
        rpe=4,
        step_type="interval",
        step_description=(
            "Long brick off the bike — race-simulation endurance running on "
            "fatigued legs at full- or half-distance race effort.  Settle into rhythm "
            "in the "
            "first 10 min, then hold steady aerobic; practise nutrition and "
            "form when fatigue rises."
        ),
    )
)

# Half-Ironman race-pace brick: sub-threshold tempo off the bike, aligned
# with HIM age-group race effort (~IF 0.88-0.92).  Lands pace/HR Z3 Tempo.
register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_BRICK_HIM_TEMPO,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.BRICK_SUPPORT,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.HALF_IRONMAN,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=20.0,
        tss_max=82.0,
        target_preference=TargetPreference.HR_FIRST,
        work_target_preference=TargetPreference.PACE_FIRST,
        warmup_min=0.0,
        if_warmup=0.75,
        if_main=0.91,
        cooldown_min=5.0,
        if_cooldown=0.65,
        main_rpe=6,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        main_description=(
            "Brick run at HIM race pace — sub-threshold tempo off the bike. "
            "Hold target effort even as legs fatigue; this is your "
            "race-day rehearsal for transitioning from cycling to running."
        ),
    )
)

# Ironman race-pace brick: high-Z2 aerobic off the bike, aligned with IM
# age-group race effort (~IF 0.80-0.84).  Lands pace/HR Z1/Z2 boundary.
register_activity(
    FixedBlockActivity(
        key=ActivityKey.RUN_BRICK_IM_TEMPO,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.BRICK_SUPPORT,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.IRONMAN,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=12.0,
        tss_max=70.0,
        target_preference=TargetPreference.HR_FIRST,
        work_target_preference=TargetPreference.PACE_FIRST,
        warmup_min=0.0,
        if_warmup=0.72,
        if_main=0.82,
        cooldown_min=5.0,
        if_cooldown=0.60,
        main_rpe=5,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        main_description=(
            "Brick run at full-distance race pace — high-Z2 aerobic off the bike. "
            "Stay disciplined on effort and nutrition; this is your "
            "race-day rehearsal for the long race run on fatigued legs."
        ),
    )
)

# Trail running: distinct from the road entries because grade and terrain
# break pace as a prescription metric (HR or RPE lead instead), because
# vertical gain raises the cost of an otherwise easy effort, and because
# the eccentric load of descending is a training stimulus in its own right.

# Easy run on trails: what an athlete means by "an easy hour on the trails".
# Its own entry because the hills raise the cost of the same easy effort and
# turn pace into noise, so heart rate leads and the intensity sits the same
# step above RUN_EASY's 0.75 that RUN_TRAIL_LONG sits above RUN_LONG's 0.72.
# No menu books it; the coach's "trail" intent does, by length.
register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_TRAIL_EASY,
        sport=SportType.RUNNING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC, ActivityStimulusTag.HILL),
            # Climbing is no recovery, however easy the effort.
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # Floor above RUN_EASY's 10 so the plain "easy" intent still resolves
        # to the road run (select.py breaks ties on tss_min). Ceiling ~75 min
        # at IF 0.77: a longer run on trails is the Long Trail Run.
        tss_min=12.0,
        tss_max=75.0,
        target_preference=TargetPreference.HR_FIRST,
        intensity_factor=0.77,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rpe=3,
        step_type="interval",
        step_description=(
            "Easy trail run — run the flats and descents relaxed and walk "
            "the steep climbs.  Steer by HR and feel, not pace: grade and "
            "footing make pace meaningless."
        ),
    )
)

# Long trail run with vertical gain. EASY hardness is deliberate: trail
# menus author back-to-back long-run weekends, and the day-layout lint
# `hard_adjacent_days` keys on `is_hard_physiologically` — a long aerobic
# run over rough ground is costly but not a hard session.
register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_TRAIL_LONG,
        sport=SportType.RUNNING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.HILL,
            ),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # Floor stays above RUN_LONG's 22 so the plain "long" intent still
        # resolves to RUN_LONG (select.py breaks ties on tss_min); ceiling
        # is ~8 h30 at IF 0.74 — ultra-distance prep puts genuinely long
        # days on the calendar. The ceiling was sized for the removed trail
        # 100M's 8 h30 Epic Mountain Day (510 min). ``tss_max`` only clamps
        # materialization — ``select.py`` ranks on ``tss_min`` — so the spare
        # room cannot move any prescription.
        tss_min=30.0,
        tss_max=515.0,
        target_preference=TargetPreference.HR_FIRST,
        # Above RUN_LONG's 0.72: vertical gain raises the cost of the same
        # easy effort. Also above the pace-Z1 floor (see RUN_LONG).
        intensity_factor=0.74,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
        rpe=4,
        step_type="interval",
        step_description=(
            "Long trail run — power-hike the steep climbs, run the flats and "
            "descents.  Steer by HR and feel, not pace: grade and footing "
            "make pace meaningless.  Practise fueling every 45 min on "
            "anything over 2 h."
        ),
    )
)

# The second, shorter long run of a back-to-back weekend: the stimulus is
# starting already tired, so effort stays easy and the clock, not the pace,
# is the target.
register_activity(
    LinearDurationActivity(
        key=ActivityKey.RUN_TRAIL_B2B,
        sport=SportType.RUNNING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.LONG_ENDURANCE,
            ),
            recovery_ok=False,
            # A second long run on pre-fatigued legs is exactly what a taper
            # drops, however short it is.
            taper_ok=False,
        ),
        hardness=EASY_HARDNESS,
        # Floor above RUN_LONG's 22 for the same intent-picker reason as
        # RUN_TRAIL_LONG; ceiling is ~4 h at IF 0.72 — the shorter of the
        # pair, but "shorter" still means hours in ultra prep.
        tss_min=30.0,
        tss_max=230.0,
        target_preference=TargetPreference.RPE_FIRST,
        # Easier than RUN_TRAIL_LONG despite the terrain — the fatigue is
        # supplied by yesterday, not by today's intensity.
        intensity_factor=0.72,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        easy_hr_band=LONG_EASY_HR_BAND,
        rpe=4,
        step_type="interval",
        step_description=(
            "Back-to-back long run on pre-fatigued legs — easy effort the "
            "whole way, time on feet over pace.  Expect heavy legs from "
            "yesterday's long run; hike the climbs rather than forcing them."
        ),
    )
)

# Sustained uphill power-hiking / climbing. RPE leads for the same reason
# as RUN_HILL_REPEATS: on a steep grade (or a treadmill incline) pace
# carries no information.
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_VERT_HIKE,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.STRENGTH,
                ActivityStimulusTag.HILL,
            ),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        # Rep lattice spans ~44 min (2 x 8 min) to ~3 h30 (8 x 20 min).
        tss_min=35.0,
        tss_max=255.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_min=10.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=0.86,
        if_rest=0.55,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=2,
        rep_count_max=8,
        rep_work_min=8.0,
        rep_work_max=20.0,
        rep_rest_min=4.0,
        work_rpe=6,
        rest_rpe=2,
        work_description=(
            "Sustained climb — hike the steep sections with purpose: strong "
            "posture, tall through the hips, hands on quads or poles if you "
            "race with them.  Steady effort you could hold to the top, not "
            "a surge."
        ),
        rest_description=(
            "Walk or easy jog back down — controlled descending, reset "
            "posture and breathing before the next climb"
        ),
    )
)

# Downhill repeats for eccentric conditioning. HARD hardness is
# deliberate and does not follow from the IF: cardiovascular strain here is
# modest, but eccentric muscle damage needs exactly the hard/easy spacing
# the day-layout lints enforce, and IF alone under-represents this
# session's recovery cost.
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.RUN_DOWNHILL_REPEATS,
        sport=SportType.RUNNING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.STRENGTH,
                ActivityStimulusTag.HILL,
                ActivityStimulusTag.NEUROMUSCULAR,
            ),
            recovery_ok=False,
            taper_ok=False,
            # Eccentric loading needs an aerobic base under it before the
            # legs can absorb it — later than RUN_HILL_REPEATS' week 2.
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=30.0,
        tss_max=90.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_cooldown=0.55,
        if_work=0.88,
        if_rest=0.60,
        threshold_tss_per_hour=RUNNING_THRESHOLD_TSS_PER_HOUR,
        rep_count_min=4,
        rep_count_max=8,
        rep_work_min=2.0,
        rep_work_max=4.0,
        rep_rest_min=4.0,
        work_rpe=7,
        rest_rpe=2,
        work_description=(
            "Downhill rep — controlled but committed descending: quick light "
            "steps, land under the hips, let the grade carry you instead of "
            "braking.  Stop the reps the moment form degrades."
        ),
        rest_description=(
            "Walk or easy jog back up — full recovery; the climb is transport "
            "between reps, not training"
        ),
    )
)

# Test protocol

register_activity(
    FixedProtocolActivity(
        key=ActivityKey.RUN_THRESHOLD_TEST,
        sport=SportType.RUNNING,
        family=ActivityFamily.TEST,
        capability_profile=_capability(
            family=ActivityFamily.TEST,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD,),
            # MAINTAIN included: the maintenance menus make the test the one
            # hard session a recovery week carries — rested legs give the
            # honest number. recovery_ok stays False; only the ad-hoc picker
            # reads it, and a test is no substitute for a recovery session.
            phases=(
                PhaseType.BASE,
                PhaseType.BUILD,
                PhaseType.PEAK,
                PhaseType.MAINTAIN,
            ),
            recovery_ok=False,
            taper_ok=False,
        ),
        hardness=HARD_HARDNESS,
        tss_min=55.0,
        tss_max=75.0,
        target_preference=TargetPreference.RPE_FIRST,
        protocol_duration_min=30.0,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.65,
        if_protocol=0.99,
        if_cooldown=0.55,
        protocol_description="30 min all-out effort — find sustainable threshold pace",
    )
)
