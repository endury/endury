"""Hours→workout materialization bridge for planner activities.

The pipeline plans sessions in hours; athletes receive structured workouts.
This module holds the deterministic bridge between the two: the
duration-first TSS derivation (:func:`assignment_target_tss`), the
step-distance summation (:func:`workout_total_meters`), the athlete-facing
swim distance (:func:`materialized_swim_meters`), the curated workout
titles (:func:`activity_display_name`), and the athlete-facing session
briefs (:func:`activity_brief`).

Boundary note: this module depends only on planner and the host-free
``endury.training`` kernel — it must stay importable from both production
use cases and devtools without touching ``endury.domain``.
"""

from __future__ import annotations

from collections.abc import Sequence

from endury.planner.activity.shapes.base import ActivityDefinition
from endury.planner.foundations.types import ActivityFamily, ActivityKey
from endury.training.load import ResolverProtocol
from endury.training.workout import WorkoutStep

#: Lower projection bound for duration-derived targets. The declared
#: ``tss_min`` window is a planning prior, not a physical floor, so
#: clamping a small solved session up to it would render a card larger
#: than the plan sized it. Physical floors (rep lattices, fixed
#: protocols) still bind inside each shape's projection; this bound sits
#: just above ``REST_TSS_THRESHOLD`` so a real session never renders as a
#: rest placeholder.
_DURATION_FIRST_MIN_TSS: float = 1.0


def assignment_target_tss(
    activity: ActivityDefinition,
    hours: float | None,
    resolver: ResolverProtocol | None,
) -> float:
    """Return the build target TSS for *hours* of this activity.

    Every duration-driven session maps its prescribed hours to TSS via the
    activity's own physics (``tss_from_duration``) so the rendered structure
    matches the plan — continuous shapes invert exactly, flexible-rep shapes
    invert the rep lattice (`docs/specs/planner/activity_library.md`,
    R-SESS-B-008). Only fixed test protocols return ``None`` there and fall
    back to the activity's mid-range TSS (their cards never resize with
    solved hours). Duration-derived targets project with a relaxed lower
    bound (``_DURATION_FIRST_MIN_TSS``) so the card honours a solved
    duration below the activity's declared ``tss_min`` instead of silently
    inflating to it.

    Args:
        activity: The ``ActivityDefinition`` to materialize.
        hours: Solved session hours from the plan (0/None → mid-range).
        resolver: Threshold resolver for athlete personalization.

    Returns:
        Target TSS projected onto the activity's feasible lattice.
    """
    derived = (
        activity.tss_from_duration(hours * 60.0, resolver=resolver) if hours else None
    )
    if derived is None:
        derived = (activity.tss_min + activity.tss_max) / 2.0
        return activity.project_target_tss(derived, resolver=resolver)
    return activity.project_target_tss(
        derived,
        lower_bound_tss=min(activity.tss_min, _DURATION_FIRST_MIN_TSS),
        resolver=resolver,
    )


def workout_total_meters(steps: Sequence[WorkoutStep]) -> float:
    """Sum step distances in meters, expanding repeat blocks.

    Args:
        steps: ``WorkoutStep`` list (may contain nested repeats).

    Returns:
        Total distance in meters (0.0 when steps are time-based).
    """
    total = 0.0
    for step in steps:
        if step.type == "repeat" and step.steps and step.iterations:
            total += step.iterations * workout_total_meters(step.steps)
        elif step.duration is not None and step.duration.value_meters is not None:
            total += step.duration.value_meters
    return total


def materialized_swim_meters(
    activity: ActivityDefinition,
    hours: float | None,
    resolver: ResolverProtocol | None,
) -> int | None:
    """Return the total distance of the workout built for *hours* of swimming.

    This is the athlete-facing truth: the distance on the session card,
    after duration-first TSS derivation, lattice projection, and step
    quantization — not the planner's hours-space intent.

    Args:
        activity: Swim ``ActivityDefinition`` to materialize.
        hours: Solved session hours from the plan.
        resolver: Threshold resolver (CSS personalization).

    Returns:
        Whole meters, or ``None`` for time-based swim steps (e.g. the
        fixed CSS test protocol), letting callers fall back to h:mm.
    """
    target_tss = assignment_target_tss(activity, hours, resolver)
    workout = activity.build(target_tss, resolver=resolver)
    meters = round(workout_total_meters(workout.steps))
    return meters if meters > 0 else None


#: Athlete-facing display names, keyed by activity. A slug-derived title
#: like ``Run Vo2Max`` mangles acronyms and reads like an internal key
#: rather than a name a human wrote — override every registered
#: ``ActivityKey`` here so cards render a proper name; the slug-based
#: fallback only fires for keys added without a corresponding entry.
ACTIVITY_DISPLAY_NAMES: dict[ActivityKey, str] = {
    # Running
    ActivityKey.RUN_EASY: "Easy Run",
    ActivityKey.RUN_STRIDES: "Easy Run + Strides",
    ActivityKey.RUN_LONG: "Long Run",
    ActivityKey.RUN_RECOVERY: "Recovery Run",
    ActivityKey.RUN_TEMPO: "Tempo Run",
    ActivityKey.RUN_THRESHOLD: "Threshold Intervals",
    ActivityKey.RUN_VO2MAX: "VO₂max Intervals",
    ActivityKey.RUN_VO2MAX_SHORT: "Short VO₂max Intervals",
    ActivityKey.RUN_PROGRESSION: "Progression Run",
    ActivityKey.RUN_FARTLEK: "Fartlek Run",
    ActivityKey.RUN_HILL_REPEATS: "Hill Repeats",
    ActivityKey.RUN_HM_PACE: "Half-Marathon Pace Run",
    ActivityKey.RUN_FULL_MP: "Marathon Pace Run",
    ActivityKey.RUN_LONG_WITH_MP: "Long Run with Marathon Pace",
    ActivityKey.RUN_5K_RACE_PACE: "5K Race-Pace Intervals",
    ActivityKey.RUN_10K_RACE_PACE: "10K Race-Pace Intervals",
    ActivityKey.RUN_SPEED: "Speed Intervals",
    ActivityKey.RUN_LONG_PROGRESSION: "Long Progression Run",
    ActivityKey.RUN_AEROBIC_TEMPO: "Aerobic Tempo Run",
    ActivityKey.RUN_BRICK_SHORT: "Brick Run (Short)",
    ActivityKey.RUN_BRICK_LONG: "Brick Run (Long)",
    ActivityKey.RUN_BRICK_HIM_TEMPO: "Brick Run — Half-Distance Tempo",
    ActivityKey.RUN_BRICK_IM_TEMPO: "Brick Run — Full-Distance Pace",
    # Trail running
    ActivityKey.RUN_TRAIL_EASY: "Easy Trail Run",
    ActivityKey.RUN_TRAIL_LONG: "Long Trail Run",
    ActivityKey.RUN_TRAIL_B2B: "Back-to-Back Trail Run",
    ActivityKey.RUN_VERT_HIKE: "Vert Climb / Power Hike",
    ActivityKey.RUN_DOWNHILL_REPEATS: "Downhill Repeats",
    # Cycling
    ActivityKey.BIKE_ENDURANCE: "Endurance Ride",
    ActivityKey.BIKE_LONG: "Long Ride",
    ActivityKey.BIKE_LONG_SWEET_SPOT: "Long Ride with Sweet Spot",
    ActivityKey.BIKE_LONG_HIM_RACE_PACE: "Long Ride at Half-Distance Pace",
    ActivityKey.BIKE_LONG_IM_RACE_PACE: "Long Ride at Full-Distance Pace",
    ActivityKey.BIKE_RECOVERY: "Recovery Ride",
    ActivityKey.BIKE_SWEET_SPOT: "Sweet Spot Intervals",
    ActivityKey.BIKE_THRESHOLD: "Threshold Intervals",
    ActivityKey.BIKE_OVER_UNDER: "Over-Under Intervals",
    ActivityKey.BIKE_VO2MAX: "VO₂max Intervals",
    ActivityKey.BIKE_VO2MAX_SHORT: "Short VO₂max Intervals",
    ActivityKey.BIKE_TEMPO: "Tempo Ride",
    ActivityKey.BIKE_CADENCE: "Cadence Drills",
    ActivityKey.BIKE_ANAEROBIC: "Anaerobic Intervals",
    ActivityKey.BIKE_SPRINT: "Sprint Intervals",
    ActivityKey.BIKE_HILL_CLIMB: "Hill Climb Intervals",
    ActivityKey.BIKE_AEROBIC: "Aerobic Ride",
    ActivityKey.BIKE_OPENER: "Opener Ride",
    # Swimming
    ActivityKey.SWIM_ENDURANCE: "Endurance Swim",
    ActivityKey.SWIM_LONG: "Long Swim",
    ActivityKey.SWIM_RECOVERY: "Recovery Swim",
    ActivityKey.SWIM_CSS: "CSS Intervals",
    ActivityKey.SWIM_CSS_SHORT: "Short CSS Intervals",
    ActivityKey.SWIM_VO2MAX: "VO₂max Swim Intervals",
    ActivityKey.SWIM_VO2MAX_SHORT: "Short VO₂max Swim Microset",
    ActivityKey.SWIM_THRESHOLD: "Threshold Swim Intervals",
    ActivityKey.SWIM_DRILL: "Technique Drills",
    ActivityKey.SWIM_PULL: "Pull Set",
    ActivityKey.SWIM_SPRINT: "Sprint Set",
    ActivityKey.SWIM_KICK: "Kick Set",
    # Fitness tests
    ActivityKey.RUN_THRESHOLD_TEST: "Threshold Run Test",
    ActivityKey.BIKE_FTP_TEST: "FTP Test",
    ActivityKey.SWIM_CSS_TEST: "CSS Swim Test",
}


def activity_display_name(key: ActivityKey) -> str:
    """Return the athlete-facing workout title for an ``ActivityKey``.

    Falls back to a title-cased slug if the key is missing from
    ``ACTIVITY_DISPLAY_NAMES`` so newly added activities still render
    something readable until they get a curated entry.

    Args:
        key: The activity key to name.

    Returns:
        Human-readable workout title.
    """
    return ACTIVITY_DISPLAY_NAMES.get(key, key.value.replace("_", " ").title())


# Coaching briefs — the always-visible "what to do" line on a session card

#: Per-``ActivityKey`` athlete-facing brief: one or two sentences saying
#: what the session is and how to execute it.  Authored key by key; a key
#: with no entry falls back to its family sentence in ``FAMILY_BRIEFS``.
ACTIVITY_BRIEFS: dict[ActivityKey, str] = {
    # Running
    ActivityKey.RUN_EASY: (
        "Fully conversational pace — if you can't speak in complete sentences, "
        "slow down. Duration is the point; pace is irrelevant."
    ),
    ActivityKey.RUN_STRIDES: (
        "Easy running with short bursts of relaxed speed late in the run — "
        "20-30 s each, fast turnover, floating rather than sprinting. Jog "
        "until fully recovered between them; these should leave you feeling "
        "springy, not spent."
    ),
    ActivityKey.RUN_LONG: (
        "Steady aerobic long run at conversational effort — let heart rate, "
        "not pace, set the ceiling. Start slower than feels necessary and "
        "drink and fuel on anything over 90 minutes."
    ),
    ActivityKey.RUN_RECOVERY: (
        "Deliberately slow shuffle to move blood through tired legs — "
        "shorter and easier than your easy run. If you finish wondering "
        "whether it counted, you did it right."
    ),
    ActivityKey.RUN_TEMPO: (
        "One continuous block at comfortably hard tempo effort after a "
        "proper warmup — controlled breathing, no surging. Lock the pace in "
        "over the first few minutes and hold it to the end rather than "
        "starting fast and fading."
    ),
    ActivityKey.RUN_THRESHOLD: (
        "Reps of 5-6 minutes right at threshold pace with short jog "
        "recoveries — hard but repeatable, not a race. Settle into each rep "
        "during the first minute; the last one should look like the first."
    ),
    ActivityKey.RUN_VO2MAX: (
        "Hard 3-4 minute reps at VO₂max effort with equal-length recoveries "
        "— breathing should be maximal by the end of each one. Hit target "
        "pace from rep one and hold it; if the last rep collapses, you "
        "started too fast."
    ),
    ActivityKey.RUN_VO2MAX_SHORT: (
        "Short 30/30 microintervals — 30 s hard, 30 s easy, repeated many "
        "times. Each rep is quick and sharp rather than all-out; the "
        "accumulated time at high effort is what does the work, so keep the "
        "recoveries genuinely jogging."
    ),
    ActivityKey.RUN_PROGRESSION: (
        "Progression run that steps up from easy to near-threshold across "
        "four blocks. Be disciplined about how slowly you start — the whole "
        "session only works if the last block is the fastest."
    ),
    ActivityKey.RUN_FARTLEK: (
        "Unstructured speed play: surges of 1-3 minutes at hard effort "
        "separated by easy running. Go by feel rather than the watch, and "
        "keep the easy portions truly easy so each surge has snap."
    ),
    ActivityKey.RUN_HILL_REPEATS: (
        "Strong uphill reps of 2-3 minutes at hard but controlled effort, "
        "jogging back down to recover. Drive from the hips with a tall "
        "posture and short quick steps; effort is the target, pace on a "
        "hill tells you nothing. The session is the reps added up: that "
        "total of hard climbing is what counts, and your hill sets the rep. "
        "A shorter hill means more, shorter reps to the same total; no hill "
        "means a treadmill at 6-8 %; neither means a flat fartlek by effort, "
        "so the hard day keeps its place."
    ),
    ActivityKey.RUN_HM_PACE: (
        "Sustained block at half-marathon race pace — the rhythm you want "
        "to feel automatic on race day. Rehearse it exactly: same effort, "
        "same cadence, same fueling you plan to use."
    ),
    ActivityKey.RUN_FULL_MP: (
        "Continuous block at marathon race pace — moderate effort that "
        "should feel almost too easy at the start. Resist running faster; "
        "hitting the pace precisely matters far more than beating it."
    ),
    ActivityKey.RUN_LONG_WITH_MP: (
        "Progressive long run: relaxed for the first two-thirds, then "
        "finish at marathon pace. Don't drift faster early — the easy "
        "running is what makes the fast finish trainable."
    ),
    ActivityKey.RUN_5K_RACE_PACE: (
        "Reps of 2-4 minutes at 5K race pace with full jog recoveries — "
        "practise the exact effort and turnover you'll need on race day. "
        "Every rep at the same pace; even splits beat a fast first one."
    ),
    ActivityKey.RUN_10K_RACE_PACE: (
        "Longer reps of 4-6 minutes at 10K race pace with short recoveries "
        "— hard, rhythmic, and controlled. Focus on holding form and "
        "cadence as the reps accumulate rather than chasing a faster split."
    ),
    ActivityKey.RUN_SPEED: (
        "Short 30-60 s reps at faster-than-race pace with generous recovery "
        "— this is about leg speed and mechanics, not lung damage. Relax "
        "the shoulders, stay tall, and stop if turnover gets sloppy."
    ),
    ActivityKey.RUN_LONG_PROGRESSION: (
        "Long run that builds from easy to strong sub-threshold effort over "
        "three steps. Start conservatively and let heart rate lead; the "
        "payoff is running well in the final third on tired legs."
    ),
    ActivityKey.RUN_AEROBIC_TEMPO: (
        "Extended steady block at strong aerobic effort — firmer than easy, "
        "softer than threshold, held by heart rate. It should feel "
        "sustainable throughout; if you're counting down the minutes, ease "
        "off."
    ),
    ActivityKey.RUN_BRICK_SHORT: (
        "Short run straight off the bike at easy aerobic effort. Expect "
        "heavy legs for the first five minutes — cue quick cadence and tall "
        "posture and they'll come around."
    ),
    ActivityKey.RUN_BRICK_LONG: (
        "Long endurance run off the bike at race effort on already-fatigued "
        "legs. Settle into rhythm over the first ten minutes, then hold "
        "steady and rehearse your race-day fueling exactly."
    ),
    ActivityKey.RUN_BRICK_HIM_TEMPO: (
        "Run off the bike at half-distance race pace — sub-threshold tempo, "
        "held despite tired legs. This is the rehearsal that matters: lock "
        "in the effort early and refuse to drift above it."
    ),
    ActivityKey.RUN_BRICK_IM_TEMPO: (
        "Run off the bike at full-distance race pace — high aerobic, disciplined, "
        "and deliberately restrained. If it feels too easy, that's the "
        "point; practise the effort and the nutrition together."
    ),
    # Trail running
    ActivityKey.RUN_TRAIL_EASY: (
        "Easy run on trails — conversational on the flats and gentle climbs, "
        "walking anything steep. Steer by heart rate and feel rather than "
        "pace: the hills make the same easy effort cost a little more."
    ),
    ActivityKey.RUN_TRAIL_LONG: (
        "Long trail run over real terrain — power-hike the steep climbs, "
        "run the flats and descents. Steer by heart rate and feel rather "
        "than pace, and fuel every 45 minutes on anything over two hours."
    ),
    ActivityKey.RUN_TRAIL_B2B: (
        "The second long run of a back-to-back weekend — easy effort the "
        "whole way, time on feet over pace. Heavy legs are the stimulus, so "
        "hike the climbs rather than forcing them."
    ),
    ActivityKey.RUN_VERT_HIKE: (
        "Sustained climbing reps, hiking the steep sections with purpose — "
        "tall through the hips, hands on quads or poles. Hold a steady "
        "effort you could carry to the summit, and descend under control "
        "between reps. No climb that long nearby: a treadmill at a steep "
        "incline, or a shorter steep climb repeated until the minutes add up."
    ),
    ActivityKey.RUN_DOWNHILL_REPEATS: (
        "Downhill reps for eccentric leg conditioning — quick light steps, "
        "land under your hips, let the grade carry you instead of braking. "
        "Stop the session the moment form degrades; sore quads tomorrow are "
        "expected. A shorter descent with more reps does the same job; a "
        "flat run does not, so without a descent skip this one rather than "
        "fake it."
    ),
    # Cycling
    ActivityKey.BIKE_ENDURANCE: (
        "Steady aerobic riding at conversational effort — smooth pedalling, "
        "no surges, no chasing segments. Keep the power flat and let the "
        "time on the bike do the work."
    ),
    ActivityKey.BIKE_LONG: (
        "Your long aerobic ride: hold an easy, sustainable effort the whole "
        "way and finish feeling like you could keep going. Eat and drink "
        "from the first hour, not when you start to fade."
    ),
    ActivityKey.BIKE_LONG_SWEET_SPOT: (
        "Long endurance ride with sweet-spot blocks (just under FTP) folded "
        "into steady Z2 riding. Ride the easy stretches genuinely easy so "
        "each block lands at the same power as the first."
    ),
    ActivityKey.BIKE_LONG_HIM_RACE_PACE: (
        "Long ride built around sustained half-distance race-effort blocks "
        "with short steady floats between them. Lock in cadence and aero "
        "position, and rehearse your race nutrition exactly as you'll take "
        "it on the day."
    ),
    ActivityKey.BIKE_LONG_IM_RACE_PACE: (
        "Long ride with full-distance race-effort blocks separated by long steady "
        "endurance stretches — the last block comes when you're already "
        "tired, on purpose. Hold position and effort there, and practise "
        "race-day fuelling throughout."
    ),
    ActivityKey.BIKE_RECOVERY: (
        "Very easy spin, light pressure on the pedals, high cadence. If it "
        "ever feels like training, back off — the only job here is to move "
        "blood and finish fresher than you started."
    ),
    ActivityKey.BIKE_SWEET_SPOT: (
        "Long reps just under threshold — firm and steady, comfortably "
        "uncomfortable, never a maximal effort. Hold the same power on the "
        "last rep as the first; if it's fading, you started too hard."
    ),
    ActivityKey.BIKE_THRESHOLD: (
        "Steady reps right at your FTP — hard but controlled, never a race. "
        "Settle in over the first minute; the closing minutes should feel "
        "like work you could hold for one more rep."
    ),
    ActivityKey.BIKE_OVER_UNDER: (
        "Each rep swings two minutes just under your FTP and one minute just "
        "over it, three times through. Ride the unders steady, lift the "
        "overs without sprinting, and settle straight back down — learning "
        "to recover while still working is the point."
    ),
    ActivityKey.BIKE_VO2MAX: (
        "Hard three-to-five-minute reps well above FTP, breathing deep and "
        "ragged by the end of each one. Ride the first rep at a power you "
        "can repeat — the last rep is what makes the session count."
    ),
    ActivityKey.BIKE_VO2MAX_SHORT: (
        "Short 30-second efforts with only 15 seconds of spinning between "
        "them, in sets with a few easy minutes between — punchy on each "
        "rep, but each set as a whole is the workout. Keep the recoveries "
        "brief and honest; the short rests are the whole point."
    ),
    ActivityKey.BIKE_TEMPO: (
        "One sustained block at tempo — firm, rhythmic, breathing "
        "noticeably but under control. Settle into a steady cadence and "
        "hold it flat rather than pushing hills and coasting descents."
    ),
    ActivityKey.BIKE_CADENCE: (
        "Easy ride with short high-cadence blocks at 100-110 rpm. Keep "
        "power low and let the legs spin fast without bouncing in the "
        "saddle — this is technique, not effort."
    ),
    ActivityKey.BIKE_ANAEROBIC: (
        "Very hard 30-45 second efforts with a couple of minutes easy "
        "between them. Go deep on each one and use the full recovery; if "
        "the last reps drop off badly, end the set there."
    ),
    ActivityKey.BIKE_SPRINT: (
        "Maximal 10-15 second sprints at high cadence with long, full "
        "recoveries. Quality over quantity — every sprint should be as "
        "fast as the first, so take all the rest you're given."
    ),
    ActivityKey.BIKE_HILL_CLIMB: (
        "Strength-focused climbing reps at low cadence, 50-65 rpm, pushing "
        "a big gear at firm tempo effort. Stay seated, keep the upper body "
        "quiet, and drive through the whole pedal stroke."
    ),
    ActivityKey.BIKE_AEROBIC: (
        "Solid aerobic ride at the upper end of easy — steady pressure, "
        "still able to talk in short sentences. Keep it honest and keep it "
        "flat; this isn't tempo."
    ),
    ActivityKey.BIKE_OPENER: (
        "Short, easy ride with a handful of sharp 15-second bursts to wake "
        "the legs up. Keep the bursts smooth and controlled, not maximal, "
        "and finish feeling fresh and quick."
    ),
    # Swimming
    ActivityKey.SWIM_ENDURANCE: (
        "Steady aerobic freestyle in sets of 400 m or less, held at a "
        "comfortable, repeatable rhythm. Count your strokes per length and "
        "keep that count stable to the end — when it climbs, your form is "
        "fading, not your fitness."
    ),
    ActivityKey.SWIM_LONG: (
        "The long continuous swim: big reps at an easy, unhurried pace you "
        "could hold far past the finish. Start slower than feels necessary "
        "and let rhythm and breathing settle — this is about time in the "
        "water, not pace."
    ),
    ActivityKey.SWIM_RECOVERY: (
        "Short, loose, and genuinely easy — freestyle or backstroke, "
        "whatever feels good on your shoulders. Long glide, soft catch, no "
        "effort worth measuring; get out feeling better than you got in."
    ),
    ActivityKey.SWIM_CSS: (
        "CSS interval set: reps at your critical swim speed with only "
        "20-25 seconds rest, so the effort accumulates across the set. "
        "Pace the first rep like the last one — go out fast and the short "
        "rest will bury you."
    ),
    ActivityKey.SWIM_CSS_SHORT: (
        "A compact CSS set: a short warm-up, then reps at your critical "
        "swim speed with only 20-25 seconds rest. Most of the session is "
        "the set itself, so hold the same pace on every rep."
    ),
    ActivityKey.SWIM_VO2MAX: (
        "Short, fast reps clearly above CSS with generous rest to let you "
        "repeat the speed. Push the pace hard, hold your stroke together, "
        "and use the full rest — the point is quality, not fatigue."
    ),
    ActivityKey.SWIM_VO2MAX_SHORT: (
        "A long microset of 50s just above CSS on ten seconds rest — brief "
        "efforts, almost no recovery, relentless rhythm. Fight to keep the "
        "pace and stroke count identical from the first 50 to the last."
    ),
    ActivityKey.SWIM_THRESHOLD: (
        "Sustained threshold reps of 200-400 m with short rest, swum at a "
        "hard-but-controlled pace you could hold for a long time. "
        "Negative-split each rep rather than fading, and keep breathing "
        "patterns consistent throughout."
    ),
    ActivityKey.SWIM_DRILL: (
        "Alternate a technique drill with easy whole-stroke swimming, "
        "carrying the drill's cue into the swim. Pace is irrelevant here — "
        "slow down enough to actually execute the movement well."
    ),
    ActivityKey.SWIM_PULL: (
        "Pull set with paddles and buoy: steady aerobic effort focused on a "
        "strong, high-elbow catch. If your shoulders start talking or your "
        "stroke rate collapses, drop the paddles rather than grinding it "
        "out."
    ),
    ActivityKey.SWIM_SPRINT: (
        "All-out 50s with full recovery between reps — maximum speed, "
        "maximum turnover, no pacing. If the last rep isn't as fast as the "
        "first, take more rest rather than swimming them tired."
    ),
    ActivityKey.SWIM_KICK: (
        "Board or on-your-back kick set at an easy, sustained effort — "
        "small, fast kicks driven from the hips, ankles relaxed. Don't "
        "chase pace; this is about ankle mobility and body position."
    ),
    # Fitness tests
    ActivityKey.RUN_THRESHOLD_TEST: (
        "A 30-minute all-out time trial after a full warmup, used to reset "
        "your threshold pace and heart rate. Go out at an effort you "
        "believe you can hold for the whole thing — even pacing gives an "
        "honest number."
    ),
    ActivityKey.BIKE_FTP_TEST: (
        "A 20-minute all-out time trial to set your FTP, after a thorough "
        "warmup including a 5-minute opener. Start at a power you believe "
        "you can hold and build slightly in the last five minutes — going "
        "out too hard wrecks the number."
    ),
    ActivityKey.SWIM_CSS_TEST: (
        "Time trial protocol: a 400 m all-out, full recovery, then an "
        "all-out 200 m — the pair sets your CSS pace for every swim that "
        "follows. Warm up properly and commit fully to both efforts; a "
        "sandbagged test gives you wrong paces for weeks."
    ),
}

#: Per-family fallback brief — the coarse sentence every unauthored key
#: inherits.
FAMILY_BRIEFS: dict[ActivityFamily, str] = {
    ActivityFamily.EASY: (
        "Aerobic maintenance — conversational effort, keep it genuinely easy."
    ),
    ActivityFamily.LONG: (
        "The week's endurance cornerstone — builds durability and fuel economy."
    ),
    ActivityFamily.RECOVERY: (
        "Active recovery — promote blood flow without adding training stress."
    ),
    ActivityFamily.TEMPO: (
        "Sustained steady-state work — raises the pace you can hold for hours."
    ),
    ActivityFamily.THRESHOLD: (
        "Threshold development — extends time near your sustainable red line."
    ),
    ActivityFamily.INTERVAL: (
        "High-intensity intervals — sharpens top-end fitness and economy."
    ),
    ActivityFamily.TEST: (
        "Fitness test — pace it honestly so your zones stay calibrated."
    ),
}


def family_intent(family: ActivityFamily) -> str:
    """Return the deterministic fallback brief for a family.

    The base intent a materialized session carries when its activity key
    has no authored brief, exposed so a later re-materialization (a week
    adjustment) reproduces it instead of stacking sentences onto the
    persisted one.

    Args:
        family: The activity family.

    Returns:
        The family's brief sentence.
    """
    return FAMILY_BRIEFS[family]


def activity_brief(key: ActivityKey, family: ActivityFamily) -> str:
    """Return the athlete-facing brief for one activity.

    Args:
        key: The activity key being materialized.
        family: The activity's family, used when the key is unauthored.

    Returns:
        The per-key brief, or the family fallback sentence.
    """
    return ACTIVITY_BRIEFS.get(key) or family_intent(family)


def capability_caveat(
    activity: ActivityDefinition, *, recovery_week: bool, taper_week: bool
) -> str | None:
    """Return a caveat when a key session lands in a week it does not suit.

    The intent picker resolves on family and tags alone and never looks at
    the capability profile, so the ad-hoc schedule and the planned-session
    swap call this when the session lands in a plan's week. Only that week
    counts: a key session in an ordinary week, or with no plan behind it,
    gets no caveat. The athlete reads the sentence as written.

    Args:
        activity: The resolved activity to check.
        recovery_week: Whether the session lands in a recovery week.
        taper_week: Whether the session lands in a taper week.

    Returns:
        One sentence naming the week, or ``None``.
    """
    profile = activity.capability_profile
    if profile is None:
        return None
    if taper_week and not profile.taper_ok:
        week = "taper"
    elif (
        recovery_week
        and not profile.recovery_ok
        # intent: the planner defers tests into recovery weeks itself
        # (test_injection); fresh legs give the honest number.
        and profile.family != ActivityFamily.TEST
    ):
        week = "recovery"
    else:
        return None
    return (
        f"{activity_display_name(activity.key)} is a key session, and it "
        f"lands in a {week} week, which is there to absorb your training "
        "rather than add to it. An easy session fits that week better."
    )


__all__ = [
    "ACTIVITY_BRIEFS",
    "ACTIVITY_DISPLAY_NAMES",
    "FAMILY_BRIEFS",
    "activity_brief",
    "activity_display_name",
    "assignment_target_tss",
    "capability_caveat",
    "family_intent",
    "materialized_swim_meters",
    "workout_total_meters",
]
