"""Fuel, water and kit lines for the brief sheet, read from YAML.

The bands live in ``fuelling.yaml`` and ``kit.yaml`` next to this module,
with the research behind them in their comments; this module only picks
the band for a session's length or the recent temperature and fills in the
athlete's weight.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

import yaml  # type: ignore[import-untyped, unused-ignore]

from endury.planner.foundations.types import SportType

_PACKAGE = __name__.rpartition(".")[0]

#: The dash of a range on screen ("60\u201380 g").
DASH = "\u2013"


def _load(name: str) -> dict[str, Any]:
    """Read one YAML file of this package (works from a zip bundle too)."""
    text = resources.files(_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    loaded: dict[str, Any] = yaml.safe_load(text)
    return loaded


FUELLING: dict[str, Any] = _load("fuelling.yaml")
KIT: dict[str, Any] = _load("kit.yaml")


def span(low: float, high: float) -> str:
    """Render a band as ``low`` en dash ``high``, in whole numbers.

    Args:
        low: The band's lower bound.
        high: The band's upper bound.
    """
    return f"{low:g}{DASH}{high:g}"


def _round10(value: float) -> int:
    """Round to the nearest ten."""
    return int(round(value / 10.0) * 10)


def _during_band(duration_min: float) -> dict[str, Any]:
    """Return the first ``during`` band whose ceiling holds the session."""
    bands: list[dict[str, Any]] = FUELLING["during"]
    for band in bands:
        ceiling = band["up_to_min"]
        if ceiling is None or duration_min <= ceiling:
            return band
    return bands[-1]


def _before_line(weight_kg: float | None) -> str:
    """Return the pre-session meal line, in grams when the weight is known."""
    before = FUELLING["before"]
    hours = span(*before["hours"])
    if weight_kg is None:
        return str(before["without_weight"]).format(hours=hours)
    low, high = (round(weight_kg * g) for g in before["g_per_kg"])
    return str(before["with_weight"]).format(hours=hours, grams=span(low, high))


def _carb_load(duration_min: float, weight_kg: float | None) -> dict[str, Any] | None:
    """Return the day-before carb load for a long race, else None."""
    load = FUELLING["carb_load"]
    if duration_min <= load["races_over_min"]:
        return None
    per_kg = list(load["g_per_kg"])
    grams = [round(weight_kg * g) for g in per_kg] if weight_kg is not None else None
    return {"g_per_kg": per_kg, "g": grams, "days": int(load["days"])}


def fuel_plan(
    duration_min: float,
    *,
    weight_kg: float | None,
    is_race: bool,
    temp_c: float | None,
) -> dict[str, Any]:
    """Build the sheet's fuel section for a session or race of a given length.

    Args:
        duration_min: The session's (or the race estimate's) length in minutes.
        weight_kg: The athlete's weight, or None when unknown.
        is_race: The subject is a race (adds the day-before carb load).
        temp_c: The warmest recent session temperature, or None.

    Returns:
        ``carbs_g_per_h``, ``carbs_total_g``, ``fluid_ml_per_h``, ``before``,
        ``carb_load`` and ``notes``, rounded for display; a session water
        alone covers has only its one note.
    """
    band = _during_band(duration_min)
    carbs = band["carbs_g_per_h"]
    fluid = list(band["fluid_ml_per_h"] or [])
    notes = list(band["notes"])
    heat = FUELLING["heat"]
    if fluid and temp_c is not None and temp_c > heat["above_c"]:
        fluid = [value + heat["extra_fluid_ml_per_h"] for value in fluid]
        notes.append(str(heat["note"]))
    if carbs:
        notes.append(str(FUELLING["guidance"]))
    hours = duration_min / 60.0
    return {
        "carbs_g_per_h": list(carbs) if carbs else None,
        "carbs_total_g": [_round10(g * hours) for g in carbs] if carbs else None,
        "fluid_ml_per_h": fluid or None,
        "before": _before_line(weight_kg) if band["before"] else None,
        "carb_load": _carb_load(duration_min, weight_kg) if is_race else None,
        "notes": notes,
    }


def kit_lines(sport: SportType, temp_c: float | None) -> list[str]:
    """Return the kit lines for a sport at a temperature.

    Args:
        sport: The session's sport; swimming has no kit line.
        temp_c: The coolest recent session temperature, or None.

    Returns:
        Two or three short lines, or an empty list for a swim or an
        unknown temperature.
    """
    bands = KIT.get(sport.value)
    if not bands or temp_c is None:
        return []
    for band in bands:
        ceiling = band["below_c"]
        if ceiling is None or temp_c < ceiling:
            return list(band["lines"])
    return []  # pragma: no cover - the last band has no ceiling


__all__ = ["DASH", "fuel_plan", "kit_lines", "span"]


def typical_race_minutes(goal_type: str | None) -> int | None:
    """Return the typical finishing time for a goal type, in minutes, or None."""
    hours = FUELLING["race_hours_by_goal"].get(goal_type or "")
    return round(hours * 60) if hours else None
