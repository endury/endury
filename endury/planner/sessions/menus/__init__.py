"""Public surface of the session-menu data layer.

Exports the ``BLUEPRINTS`` registry, the runtime ``SessionSpec`` type,
the ``get_session_menu`` adapter that flattens a (goal, phase,
mode_key, ambition) view of a blueprint into the per-week
``SessionSpec`` tuple the session_generator consumes, and the cross-
category ``MAINTENANCE_MENUS`` table (``maintenance_supplements.yaml``).
Every goal's blueprint loads from ``<GoalType.value>.yaml`` next to this
module (``loader.py``).
Import-time validation runs every time the package loads so contract
failures surface in tests, CLI commands and web requests alike.

Every Variant carries explicit per-tier bounds; the runtime applies no
ambition scale multiplier.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from endury.planner.foundations.types import (
    AmbitionLevel,
    GoalType,
    PhaseType,
)
from endury.planner.sessions.menus.contracts import (
    MenuContractError,
    validate_all,
)
from endury.planner.sessions.menus.loader import (
    load_blueprint,
    load_maintenance_menus,
)
from endury.planner.sessions.menus.resolve import flatten_menu
from endury.planner.sessions.menus.session_spec import SessionSpec
from endury.planner.sessions.menus.types import PlanBlueprint

BLUEPRINTS: Mapping[GoalType, PlanBlueprint] = MappingProxyType(
    {goal: load_blueprint(goal) for goal in GoalType}
)

MAINTENANCE_MENUS: dict[GoalType, tuple[SessionSpec, ...]] = load_maintenance_menus()

# Pre-flattened menus per (goal, ambition, phase, mode_key).


def _build_session_menus() -> dict[
    tuple[GoalType, AmbitionLevel, PhaseType, str], tuple[SessionSpec, ...]
]:
    """Pre-flatten every (goal, ambition, phase, mode_key) trio.

    Built once at import; :func:`get_session_menu` becomes a dict get
    rather than a per-call ``flatten_menu`` pass.
    """
    merged: dict[
        tuple[GoalType, AmbitionLevel, PhaseType, str],
        tuple[SessionSpec, ...],
    ] = {}
    for goal, blueprint in BLUEPRINTS.items():
        for (phase, mode_key), menu in blueprint.phases.items():
            for ambition in AmbitionLevel:
                specs = flatten_menu(menu, ambition)
                if specs:
                    merged[(goal, ambition, phase, mode_key)] = specs
    return merged


_SESSION_MENUS = _build_session_menus()


# Public lookup.


def get_session_menu(
    goal: GoalType,
    phase: PhaseType,
    mode_key: str,
    ambition: AmbitionLevel = AmbitionLevel.COMPETITIVE,
) -> tuple[SessionSpec, ...]:
    """Return the menu as a tuple of ``SessionSpec`` entries.

    Args:
        goal: Race goal type.
        phase: Training phase.
        mode_key: ``"load"``/``"recovery"``/``"taper"``/``"race"``.
        ambition: Ambition tier (drives the per-tier ``bounds`` filter).

    Returns:
        The flattened ``SessionSpec`` tuple, or an empty tuple if the
        blueprint has no Variants firing for *ambition* at this
        ``(phase, mode_key)``.
    """
    return _SESSION_MENUS.get((goal, ambition, phase, mode_key), ())


# Cross-blueprint validation pass.
validate_all(BLUEPRINTS)


__all__ = [
    "BLUEPRINTS",
    "MAINTENANCE_MENUS",
    "MenuContractError",
    "SessionSpec",
    "get_session_menu",
]
