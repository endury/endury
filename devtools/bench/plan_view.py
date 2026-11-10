"""Draw whole benchmark plans as one HTML page, every week and every session.

Usage::

    poetry run python -m devtools.bench.plan_view --scenarios ironman
    poetry run python -m devtools.bench.plan_view --scenarios ironman_32wk_competitive --contestants endury,anthropic/claude-opus-5.5

Endury's plans are built by today's planner; an AI model's plans are its saved
answers under ``bench/results/raw/``. Both are parsed and graded as the
benchmark grades them, so the zone strips show what the rulebook reads. An
Endury session's steps show the workout its card carries, targets included.
The page is written outside the repo by default.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from bench.load import seed_fitness, trajectory
from bench.parse import parse_plan
from bench.rules import load_rulebook
from bench.run import DEFAULT_RAW, Call, Runner
from bench.scenarios import MATRIX, Scenario
from bench.schema import Plan, Step
from devtools.bench.endury_contestant import export, plan_request
from devtools.bench.figure import NAMES
from devtools.planner._shared.sessions import build_synthetic_resolver, card_text
from endury.planner.activity.materialize import assignment_target_tss
from endury.planner.activity.registry import ensure_registry_loaded, get_activity
from endury.planner.activity.sets import set_occurrences, with_set
from endury.planner.activity.steps import (
    DEFAULT_SWIM_CSS_SEC_PER_100M,
    build_table_steps,
)
from endury.planner.pipeline import PlanResult, generate_plan

TEMPLATE = Path(__file__).with_name("plan_view.html")
OUT = Path(tempfile.gettempdir()) / "endury_plan_view.html"
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _fmt(step: Step) -> str:
    return f"Z{step.zone} {step.minutes:g} min"


def steps_text(steps: list[Step]) -> str:
    """The steps as the author wrote them, repeat blocks folded back to ``Nx (on / off)``."""
    parts: list[str] = []
    i = 0
    while i < len(steps):
        if steps[i].role == "straight":
            parts.append(_fmt(steps[i]))
            i += 1
            continue
        size = 2 if i + 1 < len(steps) and steps[i + 1].role == "off" else 1
        unit = steps[i : i + size]
        reps = 1
        while steps[i + reps * size : i + (reps + 1) * size] == unit:
            reps += 1
        parts.append(f"{reps}x ({' / '.join(_fmt(s) for s in unit)})")
        i += reps * size
    return " → ".join(parts)


def endury_cards(result: PlanResult) -> list[str]:
    """Every session's workout card, in the order the export lists the sessions."""
    ensure_registry_loaded()
    resolver = build_synthetic_resolver()
    css = resolver.swim_css_sec_per_100m or DEFAULT_SWIM_CSS_SEC_PER_100M
    cards = []
    occurrences = set_occurrences(result.assignments)
    for a in sorted(result.assignments, key=lambda a: (a.date, a.slot_id)):
        activity = with_set(get_activity(a.activity_key), occurrences[a.slot_id])
        tss = assignment_target_tss(activity, a.target_hours, resolver)
        workout = activity.build(tss, resolver=resolver)
        rows = build_table_steps(workout.steps, swim_css_sec_per_100m=css)
        minutes = sum(r.duration_s * r.iterations for r in rows) / 60.0
        card = card_text(rows)
        if abs(minutes - a.target_hours * 60.0) >= 1.0:
            card += f" (card {minutes:.0f} min, sized {a.target_hours * 60.0:.0f} min)"
        cards.append(card)
    return cards


def plan_weeks(plan: Plan, cards: list[str] | None = None) -> list[dict[str, Any]]:
    """The plan's weeks in the page's shape; ``cards`` replace the steps when given."""
    texts = iter(cards) if cards is not None else None
    return [
        {
            "week": w.week,
            "phase": w.phase,
            "mode": w.mode,
            "sessions": [
                {
                    "day": DAYS[s.day] if s.day is not None else "any",
                    "sport": s.sport,
                    "type": s.kind,
                    "minutes": round(s.minutes),
                    "metres": s.metres,
                    "zones": [[st.zone, round(st.minutes, 2)] for st in s.steps],
                    "steps": next(texts) if texts else steps_text(s.steps),
                    "notes": s.notes,
                }
                for s in w.sessions
            ],
        }
        for w in plan.weeks
    ]


def _raw_path(raw: Path, scenario: Scenario, model: str, arm: str, sample: int) -> Path:
    return raw / scenario.label / model.replace("/", "_") / arm / f"{sample}.json"


def entry(
    runner: Runner, scenario: Scenario, name: str, raw: Path, arm: str, sample: int
) -> dict[str, Any]:
    """One contestant's plan on one scenario, graded, or the reason there is none."""
    head: dict[str, Any] = {
        "scenario": scenario.label,
        "about": asdict(scenario.athlete) | {"weeks": scenario.weeks},
        "cap": scenario.weekly_cap_hours,
        "name": NAMES.get(name, name),
        "cost": 0.0 if name == "endury" else None,
    }
    cards = None
    if name == "endury":
        result = generate_plan(plan_request(scenario))
        plan, structure = export(result, scenario), None
        cards = endury_cards(result)
    else:
        path = _raw_path(raw, scenario, name, arm, sample)
        if not path.exists():
            return head | {"error": f"no saved answer at {path}"}
        call = Call(**json.loads(path.read_text(encoding="utf-8")))
        head["cost"] = call.cost_usd
        if not call.ok:
            return head | {"error": f"the call failed: {call.error}"}
        parsed, structure = parse_plan(
            call.text, scenario.goal, scenario.tier, scenario.weeks
        )
        if parsed is None or not structure.valid:
            return head | {"error": "no plan: " + "; ".join(structure.defects[:3])}
        plan = parsed
    row = runner.grade_plan(scenario, plan, structure)
    traj = trajectory(plan, seed_fitness(plan, scenario.athlete.hours))
    return head | {
        "ctl": [round(v, 1) for v in traj.ctl],
        "atl": [round(v, 1) for v in traj.atl],
        "summary": row["summary"],
        "facts": row["facts"],
        "off": {k: v for k, v in row["per_rule"].items() if v},
        "weeks": plan_weeks(plan, cards),
    }


def saved_models(raw: Path, scenario: Scenario, arm: str) -> list[str]:
    """Every AI model with a saved answer on the scenario."""
    folder = raw / scenario.label
    if not folder.is_dir():
        return []
    return sorted(
        d.name.replace("_", "/", 1) for d in folder.iterdir() if (d / arm).is_dir()
    )


def render(entries: list[dict[str, Any]]) -> str:
    """The self-contained page."""
    return TEMPLATE.read_text(encoding="utf-8").replace(
        "/*DATA*/null", json.dumps(entries).replace("</", "<\\/")
    )


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--scenarios",
        required=True,
        help="comma-separated labels, or any part of one (ironman, marathon_18wk)",
    )
    parser.add_argument(
        "--contestants",
        default="",
        help="comma-separated; default endury and every saved AI model",
    )
    parser.add_argument("--arm", default="blind")
    parser.add_argument("--sample", type=int, default=0)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(argv)
    wanted = [w for w in args.scenarios.split(",") if w]
    scenarios = [s for s in MATRIX if any(w in s.label for w in wanted)]
    if not scenarios:
        parser.error(f"no scenario matches {args.scenarios!r}")
    raw = Path(args.raw_dir)
    runner = Runner(load_rulebook(), raw, raw, 0.0, {})
    entries: list[dict[str, Any]] = []
    for scenario in scenarios:
        names = [c for c in args.contestants.split(",") if c] or [
            "endury",
            *saved_models(raw, scenario, args.arm),
        ]
        entries.extend(
            entry(runner, scenario, name, raw, args.arm, args.sample) for name in names
        )
    Path(args.out).write_text(render(entries), encoding="utf-8")
    print(f"wrote {args.out}: {len(scenarios)} scenarios, {len(entries)} plans")


if __name__ == "__main__":
    main()
