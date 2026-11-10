"""Turning per-rule distances into the numbers a table shows.

Rules met is a count. The composite gives every family the same weight and,
inside a family, every applicable rule the same weight, so a family with
many rules cannot dominate. Samples of the same contestant on the same
scenario are summarised by their median and range, and a rule counts as
robustly met only when every sample met it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from bench.rules import FAMILIES, GRADES, Rulebook

MET = 1e-9


def summarise(rulebook: Rulebook, distances: dict[str, float]) -> dict[str, Any]:
    """Counts by grade and family plus the composite for one plan."""
    by_family: dict[str, list[float]] = defaultdict(list)
    met_by_grade: dict[str, int] = dict.fromkeys(GRADES, 0)
    total_by_grade: dict[str, int] = dict.fromkeys(GRADES, 0)
    met_by_family: dict[str, int] = dict.fromkeys(FAMILIES, 0)
    total_by_family: dict[str, int] = dict.fromkeys(FAMILIES, 0)
    for name, d in distances.items():
        rule = rulebook.by_name(name)
        grade = str(rule.grade)  # a graded distance belongs to a benchmark rule
        by_family[rule.family].append(d)
        total_by_grade[grade] += 1
        total_by_family[rule.family] += 1
        if d <= MET:
            met_by_grade[grade] += 1
            met_by_family[rule.family] += 1
    family_means = [statistics.mean(v) for v in by_family.values() if v]
    strong_met = met_by_grade["A"] + met_by_grade["B"]
    strong_total = total_by_grade["A"] + total_by_grade["B"]
    return {
        "rules_met": sum(1 for d in distances.values() if d <= MET),
        "rules_total": len(distances),
        "strong_met": strong_met,
        "strong_total": strong_total,
        "met_by_grade": met_by_grade,
        "total_by_grade": total_by_grade,
        "met_by_family": met_by_family,
        "total_by_family": total_by_family,
        "composite": round(statistics.mean(family_means), 4) if family_means else None,
        "off_band": {k: round(v, 3) for k, v in distances.items() if v > MET},
    }


def aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Median and range over a contestant's samples on one scenario.

    Args:
        samples: Per-sample result rows, each with ``summary`` and
            ``per_rule`` (absent for a sample that produced no plan).

    Returns:
        The aggregate: how many samples produced a plan, medians and ranges
        of rules met and the composite, and the rules met in every sample.
    """
    graded = [s for s in samples if s.get("summary")]
    if not graded:
        return {"samples": len(samples), "valid": 0}
    met = [s["summary"]["rules_met"] for s in graded]
    strong = [s["summary"]["strong_met"] for s in graded]
    comp = [
        s["summary"]["composite"]
        for s in graded
        if s["summary"]["composite"] is not None
    ]
    always: set[str] | None = None
    for s in graded:
        this = {k for k, v in s["per_rule"].items() if v <= MET}
        always = this if always is None else always & this
    return {
        "samples": len(samples),
        "valid": len(graded),
        "rules_total": graded[0]["summary"]["rules_total"],
        "strong_total": graded[0]["summary"]["strong_total"],
        "rules_met_median": statistics.median(met),
        "rules_met_range": [min(met), max(met)],
        "strong_met_median": statistics.median(strong),
        "strong_met_range": [min(strong), max(strong)],
        "composite_median": round(statistics.median(comp), 4) if comp else None,
        "composite_range": [min(comp), max(comp)] if comp else None,
        "met_in_every_sample": sorted(always or ()),
        "cost_usd": _cost(samples),
    }


def _cost(samples: list[dict[str, Any]]) -> float | None:
    """Summed call cost, or None when the calls carry no metering at all."""
    calls = [s.get("call", {}) for s in samples]
    metered = [c["cost_usd"] for c in calls if c.get("cost_usd") is not None]
    if not metered and any("cost_usd" in c for c in calls):
        return None
    return round(float(sum(metered)), 4)


def ceiling(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """The feasibility ceiling of a scenario across every contestant's samples.

    A rule no contestant met in any sample is flagged for review rather
    than counted against everyone.
    """
    met_by: dict[str, set[str]] = defaultdict(set)
    all_rules: set[str] = set()
    for contestant, samples in rows.items():
        for s in samples:
            for rule, d in (s.get("per_rule") or {}).items():
                all_rules.add(rule)
                if d <= MET:
                    met_by[rule].add(contestant)
    unmet = sorted(r for r in all_rules if not met_by[r])
    return {"rules_nobody_met": unmet, "rules_total": len(all_rules)}
