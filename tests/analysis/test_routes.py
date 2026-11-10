"""Matching two GPS tracks: shared ground, its pieces, the gap, every pass."""

from __future__ import annotations

import numpy as np
import pytest

from endury.analysis import library as ana

LOOP_M = 4000.0


def _on_loop(s: float) -> tuple[float, float, float]:
    """A 4 km square loop: flat east, a 10 % climb north, a descent west, flat south."""
    s %= LOOP_M
    if s < 1000:
        return s, 0.0, 0.0
    if s < 2000:
        return 1000.0, s - 1000, 0.1 * (s - 1000)
    if s < 3000:
        return 3000 - s, 1000.0, 100 - 0.1 * (s - 2000)
    return 0.0, 4000 - s, 0.0


def _speed(s: float, climb: float, descent: float) -> float:
    leg = (s % LOOP_M) // 1000
    return climb if leg == 1 else descent if leg == 2 else 4.0


def _streams(
    points: list[tuple[float, float, float]], seed: int, noise_m: float = 4.0
) -> dict:
    """1 Hz streams with GPS error that drifts slowly, as a receiver's does."""
    rng = np.random.default_rng(seed)
    drift = np.column_stack(
        [ana.smooth(rng.normal(0, 1, len(points)), 60) for _ in range(2)]
    )
    xy = np.array([(x, y) for x, y, _ in points]) + drift / drift.std() * noise_m
    path = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(xy, axis=0).T))])
    n = len(points)
    return {
        "time": list(range(n)),
        "distance": path.tolist(),
        "x": xy[:, 0].tolist(),
        "y": xy[:, 1].tolist(),
        "fixed_altitude": [z for _, _, z in points],
        "velocity_smooth": [4.0] * n,
        "heartrate": [140.0] * n,
        "watts": [250.0] * n,
    }


def _loop_run(
    climb: float,
    descent: float,
    *,
    lead_in: float = 0.0,
    backwards: bool = False,
    seed: int = 0,
) -> dict:
    """One lap at the given speeds, maybe from a lead-in to the south, maybe the other way round."""
    points, s = [], -lead_in
    while s < LOOP_M:
        if s < 0:
            points.append((0.0, s, 0.0))
            s += 4.0
            continue
        pos = _on_loop(LOOP_M - s if backwards else s)
        points.append(pos)
        s += _speed(LOOP_M - s if backwards else s, climb, descent)
    return _streams(points, seed)


def _hill_reps(reps: int) -> dict:
    """Up the climb leg and back down, ``reps`` times."""
    points = []
    for _ in range(reps):
        points += [(1000.0, y, 0.1 * y) for y in np.arange(0, 1000, 2.0)]
        points += [(1000.0, y, 0.1 * y) for y in np.arange(1000, 0, -5.0)]
    return _streams(points, seed=7)


def _load(**streams: dict) -> None:
    ana.load(
        {
            "history": [],
            "athlete": {},
            "activities": {
                key: {
                    "meta": {
                        "id": key,
                        "date": f"2026-10-0{n + 1}",
                        "name": key,
                        "type": "Run",
                        "sport": "run",
                    },
                    "streams": s,
                    "laps": [],
                }
                for n, (key, s) in enumerate(streams.items())
            },
        }
    )


def test_the_same_loop_is_found_and_compared_piece_by_piece(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(1.6, 6.0, lead_in=300, seed=1))

    rows = ana.compare_route(ana.activity("a"), ana.activity("b"))

    header = capsys.readouterr().out.splitlines()[0]
    assert "shared of a's 4.0 km" in header
    kinds = {r["kind"]: r for r in rows}
    assert {"climb", "descent"} <= set(kinds)
    # b climbed slower (1.6 against 2.0 m/s) and descended faster.
    assert kinds["climb"]["change_pct"] == pytest.approx(25, abs=6)
    assert kinds["descent"]["change_pct"] == pytest.approx(-17, abs=6)
    assert kinds["climb"]["grade_pct"] == pytest.approx(10, abs=1.5)
    assert "a_vam" in kinds["climb"]
    assert "b_extra_m" not in kinds["climb"]


def test_the_loop_run_backwards_is_not_compared(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(2.0, 5.0, backwards=True, seed=2))

    assert ana.compare_route(ana.activity("a"), ana.activity("b")) == []
    out = capsys.readouterr().out
    assert "km more the other way round, not compared" in out
    assert "no shared ground" in out


def test_the_time_gap_grows_where_b_loses_time() -> None:
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(1.6, 5.0, seed=3))

    gap = ana.time_gap(ana.activity("a"), ana.activity("b"))

    km, seconds = np.array(gap["km"], float), np.array(gap["gap_s"], float)
    flat_end = seconds[np.searchsorted(km, 0.95)]
    climb_end = seconds[np.searchsorted(km, 1.95)]
    assert abs(flat_end) < 15
    assert climb_end - flat_end == pytest.approx(
        125, abs=20
    )  # 1000 m at 1.6 vs 2.0 m/s


def test_every_pass_up_a_hill_counts_once() -> None:
    _load(reps=_hill_reps(3), other=_loop_run(2.0, 5.0, seed=4))
    reps = ana.activity("reps")

    rows = ana.efforts(reps, 0.05, 0.95, [ana.activity("other")])

    assert [(r["id"], r["pass"]) for r in rows] == [
        ("reps", 1),
        ("reps", 2),
        ("reps", 3),
        ("other", 1),
    ]
    assert all(r["vam"] > 0 for r in rows)


def test_without_gps_there_is_nothing_to_match(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lap = _loop_run(2.0, 5.0)
    bare = {k: v for k, v in lap.items() if k not in ("x", "y")}
    _load(a=lap, b=bare)

    assert ana.compare_route(ana.activity("a"), ana.activity("b")) == []
    assert ana.time_gap(ana.activity("a"), ana.activity("b")) == {"km": [], "gap_s": []}
    assert ana.efforts(ana.activity("b"), 0, 1) == []
    assert "no GPS track in" in capsys.readouterr().out


def test_a_stretch_survives_short_gaps_and_detours_but_not_a_jump() -> None:
    steps = np.arange(200)
    assert ana._sections(steps.copy()) == [(0, 199)]
    gappy = steps.copy()
    gappy[50:60] = -1  # 100 m without a match
    assert ana._sections(gappy) == [(0, 199)]
    detour = np.where(steps < 100, steps, steps + 18)  # 180 m more ground in b
    assert ana._sections(detour) == [(0, 199)]
    jump = np.where(steps < 100, steps, steps + 300)  # b's next pass, much later
    assert ana._sections(jump) == [(0, 99), (100, 199)]


def test_chart_route_draws_two_bars_per_climb() -> None:
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(1.6, 6.0, seed=1))
    a, b = ana.activity("a"), ana.activity("b")
    rows = ana.compare_route(a, b)

    ana.chart_route(rows, a, b, value="min", kinds=("climb",))
    ana.chart_route(rows, a, b, value="pace")

    climbs, everything = ana.CHARTS
    assert climbs["labels"] == ["1 Oct", "2 Oct"]
    assert climbs["unit"] == "min"
    assert climbs["x"] == ["km 1 · +10%"]
    assert climbs["title"] == "Time per climb, 1 Oct and 2 Oct"
    assert everything["title"] == "Pace per stretch, 1 Oct and 2 Oct"
    assert climbs["ys"][1][0] > climbs["ys"][0][0]
    assert everything["unit"] == "min/km"
    assert all(isinstance(v, float) for v in everything["ys"][0])
    with pytest.raises(ana.ChartError, match="value must be one of"):
        ana.chart_route(rows, a, b, value="speed")


def test_chart_gap_draws_the_gap_line() -> None:
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(1.6, 5.0, seed=3))
    ana.chart_gap(ana.activity("a"), ana.activity("b"))
    (line,) = ana.CHARTS
    assert (line["kind"], line["unit"], line["x_label"]) == ("line", "s", "km")
    assert line["title"] == "Time gap, 2 Oct against 1 Oct"
    _load(a=_loop_run(2.0, 5.0), b=_loop_run(2.0, 5.0, backwards=True, seed=2))
    with pytest.raises(ana.ChartError, match="no shared ground"):
        ana.chart_gap(ana.activity("a"), ana.activity("b"))
