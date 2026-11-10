# Plan-quality scorecard

The deterministic scorecard for generated training plans, and its
committed baseline. The rules and their bands are data, in the rulebook
([`bench/rulebook.yaml`](../../bench/rulebook.yaml)); the scorecard
grades every plan on offer on all of them, and `make test-scorecards`
fails when a reading moves away from its band.

| Piece | Path | What it is |
| --- | --- | --- |
| Scorecard | [`planner/`](planner/README.md) | `score_quality.py` grades a plan on every rule: each measures one quantity and scores its distance from a target band. |
| Baseline | [`baselines/`](baselines/README.md) | `planner_matrix.json`, the committed reading `make test-scorecards` gates against: no composite may rise. |
| Rule page | `rules_page.py` | Writes [`docs/quality/rules.md`](../../docs/quality/rules.md), every rule of the rulebook by race goal, on `make docs`. |

The rules that can be asked of any plan are read off the workout cards by
the benchmark's own readings; the rest read Endury's plan structure here.

Run it with `make run-devtool TOOL=planner_score_quality`, with
`ARGS='--matrix --json'` for the goal by ambition matrix.
