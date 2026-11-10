"""Serialize a ``StructuredWorkout`` to intervals.icu's workout text DSL.

intervals.icu parses workout steps out of a calendar event's ``description``:
lines starting with ``- `` are steps, a bare ``Nx`` line (optionally labelled)
opens a repeat block that must be surrounded by blank lines, and free prose
before or between blocks is ignored by the parser.  Nested repeats are not
supported, so this module flattens them.

Emission policy (see ``docs/reference/intervals_api.md``): a step's **lead
metric decides** which target reaches the watch.  A step whose primary target
is heart rate goes out as ``% LTHR`` even when it also carries a pace or power
target; an effort-led (RPE) running step takes its heart-rate band or
nothing (``_RPE_LEAD_PREFERENCE``), never its pace; any other step takes the
first target its sport's fixed order (``_TARGET_PREFERENCE``) can render.
Targets are emitted **relative to
threshold** whenever possible — ``%`` of FTP for power, ``% LTHR`` for heart
rate, ``% Pace`` for run and swim pace — so the athlete's threshold changes on
intervals.icu re-resolve every pushed workout.  Concrete target bands are
converted to percentages when the relevant threshold is known; otherwise a
``TargetDef``'s zone number is mapped to a percentage range via the canonical
band specs in :mod:`endury.training.zones` (zone *numbers* are never emitted —
athletes customize their zone schemes).  Absolute units (``w``, ``bpm``,
``/km``, ``/100m``) are the last resort.

Traps encoded here: ``m`` means *minutes* (meters are ``mtr``), percentages are
whole numbers, and repeat blocks carry a blank line before and after.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from endury.planner.foundations.types import SportType
from endury.training.workout import (
    DurationDef,
    StructuredWorkout,
    TargetDef,
    WorkoutStep,
)
from endury.training.zones import (
    HR_BAND_SPECS,
    PACE_BAND_SPECS,
    POWER_BAND_SPECS,
    SWIM_PACE_BAND_SPECS,
    ZoneBandSpec,
)


@dataclass(frozen=True)
class SportThresholds:
    """Athlete thresholds for one sport, used to emit relative targets.

    Every field is optional: a missing threshold degrades emission to the
    zone-band percentage or, failing that, to absolute units.
    """

    ftp_watts: int | None = None
    lthr_bpm: int | None = None
    threshold_pace_sec_per_km: float | None = None
    css_sec_per_100m: float | None = None


#: Lowest percentage emitted for a zone-1 band.  ``HR_BAND_SPECS`` and
#: ``POWER_BAND_SPECS`` open zone 1 at 0.0, which would emit a meaningless
#: ``0-85% LTHR`` target; these floors keep recovery targets coachable.
#: intervals.icu prices a planned HR step at its band's midpoint and pairs
#: the upload by load, so the HR floor sits at an easy jog, not a walk.
_HR_ZONE_FLOOR = 0.65
_POWER_ZONE_FLOOR = 0.40

#: A step description is coaching prose; only the lead clause becomes the
#: DSL step label (see :func:`_label`).
_LABEL_SEPARATORS = ("—", "–", " - ", ";", ":")  # noqa: RUF001

#: Target types that make a step heart-rate led.
_HR_LEAD_TYPES = ("heart_rate_zone", "heart_rate")

#: Which target metric wins when a step's lead is not heart rate.
_TARGET_PREFERENCE: dict[SportType, tuple[str, ...]] = {
    SportType.RUNNING: (
        "pace",
        "pace_zone",
        "heart_rate_zone",
        "heart_rate",
        "power",
        "power_zone",
    ),
    SportType.CYCLING: ("power", "power_zone", "heart_rate_zone", "heart_rate"),
    SportType.SWIMMING: (
        "swim_pace",
        "swim_pace_zone",
        "heart_rate_zone",
        "heart_rate",
    ),
}

#: What an effort-led (RPE) step may take instead, per sport; a sport absent
#: here keeps ``_TARGET_PREFERENCE``. A run led by effort is a hill session
#: (hill repeats, a vert hike, downhill reps, a trail back-to-back) or one run
#: by feel (fartlek, a recovery jog): its pace extra is the flat-ground
#: equivalent of the effort, and a pace alert fails every step on a grade.
#: Heart rate still prices the planned event and pairs the upload, which no
#: target at all would not; a bike keeps its power, terrain or not.
_RPE_LEAD_PREFERENCE: dict[SportType, tuple[str, ...]] = {
    SportType.RUNNING: ("heart_rate_zone", "heart_rate"),
}


# Formatting primitives


def _format_time(seconds: float) -> str | None:
    """Format seconds as an intervals.icu duration (``1h2m30s``).

    Args:
        seconds: Step duration in seconds.

    Returns:
        The duration token, or ``None`` when it rounds to zero.
    """
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs:
        parts.append(f"{secs}s")
    return "".join(parts) or None


def _format_duration(duration: DurationDef | None) -> str | None:
    """Format a step duration as a time or distance token.

    Args:
        duration: The step's duration definition.

    Returns:
        ``"400mtr"`` for distance steps, a time token otherwise, or ``None``
        when the duration is absent or empty (the step is then dropped).
    """
    if duration is None:
        return None
    if duration.type == "distance" and duration.value_meters:
        return f"{round(duration.value_meters)}mtr"
    if duration.value_seconds is None:
        return None
    return _format_time(duration.value_seconds)


def _mmss(seconds: float) -> str:
    """Format seconds as ``M:SS`` for a pace value.

    Args:
        seconds: Pace in seconds (per km or per 100 m).

    Returns:
        The ``M:SS`` string, carrying 60 s up to the next minute.
    """
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _percent_range(low: float, high: float, unit: str) -> str:
    """Format a percentage range, collapsing equal bounds to one value.

    Args:
        low: Lower bound in percent.
        high: Upper bound in percent.
        unit: Threshold name appended after the percent sign (``""`` for
            power, ``"LTHR"``, ``"Pace"``).

    Returns:
        For example ``"85-95%"`` or ``"70-80% LTHR"``.
    """
    low_pct = round(low)
    high_pct = round(high)
    suffix = f"%{' ' + unit if unit else ''}"
    if low_pct == high_pct:
        return f"{low_pct}{suffix}"
    return f"{low_pct}-{high_pct}{suffix}"


def _band_percent(
    zone: int, specs: tuple[ZoneBandSpec, ...], floor: float, unit: str
) -> str:
    """Map a zone number to a percentage range via its band spec.

    Args:
        zone: Zone number from a ``TargetDef``.
        specs: The band table for the metric.
        floor: Lowest factor to emit (keeps zone 1 coachable).
        unit: Threshold name (``"LTHR"``, ``"Pace"``, or ``""``).

    Returns:
        The percentage range for that band, e.g. ``"85-90% LTHR"``;
        out-of-range zones clamp to the top band.
    """
    spec = next((s for s in specs if s.zone_number == zone), specs[-1])
    return _percent_range(
        max(spec.low_factor, floor) * 100, spec.high_factor * 100, unit
    )


# Per-metric target formatting


def _power_text(target: TargetDef, thresholds: SportThresholds) -> str | None:
    """Format a power target as ``% FTP`` (preferred) or absolute watts.

    Args:
        target: A ``power`` or ``power_zone`` target.
        thresholds: Athlete thresholds for the step's sport.

    Returns:
        The target token, or ``None`` when the target carries no usable data.
    """
    low, high = target.low_watts, target.high_watts
    if low and high and thresholds.ftp_watts:
        ftp = thresholds.ftp_watts
        return _percent_range(low / ftp * 100, high / ftp * 100, "")
    if target.zone is not None:
        return _band_percent(target.zone, POWER_BAND_SPECS, _POWER_ZONE_FLOOR, "")
    if low and high:
        return f"{low}-{high}w"
    return None


def _hr_text(target: TargetDef, thresholds: SportThresholds) -> str | None:
    """Format a heart-rate target as ``% LTHR`` (preferred) or absolute bpm.

    Args:
        target: A ``heart_rate`` or ``heart_rate_zone`` target.
        thresholds: Athlete thresholds for the step's sport.

    Returns:
        The target token, or ``None`` when the target carries no usable data.
    """
    low, high = target.low_bpm, target.high_bpm
    if low and high and thresholds.lthr_bpm:
        lthr = thresholds.lthr_bpm
        return _percent_range(low / lthr * 100, high / lthr * 100, "LTHR")
    if target.zone is not None:
        return _band_percent(target.zone, HR_BAND_SPECS, _HR_ZONE_FLOOR, "LTHR")
    if high and thresholds.lthr_bpm:
        # A zone-1 ceiling with no zone number (the band crosses the
        # athlete's own zones): the floor keeps it coachable as above.
        return _percent_range(
            _HR_ZONE_FLOOR * 100, high / thresholds.lthr_bpm * 100, "LTHR"
        )
    if low and high:
        return f"{low}-{high}bpm"
    return None


def _pace_text(target: TargetDef, thresholds: SportThresholds) -> str | None:
    """Format a run-pace target as ``% Pace`` (preferred) or absolute min/km.

    Percent-of-threshold is speed-relative: the *faster* bound of the pace
    band becomes the *higher* percentage.

    Args:
        target: A ``pace`` or ``pace_zone`` target.
        thresholds: Athlete thresholds for the step's sport.

    Returns:
        The target token, or ``None`` when the target carries no usable data.
    """
    low, high = target.low_min_per_km, target.high_min_per_km
    if low and high and thresholds.threshold_pace_sec_per_km:
        threshold = thresholds.threshold_pace_sec_per_km
        return _percent_range(
            threshold / (high * 60.0) * 100, threshold / (low * 60.0) * 100, "Pace"
        )
    if target.zone is not None:
        return _band_percent(target.zone, PACE_BAND_SPECS, 0.0, "Pace")
    if low and high:
        return f"{_mmss(low * 60.0)}/km-{_mmss(high * 60.0)}/km Pace"
    return None


def _swim_text(target: TargetDef, thresholds: SportThresholds) -> str | None:
    """Format a swim-pace target as ``% Pace`` (preferred) or absolute /100m.

    Args:
        target: A ``swim_pace`` or ``swim_pace_zone`` target.
        thresholds: Athlete thresholds for the step's sport.

    Returns:
        The target token, or ``None`` when the target carries no usable data.
    """
    low, high = target.low_sec_per_100m, target.high_sec_per_100m
    if low and high and thresholds.css_sec_per_100m:
        css = thresholds.css_sec_per_100m
        return _percent_range(css / high * 100, css / low * 100, "Pace")
    if target.zone is not None:
        return _band_percent(target.zone, SWIM_PACE_BAND_SPECS, 0.0, "Pace")
    if low and high:
        return f"{_mmss(low)}/100m-{_mmss(high)}/100m Pace"
    return None


_HANDLERS: dict[str, Callable[[TargetDef, SportThresholds], str | None]] = {
    "power": _power_text,
    "power_zone": _power_text,
    "heart_rate": _hr_text,
    "heart_rate_zone": _hr_text,
    "pace": _pace_text,
    "pace_zone": _pace_text,
    "swim_pace": _swim_text,
    "swim_pace_zone": _swim_text,
}


# Step and workout assembly


def _step_targets(step: WorkoutStep) -> list[TargetDef]:
    """Collect a step's primary, secondary, and additional targets.

    Args:
        step: The workout step.

    Returns:
        All non-``None`` targets in declaration order.
    """
    candidates = [step.target, step.secondary_target, *(step.additional_targets or [])]
    return [t for t in candidates if t is not None]


def _target_text(
    step: WorkoutStep, thresholds: SportThresholds, sport: SportType
) -> str | None:
    """Pick and format the best target for a step.

    A heart-rate lead wins whenever it can be rendered; an effort-led run
    takes its heart-rate band or nothing; otherwise the sport's fixed metric
    order picks among all the step's targets.

    Args:
        step: The workout step.
        thresholds: Athlete thresholds for the sport.
        sport: Sport of the workout, which sets the metric preference.

    Returns:
        The formatted target token, or ``None`` when no target is
        representable (RPE- or effort-only steps).
    """
    lead = step.target
    if lead is not None and lead.type in _HR_LEAD_TYPES:
        text = _hr_text(lead, thresholds)
        if text is not None:
            return text
    order = _TARGET_PREFERENCE[sport]
    if lead is not None and lead.type == "rpe":
        order = _RPE_LEAD_PREFERENCE.get(sport, order)
    by_type: dict[str, TargetDef] = {}
    for target in _step_targets(step):
        by_type.setdefault(target.type, target)
    for wanted in order:
        preferred = by_type.get(wanted)
        if preferred is not None:
            text = _HANDLERS[wanted](preferred, thresholds)
            if text is not None:
                return text
    return None


def _rpe_note(targets: list[TargetDef]) -> str | None:
    """Return the label suffix carrying a step's RPE prescription.

    intervals.icu's DSL has no RPE target syntax, so an RPE-only step
    (every fitness test's main effort) would otherwise arrive on the
    athlete's device as an untargeted block. The prescription is folded
    into the step *name* instead, where it survives the parser and stays
    visible in the workout chart.

    Args:
        targets: The step's targets.

    Returns:
        For example ``"RPE 9/10"``, or ``None`` when no RPE is prescribed.
    """
    for target in targets:
        if target.type == "rpe" and target.rpe is not None:
            return f"RPE {target.rpe}/10"
    return None


def _cadence_text(targets: list[TargetDef]) -> str | None:
    """Format a cadence target as the DSL's single-value ``rpm`` token.

    Args:
        targets: The step's targets.

    Returns:
        For example ``"90rpm"``, or ``None`` when no bounded cadence target
        is present.
    """
    for target in targets:
        if target.type == "cadence" and target.low_rpm and target.high_rpm:
            return f"{round((target.low_rpm + target.high_rpm) / 2)}rpm"
    return None


def _clauses(text: str) -> list[str]:
    """Split a description into its separator-delimited clauses."""
    parts = [text]
    for separator in _LABEL_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip() for part in parts if part.strip()]


def _label(text: str | None) -> str | None:
    """Reduce a description to a short, parser-safe step label.

    Everything the parser sees before the duration is the label, so long
    coaching prose is both unreadable in the step chart and dangerous:
    ``"... in the last 100 m"`` would be read as a 100-*minute* duration.
    A label containing digits is therefore never emitted — but the lead
    clause is often exactly the digit-bearing one (``"Block 1/3 — Easy
    aerobic effort"``), so the *first digit-free* clause wins instead of
    unconditionally the first.

    Args:
        text: The step or repeat description.

    Returns:
        The label, or ``None`` when every clause is empty or unsafe.
    """
    if not text:
        return None
    for clause in _clauses(" ".join(text.split())):
        if not any(char in "0123456789" for char in clause):
            return clause
    return None


def _step_line(
    step: WorkoutStep, thresholds: SportThresholds, sport: SportType
) -> str | None:
    """Render one step as a ``- `` DSL line.

    Args:
        step: The workout step (never a repeat block).
        thresholds: Athlete thresholds for the sport.
        sport: Sport of the workout.

    Returns:
        The step line, or ``None`` when the step has no usable duration.
    """
    duration = _format_duration(step.duration)
    if duration is None:
        return None
    targets = _step_targets(step)
    label = _label(step.description)
    target_text = _target_text(step, thresholds, sport)
    if target_text is None:
        note = _rpe_note(targets)
        if note is not None:
            label = f"{label} {note}" if label else note
    tokens = [
        token
        for token in (
            label,
            duration,
            target_text,
            _cadence_text(targets),
        )
        if token is not None
    ]
    return "- " + " ".join(tokens)


def _flatten(steps: list[WorkoutStep]) -> list[WorkoutStep]:
    """Expand nested repeat blocks into a flat step list.

    intervals.icu does not support nested repeats, so an inner block is
    unrolled into its individual reps.

    Args:
        steps: Steps inside a repeat block.

    Returns:
        The flattened steps.
    """
    flat: list[WorkoutStep] = []
    for step in steps:
        if step.type == "repeat" and step.steps and step.iterations:
            for _ in range(step.iterations):
                flat.extend(_flatten(step.steps))
        else:
            flat.append(step)
    return flat


def workout_to_dsl(
    workout: StructuredWorkout, thresholds: SportThresholds, sport: SportType
) -> str:
    """Render a structured workout as intervals.icu workout DSL text.

    The result contains only step lines and repeat blocks — the caller owns
    the surrounding coaching prose and the event name.

    Args:
        workout: The materialized workout to serialize.
        thresholds: Athlete thresholds for *sport*; missing values degrade
            targets to zone-band percentages or absolute units.
        sport: Sport of the workout, which sets the target-metric preference.

    Returns:
        The DSL text (``""`` when no step is representable), with a blank
        line before and after every repeat block and no trailing newline.
    """
    lines: list[str] = []
    for step in workout.steps:
        if step.type == "repeat" and step.steps and step.iterations:
            block = [
                line
                for line in (
                    _step_line(sub, thresholds, sport) for sub in _flatten(step.steps)
                )
                if line is not None
            ]
            if not block:
                continue
            header = _label(step.description)
            count = f"{step.iterations}x"
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"{header} {count}" if header else count)
            lines.extend(block)
            lines.append("")
            continue
        line = _step_line(step, thresholds, sport)
        if line is not None:
            lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


__all__ = ["SportThresholds", "workout_to_dsl"]
