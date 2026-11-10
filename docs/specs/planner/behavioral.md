# Planner — Behavioral Requirements

The contract for what the planner and its outputs MUST do, observed behavior by
observed behavior. Most entries are seeded from manual inspection
of the goal-ambition sweep (`make run-devtool TOOL=planner_sweep_site`):
read the site, note what looks wrong or under-specified, add a requirement.

## How to use this file

- Each requirement is one short, imperative, checkable rule (MUST / MUST NOT).
  Add rationale only when the *why* isn't obvious from the rule itself.
- Status tags: `[ ]` open · `[x]` implemented and verified against sweep output ·
  `[~]` partial / needs re-check.
- Group by domain; add sections as needed. Link the satisfying module/function
  inline so inspection can jump straight to the code.

---

## Display & Units

### Swimming distances and intervals

- `[x]` **R-DU-001**: Swimming session distances and intervals must be in meters
- `[x]` **R-DU-002**: Swimming sessions and swimming bounds (target/min/max) must all be encoded in meters
- `[x]` **R-DU-003**: Swimming sessions must round in total to a whole number of hundred meters
- `[x]` **R-DU-004**: Swimming sessions intervals must be at least 50m
- `[x]` **R-DU-005**: Swimming drill sessions must have a nice explanation for the drills
- `[x]` **R-DU-006**: Swimming may use/allow different swimming styles, when appropriate
- `[x]` **R-DU-007**: The CSS swim test (`swim_css_test`) must be distance-based — warm-up, build strides, the 400 m and 200 m time trials, and cool-down all render with distance durations (e.g. `400 m`, `4 × 50 m`, `200 m`), not minutes/seconds — so pool swimmers can execute against a lap count, not a clock
- `[x]` **R-DU-008**: A continuous-distance swim must hand its main set over as reps, not as one unbroken block. Every `SwimLinearDistanceActivity` declares a `max_rep_meters` cap: 400 m for `swim_endurance`, `swim_pull` and `swim_recovery`, 200 m for `swim_kick`, 1,000 m for `swim_long` — the half-IM / IM long swim is the only session that earns 800–1,000 m reps, and no session ever prescribes a 1,600 m rep. The reps are all the same whole-hundred length give or take 100 m and sum to the block exactly, so splitting moves no distance and no load. Each rep of a split set is followed by a 15 s wall rest, a `rest` step the watch counts, as the interval swims rest after every rep: the card's duration, the hours it is sized from, and the hours a menu's metres book include it; its load is nil and it is no time in a zone (R-ACT-016) (`activity/shapes/aquatic.py:SwimLinearDistanceActivity._main_steps`, `max_rep_meters`, `SWIM_SPLIT_REST_SECONDS`)

---

## Session Generation

- `[x]` **R-SESS-B-000**: Every athlete MUST test in the first week of a plan and every
  eight weeks after it, in a recovery week, never in the peak, at every horizon and
  volume the plan is offered at: the tests set the zones every other session is written
  against, and an athlete without them trains on stale numbers for a phase. A menu
  authors its tests as `cadence_weeks: 8` slots (R-SESS-041), keyed so a squeezed week
  shortens its easy sessions before the fixed test protocol drops; the first week is
  tests plus easy sessions only, and the long sessions open in week 2 from the athlete's
  recent longest (a replan continues the plan's cadence and does not re-open it). A
  maintenance block is the exception: it follows a season the athlete
  trained and tested through, so it opens on their current zones and tests every eight
  weeks from week 5, the first load week after its first recovery week, through an
  eight-member rotation that puts each test in a quality session's place (a block runs
  3:1 from week 0, so the rotation index is the week). A run or bike test that would
  fall in the four days after a B/C race moves to the same day a week later, or that
  occasion is skipped (R-SESS-031); a test whose week a tune-up race or the easier week
  after a long one takes moves to the same weekday of the first load week after it
  (R-SESS-042). *Trail 20K BASIC since
  2026-10-11; the half-Ironman MODERATE since 2026-10-12 (1, 8 at 16 to 20 weeks;
  1, 8, 16 at 22 to 28), where its own windows and rotations had reached the same weeks
  at 16, 24 and 28 but left an 18-week plan with one test, a 20-week plan testing in
  week 12 and a 22-week plan testing in weeks 8 and 12. Trail maintenance BASIC and
  triathlon maintenance MODERATE test in weeks 5 and 13 since 2026-10-12 (trail
  maintenance tested in weeks 1, 9 and 17 for a day; maintainer).*
  (`sessions/session_generator.py:cadence_weeks`)
- `[x]` **R-SESS-B-001**: Every emitted `PlacedSession` MUST satisfy
  `0 < min_hours ≤ target_hours ≤ max_hours`; a session whose scaled max falls
  below the `_MIN_SESSION_HOURS` floor MUST be dropped, never emitted with
  degenerate bounds. *0 violations across 8,450 swept sessions; smallest min
  10 min.* (`sessions/session_generator.py`)
- `[~]` **R-SESS-B-002**: A progressive session's `target_hours` across
  consecutive same-phase LOAD weeks MUST be non-decreasing and step-capped at
  `max(prev·MAX_DURATION_STEP_FRACTION, sport floor)`. A progressive
  session other than the race rehearsal MUST, as solved, be at most that step
  over the one solved in the load week before it, across a recovery week and a
  phase change too (R-SESS-013); the race rehearsal at most a quarter (the
  Ironman BASIC big ride 0.3) over the least its progression was last booked
  (R-SESS-023, `tests/planner/test_rehearsal_step.py`). *Holds at the generator stage (0 violations /
  1,084 LOAD pairs), and as solved for progressive running long runs: 0 of the
  default athletes at 0.8, 1.0 and 1.25 of their volume, every race goal and tier,
  the minimum horizon and 8 weeks more, past the step and one 5-minute grid step
  (`tests/planner/test_long_run_ramp.py`, all 216 plans since the PEAK long
  runs joined the ramp, session_generator V9), and since 2026-09-28 for the
  long ride and the long swim of the four triathlon goals
  (`test_no_long_ride_or_swim_jumps_past_the_step`, 144 plans). One gap:
  non-progressive long endurance sessions (e.g. `bike_endurance`) bypass the
  step cap, jumping 20–25 min at phase-frac resets because `last_duration` is
  written only for `progressive=True` specs. A second gap, since 2026-11-03:
  triathlon maintenance MODERATE's sweet-spot long ride books no less than its
  107-minute card floor, so an athlete with under about 7.2 h a week of recent
  training jumps to it (80 to 107 minutes at about 6 h), and at about 4 h the plain
  ride after a recovery week falls back to 74 minutes.*
  (`sessions/session_generator.py`)
- `[~]` **R-SESS-B-003**: IM long-session peak durations MUST reach the
  tier-invariant literature benchmarks (long bike 4.5–6 h, long run 2.5–3 h,
  IM race-pace ride 4.0–5.5 h ≈ 70–80% of the race bike leg) at every
  ambition tier — long-session duration is driven by race demands, not weekly
  capacity (tlc §4: the "Long Session Benchmarks" table has no athlete-level
  dimension; a finish-tier Ironman plan can schedule 5 h rides / 3 h
  runs inside a 10 h/wk plan). *BASIC fixed at every horizon 20–40 wk whose long
  ride has grown within a quarter of 4.5 h (R-SESS-023's rehearsal step; from a
  90-min ride with no capacity cap the 22–26 wk plans reach 210 min and keep their
  routine weekend, since 2026-10-03): the PEAK Sat/Sun
  `race_sim` slots deliver a 4.5 h race-pace ride + 2.5 h long run on exactly
  one LOAD week per plan via the generator's race-sim week selector
  (R-SESS-023) — the earlier 0.2–0.6 frac-window routing zero-fired at
  23/26 wk (ride collapsed to 3.4–3.5 h) and double-fired at 31/34/37/40 wk.
  Hairline residuals: MODERATE
  20 wk longest ride is 4.42 h (5 min under the 4.5 h benchmark — the only
  nonzero rule on ironman_moderate), and COMPETITIVE 24–32 wk race-pace ride
  is 5.67 h, just above the 4.0–5.5 h race-pace band.*
  (`sessions/menus/ironman.yaml` BASIC `peak/load`,
  `sessions/session_generator.py:race_sim_weeks`)
- `[x]` **R-SESS-B-004**: BUILD-phase brick runs MUST reach a durability-relevant
  off-the-bike run duration (~33–40 min, the top of the tri.md §4 20–40 min BUILD
  brick band), not sit at a token flat 20–25 min. Off-the-bike running is the
  specific race skill BUILD should rehearse; the baseline menus pinned every
  BUILD brick to a flat ~20–25 min `run_brick_short` and deferred the only long
  brick to a single PEAK week. *All IM tiers: the three BUILD `run_brick_short`
  slots carry `progression: im_build_brick_run` (band top 35 min), raising the
  longest BUILD brick from a flat ~25 min to 35 min (the sub-75-min running
  progressive floor holds it phase-constant at the band top). Scored by
  `build_brick_run_duration` (longest BUILD brick vs the 33–42 min band) → 0 on
  moderate/competitive/recovery and the 20/24/28/32 wk COMPETITIVE sweep. The
  added late-BUILD volume raised the global-max LOAD week, so the COMPETITIVE
  TAPER Sat ride / Sun run upper bounds were lifted (105→120 / 55→65 min) to hold
  `taper_volume_drop` in band (sweep taper improved 0.0656→0.0631; race-day TSB
  stays +8–18).*
  (`sessions/menus/ironman.yaml` BUILD brick slots and their
  `im_build_brick_run` progression,
  `quality/planner/score_quality.py:_build_brick_run_duration`)
- `[x]` **R-SESS-B-005**: The IM race-pace long ride MUST finish on a race-pace
  block, not on its easiest Z2 recovery. A race-day dress rehearsal trains the
  durability skill of holding race effort in a fatigued state (tlc §2.4:
  "finishing the last 30–60 minutes of a long ride … at race pace trains the
  specific ability to produce quality output in a fatigued state"); baseline
  `BIKE_LONG_IM_RACE_PACE` interleaved each race-pace block with a longer Z2
  recovery and ended every rep on that recovery, so the whole ride faded to its
  easiest 55-min block before cooldown. *Fixed via the `end_on_work` flag on
  `IntervalPiecewiseActivity` (orders the recovery block before the work block
  within each rep so the ride ends at race pace), set on
  `BIKE_LONG_IM_RACE_PACE`. Pure structural reorder — TSS/duration/TID are
  order-independent, so no other rule or race-day TSB
  moves. Scored by `race_pace_ride_ends_at_effort` (1.0 if the built ride ends
  on recovery, 0.0 if on race pace) → 0 on moderate/competitive/recovery and the
  20/24/28/32 wk COMPETITIVE sweep. The HIM sibling `BIKE_LONG_HIM_RACE_PACE`
  carries the identical structure and is a one-line follow-up.*
  (`activity/shapes/land.py:IntervalPiecewiseActivity.build`,
  `activity/catalog/bike.py:BIKE_LONG_IM_RACE_PACE`,
  `quality/planner/score_quality.py:_race_pace_ride_ends_at_effort`)
- `[x]` **R-SESS-B-006**: Every IM plan MUST schedule at least one swim whose
  MATERIALIZED total distance reaches the 3.8 km race distance (band
  3,800–4,500 m, tlc §4 "Long Session Benchmarks"), and the swim
  metres → hours → workout round trip MUST be lossless — the workout built
  from a swim's solved hours recovers the authored menu metres. *Was violated
  at every tier×horizon (longest rendered swim 2,800/3,100/3,300 m for
  BASIC/MOD/COMP vs 3,900–4,500 m authored): the generator budgeted metres at
  raw CSS (IF 1.0), materialization re-derived size via the generic z_low TID
  bridge (42.25 TSS/h vs the activity's true ~67 TSS/h at IF 0.82) at a
  mismatched CSS (102 render vs 120 budget), and the swim_long tss_max=90
  clamp truncated what survived. Fixed by (a) converting swim metres→minutes
  at the activity's prescribed pace `css / IF` for continuous swims
  (R-SESS-011), (b) duration-first materialization through one shared bridge
  (`assignment_target_tss`: dossier, calendar pills, scorecard), (c) a single
  CSS for budgeting and rendering (synthetic resolver 102→120 = the generator
  default), and (d) linear-swim TSS windows widened to span the authored menu
  extremes. Longest rendered swim now 3,900–4,100 (BASIC) / 4,300 (MOD) /
  4,500 m (COMP) at every horizon 20–32 wk. Scored by `long_swim_peak`
  (materialized metres vs the 3,800–4,500 band) → 0 across the matrix. Honest
  wall-clock accounting raised swim share to ~21% (lit band 15–20%):
  `sport_mix_swim` reads ~0.09–0.12 on BASIC/COMP — swim volume in metres is unchanged; only its time
  cost is now truthful.*
  (`sessions/session_generator.py:_swim_minutes`,
  `devtools/planner/_shared/sessions.py:assignment_target_tss`,
  `activity/catalog/swim.py`,
  the rulebook's `long_swim_peak`, read off the card by `bench/readings.py`)
- `[x]` **R-SESS-B-007**: For duration-driven activities (those with
  `tss_from_duration`), the materialized card duration MUST equal the solved
  session duration within rounding — catalog `[tss_min, tss_max]` windows
  MUST span the authored menu extremes so lattice projection never resizes a
  prescribed session. *Audit across every authored menu extreme (all goals ×
  phases × ambitions + maintenance) found 14 land activities clipping: worst,
  the IM 3 h long run (`run_long_progression`, tss_max 150) rendered as a
  2:17 card, `bike_endurance` 3:22 → 2:27, and capacity-squeezed short long
  sessions clamped UP (52-min `bike_long` → 118-min card). Fixed by widening
  the 14 windows (3 bike + 11 run) to the authored envelope. Residual: a
  10-min authored `run_easy`/`run_brick_short` builds as 14 min — the workout
  scaffold's own floor (warm-up 8 + cool-down 5 + 1-min main), not a clamp.
  The window-spans-envelope clause is now enforced by
  `tests/planner/test_menu_envelope.py` (which replaced the hand-written
  per-window catalog comments); interval activities gained the same duration
  honesty in R-SESS-B-008, leaving only the fixed test protocols exempt.*
  (`activity/catalog/bike.py`, `activity/catalog/run.py`,
  `devtools/planner/_shared/sessions.py:assignment_target_tss`,
  `tests/planner/test_menu_envelope.py`)
- `[x]` **R-SESS-B-008**: Flexible-rep activities (interval, CSS-interval, and
  drill shapes) MUST derive their card size from the solved session duration
  by inverting duration→rep-count within `[rep_count_min, rep_count_max]` —
  |materialized card duration − solved duration| ≤ one rep's wall-clock
  (work + rest) at every authored menu extreme. *Was violated by all 21
  interval/fixed activities returning `None` from `tss_from_duration`: their
  cards rendered at mid-window TSS regardless of the QP-solved duration (a
  `bike_threshold` solved at 75 min built the same card as one solved at
  50 min). Fixed by rep-lattice inversion on the three flexible-rep shapes —
  enumerate one lattice candidate per rep count, return the candidate whose
  re-solved duration lands closest to the request — covering 18 of the 21;
  the 3 fixed test protocols (`bike_ftp_test`, `run_threshold_test`,
  `swim_css_test`) stay duration-independent by design (exemption documented
  on the shape classes). 21 catalog windows re-derived from the authored
  menu envelope (computed on an unclamped copy of each shape — the old
  windows would otherwise distort the very inversion used to size them) so
  lattice projection never resizes the derived TSS. Audit at every authored
  menu extreme ±2.5 min (the generator's 5-min nominal rounding): land cards
  track the solved duration to ≤ 0.5 min and swim cards to ≤ 2.3 min — all
  inside the one-rep / R-DU-003 100 m-rounding bounds, vs up to 25-min
  mid-window errors before. Residual: where menus author beyond a shape's
  lattice (3,000 m `swim_css` vs its 2,600 m ceiling; a 23-min recovery-menu
  `bike_threshold` vs its 49-min scaffold+lattice floor) the card clamps to
  the nearest lattice edge — a menu-vs-shape authoring gap, not a sizing
  defect. Scorecard matrix unchanged (materialization-only).*
  (`activity/shapes/land.py:IntervalPiecewiseActivity.tss_from_duration`,
  `activity/shapes/aquatic.py:SwimCssIntervalActivity.tss_from_duration`,
  `activity/shapes/aquatic.py:SwimDrillSwimActivity.tss_from_duration`,
  `tests/planner/test_menu_envelope.py`)

---

## Macro Planning (QP)

- `[x]` **R-MACRO-B-001**: Consecutive LOAD→LOAD solved hours per sport MUST NOT
  exceed `SPORT_RAMP_LIMIT·c_k + 1.0`, and the QP MUST raise `ValueError` on
  infeasibility rather than return a partial budget. *0 ramp violations across
  the goal×ambition sweep.* (`macro/macro_planner.py:280`)
- `[x]` **R-MACRO-B-002**: Solved weekly hours MUST NOT exceed
  `capacity.weekly_ceiling[k]`, and each race MUST arrive with race-day
  TSB / CTL ≥ its freshness floor (R-MACRO-016) wherever the session bounds
  allow it. *0 ceiling violations; `race_day_tsb`
  penalty 0 across the sweep.* (`macro/macro_planner.py:269`)

---

## Day Assignment

- `[x]` **R-DAY-B-001**: `len(assignments)` MUST equal `len(sessions)` for every
  scenario — no training session may be silently dropped. The only exceptions
  are the *defined and logged* race-week drops (post-A-race sessions; race-day
  colliders with no earlier same-sport-free day) — see R-DAY-004/006/007 in
  `day_assigner.md`. Sunday races (the menu authoring convention) never drop.
- `[x]` **R-DAY-B-002**: No week may place two hard same-sport sessions on the
  same calendar day, and the long bike lands on a weekend day. *Menu-authored
  and enforced at import time by the day-layout lints (R-SESS-025,
  `menus/contracts.py`) — no longer a runtime concern.*

---

## Phase Planning

- `[~]` **R-PP-001**: Every RECOVERY week MUST reduce planned hours and recur at
  the ambition's `recovery_cadence` (no LOAD run longer than the cadence).
  *Cadence holds universally (`recovery_cadence` penalty 0 at the cadence level;
  the share-based scorecard band is now ambition-scaled — BASIC's deliberate 2:1
  cadence (share ~0.29-0.33) reads 0 against its [0.27, 0.36] band, MOD/COMP keep
  the 3:1 [0.20, 0.30] band — so the band no longer mis-flags it). The hours clause now
  holds for all three Ironman tiers (`recovery_week_depth` 0 — BASIC got its
  own BUILD-recovery menu + deepened PEAK-recovery rows, mirroring the
  MOD/COMP split) and for half-Ironman (`recovery_week_depth` 0.0022/0/0
  across BASIC/COMP/MOD, matrix baseline). Since 2026-09-25 the running
  recovery menus keep a shortened Sunday long run, and the olympic, sprint,
  half-marathon and 10K menus author one recovery week per phase, sized per
  tier against that phase's load weeks, with the fitness tests in at most
  one recovery week per plan (none at BASIC) instead of every one; the
  band is per tier (BASIC [0.50, 0.70], matching its 0.50
  `recovery_load_fraction` ceiling; MOD/COMP [0.55, 0.70]). Every
  short-course and running cell reads 0-0.07. The
  residual is BASIC PEAK recovery weeks after the biggest late-BUILD load
  week (16-week olympic and 10K, 12-week sprint: 0.39-0.44 of it), since one
  menu serves horizons whose PEAK recovery ceilings differ by 20-30 %.*
  (`phase/phase_planner.py:_stamp_recovery_weeks`)
- `[~]` **R-PP-002**: The longest long session of each sport MUST fall ~4–6 weeks
  before the race (in PEAK), not mid-BUILD. *Fixed for Ironman at all three
  tiers (`longest_long_session_in_peak` + `peak_long_timing` = 0):
  PEAK long slots carry `progression=` with lower bounds above the BUILD
  ceiling; BASIC places its big race-sim weekend via the generator's
  race-sim week selector (R-SESS-023) onto exactly one LOAD week at
  race−4..6 — never the most-shed final pre-taper LOAD week — verified at
  every horizon 20–40 wk. Half-Ironman is clean too (both metrics 0 at all
  three tiers, matrix baseline), and since 2026-09-25 so are the olympic and
  the half marathon: their BUILD ceilings sit below the PEAK floor after
  the ramp's 5-minute rounding. Still violated at one horizon each: the
  8-week 10K, whose PEAK holds only a recovery week, and the 12-week
  marathon at BASIC/MOD, whose one PEAK load week follows a recovery week
  and is capacity-capped below the last BUILD week
  (`longest_long_session_in_peak` 0.1667).*
  (`sessions/menus/ironman.yaml`,
  `sessions/session_generator.py:race_sim_weeks`)
- `[x]` **R-PP-003**: TAPER weeks MUST be monotonically non-increasing in planned
  hours and in each sport's longest session, and the first TAPER week MUST drop
  below the final PEAK-phase LOAD week.
  *Verified across the IM sweep (7.67 > 5.08 > 3.42 h; first taper < final PEAK
  LOAD at every horizon).* (`macro/capacity.py:158`,
  `sessions/menus/running_marathon.yaml` `taper/taper`,
  `tests/planner/test_taper_falls.py`)
- `[x]` **R-PP-004**: A-goal phases MUST be ordered BASE → BUILD → PEAK → TAPER
  with no regression. *Enforced by `validate_phase_plan` rule 2 and asserted in
  `plan_phases`; verified across goals/ambitions.* (`phase/phase_planner.py`)
- `[x]` **R-PP-005**: Each `WeekSpec.sport_split` MUST sum to 1.0 (±0.01).
  *Enforced in `WeekSpec.__post_init__` + `validate_phase_plan` rule 5.*
  (`foundations/types.py`)
- `[x]` **R-PP-006**: A B or C tune-up race MUST cost the A preparation only
  its race week: a load week before it, the race week as its mini-taper, an
  A-phase training week after it with no hard run or bike session in its
  first four days (R-SESS-031; one easier A-phase week after a B race over
  3 h, whose first four days hold no moderate run or ride either), and a B race arriving fresh, not tapered
  (race-day TSB / CTL near 0.24-0.29).
  (`phase/phase_planner.py:_splice_secondary_goals`)
- `[x]` **R-PP-007**: When the PEAK run before the taper holds a single LOAD
  week, that week's capacity ceiling MUST NOT fall below the ceiling issued
  to the LOAD week before it; the two-step peak shed (`PEAK_SHED_FACTORS`)
  shapes a run-down across two or more PEAK LOAD weeks only, and only before
  a taper of `PEAK_SHED_MIN_TAPER_WEEKS` (3) or more: before a shorter taper
  the plan sheds in its taper weeks alone. (`macro/capacity.py:_peak_load_shed_window`,
  `macro/capacity.py:143-166`)

---

## Session Cards (athlete-facing)

- `[x]` **R-SC-001**: Workout title must be a human-readable name, not an `activity_key` slug
- `[x]` **R-SC-002**: Repeated interval and drill sets must render as repeat blocks (`N × dur @ target / recovery`), not one row per rep
- `[x]` **R-SC-003**: The running-power-vs-pace disclaimer must only appear on steps whose primary target is running power
- `[x]` **R-SC-004**: Every workout step list must include an explicit warmup and cooldown — no session may render as a single multi-hour interval
- `[x]` **R-SC-005**: Achieved TSS must lie within the declared `tss_min`–`tss_max` range
- `[x]` **R-SC-006**: Sub-minute durations must render in seconds, never as fractional minutes
- `[x]` **R-SC-007**: Zero-duration steps must be omitted from the card
- `[x]` **R-SC-008**: A step's zone label and effort label must agree (HR Z2 ≠ "threshold"; a threshold step uses the threshold zone number; a bike step is banded by power since 2026-09-24 — 87 % FTP is power Z3 Tempo, which the HR table read as Z2 "Aerobic effort"; since 2026-09-30 a run or bike step at power Z3 or harder shows the higher of its HR and power zones, so a marathon-pace run at IF 0.85 is HR Z3 Tempo, not Z1; since 2026-10-06 the number follows the athlete's own intervals.icu zones when they have them while the effort words follow the prescribed intensity, so the two can sit a zone apart where the athlete's scheme does; since 2026-10-14 a run step's HR zone and words come from its pace rung, Friel's run pace zones (R-ACT-028), so a run at IF 0.85 is HR Z2 Aerobic and the half-Ironman brick's tempo at IF 0.91 HR Z3 Tempo, while a ride keeps the higher of its HR and power zones)
- `[x]` **R-SC-009**: Swim steps with target type `open` must carry a human cue in the description
- `[x]` **R-SC-010**: Brick runs must carry brick-specific guidance, not the generic easy-run description
- `[x]` **R-SC-011**: A step's description must match its prescribed intensity — a Z2/Z3 interval cannot reuse the generic "easy aerobic, conversational pace" blurb
- `[x]` **R-SC-012**: Each sub-block of a progression run (or any multi-segment endurance step) must carry its own intensity-specific cue, not the same description on every block
- `[x]` **R-SC-013**: A "recovery" step longer than its preceding work step must be described as steady-state riding/running, not "active recovery" (e.g. 55-min Z2 segments inside `BIKE_LONG_IM_RACE_PACE`)
- `[x]` **R-SC-014**: Sessions over 75 min must carry a fueling/hydration cue (g/h carbs, when to start) on the header or main step — long rides and runs are nutrition rehearsals (goal-agnostic; the run cue was wrongly IM-gated before, and left the trail long run and the back-to-back day out until 2026-10-06)
- `[x]` **R-SC-015**: Reps too short to elicit VO₂max must not use the "VO₂max effort — near maximal sustainable" boilerplate that suits 3-5 min reps: ≤30 s reads as sprint/stride wording, 30-90 s as speed-rep wording (VO₂ kinetics need ~90 s+ of sustained work)
- `[x]` **R-SC-016**: Step durations of 10 min or longer must round to whole minutes — never render "36m 56s" or "33m 29s"
- `[x]` **R-SC-017**: Swim cards must display an estimated total session duration alongside distance, so triathletes can plan pool time
- `[x]` **R-SC-018**: A test session must never display a "0 m" or "0 min" headline stat — pick the unit that has meaningful content (e.g. CSS swim test card currently shows `0 m distance`)
- `[x]` **R-SC-019**: Pace ranges must render in colon notation (e.g. `6:16/km–6:40/km`), not the verbose `6m16s/km–6m40s/km` form
- `[x]` **R-SC-020**: Swim warm-up and cool-down steps must carry a non-empty athlete-facing description — currently Endurance/Long/Pull-Set sets render blank description cells for WU and CD
- `[x]` **R-SC-021**: IM long-rehearsal rides (≥3 h, e.g. `bike_long`, `bike_long_im_race_pace`, `bike_long_sweet_spot`) must spell out the fueling protocol — g carbs/h, first-bottle timing, hydration cadence — not a single throwaway sentence
- `[x]` **R-SC-022**: `bike_long_im_race_pace` interval descriptions must reflect IM race-pace work, not paste the generic "Easy aerobic effort — conversational pace, building base endurance" copy from `bike_long`
- `[x]` **R-SC-023**: The Z2 segments inside `bike_long_im_race_pace` (currently 4 × ~55 min between race-pace reps) must read as steady-state aerobic riding, not as "Active recovery — keep moving, very easy effort"
- `[~]` **R-SC-024**: Long brick sessions (`run_brick_long`, `run_brick_im_tempo`) must reference the paired bike workout on the card — which ride precedes it, and within how many minutes of dismount the run starts (card now carries a generic 5-min-of-dismount note; resolving the specific paired bike requires plan-context, not yet wired)
- `[x]` **R-SC-025**: IM swim sessions ≥2,000 m (`swim_long`, IM-distance `swim_endurance`) must include open-water guidance — sighting cadence, wetsuit cue, bilateral breathing — or offer an OWS variant
- `[x]` **R-SC-026**: IM long runs (≥2 h `run_long`, `run_long_progression`) must spell out walk-break and on-the-run fueling cadence — IM marathon training is not equivalent to standalone marathon long runs
- `[x]` **R-SC-027**: IM race-week openers (`bike_opener`) and primers must state their "race-day minus N" purpose and "ride/run into form" intent, not read as a generic easy ride
- `[x]` **R-SC-028**: IM race-rehearsal sessions (long race-pace rides, long bricks) must call out gear/kit — aero bars, race-day nutrition, race shoes, race kit — these are dress rehearsals, not just hour-counters
- `[x]` **R-SC-029**: When a brick's bike leg serves as the run warmup (`run_brick_im_tempo` currently has a 0 s warmup that R-SC-007 hides), the card must state "no separate run warmup — bike legs are your warmup", not silently omit the WU row
- `[ ]` **R-SC-030**: IM session cards must surface race-distance context (e.g. "swim ≈ 80% of race distance", "ride ≈ 60% of race bike split", "run ≈ 50% of IM marathon") so athletes can calibrate the session against the 3.8 km / 180 km / 42.2 km targets
- `[x]` **R-SC-031**: A coaching note that names a race distance must be routed by the plan's goal, not by duration alone — the R-SC-026 IM run-leg note fires only for `GoalType.IRONMAN` (a half-Ironman PEAK long run reaches 95-125 min and used to inherit it), and a goalless render (ad-hoc `build_workout`) emits no goal-specific note at all

---

## Cross-cutting

- `[x]` **R-CC-001**: A goal's sport set MUST derive from a single canonical
  source, not a hand-maintained running-vs-tri frozenset. *Closed:
  both `_RUNNING_GOALS` frozensets are gone. `phase_tables.goal_sports`
  reads the goal's own `default_sport_split` keys and `phase_planner` uses it
  directly, so a new goal type can only be registered in one place.*
- `[x]` **R-CC-002**: Each distinct hours→TSS conversion semantics MUST have
  exactly one function name. *Closed by renaming, not by
  merging: the two are genuinely different functions for different layers.
  `macro/tss_estimator.tss_per_hour(TIDFractions)` keeps its name — the
  planner's TID-mix bridge, imported at 8 sites across the pipeline, the QP and the
  devtools. `training/load.tss_per_hour` — the
  per-sport IF-curve, 1 internal call and 1 test import — became
  `tss_per_hour_at_if`, named for the input that distinguishes it.*
