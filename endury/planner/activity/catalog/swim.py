"""Swimming activity definitions for the planner.

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
from endury.planner.activity.config import EASY_HARDNESS, HARD_HARDNESS
from endury.planner.activity.registry import register_activity
from endury.planner.activity.shapes.aquatic import (
    SwimCssIntervalActivity,
    SwimCssTestActivity,
    SwimDrillSwimActivity,
    SwimLinearDistanceActivity,
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
    """Build explicit compatibility metadata for one swimming activity definition."""
    return ActivityCapabilityProfile(
        sport=SportType.SWIMMING,
        family=family,
        stimulus_tags=stimulus_tags,
        goal_suitability=goals,
        phase_suitability=phases,
        recovery_ok=recovery_ok,
        taper_ok=taper_ok,
        earliest_phase_week=earliest_phase_week,
    )


# Aerobic / endurance

register_activity(
    SwimLinearDistanceActivity(
        key=ActivityKey.SWIM_ENDURANCE,
        sport=SportType.SWIMMING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC,),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=12.0,
        tss_max=80.0,
        target_preference=TargetPreference.PACE_FIRST,
        intensity_factor=0.88,
        rpe=4,
        warmup_meters=400,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle",
        # A continuous-distance swim hands its main set over as reps, not
        # one unbroken block: 400 m is the standard endurance-set rep
        # length (`docs/specs/planner/behavioral.md`, R-DU-008).
        max_rep_meters=400,
    )
)

register_activity(
    SwimLinearDistanceActivity(
        key=ActivityKey.SWIM_LONG,
        sport=SportType.SWIMMING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.LONG_ENDURANCE,
            ),
            goals=(GoalType.HALF_IRONMAN, GoalType.IRONMAN),
            recovery_ok=False,
            taper_ok=False,
        ),
        hardness=EASY_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes this session (tests/planner/test_menu_envelope.py),
        # and the ramp up from a 30-minute longest swim (R-SESS-012).
        tss_min=25.0,
        tss_max=110.0,
        target_preference=TargetPreference.PACE_FIRST,
        intensity_factor=0.82,
        rpe=3,
        warmup_meters=400,
        cooldown_meters=300,
        if_warmup=0.75,
        if_cooldown=0.72,
        step_description="freestyle",
        # Half-IM / IM only, and the one place 800-1,000 m reps belong —
        # the long swim is the rare session that earns them.
        max_rep_meters=1000,
    )
)

register_activity(
    SwimLinearDistanceActivity(
        key=ActivityKey.SWIM_RECOVERY,
        sport=SportType.SWIMMING,
        family=ActivityFamily.RECOVERY,
        capability_profile=_capability(
            family=ActivityFamily.RECOVERY,
            stimulus_tags=(ActivityStimulusTag.RECOVERY, ActivityStimulusTag.LOW_COST),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=10.0,
        tss_max=60.0,
        target_preference=TargetPreference.RPE_FIRST,
        intensity_factor=0.75,
        rpe=2,
        warmup_meters=300,
        cooldown_meters=200,
        step_description="freestyle or backstroke (athlete's choice)",
        # A recovery swim stays a loose 400 m set, not a long grind.
        max_rep_meters=400,
    )
)

# Threshold / VO2max / sprint

register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_CSS,
        sport=SportType.SWIMMING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD, ActivityStimulusTag.CSS),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=18.0,
        tss_max=81.0,
        target_preference=TargetPreference.PACE_FIRST,
        if_work=1.00,
        rep_count_min=5,
        rep_count_max=10,
        rep_meters_min=50,
        rep_meters_max=200,
        rep_rest_seconds=20,
        rep_rest_seconds_max=25,
        work_rpe=7,
        rest_rpe=2,
        warmup_meters=500,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, CSS pace",
    )
)

# intent: SWIM_CSS for short sessions. Behind SWIM_CSS's 500 m warm-up
# and 200 m cool-down, a 20-26 min session holds only 5x50 m at CSS
# (5-6.5 min), a threshold card without the threshold. With a 200 m
# warm-up and 100 m cool-down the same session holds 5x100-5x150 m at CSS
# (11-18 min) at the same length. The set itself is SWIM_CSS's.
register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_CSS_SHORT,
        sport=SportType.SWIMMING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD, ActivityStimulusTag.CSS),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=20.0,
        tss_max=60.0,
        target_preference=TargetPreference.PACE_FIRST,
        if_work=1.00,
        rep_count_min=5,
        rep_count_max=10,
        # 5x100 m is the smallest set that is still a CSS set (11 min at CSS).
        rep_meters_min=100,
        rep_meters_max=200,
        rep_rest_seconds=20,
        rep_rest_seconds_max=25,
        work_rpe=7,
        rest_rpe=2,
        warmup_meters=200,
        cooldown_meters=100,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, CSS pace",
    )
)

register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_VO2MAX,
        sport=SportType.SWIMMING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2,),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=16.0,
        tss_max=70.0,
        target_preference=TargetPreference.PACE_FIRST,
        if_work=1.10,
        rep_count_min=6,
        rep_count_max=14,
        rep_meters_min=50,
        rep_meters_max=100,
        rep_rest_seconds=45,
        work_rpe=8,
        rest_rpe=2,
        warmup_meters=500,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, above CSS pace",
    )
)

register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_VO2MAX_SHORT,
        sport=SportType.SWIMMING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        # Bounds follow the rep lattice at the default CSS: fixed 50 m reps
        # (rep_meters_min == rep_meters_max) limit the achievable TSS to
        # roughly 40-69 across 15-30 reps.
        tss_min=40.0,
        tss_max=70.0,
        target_preference=TargetPreference.PACE_FIRST,
        if_work=1.05,
        rep_count_min=15,
        rep_count_max=30,
        rep_meters_min=50,
        rep_meters_max=50,
        rep_rest_seconds=10,
        work_rpe=8,
        rest_rpe=2,
        warmup_meters=500,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, sharp microset",
    )
)

register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_THRESHOLD,
        sport=SportType.SWIMMING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=28.0,
        tss_max=87.0,
        target_preference=TargetPreference.PACE_FIRST,
        if_work=1.00,
        rep_count_min=4,
        rep_count_max=6,
        rep_meters_min=200,
        rep_meters_max=400,
        rep_rest_seconds=30,
        rep_rest_seconds_max=45,
        work_rpe=7,
        rest_rpe=2,
        warmup_meters=500,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, threshold pace",
    )
)

register_activity(
    SwimCssIntervalActivity(
        key=ActivityKey.SWIM_SPRINT,
        sport=SportType.SWIMMING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.SPEED,
                ActivityStimulusTag.NEUROMUSCULAR,
            ),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        # Bounds follow the rep lattice at the default CSS: fixed 50 m reps
        # (rep_meters_min == rep_meters_max) limit the achievable TSS to
        # roughly 31-36 across 8-10 reps.
        tss_min=31.0,
        tss_max=36.0,
        target_preference=TargetPreference.RPE_FIRST,
        if_work=1.20,
        rep_count_min=8,
        rep_count_max=10,
        rep_meters_min=50,
        rep_meters_max=50,
        rep_rest_seconds=60,
        rep_rest_seconds_max=90,
        work_rpe=10,
        rest_rpe=2,
        warmup_meters=500,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="freestyle, all-out sprint",
    )
)

# Technique / strength-endurance

register_activity(
    SwimDrillSwimActivity(
        key=ActivityKey.SWIM_DRILL,
        sport=SportType.SWIMMING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.TECHNIQUE,),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=12.0,
        # intent: 10 reps and the 1200 m aerobic block at the default CSS
        # (48.1); the set alone tops out at 23.8.
        tss_max=48.5,
        target_preference=TargetPreference.RPE_FIRST,
        if_drill=0.72,
        if_swim=0.78,
        # 50 m laps swim cleanly in a 25 m or a 50 m pool; half the reps of
        # the old 25 m laps keep the same set distance.
        drill_meters=50,
        swim_meters=50,
        rep_count_min=4,
        rep_count_max=10,
        drill_rpe=3,
        swim_rpe=3,
        warmup_meters=200,
        cooldown_meters=100,
        if_warmup=0.72,
        if_cooldown=0.68,
        drill_description=(
            "Freestyle technique drill — rotate one per set: catch-up "
            "(lead-hand extended until trailing hand touches), fingertip "
            "drag (relaxed recovery, brush water with fingers), 6-3-6 "
            "switch (six kicks on each side, three strokes between), or "
            "single-arm freestyle (non-stroking arm at side). Focus on "
            "the cue, not pace."
        ),
        swim_description="Freestyle, easy whole-stroke — apply the drill focus",
        # intent: Ten 50 m drills are as much focused drilling as one set
        # holds; a longer booking swims the rest as easy whole-stroke, the
        # way a technique session usually ends its main set. 1200 m lets the
        # card reach the longest drill booking in any menu (2400 m).
        aerobic_max_meters=1200,
        aerobic_description=(
            "Easy aerobic swim — long, relaxed freestyle, carrying the "
            "drill's focus into your normal stroke"
        ),
    )
)

register_activity(
    SwimLinearDistanceActivity(
        key=ActivityKey.SWIM_PULL,
        sport=SportType.SWIMMING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC, ActivityStimulusTag.STRENGTH),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes this session (tests/planner/test_menu_envelope.py);
        # 60 holds the 2400 m triathlon-maintenance pull booked at its own
        # pace, plus the 5-minute rounding.
        tss_min=20.0,
        tss_max=60.0,
        target_preference=TargetPreference.RPE_FIRST,
        intensity_factor=0.86,
        rpe=4,
        warmup_meters=300,
        cooldown_meters=200,
        if_warmup=0.78,
        if_cooldown=0.75,
        step_description="Freestyle pull with paddles and pull buoy",
        # Pull sets read as N x 400 m or shorter, same rationale as the
        # endurance set — paddles make a longer unbroken rep an unwanted
        # shoulder load.
        max_rep_meters=400,
    )
)

register_activity(
    SwimLinearDistanceActivity(
        key=ActivityKey.SWIM_KICK,
        sport=SportType.SWIMMING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.TECHNIQUE, ActivityStimulusTag.STRENGTH),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=12.0,
        tss_max=25.0,
        target_preference=TargetPreference.RPE_FIRST,
        intensity_factor=0.75,
        rpe=4,
        warmup_meters=200,
        cooldown_meters=100,
        step_description=(
            "Flutter kick on back with board (athlete may swap "
            "breaststroke kick for variety)"
        ),
        # 200 m is the standard board-kick rep — long enough to keep the
        # rep count reasonable, short enough that nobody kicks 1,400 m
        # unbroken.
        max_rep_meters=200,
    )
)

# Test protocol

register_activity(
    SwimCssTestActivity(
        key=ActivityKey.SWIM_CSS_TEST,
        sport=SportType.SWIMMING,
        family=ActivityFamily.TEST,
        capability_profile=_capability(
            family=ActivityFamily.TEST,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD, ActivityStimulusTag.CSS),
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
        tss_min=35.0,
        tss_max=45.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_meters=600,
        strides_count=4,
        strides_meters=50,
        strides_rest_seconds=20.0,
        tt1_meters=400,
        tt_rest_seconds=180.0,
        tt2_meters=200,
        cooldown_meters=300,
        if_warmup=0.72,
        if_strides=0.95,
        if_tt=1.00,
        if_cooldown=0.68,
        protocol_description=(
            "400 m TT + 200 m TT — establish critical swim speed; this is "
            "the first all-out leg"
        ),
        strides_description="50 m build to race pace",
    )
)
