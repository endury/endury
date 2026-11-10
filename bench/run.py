"""Run the benchmark: local planners by callable, chat models through OpenRouter.

Every model call is metered from OpenRouter's exact ``usage.cost``; a call
whose worst case would cross the ceiling is skipped, never made. Raw answers
are saved before anything is parsed and are reused on the next run, so a
regrade is free and adding samples only calls for the ones missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from bench import VERSION
from bench.brief import ARMS, SYSTEM_PROMPT, build_brief
from bench.load import ZONE_IF
from bench.parse import parse_plan
from bench.readings import read
from bench.report import write_report
from bench.rules import RULEBOOK_PATH, Rulebook, grade, load_rulebook
from bench.scenarios import MATRIX, Scenario
from bench.schema import Plan, Structure
from bench.score import aggregate, ceiling, summarise

Contestant = Callable[[Scenario], Plan]
OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_URL}/models"
DEFAULT_OUT = Path("bench/results")
DEFAULT_RAW = Path("bench/results/raw")
#: Completion budget, reasoning tokens included, by whether the plan has three sports.
MAX_TOKENS = {"running_marathon": 32000, "half_ironman": 64000, "ironman": 64000}
PROMPT_TOKENS_ESTIMATE = 1500


@dataclass
class Call:
    """One model call, as saved under the raw directory."""

    model: str
    ok: bool
    text: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: float | None
    latency_s: float
    provider: str | None = None
    served_model: str | None = None
    error: str | None = None
    brief_sha: str | None = None


def openrouter_prices() -> dict[str, tuple[float, float]]:
    """List price per token (prompt, completion) for every OpenRouter model."""
    import httpx

    data = httpx.get(OPENROUTER_MODELS_URL, timeout=30).json()["data"]
    return {
        m["id"]: (float(m["pricing"]["prompt"]), float(m["pricing"]["completion"]))
        for m in data
    }


def call_model(client: Any, model: str, brief: str, max_tokens: int) -> Call:
    """One chat completion at the model's defaults, retried on transport errors."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": brief},
    ]
    last_error: str | None = None
    for attempt in range(3):
        started = time.perf_counter()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                extra_body={"usage": {"include": True}},
            )
        except Exception as exc:  # every transport error is recorded
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            status = getattr(exc, "status_code", None)
            if status is not None and status not in (429, 500, 502, 503, 504):
                break
            time.sleep(3 * (attempt + 1))
            continue
        latency = time.perf_counter() - started
        choice = response.choices[0]
        usage = response.usage
        extra = getattr(usage, "model_extra", None) or {}
        details = getattr(usage, "completion_tokens_details", None)
        top = getattr(response, "model_extra", None) or {}
        return Call(
            model,
            True,
            choice.message.content or "",
            choice.finish_reason,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(details, "reasoning_tokens", None) if details else None,
            extra.get("cost"),
            latency,
            provider=top.get("provider"),
            served_model=getattr(response, "model", None),
        )
    return Call(model, False, "", None, None, None, None, None, 0.0, error=last_error)


def registration_fingerprint() -> str:
    """A hash of what was fixed before the run: rulebook, brief, scenarios."""
    here = Path(__file__).parent
    h = hashlib.sha256()
    for name in (
        RULEBOOK_PATH.name,
        "brief.py",
        "scenarios.py",
        "readings.py",
        "load.py",
    ):
        h.update((here / name).read_bytes())
    return h.hexdigest()[:16]


class Runner:
    """Grades local contestants and metered model calls into one results file."""

    def __init__(
        self,
        rulebook: Rulebook,
        out: Path,
        raw: Path,
        ceiling_usd: float,
        local: dict[str, Contestant],
        client: Any = None,
        prices: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        self.rulebook = rulebook
        self.out = out
        self.raw = raw
        self.ceiling = ceiling_usd
        self.local = local
        self.client = client
        self.prices = prices or {}
        self.spent = 0.0

    def grade_plan(
        self, scenario: Scenario, plan: Plan | None, structure: Structure | None
    ) -> dict[str, Any]:
        """Readings, distances and the summary for one plan."""
        row: dict[str, Any] = {}
        if structure is not None:
            row["structure"] = asdict(structure)
        if plan is None:
            return row
        readings, facts = read(
            plan, scenario.athlete.hours, scenario.athlete.longest_min
        )
        per_rule = grade(self.rulebook, readings, scenario.goal, scenario.tier)
        row["readings"] = {k: _round(v) for k, v in readings.items()}
        row["facts"] = {k: _round(v) for k, v in facts.items()}
        row["per_rule"] = {k: round(v, 4) for k, v in per_rule.items()}
        row["summary"] = summarise(self.rulebook, per_rule)
        row["weeks"] = [
            {
                "week": w.week,
                "phase": w.phase,
                "mode": w.mode,
                "hours": round(w.hours, 2),
                "sessions": len(w.sessions),
            }
            for w in plan.weeks
        ]
        return row

    def run_local(self, scenario: Scenario, name: str) -> dict[str, Any]:
        """One deterministic local contestant; one that refuses the brief declines."""
        started = time.perf_counter()
        try:
            plan = self.local[name](scenario)
        except ValueError as exc:
            return {"error": f"declined: {exc}"}
        row = self.grade_plan(scenario, plan, None)
        row["call"] = {"latency_s": round(time.perf_counter() - started, 2)}
        return row

    def _raw_path(self, scenario: Scenario, model: str, arm: str, sample: int) -> Path:
        return (
            self.raw / scenario.label / model.replace("/", "_") / arm / f"{sample}.json"
        )

    def _load_or_call(
        self, path: Path, model: str, brief: str, max_tokens: int
    ) -> Call | None:
        if path.exists():
            return Call(**json.loads(path.read_text(encoding="utf-8")))
        if self.client is None:
            print(f"SKIP {model}: no client (dry run) and no saved answer at {path}")
            return None
        p_in, p_out = self.prices.get(model, (0.0, 0.0))
        projected = PROMPT_TOKENS_ESTIMATE * p_in + max_tokens * p_out
        if self.spent + projected > self.ceiling:
            print(
                f"SKIP {model}: worst case ${projected:.2f} would cross the "
                f"${self.ceiling:.2f} ceiling (spent ${self.spent:.2f})"
            )
            return None
        print(
            f"-> {model} {path.parent.parent.name} (worst case ${projected:.2f}) ...",
            end=" ",
            flush=True,
        )
        call = call_model(self.client, model, brief, max_tokens)
        call.brief_sha = hashlib.sha256(brief.encode()).hexdigest()[:16]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(call), indent=1), encoding="utf-8")
        self.spent += call.cost_usd or 0.0
        print(
            f"{'ok' if call.ok else 'FAILED'} {call.latency_s:.0f}s "
            f"out={call.completion_tokens} reasoning={call.reasoning_tokens} "
            f"cost=${call.cost_usd or 0:.3f} finish={call.finish_reason} "
            f"(spent ${self.spent:.2f})"
        )
        return call

    def run_model(
        self, scenario: Scenario, model: str, arm: str, sample: int
    ) -> dict[str, Any]:
        """One model sample on one scenario, saved or fresh."""
        brief = build_brief(scenario, self.rulebook, arm)
        call = self._load_or_call(
            self._raw_path(scenario, model, arm, sample),
            model,
            brief,
            MAX_TOKENS[scenario.goal],
        )
        if call is None:
            return {"skipped": "ceiling or dry run"}
        if not call.ok:
            return {"error": call.error, "call": _call_view(call)}
        plan, structure = parse_plan(
            call.text, scenario.goal, scenario.tier, scenario.weeks
        )
        if plan is None or not structure.valid:
            row = self.grade_plan(scenario, None, structure)
            row["error"] = ("incomplete plan: " if plan else "no plan: ") + "; ".join(
                structure.defects[:3]
            )
        else:
            row = self.grade_plan(scenario, plan, structure)
        row["call"] = _call_view(call)
        return row

    def run(
        self,
        scenarios: list[Scenario],
        contestants: list[str],
        n: int,
        arm: str,
    ) -> dict[str, Any]:
        """Every contestant on every scenario, ``n`` samples for the models."""
        results: dict[str, Any] = {}
        for scenario in scenarios:
            print(f"\n=== {scenario.label} ===")
            rows: dict[str, list[dict[str, Any]]] = {}
            for name in contestants:
                if name in self.local:
                    rows[name] = [self.run_local(scenario, name)]
                else:
                    rows[name] = [
                        self.run_model(scenario, name, arm, i) for i in range(n)
                    ]
                for i, row in enumerate(rows[name]):
                    row["sample"] = i
                    row["arm"] = arm if name not in self.local else "local"
                    s = row.get("summary")
                    if s:
                        print(
                            f"   {name:36} #{i} met {s['rules_met']}/"
                            f"{s['rules_total']} strong {s['strong_met']}/"
                            f"{s['strong_total']} composite {s['composite']}"
                        )
                    else:
                        print(
                            f"   {name:36} #{i} no plan: "
                            f"{row.get('error') or row.get('skipped') or 'unparsable'}"
                        )
            results[scenario.label] = {
                "scenario": {
                    "goal": scenario.goal,
                    "tier": scenario.tier,
                    "weeks": scenario.weeks,
                    "athlete": asdict(scenario.athlete),
                    "weekly_cap_hours": scenario.weekly_cap_hours,
                    "tags": list(scenario.tags),
                },
                "contestants": {
                    name: {"samples": samples, "aggregate": aggregate(samples)}
                    for name, samples in rows.items()
                },
                "ceiling": ceiling(rows),
            }
        return results


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _call_view(call: Call) -> dict[str, Any]:
    view = asdict(call)
    view.pop("text")
    view["latency_s"] = round(call.latency_s, 1)
    return view


def merge_results(target: Path, fresh: dict[str, Any]) -> dict[str, Any]:
    """Fresh results merged over an earlier results file, contestant by contestant."""
    merged: dict[str, Any] = {}
    if target.exists():
        merged = json.loads(target.read_text(encoding="utf-8")).get("scenarios", {})
    for label, cell in fresh.items():
        old = merged.get(label)
        if old:
            old["contestants"].update(cell["contestants"])
        else:
            merged[label] = cell
        merged[label]["ceiling"] = ceiling(
            {name: c["samples"] for name, c in merged[label]["contestants"].items()}
        )
    return merged


def main(
    argv: list[str] | None = None, local: dict[str, Contestant] | None = None
) -> None:
    """Command-line entry point; ``local`` maps contestant names to planners."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--contestants",
        required=True,
        help="comma-separated: local names and OpenRouter model ids",
    )
    parser.add_argument(
        "--scenarios",
        default=None,
        help="comma-separated scenario labels or tags (default: all)",
    )
    parser.add_argument(
        "--n", type=int, default=1, help="samples per model per scenario"
    )
    parser.add_argument("--arm", default="blind", choices=ARMS)
    parser.add_argument(
        "--ceiling",
        type=float,
        default=2.0,
        help="hard spend ceiling in USD for this run",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="OpenRouter key; without it only saved answers are graded",
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW))
    args = parser.parse_args(argv)

    local = local or {}
    contestants = args.contestants.split(",")
    wanted = args.scenarios.split(",") if args.scenarios else None
    scenarios = [
        s
        for s in MATRIX
        if wanted is None or s.label in wanted or any(t in wanted for t in s.tags)
    ]
    for label in wanted or ():
        if label not in {s.label for s in MATRIX} and not any(
            label in s.tags for s in MATRIX
        ):
            sys.exit(
                f"no scenario or tag {label!r}; known: "
                + ", ".join(s.label for s in MATRIX)
            )
    client = None
    prices: dict[str, tuple[float, float]] = {}
    if any(c not in local for c in contestants) and args.api_key:
        from openai import OpenAI

        client = OpenAI(
            base_url=OPENROUTER_URL, api_key=args.api_key, timeout=900, max_retries=0
        )
        prices = openrouter_prices()
    rulebook = load_rulebook()
    runner = Runner(
        rulebook,
        Path(args.out),
        Path(args.raw_dir),
        args.ceiling,
        local,
        client,
        prices,
    )
    fresh = runner.run(scenarios, contestants, args.n, args.arm)
    runner.out.mkdir(parents=True, exist_ok=True)
    target = runner.out / "results.json"
    payload = {
        "bench_version": VERSION,
        "rulebook_version": rulebook.version,
        "registration": registration_fingerprint(),
        "measured_on": date.today().isoformat(),
        "spent_usd_this_run": round(runner.spent, 4),
        "zone_if": ZONE_IF,
        "scenarios": merge_results(target, fresh),
    }
    target.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    write_report(payload, rulebook, runner.out / "README.md")
    print(f"\nwrote {target}; exact spend this run ${runner.spent:.3f}")


if __name__ == "__main__":
    main()
