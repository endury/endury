"""Cycling activity definitions for the planner.

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
)
from endury.planner.activity.registry import register_activity
from endury.planner.activity.shapes.base import FixedProtocolActivity
from endury.planner.activity.shapes.land import (
    FixedBlockActivity,
    IntervalPiecewiseActivity,
    IntervalSetActivity,
    LinearDurationActivity,
    OverUnderActivity,
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
    """Build explicit compatibility metadata for one cycling activity definition."""
    return ActivityCapabilityProfile(
        sport=SportType.CYCLING,
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
        key=ActivityKey.BIKE_ENDURANCE,
        sport=SportType.CYCLING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC,),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=8.0,
        tss_max=145.0,
        target_preference=TargetPreference.POWER_FIRST,
        intensity_factor=0.65,
        rpe=4,
    )
)

register_activity(
    LinearDurationActivity(
        key=ActivityKey.BIKE_LONG,
        sport=SportType.CYCLING,
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
        tss_min=30.0,
        tss_max=240.0,
        target_preference=TargetPreference.POWER_FIRST,
        intensity_factor=0.65,
        rpe=4,
        easy_hr_band=LONG_EASY_HR_BAND,
    )
)

register_activity(
    LinearDurationActivity(
        key=ActivityKey.BIKE_RECOVERY,
        sport=SportType.CYCLING,
        family=ActivityFamily.RECOVERY,
        capability_profile=_capability(
            family=ActivityFamily.RECOVERY,
            stimulus_tags=(ActivityStimulusTag.RECOVERY, ActivityStimulusTag.LOW_COST),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=8.0,
        tss_max=22.0,
        target_preference=TargetPreference.RPE_FIRST,
        intensity_factor=0.45,
        rpe=2,
        step_type="recovery",
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_SWEET_SPOT,
        sport=SportType.CYCLING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(ActivityStimulusTag.TEMPO, ActivityStimulusTag.THRESHOLD),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        # The ceiling is the top of the rep lattice, 4 x 20 min: a two-hour
        # sweet spot ride builds to two hours instead of stopping at 3 x 20.
        tss_min=40.0,
        tss_max=135.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        # The centre of sweet spot (88-93 % FTP): tempo, under the 91 % floor
        # of the threshold zone, so the card and the benchmark read it as
        # zone 3 and the week's hard sessions are the threshold ones.
        if_work=0.90,
        if_rest=0.60,
        rep_count_min=2,
        rep_count_max=4,
        rep_work_min=15.0,
        rep_work_max=20.0,
        # A fixed five minutes: sweet spot sits below threshold, so it rests
        # about as long as bike_threshold's four, never the 7:45 a recovery
        # scaled with the rep gave a 17:45 rep. Off/on runs 0.25 (20-min
        # reps) to 0.33 (15-min reps): the top of, and just past, the
        # 0.10-0.25 cruise-interval band in
        # docs/research/bench_dossiers/session_structure.md. The cost is one
        # step in the lattice: 2 x 20 (75 min) to 3 x 15 (85 min).
        rep_rest_min=5.0,
        work_rpe=7,
        rest_rpe=2,
        work_description=(
            "Sweet spot — just under threshold, firm and steady; hold the "
            "same power on every rep."
        ),
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_THRESHOLD,
        sport=SportType.CYCLING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD,),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=38.0,
        tss_max=90.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=0.96,
        if_rest=0.45,
        rep_count_min=2,
        rep_count_max=4,
        rep_work_min=8.0,
        rep_work_max=20.0,
        rep_rest_min=4.0,
        work_rpe=7,
        rest_rpe=2,
    )
)

register_activity(
    OverUnderActivity(
        key=ActivityKey.BIKE_OVER_UNDER,
        sport=SportType.CYCLING,
        family=ActivityFamily.THRESHOLD,
        capability_profile=_capability(
            family=ActivityFamily.THRESHOLD,
            stimulus_tags=(ActivityStimulusTag.THRESHOLD, ActivityStimulusTag.MIXED),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        # Fixed 9-min reps of 3 x (2 min at 0.93, 1 min at 1.05), 3 min easy
        # between: 2 reps render a 52-min card, 5 reps an 85-min one.
        tss_min=38.0,
        tss_max=90.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=0.97,
        if_rest=0.45,
        rep_count_min=2,
        rep_count_max=5,
        rep_work_min=9.0,
        rep_work_max=9.0,
        rep_rest_min=3.0,
        work_rpe=7,
        rest_rpe=2,
        under_min=2.0,
        over_min=1.0,
        if_under=0.93,
        if_over=1.05,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_VO2MAX,
        sport=SportType.CYCLING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2,),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=39.0,
        tss_max=77.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=1.10,
        if_rest=0.45,
        rep_count_min=4,
        rep_count_max=6,
        rep_work_min=3.0,
        rep_work_max=5.0,
        rep_rest_min=3.0,
        work_rpe=8,
        rest_rpe=2,
    )
)

# Rønnestad 30/15 short-interval VO2max session (Rønnestad 2020): superior
# peak-aerobic-power gains vs. effort-matched long intervals. Sets of 30 s on /
# 15 s off, 3 min easy between sets; the study rode 3 x 13, about an hour here.
register_activity(
    IntervalSetActivity(
        key=ActivityKey.BIKE_VO2MAX_SHORT,
        sport=SportType.CYCLING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.VO2,),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        # Bounds follow the set lattice: 1 x 10 (23.2) to 3 x 13 (61.9) reps.
        tss_min=23.0,
        tss_max=62.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=1.15,
        if_rest=0.55,
        rep_count_min=10,
        rep_count_max=13,
        rep_work_min=0.5,
        rep_work_max=0.5,
        rep_rest_min=0.25,
        work_rpe=9,
        rest_rpe=3,
        work_description=(
            "Hard rep — well above threshold but not all-out; hold the same "
            "power on every rep."
        ),
        set_count_max=3,
        set_rest_min=3.0,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_LONG_SWEET_SPOT,
        sport=SportType.CYCLING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.THRESHOLD,
                ActivityStimulusTag.TEMPO,
            ),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=False,
        ),
        hardness=HARD_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes this session (tests/planner/test_menu_envelope.py);
        # the floor follows the rep geometry below down to the 107 min
        # half-Ironman BASIC ride (97 TSS at the 90 % sweet spot).
        tss_min=97.0,
        tss_max=298.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=20.0,
        cooldown_min=15.0,
        if_warmup=0.65,
        if_cooldown=0.60,
        if_work=0.90,  # the centre of sweet spot (88-93% FTP): tempo, under Z4's 91 %
        if_rest=0.70,  # Z2 endurance filler between efforts
        # The Z2 filler, not the sweet-spot block, is what makes this ride
        # long, so it is the quantity that scales with target duration:
        # rep_rest spans 21-55 min to keep the card continuous across the
        # full 107-315 min envelope (half-Ironman BASIC through Ironman)
        # instead of quantizing at a fixed filler length.
        rep_count_min=2,
        rep_count_max=4,
        rep_work_min=15.0,
        rep_work_max=20.0,
        rep_rest_min=21.0,
        rep_rest_max=55.0,
        work_rpe=7,
        rest_rpe=4,
        work_description=(
            "Sweet spot — just under threshold, firm and steady; hold the "
            "same power on every block."
        ),
        rest_description=(
            "Steady Z2 endurance between the sweet-spot blocks — keep pedalling "
            "smoothly, do not coast; this is the aerobic bulk of the ride, not "
            "recovery."
        ),
    )
)

# Half-Ironman race-pace long ride: 0.80-0.85 IF blocks for HIM age-group
# race effort.  Strong age-groupers race HIM at IF 0.78-0.82, training
# blocks here sit at the upper edge of that band for race-pace specificity.
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_LONG_HIM_RACE_PACE,
        sport=SportType.CYCLING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.HALF_IRONMAN,),
            phases=(PhaseType.PEAK, PhaseType.TAPER),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        # Window spans the authored menu envelope so lattice projection
        # never resizes this session (tests/planner/test_menu_envelope.py),
        # the half-Ironman taper's 100-minute race-pace ride included.
        tss_min=95.0,
        tss_max=320.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=20.0,
        cooldown_min=15.0,
        if_warmup=0.65,
        if_cooldown=0.60,
        if_work=0.82,  # HIM race pace (age-group 0.78-0.85)
        if_rest=0.68,  # low Z2 endurance filler
        # Race-pace-dense, unlike the Ironman sibling: the half distance's
        # long ride favours race-pace time over Z2 volume (see
        # docs/research/triathlon_long_course.md, Half-Ironman), so the Z2
        # filler is a short float between race-pace blocks. Rep bounds keep
        # the floor at 109 min, under every authored tier.
        rep_count_min=2,
        rep_count_max=5,
        rep_work_min=25.0,
        rep_work_max=45.0,
        rep_rest_min=12.0,
        work_rpe=5,
        rest_rpe=3,
        # End the ride on a race-pace block, not the easy Z2 filler: the
        # durability point is finishing at race effort in a fatigued state
        # (see docs/research/triathlon_long_course.md, Training Durability).
        end_on_work=True,
        work_description=(
            "Half-distance race-pace block — steady aero-bar effort at IF "
            "0.78-0.85. Lock cadence, lock posture, rehearse race nutrition "
            "and hydration exactly as you will on the day."
        ),
        rest_description=(
            "Steady-state Z2 endurance between race-pace blocks — keep pedalling "
            "smoothly, do not coast; this is still race-day work, not recovery."
        ),
    )
)

# Ironman race-pace long ride: 0.70-0.75 IF blocks for IM age-group race
# effort.  Age-groupers race IM at IF 0.65-0.75 (per F4L Coaching,
# TrainingPeaks); training blocks at 0.72 sit mid-band.  Distinct from
# the HIM variant which targets 0.80-0.85.
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_LONG_IM_RACE_PACE,
        sport=SportType.CYCLING,
        family=ActivityFamily.LONG,
        capability_profile=_capability(
            family=ActivityFamily.LONG,
            stimulus_tags=(
                ActivityStimulusTag.LONG_ENDURANCE,
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.RACE_SPECIFIC,
            ),
            goals=(GoalType.IRONMAN,),
            phases=(PhaseType.PEAK, PhaseType.TAPER),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=96.0,
        tss_max=320.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=20.0,
        cooldown_min=15.0,
        if_warmup=0.65,
        if_cooldown=0.60,
        if_work=0.72,  # IM race pace (age-group 0.65-0.75)
        if_rest=0.65,  # easy Z2 float
        # Race-pace-dense: a ride named for race pace holds most of its time
        # there, 2-3 h by peak, not 1 h in 5 with the filler under the plain
        # endurance ride's power.
        rep_count_min=2,
        rep_count_max=4,
        rep_work_min=30.0,
        rep_work_max=60.0,
        rep_rest_min=15.0,
        work_rpe=4,
        rest_rpe=3,
        # End the ride on a race-pace block, not the easy Z2 filler: the
        # durability-rehearsal point is finishing at race effort in a
        # fatigued state (see docs/research/triathlon_long_course.md,
        # Training Durability).
        end_on_work=True,
        work_description=(
            "Full-distance race-pace block — steady aero-bar effort at IF 0.70-0.75. "
            "Lock cadence, lock posture, rehearse race nutrition and hydration "
            "exactly as you will on the day."
        ),
        rest_description=(
            "Steady-state Z2 endurance between race-pace blocks — keep pedalling "
            "smoothly, do not coast; this is still race-day work, not recovery."
        ),
    )
)

register_activity(
    FixedBlockActivity(
        key=ActivityKey.BIKE_TEMPO,
        sport=SportType.CYCLING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(ActivityStimulusTag.TEMPO,),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=MODERATE_HARDNESS,
        tss_min=36.0,
        tss_max=85.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        if_warmup=0.55,
        if_main=0.87,
        cooldown_min=10.0,
        if_cooldown=0.45,
        main_rpe=6,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_CADENCE,
        sport=SportType.CYCLING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC, ActivityStimulusTag.TECHNIQUE),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        # Bounds follow the rep lattice: the fixed ``rep_work_min ==
        # rep_work_max`` yields a narrow ~6-TSS window.
        tss_min=18.0,
        tss_max=25.0,
        target_preference=TargetPreference.RPE_FIRST,
        warmup_min=10.0,
        cooldown_min=5.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=0.62,
        if_rest=0.55,
        rep_count_min=4,
        rep_count_max=6,
        rep_work_min=3.0,
        rep_work_max=3.0,
        rep_rest_min=2.0,
        work_rpe=3,
        rest_rpe=2,
        cadence_low_rpm=100,
        cadence_high_rpm=110,
        # Named work/rest descriptions are needed here: without them the
        # card falls back to the generic easy-ride cues, which never name
        # a target cadence.
        work_description=(
            "High-cadence spin-up — hold 100-110 rpm at easy power. Drop to a "
            "lighter gear, keep the hips still and the upper body quiet; if "
            "you start bouncing in the saddle, back the cadence off until "
            "the pedal stroke is smooth again."
        ),
        rest_description=(
            "Back to your natural cadence, 85-95 rpm, at the same easy power "
            "— let the legs settle and the breathing reset before the next "
            "spin-up."
        ),
    )
)

# Anaerobic-capacity intervals (30-45 s @ IF 1.30): Coggan Z6 work for
# anaerobic-system development and W' replenishment. SPEED-tagged only —
# not a true neuromuscular sprint (see BIKE_SPRINT below for Z7
# peak-power work).
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_ANAEROBIC,
        sport=SportType.CYCLING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(ActivityStimulusTag.SPEED,),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=25.0,
        tss_max=40.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=1.30,
        if_rest=0.45,
        rep_count_min=6,
        rep_count_max=8,
        rep_work_min=0.5,
        rep_work_max=0.75,
        rep_rest_min=2.0,
        rep_rest_max=3.0,
        work_rpe=9,
        rest_rpe=2,
    )
)

# True neuromuscular sprints (10-15 s peak-power efforts at IF 1.70+):
# Z7 work for motor-unit recruitment and peak-power capability.  Cadence
# 100-120 rpm per Tabata / Carmichael / TrainerRoad sprint convention.
register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_SPRINT,
        sport=SportType.CYCLING,
        family=ActivityFamily.INTERVAL,
        capability_profile=_capability(
            family=ActivityFamily.INTERVAL,
            stimulus_tags=(
                ActivityStimulusTag.NEUROMUSCULAR,
                ActivityStimulusTag.SPEED,
            ),
            phases=(PhaseType.BUILD, PhaseType.PEAK),
            recovery_ok=False,
            taper_ok=True,
            earliest_phase_week=3,
        ),
        hardness=HARD_HARDNESS,
        tss_min=20.0,
        tss_max=35.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=1.70,
        if_rest=0.45,
        rep_count_min=6,
        rep_count_max=10,
        rep_work_min=0.17,
        rep_work_max=0.25,
        rep_rest_min=4.0,
        rep_rest_max=5.0,
        work_rpe=10,
        rest_rpe=2,
        cadence_low_rpm=100,
        cadence_high_rpm=120,
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_HILL_CLIMB,
        sport=SportType.CYCLING,
        family=ActivityFamily.TEMPO,
        capability_profile=_capability(
            family=ActivityFamily.TEMPO,
            stimulus_tags=(
                ActivityStimulusTag.TEMPO,
                ActivityStimulusTag.STRENGTH,
                ActivityStimulusTag.HILL,
            ),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=HARD_HARDNESS,
        tss_min=37.0,
        tss_max=80.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=15.0,
        cooldown_min=10.0,
        if_warmup=0.55,
        if_cooldown=0.45,
        if_work=0.90,
        if_rest=0.55,
        rep_count_min=3,
        rep_count_max=6,
        rep_work_min=3.0,
        rep_work_max=5.0,
        rep_rest_min=3.0,
        work_rpe=7,
        rest_rpe=2,
        cadence_low_rpm=50,
        cadence_high_rpm=65,
    )
)

register_activity(
    LinearDurationActivity(
        key=ActivityKey.BIKE_AEROBIC,
        sport=SportType.CYCLING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(ActivityStimulusTag.AEROBIC,),
            recovery_ok=True,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=40.0,
        tss_max=95.0,
        target_preference=TargetPreference.POWER_FIRST,
        intensity_factor=0.75,
        rpe=4,
        step_type="interval",
    )
)

register_activity(
    IntervalPiecewiseActivity(
        key=ActivityKey.BIKE_OPENER,
        sport=SportType.CYCLING,
        family=ActivityFamily.EASY,
        capability_profile=_capability(
            family=ActivityFamily.EASY,
            stimulus_tags=(
                ActivityStimulusTag.AEROBIC,
                ActivityStimulusTag.NEUROMUSCULAR,
            ),
            phases=(PhaseType.PEAK, PhaseType.TAPER),
            recovery_ok=False,
            taper_ok=True,
        ),
        hardness=EASY_HARDNESS,
        tss_min=15.0,
        tss_max=35.0,
        target_preference=TargetPreference.POWER_FIRST,
        warmup_min=10.0,
        cooldown_min=8.0,
        if_warmup=0.55,
        if_cooldown=0.50,
        if_work=1.30,
        if_rest=0.50,
        rep_count_min=4,
        rep_count_max=6,
        rep_work_min=0.25,
        rep_work_max=0.25,
        rep_rest_min=3.0,
        work_rpe=9,
        rest_rpe=2,
        work_description=(
            "Race-day opener — short sharp 15 s burst to remind the legs of "
            "race intensity, smooth and controlled, not maximal"
        ),
        rest_description=(
            "Easy spin between primers — keep the legs turning over, breathe "
            "and reset before the next burst"
        ),
    )
)

# Test protocol

register_activity(
    FixedProtocolActivity(
        key=ActivityKey.BIKE_FTP_TEST,
        sport=SportType.CYCLING,
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
        tss_min=60.0,
        tss_max=80.0,
        target_preference=TargetPreference.RPE_FIRST,
        protocol_duration_min=20.0,
        warmup_min=20.0,
        cooldown_min=15.0,
        if_warmup=0.55,
        if_protocol=1.05,
        if_cooldown=0.45,
        protocol_description=(
            "20 min all-out effort — establish FTP: "
            "warm up 20 min including a 5-min all-out opener and 10 min easy "
            "before the 20-min TT. FTP = 0.95 x 20-min average power."
        ),
    )
)
