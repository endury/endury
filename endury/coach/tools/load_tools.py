"""Training-load analytics tools for the coach agent.

Per-sport fitness/fatigue/form stays ours: :mod:`endury.coach.form_context`
runs the Banister EMA over intervals.icu's per-activity
``icu_training_load``. Readiness signals come from their daily wellness
rows. Race-time prediction rides along here: same read client, same
"compose reads into something quotable" shape. Nothing here writes
anything.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from endury.analysis.charts import make_spec
from endury.coach.form_context import fetch_form, form_as_of, tsb_zone_read
from endury.coach.tools._errors import tool_error_response
from endury.coach.tools.analysis_tools import CHARTS_KEY
from endury.coach.tools.registry import coach_tool
from endury.coach.wellness_context import (
    MIN_TREND_DAYS,
    WELLNESS_BASELINE_DAYS,
    fetch_wellness,
    wellness_flags,
    wellness_trends,
)
from endury.common import clock
from endury.database.sqlite.repositories.plan import PlanRepository
from endury.domain.analysis import race_prediction
from endury.intervals.client import IntervalsClient
from endury.intervals.fitness import activity_date
from endury.intervals.models import (
    LOAD_ONLY_ACTIVITY_TYPES,
    SPORT_TYPE_MAP,
    IntervalsActivity,
    IntervalsActivityDetail,
    WellnessDay,
)
from endury.planner.foundations.types import SportType

#: Session families treated as "hard"/quality work when judging today's
#: session against current fatigue. These are the high-intensity values of
#: planner's ActivityFamily enum (easy/long/recovery are the non-hard
#: ones); "test" covers near-max fitness-test protocols. Brick sessions can
#: carry family="easy" (e.g. RUN_BRICK_LONG), so they are detected separately
#: by activity_key in _session_is_hard.
_HARD_SESSION_FAMILIES = {
    "tempo",
    "threshold",
    "interval",
    "test",
}

#: Work reps at/above which predict_race_time treats an activity basis as a
#: structured interval workout (warm-up + reps + recovery) rather than one
#: continuous effort.
_MIN_INTERVAL_WORK_REPS = 2

#: Prepended to the predict_race_time payload when the activity basis looks
#: like an interval workout — its average pace is diluted by recoveries.
_INTERVAL_BASIS_WARNING = (
    "This activity looks like a structured interval workout — warm-up, "
    "hard reps, and easy recoveries — not one continuous effort. The "
    "prediction is anchored on the average pace across the whole activity "
    "including the recoveries, so it will read SLOW. A race, time trial, "
    "or named best effort is the valid basis for this model."
)

#: Prepended to the predict_race_time payload when the activity basis is a
#: trail run — grade and terrain dilute the average pace, so Riegel reads
#: trail pace as weak flat-road fitness.
_TRAIL_BASIS_WARNING = (
    "This activity is a trail run — grade and terrain make its average "
    "pace much slower than the athlete's flat-road pace, so the "
    "prediction will read SLOW. Use a road race, track time trial, or "
    "flat best effort as the basis instead."
)


def _form_chart(
    activities: list[IntervalsActivity], today: date, weeks_back: int
) -> dict[str, Any]:
    """Fitness, fatigue and form for each day of the window, as one line chart."""
    days = [
        today - timedelta(days=n) for n in range(max(weeks_back, 1) * 7 - 1, -1, -1)
    ]
    forms = [form_as_of(activities, day) for day in days]
    return make_spec(
        "line",
        [day.isoformat() for day in days],
        [
            [round(form.ctl, 1) for form in forms],
            [round(form.atl, 1) for form in forms],
            [form.tsb for form in forms],
        ],
        "Fitness, fatigue and form",
        unit="load",
        labels=["Fitness", "Fatigue", "Form"],
    )


def _wellness_charts(rows: list[WellnessDay]) -> list[dict[str, Any]]:
    """HRV and resting heart rate day by day, each that has two days or more."""
    days = [str(row.id)[:10] for row in rows]
    charts = []
    for title, unit, values in (
        ("HRV", "ms", [row.hrv for row in rows]),
        ("Resting heart rate", "bpm", [row.resting_hr for row in rows]),
    ):
        if sum(value is not None for value in values) >= 2:
            charts.append(make_spec("line", days, [values], title, unit=unit))
    return charts


def _session_is_hard(session: dict[str, Any]) -> bool:
    """Return whether a planned session counts as hard/quality load.

    Args:
        session: Session dict with ``family`` and ``activity_key`` keys.

    Returns:
        True when the session's family is a hard one, or it is a brick
        (combined-discipline) session regardless of family.
    """
    if (session.get("family") or "").lower() in _HARD_SESSION_FAMILIES:
        return True
    return "brick" in (session.get("activity_key") or "").lower()


def make_load_tools(
    plan_repo: PlanRepository,
    intervals_client: IntervalsClient,
) -> list:
    """Create training-load analytics tools with injected dependencies.

    Args:
        plan_repo: Repository for the active plan and its sessions.
        intervals_client: Read client for intervals.icu activities and
            wellness.

    Returns:
        List of training-load analytics coach tools.
    """
    _plan_repo = plan_repo
    _client = intervals_client

    @coach_tool
    def get_training_load_status(weeks_back: int = 4, chart: bool = False) -> str:
        """Assess current fitness/fatigue/form and whether load is ramping gradually.

        Computes per-sport and combined CTL (fitness) / ATL (fatigue) / TSB
        (form) from the athlete's intervals.icu training load, reports the
        TSB coaching-zone read, an ACWR flag (ATL/CTL), and the
        week-over-week load ramp. Use this to answer "am I fit/fatigued/
        fresh, and ramping gradually?"

        Args:
            weeks_back: Number of trailing weeks to summarize (default 4).
            chart: Draw fitness, fatigue and form day by day under your
                reply; set it when the athlete asks how their fitness
                developed or to see it.

        Returns:
            JSON string with per-sport and combined CTL/ATL/TSB, weekly
            training load, the TSB zone read, ACWR, load ramp %, and an
            evidence caveat for those flags.
        """
        try:
            today = clock.today()
            form, activities = fetch_form(_client, today, weeks_back)
            acwr = round(form.atl / form.ctl, 2) if form.ctl > 0 else None
            weekly = _weekly_load(activities, today, weeks_back)
            ramp_pct = None
            if len(weekly) >= 2 and weekly[-2]["training_load"] > 0:
                ramp_pct = round(
                    (weekly[-1]["training_load"] - weekly[-2]["training_load"])
                    / weekly[-2]["training_load"]
                    * 100,
                    1,
                )

            payload = {
                "as_of": today.isoformat(),
                "weeks_back": weeks_back,
                "current": {
                    "ctl": round(form.ctl, 1),
                    "atl": round(form.atl, 1),
                    "tsb": form.tsb,
                    "tsb_zone": tsb_zone_read(form.tsb),
                },
                "by_sport": form.by_sport,
                "acwr": {
                    "value": acwr,
                    "note": (
                        "fatigue/fitness ratio; values above ~1.3 are a load spike, "
                        "a coaching heuristic with weak evidence"
                        if acwr is not None
                        else "No chronic load yet — insufficient training history"
                    ),
                },
                "load_ramp_pct": {
                    "value": ramp_pct,
                    "note": (
                        "Week-over-week training-load change; the most recent "
                        "week may be partial (through today)"
                        if ramp_pct is not None
                        else "Not enough weekly history to compute a ramp"
                    ),
                },
                "weekly_summary": weekly,
                CHARTS_KEY: [_form_chart(activities, today, weeks_back)]
                if chart
                else [],
                "caveat": (
                    "ACWR thresholds and the 10% weekly ramp rule are coaching "
                    "heuristics with weak evidence — treat them as advisory flags "
                    "alongside how the athlete actually feels, not as hard limits."
                ),
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            return tool_error_response("retrieve training load status", exc)

    @coach_tool
    def recommend_todays_session(date: str | None = None) -> str:
        """Recommend whether to do today's planned session as prescribed.

        Advisory only — this never edits the plan; if the recommendation
        should change future sessions, use replanning instead. Combines
        today's planned session(s), wellness flags (sleep, HRV, resting HR),
        and current TSB into a simple, transparent verdict: keep /
        reduce_intensity / swap_to_easy / rest. Degrades to load-only
        reasoning when wellness data is missing, and reports "rest" when
        there is no active plan or no session that day.

        Args:
            date: ISO date (YYYY-MM-DD) to evaluate. Defaults to today.

        Returns:
            JSON string with the verdict, the signals behind it, and the
            plan/session context.
        """
        try:
            as_of = (
                datetime.strptime(date, "%Y-%m-%d").date() if date else clock.today()
            )
            as_of_str = as_of.isoformat()

            active = _plan_repo.get_active()
            sessions: list[dict[str, Any]] = []
            plan_status = "no_active_plan"
            if active is not None:
                rows = _plan_repo.get_sessions_by_date_range(
                    active.id, as_of_str, as_of_str
                )
                sessions = [
                    {
                        "sport": row.sport,
                        "family": row.family,
                        "activity_key": row.activity_key,
                        "target_tss": round(row.target_tss, 1),
                        "duration_minutes": row.duration_minutes,
                    }
                    for row in rows
                ]
                plan_status = "session_scheduled" if sessions else "rest_day"

            wellness = fetch_wellness(_client, as_of, days=WELLNESS_BASELINE_DAYS)
            recovery_flags = wellness_flags(wellness)
            wellness_available = bool(wellness)

            tsb = fetch_form(_client, as_of, weeks_back=1)[0].tsb
            overreaching = tsb < -30
            productive_overload = -30 <= tsb <= -10
            session_is_hard = any(_session_is_hard(s) for s in sessions)

            reasoning: list[str] = []
            if not sessions:
                verdict = "rest"
                reasoning.append(
                    "no active plan"
                    if active is None
                    else "rest day — no session scheduled"
                )
            else:
                has_concern = (
                    overreaching or productive_overload or bool(recovery_flags)
                )
                if overreaching:
                    reasoning.append(f"form {tsb} — deep fatigue (below -30)")
                if productive_overload:
                    reasoning.append(f"form {tsb} — productive overload zone")
                if recovery_flags:
                    reasoning.append(
                        f"{len(recovery_flags)} recovery flag(s): "
                        + "; ".join(recovery_flags)
                    )
                if session_is_hard:
                    reasoning.append("the session is a hard/quality session")

                if len(recovery_flags) >= 2 or overreaching:
                    verdict = "rest" if session_is_hard else "swap_to_easy"
                elif (recovery_flags or productive_overload) and session_is_hard:
                    verdict = "reduce_intensity"
                else:
                    verdict = "keep"
                    if not has_concern:
                        reasoning.append("no concerning fatigue or recovery signals")

            if not wellness_available:
                reasoning.append(
                    "no recent wellness data — recommendation based on training "
                    "load (form) only"
                )

            payload = {
                "date": as_of_str,
                "plan_status": plan_status,
                "sessions": sessions,
                "verdict": verdict,
                "signals": {
                    "tsb": tsb,
                    "recovery_flags": recovery_flags,
                    "wellness_data_available": wellness_available,
                },
                "reasoning": reasoning,
                "note": (
                    "Advisory only — this does not change your persisted plan. "
                    "To act on it, skip the affected sessions with "
                    "skip_planned_session (replanning only regenerates from next "
                    "Monday and never touches the current week)."
                ),
            }
            return json.dumps(payload, indent=2)
        except Exception as exc:
            return tool_error_response("recommend today's session", exc)

    @coach_tool
    def get_wellness_trend(days: int = 28, chart: bool = False) -> str:
        """Trend the athlete's wellness metrics over recent weeks.

        Judges the last 7 days of sleep hours, sleep score, HRV, resting HR,
        and weight (from intervals.icu wellness) against the athlete's own
        rolling baseline and labels each rising / declining / stable.
        Use it for "is my HRV actually trending down?" or "am I recovering
        over the block?" — the always-on snapshot only shows the last week.

        Args:
            days: Window length in days, inclusive of today (default 28,
                clamped up to the 14 the baseline needs).
            chart: Draw HRV and resting heart rate day by day under your
                reply; set it when the athlete asks how they developed or
                to see them.

        Returns:
            JSON string with the window and one entry per metric that has
            data (days with a value, latest, baseline average and SD, recent
            average, trend).
        """
        try:
            window = max(days, MIN_TREND_DAYS)
            as_of = clock.today()
            rows = fetch_wellness(_client, as_of, days=window)
            return json.dumps(
                {
                    "start_date": (as_of - timedelta(days=window - 1)).isoformat(),
                    "end_date": as_of.isoformat(),
                    "metrics": wellness_trends(rows),
                    CHARTS_KEY: _wellness_charts(rows) if chart else [],
                    "note": (
                        "Trend compares the last 7 days against the athlete's own "
                        "prior baseline, and only calls a move when it clears 0.75 "
                        "baseline SD — smaller swings are that metric's normal "
                        "noise. Under 14 days of data it reports insufficient data. "
                        "Falling HRV with rising resting HR is the fatigue "
                        "signature; sleep and weight are context."
                    ),
                },
                indent=2,
            )
        except Exception as exc:
            return tool_error_response("retrieve wellness trend", exc)

    @coach_tool
    def predict_race_time(
        target_distance_m: float | None = None,
        known_distance_m: float | None = None,
        known_time_seconds: float | None = None,
        activity_id: str | None = None,
    ) -> str:
        """Predict running race times from one recent best effort (Riegel).

        Answers "what marathon am I in shape for?" from a race or all-out
        time trial, using Riegel's power law T2 = T1 x (D2/D1)^1.06.
        Always returns the 5k / 10k / half / marathon equivalents, plus
        ``target_distance_m`` when you name one. Supply the effort either
        as ``known_distance_m`` + ``known_time_seconds`` (from the
        conversation) or as an ``activity_id`` the athlete names, which is
        read from intervals.icu. Prefer a race, time trial, or named best
        effort as the basis over an arbitrary training activity — a workout
        that mixes warm-up, hard reps, and easy recoveries pulls the average
        pace slow, and Riegel will read that as weak fitness. If
        ``activity_id`` looks like an interval workout rather than a
        continuous effort, or is a trail run (grade dilutes its pace), the
        payload carries a ``basis_warning``; read it and pass it on rather
        than quoting the prediction at face value. Predictions are
        flat-road times — never quote them as trail-race finish times.

        Running only — the exponent is not valid for cycling or swimming,
        and there is no triathlon equivalent. Each prediction carries its
        extrapolation ratio and a confidence read; beyond ~4x the known
        distance (e.g. 5k to marathon) the model is optimistic and assumes
        endurance training the athlete may not have done.

        Args:
            target_distance_m: Extra distance to predict, in metres
                (optional — the standard distances are always returned).
            known_distance_m: Distance of the known effort, in metres.
            known_time_seconds: Time of the known effort, in seconds.
            activity_id: intervals.icu activity id to read the distance
                and moving time from instead (e.g. "i176644752").

        Returns:
            JSON string with the basis effort, the model used, one row per
            predicted distance (time, pace, extrapolation ratio,
            confidence), the model's caveats, and — when the activity basis
            looks like an interval workout — a ``basis_warning``.
        """
        try:
            source = "values"
            interval_workout = False
            trail_basis = False
            if activity_id:
                activity = _client.get_activity(activity_id)
                activity_type = activity.type or ""
                # Hike/Walk count as running LOAD but are not a
                # pace-representative Riegel basis.
                if (
                    SPORT_TYPE_MAP.get(activity_type) is not SportType.RUNNING
                    or activity_type in LOAD_ONLY_ACTIVITY_TYPES
                ):
                    return json.dumps(
                        {
                            "message": (
                                f"Activity {activity_id} is not a run "
                                f"({activity.type or 'unknown'}) — this prediction "
                                "is running-only."
                            )
                        }
                    )
                known_distance_m = float(activity.distance or 0.0)
                known_time_seconds = float(activity.moving_time or 0.0)
                source = "activity"
                interval_workout = _looks_like_interval_workout(activity)
                trail_basis = activity.type == "TrailRun"

            if not known_distance_m or not known_time_seconds:
                return json.dumps(
                    {
                        "message": (
                            f"Activity {activity_id} has no distance or moving time "
                            "to predict from."
                            if source == "activity"
                            else "Give either an activity_id, or known_distance_m "
                            "plus known_time_seconds from a race or all-out time "
                            "trial."
                        )
                    }
                )

            predictions = race_prediction.predict_standard_distances(
                known_distance_m, known_time_seconds
            )
            payload: dict[str, Any] = {
                "basis": {
                    "source": source,
                    "activity_id": activity_id,
                    "distance_m": round(known_distance_m, 1),
                    "time": _hms(known_time_seconds),
                    "pace_per_km": _pace_per_km(known_time_seconds, known_distance_m),
                },
                "model": {
                    "name": "standard race-time formula",
                    "exponent": race_prediction.RIEGEL_EXPONENT,
                    "formula": "T2 = T1 x (D2/D1)^1.06",
                },
                "predictions": [
                    _prediction_row(
                        name,
                        race_prediction.STANDARD_RUNNING_DISTANCES_M[name],
                        seconds,
                        known_distance_m,
                    )
                    for name, seconds in predictions.items()
                ],
                "caveats": [
                    "Running only — the 1.06 exponent is not valid for cycling or "
                    "swimming, and there is no triathlon equivalent.",
                    "Assumes the athlete is equally trained for both distances; a "
                    "5k specialist without long runs will not hit the marathon "
                    "prediction.",
                    "Assumes comparable course, surface, weather, and even pacing, "
                    "and that the known effort was a genuine race or time trial.",
                    "Flat-road times only — a trail or mountain race cannot be "
                    "predicted from this model; reason from the course's "
                    "distance, climb, and terrain instead.",
                ],
            }
            basis_warnings = []
            if interval_workout:
                basis_warnings.append(_INTERVAL_BASIS_WARNING)
            if trail_basis:
                basis_warnings.append(_TRAIL_BASIS_WARNING)
            if basis_warnings:
                payload = {"basis_warning": " ".join(basis_warnings), **payload}
            if target_distance_m:
                payload["target"] = _prediction_row(
                    "target",
                    target_distance_m,
                    race_prediction.predict_race_time(
                        known_distance_m, known_time_seconds, target_distance_m
                    ),
                    known_distance_m,
                )
            return json.dumps(payload, indent=2)
        except ValueError as exc:
            return json.dumps({"message": str(exc)})
        except Exception as exc:
            return tool_error_response("predict race time", exc)

    return [
        get_training_load_status,
        recommend_todays_session,
        get_wellness_trend,
        predict_race_time,
    ]


def _weekly_load(
    activities: list[IntervalsActivity],
    as_of: date,
    weeks_back: int,
) -> list[dict[str, Any]]:
    """Sum training load into the last ``weeks_back`` Monday-based weeks."""
    weeks = max(weeks_back, 1)
    this_monday = as_of - timedelta(days=as_of.weekday())
    first_monday = this_monday - timedelta(weeks=weeks - 1)
    loads = [0.0] * weeks
    for activity in activities:
        day = activity_date(activity)
        if day is None or day < first_monday or day > as_of:
            continue
        loads[(day - first_monday).days // 7] += float(
            activity.icu_training_load or 0.0
        )
    return [
        {
            "week_start": (first_monday + timedelta(weeks=index)).isoformat(),
            "training_load": round(load, 1),
        }
        for index, load in enumerate(loads)
    ]


def _hms(seconds: float) -> str:
    """Format seconds as ``M:SS`` (or ``H:MM:SS`` past an hour)."""
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _pace_per_km(seconds: float, distance_m: float) -> str:
    """Format an effort as its average ``M:SS`` per-kilometre pace."""
    return f"{_hms(seconds / (distance_m / 1000.0))}/km"


def _confidence(ratio: float) -> str:
    """Describe how much to trust a prediction at this extrapolation ratio."""
    if ratio <= 2.0:
        return "high — close to the known distance"
    if ratio <= race_prediction.RELIABLE_EXTRAPOLATION_RATIO:
        return "moderate — within the model's usual range"
    return (
        f"low — beyond {race_prediction.RELIABLE_EXTRAPOLATION_RATIO:g}x "
        "extrapolation; read it as a best case, not a target"
    )


def _prediction_row(
    label: str,
    distance_m: float,
    seconds: float,
    known_distance_m: float,
) -> dict[str, Any]:
    """Build one predicted-distance row for the race-prediction payload.

    Args:
        label: Distance name (``"5k"``, ``"marathon"``, ``"target"``).
        distance_m: Predicted distance, in metres.
        seconds: Predicted time, in seconds.
        known_distance_m: Distance of the basis effort, in metres.

    Returns:
        Dict with the distance, predicted time and pace, the
        extrapolation ratio, and a confidence read.
    """
    ratio = race_prediction.extrapolation_ratio(known_distance_m, distance_m)
    return {
        "distance": label,
        "distance_m": round(distance_m, 1),
        "predicted_time": _hms(seconds),
        "predicted_seconds": round(seconds, 1),
        "pace_per_km": _pace_per_km(seconds, distance_m),
        "extrapolation_ratio": round(ratio, 2),
        "confidence": _confidence(ratio),
    }


def _looks_like_interval_workout(activity: IntervalsActivityDetail) -> bool:
    """Detect a warm-up/reps/recovery structure rather than a continuous effort.

    Args:
        activity: The full activity detail, fetched with ``intervals=true``
            so ``icu_intervals`` is populated.

    Returns:
        True when intervals.icu recorded 2 or more work reps — the
        activity's average pace is diluted by recoveries (and any
        warm-up/cooldown), not a clean continuous or all-out effort.
    """
    work_reps = sum(1 for row in activity.icu_intervals if row.type == "WORK")
    return work_reps >= _MIN_INTERVAL_WORK_REPS
