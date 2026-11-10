"""The rulebook: bands with their evidence, and the distance of a reading from a band.

``rulebook.yaml`` is data. Each rule names what it measures, the goals and
tiers it applies to, its band and the evidence behind that band. A
``benchmark`` rule names a reading of the generic plan table, so it can be
asked of any contestant's plan; its band carries an evidence grade and
sources a reader can check. A rule with ``benchmark: false`` reads a
planner's own plan structure and is graded by that planner's quality gate,
never by the benchmark. This module loads and validates the file and turns
readings into per-rule distances.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped, unused-ignore]

from bench.schema import GOALS, TIERS

RULEBOOK_PATH = Path(__file__).with_name("rulebook.yaml")
FAMILIES: tuple[str, ...] = (
    "periodisation",
    "taper",
    "load",
    "intensity",
    "specificity",
    "session",
    "consistency",
)
#: A = meta-analysis or trial, B = cohort or validated modelling, C = at least
#: two independent coaching texts, D = one source or a convention, S = a
#: structural rule of the plan itself, not a coaching band.
GRADES: tuple[str, ...] = ("A", "B", "C", "D", "S")
#: ``ramp: width`` makes the distance reach 1 one band-width outside the band.
WIDTH = "width"
_RULE_KEYS = frozenset(
    {
        "name",
        "family",
        "reading",
        "reads",
        "benchmark",
        "grade",
        "weight",
        "goals",
        "band",
        "ramp",
        "missing",
        "school_specific",
        "note",
        "sources",
    }
)
_RANGE_KEYS = frozenset({"low", "high", "by_tier", "why", "evidence"})


@dataclass(frozen=True)
class Band:
    """An inclusive target range and the span over which the distance ramps to 1."""

    low: float
    high: float
    ramp: float

    def distance(self, value: float) -> float:
        """0 inside the range, rising linearly to 1 at ``ramp`` outside it."""
        if self.low <= value <= self.high:
            return 0.0
        gap = self.low - value if value < self.low else value - self.high
        if self.ramp <= 0:
            return 1.0
        return min(1.0, gap / self.ramp)


@dataclass(frozen=True)
class Rule:
    """One rule of the rulebook.

    ``reading`` and ``grade`` are ``None`` for a rule with ``benchmark``
    false: no reading of the generic plan table exists for it. ``ramp`` is
    ``None`` when the rule ramps over each band's own width.
    """

    name: str
    family: str
    reading: str | None
    reads: str
    grade: str | None
    benchmark: bool
    weight: float
    goals: tuple[str, ...]
    ramp: float | None
    missing: float
    school_specific: bool
    band_spec: Any
    sources: tuple[dict[str, Any], ...]
    note: str

    def band(self, goal: str, tier: str) -> Band | None:
        """The band for a goal and tier, or None when the rule does not apply.

        A goal's own entry under ``by_goal`` wins; without one the rule's
        top-level range applies, and a rule with neither does not grade
        that goal.
        """
        if goal not in self.goals:
            return None
        spec = self.band_spec.get("by_goal", {}).get(goal)
        if spec is None:
            spec = self.band_spec
            if not {"low", "high", "by_tier"} & set(spec):
                return None
        edges = spec["by_tier"][tier] if "by_tier" in spec else spec
        low = float(edges["low"]) if edges.get("low") is not None else -math.inf
        high = float(edges["high"]) if edges.get("high") is not None else math.inf
        return Band(low, high, high - low if self.ramp is None else self.ramp)


def _ranges(band: dict[str, Any]) -> list[dict[str, Any]]:
    """The top-level range of a band and every goal's own range."""
    return [band, *band.get("by_goal", {}).values()]


def _check_band(name: str, band: dict[str, Any], goals: tuple[str, ...]) -> None:
    """Reject a band with an unknown key, goal or tier, or malformed evidence."""
    for goal in band.get("by_goal", {}):
        if goal not in goals:
            raise ValueError(f"rule {name}: band for {goal!r}, which it does not cover")
    for spec in _ranges(band):
        unknown = set(spec) - _RANGE_KEYS - {"by_goal"}
        if unknown:
            raise ValueError(f"rule {name}: unknown band keys {sorted(unknown)}")
        if "by_tier" in spec and set(spec["by_tier"]) != set(TIERS):
            raise ValueError(f"rule {name}: a tiered band names every tier")
        for edges in spec.get("by_tier", {}).values():
            if set(edges) - {"low", "high"}:
                raise ValueError(f"rule {name}: a tier holds only low and high")
        for edges in spec.get("by_tier", {"": spec}).values():
            low, high = edges.get("low"), edges.get("high")
            if low is not None and high is not None and low > high:
                raise ValueError(f"rule {name}: band low {low} is above high {high}")
        for key in ("why", "evidence"):
            text = spec.get(key, "-")
            if not isinstance(text, str) or not text.strip() or "\n" in text.strip():
                raise ValueError(f"rule {name}: a band's {key} is one line of text")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _parse_rule(raw: dict[str, Any]) -> Rule:
    name = str(raw.get("name"))
    benchmark = bool(raw.get("benchmark", True))
    required = {"name", "family", "reads", "band", "ramp"}
    if benchmark:
        required |= {"reading", "grade"}
    missing_keys = required - set(raw)
    if missing_keys:
        raise ValueError(f"rule {name} lacks {sorted(missing_keys)}")
    if set(raw) - _RULE_KEYS:
        raise ValueError(f"rule {name}: unknown keys {sorted(set(raw) - _RULE_KEYS)}")
    if raw["family"] not in FAMILIES:
        raise ValueError(f"rule {name}: unknown family {raw['family']!r}")
    if benchmark and raw["grade"] not in GRADES:
        raise ValueError(f"rule {name}: unknown grade {raw['grade']!r}")
    if not benchmark and ("reading" in raw or "grade" in raw):
        raise ValueError(f"rule {name}: a reading and a grade need benchmark: true")
    goals = tuple(raw.get("goals") or GOALS)
    for goal in goals:
        if goal not in GOALS:
            raise ValueError(f"rule {name}: unknown goal {goal!r}")
    band = raw["band"]
    _check_band(name, band, goals)
    if benchmark and raw["grade"] != "S" and not raw.get("sources"):
        raise ValueError(f"rule {name}: a graded band needs sources")
    ramp = raw["ramp"]
    if ramp != WIDTH and not (_is_number(ramp) and ramp > 0):
        raise ValueError(f"rule {name}: ramp is a positive number or {WIDTH!r}")
    rule = Rule(
        name=name,
        family=raw["family"],
        reading=raw.get("reading"),
        reads=raw["reads"],
        grade=raw.get("grade"),
        benchmark=benchmark,
        weight=float(raw.get("weight", 1.0)),
        goals=goals,
        ramp=None if ramp == WIDTH else float(ramp),
        missing=float(raw.get("missing", 0.0)),
        school_specific=bool(raw.get("school_specific", False)),
        band_spec=band,
        sources=tuple(raw.get("sources") or ()),
        note=str(raw.get("note") or ""),
    )
    for goal in goals:
        for tier in TIERS:
            resolved = rule.band(goal, tier)
            if resolved is None:
                raise ValueError(f"rule {name} covers {goal} but has no band for it")
            if not math.isfinite(resolved.ramp):
                raise ValueError(f"rule {name}: an open band needs a ramp in numbers")
    return rule


@dataclass(frozen=True)
class Rulebook:
    """The loaded rulebook."""

    version: str
    rules: tuple[Rule, ...]
    schools: tuple[dict[str, Any], ...]

    def by_name(self, name: str) -> Rule:
        """The rule called ``name``."""
        return next(r for r in self.rules if r.name == name)

    def applicable(self, goal: str, tier: str) -> list[tuple[Rule, Band]]:
        """Each benchmark rule with a band for this goal and tier, in rulebook order."""
        out: list[tuple[Rule, Band]] = []
        for rule in self.rules:
            band = rule.band(goal, tier)
            if band is not None and rule.benchmark:
                out.append((rule, band))
        return out


def load_rulebook(path: Path = RULEBOOK_PATH) -> Rulebook:
    """Load and validate the rulebook."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = tuple(_parse_rule(r) for r in data["rules"])
    names = [r.name for r in rules]
    if len(names) != len(set(names)):
        raise ValueError("duplicate rule names")
    return Rulebook(str(data["version"]), rules, tuple(data.get("schools") or ()))


def grade(
    rulebook: Rulebook,
    readings: dict[str, float | None],
    goal: str,
    tier: str,
) -> dict[str, float]:
    """Distance per applicable benchmark rule; 0 means inside the band.

    A rule whose reading is ``None`` scores its ``missing`` distance when the
    absent feature is itself the defect (no recovery weeks, no long ride) and
    is left out when the plan simply carries no such data (no steps).
    """
    out: dict[str, float] = {}
    for rule, band in rulebook.applicable(goal, tier):
        value = readings.get(rule.reading or "")
        if value is None:
            if rule.missing > 0:
                out[rule.name] = rule.missing
            continue
        out[rule.name] = band.distance(value)
    return out
