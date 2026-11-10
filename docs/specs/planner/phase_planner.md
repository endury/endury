# Phase Planner — Requirements

The contract for planner layer 1: turning a set of race goals plus a training
start date into an ordered, gap-free week skeleton (phase, mode, sport split)
that every downstream layer builds on.

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### I/O contract
- `[x]` **R-PHASE-001**: `plan_phases` MUST take a `PhaseRequest`
  (`goals: list[RaceGoal]`, `training_start: date`, `ambition: AmbitionLevel`,
  `prior_training_weeks: int = 0`, `horizon_join_weeks: int = 0`)
  and return a `PhasePlan` (`weeks: list[WeekSpec]`, `prior_weeks: tuple[WeekSpec, ...]`,
  `race_events: list[RaceEvent]`, `a_goal: RaceGoal`). It MUST raise `ValueError` if the
  request fails hard validation, and MUST NOT touch the DB or any I/O — pure function of
  the request.
- `[x]` **R-PHASE-002**: The plan MUST span weeks `[0, race_week]` inclusive where
  `race_week = (a_goal.race_date − training_start).days // 7`; `WeekSpec.week`
  indices MUST be gap-free `[0..N)` (downstream layers index by week number).
- `[x]` **R-PHASE-002b**: With `prior_training_weeks = k > 0` (replanning) and
  `horizon_join_weeks = j` (the weeks a too-short race horizon lacks), the
  four steps MUST run on the virtual timeline starting `k + j` weeks before
  `training_start`, post-conditions MUST be checked on the full virtual plan,
  and the result MUST be the re-indexed suffix:
  `plan_phases(prior=k).weeks == plan_phases(prior=0, start−k wk).weeks[k:]`.
  The `k` trained weeks are kept as `PhasePlan.prior_weeks`, re-indexed `-k..-1`
  and carrying their phase, mode and owning goal, so the session generator
  continues the plan's own timeline (R-SESS-041); the `j` joined weeks are
  discarded, so a fresh short-horizon plan still opens in its first real week.
  Validation week math (horizon, B/C race weeks) MUST use the virtual start;
  min-horizon therefore applies to `prior + joined + actual` weeks. Race-date-after-start
  checks MUST keep using the actual `training_start`. A B/C race on or before
  `training_start` with `k > 0` is one the trained weeks hold: the replan
  passes it, it keeps its race week, easier week and place in the recovery
  count on the virtual timeline, no request check refuses it, and its event
  leaves with the truncated weeks. A fresh retry (`k = 0`) drops it. Negative
  `k` MUST be hard-rejected. `k = 0` MUST be bit-identical to the pre-replan behavior.
- `[x]` **R-PHASE-003**: There MUST be exactly one `RaceEvent` per goal. The
  A-goal event MUST land in the final (`RACE`-mode) week; every event MUST sit in
  a `RACE`-mode week. `event_id` MUST be stable: `race:a_{goal_type}` for the
  A-goal, `race:{priority}_{goal_type}_w{week}` for B/C.

### Phase skeleton (A-goal, step 1)
- `[x]` **R-PHASE-004**: Phase order MUST be monotonic
  `BASE → BUILD → PEAK → TAPER`; no `BASE` may follow `BUILD`/`PEAK`/`TAPER`
  within the A-goal skeleton (weeks where `owning_goal is None`). B/C race
  weeks are exempt.
- `[x]` **R-PHASE-005**: Pre-taper weeks (`total − taper_weeks`) MUST be split
  into `(base, build, peak)` by the goal's phase ratios, with hard floors
  `build ≥ min_build_weeks`, `peak ≥ min_peak_weeks` (from `GoalProfile`); base
  absorbs the remainder and MUST NOT go negative (steal back from build/peak
  above their floors).
- `[x]` **R-PHASE-006**: Phase ratios MUST come from `resolve_phase_ratios`.
  Ironman and Half-Ironman MUST use horizon-aware bands (longer pre-taper →
  more BASE) with an ambition base-shift (`BASIC +0.03`, `COMPETITIVE −0.03`,
  renormalised); all other goals MUST use fixed `GoalProfile.phase_ratios`.
  From `min_horizon_weeks` to `max_recommended_weeks` a longer plan MUST NOT
  have a shorter peak: R-PHASE-011 reads the first peak week, so a shorter peak
  makes a tune-up date flip between accepted and refused across adjacent plan
  lengths. *Known: past its 28-week ceiling the half-Ironman's peak falls from
  5 to 4 weeks at 31-32 weeks.*
  *Previous rule, until 2026-11-04: the Ironman's standard band ran to 32
  pre-taper weeks, so 34- and 35-week plans rounded to a six-week peak between
  five-week neighbours, and a half-Ironman eight weeks out was refused at those
  two lengths only.*
- `[x]` **R-PHASE-007**: Week mode MUST be assigned: last week = `RACE`, taper
  weeks = `TAPER`, all others = `LOAD`. Every A-goal phase MUST be non-empty at
  the post-condition floor (build ≥ 2, peak ≥ 1, taper ≥ 1, base ≥ min(2, rem)).

### Sport splits (steps 1 & 3)
- `[x]` **R-PHASE-008**: Each week's `sport_split` MUST sum to 1.0 (±0.01).
  Triathlon goals MUST use the phase-keyed split from `get_phase_sport_split`;
  running goals MUST fall back to `GoalProfile.default_sport_split`
  (`{RUNNING: 1.0}`).
- `[x]` **R-PHASE-009**: At a B/C race week the split MUST be the normalised
  blend `BLEND_WEIGHT_A·a_split + w_bc·bc_split` (`w_bc = BLEND_WEIGHT_B` 0.8
  for B, `BLEND_WEIGHT_C` 0.3 for C). The A-side MUST use the *underlying*
  A-goal phase the race week replaced, the B/C-side the race week's own phase
  (`TAPER` for B, the underlying phase for C). No other week is blended.
  *Previous rule, until 2026-09-25: the B sharpening PEAK and taper weeks were
  blended too, B recovery weeks were not, and a B race week's A-side read the
  A-goal `TAPER` split.*

### B/C race weeks (step 2)
- `[x]` **R-PHASE-010**: A B or C race MUST cost the A-goal preparation only its
  own week. Splicing MUST replace that week in place (fixed plan length, no
  insertion) with `mode=RACE`, processing goals by `race_date`; there is no
  sharpening block and no separate taper week, so every other week keeps the
  A-goal skeleton's phase, and R-PHASE-014 stamps recovery weeks around it as
  usual. A B race whose `GoalProfile.race_duration` sums past
  `B_RACE_RECOVERY_MIN_HOURS` (3 h: a marathon, a trail 20K and up, a half or
  full Ironman) MUST turn the next week into one `RACE_RECOVERY` week on the
  A goal's phase, menus and split, unless that week is another B/C race week;
  a C race and a shorter B race get none. When that week is the first A-goal
  `PEAK` week of a peak longer than one week it MUST be a `BUILD` week on the
  build's menus and split: the peak starts a week later, and its session
  windows, which count the weeks of the phase, keep their places. A one-week
  peak keeps its `PEAK` label. For a B race the race week itself is the
  mini-taper: its race-week menu (R-PHASE-013) keeps the last days before the
  race light, and the QP's B freshness floor (R-MACRO-016) holds race-day form.
  *Why:* a tune-up is trained through: the week before it is
  a full load week (0.90-1.00 of
  the load weeks before it), only the race week is reduced (0.35-0.74), the
  week after is a full-volume week with fewer quality sessions (0.80-1.16),
  and the B race arrives at a race-day
  TSB / CTL of 0.24-0.29. Friel (*The Triathlete's Training Bible*) gives a
  B race a rest of a few days inside the race week, not a block. The old
  window took four weeks of a 30-week competitive Ironman with a
  half-Ironman B race (two B-goal PEAK weeks, a taper week, the race week)
  and put its biggest week 13 weeks before the A race (9 now).
  The long-B-race recovery week (Kim, 2026-09-25) goes further than usual practice,
  where a long B race is followed by a full-volume week with half the quality sessions.
  A marathon or ultra tune-up raced near full effort costs well over a week
  (`docs/research/trail_running.md` §13.2), so the planner
  gives the long ones an easier week (0.46-0.57 of the load week before, no
  run or ride quality in its first four days by R-SESS-031, the swim set
  kept) and lets the shorter ones train through.
  *Previous rule, until 2026-11-04: no easier week when the next week was the
  first `PEAK` week, so a half-Ironman eight weeks before an Ironman at 29-33
  or 36 weeks was followed on days six and seven by a 280-minute race-pace ride
  and a 154-minute long run. Left on the peak's menus the easier week took the
  305-minute weekend; relabelled, those plans close as a 28-week plan does.*
  *Previous rule, until 2026-09-25: a B window was peak 2 (`PEAK`/`LOAD` on
  the B goal's menus) + taper 1 (`TAPER`/`TAPER`) + race week + recovery
  `min(GoalProfile.recovery_weeks, B_GOAL_MAX_RECOVERY_WEEKS = 2)` weeks of
  `RACE_RECOVERY` on the underlying A-goal phase; a C window was its race week
  alone. The window constants and `GoalProfile.recovery_weeks` went with it.*
- `[x]` **R-PHASE-011**: A B/C race MUST be skipped if its week falls at or beyond
  the first A-goal `PEAK`/`TAPER` week (A-goal specificity is protected).
- `[x]` **R-PHASE-012**: A B/C race week MUST carry the B/C goal as
  `effective_goal` and `owning_goal`; every other week MUST carry the A-goal
  (`owning_goal is None`), a long B race's `RACE_RECOVERY` week included.
  `validate_phase_plan` MUST reject a `RACE_RECOVERY` week that does not
  directly follow a B/C race week. In a maintenance block a C race week MUST
  keep the block's goal as `effective_goal` (a race goal's menu has no
  `MAINTAIN` phase; the block's recovery menu is the lighter week with the
  race in it) and the C goal as `owning_goal`. *Until later on 2026-09-25 `plan_phases`
  emitted no `RACE_RECOVERY` at all.*
  *Previous rule, until 2026-09-25: sharpening, race and recovery weeks all
  kept the B/C goal identity, recovery weeks reverted `phase` to the
  underlying A-goal phase, and `RACE_RECOVERY` directly followed a B race.*
- `[x]` **R-PHASE-013**: A B-race week MUST be relabelled `phase=TAPER`, so it
  draws the B goal's race-week (`taper/race`) menu: a mini-taper of a few days
  before a race the athlete wants to do well in. A C-race week MUST keep its
  underlying phase (e.g. `BUILD/RACE`); the session layer serves it the C
  goal's recovery menu for that phase, a lighter training week with the race
  in it (R-SESS-005). *Previous rule, until 2026-09-25: the same labels, with
  the B race week closing a dedicated sharpening and taper block.*

### Recovery cadence (step 4)
- `[x]` **R-PHASE-014**: Every `recovery_cadence`-th consecutive `LOAD` week MUST
  be stamped `RECOVERY`, where cadence comes solely from
  `AmbitionProfile.recovery_cadence` (4 at every tier; *BASIC was 3 until
  2026-10-06: at 2:1 a basic trail 20K averaged the athlete's recent volume
  over 12 weeks and its long run topped out at 43 % of the race*). All
  non-`LOAD` modes, a B/C race week included, MUST reset the counter, and a
  `LOAD` week directly before a B/C race week MUST stay `LOAD`: the race week
  is that block's deload.
  *Previous rule, until 2026-09-25: B sharpening weeks
  reset the counter too, and the cadence could put a recovery week directly
  before a race week, two light weeks back to back.*
- `[x]` **R-PHASE-015**: Before a taper of `LONG_TAPER_WEEKS` (3) or more, no
  `RECOVERY` MUST be placed within `TAPER_SUPPRESSION_WEEKS` (3) of the first
  A-goal taper week — *except* in `PEAK` phase, where deload is required
  regardless (taper is additional, not replacement, rest). Before a shorter
  taper the window is `SHORT_TAPER_SUPPRESSION_WEEKS` (2) with no `PEAK`
  exemption, and the last recovery week moves up to the last week the window
  allows when the cadence would otherwise run past it into the window, so
  the last load block holds two to four weeks and no recovery week sits
  beside the one taper week. *Previous rule, until 2026-10-06: the
  3-week window and the `PEAK` exemption for every taper; a 20-week trail 20K
  ran recovery, taper, race (4.1, 2.1, 2.9, 1.4 h) and a 12-week one held a
  single peak load week between its last recovery week and the taper.*

### Maintenance branch (raceless blocks)
- `[x]` **R-PHASE-019**: When the A-goal is a maintenance goal
  (`is_maintenance_goal`), the planner MUST emit an all-`MAINTAIN` skeleton with
  `LOAD`/`RECOVERY` modes, no taper and no `RaceEvent` for the block's end,
  then splice its B/C goals exactly as step 2 does (a B race week
  `TAPER`/`RACE` on the B goal's race-week menu, a C race week
  `MAINTAIN`/`RACE` on the block's own recovery menu, one `RACE_RECOVERY`
  week after a long B race), blend the race weeks' splits (step 3) and stamp
  recovery weeks (step 4); every tune-up has its one `RaceEvent` in its
  `RACE` week. A maintenance goal MUST NOT be a B/C goal. `race_date` fixes
  the horizon (end of block) and `prior_training_weeks` truncation MUST
  behave exactly as for race plans. *Until 2026-10-20 B/C goals were
  hard-rejected, so an A race inside a block had no planner path and the
  coach hand-wrote a taper the calendar never saw.*
- `[x]` **R-PHASE-020**: A maintenance block's final week MUST NOT be
  `RECOVERY` — ending the block on its lightest week is a cadence artifact,
  not a design choice. When R-PHASE-014's cadence lands `RECOVERY` on the
  last week (block length is an exact multiple of `recovery_cadence`), that
  week MUST revert to `LOAD` instead; earlier recovery weeks are unaffected.
  Maintenance-only — MUST NOT alter race-goal recovery placement.

### Input validation (`validate_phase_request`)
- `[x]` **R-PHASE-016**: MUST hard-reject: not exactly one A-goal; any race date
  ≤ `training_start`; any B/C date ≥ A date; horizon < `min_horizon_weeks`;
  horizon > `MAX_HORIZON_WEEKS` (52); B/C within `BC_PROXIMITY_TO_A_RACE_WEEKS`
  (2) of A-race or inside the A peak/taper zone; B/C in the first
  `BC_EARLIEST_WEEK` (3) weeks; two B/C in one week; more B/C race weeks than
  `BC_WINDOW_FRACTION` (40%) of pre-taper weeks. The A-race proximity, the
  peak/taper zone and the first-weeks floor protect an A-race build: a
  maintenance block (R-PHASE-019) takes a tune-up in any of its weeks. The plan
  wizard and the coach show a refusal verbatim, so it MUST read as
  athlete-facing text that names a race by its date, never by goal type; a
  race-date refusal gives one reason per tune-up, its week counted from 1 on
  the plan's timeline (trained weeks on it, joined weeks off it), and the dates
  that fit, which MUST be exactly the dates accepted for a race after
  `training_start` (a replan keeps one on or before it, R-PHASE-002b).
  *Previous rule, until
  2026-09-25: the 40% budget summed the multi-week B windows, and overlapping
  windows were rejected; a single-week window overlaps only in a shared
  week.*
- `[x]` **R-PHASE-017**: MUST emit soft warnings (not rejections) for: horizon >
  the goal's `max_recommended_weeks` focused-prep ceiling (fresh plans only —
  no prior or joined weeks — recommending a maintenance block first;
  maintenance goals have no ceiling); B-goal sport ≠ A-goal sport; more
  than 2 secondary goals. The pipeline MUST carry these warnings on
  `PlanResult.warnings` — they were previously computed and dropped. The plan
  preview, the plan's brief and the coach show them verbatim, so they MUST read
  as athlete-facing text that names a race by its date; the horizon warning
  names the start that leaves exactly `max_recommended_weeks`.
  *Previous rule, until 2026-09-25: a B-goal in the first 40% of the plan
  also warned that its sharpening block disrupts the aerobic base; a B race
  no longer has one.*

### Boundaries (MUST NOT)
- `[x]` **R-PHASE-018**: L1 MUST NOT decide session counts, TSS/capacity, day-of-
  week assignment, workout structure, or TID — it owns only the week skeleton,
  race placement, and sport-split targets.

## Map (orientation only)

| File | Owns |
|---|---|
| `planner/phase/phase_planner.py` | 4-step algorithm, `plan_phases`, `validate_phase_request`/`validate_phase_plan`. |
| `planner/phase/phase_tables.py` | `GoalProfile` (ratios, taper, minimums, race load), `resolve_phase_ratios` horizon bands. |
| `planner/phase/phase_config.py` | Scalar policy knobs (blend weights, the long-B-race recovery threshold, taper suppression, validation thresholds). |
| `planner/phase/sport_split_tables.py` | Phase-keyed sport splits for triathlon goals; `get_phase_sport_split`. |
| `planner/foundations/ambition_tables.py` | `recovery_cadence` (the only L0 field L1 consumes). |

## Known violations

None.
