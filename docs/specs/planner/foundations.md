# Foundations — Requirements

The shared dependency root of planner: frozen data contracts, the canonical
enums, the ambition policy system, and the per-sport Banister load model
(hours → TSS bridge, capacity ceiling) that every downstream layer reads.

The lower-level, host-free training-science primitives shared with the domain
layer — TSS/load math, zone band-specs, and the workout-step schema — live in
the standalone `endury/training/` kernel (`load.py`, `zones.py`, `workout.py`),
not here; planner imports them from there and never from `endury.domain`
(enforced by the `planner-boundary` architecture check).

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### Contracts
- `[x]` **R-FND-001**: Every inter-layer dataclass MUST be
  `@dataclass(frozen=True, slots=True)` so the forward-only pipeline cannot
  mutate upstream output. Covers `RaceGoal`, `WeekSpec`, `RaceEvent`,
  `PlacedSession`, `CapacityProjection`, `ActivityAssignment`,
  `PhaseRequest`, `PhasePlan`, `AmbitionProfile`, the value objects, and the
  snapshot types.
- `[x]` **R-FND-002**: `types.py` MUST NOT import from any other planner
  layer module — it is the dependency root and stands alone with
  `ambition_tables.py`.
- `[x]` **R-FND-003**: All categorical values MUST be `StrEnum`s with lowercase
  string values, so contracts serialise safely and support exhaustive `match`.

### Enums (canonical vocabulary)
- `[x]` **R-FND-004**: `SportType` MUST be exactly `RUNNING`, `CYCLING`,
  `SWIMMING` — the keying dimension for every per-sport dict.
- `[x]` **R-FND-005**: `GoalType` MUST enumerate the 11 supported races (4 running,
  3 trail and 4 triathlon) plus the 2 raceless maintenance goals (`RUNNING_MAINTENANCE`,
  `TRIATHLON_MAINTENANCE`) and drive phase profiles, session menus, and sport
  splits. `MAINTENANCE_GOALS` / `is_maintenance_goal()` MUST be the single
  membership authority for the maintenance subset.
- `[x]` **R-FND-005b**: A maintenance goal's `race_date` MUST mean the end of
  the block (last day), MUST produce no `RaceEvent`, and MUST NOT appear as a
  B/C goal.
- `[x]` **R-FND-006**: `PhaseType` MUST be `BASE`, `BUILD`, `PEAK`, `TAPER`,
  `MAINTAIN` (maintenance blocks only);
  `WeekMode` MUST be `LOAD`, `RECOVERY`, `TAPER`, `RACE`, `RACE_RECOVERY` and is
  the primary dispatch key for menus, TID, and capacity. `MAINTAIN` weeks MUST
  only carry `LOAD` or `RECOVERY` modes.
- `[x]` **R-FND-007**: `SessionOrigin` MUST contain exactly `ANCHOR` (prescribed
  menu session) and `TEST` (fixed-protocol fitness test). There MUST be no
  `FILL` and no `RACE` origin — races are `RaceEvent`s, and every session is
  prescribed (no fill concept).

### Session contract
- `[x]` **R-FND-008**: `PlacedSession` MUST be single-sport and carry a stable
  `slot_id` assigned at creation (`{sport}:{activity_key}:w{week}:{idx}`),
  enabling end-to-end tracing through `ActivityAssignment`.
- `[x]` **R-FND-009**: `PlacedSession` MUST expose `min_hours`, `target_hours`,
  `max_hours` as the QP sizing bounds; `min == max == target` (the `0` defaults
  collapse to `target_hours`) MUST mean a locked/fixed session.
- `[x]` **R-FND-010**: `PlacedSession.brick_partner` MUST hold the `slot_id` of
  the co-located bike session (or `None`); bricks are two single-sport sessions
  on one day, never a composite session.
- `[x]` **R-FND-011**: A triathlon race MUST be a single `RaceEvent` with
  per-sport entries in `duration_by_sport`, kept separate from `PlacedSession`
  so the race is a day-level fixture the day assigner evicts training away
  from. It MUST provide `leg_id(sport)` for single-sport materialisation.
- `[x]` **R-FND-012**: `WeekSpec.sport_split` MUST sum to 1.0 (±0.01),
  enforced in `__post_init__`.

### Ambition system
- `[x]` **R-FND-013**: `AmbitionLevel` MUST be the 3 tiers `BASIC`, `MODERATE`,
  `COMPETITIVE`, carried on every request contract.
- `[x]` **R-FND-014**: `AMBITION_PROFILES` in `ambition_tables.py` MUST be the
  single source of truth mapping each level to one frozen `AmbitionProfile`;
  layers MUST read profile knobs rather than the `macro_config.py` scalars
  (which are MODERATE-level reference defaults only).
- `[x]` **R-FND-015**: An `AmbitionProfile` MUST carry exactly these knobs:
  `hours_ramp_limit` (LOAD→LOAD growth), `recovery_cadence` (LOAD weeks per
  RECOVERY), `max_weekly_hours_ceiling`, `recovery_load_fraction`. Every knob MUST be read
  by a downstream layer — the profile carries no dead metadata. The progression
  MUST be monotonic in aggressiveness (BASIC ramps slowest / deloads deepest;
  COMPETITIVE ramps fastest / deloads lightest).

### Load model (Banister)
- `[x]` **R-FND-016**: CTL/ATL/TSB MUST be tracked **per sport** as a Banister
  impulse-response state, with TSB = CTL − ATL (freshness). These are
  TSS-dimensioned internal quantities, not user-facing.
- `[x]` **R-FND-017**: Weekly decay MUST use `CTL_DECAY_WEEKLY = exp(-7/42)`
  (42-day fitness constant) and `ATL_DECAY_WEEKLY = exp(-7/7)` (7-day fatigue
  constant) from `macro_config.py`. The EMA update MUST stay affine in hours so
  the QP constraints remain linear.
- `[x]` **R-FND-018**: Training volume MUST be expressed in **hours**. A
  session's TSS MUST be derived from its own activity through
  `session_tss_per_hour(activity_key, hours)`, evaluated once at its menu
  target hours so the coefficient is a pre-solve constant. The constant bridge
  `tss_per_hour(TIDFractions)` = `z_low·42.25 + z_mid·76.56 + z_high·110.25`
  (IF² · 100 at zone midpoints, TID deterministic per (phase, mode, goal))
  remains for race events and as the seed fallback. The CTL/ATL seed from
  recent hours MUST be the hours-weighted mean of the opening week's session
  prices (`initial_load_state`), the bridge when that week has no sessions.


### Capacity
- `[x]` **R-FND-019**: `build_capacity_projection` MUST return a
  `CapacityProjection` giving the per-week hours ceiling (`weekly_ceiling` and
  `weekly_ceiling_by_sport`), seeded from recent weekly hours and shaped by week
  mode (LOAD ramp, RECOVERY/TAPER/RACE/RACE_RECOVERY step-downs), capped by
  `max_weekly_hours_ceiling`. It is a ceiling, not a prescription.

### Athlete-authored constraints
- `[x]` **R-FND-020**: `PlanRequest.constraints` MUST be a frozen
  `PlanningConstraints` whose every field defaults to today's behaviour, so a
  request that omits it produces a bit-identical plan. `weekly_hours_cap`
  (`None` → uncapped) MUST be a SOFT target: it is handed to the macro QP as a
  penalty (R-MACRO-021), never to `build_capacity_projection` as a ceiling and
  never to the session generator, so a cap below the menu's own session floors
  degrades to those floors instead of dropping sessions or failing the solve.

## Map (orientation only)

| File | Owns |
|---|---|
| `planner/foundations/types.py` | Frozen contracts + all canonical enums; `WeekSpec` sport-split validation. |
| `planner/foundations/ambition_tables.py` | `AMBITION_PROFILES` lookup (single source of truth) + `get_ambition_profile`. |
| `planner/foundations/request.py` | Request contracts: `PlanRequest`, `AthleteState`/`SportState` (per-sport state; sport set = keys), `SchedulePreferences`, `SolverOptions`, `PlanningConstraints`, `DEFAULT_START`. |
| `planner/macro/macro_config.py` | Banister decay constants; MODERATE-reference scalar defaults; QP weights. |
| `planner/macro/capacity.py` | `build_capacity_projection` — per-week hours ceiling. |
| `planner/macro/session_pricing.py` | `session_tss_per_hour` — a session's TSS/h from its own activity. |
| `planner/macro/tss_estimator.py` | `tss_per_hour(TIDFractions)` hours→TSS bridge + zone constants (race events, seed fallback). |
| `planner/macro/tid_tables.py` | `get_tid_fractions(phase, mode, goal)` — TID fractions feeding the bridge. |

## Known violations

- **V1 → R-FND-018** — *Resolved.* `tss_estimator.py` now documents the live
  zone cutoffs (`TID_LOW_IF_THRESHOLD = 0.85` / `TID_HIGH_IF_THRESHOLD = 1.00`
  from `types.py`) and references `tid_tables.get_tid_fractions`. The numeric
  per-hour coefficients (42.25 / 76.56 / 110.25) were never affected.
