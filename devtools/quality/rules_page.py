"""Write the rule page: every rule of the rulebook, by race goal, as Markdown.

``docs/quality/rules.md`` is generated from ``bench/rulebook.yaml``, the
committed scorecard baseline (which goals have scored plans) and the
benchmark's scenario matrix (which goals it asks AI language models for).
``make docs`` runs this; ``tests/devtools/test_rules_page.py`` fails when the
committed page no longer matches.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from bench.rules import FAMILIES, Rule, Rulebook, load_rulebook
from bench.scenarios import MATRIX
from bench.schema import TIERS

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "quality" / "rules.md"
BASELINE = ROOT / "devtools" / "quality" / "baselines" / "planner_matrix.json"
#: Race goals in the page's column order, with the column's label.
GOAL_LABELS: dict[str, str] = {
    "running_5k": "5K",
    "running_10k": "10K",
    "running_half_marathon": "HM",
    "running_marathon": "Mar",
    "sprint_triathlon": "Spr",
    "olympic_triathlon": "Oly",
    "half_ironman": "70.3",
    "ironman": "IM",
    "trail_20k": "T20",
    "trail_50k": "T50",
    "trail_100k": "T100K",
}
GOAL_NAMES: dict[str, str] = {
    "running_5k": "5K",
    "running_10k": "10K",
    "running_half_marathon": "Half marathon",
    "running_marathon": "Marathon",
    "sprint_triathlon": "Sprint triathlon",
    "olympic_triathlon": "Standard-distance triathlon",
    "half_ironman": "Half-distance triathlon",
    "ironman": "Full-distance triathlon",
    "trail_20k": "Trail 20K",
    "trail_50k": "Trail 50K",
    "trail_100k": "Trail 100K",
}


def _cell(text: object) -> str:
    """Text made safe for one Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _range(edges: dict[str, Any]) -> str:
    """One ``low``/``high`` pair in words."""
    low, high = edges.get("low"), edges.get("high")
    if high is None:
        return f"at least {low:g}"
    if low is None:
        return f"at most {high:g}"
    return f"exactly {low:g}" if low == high else f"{low:g} to {high:g}"


def _band_text(spec: dict[str, Any]) -> str:
    """A band as written in the rulebook: one range, or one per tier."""
    if "by_tier" in spec:
        return "; ".join(f"{tier} {_range(spec['by_tier'][tier])}" for tier in TIERS)
    return _range(spec)


def _evidence(rule: Rule) -> str:
    """The evidence column: a grade with its sources, or the maintainer's band."""
    if rule.benchmark:
        count = len(rule.sources)
        return f"{rule.grade}, {count} source{'' if count == 1 else 's'}"
    spec = rule.band_spec
    bands = (spec, *spec.get("by_goal", {}).values())
    return "house band" if any("why" in band for band in bands) else "yes or no"


def _coverage(rulebook: Rulebook) -> list[str]:
    """The table of how many rules, scored plans and benchmark plans each goal has."""
    names = json.loads(BASELINE.read_text(encoding="utf-8"))["cells"]
    cells = {
        goal: sum(
            1
            for name in names
            if name.startswith(f"{goal}_")
            and name.removeprefix(f"{goal}_").split("_")[0] in TIERS
        )
        for goal in GOAL_LABELS
    }
    scenarios = Counter(scenario.goal for scenario in MATRIX)
    lines = [
        "| Race goal | Rules | Also asked of AI models | Scorecard cells | Benchmark plans |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for goal, name in GOAL_NAMES.items():
        covering = [rule for rule in rulebook.rules if goal in rule.goals]
        lines.append(
            f"| {name} | {len(covering)} | {sum(r.benchmark for r in covering)} "
            f"| {cells[goal]} | {scenarios[goal]} |"
        )
    return lines


def _family_table(rules: list[Rule]) -> list[str]:
    """One family's rules, a row each, with a dot under every goal the rule covers."""
    head = ["Rule", "What it measures", "Evidence", "Asked of", *GOAL_LABELS.values()]
    lines = [
        "| " + " | ".join(head) + " |",
        "| --- | --- | --- | --- |" + " :-: |" * len(GOAL_LABELS),
    ]
    for rule in rules:
        dots = ["●" if goal in rule.goals else "" for goal in GOAL_LABELS]
        asked = "AI models too" if rule.benchmark else "Endury only"
        row = [f"`{rule.name}`", _cell(rule.reads), _evidence(rule), asked, *dots]
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _band_rows(rule: Rule) -> list[str]:
    """A rule's bands as the rulebook writes them: its fallback, then each goal's own."""
    spec = rule.band_spec
    own = spec.get("by_goal", {})
    entries: list[tuple[str, dict[str, Any]]] = []
    if "low" in spec or "by_tier" in spec:
        entries.append(("every other goal" if own else "every goal it covers", spec))
    entries += [(GOAL_NAMES[goal], band) for goal, band in own.items()]
    rows = ["| Goal | Band | Why |", "| --- | --- | --- |"]
    for label, band in entries:
        why = " ".join(str(band[key]) for key in ("why", "evidence") if key in band)
        rows.append(f"| {label} | {_band_text(band)} | {_cell(why)} |")
    return rows


def _details(rule: Rule) -> list[str]:
    """One collapsible block: the rule's bands, ramp, weight and note."""
    ramp = "the band's own width" if rule.ramp is None else f"{rule.ramp:g}"
    facts = [f"The distance reaches 1 at {ramp} outside the band."]
    if rule.weight != 1.0:
        facts.append(f"Weight {rule.weight:g} in the scorecard's composite.")
    if rule.missing > 0:
        facts.append(
            f"A benchmark plan without the feature scores {rule.missing:g}; "
            "the scorecard scores 0."
            if rule.benchmark
            else f"A plan without the feature scores {rule.missing:g}."
        )
    if rule.school_specific:
        facts.append("Other coaching schools differ here.")
    if rule.note:
        facts.append(_cell(rule.note))
    return [
        "<details>",
        f"<summary><code>{rule.name}</code>: {_cell(rule.reads)}</summary>",
        "",
        *_band_rows(rule),
        "",
        " ".join(facts),
        "",
        "</details>",
    ]


def render(rulebook: Rulebook) -> str:
    """The whole page for a rulebook.

    Args:
        rulebook: The loaded rulebook.

    Returns:
        The page's Markdown, ending in a newline.
    """
    benchmark = sum(rule.benchmark for rule in rulebook.rules)
    legend = ", ".join(
        f"{label} = {GOAL_NAMES[goal]}"
        for goal, label in GOAL_LABELS.items()
        if label != GOAL_NAMES[goal]
    )
    lines = [
        "# The rules a plan is graded on",
        "",
        "Generated by `make docs` from [`bench/rulebook.yaml`](../../bench/rulebook.yaml)"
        f" (version {rulebook.version}). Do not edit it by hand.",
        "",
        f"{len(rulebook.rules)} rules. {benchmark} read a plan's sessions and steps, so"
        " the benchmark also asks them of an AI language model's plan."
        f" {len(rulebook.rules) - benchmark} read Endury's own plan structure and are"
        " graded only by its scorecard. The scorecard holds Endury's plans to every"
        " rule that covers their goal.",
        "",
        "## Coverage by race goal",
        "",
        *_coverage(rulebook),
        "",
        "A scorecard cell is one goal and tier, scored at several plan lengths. A goal"
        " with no cell has plans that no rule grades yet.",
        "",
        "## Rules by family",
        "",
        f"Columns: {legend}. A dot means the rule covers that goal. Evidence is a"
        " grade (A a trial or meta-analysis, B a cohort or validated model, C two or"
        " more coaching texts, D one source or a convention, S the plan's own"
        " consistency) with its number of sources, or a house band the maintainer"
        " set.",
    ]
    for family in FAMILIES:
        rules = [rule for rule in rulebook.rules if rule.family == family]
        lines += ["", f"### {family.capitalize()}", "", *_family_table(rules)]
    lines += ["", "## Bands", ""]
    for rule in rulebook.rules:
        lines += [*_details(rule), ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    """Write the page from the committed rulebook."""
    PAGE.parent.mkdir(parents=True, exist_ok=True)
    PAGE.write_text(render(load_rulebook()), encoding="utf-8")
    print(f"wrote {PAGE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
