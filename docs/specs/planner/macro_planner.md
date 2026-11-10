# Macro Planner — Requirements

The contract for planner layer 3 (`plan_macro`): a `cvxpy` QP that sizes
each prescribed session within its menu bounds, tracking the menu prescription
while honouring weekly capacity and per-sport Banister dynamics.

Status legend: `[x]` holds · `[!]` violated (see Known violations) · `[~]` partial.

## Requirements

### I/O contract
- `[x]` **R-MACRO-001**: The entry point MUST be `plan_macro(MacroRequest) ->
  MacroBudget`. The request MUST carry `phase_plan` (L1), `sessions`
  (every `PlacedSession` with `min/target/max_hours`), per-sport `ctl_0`/`atl_0`,
  a `CapacityProjection`, and an optional `weekly_hours_cap` (`None` →
  uncapped).
- `[x]` **R-MACRO-002**: The output MUST be one optimised hours value per session:
  `session_hours[slot_id]` plus a `sessions` list of `PlacedSession` copies whose
  `target_hours` is replaced by the QP solution (`min/max_hours` preserved).
- `[x]` **R-MACRO-003**: The solver MUST raise `ValueError` when `problem.status`
  is not `optimal` / `optimal_inaccurate` (infeasible). It MUST NOT return a
  partial or best-effort budget.

### Decision variables
- `[x]` **R-MACRO-004**: There MUST be exactly one continuous nonneg variable per
  session (`s_var[i]`). Per-week, per-sport hour aggregates MUST be affine sums of
  these vars — never independent variables.
- `[x]` **R-MACRO-005**: Per-sport CTL/ATL MUST be auxiliary nonneg variables
  (`ctl_v`, `atl_v`) over `n+1` week boundaries × 3 sports
  (`RUNNING`, `CYCLING`, `SWIMMING`).

### Constraints (hard, MUST hold)
- `[x]` **R-MACRO-006**: Each session var MUST be bounded `lo ≤ s_var[i] ≤ hi`
  where `lo = min_hours or target_hours` and `hi = max_hours or target_hours`
  (a zero bound means "pin to target", i.e. fixed-duration session).
- `[x]` **R-MACRO-007**: Per-sport Banister recursion MUST hold as a linear
  equality at every week: `CTL[k+1] = α·CTL[k] + (1-α)·T`,
  `ATL[k+1] = β·ATL[k] + (1-β)·T`, with `α = CTL_DECAY_WEEKLY`,
  `β = ATL_DECAY_WEEKLY`. `T` is the week-sport TSS impulse:
  `Σ mult_i·s_var[i]` over that week-sport's sessions plus
  `race_hours · tss_mult[k,s]`, evaluated at week start.
- `[x]` **R-MACRO-008**: `mult_i` MUST be the precomputed constant
  `session_tss_per_hour(activity_key, target_hours)` at the session's menu
  target: the session's own activity,
  `achieved_tss(assignment_target_tss(act, hours, None), resolver=None) / hours`,
  so the constraints stay affine and the QP sizes against the load the cards
  will carry. The same `mult_i` prices the session's race-day freshness term
  and is returned as `MacroBudget.session_tss_per_hour[slot_id]`. Race hours
  `r_k` (from `phase_plan.race_events`, `duration_by_sport`) MUST be a fixed
  (non-variable) impulse priced by the week bridge
  `tss_mult[k,s] = tss_per_hour(get_tid_fractions(phase, mode, effective_goal))`:
  a race is an event, not an activity.

- `[x]` **R-MACRO-009**: Initial conditions MUST pin `CTL[0]/ATL[0]` to
  `ctl_0/atl_0` per sport (0.0 default).
- `[x]` **R-MACRO-010**: Total controllable hours per week MUST NOT exceed
  `capacity.weekly_ceiling[k]` (skipped only when the week has no session vars). An
  optional session (R-SESS-037) is outside the QP altogether (`plan_macro` sets it
  aside); a session booked at its card over the ceiling (`over_ceiling`) counts toward
  load, but not toward the ceiling, the per-sport ramp (R-MACRO-011) or smoothing.
- `[x]` **R-MACRO-011**: A loose per-sport ramp `c[k+1] ≤ SPORT_RAMP_LIMIT·c[k] +
  1.0` (`SPORT_RAMP_LIMIT = 1.50`, defined in `macro_planner.py`) MUST bound
  LOAD→LOAD volume growth as a safety net. *The shadow config knob was deleted —
  see V1.*

### Objective (Minimize, tracking-first)
- `[x]` **R-MACRO-012**: The objective MUST be
  `cost_track + cost_smooth + cost_fresh + cost_over_fresh − tiebreaker`,
  minimised, plus `cost_cap` when (and only when) a `weekly_hours_cap` is set
  — an uncapped request MUST compile to the expression above, unchanged.
- `[x]` **R-MACRO-013**: `cost_track` (primary) MUST be the sum of squared
  deviations `(s_var[i] − target_hours)²` over flexible sessions only
  (`hi − lo ≥ 1e-9`); fixed-duration sessions contribute nothing.
- `[x]` **R-MACRO-014**: `cost_smooth` MUST be `LAMBDA_SMOOTH · Σ (c[k_next,s] −
  c[k_prev,s])²` over consecutive LOAD-week pairs **within the same phase**
  (recovery gaps bridged; cross-phase pairs skipped as deliberate ramps).
- `[x]` **R-MACRO-015**: Race-day freshness MUST be a band on the ratio
  `TSB / CTL`, not a floor on TSB. `CTL` and `TSB` are the combined
  (unweighted-sum) PMC over the sports the goal scores
  (`GOAL_CTL_WEIGHTS[goal] > 0`) at the race-day instant
  (`race_day_carryover`, `race_day_session_weights`), both affine in the session hours. `cost_fresh` MUST be
  `LAMBDA_FRESH · Σ deficit²`, where each race contributes a nonneg
  `deficit ≥ (target·CTL − TSB) / 7`; `cost_over_fresh` MUST be
  `LAMBDA_FRESH_CEILING · Σ surplus²`, where a nonneg
  `surplus ≥ (TSB − (target + FRESHNESS_CEILING_MARGIN)·CTL) / 7` penalises
  over-tapering far more softly (racing fatigued is the worse error). Both
  misses are in daily TSB points. Both halves MUST be soft penalties, never
  hard constraints.
- `[x]` **R-MACRO-016**: The freshness `target` (the band's floor, a TSB /
  CTL ratio) MUST come from `RACE_DAY_FRESHNESS_RATIO` by race priority
  (A 0.27, B 0.135, C none), shared by every ambition tier, then scaled by
  `TAPER_FRESHNESS_SCALE[min(taper_weeks, 3)]` counting TAPER/RACE weeks in
  the 4-week window before the race (0.75 for a one-week taper), or the taper
  length `GOAL_FRESHNESS_TAPER_WEEKS` names for the goal: the 10K reads as a
  one-week taper, since it tapers for about a week (before 2026-09-30, its 0.27 floor made the QP pad a fuller race week's
  opening weeks). The ceiling
  sits `FRESHNESS_CEILING_MARGIN` (0.22) above the floor. The A floor is the
  lower edge of the band at the race-day instant
  (`docs/research/endurance_methodology.md`, TSB Zones).
- `[x]` **R-MACRO-017**: `tiebreaker` MUST be `EPS_CTL · Σ w_s·CTL[k_race,s]`
  (subtracted) so equal-tracking solutions prefer higher race-week fitness.
  `EPS_CTL` MUST stay small enough that tracking dominates.
- `[x]` **R-MACRO-021**: The athlete-authored `weekly_hours_cap` MUST enter as
  a soft LINEAR hinge and nothing else: one nonneg slack `over_k ≥ H[k] − cap`
  per week that has session vars — LOAD, RECOVERY, TAPER and RACE alike (an
  exempt taper is where the QP parks the volume it cut from the load weeks;
  measured on the first cut), contributing `cost_cap = LAMBDA_CAP · Σ over_k`.
  It MUST NOT be a hard constraint, and MUST leave the session bounds
  (R-MACRO-006) the only floor — so a cap under them is absorbed by the slack
  rather than raising `QPInfeasibleError`. `LAMBDA_CAP` MUST out-bid the
  freshness term, which is what actually resists a cap (less volume → lower
  race-day CTL → lower TSB → a larger `cost_fresh` deficit).

### Post-solve
- `[x]` **R-MACRO-018**: Extracted hours MUST be clipped to `≥ 0` and rounded to
  5-minute steps **only** when `hours ≥ 1 h 20 min` or the activity family is
  EASY/RECOVERY; rounded values MUST be re-clamped into `[lo, hi]`.

### Boundaries (MUST NOT)
- `[x]` **R-MACRO-019**: The QP MUST NOT assign sessions to calendar days, choose
  activities, or prescribe `target_hours` from scratch — it only resizes within
  menu bounds. Calendar dates are the day_assigner's job (menu-authored days).
- `[x]` **R-MACRO-020**: The QP MUST NOT introduce nonlinearity into the Banister
  state path — activity prices (evaluated once at each session's menu target)
  and TID fractions are frozen coefficients, not variables.

## Map (orientation only)

| File | Owns |
|---|---|
| `planner/macro/macro_planner.py` | QP build/solve (`plan_macro`), bounds, rounding, `skip_macro` bypass. |
| `planner/macro/macro_config.py` | Decay constants, `LAMBDA_*` (incl. `LAMBDA_FRESH_CEILING`, `LAMBDA_CAP`), `FRESHNESS_CEILING_MARGIN`, `EPS_CTL`, solver name. |
| `planner/macro/macro_tables.py` | `GOAL_CTL_WEIGHTS`, `RACE_DAY_FRESHNESS_RATIO`, `TAPER_FRESHNESS_SCALE`. |
| `planner/macro/session_pricing.py` | `session_tss_per_hour`: a session's TSS/h from its own activity. |
| `planner/macro/tss_estimator.py` | `tss_per_hour` hours→TSS bridge (race events, seed fallback). |
| `planner/macro/tid_tables.py` | `get_tid_fractions` (per phase/mode/goal TID). |
| `planner/foundations/types.py` | `MacroRequest` inputs, `AmbitionProfile`, `PlacedSession`. |

## Known violations

- **V1 → R-MACRO-011** — *Resolved.* The dead config knobs `HOURS_RAMP_LIMIT`,
  `DEFAULT_ATL_CAP`, `RACE_SHAKEOUT_HOURS`, and `TAPER_INTER_WEEK_DECAY` were
  deleted from `macro_config.py`. The ramp the QP enforces is the module-local
  `SPORT_RAMP_LIMIT = 1.50` in `macro_planner.py` (the documented live knob),
  and the recovery/taper shaping lives in `capacity.py` (which owns its own
  `RECOVERY_CEILING_FACTOR` / `TAPER_DECAY_FACTOR` copies). Every name
  `macro_config.py` still exports is read by the QP or by `capacity.py`.
  *Follow-up: `capacity.py` defines a separately-dead `RACE_SHAKEOUT_HOURS` (also
  exported, no reader) — left untouched this round, out of scope for the minimal
  cut.*
- **V2 → R-MACRO-007** — *Resolved.* The dead `atl_cap_tss` knob and its
  commented-out QP constraint (`# atl_v[k+1] <= amb.atl_cap_tss`) were removed
  from `AmbitionProfile` and `macro_planner.py`. The module docstring now
  lists only the constraints the QP actually enforces (Banister dynamics,
  session bounds, capacity ceiling, loose per-sport ramp); recovery/taper
  shaping is delegated to the upstream `CapacityProjection` ceiling
  (R-MACRO-010) by design, not claimed as a macro constraint.
- **V3 → R-MACRO-008** — *Resolved.* `tss_estimator.py` now references the live
  TID source `tid_tables.get_tid_fractions`; the stale `block_tables.py`
  mentions are gone.
