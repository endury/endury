# Planner plan-quality scorecard

The planner domain of [`devtools/quality/`](../README.md): a
deterministic scorecard for generated training plans. Every rule
measures one quantity on a plan and returns a bounded distance from its
target band: 0 inside the band, 1 far outside. The rules and their bands
are data, in the rulebook ([`bench/rulebook.yaml`](../../../bench/rulebook.yaml),
format in [`bench/README.md`](../../../bench/README.md)). A `benchmark`
rule is read off the plan's workout cards by the benchmark's own readings
(`bench/readings.py`), exactly as it is read off an AI language model's
plan; a rule with `benchmark: false` reads Endury's own plan structure,
and its evaluator is Python, here.

| Piece | Path | What it is |
| --- | --- | --- |
| Scorecard | `score_quality.py` | The evaluators of the structural rules (`EVALUATORS`), the card readings of every other rule (`_card_distances`), and the composite weighted by each rule's `weight`. |
| Rulebook | `bench/rules.py`, `bench/rulebook.yaml` | Loads and validates the rulebook and resolves a rule's band for a goal and ambition tier: the goal's own band first, then the rule's fallback. |

## What it sweeps

- By default, the Ironman competitive plan at 22, 24, 28 and 32 weeks.
- `--matrix`, the accept check that `make test-scorecards` gates through
  `baselines/planner_matrix.json`: every goal in `GOAL_MATRIX_HORIZONS`
  by the ambition tiers its menu offers (`offered:`), with three evidence
  families per cell. `_wide` is the same default athlete at the rest of
  the planner's recommended horizons (`GOAL_MATRIX_HORIZONS_WIDE`),
  `_low` the base horizons at 0.75 times the tier's default recent
  volume, `_high` the same at 1.5 times.
- `--replan`: mid-preparation replans against `REPLAN_RULES`, the
  R-PHASE-002b suffix, fixed-bound stability and seam convergence.

The rulebook's loader rejects a rule that covers a goal without a band
for it, and a band for a goal the rule does not cover. At import,
`score_quality.check_rulebook` asserts that every `benchmark: false` rule
has an evaluator and every evaluator a rule, so the YAML and the registry
cannot drift apart.

The scorecard grades the plan the weeks book: an optional session is left
out before the cards are read. A reading a plan does not carry (no taper
week in a sprint plan) scores 0 here; the benchmark's `missing` distance
applies only when it grades a contestant's plan.

The shared pipeline-run, metrics, scenario and replan-replay primitives
live in `devtools/planner/_shared/`, imported here and owned there.

## How to run it

```bash
# The competitive 22/24/28/32-week Ironman sweep
poetry run python -m devtools.quality.planner.score_quality
# Every goal by tier, the accept check make test-scorecards gates
poetry run python -m devtools.quality.planner.score_quality --matrix --json
# The replan-quality axis, a cutover-week sweep
poetry run python -m devtools.quality.planner.score_quality --replan

# The replan-diff replay behind --replan, deterministic and without a database
make run-devtool TOOL=planner_scenario_report ARGS='--scenario ironman_moderate --replan-at-week 8 --no-plots'
# The blast radius of a menu edit on the flattened menus; an empty diff is a pure refactor
make run-devtool TOOL=planner_menu_diff
```

Scoring and the replan replay are deterministic: the pipeline has no
solver jitter, and sessions live on their menu-authored days.

## Changing a band or a rule

1. Edit the band in `bench/rulebook.yaml`; the format is in its header
   and in `bench/README.md`. A new band needs at least two independent
   published sources.
2. A band for a goal the rule does not yet cover also adds that goal to
   the rule's `goals`; the loader says so if you forget. A changed band
   of a `benchmark` rule also regrades the benchmark's saved answers
   (`bench/README.md`, "Run it").
3. Re-run `--matrix --json` and re-record
   `devtools/quality/baselines/planner_matrix.json` in the same commit,
   saying what moved and why.

## Authorities the rules cite

Referenced here, owned elsewhere.

- `docs/specs/planner/`: the planner's behavioural and per-layer
  contracts, such as `behavioral.md` R-PP-002 on the long-session peak
  and `phase_planner.md` R-PHASE-002b on the replan suffix.
- `docs/research/`: `triathlon_long_course.md`,
  `endurance_methodology.md`, `triathlon.md` and `trail_running.md`, the
  sport-science sources behind the literature bands. The trail 50K's
  bands, bar race-day freshness, are all literature.
