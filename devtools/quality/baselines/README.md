# Scorecard baselines

One committed reading per deterministic instrument. The scorecard is a
distance: lower is better, and 0.0 is ideal. The gate is
`tests/devtools/test_scorecard_baselines.py`, which re-runs each
instrument and fails if any composite has moved up.

| File | Instrument | Re-measure with | Runtime |
| --- | --- | --- | --- |
| `planner_matrix.json` | `planner/score_quality.py` | `make run-devtool TOOL=planner_score_quality ARGS='--matrix --json'` | Seconds: one base, `_wide`, `_low` and `_high` cell per plan on offer, every workout card built and read. |

## What is gated, and where it runs

Every composite is gated, one per planner goal and tier cell, with a
tolerance of `EPSILON = 1e-3`: baselines are stored at 4 decimals and the
planner runs a floating-point QP. The per-rule numbers are recorded for
attribution, not gated: a failing composite is reported with the rules
that moved, so a regression traces to a rule instead of a bare number.
The key sets are pinned, so a rule or cell appearing or disappearing
fails the gate and the file cannot go quietly stale.

The planner matrix is marked `slow` and excluded from `make test`
(`PYTEST_MARKERS` in the Makefile), so the everyday gate stays fast.
`make test-scorecards` runs it, and `.githooks/pre-push` runs `make test`
followed by `make test-scorecards`. The hook exists only in clones that
ran `make install-hooks`: `core.hooksPath` is per-clone config and cannot
be tracked by git.

## Raising a baseline

Lowering one is free; the gate is one-sided. Raising one is a deliberate
act:

1. Re-measure with the command above and confirm the new reading is
   real, not solver noise.
2. Update the JSON, composites and per-rule numbers, in the same commit
   as the change that justifies it, so the trade is visible in review.
3. Say in the commit message what was traded for what.

Never widen `EPSILON` to absorb a regression.

## File format

Readings rounded to 4 decimals, keys sorted, one number per line, so a
regression shows up as a readable diff. Each file carries its own
`command` and `measured_on` fields.
