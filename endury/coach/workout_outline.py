"""Athlete-facing outline of one ad-hoc structured workout.

The coach's ``build_workout`` tool ships the outline in its response for the
model to quote verbatim, rebuilt from the same payload every caller reads.
It reads the flat-step shape ``build_workout`` returns. Pure formatting
over the tool's own JSON shape — no planner or tool imports, so either
side can depend on it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# FlatStep.step_type -> the phase name the prompt asks the coach to bold.
_PHASE_NAMES = {
    "warmup": "Warm-up",
    "interval": "Main",
    "recovery": "Recovery",
    "rest": "Rest",
    "cooldown": "Cool-down",
}

# A resolved target carries its own band in brackets ("HR Z1 (under 143
# bpm)"); the step line puts the target in brackets too, which nested them
# one inside the other. The target's own pair becomes a comma clause.
_TARGET_PARENS_PATTERN = re.compile(r"\s*\(([^)]*)\)")


@dataclass(frozen=True)
class WorkoutOutline:
    """One built workout rendered as lines the coach can quote as-is."""

    steps: tuple[str, ...]
    total: str
    total_minutes: int

    @property
    def text(self) -> str:
        """Return every line — the steps then the total — as one block."""
        return "\n".join([*self.steps, self.total])


def _format_minutes(seconds: float) -> str:
    """Render a step length in whole minutes, keeping a clean half."""
    halves = seconds / 30.0
    if abs(halves - round(halves)) < 1e-6 and round(halves) % 2 == 1:
        return f"{round(halves) / 2:g}"
    return str(round(seconds / 60.0))


def _step_length(step: Mapping[str, Any]) -> str:
    """Render one flattened step's length as metres, minutes, or seconds."""
    distance_m = step.get("distance_m")
    seconds = float(step.get("duration_s") or 0.0)
    if distance_m:
        return f"{round(float(distance_m))} m"
    if seconds < 60.0:
        # A 20 s rest reads as "0 min" in whole minutes — quote the seconds.
        return f"{round(seconds)} s"
    return f"{_format_minutes(seconds)} min"


def _flatten_target(target: str) -> str:
    """Return a target with its own bracketed clause folded into a comma."""
    return _TARGET_PARENS_PATTERN.sub(r", \1", target).strip(" ,")


def _phase(step: Mapping[str, Any]) -> str:
    """Return the phase name a step's line leads with."""
    step_type = str(step.get("type") or "")
    return _PHASE_NAMES.get(step_type, step_type.replace("_", " ").title())


def _step_body(step: Mapping[str, Any], label: str = "") -> str:
    """Render a step's length, an optional label, and its target in brackets."""
    target = _flatten_target(
        str(step.get("primary_target") or step.get("zone_band") or "")
    )
    suffix = f" ({target})" if target else ""
    return f"{_step_length(step)}{label}{suffix}"


def _step_line(step: Mapping[str, Any]) -> str:
    """Render one flattened step as a bold phase line."""
    return f"**{_phase(step)}** — {_step_body(step)}"


def _repeat_line(block: list[Mapping[str, Any]], count: int) -> str:
    """Render ``count`` back-to-back copies of one step or one pair as one line."""
    first, *rest = block
    if not rest:
        return f"**{_phase(first)}** — {count}x {_step_body(first)}"
    second = rest[0]
    label = "" if _phase(second) == _phase(first) else f" {_phase(second).lower()}"
    pair = f"{_step_body(first)} + {_step_body(second, label)}"
    return f"**{_phase(first)}** — {count}x [{pair}]"


def _grouped_lines(steps: list[Mapping[str, Any]]) -> list[str]:
    """Render the steps, folding back-to-back copies of a step or a pair."""
    lines = [_step_line(step) for step in steps]
    grouped: list[str] = []
    index = 0
    while index < len(lines):
        period, count = 1, 1
        for size in (1, 2):
            block = lines[index : index + size]
            copies = 1
            while lines[index + copies * size : index + (copies + 1) * size] == block:
                copies += 1
            if copies > 1 and size * copies > period * count:
                period, count = size, copies
        if count == 1:
            grouped.append(lines[index])
        else:
            grouped.append(_repeat_line(steps[index : index + period], count))
        index += period * count
    return grouped


def build_workout_outline(payload: Mapping[str, Any]) -> WorkoutOutline:
    """Render a ``build_workout`` payload as quotable outline lines.

    Back-to-back copies of one step, or of a rep and its recovery, fold into
    one line with a count, so a 13-rep set reads as one line, not 26.

    Args:
        payload: The tool's response object — ``steps`` plus the duration
            and TSS totals.

    Returns:
        The per-step lines, the total line, and the workout's total minutes.
    """
    steps: list[Mapping[str, Any]] = list(payload.get("steps") or [])
    seconds = tuple(float(s.get("duration_s") or 0.0) for s in steps)
    summed_minutes = sum(round(sec / 60.0) for sec in seconds)
    duration_minutes = payload.get("duration_minutes")
    total_minutes = (
        round(float(duration_minutes)) if duration_minutes else summed_minutes
    )
    tss = payload.get("achieved_tss") or payload.get("target_tss")
    tss_clause = f" · ~{round(float(tss))} load" if tss else ""
    return WorkoutOutline(
        steps=tuple(_grouped_lines(steps)),
        total=f"**Total** — {total_minutes} min{tss_clause}",
        total_minutes=total_minutes,
    )


__all__ = [
    "WorkoutOutline",
    "build_workout_outline",
]
