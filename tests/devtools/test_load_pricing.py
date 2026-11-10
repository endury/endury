"""The load_pricing devtool: bridge, sized and card TSS on one matrix cell."""

from __future__ import annotations

import pytest

from devtools.planner import load_pricing
from endury.planner.foundations.types import AmbitionLevel, GoalType


def test_measure_cell_ratios_and_totals() -> None:
    report = load_pricing.measure_cell(GoalType.RUNNING_10K, AmbitionLevel.BASIC, 8)

    assert report.weekly_rows
    assert all(row.priced.card_over_bridge > 0 for row in report.weekly_rows)
    assert all(row.priced.card_over_sized > 0 for row in report.weekly_rows)

    assert report.plan.card_over_bridge == pytest.approx(
        report.plan.card_tss / report.plan.bridge_tss
    )
    # The rule's reading: the worst week's |card / sized - 1|.
    assert report.worst_sized_deviation == pytest.approx(
        max(abs(row.priced.card_over_sized - 1.0) for row in report.weekly_rows)
    )

    family_hours = sum(p.hours for p in report.family_rows.values())
    assert family_hours == pytest.approx(report.plan.hours, rel=1e-6)
    assert set(report.race_day) == {"bridge", "sized", "card"}


def test_main_runs_one_cell_and_prints_the_header(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = load_pricing.main(
        ["--goal", "running_10k", "--ambition", "basic", "--weeks", "8", "--brief"]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "== running_10k basic 8wk ==" in out
    assert "card/sized" in out
    assert "1 cell(s) in" in out
