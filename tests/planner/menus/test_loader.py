"""Tests for endury.planner.sessions.menus.loader (YAML -> blueprint)."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest
import yaml

import endury.planner.sessions.menus as menus
from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planner.sessions.menus import loader
from endury.planner.sessions.menus.contracts import MenuContractError
from endury.planner.sessions.menus.loader import (
    MenuYamlError,
    blueprint_from_yaml,
    load_blueprint,
    maintenance_from_yaml,
)
from endury.planner.sessions.menus.patterns import scaled
from endury.planner.sessions.menus.session_spec import SessionSpec
from endury.planner.sessions.menus.types import MenuContract, Weekday

# A triathlon-maintenance document that uses every key the loader reads.
_DOC: dict = {
    "goal": "triathlon_maintenance",
    "progressions": ["tri_long_bike"],
    "menus": {
        "maintain/load": {
            "contract": {
                "must_contain": ["bike_long"],
                "must_not_contain": ["run_5k_race_pace"],
                "min_sport_sessions": {"cycling": 1},
                "weekly_quality_sessions": [0, 1],
                "bricks_per_week": [0, 1],
            },
            "slots": [
                {
                    "name": "sat_long_bike",
                    "day": "sat",
                    "activity": "bike_long",
                    "minutes": {"scaled": [90, 120]},
                    "progression": "tri_long_bike",
                    "key": 0.5,
                    "why": "Long ride.",
                },
                {
                    "name": "sat_brick",
                    "day": "sat",
                    "activity": "run_brick_short",
                    "minutes": [15, 20],
                    "brick": True,
                },
                {
                    "name": "tue_swim",
                    "day": "tue",
                    "activity": "swim_css",
                    "rotation": ["swim_css", "swim_threshold"],
                    "metres": {"basic": [1500, 1800], "competitive": [2000, 2400]},
                    "optional": True,
                },
                {
                    "name": "thu_run",
                    "day": "thu",
                    "why": "Slot why.",
                    "variants": [
                        {
                            "activity": "run_easy",
                            "minutes": [30, 40],
                            "window": [0, 0.5],
                            "group": "thu_run",
                            "why": "Variant why.",
                        },
                        {
                            "activity": "run_tempo",
                            "minutes": [30, 40],
                            "window": [0.5, 1.0],
                            "group": "thu_run",
                        },
                        {
                            "activity": "run_strides",
                            "minutes": [30, 36],
                            "group": "thu_run",
                            "race_sim": True,
                        },
                    ],
                },
            ],
        },
        "maintain/recovery": {
            "slots": [
                {
                    "name": "wed_easy",
                    "day": "wed",
                    "activity": "run_easy",
                    "minutes": [20, 30],
                    "eases": "tri_long_bike",
                }
            ]
        },
    },
}

_LOAD = ("menus", "maintain/load")
_SLOTS = (*_LOAD, "slots")


def _text(doc: object) -> str:
    return yaml.safe_dump(doc, sort_keys=False)


def _mutated(path: tuple, value: object) -> dict:
    """Copy of _DOC with the value at *path* replaced (``...`` deletes it)."""
    doc = copy.deepcopy(_DOC)
    node = doc
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return doc


def _slot(index: int, **changes: object) -> Callable[[], dict]:
    """Copy of _DOC with load-menu slot *index* updated (``...`` deletes a key)."""

    def build() -> dict:
        doc = copy.deepcopy(_DOC)
        slot = doc["menus"]["maintain/load"]["slots"][index]
        for key, value in changes.items():
            if value is ...:
                del slot[key]
            else:
                slot[key] = value
        return doc

    return build


def _variant(index: int, **changes: object) -> Callable[[], dict]:
    """Copy of _DOC with variant *index* of the multi-variant slot updated."""

    def build() -> dict:
        doc = copy.deepcopy(_DOC)
        variant = doc["menus"]["maintain/load"]["slots"][3]["variants"][index]
        for key, value in changes.items():
            if value is ...:
                del variant[key]
            else:
                variant[key] = value
        return doc

    return build


def test_every_key_builds_the_matching_dataclass_field():
    blueprint = blueprint_from_yaml(_text(_DOC), "t.yaml")

    assert blueprint.goal is GoalType.TRIATHLON_MAINTENANCE
    assert list(blueprint.phases) == [
        (PhaseType.MAINTAIN, "load"),
        (PhaseType.MAINTAIN, "recovery"),
    ]
    load = blueprint.phases[(PhaseType.MAINTAIN, "load")]
    assert (load.phase, load.mode_key) == (PhaseType.MAINTAIN, "load")

    contract = load.contract
    assert contract.must_contain == frozenset({ActivityKey.BIKE_LONG})
    assert contract.must_not_contain == frozenset({ActivityKey.RUN_5K_RACE_PACE})
    assert dict(contract.min_sport_sessions) == {SportType.CYCLING: 1}
    assert contract.weekly_quality_sessions == (0, 1)
    assert contract.bricks_per_week == (0, 1)

    bike, brick, swim, run = load.slots
    assert (bike.name, bike.day, bike.rationale) == ("sat_long_bike", Weekday.SAT, None)
    (long_ride,) = bike.variants
    assert long_ride.activity_key is ActivityKey.BIKE_LONG
    assert long_ride.bounds == scaled(90, 120)
    assert long_ride.progression is not None
    assert long_ride.progression.name == "tri_long_bike"
    assert long_ride.rationale is not None
    assert long_ride.rationale.athlete_why == "Long ride."
    assert long_ride.frac == (0.0, 1.0)
    assert long_ride.slot_group is None
    assert long_ride.rotation == ()
    assert not long_ride.is_brick_pair
    assert not long_ride.race_sim
    assert long_ride.key == 0.5
    assert not long_ride.optional

    assert brick.variants[0].bounds == (15, 20)
    assert brick.variants[0].is_brick_pair
    assert brick.variants[0].rationale is None
    assert brick.variants[0].key == 0.0

    assert swim.variants[0].rotation == (
        ActivityKey.SWIM_CSS,
        ActivityKey.SWIM_THRESHOLD,
    )
    assert swim.variants[0].bounds == {
        AmbitionLevel.BASIC: (1500, 1800),
        AmbitionLevel.COMPETITIVE: (2000, 2400),
    }
    assert swim.variants[0].optional

    assert run.rationale is not None
    assert run.rationale.athlete_why == "Slot why."
    easy, tempo, sim = run.variants
    assert easy.frac == (0.0, 0.5)
    assert isinstance(easy.frac[0], float)
    assert easy.slot_group == "thu_run"
    assert easy.rationale is not None
    assert easy.rationale.athlete_why == "Variant why."
    assert tempo.activity_key is ActivityKey.RUN_TEMPO
    assert tempo.rationale is None
    assert sim.race_sim

    assert long_ride.eases is None

    recovery = blueprint.phases[(PhaseType.MAINTAIN, "recovery")]
    assert recovery.contract == MenuContract()
    (eased,) = recovery.slots[0].variants
    assert eased.eases is long_ride.progression


def test_progressions_and_contract_are_optional():
    doc = _mutated(("progressions",), ...)
    del doc["menus"]["maintain/load"]["slots"][0]["progression"]
    del doc["menus"]["maintain/recovery"]["slots"][0]["eases"]
    del doc["menus"]["maintain/load"]["contract"]
    blueprint = blueprint_from_yaml(_text(doc), "t.yaml")
    load = blueprint.phases[(PhaseType.MAINTAIN, "load")]
    assert load.contract == MenuContract()
    assert load.slots[0].variants[0].progression is None


def test_offered_names_the_tiers_a_new_plan_may_use():
    offering = blueprint_from_yaml(
        _text(_mutated(("offered",), ["basic", "competitive"])), "t.yaml"
    )

    assert offering.offered == {AmbitionLevel.BASIC, AmbitionLevel.COMPETITIVE}
    assert blueprint_from_yaml(_text(_DOC), "t.yaml").offered == frozenset()


def test_yaml_anchors_share_a_slot_list():
    text = _text(_DOC).replace(
        "  maintain/recovery:\n    slots:\n", "  maintain/recovery:\n    slots: &rec\n"
    )
    assert "&rec" in text
    blueprint = blueprint_from_yaml(text, "t.yaml")
    assert (
        blueprint.phases[(PhaseType.MAINTAIN, "recovery")].slots[0].name == "wed_easy"
    )


@pytest.mark.parametrize(
    ("build", "message"),
    [
        # document level
        (lambda: ["not", "a", "mapping"], "t.yaml: expected a mapping"),
        (lambda: _mutated(("owner",), "x"), "unknown key(s) ['owner']"),
        (lambda: _mutated(("menus",), ...), "missing key(s) ['menus']"),
        (lambda: _mutated(("offered",), "basic"), "offered: expected a list"),
        (lambda: _mutated(("offered",), ["elite"]), "offered[0]: unknown"),
        (lambda: _mutated(("goal",), "marathon"), "unknown GoalType 'marathon'"),
        (lambda: _mutated(("progressions",), "tri_long_bike"), "expected a list"),
        (
            lambda: _mutated(("progressions",), [1]),
            "progressions[0]: expected a string",
        ),
        (lambda: _mutated(("menus",), []), "t.yaml:menus: expected a mapping"),
        # menu level
        (
            lambda: {**_DOC, "menus": {"maintain": _DOC["menus"]["maintain/load"]}},
            "menu key must be <phase>/<mode_key>",
        ),
        (
            lambda: {**_DOC, "menus": {"maint/load": _DOC["menus"]["maintain/load"]}},
            "unknown PhaseType 'maint'",
        ),
        (lambda: _mutated((*_LOAD, "slot"), []), "unknown key(s) ['slot']"),
        (lambda: _mutated(_SLOTS, {"name": "x"}), "slots: expected a list"),
        # slot level
        (lambda: _mutated((*_SLOTS,), ["x"]), "slots[0]: expected a mapping"),
        (_slot(1, minute=[1, 2]), "slots[1]: unknown key(s) ['minute']"),
        (_slot(1, name=...), "slots[1]: missing key(s) ['name']"),
        (_slot(1, activity=...), "missing key(s) ['activity']"),
        (_slot(1, name=5), "slots[1].name: expected a string"),
        (_slot(1, day="saturday"), "slots[1].day: expected one of"),
        (_slot(1, day=6), "slots[1].day: expected one of"),
        (_slot(3, variants=[]), "slots[3]: Slot 'thu_run' has no variants"),
        (_slot(3, variants={"activity": "run_easy"}), "variants: expected a list"),
        (_slot(3, activity="run_easy"), "slots[3]: unknown key(s) ['activity']"),
        (_slot(3, why=["x"]), "slots[3].why: expected a string"),
        # variant level
        (_variant(0, activity=...), "variants[0]: missing key(s) ['activity']"),
        (_variant(0, pace=1), "variants[0]: unknown key(s) ['pace']"),
        (_slot(1, activity="run_jog"), "slots[1].activity: unknown ActivityKey"),
        (_slot(2, rotation="swim_css"), "slots[2].rotation: expected a list"),
        (_slot(2, rotation=["swim_fast"]), "rotation[0]: unknown ActivityKey"),
        # unit law: swim in metres, everything else in minutes
        (_slot(2, minutes=[30, 40]), "swim_css is authored in metres, not minutes"),
        (
            _slot(1, metres=[500, 800]),
            "run_brick_short is authored in minutes, not metres",
        ),
        (_slot(1, minutes=...), "slots[1]: missing minutes"),
        # bounds
        (_slot(1, minutes=[15]), "minutes: expected [lo, hi]"),
        (_slot(1, minutes=[15, 20.5]), "minutes: expected an integer"),
        (_slot(1, minutes=[True, 20]), "minutes: expected an integer"),
        (_slot(1, minutes="15-20"), "minutes: expected a mapping"),
        (_slot(1, minutes={"elite": [15, 20]}), "unknown key(s) ['elite']"),
        (
            _slot(1, minutes={"scaled": [15, 20], "basic": [10, 15]}),
            "scaled cannot be combined with tiers",
        ),
        (_slot(1, minutes={"scaled": [15]}), "minutes.scaled: expected [lo, hi]"),
        (_slot(1, minutes={"basic": [15, "x"]}), "minutes.basic: expected an integer"),
        (_slot(1, minutes={}), "slots[1]: Variant run_brick_short has empty per-tier"),
        (_slot(1, minutes=[20, 15]), "slots[1]: Variant run_brick_short has bounds"),
        # the other variant keys
        (
            _slot(0, progression="long_bike"),
            "'long_bike' is not listed under progressions",
        ),
        (_slot(0, progression=1), "progression: expected a string"),
        (
            _slot(1, eases="long_bike"),
            "slots[1].eases: 'long_bike' is not listed under progressions",
        ),
        (_slot(1, eases=["tri_long_bike"]), "slots[1].eases: expected a string"),
        (_variant(0, window=[0.5]), "window: expected [start, end] numbers"),
        (_variant(0, window=[0, "x"]), "window: expected [start, end] numbers"),
        (_variant(0, window=[False, 0.5]), "window: expected [start, end] numbers"),
        (_variant(0, window=[0.5, 0.2]), "invalid frac window"),
        (_variant(0, group=1), "group: expected a string"),
        (_slot(1, brick=1), "brick: expected true or false"),
        (_variant(2, race_sim="true"), "race_sim: expected true or false"),
        (
            _variant(0, cadence_weeks=1),
            "cadence_weeks: expected a number of weeks >= 2",
        ),
        (
            _variant(0, cadence_weeks="8"),
            "cadence_weeks: expected a number of weeks >= 2",
        ),
        (
            _variant(0, cadence_weeks=True),
            "cadence_weeks: expected a number of weeks >= 2",
        ),
        (
            _variant(2, cadence_weeks=8),
            "needs a cadence_weeks of at least 2 and cannot also be a race-sim",
        ),
        (_slot(0, key=0), "key: expected a share in (0, 1], got 0"),
        (_slot(0, key=1.5), "key: expected a share in (0, 1], got 1.5"),
        (_slot(0, key=True), "key: expected a share in (0, 1], got True"),
        (_slot(0, key="0.5"), "key: expected a share in (0, 1], got '0.5'"),
        (_slot(2, optional="yes"), "optional: expected true or false"),
        (
            _slot(2, max_step=0.5),
            "has a max_step but is not a race-sim ramp session",
        ),
        (_variant(2, max_step=2), "max_step: expected a share in (0, 1], got 2"),
        (
            _slot(0, optional=True),
            "is optional, so it cannot be a key, ramp or race-sim session",
        ),
        (_variant(0, why=1), "variants[0].why: expected a string"),
        # contract
        (
            lambda: _mutated((*_LOAD, "contract", "max_hours"), 1),
            "unknown key(s) ['max_hours']",
        ),
        (lambda: _mutated((*_LOAD, "contract"), []), "contract: expected a mapping"),
        (
            lambda: _mutated((*_LOAD, "contract", "must_contain"), ["bike_huge"]),
            "must_contain[0]: unknown ActivityKey",
        ),
        (
            lambda: _mutated((*_LOAD, "contract", "must_not_contain"), "run_long"),
            "must_not_contain: expected a list",
        ),
        (
            lambda: _mutated((*_LOAD, "contract", "min_sport_sessions"), {"rowing": 1}),
            "unknown key(s) ['rowing']",
        ),
        (
            lambda: _mutated(
                (*_LOAD, "contract", "min_sport_sessions"), {"cycling": "1"}
            ),
            "min_sport_sessions.cycling: expected an integer",
        ),
        (
            lambda: _mutated((*_LOAD, "contract", "bricks_per_week"), 1),
            "bricks_per_week: expected a list",
        ),
        (
            lambda: _mutated(
                (*_LOAD, "contract", "weekly_quality_sessions"), [0, 1, 2]
            ),
            "weekly_quality_sessions: expected [lo, hi]",
        ),
    ],
)
def test_rejects_malformed_documents(build, message):
    with pytest.raises(MenuYamlError) as exc:
        blueprint_from_yaml(_text(build()), "t.yaml")
    assert message in str(exc.value)


def test_menu_contract_violations_still_raise_at_load_time():
    doc = _mutated((*_LOAD, "contract", "must_contain"), ["swim_long"])
    with pytest.raises(MenuContractError, match="must_contain"):
        blueprint_from_yaml(_text(doc), "t.yaml")


def test_package_yaml_is_found_and_loaded():
    blueprint = load_blueprint(GoalType.RUNNING_5K)
    assert blueprint.goal is GoalType.RUNNING_5K
    assert blueprint == menus.BLUEPRINTS[GoalType.RUNNING_5K]


def test_load_blueprint_rejects_a_file_declaring_another_goal(monkeypatch, tmp_path):
    text = loader._package_file("running_5k.yaml").read_text(encoding="utf-8")
    (tmp_path / "running_10k.yaml").write_text(text)
    monkeypatch.setattr(loader, "_package_file", lambda name: tmp_path / name)
    with pytest.raises(
        MenuYamlError, match=r"running_10k\.yaml:goal: declares 'running_5k'"
    ):
        load_blueprint(GoalType.RUNNING_10K)


def test_registry_loads_every_goal_from_its_yaml_file():
    assert list(menus.BLUEPRINTS) == list(GoalType)
    for goal, blueprint in menus.BLUEPRINTS.items():
        assert blueprint.goal is goal
        assert loader._package_file(f"{goal.value}.yaml").is_file()


def test_maintenance_supplements_load_in_both_units():
    specs = maintenance_from_yaml(
        "ironman:\n"
        "  - {activity: bike_endurance, day: sun, minutes: [60, 90]}\n"
        "  - {activity: swim_endurance, day: fri, metres: [1500, 2000]}\n",
        "m.yaml",
    )
    assert specs == {
        GoalType.IRONMAN: (
            SessionSpec(
                activity_key=ActivityKey.BIKE_ENDURANCE,
                sport=SportType.CYCLING,
                min_minutes=60,
                max_minutes=90,
                day_hint=6,
            ),
            SessionSpec(
                activity_key=ActivityKey.SWIM_ENDURANCE,
                sport=SportType.SWIMMING,
                min_meters=1500,
                max_meters=2000,
                day_hint=4,
            ),
        )
    }


def test_packaged_maintenance_supplements_cover_the_triathlon_goals():
    # The triathlon maintenance block takes a tune-up too (R-PHASE-019).
    assert set(menus.MAINTENANCE_MENUS) == {
        GoalType.IRONMAN,
        GoalType.HALF_IRONMAN,
        GoalType.OLYMPIC_TRIATHLON,
        GoalType.SPRINT_TRIATHLON,
        GoalType.TRIATHLON_MAINTENANCE,
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("- ironman\n", "m.yaml: expected a mapping"),
        ("rowing: []\n", "m.yaml:rowing: unknown GoalType 'rowing'"),
        ("ironman: {}\n", "m.yaml:ironman: expected a list"),
        ("ironman: [bike]\n", "ironman[0]: expected a mapping"),
        (
            "ironman: [{activity: bike_endurance, day: sun, minutes: [60, 90], why: x}]\n",
            "unknown key(s) ['why']",
        ),
        (
            "ironman: [{activity: bike_endurance, minutes: [60, 90]}]\n",
            "missing key(s) ['day']",
        ),
        (
            "ironman: [{activity: bike_slow, day: sun, minutes: [60, 90]}]\n",
            "unknown ActivityKey",
        ),
        (
            "ironman: [{activity: swim_endurance, day: fri, minutes: [30, 40]}]\n",
            "swim_endurance is authored in metres, not minutes",
        ),
        ("ironman: [{activity: bike_endurance, day: sun}]\n", "missing minutes"),
        (
            "ironman: [{activity: bike_endurance, day: sun, minutes: 60}]\n",
            "expected a list",
        ),
        (
            "ironman: [{activity: bike_endurance, day: 7, minutes: [60, 90]}]\n",
            "expected one of",
        ),
        (
            "ironman: [{activity: bike_endurance, day: sun, minutes: [0, 90]}]\n",
            "ironman[0]: Non-swim spec bike_endurance must set positive",
        ),
    ],
)
def test_maintenance_supplements_reject_malformed_documents(text, message):
    with pytest.raises(MenuYamlError) as exc:
        maintenance_from_yaml(text, "m.yaml")
    assert message in str(exc.value)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "goal: running_5k\nmenus: {}\ngoal: running_10k\n",
            "line 3: duplicate key 'goal'",
        ),
        (
            "ironman:\n  - {activity: bike_endurance, day: sun, minutes: [60, 90], minutes: [70, 90]}\n",
            "duplicate key 'minutes'",
        ),
    ],
)
def test_a_key_written_twice_is_rejected(text, message):
    with pytest.raises(MenuYamlError) as exc:
        maintenance_from_yaml(text, "m.yaml") if text.startswith(
            "ironman"
        ) else blueprint_from_yaml(text, "x.yaml")
    assert message in str(exc.value)


def test_cadence_weeks_builds_the_variant_field():
    blueprint = blueprint_from_yaml(_text(_variant(0, cadence_weeks=8)()), "t.yaml")
    run = blueprint.phases[(PhaseType.MAINTAIN, "load")].slots[3]
    assert run.variants[0].cadence_weeks == 8
    assert run.variants[1].cadence_weeks is None
