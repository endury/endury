"""Chart specs and the script runner."""

from __future__ import annotations

import asyncio

import pytest

from endury.analysis.charts import ChartError, checked_specs, make_spec
from endury.analysis.runner import MAX_OUTPUT_CHARS, run_local, run_script


def test_a_spec_keeps_one_unit_per_chart() -> None:
    with pytest.raises(ChartError, match="unit must be one of"):
        make_spec("line", [1, 2], [[1, 2]], "t", unit="bpm / W")
    with pytest.raises(ChartError, match="one unit per chart"):
        make_spec(
            "bar",
            [1, 2],
            [[1, 2], [3, 4]],
            "t",
            unit="W",
            labels=["Power (W)", "HR (bpm)"],
        )
    spec = make_spec(
        "bar", [1, 2], [[1, 2], [3, 4]], "t", unit="W", labels=["Power (W)", "NP (W)"]
    )
    assert spec["labels"] == ["Power (W)", "NP (W)"]


def test_a_spec_names_every_series_and_matches_lengths() -> None:
    with pytest.raises(ChartError, match="labels"):
        make_spec("line", [1, 2], [[1, 2], [3, 4]], "t")
    with pytest.raises(ChartError, match="as long as x"):
        make_spec("line", [1, 2, 3], [[1, 2]], "t")


def test_names_on_x_need_bars_but_dates_draw_as_lines() -> None:
    with pytest.raises(ChartError, match="bar chart"):
        make_spec("line", ["a", "b"], [[1, 2]], "t")
    assert make_spec("line", ["2026-10-01", "2026-10-08"], [[1, 2]], "t")["x"] == [
        "2026-10-01",
        "2026-10-08",
    ]


def test_long_series_are_thinned_and_long_bars_refused() -> None:
    spec = make_spec("line", list(range(1000)), [[float(v) for v in range(1000)]], "t")
    assert len(spec["x"]) == 200
    assert spec["ys"][0][0] == pytest.approx(2.0)
    with pytest.raises(ChartError, match="at most 60 bars"):
        make_spec("bar", list(range(61)), [[1] * 61], "t")


def test_gaps_survive_as_none_and_stacking_is_bars_only() -> None:
    spec = make_spec("line", [1, 2, 3], [[1, float("nan"), None]], "t", stacked=True)
    assert spec["ys"] == [[1.0, None, None]]
    assert spec["stacked"] is False


def test_checked_specs_drop_what_fails() -> None:
    good = make_spec("bar", ["a"], [[1]], "fine")
    assert checked_specs([good, {"kind": "pie"}, "nonsense"]) == [good]
    assert checked_specs(None) == []


def _payload(code: str) -> dict:
    return {
        "code": code,
        "history": [{"id": "x", "date": "2026-10-01"}],
        "athlete": {},
        "activities": {},
    }


def test_a_script_prints_and_charts() -> None:
    result = run_script(
        _payload(
            "print(len(ana.history)); ana.chart('bar', ['a'], [[np.float64(2)]], 'one', unit='h')"
        )
    )
    assert result["stdout"] == "1\n"
    assert result["error"] is None
    assert result["charts"][0]["title"] == "one"


def test_an_error_names_the_line_and_never_a_path() -> None:
    result = run_script(_payload("x = 1\nana.activity('missing')"))
    assert result["charts"] == []
    assert "line 2: ana.activity('missing')" in result["error"]
    assert "inside ana.activity" in result["error"]
    assert "/" not in result["error"].replace("activity_ids", "")


def test_a_chart_drawn_before_an_error_is_kept() -> None:
    result = run_script(_payload("ana.chart('bar', ['a'], [[1]], 't')\n1 / 0"))
    assert [c["title"] for c in result["charts"]] == ["t"]
    assert "ZeroDivisionError" in result["error"]


def test_long_output_is_cut() -> None:
    result = run_script(_payload("print('x' * 9000)"))
    assert len(result["stdout"]) < MAX_OUTPUT_CHARS + 80
    assert "[output cut at 4,000 of 9,001 characters]" in result["stdout"]


def test_run_local_is_the_runner_awaited() -> None:
    assert asyncio.run(run_local(_payload("print('ok')")))["stdout"] == "ok\n"
