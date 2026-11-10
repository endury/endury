# Activity Library — Requirements

The contract for turning an abstract session (an `ActivityKey` + a target TSS)
into a concrete, athlete-facing `StructuredWorkout` with explicit steps,
intensity targets, and a derivable training-intensity distribution.

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### Registry
- `[x]` **R-ACT-001**: Every activity MUST be registered by a unique
  `ActivityKey`. Registering a duplicate key MUST raise. Lookup of an unknown
  key MUST raise (never silently return a default).
- `[x]` **R-ACT-002**: The registry MUST be queryable by semantic criteria
  (sport, family, stimulus tags, phase, goal, recovery/taper safety) so the
  selector can pick without hard-coding keys.
- `[x]` **R-ACT-003**: `build_catalog_enum` MUST expose the registered keys as
  the runtime schema's allowed values — the registry is the single catalog
  source of truth for what the planner may materialize.

### Capability metadata
- `[x]` **R-ACT-004**: Each activity MUST declare a capability profile (sport,
  family, structure type, stimulus tags, goal/phase suitability, recovery/taper
  flags, brick/long flags, `earliest_phase_week`) and a hardness descriptor.
- `[x]` **R-ACT-005**: A profile's `sport` and `family` MUST match the
  activity's own — construction MUST reject a mismatch. Stimulus tags MUST be
  members of the controlled `ActivityStimulusTag` vocabulary.
- `[x]` **R-ACT-006**: Hardness MUST be a fixed descriptor (easy / moderate /
  hard), not re-derived per session — it carries the hard/easy-alternation
  signal the menu day-layout lints enforce (R-SESS-025).

### Intensity → zone mapping
- `[x]` **R-ACT-007**: Mapping an intensity factor (IF) to a zone number MUST
  use threshold tables **derived** from the canonical `*_BAND_SPECS` in
  `endury/training/zones.py` (single source of truth — enforced by the
  `zone-spec-single-home` architecture rule and pinned by
  `tests/analytics/test_zone_band_specs.py`).
  The activity library MUST NOT define its own band boundaries.
  Since 2026-10-06 the zone *number* the athlete reads beside a target is
  theirs: when the resolver carries `athlete_zones` (the zone sets
  intervals.icu holds), a pace, power, swim-pace or HR band is numbered by
  the athlete's zone that holds it whole, and carries no number when it
  crosses their zones (since 2026-10-09; a pace, power or swim-pace band
  took the zone at its centre before, which filed Endury's aerobic swim
  under an athlete's zone 3 by half a second and had the coach re-prescribe
  from the number). The band specs still prescribe the step, so its IF,
  pace, watts, bpm range, metres, minutes and load do not change; without
  the athlete's zones the band spec's zone stands in.
- `[x]` **R-ACT-008**: Each band's upper bound MUST be the spec's `high_factor`,
  and `_if_to_zone` MUST clamp an IF above the top band to the top zone (never
  error), mirroring the domain-side above-top clamping behavior.
- `[x]` **R-ACT-009**: Each sport's TID-band cut points (`TID_CUTS`, LT1 /
  LT2 on its own intensity factor) MUST derive from the band specs, not be
  re-declared as standalone constants (`planner/foundations/types.py`): a run
  reads the heart-rate table's zone 1 and zone 4 tops (0.85 / 1.00 of
  threshold speed), a ride power's zone 2 top and FTP (0.75 / 1.00), a swim
  the swim table's zone 2 and zone 4 tops (0.90 / 1.00 of CSS speed). A card
  step's band (`FlatStep.zone_band`, what the coach's workout tools report per
  step) MUST follow the same split, zones 1-2 low, 3-4 middle, 5 and up high
  in every metric (`activity/steps.py`): exact for swim pace and for power's
  LT1; a run at 0.85-0.877 of threshold speed (inside Friel's pace zone 2)
  reads low on the card and middle in `tid_fractions`, and a ride at
  IF 1.00-1.05 (inside power zone 4) middle and high.

- `[x]` **R-ACT-028**: Each intensity question has one reading, at one home:
  - easy / middle / hard time (polarisation, quality minutes): the planner's
    `tid_fractions` on the R-ACT-009 cut points;
  - a hard day or hard session: the rulebook's (`bench/rulebook.yaml`, zone 4
    and up on the card);
  - the session sheet's "hard minutes": zone 3 and up in the athlete's own
    zones, beside intervals.icu's time in those zones (`hardMinutes` in
    `web/data/adapters.js`);
  - the zone number on a card: the athlete's own zone, else the band spec's
    (R-ACT-007).

  Each metric has its own zone table, the rungs of one ladder: power
  Coggan's, heart rate Friel's LTHR zones, run pace Friel's threshold-pace
  zones as intervals.icu ships them by default (77.5 / 87.7 / 94.3 / 100 /
  103.4 / 111.5 % of threshold speed), swim pace Endury's own (the
  run-derived defaults would read an easy aerobic swim at 88 % of CSS as
  zone 3). A run step's heart-rate zone is the zone of its pace rung, the
  heart-rate table's top zone at most (`_hr_zone`), so pace and heart rate
  on one run step name one zone; a ride keeps the heart-rate table's zone,
  raised to its power zone from power zone 3 up.

### Workout assembly
- `[x]` **R-ACT-010**: Every materialized non-rest workout MUST render an
  explicit warmup, a main block, and a cooldown — a long endurance session MUST
  NOT collapse to one undifferentiated interval.
- `[x]` **R-ACT-011**: Repeated sets (intervals, drill/swim alternations,
  strides) MUST be emitted as a `repeat` block with an `iterations` count, not
  flattened into N copied steps.
- `[x]` **R-ACT-012**: A target at or below `REST_TSS_THRESHOLD` MUST
  materialize as a zero-load rest workout.
- `[x]` **R-ACT-013**: Every work/recovery step MUST carry an athlete-facing
  description, and the wording MUST agree with the displayed zone band (the
  cue is derived from the band the IF lands in, not chosen independently).

### Targets
- `[x]` **R-ACT-014**: Each step MUST carry a primary target plus a secondary
  (always an HR-zone or RPE backstop), ordered by the activity's
  `TargetPreference`. Pace/power targets MUST only appear when the resolver
  supplies the matching threshold; absent thresholds MUST degrade to HR + RPE,
  never error.
- `[x]` **R-ACT-015**: Concrete pace/power center values MUST be bracketed by a
  `±TARGET_BAND_FRACTION` band, and both the zone-number target and the
  metric-band target MUST stay mutually consistent.
- `[x]` **R-ACT-023**: The easy main steps (HR Zone 1 by intensity factor) of
  the long sessions — the six long-run activities and `BIKE_LONG` — MUST
  carry the explicit `LONG_EASY_HR_BAND` (75-85 % LTHR) whenever the
  athlete's LTHR is known. HR Zone 1 spans 0-85 % LTHR, so without it the
  main set targets the same band as its own warm-up and cool-down. Warm-up,
  cool-down, faster blocks and `intensity_factor` (hence load) MUST NOT
  change.
- `[x]` **R-ACT-025**: A step defined by its pace leads with pace even in a
  session that is otherwise heart-rate led: `RUN_LONG_PROGRESSION`'s blocks above
  HR Zone 1 and the main step of the HIM and IM brick tempo runs carry
  `work_target_preference` (pace first, HR secondary). Their warm-up,
  cool-down and easy block keep the activity's heart-rate lead, and without a
  threshold pace the whole session stays heart-rate led (R-ACT-014).

### TID bridge
- `[x]` **R-ACT-016**: Every activity MUST expose `tid_fractions` returning
  duration-weighted `(z_low, z_mid, z_high)` summing to 1.0, so the TSS bridge
  can size load. Stationary wall-rest in swim MUST be excluded from TID time.
- `[x]` **R-ACT-017**: `achieved_tss`, `duration_minutes_exact`, and
  `tid_fractions` MUST be computed from the *same* solved pattern that `build`
  renders — the four outputs MUST describe one workout.

### Duration-first sizing
- `[x]` **R-ACT-022**: Every shape except the fixed test protocols MUST
  implement `tss_from_duration` so cards can be sized duration-first:
  continuous shapes invert the duration exactly; flexible-rep shapes return
  the rep-lattice TSS whose re-solved duration lands closest to the request
  (card tracks the solved duration to within one rep's wall-clock,
  R-SESS-B-008). Fixed protocols MUST return `None` — the protocol never
  resizes with solved hours. Catalog `[tss_min, tss_max]` windows MUST span
  the authored menu envelope so `project_target_tss` never resizes the
  derived TSS (enforced by `tests/planner/test_menu_envelope.py`).

### Rotating sets
- `[x]` **R-ACT-024**: A session type that recurs in a plan MUST rotate
  through its sets: the n-th occurrence of an activity (counted per activity
  in date order, `set_occurrences`) is built from `with_set(activity, n)` —
  the card itself first, then each alternative in `SET_SCHEMES` in turn.
  An alternative MUST override only rep counts, rep lengths and recoveries
  of its card, or a progression run's number of steps (never intensities);
  a continuous card's alternative MAY name an interval `shape`, built with
  the card's key, family and hardness at the card's intensities (the tempo
  ride's and tempo runs' blocks). Every alternative MUST
  hold the session within 8 min of its planned length and within 15 % of
  the first set's load per minute over the card's whole range, and reach
  down to within 2 min of the first set's shortest card
  (`tests/planner/test_activity_sets.py`). Only
  a plan's materialized cards rotate; the planner sizes every session on
  the first set, so plan hours never depend on the rotation.

### Sport specifics
- `[x]` **R-ACT-018**: Swim MUST be prescribed in METERS in whole 50 m
  lengths (`SWIM_STEP_METERS`), so every card swims in a 25 m or a 50 m pool, with total session distance rounded to a
  whole 100 m; the cooldown MUST absorb the rounding slop.
- `[x]` **R-ACT-019**: Run MUST default to pace and/or running-power targets
  (HR/RPE backstop); bike MUST default to power targets. Cadence targets MUST
  attach only when cadence bounds are configured.

### Boundaries (MUST NOT)
- `[x]` **R-ACT-020**: The activity library MUST NOT estimate athlete
  thresholds (LTHR/FTP/CSS/pace) — it takes them from the resolver. Without an
  athlete CSS every swim shape falls back to `DEFAULT_SWIM_CSS_SEC_PER_100M`
  (120 s/100 m, `activity/config.py`), the same CSS the session generator
  budgets menu metres at (R-SESS-011), so a card shows the metres the plan
  sized.
- `[x]` **R-ACT-026**: A swim's `tss_min` / `tss_max` are authored at
  `DEFAULT_SWIM_CSS_SEC_PER_100M` and MUST scale with the athlete's CSS when a
  card is built (`_swim_window` in `activity/shapes/aquatic.py`): the same
  metres carry load in proportion to CSS pace, so the window bounds a card's
  metres, never its hours, and a 2:30 swimmer's card swims the distance a
  1:40 swimmer's does.
- `[x]` **R-ACT-027**: The drill swim (`SwimDrillSwimActivity`) MUST hold no
  more than its `rep_count_max` drill reps (ten 50 m drills, as much focused
  drilling as one set holds); a longer booking swims the rest as an easy
  whole-stroke block after the set, in whole 100 m up to
  `aerobic_max_meters` (1200 m, so the card reaches the longest drill booking
  in any menu, 2400 m).
- `[x]` **R-ACT-021**: The library MUST NOT choose which activity runs on which
  day, nor size weekly load — that is the day-assigner's and QP's job.

## Map (orientation only)

| File | Owns |
|---|---|
| `activity/registry.py` | `ActivityKey`→definition registry, semantic `query_activities`, catalog enum. |
| `activity/capabilities.py` | `ActivityCapabilityProfile`, `ActivityStimulusTag` vocabulary. |
| `activity/config.py` | IF→zone tables (derived from `endury.training.zones`), `REST_TSS_THRESHOLD`, `TARGET_BAND_FRACTION`, hardness/pool constants, `DEFAULT_SWIM_CSS_SEC_PER_100M`, `RUNNING_THRESHOLD_TSS_PER_HOUR` re-export. |
| `activity/shapes/base.py` | `ActivityDefinition` base, `_if_to_zone`, target assembly, TID helpers, zone-band cues. |
| `activity/shapes/land.py` | Run/bike step classes (linear, fixed-block, progressive, interval-piecewise, interval sets). |
| `activity/shapes/aquatic.py` | Swim step classes (linear-distance, CSS-interval, drill, CSS-test). |
| `activity/catalog/run.py` · `catalog/bike.py` · `catalog/swim.py` | Per-sport activity registrations (pure data, no host coupling). |
| `activity/sets.py` | `SET_SCHEMES` (alternative sets per card), `with_set`, `set_occurrences`. |

The activity library splits into **`shapes/`** (workout-materialization classes —
the only part that reads the host-free training kernel `endury.training`) and
**`catalog/`** (the concrete per-sport registrations, pure data). `catalog`
imports `shapes`; never the reverse. No part of `planner` imports
`endury.domain` (enforced by the `planner-boundary` architecture check).

## Known violations

None.

- **V1 → R-ACT-009 — resolved by derivation.** `TID_LOW_IF_THRESHOLD`
  and `TID_HIGH_IF_THRESHOLD` were hand-declared as `0.85` / `1.00` in
  `planner/foundations/types.py` — a second, undisclosed copy of the HR Z1→Z2 and
  Z4→Z5 `high_factor` boundaries that nothing held in step. They are now read off
  `HR_BAND_SPECS` at import (`endury.planner` may import the host-free kernel
  `endury.training`; only `endury.domain` is barred by `planner-boundary`), so a
  move in the canonical specs carries the TID model with it. Values unchanged.
