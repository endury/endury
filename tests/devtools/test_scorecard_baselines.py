"""Baseline gates for the devtools/quality scorecards.

Every instrument under ``devtools/quality/`` has a committed
reading in ``devtools/quality/baselines/``. These tests re-run
each instrument and assert that no composite has moved *up* (the
scorecard is a distance: lower is better, 0 = ideal). Without them a
product change can regress plan quality with the whole suite
green — the instruments live outside ``--cov=endury`` and nothing else
executes them.

Raising a baseline is allowed, but only as a reviewed diff in the baseline
JSON, in the same commit as the change that justifies it.

Runtime (also documented in the baselines README): the planner matrix
(one base cell per plan on offer, plus its ``_wide``, ``_low`` and ``_high``
evidence families) is marked ``slow`` and excluded from ``make test``.
``make test-scorecards`` and ``.githooks/pre-push`` run it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "devtools" / "quality" / "baselines"

EPSILON = 1e-3
"""Tolerance applied to every composite comparison.

Baselines are stored rounded to four decimals and the planner matrix runs
through a floating-point QP solver, so an exact ``<=`` would be flaky.
1e-3 sits an order of magnitude above that storage resolution and well
below the movement a single firing rule puts into a composite.
"""


def _load_baseline(instrument: str) -> dict[str, Any]:
    """Load one instrument's committed baseline reading.

    Args:
        instrument: Baseline file stem (e.g. ``planner_matrix``).

    Returns:
        The parsed baseline payload.
    """
    return json.loads((BASELINE_DIR / f"{instrument}.json").read_text())


def _run_scorecard(module: str, *args: str) -> dict[str, Any]:
    """Run a scorecard's JSON CLI in a child process and parse its stdout.

    A subprocess keeps each instrument's process-wide side effects out of
    the pytest worker.

    Args:
        module: Dotted module path of the scorecard CLI.
        *args: Extra CLI arguments appended after ``--json``.

    Returns:
        The scorecard's JSON payload.
    """
    proc = subprocess.run(
        [sys.executable, "-m", module, "--json", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"{module} exited {proc.returncode}; the instrument itself is broken:\n"
        f"{proc.stderr}"
    )
    return json.loads(proc.stdout)


def _key_drift(label: str, measured: set[str], baseline: set[str]) -> list[str]:
    """Report entries where the instrument and its baseline disagree.

    Args:
        label: What the keys are (``rules``, ``cells``, ...).
        measured: Keys the instrument just emitted.
        baseline: Keys the committed baseline records.

    Returns:
        Zero or one message describing the drift.
    """
    added = sorted(measured - baseline)
    removed = sorted(baseline - measured)
    if not added and not removed:
        return []
    return [
        f"{label} drifted from the baseline: "
        f"added {added or 'none'}, missing {removed or 'none'}"
    ]


def _composite_regressions(
    checks: list[tuple[str, float, float, dict[str, float], dict[str, float]]],
) -> list[str]:
    """Find composites above their baseline and attribute each to its rules.

    Args:
        checks: ``(label, measured, baseline, measured_rules, baseline_rules)``
            per composite under gate. Pass empty rule maps when an entry has
            no per-rule breakdown.

    Returns:
        One message per regressed composite, naming the rules that got worse.
    """
    messages: list[str] = []
    for label, measured, baseline, measured_rules, baseline_rules in checks:
        if measured <= baseline + EPSILON:
            continue
        worse = sorted(
            (
                (name, value - baseline_rules.get(name, 0.0))
                for name, value in measured_rules.items()
                if value - baseline_rules.get(name, 0.0) > EPSILON
            ),
            key=lambda item: -item[1],
        )
        blame = (
            "; ".join(f"{name} +{delta:.4f}" for name, delta in worse)
            or "no single rule moved by more than the epsilon"
        )
        messages.append(f"{label}: {measured:.4f} > baseline {baseline:.4f} — {blame}")
    return messages


def _fail_on(instrument: str, problems: list[str]) -> None:
    """Fail the gate with every problem listed at once.

    Args:
        instrument: Baseline file stem the problems belong to.
        problems: Messages from :func:`_key_drift` / :func:`_composite_regressions`.
    """
    if not problems:
        return
    pytest.fail(
        f"{instrument} regressed against devtools/quality/"
        f"baselines/{instrument}.json:\n  " + "\n  ".join(problems) + "\n\n"
        "Re-measure with that file's `command`. Raising a baseline is a "
        "reviewed diff in that file, in the same commit as the change.",
        pytrace=False,
    )


@pytest.mark.slow
def test_planner_matrix_no_worse_than_baseline() -> None:
    """Every planner goal x tier cell holds.

    Marked ``slow``: excluded from the default ``make test``, run by
    ``make test-scorecards`` and the pre-push hook.
    """
    baseline = _load_baseline("planner_matrix")
    report = _run_scorecard("devtools.quality.planner.score_quality", "--matrix")

    problems = _key_drift("cells", set(report["cells"]), set(baseline["cells"]))

    checks = []
    for cell, base in sorted(baseline["cells"].items()):
        got = report["cells"].get(cell)
        if got is not None:
            checks.append(
                (
                    f"cell {cell}",
                    got["composite"],
                    base["composite"],
                    got["per_rule"],
                    base["per_rule"],
                )
            )
    problems += _composite_regressions(checks)
    _fail_on("planner_matrix", problems)
