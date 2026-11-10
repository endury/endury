"""Prose restatement of a planned session's per-step targets.

The step payload nests each band under ``target`` / ``secondary_target`` /
``additional_targets`` with a zone number apiece, but power zones and
heart-rate zones are numbered independently: a step's zone number must stay
paired with its own band, never read against another band's zone table. One
line per step, every band next to its own zone label, is what the coach
should be reading back.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _pace(seconds_per_km: float) -> str:
    """Render a min/km pace held as decimal minutes as m:ss."""
    total = round(seconds_per_km * 60)
    return f"{total // 60}:{total % 60:02d}"


def _band(target: Mapping[str, Any], zones: Mapping[str, int]) -> str | None:
    """Render one target dict as a band with its own zone label."""
    kind = str(target.get("target_type") or "")
    zone = target.get("zone")
    if kind == "power":
        low, high = target.get("low_watts"), target.get("high_watts")
        label = f" (power Z{zones['power_zone']})" if "power_zone" in zones else ""
        return f"{low}-{high} W{label}" if low is not None else None
    if kind == "heart_rate_zone":
        low, high = target.get("low_bpm"), target.get("high_bpm")
        label = f" (HR Z{zone})" if zone is not None else ""
        if low is not None and high is not None:
            return f"{low}-{high} bpm{label}"
        if high is not None:
            return f"under {high} bpm{label}"
        return f"HR Z{zone}" if zone is not None else None
    if kind == "pace":
        low, high = target.get("low_min_per_km"), target.get("high_min_per_km")
        if low is not None and high is not None:
            return f"{_pace(low)}-{_pace(high)} /km"
        return None
    if kind == "swim_pace_zone":
        low, high = target.get("low_sec_per_100m"), target.get("high_sec_per_100m")
        label = f" (swim Z{zone})" if zone is not None else ""
        if low is not None and high is not None:
            return f"{_pace(low / 60)}-{_pace(high / 60)} /100m{label}"
        return f"swim Z{zone}" if zone is not None else None
    if kind == "rpe" and target.get("rpe") is not None:
        return f"RPE {target['rpe']}"
    return None


def _step_line(step: Mapping[str, Any]) -> str:
    """Render one non-repeat step as ``<type> <minutes> min — <bands>``."""
    targets = [
        t
        for t in (
            step.get("target"),
            step.get("secondary_target"),
            *(step.get("additional_targets") or []),
        )
        if isinstance(t, Mapping)
    ]
    lead = step.get("target")
    if isinstance(lead, Mapping) and lead.get("target_type") == "rpe":
        # An effort-led step (a hill rep, a vert hike, a fartlek surge) carries
        # its flat-ground pace only as an extra: not a target to judge it by.
        targets = [
            t for t in targets if t.get("target_type") not in ("pace", "pace_zone")
        ]
    # Zone-only targets ("power_zone": 3) label the band they belong to
    # rather than standing on their own, so a bare zone number is never read
    # against the wrong band's zone table.
    zones = {
        str(t["target_type"]): int(t["zone"])
        for t in targets
        if str(t.get("target_type") or "").endswith("_zone")
        and t.get("zone") is not None
        and t.get("target_type") != "heart_rate_zone"
        and t.get("target_type") != "swim_pace_zone"
    }
    bands = [b for b in (_band(t, zones) for t in targets) if b]
    minutes = float(step.get("duration_seconds") or 0.0) / 60.0
    head = f"{step.get('type') or 'step'} {minutes:g} min"
    return f"{head} — {', '.join(bands)}" if bands else head


def summarize_prescribed_steps(steps: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return one prose line per step, repeats expanded with their count.

    Args:
        steps: The serialized ``workout.steps`` list of a planned session.

    Returns:
        Lines such as ``"interval 37 min — 211-224 W (power Z3), 132-140
        bpm (HR Z2), RPE 6"``; a repeat block contributes ``"4x ..."`` lines.
    """
    lines: list[str] = []
    for step in steps:
        if step.get("type") == "repeat" and step.get("steps"):
            count = int(step.get("iterations") or 1)
            lines.extend(
                f"{count}x {line}" for line in summarize_prescribed_steps(step["steps"])
            )
            continue
        lines.append(_step_line(step))
    return lines


__all__ = ["summarize_prescribed_steps"]
