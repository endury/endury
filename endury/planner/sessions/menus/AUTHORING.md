# Menu authoring guide

How to edit the session menus safely — written for both coaches and AI
agents. The menus are the plan: every training session an athlete sees is
prescribed by a slot in this package. The wider contributor path (the
scorecard's rules and bands, the gates, pull requests) is in the
repository's `CONTRIBUTING.md`.

## The model (read this, then you know the layer)

- **`PlanBlueprint`** (one per goal, `<GoalType.value>.yaml`, e.g.
  `running_5k.yaml`) maps each of the 8 `(phase, mode_key)` combos —
  BASE/BUILD/PEAK × load/recovery, TAPER × taper/race — to a
  **`PhaseMenu`**: the weekly template for that mode. Maintenance goals
  have two: MAINTAIN × load/recovery.
- A **`Slot`** is one position in the week: `name` (provenance, unique
  within its menu), `day` (mandatory — the authored weekday IS the
  placement), and variants. Recovery weeks are *not* scaled down from
  the load menu — the `recovery` menu is a separate menu with its own
  authored bounds.
- A **`Variant`** is one concrete prescription: activity, bounds, and
  selection mechanics (below). Almost every slot is single-Variant.
- **Bounds** carry the ambition system: per-tier bounds
  `{basic: [55, 85], moderate: [60, 100], competitive: [65, 115]}`,
  where an omitted tier means *this variant does not fire for that
  athlete*. `{scaled: [lo, hi]}` derives BASIC/MODERATE at 0.65/0.85 from
  the COMPETITIVE literals (`patterns.scaled`, Python rounding). A bare
  `[lo, hi]` fires for all tiers (race week).
- A **`Progression`** is only a shared ramp identity: variants naming
  the same progression share week-over-week ramp state. Magnitudes
  always live on the bounds, never on the progression.

## The YAML format

`loader.py` reads it, rejects any unknown key or malformed value with its
path (`running_5k.yaml:menus.base/load.slots[2].day: ...`), and runs the
import-time contracts below on the result.

```yaml
goal: running_5k
offered: [basic]                         # optional; the tiers a new plan may use
progressions: [running_5k_long_run]      # every name a `progression:` may use
menus:
  base/load:                             # "<phase>/<mode_key>"
    contract:                            # optional; every field optional
      must_contain: [run_long]
      must_not_contain: []
      min_sport_sessions: {running: 3}
      weekly_quality_sessions: [0, 1]
      bricks_per_week: [0, 1]
    slots:                               # ORDER IS LOAD-BEARING (brick pairing)
      - name: 5k_base_sun_long_run
        day: sun                         # mon | tue | ... | sun
        activity: run_long
        rotation: [run_long]             # optional
        minutes: {scaled: [55, 65]}      # swim: metres: instead
        progression: running_5k_long_run # optional
        window: [0.0, 1.0]               # optional phase-fraction window
        load_weeks: false                # optional; window reads the phase's load weeks
        group: base_long                 # optional slot group
        brick: false                     # optional
        race_sim: false                  # optional
        max_step: 0.5                    # optional; race_sim ramp slots only
        key: 0.5                         # optional key-session share, (0, 1]
        eases: running_5k_long_run       # optional; easier-week slots only
        optional: false                  # optional; true = an extra the plan does not count on
        cadence_weeks: 8                 # optional; fires on week 1, then every 8 weeks
        # intent: dev-facing reasoning lives in comments
        why: >-
          Athlete-facing text, rendered verbatim on the session card.
      - name: sprint_build_mon_run_speed # a multi-variant slot
        day: mon
        why: >-
          Slot-level why, used when a variant has none.
        variants:
          - activity: run_strides
            minutes: {basic: [30, 36]}
          - activity: run_speed
            minutes: {moderate: [26, 34], competitive: [30, 40]}
```

- A slot either carries its one variant's keys inline (its `why` is the
  variant's) or lists `variants:` (the slot's own `why` is the fallback).
- Identical slot lists may be shared with a YAML anchor
  (`slots: &base_recovery` ... `slots: *base_recovery`).
- `maintenance_supplements.yaml` holds the cross-category B/C-goal
  supplements, keyed by A-goal: `- {activity: ..., day: ..., minutes: [lo, hi]}`.
- `offered:` names the ambition tiers a new plan may be built at; a menu
  without it offers none. The wizard, the coach and the plan guard read it
  (`endury/planning/offered_plans.py`), so a reviewed menu goes back on offer
  with this one line. The planner itself builds every tier: a stored plan
  replans, and a tune-up race keeps its goal type, whatever is offered.
  Changing a tier on offer fails `tests/planner/test_maturity_audit.py` until
  the tier is reviewed again and a new review line in `docs/plan_maturity.md`
  carries the menu fingerprint the test prints.

## Unit law

Swim bounds are **metres** (`metres:`); everything else is **minutes**
(`minutes:`). The loader rejects the wrong key for the activity's sport,
but not the wrong scale: a swim slot with `metres: [30, 45]` validates
fine, converts to a ~1-minute session, and is then **silently dropped**
by the generator's 9-minute floor. Check the swim sessions in the
scenario report after any swim edit.

Swim metres are the card's metres. The generator books each swim at the
clock of the card that swims them, at the athlete's own CSS, for the swim
the week books (each member of a rotation converts the slot's metres for
itself), so a slower swimmer gets a longer session, not a shorter swim.
A card moves in 100 m steps (an interval swim in its own rep steps), so it
lands within one step of the bounds. The drill holds ten 50 m drills at
most and swims the rest of a longer booking as easy freestyle, up to
2,500 m in all (R-SESS-011, R-ACT-027).

## How a week is selected at runtime

1. `window` filters variants by position within the phase run:
   `window: [0.0, 0.55]` = early-phase only. With `load_weeks: true`
   a load week reads its place among the run's load weeks instead
   (load week i of n at i/n, so the last of three reads 0.67), and
   a recovery week cannot shift it. The Ironman PEAK weekend slots
   carry it so the two big weekends survive any recovery week; give
   every member of a group the same reading.
2. Within one `group`, exactly one variant wins per week — nearest
   window midpoint, later window on ties. Groups resolve within one
   tier's menu, so tiers can share group names.
3. `race_sim: true` variants skip window geometry and fire only on the
   one PEAK race-sim week, winning their group outright — and only when
   each keyed `race_sim` variant's minimum (at least its card's floor)
   fits its `key:` share of that week, and each `progression:` one is at
   most a quarter (its `max_step:`) longer than the least its progression
   was last booked or the athlete's recent longest, whichever is longer;
   otherwise the week stays routine.
4. `cadence_weeks: N` variants skip window geometry and fire on the
   plan's cadence weeks for N: the first week, then the recovery week
   nearest every N plan weeks (at most two away, later on ties), never in
   peak, taper or a tune-up's race week. A mark with no such week fires
   nothing. On its weeks the variant wins its group outright; on every
   other week it is absent, so its group's stand-ins need windows that
   cover the whole phase. The fitness tests use it: an athlete tests in
   week 1 and every eight weeks, whatever the horizon.
5. `rotation: [A, B]` alternates the activity key week-over-week.
5. A `progression:` variant ramps from the athlete's current longest
   within its bounds (10 %/week cap).
6. `brick: true` co-locates with the **most recent preceding cycling
   slot in the list** — slot order is load-bearing; keep a brick
   immediately after its bike.
7. `key: <share>` marks a key session. When the week is short on time,
   a key session keeps its size (never below its card's floor) and the
   other sessions shrink; while they would shrink below half their
   minimum, the one that fits the week least drops (the largest, or one
   that cannot shrink to its card), but never the last one. While the
   week holds any other session, a key session takes at most `share` of
   the week; a week left with key sessions only books each at most its
   share and stays light. A week too small for the share drops the key
   session instead, and its time goes to the other sessions. Should the
   keys' own bookings fill the whole week (shares summing to 1 or more),
   the week falls back to shrinking everything alike. The trail menus
   key climbing 0.25, load and taper long runs 0.5, rehearsals 0.67; the
   half-Ironman keys its PEAK long run at 0.3, and its moderate
   recovery-week tests at 0.3 so a small recovery week keeps its tests
   and shrinks the easy sessions around them; triathlon maintenance
   MODERATE keys its three quality sessions and its test-week swim at
   0.2 each. A maintenance session targets its authored maximum
   (R-SESS-035), and a key keeps its target in a squeezed week, so a
   keyed maintenance slot needs a narrow range: a wide one books the
   small athlete's quality at its top and cuts the long sessions under
   their ramp.
   Keying a slot changes every squeezed week it fires in, so run the
   whole ladder.
8. `eases: <progression>` marks a recovery, taper or race-week slot that
   stands in for that progression: it is never longer than 0.75 of the
   longest the progression has reached (the athlete's recent longest
   before it starts), rounded down to 5 minutes; lower bounds stand. The
   week's other easy-family runs of that sport (not key, not eased) are
   capped the same way, so no easy run outgrows the long run. It never
   feeds the ramp. The trail menus ease their recovery and taper long
   runs, and the 100K recovery Saturday and taper Sunday; the
   half-Ironman and half-marathon menus ease their taper long run.
9. `optional: true` marks an extra the plan does not count on: it is
   booked at its target, titled "Optional: ", and stays outside the
   week's capacity fit and the load model, so the rest of the plan is the
   same with or without it (R-SESS-040). It is never a key, ramp or
   race-sim session. The trail menus offer easy rides this way, on days
   they leave free of running.

## What can break silently (validation will NOT catch these)

| Trap | Symptom | Caught by |
|:-----|:--------|:----------|
| Swim `metres:` with minute-scale numbers | session vanishes (9-min floor) | scenario report / menu_diff |
| `group` typo on one member | both variants fire same week | menu_diff (extra spec) |
| Group windows leaving a gap | session vanishes in gap weeks | scenario report week walk |
| Reordering slots across a bike/brick pair | brick co-locates with wrong ride | `test_brick_partner_is_colocated_bike...` |
| Editing one tier of per-tier bounds when you meant all | tier divergence | menu_diff per-cell view |
| Bounds pushed past the activity's catalog TSS window | card silently resizes vs solved hours | `tests/planner/test_menu_envelope.py` (widen the catalog window); the scorecard rule `load_pricing_agreement` (`make run-devtool TOOL=planner_score_quality`) |

Load-time validation DOES catch: unknown keys, the wrong unit key, an
unknown activity/day/phase, an undeclared progression, malformed bounds
or windows, the coverage matrix, `must_contain` / `must_not_contain`,
`min_sport_sessions`, `bricks_per_week`, `weekly_quality_sessions`,
the swim floor (a load or taper swim starts at 1500 / 1800 / 2000 m by
tier; drills and the CSS test are exempt, `patterns.swim_floor_metres`),
duplicate slot names, reversed bounds, invalid windows — and the
day-layout lints, replayed on every weekly layout the menu can emit:
>2 sessions on one day, same-sport same-day doubles (including
inclusive-window-boundary co-fires), same-sport physiologically-hard
sessions on adjacent days (rotation-aware, including the Sun→Mon wrap of
the repeating weekly layout), a brick without a same-day ride, and
race-week menus authoring a Sunday slot (race menus assume a Sunday race;
other race days are evicted at runtime).

## Naming grammar

`[tier_]<phase>_<day>_<what>` — `base_sun_long_run` (all tiers),
`mod_base_tue_bike_climb` (tier-gated). Unique within each menu.
Names are provenance: every placed session carries its slot name, so a
finding can name the exact slot to edit.

## The validation ladder (run after every edit)

| # | Command | Answers | Time |
|:--|:--------|:--------|:-----|
| 1 | `make test PYTEST_PATH=tests/planner/menus PYTEST_COVERAGE=` | loader + contracts + brick pairing hold | ~5 s |
| 2 | `make run-devtool TOOL=planner_menu_diff` | exactly what changed, per goal/phase/mode/tier cell (empty = pure refactor) | ~15 s |
| 3 | `make run-devtool TOOL=planner_menu_canon` | slot order, names, `why` text or contracts changed (empty = pure reformat) | ~3 s |
| 4 | `make run-devtool TOOL=planner_fingerprints` | which scenario plans changed at all (empty = planner output unchanged) | ~11 s |
| 5 | `make run-devtool TOOL=planner_score_quality ARGS='--matrix --json'` | no rule regressed at any tier × horizon | a few minutes |
| 6 | `make run-devtool TOOL=planner_scenario_report ARGS='--scenario ironman_moderate --no-plots'` | qualitative week-walk of one scenario plan | ~10 s |
| 7 | `make test PYTEST_PATH=tests/planner` + `make lint` + `make test-scorecards` | nothing else broke; the scorecard baseline holds | ~2 min |

Steps 2–4 diff the working tree against `HEAD`; `ARGS='--rev <rev>'` picks
another base and `--goal <goal>` narrows them. No scenario covers
`trail_100k`, so for it only steps 1–3 see an edit,
and the scorecard matrix (step 5) covers the triathlon race goals, the
marathon, the half marathon and the 10K only. A composite that rises in step 7 is a
regression unless the edit meant it: re-record
`devtools/quality/baselines/planner_matrix.json` in the same
commit and say what was traded for what.

## Edit-proposal schema (for reviews and agents)

A finding from a plan review — an issue, a pull request, an agent's
report — should land as a structured proposal, so anyone (or any agent) can
apply → verify → revert it mechanically:

```json
{
  "finding": "MODERATE BUILD brick ceiling too low for durability (build_brick_run_duration)",
  "target": {"goal": "ironman", "phase": "build", "mode": "load",
             "slot": "build_sat_brick", "tier": "moderate"},
  "edit": {"kind": "bounds", "from": [18, 35], "to": [18, 40]},
  "expected": {"improve": ["build_brick_run_duration"], "hold": "all other rules",
               "blast_radius": "1 cell (build/load/moderate)"},
  "verify": ["planner_menu_diff", "score_quality --matrix --json"]
}
```

`edit.kind` ∈ `bounds` | `move_day` | `add_slot` | `remove_slot` |
`attach_progression` | `set_flag` (race_sim / brick) |
`retier` (change which tiers fire) | `refrac` (window).

## Rationale policy

`why:` is athlete-facing: the session card renders it verbatim, so it is
self-contained coaching language — no citations, section refs, author
names or dev-speak. The engineering reasoning behind a slot goes in a
`# intent:` comment above its `why:`. Author both on mechanism-bearing
slots (window splits, race_sim, rotation, tier-conditional structure);
don't boilerplate them onto self-evident easy sessions.

Comments cite the research pages in `docs/research/` by a short name and
section: `tlc` is `triathlon_long_course.md`, `tri` is `triathlon.md`, `tr`
is `trail_running.md`, `em` is `endurance_methodology.md` and `sc` is
`short_course.md`.

## Known violations

None.
