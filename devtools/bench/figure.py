"""Draw the README's benchmark figure from the committed results.

Usage::

    poetry run python -m devtools.bench.figure            # docs/readme/benchmark_{light,dark}.svg
    poetry run python -m devtools.bench.figure --print    # the numbers only

The README shows the light figure and, under GitHub's dark theme, the dark
one; both are drawn from the same numbers with one palette each.

Every number is read from ``bench/results/results.json``: rules met over the
marathon cells and over the long-course cells, cells with no plan, the cost
and the time of one plan, and each contestant's most frequent miss.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

RESULTS = Path("bench/results/results.json")
OUT = Path("docs/readme/benchmark")

#: One palette per theme: card, rule, text, muted text, bar track, model bar,
#: Endury's bar, and the "no plan" note.
PALETTES = {
    "dark": {
        "card": "#19191B",
        "edge": "none",
        "rule": "#333336",
        "text": "#E5E5E7",
        "muted": "#ADADB1",
        "track": "#2D2D30",
        "model": "#B5B3AE",
        "endury": "#86AD90",
        "bad": "#C68F6C",
    },
    "light": {
        "card": "#F6F8FA",
        "edge": "#D0D7DE",
        "rule": "#D0D7DE",
        "text": "#1F2328",
        "muted": "#59636E",
        "track": "#E7ECF0",
        "model": "#8C959F",
        "endury": "#3F8A53",
        "bad": "#B86B3F",
    },
}

NAMES = {
    "endury": "Endury's planner",
    "anthropic/claude-opus-5.5": "Claude Opus 5.5",
    "anthropic/claude-sonnet-5": "Claude Sonnet 5",
    "google/gemini-3.8-flash": "Gemini 3.8 Flash",
    "google/gemini-3.5-flash-lite": "Gemini 3.5 Flash Lite",
    "openai/gpt-6-sol": "GPT-6 Sol",
    "x-ai/grok-4.7": "Grok 4.7",
    "deepseek/deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
}
PHRASES = {
    "fuelling_cues": "few fuelling cues",
    "first_long_session_fit": "first long session off the athlete's",
    "taper_intensity_kept": "taper drops quality work",
    "taper_volume_drop": "taper volume off band",
    "hard_sessions_per_week": "hard sessions/week off band",
    "tid_low_share": "easy share off band",
    "tid_high_share": "hard share off band",
    "vo2max_in_base": "VO2max in base",
    "label_honesty": "labels off the steps",
    "recovery_week_depth": "recovery weeks off band",
    "race_week_volume": "race-week volume off band",
    "monotony": "monotonous weeks",
    "warmup_before_hard": "no warm-up before hard work",
    "peak_weekly_hours": "peak week off band",
    "first_week_fit": "first week off current training",
    "long_run_peak": "longest run off band",
}


def _family(label: str) -> str:
    return "marathon" if label.startswith("marathon") else "long"


def _shared_cells(results: dict[str, Any]) -> dict[str, Any]:
    """The cells every contestant was asked: a cell no call ran for compares nothing."""
    return {
        label: cell
        for label, cell in results["scenarios"].items()
        if not any(
            all(s.get("skipped") for s in c["samples"])
            for c in cell["contestants"].values()
        )
    }


def summarise(results: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per contestant: rules met per family, failures, cost, time, misses."""
    rows: dict[str, dict[str, Any]] = {}
    for label, cell in _shared_cells(results).items():
        fam = _family(label)
        for name, c in cell["contestants"].items():
            row = rows.setdefault(
                name,
                {
                    "name": name,
                    "met": Counter(),
                    "total": Counter(),
                    "failed": Counter(),
                    "cost": {"marathon": [], "long": []},
                    "time": {"marathon": [], "long": []},
                    "misses": Counter(),
                    "valid_cells": 0,
                },
            )
            agg = c["aggregate"]
            if agg.get("valid"):
                row["met"][fam] += agg["rules_met_median"]
                row["total"][fam] += agg["rules_total"]
                row["valid_cells"] += 1
                first = next(s for s in c["samples"] if s.get("summary"))
                row["misses"].update(first["summary"]["off_band"].keys())
            else:
                row["failed"][fam] += 1
            for s in c["samples"]:
                cost = s.get("call", {}).get("cost_usd")
                if cost is not None:
                    row["cost"][fam].append(cost)
                # A call that never ran records 0 s.
                latency = s.get("call", {}).get("latency_s")
                if latency:
                    row["time"][fam].append(latency)
    out = list(rows.values())
    out.sort(
        key=lambda r: (
            r["name"] != "endury",
            -(sum(r["met"].values()) / max(sum(r["total"].values()), 1)),
        )
    )
    return out


def _cents(values: list[float]) -> str:
    if not values:
        return "–"
    c = statistics.median(values) * 100
    return f"{c:.1f} ¢" if c < 1 else f"{c:.0f} ¢"


def _cost(row: dict[str, Any]) -> str:
    if row["name"] == "endury":
        return "free · free"
    if not row["cost"]["marathon"] and not row["cost"]["long"]:
        return "-"
    return f"{_cents(row['cost']['marathon'])} · {_cents(row['cost']['long'])}"


def _duration(values: list[float]) -> str:
    if not values:
        return "–"
    s = statistics.median(values)
    if s < 1:
        return f"{s:.1f} s"
    return f"{s:.0f} s" if s < 90 else f"{s / 60:.0f} min"


def _time(row: dict[str, Any]) -> str:
    if not row["time"]["marathon"] and not row["time"]["long"]:
        return "-"
    return f"{_duration(row['time']['marathon'])} · {_duration(row['time']['long'])}"


def _miss(row: dict[str, Any]) -> str:
    top = row["misses"].most_common(1)
    if not top:
        return ""
    rule, count = top[0]
    phrase = PHRASES.get(rule, rule.replace("_", " "))
    return f"{phrase} ({count}/{row['valid_cells']})"


def _bar(x: float, y: float, share: float, cls: str) -> str:
    return (
        f'  <rect x="{x}" y="{y}" width="100" height="16" rx="4" class="track"/>\n'
        f'  <rect x="{x}" y="{y}" width="{100 * share:.1f}" height="16" rx="4" '
        f'class="{cls}"/>\n'
    )


def render(results: dict[str, Any], theme: str = "dark") -> str:
    """The SVG, in the palette of ``theme`` (``"dark"`` or ``"light"``)."""
    p = PALETTES[theme]
    rows = summarise(results)
    marathon_cells = sum(1 for lb in results["scenarios"] if _family(lb) == "marathon")
    long_cells = len(results["scenarios"]) - marathon_cells
    desc = "; ".join(
        f"{NAMES.get(r['name'], r['name'])} {r['met']['marathon']} of "
        f"{r['total']['marathon']} and {r['met']['long']} of {r['total']['long']}"
        + (f", no plan in {sum(r['failed'].values())} cell(s)" if r["failed"] else "")
        for r in rows
    )
    height = 202 + 36 * len(rows)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1040 {height}" '
        f'width="1040" height="{height}" role="img" aria-labelledby="t d">',
        '  <title id="t">The training-plan benchmark</title>',
        f'  <desc id="d">Rules met out of the rulebook\'s bands over {marathon_cells} '
        f"marathon cells and {long_cells} long-course cells: {desc}.</desc>",
        "  <defs>",
        "    <style>",
        '      text { font-family: -apple-system, "Segoe UI", Helvetica, Arial, '
        f"sans-serif; fill: {p['text']}; }}",
        "      .h { font-size: 15px; font-weight: 600; }",
        "      .col { font-size: 12.5px; font-weight: 600; }",
        f"      .s {{ font-size: 11.5px; fill: {p['muted']}; }}",
        "      .row { font-size: 12.5px; }",
        "      .rowb { font-size: 12.5px; font-weight: 700; }",
        f"      .n {{ font-size: 12px; fill: {p['text']}; }}",
        f"      .note {{ font-size: 11px; fill: {p['muted']}; }}",
        f"      .bad {{ font-size: 11px; fill: {p['bad']}; font-weight: 600; }}",
        f"      .track {{ fill: {p['track']}; }}",
        f"      .model {{ fill: {p['model']}; }}",
        f"      .endury {{ fill: {p['endury']}; }}",
        "    </style>",
        "  </defs>",
        f'  <rect width="1040" height="{height}" rx="18" fill="{p["card"]}" '
        f'stroke="{p["edge"]}"/>',
        '  <text x="24" y="38" class="h">Rules met, out of the rulebook\'s bands, '
        f"over {len(_shared_cells(results))} plans</text>",
        '  <text x="24" y="58" class="s">The same brief for every contestant, '
        "complete plans down to the workout steps, nothing about the rules in the "
        "brief. Endury's own rulebook, and its plans are tuned against it.</text>",
        '  <text x="24" y="74" class="s">One draw per AI model (Flash Lite: three to '
        "five). Cost and time are the median for one plan.</text>",
        f'  <text x="176" y="106" class="col">{marathon_cells} marathon plans</text>',
        f'  <text x="356" y="106" class="col">{long_cells} long-course plans</text>',
        '  <text x="552" y="106" class="col">Cost per plan</text>',
        '  <text x="552" y="119" class="s" style="font-size: 10px">marathon · long '
        "course</text>",
        '  <text x="684" y="106" class="col">Time per plan</text>',
        '  <text x="684" y="119" class="s" style="font-size: 10px">marathon · long '
        "course</text>",
        '  <text x="816" y="106" class="col">Most frequent miss (plans)</text>',
    ]
    for i, r in enumerate(rows):
        y = 146 + i * 36
        cls = "endury" if r["name"] == "endury" else "model"
        lines.append(
            f'  <text x="24" y="{y}" class="{"rowb" if cls == "endury" else "row"}">'
            f"{NAMES.get(r['name'], r['name'])}</text>"
        )
        for fam, x in (("marathon", 176), ("long", 356)):
            met, tot, failed = r["met"][fam], r["total"][fam], r["failed"][fam]
            share = met / tot if tot else 0.0
            lines.append(_bar(x, y - 13, share, cls).rstrip("\n"))
            label = f"{met:.0f} / {tot}" if tot else "no plan"
            lines.append(f'  <text x="{x + 106}" y="{y}" class="n">{label}</text>')
            if failed and tot:
                lines.append(
                    f'  <text x="{x + 106}" y="{y + 12}" class="bad">'
                    f"no plan in {failed}</text>"
                )
        lines.append(f'  <text x="552" y="{y}" class="n">{_cost(r)}</text>')
        lines.append(f'  <text x="684" y="{y}" class="n">{_time(r)}</text>')
        lines.append(f'  <text x="816" y="{y}" class="note">{_miss(r)}</text>')
    foot = 146 + 36 * len(rows) - 14
    lines += [
        f'  <line x1="24" y1="{foot}" x2="1016" y2="{foot}" stroke="{p["rule"]}"/>',
        f'  <text x="24" y="{foot + 20}" class="note">A plan is graded on the rules '
        "that apply to it, so totals differ by contestant; an AI model with no plan "
        "for a brief is graded on the plans it wrote.</text>",
        f'  <text x="24" y="{foot + 52}" class="note">The Claude models ran through '
        "Claude Code on a subscription: their cost is an estimate at list price and "
        "their time is not comparable. The brief left session notes optional.</text>",
        f'  <text x="24" y="{foot + 36}" class="note">Rulebook version '
        f"{results['rulebook_version']}, 39 rules with evidence grades and sources "
        f"in bench/rulebook.yaml. Measured {results['measured_on']}.</text>",
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--results", default=str(RESULTS))
    parser.add_argument("--out", default=str(OUT), help="the output path's stem")
    parser.add_argument("--print", action="store_true", help="print the numbers")
    args = parser.parse_args(argv)
    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    if args.print:
        for r in summarise(results):
            print(
                f"{NAMES.get(r['name'], r['name']):24} "
                f"marathon {r['met']['marathon']:.0f}/{r['total']['marathon']} "
                f"long {r['met']['long']:.0f}/{r['total']['long']} "
                f"failed {dict(r['failed'])} cost {_cost(r)} time {_time(r)} "
                f"| {_miss(r)}"
            )
        return
    for theme in PALETTES:
        out = Path(f"{args.out}_{theme}.svg")
        out.write_text(render(results, theme), encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
