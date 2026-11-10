"""The results file as tables a reader can check without opening the JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.rules import Rulebook

VOLUME = {"light": "low", "typical": "typical", "heavy": "high"}


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _row(name: str, cell: dict[str, Any]) -> str:
    agg = cell["aggregate"]
    samples = cell["samples"]
    if not agg.get("valid"):
        why = next(
            (s.get("error") or s.get("skipped") or "no plan" for s in samples),
            "no plan",
        )
        return f"| {name} | {_called(samples)} | 0 | - | - | - | {why} |"
    facts = [s["facts"] for s in samples if s.get("facts")]
    peak = _median([f.get("total_hours") for f in facts])
    cost = agg.get("cost_usd")
    return (
        f"| {name} | {_called(samples)} | {agg['valid']} | "
        f"{_fmt(agg['rules_met_median'], 0)} "
        f"({agg['rules_met_range'][0]}-{agg['rules_met_range'][1]}) "
        f"of {agg['rules_total']} | "
        f"{_fmt(agg['strong_met_median'], 0)} of {agg['strong_total']} | "
        f"{_fmt(agg['composite_median'], 3)} | "
        f"{_fmt(peak, 0)} h, "
        f"{'-' if cost is None else f'${cost:.3f}'} |"
    )


def _median(values: list[Any]) -> float | None:
    clean = sorted(v for v in values if isinstance(v, (int, float)))
    if not clean:
        return None
    mid = len(clean) // 2
    return float(clean[mid]) if len(clean) % 2 else (clean[mid - 1] + clean[mid]) / 2.0


def _called(samples: list[dict[str, Any]]) -> int:
    """Count the samples a call actually ran for."""
    return sum(1 for s in samples if not s.get("skipped"))


def write_report(payload: dict[str, Any], rulebook: Rulebook, path: Path) -> None:
    """Write the markdown report beside the results file."""
    lines = [
        "# Benchmark results",
        "",
        f"Measured {payload['measured_on']}; rulebook version "
        f"{payload['rulebook_version']}, registration `{payload['registration']}`. "
        "Rules met counts every applicable rule; strong counts the rules with A or "
        "B evidence. The composite is the mean distance from the bands, equal "
        "weight per family; 0 is inside every band. The answers under `raw/` are "
        "AI-generated, kept unedited as evidence, and are not training advice."
        + (
            " The Claude models ran through Claude Code on a subscription: their "
            "cost is an estimate at list price and their time is not comparable."
            if "claude-code" in json.dumps(payload)
            else ""
        ),
        "",
    ]
    for label, cell in payload["scenarios"].items():
        sc = cell["scenario"]
        lines += [
            f"## {label}",
            "",
            f"{sc['goal']} in {sc['weeks']} weeks, {sc['tier']} tier, "
            f"athlete on a {VOLUME[sc['athlete']['label']]} training volume"
            + (
                f", weekly cap {sc['weekly_cap_hours']} h"
                if sc.get("weekly_cap_hours")
                else ""
            )
            + ".",
            "",
            "| Contestant | Samples | Valid | Rules met, median (range) | "
            "Strong met | Composite | Total hours, cost |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        lines += [_row(name, c) for name, c in cell["contestants"].items()]
        nobody = cell["ceiling"]["rules_nobody_met"]
        if nobody:
            lines += [
                "",
                "Rules no contestant met (review the band before counting them): "
                + ", ".join(nobody)
                + ".",
            ]
        lines += ["", "Off band, first sample of each contestant:", ""]
        for name, c in cell["contestants"].items():
            first = next((s for s in c["samples"] if s.get("summary")), None)
            if first:
                off = (
                    ", ".join(
                        f"{k} {v}" for k, v in first["summary"]["off_band"].items()
                    )
                    or "none"
                )
                lines.append(f"- **{name}**: {off}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
