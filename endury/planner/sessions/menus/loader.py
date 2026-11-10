"""YAML menu loader: ``<GoalType.value>.yaml`` -> :class:`PlanBlueprint`.

A goal's menus live in ``<GoalType.value>.yaml`` next to this module and the
cross-category B/C-goal supplements in ``maintenance_supplements.yaml``;
``AUTHORING.md`` documents the format. The loader builds the frozen
dataclasses of ``types.py``, rejects unknown keys and malformed
values with the offending path (``running_5k.yaml:menus.base/load.slots[2]``),
and runs :func:`contracts.validate_blueprint` on every blueprint it builds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from importlib import resources
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Any, TypeVar

import yaml  # type: ignore[import-untyped, unused-ignore]

from endury.planner.foundations.types import (
    ActivityKey,
    AmbitionLevel,
    GoalType,
    PhaseType,
    SportType,
)
from endury.planner.sessions.menus.contracts import sport_of, validate_blueprint
from endury.planner.sessions.menus.patterns import scaled
from endury.planner.sessions.menus.session_spec import SessionSpec
from endury.planner.sessions.menus.types import (
    Bounds,
    MenuContract,
    PhaseMenu,
    PlanBlueprint,
    Progression,
    Rationale,
    Slot,
    Variant,
    Weekday,
)

#: File holding the cross-category B/C-goal maintenance supplements.
MAINTENANCE_FILE = "maintenance_supplements.yaml"

_PACKAGE = __name__.rpartition(".")[0]

# libyaml's safe loader parses ~10x faster; same constructors as SafeLoader.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

_DAYS: Mapping[str, Weekday] = {day.name.lower(): day for day in Weekday}

_FILE_KEYS = frozenset({"goal", "offered", "progressions", "menus"})
_MENU_KEYS = frozenset({"contract", "slots"})
_CONTRACT_KEYS = frozenset(
    {
        "must_contain",
        "must_not_contain",
        "min_sport_sessions",
        "weekly_quality_sessions",
        "bricks_per_week",
    }
)
_VARIANT_KEYS = frozenset(
    {
        "activity",
        "rotation",
        "minutes",
        "metres",
        "progression",
        "window",
        "group",
        "brick",
        "race_sim",
        "key",
        "eases",
        "optional",
        "max_step",
        "cadence_weeks",
        "load_weeks",
        "why",
    }
)
_SLOT_KEYS = frozenset({"name", "day", "why", "variants"})
_INLINE_SLOT_KEYS = (_SLOT_KEYS - {"variants"}) | _VARIANT_KEYS
_BOUNDS_KEYS = frozenset({"scaled"} | {tier.value for tier in AmbitionLevel})
_SPORT_KEYS = frozenset(sport.value for sport in SportType)
_SUPPLEMENT_KEYS = frozenset({"activity", "day", "minutes", "metres"})

_E = TypeVar("_E", bound=Enum)
_T = TypeVar("_T")


class MenuYamlError(ValueError):
    """A menu YAML document that does not describe a valid menu."""


# Value readers — one rejection path each.


def _dict(value: object, where: str) -> dict[Any, Any]:
    """Return *value* if it is a mapping."""
    if not isinstance(value, dict):
        raise MenuYamlError(f"{where}: expected a mapping, got {value!r}")
    return value


def _keys(
    value: object,
    where: str,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Return *value* as a mapping whose keys are all *allowed* and *required*."""
    mapping = _dict(value, where)
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise MenuYamlError(f"{where}: unknown key(s) {unknown}")
    missing = sorted(required - mapping.keys())
    if missing:
        raise MenuYamlError(f"{where}: missing key(s) {missing}")
    return mapping


def _list(value: object, where: str) -> list[Any]:
    """Return *value* if it is a list."""
    if not isinstance(value, list):
        raise MenuYamlError(f"{where}: expected a list, got {value!r}")
    return value


def _str(value: object, where: str) -> str:
    """Return *value* if it is a string."""
    if not isinstance(value, str):
        raise MenuYamlError(f"{where}: expected a string, got {value!r}")
    return value


def _bool(value: object, where: str) -> bool:
    """Return *value* if it is ``true``/``false``."""
    if not isinstance(value, bool):
        raise MenuYamlError(f"{where}: expected true or false, got {value!r}")
    return value


def _int(value: object, where: str) -> int:
    """Return *value* if it is an integer (booleans are not)."""
    if type(value) is not int:
        raise MenuYamlError(f"{where}: expected an integer, got {value!r}")
    return value


def _int_pair(value: object, where: str) -> tuple[int, int]:
    """Return a ``[lo, hi]`` integer pair as a tuple."""
    pair = _list(value, where)
    if len(pair) != 2:
        raise MenuYamlError(f"{where}: expected [lo, hi], got {value!r}")
    return _int(pair[0], where), _int(pair[1], where)


def _window(value: object, where: str) -> tuple[float, float]:
    """Return a ``[start, end]`` phase-fraction window as floats."""
    pair = _list(value, where)
    if len(pair) != 2 or not all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in pair
    ):
        raise MenuYamlError(f"{where}: expected [start, end] numbers, got {value!r}")
    return float(pair[0]), float(pair[1])


def _share(value: object, where: str) -> float:
    """Return a share of the week: a number in (0, 1]."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= 1
    ):
        raise MenuYamlError(f"{where}: expected a share in (0, 1], got {value!r}")
    return float(value)


def _weeks(value: object, where: str) -> int:
    """Return a cadence in weeks: an integer of at least 2."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 2:
        raise MenuYamlError(f"{where}: expected a number of weeks >= 2, got {value!r}")
    return value


def _enum(cls: type[_E], value: object, where: str) -> _E:
    """Return the *cls* member whose value is *value*."""
    try:
        return cls(value)
    except ValueError:
        raise MenuYamlError(f"{where}: unknown {cls.__name__} {value!r}") from None


def _day(value: object, where: str) -> Weekday:
    """Return the weekday named ``mon`` .. ``sun``."""
    if not isinstance(value, str) or value not in _DAYS:
        raise MenuYamlError(f"{where}: expected one of {list(_DAYS)}, got {value!r}")
    return _DAYS[value]


class _UniqueKeyLoader(_SAFE_LOADER):  # type: ignore[misc,valid-type]
    """The safe loader, refusing a mapping key written twice.

    Plain YAML keeps the last value of a repeated key without a word, so a
    slot with ``minutes:`` twice or a second ``base/recovery:`` menu would
    silently override the first — exactly the edit a contributor must hear
    about.
    """

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        """Build a mapping after checking that no key repeats."""
        seen: set[object] = set()
        for key_node, _value in node.value:
            key = self.construct_object(key_node, deep=deep)
            if isinstance(key, str) and key in seen:
                raise MenuYamlError(
                    f"line {key_node.start_mark.line + 1}: duplicate key {key!r}"
                )
            seen.add(key)
        mapping: dict[Any, Any] = super().construct_mapping(node, deep=deep)
        return mapping


def _parse(text: str) -> object:
    """Parse a YAML document with the safe, duplicate-refusing loader."""
    return yaml.load(text, Loader=_UniqueKeyLoader)  # noqa: S506 - a safe loader


def _build(factory: Callable[..., _T], where: str, **kwargs: Any) -> _T:
    """Construct a menu dataclass, prefixing its own validation error with *where*."""
    try:
        return factory(**kwargs)
    except ValueError as exc:
        raise MenuYamlError(f"{where}: {exc}") from exc


# Bounds and the unit law.


def _unit(raw: Mapping[str, Any], where: str, activity: ActivityKey) -> str:
    """Return the bounds key *activity* is authored in.

    Swim is authored in ``metres``, everything else in ``minutes``; the
    other key is rejected rather than silently misread.
    """
    unit, wrong = (
        ("metres", "minutes")
        if sport_of(activity) is SportType.SWIMMING
        else ("minutes", "metres")
    )
    if wrong in raw:
        raise MenuYamlError(
            f"{where}: {activity.value} is authored in {unit}, not {wrong}"
        )
    if unit not in raw:
        raise MenuYamlError(f"{where}: missing {unit}")
    return unit


def _bounds(value: object, where: str) -> Bounds:
    """Parse ``[lo, hi]``, ``{scaled: [lo, hi]}`` or ``{<tier>: [lo, hi], ...}``."""
    if isinstance(value, list):
        return _int_pair(value, where)
    tiers = _keys(value, where, _BOUNDS_KEYS)
    if "scaled" in tiers:
        if len(tiers) != 1:
            raise MenuYamlError(f"{where}: scaled cannot be combined with tiers")
        return scaled(*_int_pair(tiers["scaled"], f"{where}.scaled"))
    return {
        AmbitionLevel(tier): _int_pair(pair, f"{where}.{tier}")
        for tier, pair in tiers.items()
    }


# Blueprint documents.


def _why(raw: Mapping[str, Any], where: str) -> Rationale | None:
    """Return the athlete-facing ``why`` as a Rationale, or None."""
    if "why" not in raw:
        return None
    return Rationale(athlete_why=_str(raw["why"], f"{where}.why"))


def _progression(
    raw: Mapping[str, Any],
    key: str,
    where: str,
    progressions: Mapping[str, Progression],
) -> Progression | None:
    """Return the declared Progression named under *key*, or None."""
    if key not in raw:
        return None
    name = _str(raw[key], f"{where}.{key}")
    if name not in progressions:
        raise MenuYamlError(f"{where}.{key}: {name!r} is not listed under progressions")
    return progressions[name]


def _variant(
    raw: Mapping[str, Any], where: str, progressions: Mapping[str, Progression]
) -> Variant:
    """Build one Variant from its (already key-checked) mapping."""
    activity = _enum(ActivityKey, raw["activity"], f"{where}.activity")
    unit = _unit(raw, where, activity)
    rotation = _list(raw.get("rotation", []), f"{where}.rotation")
    return _build(
        Variant,
        where,
        activity_key=activity,
        bounds=_bounds(raw[unit], f"{where}.{unit}"),
        frac=(
            _window(raw["window"], f"{where}.window") if "window" in raw else (0.0, 1.0)
        ),
        progression=_progression(raw, "progression", where, progressions),
        is_brick_pair=_bool(raw.get("brick", False), f"{where}.brick"),
        rationale=_why(raw, where),
        slot_group=_str(raw["group"], f"{where}.group") if "group" in raw else None,
        rotation=tuple(
            _enum(ActivityKey, key, f"{where}.rotation[{i}]")
            for i, key in enumerate(rotation)
        ),
        race_sim=_bool(raw.get("race_sim", False), f"{where}.race_sim"),
        key=_share(raw["key"], f"{where}.key") if "key" in raw else 0.0,
        eases=_progression(raw, "eases", where, progressions),
        optional=_bool(raw.get("optional", False), f"{where}.optional"),
        max_step=(
            _share(raw["max_step"], f"{where}.max_step") if "max_step" in raw else None
        ),
        cadence_weeks=(
            _weeks(raw["cadence_weeks"], f"{where}.cadence_weeks")
            if "cadence_weeks" in raw
            else None
        ),
        load_weeks=_bool(raw.get("load_weeks", False), f"{where}.load_weeks"),
    )


def _slot(raw: object, where: str, progressions: Mapping[str, Progression]) -> Slot:
    """Build one Slot: inline variant keys, or a ``variants:`` list."""
    if isinstance(raw, dict) and "variants" in raw:
        slot = _keys(raw, where, _SLOT_KEYS, frozenset({"name", "day"}))
        variants = tuple(
            _variant(
                _keys(
                    v, f"{where}.variants[{i}]", _VARIANT_KEYS, frozenset({"activity"})
                ),
                f"{where}.variants[{i}]",
                progressions,
            )
            for i, v in enumerate(_list(slot["variants"], f"{where}.variants"))
        )
        rationale = _why(slot, where)
    else:
        slot = _keys(
            raw, where, _INLINE_SLOT_KEYS, frozenset({"name", "day", "activity"})
        )
        variants = (_variant(slot, where, progressions),)
        rationale = None
    return _build(
        Slot,
        where,
        name=_str(slot["name"], f"{where}.name"),
        day=_day(slot["day"], f"{where}.day"),
        variants=variants,
        rationale=rationale,
    )


def _activity_set(
    raw: Mapping[str, Any], key: str, where: str
) -> frozenset[ActivityKey]:
    """Return a contract's activity-key list as a frozenset."""
    return frozenset(
        _enum(ActivityKey, value, f"{where}.{key}[{i}]")
        for i, value in enumerate(_list(raw.get(key, []), f"{where}.{key}"))
    )


def _optional_pair(
    raw: Mapping[str, Any], key: str, where: str
) -> tuple[int, int] | None:
    """Return a contract's optional ``[lo, hi]`` range."""
    return _int_pair(raw[key], f"{where}.{key}") if key in raw else None


def _contract(value: object, where: str) -> MenuContract:
    """Build a MenuContract; every field is optional."""
    raw = _keys(value, where, _CONTRACT_KEYS)
    sessions = _keys(
        raw.get("min_sport_sessions", {}), f"{where}.min_sport_sessions", _SPORT_KEYS
    )
    return MenuContract(
        must_contain=_activity_set(raw, "must_contain", where),
        must_not_contain=_activity_set(raw, "must_not_contain", where),
        min_sport_sessions=MappingProxyType(
            {
                SportType(sport): _int(count, f"{where}.min_sport_sessions.{sport}")
                for sport, count in sessions.items()
            }
        ),
        weekly_quality_sessions=_optional_pair(raw, "weekly_quality_sessions", where),
        bricks_per_week=_optional_pair(raw, "bricks_per_week", where),
    )


def _menu(
    key: object, value: object, where: str, progressions: Mapping[str, Progression]
) -> PhaseMenu:
    """Build the PhaseMenu stored under ``<phase>/<mode_key>``."""
    phase_value, sep, mode_key = str(key).partition("/")
    if not sep:
        raise MenuYamlError(f"{where}: menu key must be <phase>/<mode_key>")
    phase = _enum(PhaseType, phase_value, where)
    raw = _keys(value, where, _MENU_KEYS, frozenset({"slots"}))
    return PhaseMenu(
        phase=phase,
        mode_key=mode_key,
        slots=tuple(
            _slot(slot, f"{where}.slots[{i}]", progressions)
            for i, slot in enumerate(_list(raw["slots"], f"{where}.slots"))
        ),
        contract=(
            _contract(raw["contract"], f"{where}.contract")
            if "contract" in raw
            else MenuContract()
        ),
    )


def blueprint_from_yaml(text: str, source: str) -> PlanBlueprint:
    """Parse and validate one goal's menu document.

    Args:
        text: The YAML document.
        source: Name used as the prefix of every error path.

    Returns:
        The validated blueprint.

    Raises:
        MenuYamlError: When the document is malformed.
        MenuContractError: When the blueprint breaks a menu contract.
    """
    doc = _keys(
        _parse(text),
        source,
        _FILE_KEYS,
        frozenset({"goal", "menus"}),
    )
    goal = _enum(GoalType, doc["goal"], f"{source}:goal")
    names = _list(doc.get("progressions", []), f"{source}:progressions")
    progressions = {
        name: Progression(name=name)
        for name in (
            _str(value, f"{source}:progressions[{i}]") for i, value in enumerate(names)
        )
    }
    menus = [
        _menu(key, value, f"{source}:menus.{key}", progressions)
        for key, value in _dict(doc["menus"], f"{source}:menus").items()
    ]
    blueprint = PlanBlueprint(
        goal=goal,
        phases={(menu.phase, menu.mode_key): menu for menu in menus},
        offered=frozenset(
            _enum(AmbitionLevel, tier, f"{source}:offered[{i}]")
            for i, tier in enumerate(_list(doc.get("offered", []), f"{source}:offered"))
        ),
    )
    validate_blueprint(blueprint)
    return blueprint


def maintenance_from_yaml(
    text: str, source: str
) -> dict[GoalType, tuple[SessionSpec, ...]]:
    """Parse the ``<A-goal>: [supplement, ...]`` maintenance document.

    Args:
        text: The YAML document.
        source: Name used as the prefix of every error path.

    Returns:
        A-goal -> the maintenance specs added to its B/C-race weeks.

    Raises:
        MenuYamlError: When the document is malformed.
    """
    doc = _dict(_parse(text), source)
    return {
        _enum(GoalType, goal, f"{source}:{goal}"): tuple(
            _supplement(raw, f"{source}:{goal}[{i}]")
            for i, raw in enumerate(_list(specs, f"{source}:{goal}"))
        )
        for goal, specs in doc.items()
    }


def _supplement(value: object, where: str) -> SessionSpec:
    """Build one maintenance SessionSpec (a plain ``[lo, hi]`` bound)."""
    raw = _keys(value, where, _SUPPLEMENT_KEYS, frozenset({"activity", "day"}))
    activity = _enum(ActivityKey, raw["activity"], f"{where}.activity")
    unit = _unit(raw, where, activity)
    lo, hi = _int_pair(raw[unit], f"{where}.{unit}")
    bounds = (
        {"min_meters": lo, "max_meters": hi}
        if unit == "metres"
        else {"min_minutes": lo, "max_minutes": hi}
    )
    return _build(
        SessionSpec,
        where,
        activity_key=activity,
        sport=sport_of(activity),
        day_hint=int(_day(raw["day"], f"{where}.day")),
        **bounds,
    )


# Package files.


def _package_file(name: str) -> Traversable:
    """Return the package resource *name* (works from a zip bundle too)."""
    return resources.files(_PACKAGE).joinpath(name)


def load_blueprint(goal: GoalType) -> PlanBlueprint:
    """Load and validate ``<goal>.yaml`` from the package.

    Args:
        goal: The goal to load.

    Returns:
        The validated blueprint.

    Raises:
        MenuYamlError: When the file is malformed or declares another goal.
    """
    name = f"{goal.value}.yaml"
    blueprint = blueprint_from_yaml(
        _package_file(name).read_text(encoding="utf-8"), name
    )
    if blueprint.goal is not goal:
        raise MenuYamlError(f"{name}:goal: declares {blueprint.goal.value!r}")
    return blueprint


def load_maintenance_menus() -> dict[GoalType, tuple[SessionSpec, ...]]:
    """Load ``maintenance_supplements.yaml`` from the package."""
    return maintenance_from_yaml(
        _package_file(MAINTENANCE_FILE).read_text(encoding="utf-8"), MAINTENANCE_FILE
    )


__all__ = [
    "MAINTENANCE_FILE",
    "MenuYamlError",
    "blueprint_from_yaml",
    "load_blueprint",
    "load_maintenance_menus",
    "maintenance_from_yaml",
]
