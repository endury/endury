"""The demo athlete: Sam, four weeks into a half-Ironman plan.

Booting with ``{"demo": "1"}`` runs Endury against an in-memory
:class:`~endury.intervals.fake.FakeIntervals` account instead of
intervals.icu, so a visitor can try every screen without a key. The account
dies with the worker: nothing is written anywhere real.

:func:`account` builds Sam's intervals.icu side: swim, bike and run
thresholds, twelve weeks of training before the plan and two weeks of
wellness. :func:`seed` then generates and commits the plan as if on the
Monday four weeks ago (the clock is shifted for that call only, so the
planner sizes it from the history it saw then), and turns every plan
session before today into a completed, paired activity with a debrief and a
trace — all but one, which shows as missed — plus an unplanned ride each
week. Runs and rides carry sampled streams; a pool swim carries its lengths
as interval rows, the way intervals.icu scores one. The same day always
gives the same demo.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import random
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, timedelta
from itertools import groupby
from typing import Any

from endury import store
from endury.browser import BrowserApp
from endury.common import clock
from endury.database.sqlite.repositories.plan import PlanSessionRow
from endury.intervals.fake import FakeIntervals
from endury.intervals.models import (
    ActivityStream,
    AthleteProfile,
    IntervalRow,
    IntervalsActivityDetail,
    SportSettingsRow,
    WellnessDay,
)
from endury.intervals.push import session_external_id
from endury.planning.generate_plan import run_commit, run_generate
from endury.planning.wire.requests import (
    CommitRequest,
    GenerateRequest,
    GenerateSuccess,
    RaceGoalModel,
)

#: The boot setting that asks for the demo athlete instead of intervals.icu.
DEMO_SETTINGS_KEY = "demo"

#: What the demo plan is called on every screen.
PLAN_NAME = "Sam's half distance"

#: The variable ``endury.common.clock`` reads a simulated date from.
_SIM_DATE_ENV = "COACHING_BUDDY_SIM_DATE"

#: Plan weeks already behind Sam on the day the demo boots.
_WEEKS_INTO_PLAN = 4
_PLAN_WEEKS = 18
_HISTORY_WEEKS = 12
_WELLNESS_DAYS = 14

_RUN_LTHR = 168
_RUN_THRESHOLD_PACE = 1000 / 290  # m/s: 4:50 per km
_RIDE_FTP = 210
_RIDE_LTHR = 160
_SWIM_CSS = 100 / 110  # m/s: 1:50 per 100 m
_MAX_HR = 190
_RIDE_SPEED_AT_FTP = 11.0  # m/s on the flat at FTP, for a ride's distance
_POOL_LENGTH_M = 25.0

#: intervals.icu's default zone bounds: bpm as a fraction of LTHR, and
#: percent of threshold speed or FTP.
_HR_ZONE_FRACTIONS = (0.81, 0.89, 0.93, 0.99, 1.02, 1.06)
_PACE_ZONES = [77.5, 87.7, 94.3, 100.0, 103.4, 111.5, 999.0]
_POWER_ZONES = [55.0, 75.0, 90.0, 105.0, 120.0, 150.0, 999.0]

#: One week of Sam's training before the plan: (weekday, type, minutes
#: low, minutes high, intensity factor).
_HISTORY_WEEK = (
    (0, "Swim", 40, 55, 0.78),
    (1, "Run", 40, 50, 0.72),
    (2, "Swim", 35, 50, 0.80),
    (3, "Ride", 55, 75, 0.70),
    (5, "Ride", 90, 150, 0.66),
    (6, "Run", 60, 85, 0.74),
)

#: A plan session's sport, as the row stores it, to the activity type.
_KIND_BY_SPORT = {"running": "Run", "cycling": "Ride", "swimming": "Swim"}

#: The heart-rate threshold an activity type is scored against.
_LTHR_BY_KIND = {"Run": _RUN_LTHR, "Ride": _RIDE_LTHR, "Swim": _RIDE_LTHR}

#: Threshold speed (m/s) for the types whose steps are set in metres.
_THRESHOLD_SPEED = {"Run": _RUN_THRESHOLD_PACE, "Swim": _SWIM_CSS}

#: A step's intensity, as a fraction of threshold, by its target zone.
_ZONE_INTENSITY = {1: 0.65, 2: 0.75, 3: 0.86, 4: 0.97, 5: 1.06}
_STEP_INTENSITY = {"warmup": 0.66, "cooldown": 0.62, "recovery": 0.6, "rest": 0.5}

#: Seconds between two samples of a demo effort profile.
_SAMPLE_SECONDS = 10


def seed_monday(today: date) -> date:
    """Return the Monday the demo plan started: four weeks before this one."""
    return today - timedelta(days=today.weekday(), weeks=_WEEKS_INTO_PLAN)


def _rng(day: date) -> random.Random:
    """Seeded randomness: the same day always draws the same demo."""
    return random.Random(day.toordinal())  # noqa: S311 - sample data, not security


@contextmanager
def _clock_at(day: date) -> Iterator[None]:
    """Make ``clock.today()`` read ``day`` inside the block, then restore it."""
    previous = os.environ.get(_SIM_DATE_ENV)
    os.environ[_SIM_DATE_ENV] = day.isoformat()
    try:
        yield
    finally:
        if previous is None:
            del os.environ[_SIM_DATE_ENV]
        else:
            os.environ[_SIM_DATE_ENV] = previous


def _profile() -> AthleteProfile:
    """Sam's thresholds, with Garmin taking the planned workouts."""
    return AthleteProfile(
        id="demo",
        sportSettings=[
            SportSettingsRow(
                id=1,
                types=["Run", "TrailRun"],
                lthr=_RUN_LTHR,
                max_hr=_MAX_HR,
                threshold_pace=_RUN_THRESHOLD_PACE,
            ),
            SportSettingsRow(
                id=2,
                types=["Ride", "VirtualRide"],
                ftp=_RIDE_FTP,
                lthr=_RIDE_LTHR,
                max_hr=_MAX_HR,
            ),
            SportSettingsRow(id=3, types=["Swim"], threshold_pace=_SWIM_CSS),
        ],
        icu_garmin_upload_workouts=True,
    )


def account(today: date) -> FakeIntervals:
    """Build Sam's intervals.icu account as it stands before the plan.

    Args:
        today: The day the demo boots; the history ends the Sunday before
            :func:`seed_monday`.

    Returns:
        The in-memory account the demo boots against.
    """
    rng = _rng(today)
    fake = FakeIntervals(athlete=_profile())
    first = seed_monday(today) - timedelta(weeks=_HISTORY_WEEKS)
    for week in range(_HISTORY_WEEKS):
        for weekday, kind, low, high, intensity in _HISTORY_WEEK:
            day = first + timedelta(weeks=week, days=weekday)
            seconds = round(rng.uniform(low, high)) * 60
            profile = _flat_profile(seconds, intensity)
            _record(fake, rng, day, kind, profile, event_id=None)
    for back in range(_WELLNESS_DAYS, 0, -1):
        fake.wellness.append(
            WellnessDay(
                id=(today - timedelta(days=back)).isoformat(),
                sleepSecs=round(rng.uniform(6.6, 8.2) * 3600),
                hrv=round(rng.uniform(58, 72), 1),
                restingHR=rng.randint(46, 51),
            )
        )
    return fake


# Effort profiles


def _flat_profile(seconds: int, intensity: float) -> list[float]:
    """An even effort with a short warm-up and cool-down, one value a sample."""
    count = max(seconds // _SAMPLE_SECONDS, 12)
    edge = count // 10
    return [
        intensity * (0.85 if i < edge or i >= count - edge else 1.0)
        for i in range(count)
    ]


def _step_intensity(step: Mapping[str, Any]) -> float:
    """A workout step's effort as a fraction of threshold."""
    target = step.get("target") or {}
    zone = target.get("zone")
    if zone in _ZONE_INTENSITY:
        return _ZONE_INTENSITY[zone]
    return _STEP_INTENSITY.get(str(step.get("type")), 0.78)


def _steps_profile(
    steps: list[Any], stretch: float, threshold_speed: float | None
) -> list[float]:
    """Unroll workout steps into one intensity value a sample.

    A step set in metres (a swim set) lasts as long as its distance takes at
    the step's effort against ``threshold_speed``.
    """
    profile: list[float] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        children = step.get("steps") or []
        if children:
            block = _steps_profile(children, stretch, threshold_speed)
            profile.extend(block * int(step.get("iterations") or 1))
            continue
        intensity = _step_intensity(step)
        seconds = float(step.get("duration_seconds") or 0)
        if not seconds and threshold_speed:
            meters = float(step.get("duration_meters") or 0)
            seconds = meters / (threshold_speed * intensity)
        profile.extend([intensity] * round(seconds * stretch / _SAMPLE_SECONDS))
    return profile


def _scaled(profile: list[float], intensity: float) -> list[float]:
    """Scale ``profile`` so its root-mean-square effort is ``intensity``."""
    rms = math.sqrt(sum(v * v for v in profile) / len(profile))
    return [v * intensity / rms for v in profile]


# Activities


def _hr_zones(lthr: int) -> list[int]:
    """HR zone upper bounds for ``lthr``, the top one at max HR."""
    return [round(lthr * f) for f in _HR_ZONE_FRACTIONS] + [_MAX_HR]


def _zone_times(heartrate: list[float], bounds: list[int]) -> list[int]:
    """Seconds spent in each HR zone."""
    times = [0] * len(bounds)
    for bpm in heartrate:
        zone = next((i for i, b in enumerate(bounds) if bpm <= b), len(bounds) - 1)
        times[zone] += _SAMPLE_SECONDS
    return times


def _name_and_start(day: date, kind: str) -> tuple[str, str]:
    """A device-style name and the local start it was named after."""
    hour = 9 if day.weekday() >= 5 else (7, 12, 18)[day.toordinal() % 3]
    part = {7: "Morning", 9: "Morning", 12: "Lunch", 18: "Evening"}[hour]
    start = f"{day.isoformat()}T{hour:02d}:{(day.day * 7) % 50:02d}:00"
    return f"{part} {kind}", start


def _streams(
    rng: random.Random, kind: str, profile: list[float]
) -> tuple[dict[str, list[float]], float]:
    """Build the sampled streams for an effort profile.

    Returns:
        The streams by type, and the mean speed (m/s) or power (W).
    """
    lthr = _LTHR_BY_KIND[kind]
    heartrate: list[float] = [
        round(lthr * (0.45 + 0.55 * v) + rng.uniform(-2, 2)) for v in profile
    ]
    phase = rng.uniform(0, math.tau)
    altitude = [
        round(40 + 25 * math.sin(phase + i / 30), 1) for i in range(len(profile))
    ]
    if kind == "Ride":
        main: list[float] = [
            round(_RIDE_FTP * v * rng.uniform(0.85, 1.15)) for v in profile
        ]
        streams = {"watts": main}
    else:
        threshold = _THRESHOLD_SPEED[kind]
        main = [round(threshold * v * rng.uniform(0.97, 1.03), 3) for v in profile]
        streams = {"velocity_smooth": main}
    streams.update(heartrate=heartrate, altitude=altitude)
    return streams, sum(main) / len(main)


def _pace_zone(intensity: float) -> int:
    """The 1-based pace zone a fraction of threshold speed falls in."""
    pct = intensity * 100
    return next((i + 1 for i, top in enumerate(_PACE_ZONES) if pct <= top), 7)


def _swim_rows(
    profile: list[float], streams: Mapping[str, list[float]]
) -> list[IntervalRow]:
    """A pool swim's sets: one row per stretch of even effort, in lengths."""
    rows: list[IntervalRow] = []
    start = 0
    for intensity, group in groupby(enumerate(profile), key=lambda item: item[1]):
        indexes = [i for i, _ in group]
        seconds = len(indexes) * _SAMPLE_SECONDS
        speed = sum(streams["velocity_smooth"][i] for i in indexes) / len(indexes)
        lengths = max(1, round(speed * seconds / _POOL_LENGTH_M))
        rows.append(
            IntervalRow(
                type="RECOVERY" if intensity < 0.62 else "WORK",
                start_time=start,
                elapsed_time=seconds,
                moving_time=seconds,
                distance=lengths * _POOL_LENGTH_M,
                average_speed=round(speed, 3),
                average_heartrate=round(
                    sum(streams["heartrate"][i] for i in indexes) / len(indexes)
                ),
                zone=_pace_zone(intensity),
                intensity=round(intensity * 100, 1),
            )
        )
        start += seconds
    return rows


def _kind_fields(kind: str, mean: float, rows: list[IntervalRow]) -> dict[str, Any]:
    """The detail fields that differ by activity type."""
    if kind == "Ride":
        return {
            "icu_average_watts": round(mean),
            "icu_weighted_avg_watts": round(mean * 1.03),
            "icu_ftp": _RIDE_FTP,
            "icu_power_zones": _POWER_ZONES,
            "lthr": _RIDE_LTHR,
        }
    fields: dict[str, Any] = {
        "threshold_pace": _THRESHOLD_SPEED[kind],
        "pace_zones": _PACE_ZONES,
    }
    if kind == "Swim":
        return {**fields, "pool_length": _POOL_LENGTH_M, "icu_intervals": rows}
    return {**fields, "lthr": _RUN_LTHR}


def _clock_and_odometer(
    streams: dict[str, list[float]], speed_per_unit: float
) -> dict[str, list[float]]:
    """The time and distance streams, so the analysis reads the demo like a file.

    A run's distance follows its speed, a ride's its power.

    Args:
        streams: The effort's streams (``velocity_smooth`` or ``watts``).
        speed_per_unit: Metres per second for one unit of the main stream.
    """
    main = streams.get("velocity_smooth") or streams["watts"]
    speeds = [v * speed_per_unit for v in main] if "watts" in streams else main
    covered = itertools.accumulate(speeds[:-1], initial=0.0)
    return {
        "time": [i * _SAMPLE_SECONDS for i in range(len(main))],
        "distance": [round(m * _SAMPLE_SECONDS, 1) for m in covered],
    }


def _record(
    fake: FakeIntervals,
    rng: random.Random,
    day: date,
    kind: str,
    profile: list[float],
    event_id: int | None,
    planned_load: float | None = None,
) -> None:
    """Add one completed activity with its detail (and streams) to ``fake``.

    Args:
        fake: The demo account.
        rng: The demo's seeded randomness.
        day: The activity's local date.
        kind: ``"Run"``, ``"Ride"`` or ``"Swim"``.
        profile: The effort, one fraction of threshold per sample.
        event_id: The planned event the activity completes, if any.
        planned_load: The planned session's load, for the compliance.
    """
    seconds = len(profile) * _SAMPLE_SECONDS
    intensity = math.sqrt(sum(v * v for v in profile) / len(profile))
    load = round(seconds / 3600 * intensity**2 * 100, 1)
    streams, mean = _streams(rng, kind, profile)
    hr_zones = _hr_zones(_LTHR_BY_KIND[kind])
    heartrate = streams["heartrate"]
    rows = _swim_rows(profile, streams) if kind == "Swim" else []
    speed = mean if kind != "Ride" else mean / _RIDE_FTP * _RIDE_SPEED_AT_FTP
    distance = sum(row.distance or 0 for row in rows) or round(speed * seconds)
    climb = {"Run": 60, "Ride": 250}.get(kind)
    activity_id = f"demo-{len(fake.activities) + 1:04d}"
    name, start = _name_and_start(day, kind)
    detail = IntervalsActivityDetail(
        id=activity_id,
        name=name,
        start_date_local=start,
        type=kind,
        moving_time=seconds,
        elapsed_time=seconds + rng.randint(30, 240),
        icu_recording_time=seconds,
        icu_training_load=load,
        distance=distance,
        paired_event_id=event_id,
        compliance=round(load / planned_load * 100) if planned_load else None,
        average_heartrate=round(sum(heartrate) / len(heartrate)),
        max_heartrate=round(max(heartrate)),
        average_speed=round(distance / seconds, 3),
        pace=round(distance / seconds, 3) if kind != "Ride" else None,
        total_elevation_gain=round(seconds / 3600 * climb) if climb else None,
        icu_intensity=round(intensity * 100, 1),
        icu_hr_zone_times=_zone_times(heartrate, hr_zones),
        icu_hr_zones=hr_zones,
        # The watch's post-session answers: harder sessions feel and rate worse.
        feel=1 if intensity < 0.7 else 2 if intensity < 0.85 else 3,
        icu_rpe=max(1, min(10, round(intensity * 8))),
        **_kind_fields(kind, mean, rows),
    )
    fake.activities.append(detail)
    if kind != "Swim":  # a pool swim draws from its lengths, never streams
        streams.update(_clock_and_odometer(streams, speed / mean))
        fake.streams[activity_id] = [
            ActivityStream(type=key, data=list(data)) for key, data in streams.items()
        ]


# Seeding


def _commit_plan(app: BrowserApp, start: date) -> int:
    """Generate and commit Sam's plan as of ``start``; return its id."""
    request = GenerateRequest(
        races=[
            RaceGoalModel(
                goal_type="half_ironman",
                race_date=(start + timedelta(weeks=_PLAN_WEEKS, days=-1)).isoformat(),
            )
        ],
        training_start=start.isoformat(),
        ambition_level="moderate",
        # The demo plan is built directly, with no dialog to confirm in, so
        # it is built whatever its label.
        accept_experimental=True,
    )
    result = run_generate(
        request,
        repos=app.repos,
        progress=lambda _payload: None,
        # The demo shows a stored plan; it is not a new request.
        enforce_offered=False,
    )
    if not isinstance(result, GenerateSuccess):
        raise RuntimeError(f"The demo plan could not be generated: {result}")
    outcome = run_commit(
        CommitRequest(name=PLAN_NAME, generate_request=request, generate_result=result),
        repos=app.repos,
        maybe_get_coach=app.maybe_get_coach,
        client=app.client,
    )
    if outcome.get("status") != "ok":
        raise RuntimeError(f"The demo plan could not be saved: {outcome}")
    return int(outcome["plan_id"])


def _missed(past: list[PlanSessionRow], start: date) -> PlanSessionRow | None:
    """The one past session Sam skipped: the first weekday run of week three."""
    week_three = (start + timedelta(weeks=2)).isoformat()
    return next(
        (
            row
            for row in past
            if row.date >= week_three
            and row.sport == "running"
            and date.fromisoformat(row.date).weekday() < 5
        ),
        None,
    )


def _complete(
    fake: FakeIntervals, rng: random.Random, row: PlanSessionRow, plan_id: int
) -> None:
    """Record the activity that completed one past plan session."""
    kind = _KIND_BY_SPORT.get(row.sport)
    event = fake.events.get(session_external_id(plan_id, row.slot_id))
    if kind is None or event is None:
        return
    workout = json.loads(row.workout_json or "{}")
    stretch = rng.uniform(0.94, 1.08)
    profile = _steps_profile(
        workout.get("steps") or [], stretch, _THRESHOLD_SPEED.get(kind)
    )
    if len(profile) < 12:
        minutes = float(row.duration_minutes or 45) * stretch
        profile = _flat_profile(round(minutes * 60), 0.75)
    hours = len(profile) * _SAMPLE_SECONDS / 3600
    load = float(row.target_tss) * rng.uniform(0.90, 1.10)
    intensity = min(math.sqrt(load / hours / 100), 1.05)
    _record(
        fake,
        rng,
        date.fromisoformat(row.date),
        kind,
        _scaled(profile, intensity),
        event_id=int(event["id"]),
        planned_load=float(row.target_tss),
    )


def seed(app: BrowserApp, fake: FakeIntervals) -> None:
    """Give a freshly booted demo session Sam's plan and the weeks behind it.

    Args:
        app: The session, booted against ``fake``.
        fake: The account from :func:`account`.
    """
    today = clock.today()
    start = seed_monday(today)
    with _clock_at(start):
        plan_id = _commit_plan(app, start)
    rng = _rng(start)
    past = [
        row
        for row in app.repos.plan.get_sessions(plan_id)
        if row.date < today.isoformat()
    ]
    missed = _missed(past, start)
    for row in past:
        if row is not missed:
            _complete(fake, rng, row, plan_id)
    # The commute ride Sam never plans: one a week, unpaired.
    for week in range(_WEEKS_INTO_PLAN):
        wednesday = start + timedelta(weeks=week, days=2)
        if wednesday < today:
            minutes = rng.uniform(30, 45)
            profile = _flat_profile(round(minutes * 60), 0.6)
            _record(fake, rng, wednesday, "Ride", profile, event_id=None)
    app.rev = store.save(app.client, app.repos.session, app.rev)
    if app.runtime is not None:
        app.runtime.load_plan_context()


__all__ = ["DEMO_SETTINGS_KEY", "PLAN_NAME", "account", "seed", "seed_monday"]
