"""Chart specs: what a script may attach to the coach's reply.

A spec is plain JSON the page draws as SVG (``web/components/chat_chart.js``).
The same check runs in the sandbox, when the script calls ``ana.chart``, and
again on the coach's side before a spec reaches the reply, so a script that
bypasses the helper cannot hand the page anything else.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

#: Chart kinds and the most points each keeps; longer line, area and scatter
#: series are thinned to this many bucket means, a longer bar chart refused.
KIND_POINTS: dict[str, int] = {"line": 200, "area": 200, "scatter": 300, "bar": 60}

#: One unit per y-axis. Pace units take seconds per km or per 100 m and are
#: drawn as m:ss on an axis where faster sits higher.
UNITS: frozenset[str] = frozenset(
    {
        "",
        "bpm",
        "W",
        "W/bpm",
        "km/h",
        "min/km",
        "min/100m",
        "m",
        "km",
        "m/h",
        "%",
        "rpm",
        "spm",
        "s",
        "min",
        "h",
        "ms",
        "load",
        "°C",
    }
)

#: A unit a series label names in brackets, e.g. ``HR (bpm)``.
_LABEL_UNIT = re.compile(r"[(\[]\s*([^()\[\]]+?)\s*[)\]]\s*$")

MAX_CHARTS = 3
MAX_SERIES = 3
MAX_TITLE = 80


class ChartError(ValueError):
    """A chart spec the page would not draw."""


def _number(value: Any) -> float | None:
    """Return a finite float, or None for a gap."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 3) if math.isfinite(number) else None


def _thin(
    x: list[Any], ys: list[list[float | None]], cap: int
) -> tuple[list[Any], list[list[float | None]]]:
    """Average a long numeric series down to ``cap`` buckets."""
    if len(x) <= cap:
        return x, ys
    if any(isinstance(v, str) for v in x):
        raise ChartError("a long series needs numeric x to be thinned")
    edges = [round(i * len(x) / cap) for i in range(cap + 1)]

    def mean(values: list[Any]) -> float | None:
        kept = [v for v in values if v is not None]
        return round(sum(kept) / len(kept), 3) if kept else None

    buckets = list(itertools.pairwise(edges))
    return (
        [mean(x[a:b]) for a, b in buckets],
        [[mean(y[a:b]) for a, b in buckets] for y in ys],
    )


def make_spec(
    kind: str,
    x: list[Any],
    ys: list[list[Any]],
    title: str,
    *,
    x_label: str = "",
    unit: str = "",
    labels: list[str] | None = None,
    stacked: bool = False,
) -> dict[str, Any]:
    """Validate one chart and return its spec.

    Args:
        kind: ``line``, ``area``, ``bar`` or ``scatter``.
        x: Numbers, ``YYYY-MM-DD`` dates, or category names for bars.
        ys: One to three series as long as ``x``, all in ``unit``.
        title: What the chart shows, in the athlete's words.
        x_label: The x-axis label.
        unit: One of :data:`UNITS`.
        labels: A name per series; needed for two or more series.
        stacked: Stack the bars of several series.

    Returns:
        The spec the page draws.

    Raises:
        ChartError: The chart breaks one of the rules above.
    """
    if kind not in KIND_POINTS:
        raise ChartError(f"chart kind must be one of {sorted(KIND_POINTS)}")
    if unit not in UNITS:
        raise ChartError(
            f"unit must be one of {sorted(UNITS)}: one unit per chart, "
            "make two charts for two units"
        )
    x = [v if isinstance(v, str) else _number(v) for v in list(x)]
    series = [[_number(v) for v in list(y)] for y in ys]
    if not series or len(series) > MAX_SERIES or any(len(y) != len(x) for y in series):
        raise ChartError(f"ys must be 1 to {MAX_SERIES} series, each as long as x")
    if len(series) > 1 and (labels is None or len(labels) != len(series)):
        raise ChartError("name every series in labels")
    for label in labels or []:
        # "HR (bpm)" on a chart in W: the series names a unit of its own.
        named = _LABEL_UNIT.search(str(label))
        if named and named.group(1) in UNITS and named.group(1) != unit:
            raise ChartError(
                f"series '{label}' is in {named.group(1)}, not "
                f"{unit or 'no unit'}: one unit per chart, make two charts"
            )
    if kind == "bar" and len(x) > KIND_POINTS["bar"]:
        raise ChartError(f"a bar chart takes at most {KIND_POINTS['bar']} bars")
    if (
        any(isinstance(v, str) for v in x)
        and kind != "bar"
        and not all(_is_date(v) for v in x)
    ):
        raise ChartError("category names on x need a bar chart")
    x, series = _thin(x, series, KIND_POINTS[kind])
    return {
        "kind": kind,
        "title": str(title)[:MAX_TITLE],
        "x_label": str(x_label)[:40],
        "unit": unit,
        "labels": [str(label)[:30] for label in labels] if labels else None,
        "stacked": bool(stacked) and kind == "bar",
        "x": x,
        "ys": series,
    }


def _is_date(value: Any) -> bool:
    """Return whether a value reads as a ``YYYY-MM-DD`` date."""
    text = str(value)
    return len(text) >= 10 and text[4] == "-" and text[7] == "-" and text[:4].isdigit()


def checked_specs(specs: Any) -> list[dict[str, Any]]:
    """Re-check specs that came back from the sandbox; drop any that fail.

    Args:
        specs: Whatever the runner returned under ``charts``.

    Returns:
        At most :data:`MAX_CHARTS` specs that pass :func:`make_spec`.
    """
    kept: list[dict[str, Any]] = []
    for spec in specs if isinstance(specs, list) else []:
        try:
            kept.append(
                make_spec(
                    spec["kind"],
                    spec["x"],
                    spec["ys"],
                    spec["title"],
                    x_label=spec.get("x_label", ""),
                    unit=spec.get("unit", ""),
                    labels=spec.get("labels"),
                    stacked=spec.get("stacked", False),
                )
            )
        except (ChartError, KeyError, TypeError):
            continue
    return kept[:MAX_CHARTS]
