# Session Generator — Requirements

The contract for turning a phase/mode week skeleton into a complete, sized-but-flexible
session prescription: every training session for every week, looked up from the menu
blueprints and emitted as a `PlacedSession` with `(min, target, max)` hour bounds for the QP.

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### I/O contract
- `[x]` **R-SESS-001**: `generate_session_menu(phase_plan, longest_recent_by_family,
  ambition, capacity, swim_css, weekly_hours_cap)` MUST return a flat `list[PlacedSession]` — one entry per
  prescribed session per week, with no day assigned yet (`day_of_week` is a *hint*, not a
  placement; L4 owns calendar days).
- `[x]` **R-SESS-002**: Every emitted `PlacedSession` MUST carry `(min_hours, target_hours,
  max_hours)` in **hours**, `min_hours ≤ target_hours ≤ max_hours`, where `target_hours` is
  the menu prescription and the band is what the L3 QP optimises within.
- `[x]` **R-SESS-003**: The generator MUST be pure w.r.t. the DB — all athlete state arrives
  via arguments (`longest_recent_by_family`, `swim_css`, `capacity`, `weekly_hours_cap`);
  it performs no I/O.

### Menu lookup
- `[x]` **R-SESS-004**: Each week's sessions MUST come from
  `get_session_menu(effective_goal, phase, mode_key, ambition)`, keyed on
  `(GoalType, PhaseType, mode_key, AmbitionLevel)` where `mode_key ∈
  {"load","recovery","taper","race"}` derived from `WeekMode`.
- `[x]` **R-SESS-005**: `WeekMode` → `mode_key` mapping MUST be: `RACE`→`"race"`
  (falling back to `"recovery"` when the goal has no race menu, i.e. C-races),
  `RECOVERY`/`RACE_RECOVERY`→`"recovery"`, `TAPER`→`"taper"`, else `"load"`.
- `[x]` **R-SESS-006**: **Every** training session MUST be prescribed by the menu. There is
  no fill/anchor split — the menu is the complete prescription, and the QP only *sizes*
  what the menu lists. `SessionOrigin` MUST be `TEST` for fixed-protocol fitness tests
  (`RUN_THRESHOLD_TEST`, `BIKE_FTP_TEST`, `SWIM_CSS_TEST`) and `ANCHOR` for everything else.
  A test reached through a `rotation` is a test that week: the rotated activity, not the
  slot's first one, decides the origin and the fixed-protocol booking below (the trail
  maintenance BASIC climb day rotates into the threshold test; sized as the hill session,
  it booked 51 minutes of the 55-minute protocol).
- `[x]` **R-SESS-007**: When `effective_goal != a_goal` and the A-goal has a
  `MAINTENANCE_MENUS` entry, the generator MUST add maintenance sessions for sports the
  B-race menu does not already cover (the triathlon maintenance block has an entry
  since 2026-10-20, so a running tune-up inside it keeps a swim and a ride).

### Variant selection (within a week)
- `[x]` **R-SESS-008**: Sessions whose `[phase_fraction_start, phase_fraction_end]` window
  excludes the week's phase fraction MUST be filtered out — except `race_sim` specs, which
  bypass frac geometry entirely: they pass the filter only on the run's race-sim week
  (R-SESS-023) and are excluded on every other week. A `load_weeks` spec MUST read its
  window, and its group's midpoint distance (R-SESS-009), at the week's place among its
  phase run's LOAD weeks in a LOAD week: load week i of n reads i/n, so a recovery week
  never moves it; in any other week it reads the phase fraction. The Ironman PEAK weekend
  (the long ride, its brick and the Sunday long run, competitive's Wednesday one) carries
  it, so every plan length holds two big weekends wherever the recovery week falls.
- `[x]` **R-SESS-009**: Within one `slot_group`, exactly one spec MUST be chosen per week.
  A `race_sim` member (present only on the race-sim week) wins its group outright;
  otherwise the winner is the spec whose fraction-window midpoint is nearest the week's
  phase fraction. Midpoint distances within `_MIDPOINT_TIE_EPS = 1e-9` MUST count as tied,
  and ties break in favour of the later `phase_fraction_start` — without the epsilon,
  float noise at exact window boundaries (e.g. phase fraction 3/5 against windows
  (0.2, 0.6)/(0.6, 1.0)) silently defeats the tie rule. Ungrouped specs pass through
  unchanged.
- `[x]` **R-SESS-010**: A spec with a non-empty `rotation` MUST resolve its activity key by
  `rotation[week_offset_in_phase % len(rotation)]`.
- `[x]` **R-SESS-032**: A week's phase fraction MUST be its position in its run of
  contiguous same-(phase, effective goal) weeks, `offset / max(run_length - 1, 1)`, and
  the same offset drives R-SESS-010's rotation. A B/C race week (`owning_goal` set) MUST
  be a one-week run of its own that does not break the A-goal run around it: that run
  spans the race week, so the weeks after a tune-up keep their place in the phase.

- `[x]` **R-SESS-031**: In the week after a B/C race week, a run or bike session whose
  resolved activity is hard physiologically (`hardness.is_hard_physiologically`), or in a
  `RACE_RECOVERY` week hard overall (`hardness.is_hard_overall`, the moderate tempo
  ride of a recovery menu), and whose menu day falls within `POST_RACE_EASY_DAYS` (4)
  after race day MUST become the sport's easy session (`RUN_EASY`, `BIKE_ENDURANCE`)
  inside the slot's bounds. Swims keep their activity, and so do sessions later in the
  week. A run or bike fitness test is eased like any hard session and MUST take the
  place of the hard session of its sport on the same day of the next week when that
  week is a LOAD week outside PEAK and TAPER; otherwise that occasion is skipped
  (R-SESS-041 keeps its own weeks). The card's why says the session was eased
  (`why.EASED_AFTER_RACE_WHY`), since the slot's own why describes the hard session.
  *Why:* the week after a tune-up stays at full volume (0.80-1.16 of the load week
  before) but its first days lose the run and bike quality; swim sets stay. The easier
  week after a long B race (R-PHASE-010) loses its moderate ride too: a recovery menu
  books it to keep some intensity in an ordinary recovery week, which the race has
  just supplied, and it lands two days after a half-distance race, when muscle damage
  is still high (creatine kinase about three times its pre-race level at 48 h,
  Camacho-Cardenosa et al. 2023, n=17). Load weeks after a shorter tune-up keep their moderate sessions,
  since those weeks are trained through. A test two to four days after a race reads the
  race's fatigue (a few percent low, more than the test's own repeatability), and its
  number sets the targets of every later card until the next test.
- `[x]` **R-SESS-042**: A fitness test the A goal's load menu would have drawn in a B/C
  race week or in the `RACE_RECOVERY` week after a long B race (the A-goal run spanning
  the week, R-SESS-032, its `rotation` resolved at that offset, R-SESS-010;
  `_displaced_tests`) MUST take the place of the session of its sport on the same
  weekday in the first LOAD week after that week, carried over
  the recovery, easier and race weeks between; when that week is a PEAK or TAPER week,
  when no LOAD week follows, or when that week has no session of the sport on that day
  (a menu's optional extra does not count), the occasion is skipped. Placed there, the
  test is any test the menu placed: inside `POST_RACE_EASY_DAYS` of the race R-SESS-031
  eases it and owes it to the same day a week later. The race week's own menu does not
  change (R-PHASE-010, R-PHASE-013), and a `cadence_weeks` test keeps R-SESS-041's
  weeks. *Why:* a maintenance block holds its tests in an eight-week rotation
  (R-SESS-B-000), so a tune-up in or just before a test week replaced the test with the
  race week or the easier week and left a 16-week block with one test or none; the
  test sets the numbers of every later card.

### Race-sim week (cross-week selection)
- `[x]` **R-SESS-023**: For each contiguous PEAK phase run with ≥ 1 LOAD week, the
  generator MUST designate exactly one race-sim week (`race_sim_weeks`): the PEAK LOAD
  week whose distance to the race week is nearest `RACE_SIM_TARGET_WEEKS_OUT = 5`
  (ties go to the earlier week), never the final pre-taper LOAD week (the most-shed
  0.80-factor week) when the run has more than one LOAD week; a run with a single LOAD
  week uses that week, so a menu's `race_sim` specs can never silently vanish. The race
  week is the first `RACE`-mode week after the run (falling back to the last plan week).
  All inputs derive from the plan's timeline, `phase_plan.prior_weeks` then
  `phase_plan.weeks` (R-SESS-003 purity holds). Frac-window geometry MUST NOT be used
  for this choice: per-week window selection cannot express
  "exactly one big week" across PEAK geometries (LOAD counts 2-5, recovery position
  varies with the 2:1/3:1 cadence carry-over from BUILD). The race-sim variants MUST
  fire on that week only when every `race_sim` key spec's minimum, at least its
  materializable floor, fits within its share of the week's budget
  (`key × 0.98 × ceiling`, R-SESS-033), and when every `race_sim` spec on a ramp is at
  most its step longer than the least its progression was last booked (R-SESS-013's
  floor, so the solved long session before it is no shorter) or the athlete's recent
  longest, whichever is longer: `REHEARSAL_STEP_FRACTION` (0.25), or the variant's own
  `max_step` (the Ironman BASIC big ride 0.3) (`_holds_race_sim`). A rehearsal that fires books
  its menu minimum. Otherwise the week resolves its slot groups as a routine PEAK LOAD
  week. A week that falls back MUST be reported to the athlete by the
  planning layer with its cause (`endury.planning.generate_plan.rehearsal_skipped_warning`):
  when the long rehearsal's minimum fits its share of the plan's biggest PEAK LOAD week
  and is more than its step past the longest long run before its week, the long run had
  too few weeks to grow (`REHEARSAL_TOO_SOON_WARNING`); otherwise the weekly volume was
  too low. A rehearsal
  is only a rehearsal at its authored length: shrunk to fit, it was a card whose why
  promised a four-hour run.



### Cadence weeks (cross-week selection)
- `[x]` **R-SESS-041**: A `cadence_weeks: N` spec MUST fire on exactly the plan's cadence
  weeks for N (`cadence_weeks`): the first week of the plan's timeline (negative after a
  replan, so it never fires again), then for each mark N, 2N, 3N … weeks on from it, the
  RECOVERY week nearest the mark within `CADENCE_TOLERANCE_WEEKS` (2; ties go to the later
  week), never a PEAK or TAPER week and never a tune-up race's own week (`owning_goal`
  set); a mark with no such week fires nothing. On those weeks the spec wins its slot
  group outright; on every other week it is absent and the group resolves by frac
  geometry. All inputs derive from the plan's timeline, `phase_plan.prior_weeks` then
  `phase_plan.weeks` (R-SESS-003). A replan therefore continues the plan it replaces: the
  same test weeks, and the same phase-run windows and rotation positions
  (`_compute_phase_runs`), not a new opening week; sizing still adapts to the athlete at
  the replan. Frac-window geometry MUST NOT be used for this: a plan week sits at a different phase fraction at
  every horizon (the 3:1 trail 20K's week 8 runs from the last BUILD week at 12 weeks to
  the first at 19), so no window names "week 8" at every horizon without also firing
  four weeks later at some. The fitness tests of a race preparation are the cadence specs
  (a maintenance block tests through its rotations instead, from week 5: R-SESS-B-000):
  every athlete tests in week 1 and every eight weeks, whatever the horizon and volume,
  and never in the peak. The import-time day-layout lints replay every layout with no cadence firing and
  with each interval the menu names firing (`contracts._check_tier_days`).
  (`tests/planner/test_session_generator.py::TestCadenceWeeks`)

### Sizing logic
- `[x]` **R-SESS-011**: Non-swim bounds are minutes; swim bounds are **metres** and MUST be
  converted to the wall clock of the card that swims them, at the athlete `swim_css`
  (else `DEFAULT_SWIM_CSS_SEC_PER_100M = 120`), per R-DU-002 (`_swim_minutes`): a
  continuous swim (long/endurance/recovery/pull/kick) inverts its card exactly (the
  warm-up and cool-down at their own pace `css / IF`, the rest of the distance at the
  activity's `intensity_factor`, and the wall rest after each rep of a split set,
  R-DU-008; `SwimLinearDistanceActivity.minutes_for_meters`); an interval or drill swim
  is probed through its card builder, the shortest card covering the lower bound and
  the longest within the upper one, booked at that card's own clock. A rotating slot
  converts its metres for the swim the week books, not for its first activity. The
  swim floors and ceilings the generator sizes against (R-SESS-039, the activity's
  card floor and ceiling, a swim test's fixed protocol) are probed at the same CSS.
  The same law is the materializer's inverse, so budgeted hours and built workout
  distance round-trip losslessly (R-SESS-B-006), within one card step (100 m).
  (`tests/planner/test_card_honesty.py::test_swims_hold_their_menu_metres`)

- `[x]` **R-SESS-012**: Progressive sessions (`progressive=True`, LOAD weeks only) MUST grow
  from the athlete's current longest by a per-week increment, sharing ramp state across
  variants of one trainable quantity via the `(sport, progression_key)` key — so
  `RUN_LONG` → `RUN_LONG_PROGRESSION` carries a continuous trajectory. The ramp opens at
  the athlete's longest recent session, collapsed to a per-sport maximum across reported
  families; `_PROGRESSIVE_FLOOR` seeds only a sport the athlete reported nothing for.
  Until a progression first reaches its menu minimum, the ramp value is also its lower
  bound, so a measured athlete's first long sessions sit below the menu minimum.

- `[x]` **R-SESS-013**: Week-over-week growth of a progressive session MUST be capped at
  `max(prev × 0.10, sport_step_floor)` and rounded to 5-minute granularity, the rounding
  never carrying it past the cap, nor a swim past the minutes of its authored metres
  (R-SESS-011: half a step is another 100 m on the card, which took the Ironman
  competitive peak long swim to 4,600 m; a maintenance block's rounding too, which took
  the 2,400 m triathlon-maintenance Friday swim to 2,500 m); for progressive sessions `target_hours` (the ramp value)
  MUST also be the QP `max_hours`, so the QP cannot inflate the session to its phase-end
  ceiling. A progressive session of any sport MUST also keep the cap as its lower bound
  all plan long, except a `race_sim` spec (the one race-rehearsal weekend): a menu
  minimum above the cap would otherwise force a jump at a phase change (95 → 130 min
  into a competitive marathon BUILD).
  A progressive session other than the rehearsal MUST also be booked at most one step,
  `max(booked × 0.10, sport step floor)` (8 min running, 12 cycling, 4 swimming), over
  what it was booked in the last load week that held it (`booked_last`): after capacity
  sizing its three bounds are capped there (never under its card's floor,
  R-SESS-030: a card longer than one step books its card), and its minimum then rises to its target as
  far as the week's minimums leave room under its budget and under the athlete's
  weekly-hours cap (the QP books a week over that soft cap at its minimums), so the QP
  cannot book it shorter than the value the next step grows from. The running
  sessions take that room first, then the ride and the swim, so a squeezed triathlon
  week keeps its long run's ramp whole. That value is the emitted minimum after the
  rise. The ramp value
  (`last_duration`, which sizes the next week before this cap and gates the
  rehearsal) is unchanged, so a week squeezed for many weeks books its long run at the
  week's scale of the ramp, not at the scale of an already squeezed booking. The
  rehearsal is exempt: it neither reads nor writes the booking, and the week after it
  grows one step from the week before it. A plan joined at short notice
  (`horizon_join_weeks`) or replanned (`prior_training_weeks`) never books its hidden
  weeks, so its first visible load week opens from the athlete's recent longest as a new
  plan does. A sport's long session is
  one progression through every phase, whatever the phase calls it: the half-Ironman
  BUILD sweet-spot ride shares `him_long_bike`, and the PEAK long ride and long swim of
  the sprint, olympic and half-Ironman menus and the Ironman PEAK rides carry their
  goal's progression. The Ironman race-distance swim (R-SESS-B-006) is a `race_sim`
  variant of the PEAK long swim, so it rides the race-sim week, the one weekend exempt
  from the step.


- `[x]` **R-SESS-014**: When the sum of session minimums exceeds the week's capacity ceiling,
  all minimums MUST be scaled down proportionally (to 0.98× ceiling) to keep the QP feasible
  while preserving the session mix. A week with key sessions is sized by R-SESS-033
  instead.
- `[x]` **R-SESS-030**: Emitted bounds MUST honour each activity's materializable floor —
  the smallest workout its shape can build (rep lattices, fixed test protocols,
  warmup+cooldown scaffolds), probed through the real materialization bridge
  (`_activity_floor_hours`). After R-SESS-014 scaling, a session whose prescription cannot
  be booked at ≥ `FLOOR_DROP_TOLERANCE` (0.85) of its floor MUST be dropped (the card would
  render substantially larger than the plan sized it). Drops MUST re-fit one at a time,
  because each drop frees scale for the rest: offending unkeyed fixed test protocols go
  first and together (a keyed test gives way as a key session, R-SESS-033), then the
  single largest-shortfall session per re-fit (dropping every offender
  against one crowded scale left squeezed BASIC recovery weeks empty and some load weeks
  a lone long run; matrix sprint_triathlon basic −0.012 / moderate −0.002, olympic_triathlon
  basic −0.003, all other cells unmoved). Survivors'
  minimums are lifted toward the floor within the week's remaining headroom, so the QP stays
  feasible by construction
  and every kept session solves at a duration the materializer will honour. In TAPER- and
  RACE-mode weeks the partial-lift compromise is refused: when headroom cannot fund the
  full lifts (lift ratio < 1), the pass MUST drop the largest-lift non-progressive,
  non-test, non-key session outright and re-fit — a taper sheds sessions to protect race-day
  freshness rather than carrying floor-inflated bookings (measured +0.0005 on the
  running_marathon_basic composite via taper_volume_drop, all other matrix cells
  unmoved). A brick run
  MUST drop whenever no cycling session survives on its own authored day (same-day
  co-location is the brick's meaning; R-SESS-015's raise stays for authoring errors only).
  Bounds are equally capped at the activity's materializable **ceiling**
  (`_activity_ceiling_hours` — lattices top out too), so a booking can never exceed what
  the card renders. Fixed test protocols book **exactly** their card duration
  (min=target=max, replacing the authored slot duration) or are dropped — a protocol
  cannot be partially performed, so the partial-lift tolerance does not apply to them.
  Complementarily, the materializer MUST NOT inflate a
  duration-derived target to the activity's declared `tss_min` window
  (`assignment_target_tss` projects with a relaxed lower bound): the declared window is a
  planning prior, only physical floors bind the card. The whole invariant is swept across
  every goal × tier × capacity seed by `tests/planner/test_card_honesty.py` (fast subset
  in the default gate; full grid `slow`, run by `make test-scorecards`).
- `[x]` **R-SESS-033**: A menu Variant MAY carry `key: <share>` (0 < share ≤ 1): a key
  session, and the largest share of a squeezed week it may take. In a week whose session
  minimums exceed its capacity ceiling, each key session MUST book its target, raised to
  at least its activity's materializable floor and capped at `share × budget`; the other
  sessions share the rest of the budget at one common scale. While that scale is below
  `FILLER_MIN_SCALE` (0.5) and more than one non-key session is left, the one that fits
  the week least MUST drop and the week re-fit: the one whose smallest honest booking
  (`FILLER_MIN_SCALE` of its minimum, at least its card floor) is largest, so a 178-min
  back-to-back day goes before a 50-min easy run, and strides that cannot halve go
  before an easy run that can. The last non-key session never drops for the scale; it
  drops only under R-SESS-030, when its booking misses its card. While a non-key session
  is named to drop, R-SESS-030's floor drops take key sessions only: the other sessions'
  scale is not final until the drops are done (the drop loop). A key session whose
  capped booking is below `FLOOR_DROP_TOLERANCE` of its floor drops under R-SESS-030 as
  any session would, and its time goes to the others. So in a squeezed week that holds
  a non-key session, no key session books more than its share of the budget; a squeezed
  week left with key sessions only books each at most its share and stays light. Only
  when the keys' capped bookings alone fill the budget (shares summing to 1 or more)
  does the week scale as R-SESS-014; no trail week reaches that (a rehearsal's 0.67
  and a climb's 0.25 sum to 0.92). In a week that fits, a key session's minimum
  is still raised to its floor, and no share applies. A keyed fixed test (the fitness
  tests, R-SESS-B-000) MUST NOT be dropped first with the unkeyed protocols: it gives
  way as any key session does, sending the week's biggest other session optional, and
  books over the ceiling when nothing else can go (since 2026-10-11: the half-volume
  trail maintenance athlete's 1.5 h opening week keeps its 55-minute test with the easy
  runs optional, where a share large enough to hold it had overfilled the week with the
  long run's and scaled every session alike).
  A menu with no key session MUST size exactly as R-SESS-014 and R-SESS-030 describe. The
  TAPER/RACE lift-ratio shed of R-SESS-030 never picks a key session (`_fit_scale`).
  A card that builds shorter than the session it names is floored by
  `ACTIVITY_FLOOR_MINUTES` (the steady run and the progression run at 30 min; their
  cards build down to 21, a warm-up and cool-down around a minute of work), so a
  squeezed week turns such a run optional whole instead of booking a shell of it
  (trail 20K basic at 8 wk and at 0.75 of the default volume).

  *Why:* proportional scaling cannot protect a session whose floor is near its minimum —
  a 50-min hill card survives only at scale ≥ 0.94 — so every real squeeze dropped the
  climbing first, and the trail menus author basic-tier climbing below its card. The
  share caps are what keep a small week from collapsing into one long run. The trail
  menus key their climbing slots at 0.25, their load and taper long runs at 0.5, the race
  rehearsals at 0.67 — hypotheses anchored in the
  menus' `# intent:` comments, not measurements; the half-Ironman keys its PEAK long
  run at 0.3, above the largest share it took of a PEAK week (0.26) (2026-09-29: half-volume
  joins lost it from 6 of 210 PEAK load weeks; now 0, while 14 other PEAK sessions drop
  as fillers in low-volume weeks and 10 return); `FILLER_MIN_SCALE` is a hypothesis too
  (no source states how short a squeezed easy run may get). Recovery and race-week menus
  carry no key.
- `[x]` **R-SESS-034**: A menu Variant MAY carry `eases: <progression>`: it stands in,
  in an easier week, for that declared progression (the loader rejects an undeclared
  one). Its minimum, target and maximum MUST be at most `EASED_FRACTION` (0.75) of the
  longest that progression has been booked so far (its emitted target after capacity
  sizing, not its ramp value: a squeezed week books its long run under the ramp) —
  before its first week, of the athlete's ramp start (R-SESS-012, the recent longest) —
  rounded down to 5 minutes, but never under its card's floor while that floor is
  shorter than the longest booked (a cap under the card would drop the long run). The
  TAPER/RACE lift-ratio shed of R-SESS-030 never picks an eased session.
  It is a cap, not a target: bounds already below it stand. In a week holding an eased
  session, every other session of that sport that is not key, not eased and whose
  activity is in the easy family MUST be capped the same way (`_under_eased_cap`): the
  week gets lighter instead of re-spending the minutes the long run gave up on its easy
  runs. Quality and key sessions are untouched. *Why:* without it the capacity target
  stayed and the solver filled it from the easy runs' authored maxima, so at low volume
  and on short-notice joins the "long run" card was not the week's longest run (gate
  harness, 1,664 cells: trail easier weeks with an easy run longer than the long run
  29 → 172 with the cap alone, 38 with this rule; the 9 left over the base are within
  10 min: the solver placing a squeezed long run a few minutes under its target, and a
  27-min strides card, which never builds shorter, over a 25-min cap). The session is not
  progressive, so it reads the ramp state and never writes it; the next load week grows
  from the last load week (R-SESS-013). A Variant without `eases` sizes exactly as
  before. After the cap the session is sized like any other: a capped key session books
  its capped target (R-SESS-033), and a capped session under its card floor is lifted to
  the floor when the week has headroom (R-SESS-030; for the 14-min trail long run card
  only a recent longest under ~19 min gets there). One change to R-SESS-030's drop
  order (`_worst_offender`): an eased session is the easier week's long run, so it
  drops last — other offenders go first, and when it is the only offender the week
  drops its shortest other session instead, freeing the scale it needs; it drops only
  when it is alone. Without this the capped long run, now the shortest session of a
  squeezed recovery week, dropped first: the 1 h/week returner (recent longest 35 min)
  lost the long run from 4 of its recovery weeks across the trail sweep (below); with it
  0 of 472 recovery and taper weeks lose theirs.
  The trail menus ease the recovery and taper long runs, the 100K recovery Saturday
  easy run, which is that week's long run, and the 100K taper Sunday easy run, which
  otherwise outruns the capped Saturday; race weeks author no long run. The
  half-Ironman and half-marathon menus ease their taper long run (2026-09-29, V9's
  taper residual).
  *Why:* those slots are not on the ramp, and their bounds (competitive 50K recovery
  90-120, taper 140-165 min) assume an athlete already at 3-4 h. The default competitive
  trail 50K at 12 weeks (recent longest 60 min) ran long runs of 68, 76, 84 then a 90-min
  recovery long run, and a 140-min taper long run after a 135-min peak: the easier weeks
  ran longer than the hard ones. 0.75 is the top of "the other long runs at 50-75 % of
  the maximum" (docs/research/trail_running.md §5.3, §13.5). Measuring the booked value
  matters at low volume: at half the default hours the basic 50K ramp reached 145 min
  while its load long runs were booked at 80-97, and a ramp-based cap left a 91-min taper
  long run. The rounding keeps the cap on the 5-minute grid and absorbs the few minutes
  the QP may place a load long run under its booking; the QP sizing one further below
  can still leave the realised ratio a little above 0.75.
- `[x]` **R-SESS-035**: A `MAINTAIN`-phase week (`ws.phase == PhaseType.MAINTAIN`) MUST NOT
  size its sessions by the phase-fraction interpolation of R-SESS-011's neighbouring
  paragraph, nor by the step cap that follows it: every maintenance menu authors nothing
  progressive (R-SESS-012 does not apply), so there is no phase-length ramp for either
  mechanism to protect. Instead, every session's nominal duration MUST be its authored
  maximum, letting the capacity ceiling (already flat at, or ramping to, the tier's
  maintenance volume per `maintenance_weekly_hours`) and the existing minimum-vs-ceiling
  scale-down (R-SESS-014) size the week: an athlete at or above the tier target lands
  within a few percent of it from week one (every session at its own authored maximum
  unless the ceiling binds first); an athlete below it opens at its own recent volume and
  climbs at the capacity ramp's own rate, with no extra logic here. *Why:* a maintenance
  block holds a target volume rather than building toward a race (the menus' own
  docstrings: "the block holds the tier's maintenance volume instead of ramping"), but
  `_compute_phase_runs` treats the whole block — MAINTAIN is one contiguous phase run
  start to finish — as a single phase-fraction ramp from 0 to 1, so every session opened
  at its menu minimum in week one and only reached its authored band by the block's last
  week. A second, compounding defect: the step cap that follows the interpolation exists
  to bound a *progressive* session's week-over-week growth, but it also fires for any
  non-progressive session authored above its sport's generic floor
  (`_PROGRESSIVE_FLOOR`: 75 min running, 90 cycling, 30 swimming) — the maintenance long
  run (90-110 min competitive running) and long ride (up to 195 min competitive
  triathlon) both clear it. A non-progressive spec never writes `last_duration`, so the
  cap's `prev_dur` fallback recomputes the same generic floor every week, collapsing that
  session to its menu *minimum* for the whole block regardless of the athlete's tier or
  the ceiling's own room.
  *Amended 2026-10-08:* a maintenance menu MAY author a `progression:` on its long run
  (the trail BASIC menu does). That session is sized as any ramp (R-SESS-012, R-SESS-013:
  it opens from the athlete's recent longest, grows by the 10 % step, at least 8 minutes
  running, up to its menu range, then holds there); only the non-progressive sessions of a
  maintenance block open at their authored maximum. *Why:* an athlete whose longest run
  is 51 minutes opened a 60-80 long run at 71, and one at 30 minutes at 48.
  *Amended 2026-10-13:* a week whose long sessions still ramp MAY book below the tier
  volume (or the athlete's own, whichever is lower), down to 0.8 of it, and a load week
  carrying the block's fitness tests down to 0.9, by the tests' shortfall against the
  quality sessions they replace. Nothing fills the gap: the other sessions already sit at
  their authored maximum. *Why:* triathlon MODERATE books one session a day (the long ride
  and its brick share Saturday) and both long sessions ramp, so its week carries no easy
  session to fill around them; the default athlete (8.5 h, longest ride 90, longest run
  60) opens at 0.85 of the tier and holds within 5 % of it from week 6, its test weeks at
  0.91 (maintainer).
- `[x]` **R-SESS-036**: A race week (`mode_key == "race"`) MUST book at most its goal's
  share (`RACE_WEEK_SHARE`) of the biggest load week booked before it, each load week
  counted at its emitted targets up to its sizing budget. The share is the top of each
  goal's band: 0.65 for the 5K, 0.60 for the 10K, 0.55 for the half
  marathon and 0.45 for the marathon (the taper literature,
  `docs/research/endurance_methodology.md` §6, Race Week in Road Running: the shorter
  the race, the larger its race week), 0.40 for triathlon (long course 19-40 %, short
  course 20-40 %), 0.50 for the trail 20K (the top of its own
  band, bridged between the 10K's and the marathon's; *0.40 until 2026-10-07, which
  cut a 3 h/wk athlete's three shakeout runs to 38, 19 and 16 minutes*) and 0.20 for
  the trail 50K and 100K (the aggressive race-week number, under 20 %). Before capacity fitting, every race-week session, counted at its card
  length, shrinks by one common scale, but none under its card nor under half its
  authored length (`FILLER_MIN_SCALE`, the least a squeezed session is booked,
  R-SESS-033): a session the scale would take under that stays there and the others
  share the rest, and a week whose floors alone pass the share keeps every session at
  its floor and stays over it. No session drops for the share, and a race week inside
  it is unchanged. *Why:* each tier's race week was one fixed size, so its share of the
  peak grew as the athlete got smaller (basic olympic 0.46 of the peak week at the
  default volume, 0.71 at half of it), and the taper-and-race shed of R-SESS-030
  dropped the strides or bike opener of a small race week, whose cards are longer than
  the menus author them. Grid of every race goal × tier × 0.5-1.5
  of the default volume × the minimum horizon and 8 weeks more × a fresh plan and a
  join five weeks out (705 plans): 376 race weeks change; basic olympic 0.71 / 0.62 /
  0.46 / 0.38 → 0.54 / 0.38 / 0.38 / 0.38 at 0.5 / 0.75 / 1.0 / 1.25 of the default
  volume, basic marathon 0.54 / 0.52 / 0.39 / 0.34 → 0.51 / 0.39 / 0.39 / 0.34; 71
  race weeks keep strides or an opener they lost before, and 3 low-volume trail 20K
  race weeks lose their fartlek. Over the share are weeks at their floors (a 5K or 10K
  race week of strides and one easy run is 41 minutes) and, by up to 5 minutes, weeks
  whose peak the solver booked a few percent under its targets
  (`tests/planner/test_race_week_share.py`). *Other weeks:* 78 of the 705
  plans also change outside the race week (19 by more than 9 minutes in a week, at most
  29), and they return to their menu targets: with the fixed race week, the QP's
  race-day freshness floor (`LAMBDA_FRESH`, R-MACRO-015) had pushed them off the
  targets to offset a race week too heavy to arrive fresh, and with that term switched
  off the old code books exactly the new weeks (basic marathon at 0.75 volume, 12
  weeks: peak 2.97 → 2.79 h, last taper week 1.83 → 2.09 h; the default basic trail
  20K at 8 weeks: peak 3.02 → 2.77 h, its targets). When in doubt the plan follows its
  menus, not a compensation for the athlete's state.
- `[x]` **R-SESS-037**: No session leaves a week for capacity. Wherever R-SESS-030,
  R-SESS-033 or R-SESS-034 names a session to go because its week cannot fit it (a floor
  drop, a filler that gives way, the TAPER/RACE shed), `_give_way` MUST decide:
  - in a taper or race week a hard or long session leaves, key or not: that week sheds
    load (`_may_be_optional`);
  - a ramp session whose own ramp value is under its card is not ready for it: it stays
    optional (`OPTIONAL_NOT_READY_WHY`), whatever the week's room;
  - any other key or ramp session never goes optional: the week's biggest other session
    (the one whose smallest honest booking is largest) goes in its place, and with none
    left the key session stays booked at its card, over the week's ceiling
    (`PlacedSession.over_ceiling`: it counts toward load, not the ceiling or ramp,
    R-MACRO-010, R-MACRO-011);
  - the session that goes stays in the week at its smallest card, never under its
    authored minimum (min = target = max), optional (`PlacedSession.optional_reason`:
    `OPTIONAL_SHORT_WEEK_WHY`, or `OPTIONAL_TAPER_WHY` for an easy session in a taper
    or race week).
  - *Amended 2026-10-08:* a session that is neither key, ramp nor fixed test never books
    under `SQUEEZE_TOLERANCE` (0.95) of its menu minimum where the week would shrink it
    that far: in a maintenance block's down week at any scale, in any other week once the
    scale is under `FILLER_MIN_SCALE`. The shortest such session stays whole and optional
    (the week's long run and any eased session go last, `_spared`), and the week re-fits.
    *Why:* a half-volume athlete's down week booked a 34-minute long run against a 37
    minimum and 25-minute strides against 27, and a squeezed load week a 15-minute easy run
    against 35. A mild squeeze in a load week still shrinks every session alike.
  A brick run follows its ride: optional with an optional ride, gone with a gone one. An
  optional session is outside the load model: the QP is solved as if the athlete skips
  it (`plan_macro` sets it aside), and it rides along at its card. Its card leads with
  its note (`sessions/why.py`) and its title with "Optional: "
  (`planning/materialize_plan.py`), which the intervals.icu push carries; the weekly
  summary, the coach brief, the projections, the capacity warnings and the scorecard
  (`devtools/planner/_shared/metrics.py`) read the
  hours the weeks book, without it. A stored session keeps the flag
  (`plan_sessions.optional`), so totals read back from a stored plan leave it out too:
  the calendar's planned week and day load and its key session, the compliance counts
  (an optional session is never missed), the coach's planned totals, an extra session's
  budget and a swap's week delta. Still leaving: a fixed-protocol test that cannot be done whole, a brick whose ride is gone and a session after the A
  race (day assigner). *Why:* a vanished session left the athlete no choice and no
  explanation; counting optional sessions in the load (the first version) shrank the
  regular sessions to make room (the maintainer, 2026-09-29). Grid of every
  race goal × tier × 0.5-1.5 of the default volume × the minimum horizon and 8 weeks
  more × a fresh plan and a join five weeks out (705 plans): 391 plans hold optional
  sessions (2,550 in all, none under 10 minutes; 34 of the 72 default plans), most of
  them strides (502), easy runs (293), threshold runs and rides (367), short bricks (178)
  and VO2max runs (161); in a week that holds any they add a median 36 % to its booked
  hours (90th percentile 96 %); 50 carry the not-ready note; 6 key sessions stay booked
  over the ceiling; no session the old plans held is gone. The same plan at 0.3 and 3
  times the default volume holds the same sessions but for hard taper and race-week ones
  (`tests/planner/test_optional_sessions.py`); before, the small one held 7-64 fewer.
- `[x]` **R-SESS-038**: A taper week (`mode_key == "taper"`) MUST book at most
  `TAPER_WEEK_SHARE` (0.75) of the biggest load week booked before it, and no more than
  the taper week before it: the top of the 50-75 % of peak taper weeks hold at
  (`docs/research/triathlon_long_course.md` §7, `trail_running.md`
  §11.2). Volume falls, intensity holds (`_shape_taper_week`, before capacity fitting):
  tempo, threshold and interval sessions keep their bounds and count at their upper
  bound; the easy, recovery and long sessions share the rest as a race week's do
  (R-SESS-036), never under their card nor half their authored length, and a long
  session never under the least the week's longest quality session is booked. The
  shaping only caps: the upper bound drops to the shortened length, the minimum and
  target only as far as that takes them. No easy or long session outgrows the previous
  taper week's longest session of its sport. A taper week whose floors cannot reach the
  share is otherwise left as it was: shortening it would only make room for a session
  the capacity fit drops. *Why:* a one-week taper is booked at its menu minimums, which
  do not scale with the athlete: the basic trail 50K at 0.75 of the default volume ran a
  3.3 h taper after a 3.9 h peak (0.85). Lowering the capacity ceiling's taper factor
  instead (0.85 → 0.75) made 138 threshold, tempo and interval sessions leave their
  tapers, and shortening every session alike cut the quality minutes of 21 tapers by
  over 10 %. Grid (720 plans, 780 taper weeks): 111 tapers change;
  taper weeks over 0.77 of the peak 217 → 177; no taper session leaves; four quality
  sessions book 1-5 % shorter where a kept session scales the week's minimums; 19
  tapers grow by a session the shed used to drop, 2 of them past 0.77; race weeks
  unchanged, 8 other weeks move by at most 7 min through the solver; the trail 50K case
  3.30 → 2.76 h (`tests/planner/test_taper_share.py`). The 153 taper weeks still over
  0.80 are ones whose authored quality sessions and shortest easy and long sessions
  alone pass the share: a menu question.
- `[x]` **R-SESS-039**: A load or taper swim MUST cover at least its tier's floor on the
  card: 1500 m at BASIC, 1800 m at MODERATE, 2000 m at COMPETITIVE
  (`SWIM_FLOOR_METRES` in `menus/patterns.py`). Recovery and race weeks, technique
  drills and the CSS test carry no floor; a rotating slot takes the floor of any
  member that carries one. The menus author no such swim under the floor (the
  `swim_floor` contract, R-SESS-018), and the generator books each floored swim at
  least at the hours its card needs to cover the floor, probed through the card
  builder (an interval swim's rests cover no distance). A squeezed week books the
  floored swims at their minimum the way it books a key session and fits the other
  sessions around them; a swim the week cannot fit at its floor gives way whole, as
  any session under its card does (R-SESS-037). *Why:* a short swim spends most of
  its time on warm-up and cool-down; 1300 of 1962 load and taper swims across the
  triathlon grid (5 goals x 3 tiers x 2 horizons x 0.6/1.0/1.25 of the default
  volume) covered less than the floor, sprint BASIC at 700-1300 m. Same grid after: none under the floor (`tests/planner/test_swim_floor.py`).
  The time comes from the other sports in short weeks: a sprint BASIC athlete at 0.75
  of the default volume swims 0.65 of a band width over the sprint swim share, and
  low-volume sprint and olympic weeks turn a brick run or a quality session optional.
- `[x]` **R-SESS-040**: A session the menu authors `optional: true` MUST be booked at its
  target (the phase-interpolated bound, clamped to its card), optional with
  `OPTIONAL_MENU_WHY`, and MUST NOT enter the week's capacity fit: the rest of the plan is
  the same with or without it. From there it is an R-SESS-037 optional session, outside
  the load model and every total. An optional variant is never a key, ramp or race-sim
  session (`Variant.__post_init__`). The trail menus author easy rides this way, only on
  days their menus leave free of running: a Monday recovery spin in every load and
  recovery week; in recovery weeks a second spin on Saturday (20K, 50K, maintenance) or
  Friday (100K); in load weeks an endurance ride on that day where it holds no run
  (20K and maintenance BASIC, 100K). Spins scale 30-40 min (20K and maintenance BASIC)
  to 50-60 min (100K COMPETITIVE); rides 45-60 min to 60-80 min. *Why:* a ride
  adds aerobic time without the impact that limits how much a runner can run, and an
  optional session costs the athlete who skips it nothing (the maintainer, 2026-10-01). Every trail goal x tier at the default profile and the minimum horizon
  plus four weeks (15 plans): 290 rides of 30-80 min, a median 16 % on top of the week's
  booked hours (90th percentile 50 %, at most 89 % in a BASIC maintenance recovery
  week); the booked sessions match the menus without the rides, their hours within 5
  seconds (`tests/planner/test_optional_extras.py`). The `bike_recovery` card's window
  widened to 8-22 TSS so a 30-min spin renders 30 min.

### Brick pairing
- `[x]` **R-SESS-015**: A spec with `is_brick_pair=True` MUST set `brick_partner` to the
  `slot_id` of the most recent preceding cycling session in the same week; menu slot order
  is therefore load-bearing and a brick with no preceding bike MUST raise.

### Menu data model (what the blueprint/contract layer guarantees)
- `[x]` **R-SESS-016**: A `PlanBlueprint` MUST cover exactly the `(phase, mode_key)`
  combinations in `expected_coverage(goal)` — the 8 race-prep combos
  (BASE/BUILD/PEAK × load/recovery, TAPER × taper/race) for race goals, or the 2
  maintenance combos (MAINTAIN × load/recovery) for maintenance goals; missing or
  extra combos raise `MenuContractError` at import.
- `[x]` **R-SESS-017**: Per-tier presence MUST be the single ambition system: a `Variant`'s
  `bounds` is either a shared `(lo, hi)` tuple (all tiers) or a `{ambition: (lo,hi)}` dict;
  a tier absent from the dict means the Variant does not fire for that athlete. No separate
  scale multiplier, no per-tier menu forks.
- `[x]` **R-SESS-018**: `MenuContract` MUST be validated at **import time** (each `<goal>.py`
  calls `validate_blueprint`; `__init__` calls `validate_all`) — enforcing `must_contain`,
  `must_not_contain`, `min_sport_sessions`, `bricks_per_week`, and `weekly_quality_sessions`
  (Z4+ families) per ambition tier.
- `[x]` **R-SESS-025**: The menus author the calendar (`Slot.day` is mandatory), so the
  day-layout invariants MUST hold at import time on every weekly layout a menu can emit
  (replayed via `menus/resolve.py` across frac breakpoints + midpoints, race-sim on/off,
  per tier): at most 2 sessions per day (`day_capacity`), at most one session per
  (day, sport) (`same_sport_same_day`), no same-sport physiologically-hard sessions on
  adjacent days including the Sun→Mon wrap of the repeating weekly layout
  (`hard_adjacent_days`, rotation-aware, hardness =
  `is_hard_physiologically`), every brick variant shares its day with a cycling session
  (`brick_partner_day`), and race-week menus author no Sunday slot (`race_menu_sunday`
  — the Sunday-race authoring convention; non-Sunday race days are handled at runtime
  by the day assigner's race-day eviction).
  - **Decision: two more layout invariants exist, and they
    deliberately live outside these lints.** Every half-Ironman LOAD/RECOVERY layout
    leaves one weekday empty, and no long-course layout puts a physiologically-hard
    ride and run on one day (a hard swim may share a hard day). Both are held by
    `tests/planner/menus/test_day_layout.py` plus the `rest_days` /
    `cross_sport_hard_day_stacking` scorecard rules, **not** by `_check_tier_days`,
    because both bands are goal-scoped: our Ironman menus stack two hard sports on some
    weeks on purpose, so an import-time ban would condemn them. A lint may only state what is true for *every* goal; these are not.
  - **Decision (2026-10-09): a brick run does not count as the intensive run day.** On
    triathlon goals `run_long_after_hard_run_day` ignores a tempo/threshold/interval/test
    run that is paired with the long ride (`is_brick_pair`), because that run is the
    weekend's own fatigue and the long run the next day is written for it: the ride,
    brick, long run weekend of a triathlon plan. A standalone hard run the
    day before a long run is still a violation.
- `[x]` **R-SESS-024**: Slot names MUST be unique within each `PhaseMenu` (enforced at
  import by `validate_blueprint`). A name is provenance: it flows through
  `SessionSpec.slot_name` onto every placed session, so a finding can name the exact
  slot to edit; a duplicate would make that mapping ambiguous. Reusing a name across
  menus stays legal.
- `[x]` **R-SESS-019**: A `Progression` MUST be the shared ramp identity for one trainable
  quantity: declared once under its goal file's `progressions:` list and referenced by that
  name from a `Variant` (`progression:` or `eases:`, R-SESS-034; the loader rejects an
  undeclared one). Its `name`
  keys the generator's shared last-duration state (R-SESS-012); magnitudes live on the
  referencing Variants' `bounds`, never on the Progression.
- `[x]` **R-SESS-020**: `SessionSpec` MUST enforce the units invariant: swim specs set only
  `min_meters`/`max_meters` (>0), non-swim specs set only `min_minutes`/`max_minutes` (>0).

### Boundaries (MUST NOT)
- `[x]` **R-SESS-021**: The generator MUST NOT compute calendar dates or perform race-day
  eviction — that is L4 (`day_assigner`). Day *layout* (hard/easy alternation, rest-day
  placement, per-day capacity) is authored in the menus and enforced at import time by
  the day-layout lints (R-SESS-025) — no layer re-derives it at runtime.
- `[x]` **R-SESS-022**: The generator MUST NOT run the Banister model or final session
  sizing — it emits bounds only; L3 (`macro_planner`) optimises within them.

## Map (orientation only)

| Layer | File | Owns |
|---|---|---|
| Generator | `planner/sessions/session_generator.py` | `generate_session_menu`; progressive ramp and its eased stand-ins, rotation resolution, race-sim week selection, post-race easing, capacity capping and key sessions, the race week's share of the peak, brick wiring. |
| Adapter | `planner/sessions/menus/__init__.py` | `get_session_menu` — pre-flattened `(goal, ambition, phase, mode)` → `SessionSpec` tuple; `BLUEPRINTS`, `validate_all`. |
| Resolution | `planner/sessions/menus/resolve.py` | `flatten_menu`, `active_specs`, `resolve_slot_groups` — the weekly-layout truth shared by the generator and the day-layout lints. |
| Contracts | `planner/sessions/menus/contracts.py` | `MenuContract` enforcement, day-layout lints (R-SESS-025), `validate_blueprint`, `sport_of`. |
| Types | `planner/sessions/menus/types.py` | `Variant`, `Slot`, `PhaseMenu`, `PlanBlueprint`, `Progression`, `MenuContract`, `Bounds`. |
| Authoring | `planner/sessions/menus/patterns.py` | `session`/`scaled` factories and the swim floor (`swim_floor_metres`, R-SESS-039); editing guide in `menus/AUTHORING.md`. |
| Runtime record | `planner/sessions/menus/session_spec.py` | `SessionSpec` — the post-adapter currency the generator consumes. |
| Blueprints | `planner/sessions/menus/<goal>.yaml` | Per-goal `PlanBlueprint` and its `progressions:` (e.g. `ironman.yaml`), read by `loader.py`. |
| Output type | `planner/foundations/types.py` | `PlacedSession`, `SessionOrigin`, `WeekMode`, `CapacityProjection`. |

## Known violations

V1–V9 are resolved; the entries below record how, because each resolution
changed what the layer *is*, not just what it does.

- **V9 → R-SESS-013 — resolved 2026-09-28 in menu data.** The PEAK long run of the
  5K, 10K, half marathon, sprint, olympic and half-Ironman menus was not progressive:
  it booked its authored length (half marathon moderate 94-102 min, half-Ironman
  basic 95-100) whatever the ramp reached, so a plan whose BUILD long run stayed
  short jumped into PEAK. The default moderate half marathon at 8 weeks ran 63, 71,
  76 then 95 min (+25 %); the basic half-Ironman 80 then 95 at every volume. Those
  six PEAK long runs now carry their goal's `progression:`, as the marathon's does,
  so they grow at most one step from the long run booked the load week before (76 →
  84 and 80 → 88 for those two athletes). Gate grid (1,580 plans): jumps over 15 %
  between consecutive load weeks 111 → 0 in every volume band; no week loses a
  session except below. What it costs: where the ramp has not reached the menu's
  PEAK minimum the athlete runs a shorter PEAK long run than the menu authors
  (default athletes at the minimum horizon: half marathon moderate 95 → 84,
  competitive 110 → 97, half-Ironman basic 95 → 88); where it has, PEAK now climbs
  inside its bounds (half marathon competitive at 16 weeks 110 → 120, half-Ironman
  moderate 105 → 110). Two residuals, both from `run_long_progression`'s 61-min
  card floor on the half-Ironman PEAK and taper long runs: in 14 PEAK load weeks
  of short-notice joins at 0.33-0.5 volume (recent longest 20-30 min) the ramped
  long run is under the card and drops (it was 42-71 min before, two to three
  times the athlete's longest); and 15 half-Ironman taper long runs (65-min floor)
  now outrun a shorter PEAK long run, by up to 19 min. Eight low-volume half
  marathon tapers outrun their PEAK long run too, each by at most one step.
  *2026-09-29:* the card floor is 31 min, not 61 (the card renders honestly from
  31 min up); the 65-min taper minimum was authored on the old figure. Both taper
  long runs now ease their goal's long-run progression (R-SESS-034), so a taper
  long run is at most 0.75 of the longest long run booked, never under its card
  while the card is shorter than that, and the taper shed no longer drops it: in
  the measured grid (half-Ironman and half marathon × tier × 0.5-1.5 of the default
  volume × the minimum horizon and 8 weeks more × fresh or joined five weeks out,
  120 plans) taper long runs over the plan's longest 14 of 120 taper weeks → 0; no
  fresh plan at the default volume changes; 15 taper sessions the shed had dropped
  come back and 6 leave, all in joins at half the default volume (the competitive
  half-Ironman there books no long run in any week, so its taper has none to ease).
  The PEAK weeks that drop their long run (6 of 210, all half-Ironman joins at half
  the default volume) are a squeeze, not the card: the week cannot book the long run
  at 0.85 of its 31-min card at its scale (R-SESS-030), and stay open.

- **V8 → the maintenance goals — resolved 2026-09-28 by R-SESS-035.** A maintenance
  block's load weeks opened at the menu's minimum and only reached its authored band by
  the block's last week, undershooting an at-or-above-target athlete's own tier volume
  the whole way there (running competitive at 6.0 h: 5.1, 5.2, 5.3 h ... reaching only
  5.9 h by the final week of a 12-week block, never 6.0 h); recovery weeks fell as low as
  0.25-0.38 of the load week before them against the tier's 0.50-0.65
  `recovery_load_fraction`. Two compounding causes, both scoped to
  `ws.phase == PhaseType.MAINTAIN`: the phase-fraction interpolation ramped every
  session from its menu minimum to maximum across the *whole* block (MAINTAIN is one
  contiguous phase run, and nothing in a maintenance menu is progressive), and the
  generic per-sport step cap — built to bound a progressive session's week-over-week
  growth — additionally collapsed any big *non*-progressive session (the maintenance
  long run/ride, authored above the cap's generic per-sport floor) to its menu minimum
  every week, forever, since a non-progressive spec never records the `last_duration`
  the cap needs to grow from. Fixed by R-SESS-035: both mechanisms are skipped for
  `MAINTAIN` weeks, so every session's nominal duration is its authored maximum and the
  (already-correct) capacity ceiling does the rest. The menus' own recovery-week bounds
  were a second, independent gap — authored with a single `{scaled: [lo, hi]}` factor
  derived from a COMPETITIVE literal, so a recovery week's volume did not grow with the
  tier's own (bigger) load week the way `recovery_load_fraction` intends — closed by
  widening `maintain/recovery` bounds per tier in the three maintenance menus (a
  cut-back long run added to running/trail MODERATE and COMPETITIVE, at most three
  quarters of the load week's long run; triathlon BASIC's
  bike/swim retests dropped, since BASIC gets no bike/swim quality session in
  `maintain/load` either, freeing its small recovery budget for the sessions it does
  keep). After both fixes every measured goal × tier × block-length × athlete-volume
  cell (`tests/planner/test_maintenance.py::TestMaintenanceBlockHoldsFromWeekOne`) lands
  a load week within 5 % of the tier target from week one (at or above it) or at the
  athlete's own volume, one ramp step at a time (below it), and a recovery week within
  roughly its tier's recovery fraction, never below half — except a recovery week whose
  fixed test cannot fit a still-ramping athlete's small early-block ceiling and is
  dropped outright rather than partially run (R-SESS-030); that carve-out is asserted
  explicitly, not silently absorbed.

- **V7 → R-SESS-030 / R-SESS-023 — resolved 2026-09-28 by key sessions (R-SESS-033).**
  Capacity sizing dropped the climbing sessions of a squeezed trail week: R-SESS-014
  scaled every minimum alike, and R-SESS-030 then dropped whatever it could not book at
  0.85 of its materializable floor — the climbing cards have the highest floors
  (warm-up, reps, cool-down), so they went first.
  *Case:* a real athlete asked for a competitive trail 50K (37 km, 1,300 m) five weeks
  out. The plan joins week 8 of 12 (`prior_training_weeks=7`) and is sized from
  3.5 h/week of recent running; the athlete also rides and swims, which does not count.
  Weeks came out at 4.0 / 2.6 / 3.3 / 2.8 / 1.8 h with **no hill repeats, vert hike or
  downhill session in any week**, and a 100-min race-sim "rehearsal" whose why promised
  four hours. `must_contain` is a menu-level contract (R-SESS-018), not a per-week
  promise: the race-sim week's downhill-to-uphill swap and the 100K/100M back-to-back
  rotation are authored absences. The per-week promise is R-SESS-033: key sessions keep
  their size and fit their share of the week.
  *Now (minutes, measured through `generate_plan` with the lone-peak-week rule R-PP-007
  and R-SESS-033's drop order):* at 3.53 h the build week is vert 63, tempo 32, easy
  26, long 116 (was easy 36, tempo 33, easy 26, easy 40, long 105); the peak week hills
  55, tempo 35, easy 26, long 116 with the routine long run in place of the rehearsal
  (was easy 26, tempo 24, easy 20, easy 30, long 100); the taper vert 49, tempo 35,
  easy 30, long 85 (was tempo 31, easy 25, easy 25, long 86). At 6.0 h the peak week
  holds the rehearsal at its full 250 min (hills 55, easy 40, tempo 34, easy 25, long
  250). Across the trail goals × tier × {default, returner at a third of its hours} ×
  {full, short-notice} sweep, load and taper weeks without climbing fell 210 → 114 of
  414 (BUILD/PEAK load and taper 172 → 86 of 340; default profiles 30 → 9, of which 5
  are the 8-week basic 20K and 4 base weeks of the 100M's authored climb/tempo
  rotation). The race rehearsal fires in 4 of 48 cases at its full length (was 48, most
  of them short): the 24 returners skip it, and so do 20 of the 24 default profiles,
  whose long run starts at 60 min and has not reached the rehearsal's length by the
  minimum horizon (R-SESS-023); the 8-week basic 20K cannot fit its hill card's share
  either. Only
  `trail_20k_basic` and `trail_returner_20k` moved among the fingerprint scenarios; the
  scorecard matrix (no trail cells) did not move. *Tests:*
  `tests/planner/test_menu_contract_survives_sizing.py` (the athlete and the
  default-profile sweep), `TestKeySessions`, `TestSqueezedTrailWeeks` and
  `TestRaceSimFitsItsShare` in `tests/planner/test_session_generator.py`.
  *Residual, by design:* a week under ~2.9 h cannot give a 50-min hill card its quarter
  (~2.5 h for the 44-min vert hike), so it keeps its easy runs instead — the 8-week
  basic 20K and the returners. The athlete's lone PEAK load week no longer falls below
  the build week before it (R-PP-007).

- **V6 → R-SESS-012 — resolved.** The seed was written at
  `(sport, family.value)` and read at `(sport, progression_key)` — no key ever matched,
  so no seed was ever read and a 45-min and a 150-min athlete got bit-identical plans.
  Fixed by collapsing the seed to a per-sport maximum **floored at**
  `_PROGRESSIVE_FLOOR`: the floor was re-derived as the menu-authored *minimum viable
  ramp start* that bounds the seed from below, not a pure no-data fallback the seed
  replaces. Rationale: the floored fix keeps every default profile (run 60 / bike 90 / swim 30,
  all at-or-below floor) and every matrix cell bit-identical. The seed feeds the **ramp only**: Gate-2's step cap keeps its
  floor-based fallback for non-progressive specs, because seeding it too released a
  latent clamp (non-progressive slots step-capped near `floor × 1.1`) only in replans,
  moving fixed-bound recovery weeks ~+0.9 h against their own original plan. What still
  moves are the above-floor bike/swim seeds on the progressive ramps. The replan replay also improves
  materially: the pre-fix suffix carried a −1.8 h/wk BUILD drag (progressive sessions
  re-ramping from the floor at the seam — invisible to `replan_seam_convergence`, which
  only samples PEAK), now decayed to zero by mid-BUILD with recovery/taper/race weeks
  bit-stable. The contract is pinned by `TestLongestRecentSeedsTheRamp`;
  `TestLongestRecentSeedIsInert` (which asserted the defect) was deleted with the old
  text of this entry.
  *Reversed 2026-09-25 (R-SESS-012): the floor no longer bounds a measured seed. A
  menu's start assumes an athlete who already does those sessions; Endury knows the
  athlete's longest, and opening at twice it is the
  injury spike. Default profiles now move (24 of 28 scenarios).*

- **V1–V3 — resolved by deletion.** Dead `Progression.bands`/step-cap metadata and the
  unenforced `MenuContract.max_z6_minutes`: the runtime was already the source of truth,
  and the metadata had drifted from the live bounds. R-SESS-019 was rewritten accordingly.
- **V4 → R-SESS-009/017 — resolved by re-documentation.** The doc, not the
  engine, was wrong. R-SESS-008/009/010 above now describe the model that actually ships
  and has carried the scorecard for months; the aspirational one is recorded here only so
  nobody re-derives it from the field names:
  - **`Slot` = one weekday, one prescription.** 445 of 446 authored slots are
    single-Variant. `flatten_menu` emits one `SessionSpec` per (Slot, Variant) that fires
    for the tier; per-tier presence is `bounds` (R-SESS-017), never a separate menu fork.
  - **`slot_group` is the mutual-exclusion picker** (93 variants carry one): specs sharing
    a group name compete and exactly one wins the week, by nearest frac-window midpoint
    with the race-sim override and epsilon tie-break of R-SESS-009.
  - **`frac` is a pass/filter window** (88 variants narrow it), not a selector: it decides
    whether a spec is *eligible* this week; `slot_group` decides which eligible spec fires.
  - **`Variant.rotation`** (31 variants) cycles the emitted `activity_key` by phase-run
    week offset (R-SESS-010) — a within-spec key swap, not a selection mechanism.
  - *The original frac-window design was never realised*: several Variants per Slot with
    non-overlapping `frac` windows selecting directly, no `slot_group`, no `rotation`.
    Nor was its corollary "two Slots on the same Weekday per tier" — `ironman.py` emits
    three disjoint per-tier slot tuples gated by single-tier `bounds` dicts
    (`_slot(tier, ...)`) rather than merged multi-tier Variants, which is why
    `min_sport_sessions` is set on 1 of 68 contracts (across concatenated per-tier tuples
    it would over/under-count). `slot_group` and `rotation` are therefore the model, not
    "compat fields" awaiting removal.
- **V5 → R-SESS-002 — resolved by deletion.** `recovery_scale` was plumbed
  Variant→Slot→`SessionSpec` and never read by `session_generator.py`; no menu ever
  authored a non-default value, so the whole path was deleted end to end (including the
  `menu_diff` fingerprint field). Recovery weeks are smaller because `mode_key="recovery"`
  selects a separate menu with its own authored bounds — the generator applies no
  recovery/taper down-scaling of its own, by design.
