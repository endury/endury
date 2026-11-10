"""Threshold tools for the coach agent.

intervals.icu owns the athlete's thresholds and zones: the coach reads the
per-sport settings rows and reports them, and writes them only through an
Apply card (``set_athlete_thresholds``). Each sport carries its thresholds
(FTP, LTHR, max HR, threshold pace, CSS) alongside the athlete's own zone
tables, the ones the calendar and the cards number by, so the coach can say
what a zone number means in native units. Where intervals.icu holds a
threshold but no zones, Endury's bands stand in. ``estimate_threshold_from_race``
turns a recent race time into a running threshold pace.
"""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import ValidationError

from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.activity_tools import _clock
from endury.coach.tools.registry import coach_tool
from endury.domain.analysis.zones import (
    compute_hr_zones,
    compute_pace_zones,
    compute_power_zones,
    compute_swim_pace_zones,
)
from endury.domain.models.zones import ZoneMetric, ZoneSet
from endury.intervals.client import IntervalsClient
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    SportSettingsRow,
)
from endury.intervals.sport_settings import set_thresholds
from endury.planner.foundations.types import SportType
from endury.planning.load_planner_metrics import athlete_zone_sets
from endury.planning.wire.requests import ThresholdsRequest

#: Native units per metric, as the band tables report them.
_UNITS: dict[ZoneMetric, str] = {
    ZoneMetric.HEART_RATE: "bpm",
    ZoneMetric.POWER: "watts",
    ZoneMetric.PACE: "min/km",
    ZoneMetric.SWIM_PACE: "sec/100m",
}

# intent: Riegel (1981), "Athletic records and human endurance": race time
# grows as distance^1.06 across running distances from about 3 km to the
# marathon; the hour-race pace it predicts is the running threshold pace.
RIEGEL_EXPONENT = 1.06

#: Finish times the race estimate accepts, in seconds (10 min to 6 h).
_RACE_SECONDS = (600, 21600)
#: The shortest race the estimate reads, in km.
_MIN_RACE_KM = 3.0

#: ``set_athlete_thresholds`` argument -> its ThresholdsRequest field, the
#: athlete's name for it, and how a value of that field reads.
_THRESHOLD_ARGS: dict[str, tuple[str, str, Any]] = {
    "threshold_pace_min_km": (
        "threshold_pace_sec_per_km",
        "threshold pace",
        lambda sec: f"{_clock(sec)}/km",
    ),
    "lthr_bpm": ("lthr_bpm", "threshold heart rate", lambda bpm: f"{bpm} bpm"),
    "ftp_watts": ("ftp_watts", "FTP", lambda watts: f"{watts} W"),
    "css_min_100m": (
        "css_sec_per_100m",
        "CSS pace",
        lambda sec: f"{_clock(sec)}/100 m",
    ),
}


def make_zone_tools(intervals_client: IntervalsClient) -> list:
    """Create the threshold-reporting tool with an injected intervals client.

    Args:
        intervals_client: Read client for the intervals.icu athlete profile.

    Returns:
        List with the single threshold coach tool.
    """
    _client = intervals_client

    @coach_tool
    def get_athlete_thresholds(sport: str | None = None) -> str:
        """Get the athlete's training thresholds from intervals.icu.

        Reports FTP, LTHR, max HR, run threshold pace, and swim CSS as
        configured in the athlete's intervals.icu sport settings. Use this
        when discussing intensity, prescribing targets, or sanity-checking a
        workout. To change one, offer ``set_athlete_thresholds``. Each
        sport entry also carries a "zones" band table
        (heart rate in bpm, pace in min/km, power in watts, swim pace in
        sec/100m): the athlete's own intervals.icu zones, which planned
        workouts and the calendar are numbered by. Those tables are labels,
        not targets: an Endury workout's pace, watts or bpm band is its
        prescription, and the zone number beside it is only the athlete's
        name for that band — never re-prescribe a session from these
        tables, and never call a band wrong because its number is not the
        zone its session name suggests. A pool swim carries no heart-rate
        target: it is prescribed by pace.

        Args:
            sport: Optional sport filter ("running", "cycling", "swimming").

        Returns:
            JSON string with one entry per sport that has settings.
        """
        try:
            profile = _client.get_athlete()
            rows = profile.sport_settings or []
            entries = [
                _threshold_entry(found_sport, row)
                for found_sport, row in _settings_by_sport(rows).items()
                if sport is None or found_sport.value == sport.lower()
            ]
            if not entries:
                return json.dumps(
                    {
                        "message": (
                            f"No intervals.icu sport settings for '{sport}'."
                            if sport
                            else "No sport settings configured in intervals.icu."
                        )
                    }
                )
            return json.dumps(entries, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve athlete thresholds", exc)

    @coach_tool
    def set_athlete_thresholds(
        sport: str,
        threshold_pace_min_km: str | None = None,
        lthr_bpm: int | None = None,
        ftp_watts: int | None = None,
        css_min_100m: str | None = None,
        confirm: bool = False,
    ) -> str:
        """Set the athlete's thresholds for one sport on intervals.icu.

        Thresholds live on intervals.icu, and every later session's pace,
        heart-rate and power targets are read from them. Offer this after a
        fitness-test readout (``get_session_vs_actual``'s
        ``suggested_thresholds``), after ``estimate_threshold_from_race``,
        or when the athlete gives you a number (a lab test, or the lactate
        threshold their watch shows). Never invent a value: pass only
        numbers that came from one of those. Pass only the values that
        change; one left out stays as it is.

        Runs as a preview: the reply lists each value's current and proposed
        setting, and the card does the asking.

        Reply contract — when relaying the result:
        * status "preview" means nothing is saved yet — name each change
          (current to proposed); the card asks the athlete.
        * Only status "ok" means intervals.icu was updated.
        * status "error" means nothing was saved; relay ``message``.

        Args:
            sport: "running", "cycling" or "swimming".
            threshold_pace_min_km: Running threshold pace as "M:SS" per km,
                e.g. "4:52".
            lthr_bpm: Lactate threshold heart rate in bpm (running or
                cycling).
            ftp_watts: Cycling functional threshold power in watts.
            css_min_100m: Swim critical swim speed as "M:SS" per 100 m,
                e.g. "1:35".
            confirm: Filled in by the app when the athlete applies the
                card; not part of your call.

        Returns:
            JSON string: the preview (``status`` "preview"), the saved
            values (``status`` "ok"), or an error.
        """
        args = {
            "threshold_pace_min_km": threshold_pace_min_km,
            "lthr_bpm": lthr_bpm,
            "ftp_watts": ftp_watts,
            "css_min_100m": css_min_100m,
        }
        try:
            request = _thresholds_request(sport, args)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        try:
            planner_sport = SportType(request.sport)
            row = _settings_by_sport(_client.get_athlete().sport_settings or []).get(
                planner_sport
            )
            if row is None:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"intervals.icu has no {request.sport} sport settings "
                            "yet. The athlete adds the sport under Settings → "
                            "Sports on intervals.icu first."
                        ),
                    }
                )
            changes = _threshold_changes(request, row)
            if not confirm:
                return json.dumps(
                    {
                        "status": "preview",
                        "confirm_required": True,
                        "sport": request.sport,
                        "changes": changes,
                        "message": "Nothing is saved yet; the card asks the athlete.",
                    }
                )
            values = request.model_dump(exclude={"sport"}, exclude_none=True)
            types = set_thresholds(_client, planner_sport, **values)
            return json.dumps(
                {
                    "status": "ok",
                    "confirm_required": False,
                    "sport": request.sport,
                    "changes": changes,
                    "updated_types": types,
                    "message": (
                        f"Saved on intervals.icu ({', '.join(types)}). Every later "
                        "session's targets read the new values."
                    ),
                }
            )
        except Exception as exc:
            return tool_error_response("set the athlete's thresholds", exc)

    @coach_tool
    def estimate_threshold_from_race(distance_km: float, finish_time: str) -> str:
        """Estimate running threshold pace from a recent race result.

        Threshold pace is roughly the pace the athlete could hold for an
        hour, predicted here from a race of 3 km or longer run flat out in
        the last couple of months. A recent race is enough to set a
        threshold; the booked test is a check, not a prerequisite. Use it
        when the athlete has no threshold pace yet or the one on file looks
        stale, then offer ``set_athlete_thresholds`` with the estimate.

        Args:
            distance_km: Race distance in km (3 or more).
            finish_time: Finish time as "h:mm:ss" or "mm:ss".

        Returns:
            JSON string with the threshold pace estimate, the race pace and
            the next step, or an error.
        """
        try:
            seconds = _clock_seconds(finish_time)
        except ValueError as exc:
            return json.dumps({"status": "error", "message": str(exc)})
        if distance_km < _MIN_RACE_KM:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        "Use a race of 3 km or longer; shorter efforts say too "
                        "little about threshold pace."
                    ),
                }
            )
        if not _RACE_SECONDS[0] <= seconds <= _RACE_SECONDS[1]:
            return json.dumps(
                {
                    "status": "error",
                    "message": "Use a finish time between 10 minutes and 6 hours.",
                }
            )
        # T2 = T1 x (D2 / D1)^k, solved for the distance D2 that takes an hour.
        hour_km = distance_km * (3600.0 / seconds) ** (1.0 / RIEGEL_EXPONENT)
        pace = _clock(3600.0 / hour_km)
        return json.dumps(
            {
                "status": "ok",
                "threshold_pace_min_km": pace,
                "race_pace_min_km": _clock(seconds / distance_km),
                "one_hour_distance_km": round(hour_km, 2),
                "next_step": (
                    "Offer set_athlete_thresholds with sport running and "
                    f'threshold_pace_min_km "{pace}" as an Apply card.'
                ),
            }
        )

    return [
        get_athlete_thresholds,
        set_athlete_thresholds,
        estimate_threshold_from_race,
    ]


def _clock_seconds(text: str) -> int:
    """Parse ``"M:SS"`` or ``"H:MM:SS"`` into seconds.

    Raises:
        ValueError: The text is not a clock time.
    """
    parts = text.strip().split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise ValueError(f"'{text}' is not a time; write it as M:SS or H:MM:SS.")
    numbers = [int(part) for part in parts]
    if any(number >= 60 for number in numbers[1:]):
        raise ValueError(f"'{text}' is not a time; minutes and seconds stay below 60.")
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return seconds


def _thresholds_request(sport: str, args: dict[str, Any]) -> ThresholdsRequest:
    """Validate the tool's arguments with the Planning Inputs bounds.

    Raises:
        ValueError: Nothing to set, a pace that is not M:SS, or a value
            outside the bounds ``put_thresholds`` uses.
    """
    body: dict[str, Any] = {"sport": sport.strip().lower()}
    for arg, value in args.items():
        if value is None:
            continue
        field = _THRESHOLD_ARGS[arg][0]
        body[field] = _clock_seconds(value) if arg.endswith(("_km", "_100m")) else value
    if len(body) == 1:
        raise ValueError("Pass at least one threshold to set.")
    try:
        return ThresholdsRequest.model_validate(body)
    except ValidationError as exc:
        raise ValueError(_bounds_message(exc)) from None


def _bounds_message(exc: ValidationError) -> str:
    """Say which argument was refused, in the units the tool takes."""
    by_field = {spec[0]: (arg, spec[2]) for arg, spec in _THRESHOLD_ARGS.items()}
    lines: list[str] = []
    for error in exc.errors():
        field = str(error["loc"][0]) if error["loc"] else ""
        if field in by_field and error["type"] in (
            "greater_than_equal",
            "less_than_equal",
        ):
            arg, show = by_field[field]
            meta = ThresholdsRequest.model_fields[field].metadata
            low = next(m.ge for m in meta if hasattr(m, "ge"))
            high = next(m.le for m in meta if hasattr(m, "le"))
            lines.append(f"{arg} must be between {show(low)} and {show(high)}.")
        else:
            line = str(error["msg"]).removeprefix("Value error, ")
            for name, (arg, _show) in by_field.items():
                line = line.replace(name, arg)
            lines.append(line)
    return " ".join(lines)


def _current_value(field: str, row: SportSettingsRow) -> Any:
    """The value a ThresholdsRequest field holds on intervals.icu now, or None."""
    if field == "lthr_bpm":
        return row.lthr
    if field == "ftp_watts":
        return row.ftp
    if not row.threshold_pace:
        return None
    # threshold_pace is metres per second on every row.
    metres = 1000.0 if field == "threshold_pace_sec_per_km" else 100.0
    return round(metres / row.threshold_pace)


def _threshold_changes(
    request: ThresholdsRequest, row: SportSettingsRow
) -> list[dict[str, Any]]:
    """Each value being set: its name, current and proposed, as the athlete reads it."""
    changes: list[dict[str, Any]] = []
    for field, label, show in _THRESHOLD_ARGS.values():
        proposed = getattr(request, field)
        if proposed is None:
            continue
        current = _current_value(field, row)
        changes.append(
            {
                "label": label,
                "current": show(current) if current is not None else None,
                "proposed": show(proposed),
            }
        )
    return changes


def _settings_by_sport(
    rows: list[SportSettingsRow],
) -> dict[SportType, SportSettingsRow]:
    """Map each planner sport to the first settings row covering it.

    Load-only types (Hike/Walk) never claim a sport's row: a dedicated
    hiking row must not supply RUNNING thresholds.
    """
    by_sport: dict[SportType, SportSettingsRow] = {}
    for row in rows:
        for type_name in row.types or []:
            if type_name in LOAD_ONLY_ACTIVITY_TYPES:
                continue
            found = SPORT_TYPE_MAP.get(type_name)
            if found is not None and found not in by_sport:
                by_sport[found] = row
    return by_sport


def _band_table(zone_set: ZoneSet) -> dict[str, Any]:
    """Project a ``ZoneSet`` to the compact band-table shape the tool reports.

    Args:
        zone_set: Zone bands for one sport + metric.

    Returns:
        ``{"units": ..., "bands": [{"zone", "name", "low", "high"}, ...]}``,
        bpm/watts rounded to whole numbers, pace/swim-pace to 1 decimal; a
        pace zone 1 with no slow limit reports ``null`` there.
    """
    units = _UNITS[zone_set.metric]
    round_fn = round if units in ("bpm", "watts") else (lambda v: round(v, 1))
    return {
        "units": units,
        "bands": [
            {
                "zone": band.zone_number,
                "name": band.name,
                "low": round_fn(band.low),
                "high": None if math.isinf(band.high) else round_fn(band.high),
            }
            for band in zone_set.bands
        ],
    }


def _endury_zone_sets(sport: SportType, row: SportSettingsRow) -> list[ZoneSet]:
    """Build Endury's band tables for the thresholds this row holds."""
    sets: list[ZoneSet] = []
    if row.lthr:
        sets.append(compute_hr_zones(int(row.lthr), sport=sport.value))
    if sport is SportType.RUNNING and row.threshold_pace:
        sets.append(compute_pace_zones((1000.0 / row.threshold_pace) / 60.0))
    if sport is SportType.CYCLING and row.ftp:
        sets.append(compute_power_zones(int(row.ftp)))
    if sport is SportType.SWIMMING and row.threshold_pace:
        sets.append(compute_swim_pace_zones(100.0 / row.threshold_pace))
    return sets


def _sport_zones(sport: SportType, row: SportSettingsRow) -> dict[str, Any]:
    """Band tables per metric: the athlete's own zones over Endury's bands."""
    zones = {zs.metric.value: _band_table(zs) for zs in _endury_zone_sets(sport, row)}
    zones.update(
        {zs.metric.value: _band_table(zs) for zs in athlete_zone_sets(sport, row)}
    )
    return zones


def _threshold_entry(sport: SportType, row: SportSettingsRow) -> dict[str, Any]:
    """Project one settings row to the thresholds relevant for its sport.

    ``threshold_pace`` is metres per second on every row: run threshold
    pace is ``1000 / v`` sec/km and swim CSS is ``100 / v`` sec/100 m.
    """
    entry: dict[str, Any] = {
        "sport": sport.value,
        "lthr_bpm": row.lthr,
        "max_hr_bpm": row.max_hr,
    }
    if sport is SportType.CYCLING:
        entry["ftp_watts"] = row.ftp
    if sport is SportType.RUNNING and row.threshold_pace:
        entry["threshold_pace_sec_per_km"] = round(1000.0 / row.threshold_pace, 1)
    if sport is SportType.SWIMMING and row.threshold_pace:
        entry["css_sec_per_100m"] = round(100.0 / row.threshold_pace, 1)
    zones = _sport_zones(sport, row)
    if zones:
        entry["zones"] = zones
    return entry
