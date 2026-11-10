import json
from dataclasses import asdict
from types import SimpleNamespace

from bench.brief import build_brief
from bench.rules import load_rulebook
from bench.run import Call, Runner, call_model, merge_results, registration_fingerprint
from bench.scenarios import by_label
from tests.bench.conftest import marathon_plan

SC = by_label("marathon_18wk_moderate")


def _answer(weeks: int) -> str:
    plan = marathon_plan(weeks)
    return json.dumps(
        {
            "weeks": [
                {
                    "week": w.week,
                    "phase": w.phase,
                    "mode": w.mode,
                    "sessions": [
                        {
                            "day": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
                                s.day
                            ],
                            "sport": s.sport,
                            "type": s.kind,
                            "minutes": s.minutes,
                            "steps": [
                                [st.zone, round(st.minutes, 1)] for st in s.steps
                            ],
                            "notes": s.notes,
                        }
                        for s in w.sessions
                    ],
                }
                for w in plan.weeks
            ]
        }
    )


class _FakeClient:
    """Answers every call with a tidy plan and OpenRouter-shaped usage."""

    def __init__(self, weeks: int) -> None:
        self.calls = 0
        self.text = _answer(weeks)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        usage = SimpleNamespace(
            prompt_tokens=900,
            completion_tokens=4000,
            completion_tokens_details=None,
            model_extra={"cost": 0.01},
        )
        choice = SimpleNamespace(
            message=SimpleNamespace(content=self.text), finish_reason="stop"
        )
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model_extra={"provider": "Fake"},
            model=kwargs["model"],
        )


def test_local_contestant_and_model_samples_are_graded_and_reused(tmp_path):
    rb = load_rulebook()
    client = _FakeClient(SC.weeks)
    runner = Runner(
        rb,
        tmp_path / "out",
        tmp_path / "raw",
        ceiling_usd=1.0,
        local={"toy": lambda sc: marathon_plan(sc.weeks)},
        client=client,
        prices={"fake/model": (1e-7, 1e-6)},
    )
    results = runner.run([SC], ["toy", "fake/model"], n=2, arm="blind")
    cell = results[SC.label]
    assert cell["contestants"]["toy"]["samples"][0]["summary"]["rules_total"] > 20
    model = cell["contestants"]["fake/model"]
    assert model["aggregate"]["valid"] == 2
    assert client.calls == 2
    assert model["samples"][0]["call"]["provider"] == "Fake"
    assert runner.spent == 0.02
    # a second run reuses the saved answers and calls nothing
    runner2 = Runner(
        rb,
        tmp_path / "out",
        tmp_path / "raw",
        1.0,
        {},
        client,
        {"fake/model": (1e-7, 1e-6)},
    )
    runner2.run([SC], ["fake/model"], n=2, arm="blind")
    assert client.calls == 2
    assert runner2.spent == 0


def test_ceiling_skips_a_call_before_making_it(tmp_path):
    rb = load_rulebook()
    client = _FakeClient(SC.weeks)
    runner = Runner(
        rb,
        tmp_path / "out",
        tmp_path / "raw",
        ceiling_usd=0.001,
        local={},
        client=client,
        prices={"fake/model": (1e-7, 1e-4)},
    )
    results = runner.run([SC], ["fake/model"], n=1, arm="blind")
    assert results[SC.label]["contestants"]["fake/model"]["samples"][0]["skipped"]
    assert client.calls == 0


def test_a_local_contestant_that_refuses_the_brief_declines(tmp_path):
    def refuse(_scenario):
        raise ValueError("horizon below minimum")

    runner = Runner(
        load_rulebook(),
        tmp_path / "out",
        tmp_path / "raw",
        ceiling_usd=0.0,
        local={"planner": refuse},
        client=None,
        prices={},
    )
    cell = runner.run([SC], ["planner"], n=1, arm="blind")[SC.label]["contestants"]
    assert cell["planner"]["samples"][0]["error"] == "declined: horizon below minimum"
    assert cell["planner"]["aggregate"]["valid"] == 0


def test_transport_errors_are_recorded_not_raised():
    class Broken:
        chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: (_ for _ in ()).throw(RuntimeError("down"))
            )
        )

    call = call_model(Broken(), "fake/model", "brief", 100)
    assert not call.ok
    assert call.error.startswith("RuntimeError")


def test_merge_keeps_other_contestants_and_fingerprint_is_stable(tmp_path):
    target = tmp_path / "results.json"
    x = {"samples": [{"per_rule": {"r": 0.0, "q": 1.0}}]}
    y = {"samples": [{"per_rule": {"r": 0.5, "q": 0.5}}]}
    target.write_text(
        json.dumps({"scenarios": {"a": {"contestants": {"x": x}, "ceiling": {}}}})
    )
    fresh = {
        "a": {"contestants": {"y": y}, "ceiling": {}},
        "b": {"contestants": {}, "ceiling": {}},
    }
    merged = merge_results(target, fresh)
    assert set(merged["a"]["contestants"]) == {"x", "y"}
    assert merged["a"]["ceiling"]["rules_nobody_met"] == ["q"]  # x met r; nobody met q
    assert "b" in merged
    assert registration_fingerprint() == registration_fingerprint()
    assert Call(**asdict(Call("m", True, "t", "stop", 1, 2, 0, 0.0, 1.0))).model == "m"
    assert "steps" in build_brief(SC)


def test_an_incomplete_plan_is_recorded_with_its_defects_not_graded(tmp_path):
    client = _FakeClient(18)
    client.text = json.dumps(
        {
            "weeks": [
                {
                    "week": 1,
                    "phase": "base",
                    "mode": "load",
                    "sessions": [
                        {
                            "day": "mon",
                            "sport": "run",
                            "type": "easy",
                            "minutes": 45,
                            "steps": [[2, 45]],
                        }
                    ],
                }
            ]
        }
    )
    runner = Runner(load_rulebook(), tmp_path, tmp_path / "raw", 1.0, {}, client, {})
    row = runner.run_model(SC, "fake/model", "blind", 0)
    assert "summary" not in row
    assert row["error"].startswith("incomplete plan: ")
    assert "weeks delivered: 1 of 18" in row["error"]
    assert row["structure"]["weeks_delivered"] == 1
