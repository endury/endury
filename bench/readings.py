"""Everything the rules read off a plan, before any band is applied.

Each reading is one number (or ``None`` when the plan has no such feature).
Labelled readings trust the plan's own phase, mode and type labels; the
step readings trust only the steps. ``facts`` carries the readings no rule
grades, so a report can still show them.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import defaultdict
from itertools import pairwise

from bench.load import monotony_and_strain, seed_fitness, session_tss, trajectory
from bench.schema import GOALS, SPORTS, Plan, Session, Week

#: A session is hard when its steps carry at least this many minutes in
#: zones 4 and 5; short strides and openers stay below it.
HARD_MIN = 8.0
#: A VO2max session carries at least this many minutes in zone 5.
VO2_MIN = 8.0
#: Zone-5 minutes below this are strides or openers, not interval work.
STRIDES_MAX = 4.0
#: Sessions at least this long should say how to fuel.
FUEL_MIN = 75.0
FUEL_CUE = re.compile(
    r"fuel|carb|gel|g/h|g per h|grams|drink|hydrat|nutrition|eat", re.IGNORECASE
)
TRIATHLON = frozenset(
    {"half_ironman", "ironman", "sprint_triathlon", "olympic_triathlon"}
)
_PHASE_OFFSET = {"peak": 0, "taper": 0, "build": 1, "base": 2}

Readings = dict[str, float | None]


def _has_steps(plan: Plan) -> bool:
    return any(s.steps for _, s in plan.sessions)


def _is_hard(s: Session) -> bool:
    return s.zone_minutes(4) >= HARD_MIN


def _long_sessions(plan: Plan, sport: str) -> list[tuple[Week, Session]]:
    """The sport's long session per week.

    The longest session labelled long or race pace; for a plan that never
    labels one, the week's longest session of the sport.
    """
    labelled: list[tuple[Week, Session]] = []
    fallback: list[tuple[Week, Session]] = []
    for w in plan.weeks:
        mine = [s for s in w.sessions if s.sport == sport]
        if not mine:
            continue
        fallback.append((w, max(mine, key=lambda s: s.minutes)))
        longs = [s for s in mine if s.kind in ("long", "race_pace")]
        if longs:
            labelled.append((w, max(longs, key=lambda s: s.minutes)))
    return labelled or fallback


def _quality_minutes(week: Week) -> float:
    """Zone-3-and-up minutes inside the week's quality sessions.

    A quality session spends at least a third of its time at zone 3 or
    above, which admits a short taper session behind a long warm-up and
    leaves out a long ride with a brief race-pace block, so the taper is
    read on the sessions whose intensity it is meant to keep.
    """
    return sum(
        s.zone_minutes(3)
        for s in week.sessions
        if s.minutes > 0 and s.zone_minutes(3) >= s.minutes / 3.0
    )


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


def _mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _periodisation(plan: Plan, r: Readings, facts: Readings) -> None:
    weeks = plan.weeks
    hours = {w.week: w.hours for w in weeks}
    load = [w for w in weeks if w.mode == "load"]
    recovery = [w for w in weeks if w.mode == "recovery"]
    taper = [w for w in weeks if w.mode == "taper"]
    race = weeks[-1]
    peak = max(load, key=lambda w: w.hours) if load else None
    by_no = {w.week: w for w in weeks}

    r["recovery_cadence"] = _ratio(len(recovery), len(load) + len(recovery))
    depths: list[float] = []
    honest = 0
    for w in recovery:
        prev = w.week - 1
        while prev in by_no and by_no[prev].mode != "load":
            prev -= 1
        if prev in by_no and hours[prev] > 0.1:
            depths.append(hours[w.week] / hours[prev])
        if w.week - 1 in by_no and hours[w.week] < hours[w.week - 1]:
            honest += 1
    r["recovery_week_depth"] = _mean(depths)
    r["recovery_label_honesty"] = _ratio(honest, len(recovery)) if recovery else None
    prep = [w for w in weeks if w.phase in ("base", "build", "peak")]
    r["phase_share_base"] = _ratio(sum(w.phase == "base" for w in prep), len(prep))
    r["phase_share_build"] = _ratio(sum(w.phase == "build" for w in prep), len(prep))

    tail = 0
    for w in reversed(weeks):
        if w.mode not in ("taper", "race"):
            break
        tail += 1
    r["taper_duration_days"] = 7.0 * tail
    if peak is not None and peak.hours > 0.1:
        # A one-week taper is read with its race week: Bosquet's 41-60 %
        # cut is the whole taper's mean, and the band was measured on two-
        # and three-week tapers.
        drop_weeks = [*taper, race] if len(taper) == 1 else taper
        r["taper_volume_drop"] = (
            statistics.mean(w.hours for w in drop_weeks) / peak.hours if taper else None
        )
        r["race_week_volume"] = race.hours / peak.hours
        r["taper_session_frequency"] = (
            statistics.mean(len(w.sessions) for w in taper) / len(peak.sessions)
            if taper and peak.sessions
            else None
        )
        peak_q = _quality_minutes(peak)
        taper_q = [_quality_minutes(w) for w in taper]
        r["taper_intensity_kept"] = (
            statistics.mean(taper_q) / peak_q if taper and peak_q > 0 else None
        )
        facts["taper_z3plus_over_peak"] = (
            statistics.mean(sum(s.zone_minutes(3) for s in w.sessions) for w in taper)
            / sum(s.zone_minutes(3) for s in peak.sessions)
            if taper and sum(s.zone_minutes(3) for s in peak.sessions) > 0
            else None
        )
        peak_h = sum(s.zone_minutes(4) for s in peak.sessions)
        facts["taper_z4plus_over_peak"] = (
            statistics.mean(sum(s.zone_minutes(4) for s in w.sessions) for w in taper)
            / peak_h
            if taper and peak_h > 0
            else None
        )
    else:
        r["taper_volume_drop"] = r["race_week_volume"] = None
        r["taper_session_frequency"] = r["taper_intensity_kept"] = None
    r["taper_shape"] = (
        taper[-1].hours / taper[0].hours
        if len(taper) >= 2 and taper[0].hours > 0.1
        else None
    )
    ramps = [
        hours[b.week] / hours[a.week]
        for a, b in pairwise(weeks)
        if a.mode == "load" and b.mode == "load" and hours[a.week] > 0.1
    ]
    r["weekly_volume_ramp"] = max(ramps) if ramps else None
    r["peak_weekly_hours"] = max((w.hours for w in weeks[:-1]), default=0.0)
    facts["total_hours"] = sum(hours.values())
    facts["recovery_weeks"] = float(len(recovery))
    facts["taper_weeks"] = float(len(taper))
    if peak is not None:
        plateau = 0
        for w in reversed([w for w in weeks if w.phase in ("base", "build", "peak")]):
            if w.mode == "load" and w.hours >= 0.95 * peak.hours:
                plateau += 1
            elif w.mode == "load":
                break
        facts["peak_plateau_weeks"] = float(plateau)


def _load(
    plan: Plan, r: Readings, facts: Readings, recent_hours: dict[str, float]
) -> None:
    traj = trajectory(plan, seed_fitness(plan, recent_hours))
    r["race_day_tsb"] = traj.race_tsb
    facts["race_day_ctl"] = traj.race_ctl
    facts["race_day_form_ratio"] = _ratio(traj.race_tsb, traj.race_ctl)
    r["min_tsb_7d"] = traj.min_tsb_7d
    facts["min_tsb"] = traj.min_tsb
    ms = monotony_and_strain(plan)
    load_weeks = [w.week for w in plan.weeks if w.mode == "load"]
    finite = [ms[w][0] for w in load_weeks if math.isfinite(ms[w][0])]
    # Foster's signal is monotony persistently above 2, so the plan is read
    # on average over its load weeks, not on its one flattest week.
    r["monotony"] = statistics.mean(finite) if finite else None
    facts["monotony_max"] = max(finite) if finite else None
    facts["weeks_monotony_over_2"] = float(sum(1 for w in load_weeks if ms[w][0] > 2.0))
    strains = [ms[w][1] for w in load_weeks if math.isfinite(ms[w][1])]
    facts["strain_max"] = max(strains) if strains else None
    tss = [sum(session_tss(s) for s in w.sessions) for w in plan.weeks]
    facts["total_tss"] = sum(tss)
    weekly = [
        b / a
        for (wa, a), (wb, b) in zip(
            zip(plan.weeks, tss, strict=True),
            zip(plan.weeks[1:], tss[1:], strict=True),
            strict=False,
        )
        if wa.mode == "load" and wb.mode == "load" and a > 0
    ]
    facts["weekly_load_ramp_max"] = max(weekly) if weekly else None
    acwr: list[float] = []
    for i in range(4, len(tss)):
        chronic = statistics.mean(tss[i - 4 : i])
        if chronic > 0:
            acwr.append(tss[i] / chronic)
    facts["acwr_max"] = max(acwr) if acwr else None
    first = plan.weeks[0].hours
    r["first_week_fit"] = _ratio(first, sum(recent_hours.values()))


def _intensity(plan: Plan, r: Readings, facts: Readings) -> None:
    if not _has_steps(plan):
        for key in (
            "tid_low_share",
            "tid_high_share",
            "hard_sessions_per_week_build",
            "hard_day_adjacent_same_sport",
            "vo2max_in_base",
            "label_mismatches",
        ):
            r[key] = None
        return
    training = [(w, s) for w, s in plan.sessions if w.mode in ("load", "recovery")]
    total = sum(st.minutes for _, s in training for st in s.steps)
    r["tid_low_share"] = _ratio(sum(s.zone_minutes(1, 2) for _, s in training), total)
    r["tid_high_share"] = _ratio(sum(s.zone_minutes(4) for _, s in training), total)
    facts["tid_mid_share"] = _ratio(
        sum(s.zone_minutes(3, 3) for _, s in training), total
    )
    load = [w for w in plan.weeks if w.mode == "load"]
    build = [w for w in load if w.phase == "build"]
    r["hard_sessions_per_week_build"] = _mean(
        [float(sum(_is_hard(s) for s in w.sessions)) for w in build]
    )
    facts["hard_sessions_per_week"] = _mean(
        [float(sum(_is_hard(s) for s in w.sessions)) for w in load]
    )
    hard_days: dict[tuple[int, int], set[str]] = defaultdict(set)
    for w in plan.weeks:
        for s in w.sessions:
            if s.day is not None and _is_hard(s):
                hard_days[(w.week, s.day)].add(s.sport)
    adjacent = 0
    for (week, day), sports in hard_days.items():
        nxt = (week, day + 1) if day < 6 else (week + 1, 0)
        adjacent += len(sports & hard_days.get(nxt, set()))
    r["hard_day_adjacent_same_sport"] = float(adjacent)
    facts["cross_sport_hard_stacking_share"] = _ratio(
        float(len({wk for (wk, _), sp in hard_days.items() if len(sp) > 1})),
        len(plan.weeks),
    )
    base_weeks = sum(1 for w in plan.weeks if w.phase == "base")
    r["vo2max_in_base"] = _ratio(
        sum(
            1
            for w, s in plan.sessions
            if w.phase == "base" and s.zone_minutes(5) >= VO2_MIN
        ),
        base_weeks,
    )
    facts["vo2max_in_peak_or_taper"] = float(
        sum(
            1
            for w, s in plan.sessions
            if w.phase in ("peak", "taper") and s.zone_minutes(5) >= VO2_MIN
        )
    )
    r["label_mismatches"] = float(sum(_label_mismatch(s) for _, s in plan.sessions))


def _label_mismatch(s: Session) -> bool:
    """Whether a session's type label contradicts its steps."""
    if not s.steps:
        return False
    if s.kind in ("easy", "recovery"):
        return s.zone_minutes(4) >= HARD_MIN
    if s.kind == "long":
        return s.zone_minutes(5) >= VO2_MIN
    if s.kind == "intervals":
        return s.zone_minutes(5) < STRIDES_MAX
    if s.kind == "tempo":
        return s.zone_minutes(3) < HARD_MIN
    if s.kind == "threshold":
        return s.zone_minutes(4) < HARD_MIN
    return False


def _specificity(plan: Plan, r: Readings, facts: Readings) -> None:
    weeks = plan.weeks
    tri = plan.goal in TRIATHLON
    longs = {sp: _long_sessions(plan, sp) for sp in SPORTS}

    def peak_hours(sport: str) -> float:
        return max((s.hours for _, s in longs[sport]), default=0.0)

    r["long_run_peak_hours"] = peak_hours("run")
    r["long_ride_peak_hours"] = peak_hours("bike") if tri else None
    swims = [s.metres for _, s in plan.sessions if s.sport == "swim" and s.metres]
    r["long_swim_peak_metres"] = max(swims) if swims and tri else None
    primary = "bike" if tri else "run"
    ph = peak_hours(primary)
    at_peak = [w.week for w, s in longs[primary] if s.hours >= ph - 1e-6]
    r["peak_long_timing_weeks"] = (
        float(weeks[-1].week - max(at_peak)) if at_peak else None
    )
    offsets = [
        min(
            _PHASE_OFFSET[w.phase]
            for w, s in longs[sp]
            if s.hours >= peak_hours(sp) - 1e-6
        )
        for sp in SPORTS
        if longs[sp]
    ]
    facts["longest_long_phase_offset"] = float(max(offsets)) if offsets else None
    steps: list[float] = []
    for sp in SPORTS:
        seq = [(w, s) for w, s in longs[sp] if w.mode == "load"]
        for (wa, sa), (wb, sb) in pairwise(seq):
            if wb.week == wa.week + 1 and sa.minutes > 0:
                steps.append(sb.minutes / sa.minutes)
    facts["long_session_step_max"] = max(steps) if steps else None

    if tri:
        bricks = 0
        eligible = [
            w for w in weeks if w.phase in ("build", "peak") and w.mode == "load"
        ]
        brick_minutes: list[float] = []
        for w in eligible:
            by_day: dict[int, list[Session]] = defaultdict(list)
            for s in w.sessions:
                if s.day is not None:
                    by_day[s.day].append(s)
            found = False
            for day_sessions in by_day.values():
                sports = [s.sport for s in day_sessions]
                if "bike" in sports and "run" in sports:
                    found = True
                    brick_minutes.extend(
                        s.minutes for s in day_sessions if s.sport == "run"
                    )
            bricks += int(found)
        r["brick_weeks_share"] = _ratio(bricks, len(eligible)) if eligible else None
        facts["brick_run_minutes_max"] = max(brick_minutes) if brick_minutes else None
        r["marathon_race_pace_sessions"] = None
    else:
        r["brick_weeks_share"] = None
        r["marathon_race_pace_sessions"] = (
            float(
                sum(
                    1
                    for w, s in plan.sessions
                    if w.phase in ("build", "peak")
                    and s.sport == "run"
                    and (s.kind == "race_pace" or s.zone_minutes(3, 3) >= 20)
                )
            )
            if _has_steps(plan)
            else float(
                sum(
                    1
                    for w, s in plan.sessions
                    if w.phase in ("build", "peak") and s.kind == "race_pace"
                )
            )
        )
    by_sport = {
        sp: sum(s.hours for _, s in plan.sessions if s.sport == sp) for sp in SPORTS
    }
    total = sum(by_sport.values())
    for sp in SPORTS:
        r[f"sport_mix_{sp}"] = _ratio(by_sport[sp], total) if tri else None
    empty = [
        7 - len({s.day for s in w.sessions if s.day is not None})
        for w in weeks
        if w.mode in ("load", "recovery")
    ]
    r["rest_days_per_week"] = _mean([float(e) for e in empty])
    run_shares: list[float] = []
    for w in weeks:
        if w.mode != "load":
            continue
        runs = [s for s in w.sessions if s.sport == "run"]
        long_runs = [s for s in runs if s.kind in ("long", "race_pace")]
        run_min = sum(s.minutes for s in runs)
        if long_runs and run_min >= 60:
            run_shares.append(max(s.minutes for s in long_runs) / run_min)
    r["long_run_share"] = _mean(run_shares) if not tri else None
    facts["long_run_share_all_goals"] = _mean(run_shares)
    over = 0
    for w in weeks:
        per_day: dict[int, list[str]] = defaultdict(list)
        for s in w.sessions:
            if s.day is not None:
                per_day[s.day].append(s.sport)
        over += sum(
            1
            for sports in per_day.values()
            if len(sports) > 2 or len(set(sports)) < len(sports)
        )
    facts["over_capacity_days"] = float(over)
    tests = [(w, s) for w, s in plan.sessions if s.kind == "test"]
    facts["test_sessions"] = float(len(tests))
    facts["tests_in_recovery_share"] = _ratio(
        float(sum(1 for w, _ in tests if w.mode == "recovery")), len(tests)
    )


def _sessions(plan: Plan, r: Readings, facts: Readings) -> None:
    if not _has_steps(plan):
        for key in (
            "warmup_share",
            "vo2max_work_rest_ratio",
            "z5_minutes_per_session",
            "threshold_minutes_per_session",
            "easy_session_low_share",
        ):
            r[key] = None
        r["fuelling_cue_share"] = None
        return
    hard = [(w, s) for w, s in plan.sessions if _is_hard(s)]
    warm: list[float] = []
    cool: list[float] = []
    for w, s in hard:
        before = 0.0
        for st in s.steps:
            if st.zone >= 4:
                break
            before += st.minutes
        earlier = w.sessions[: w.sessions.index(s)]
        if s.sport == "run" and any(
            o.sport == "bike" and o.day == s.day for o in earlier
        ):
            # A brick: the ride before the run is its warm-up.
            before = max(before, 10.0)
        after = 0.0
        for st in reversed(s.steps):
            if st.zone >= 3:
                break
            after += st.minutes
        warm.append(float(before >= 10.0))
        cool.append(float(after >= 5.0))
    r["warmup_share"] = _mean(warm)
    facts["cooldown_share"] = _mean(cool)
    # Only VO2max sessions: strides (a few minutes of zone 5 at a long easy
    # recovery) sharpen, they are not a VO2max set.
    ratios = [
        rep.off_minutes / rep.on_minutes
        for _, s in plan.sessions
        if s.zone_minutes(5) >= VO2_MIN
        for rep in s.repeats
        if rep.on_zone == 5 and rep.on_minutes > 0
    ]
    r["vo2max_work_rest_ratio"] = statistics.median(ratios) if ratios else None
    z5 = [s.zone_minutes(5) for _, s in plan.sessions if s.zone_minutes(5) >= VO2_MIN]
    r["z5_minutes_per_session"] = _mean(z5)
    thr = [
        s.zone_minutes(3, 4)
        for _, s in plan.sessions
        if s.kind in ("tempo", "threshold") and s.steps
    ]
    r["threshold_minutes_per_session"] = _mean(thr)
    easy_shares = [
        s.zone_minutes(1, 2) / s.minutes
        for _, s in plan.sessions
        if s.kind in ("easy", "long")
        and s.steps
        and s.minutes > 0
        and s.zone_minutes(4) < 3.0  # strides and openers are not intensity
    ]
    r["easy_session_low_share"] = _mean(easy_shares)
    rec = [
        s.zone_minutes(1, 1) / s.minutes
        for _, s in plan.sessions
        if s.kind == "recovery" and s.steps and s.minutes > 0
    ]
    facts["recovery_session_z1_share"] = _mean(rec)
    longish = [s for _, s in plan.sessions if s.minutes >= FUEL_MIN]
    r["fuelling_cue_share"] = (
        _ratio(float(sum(1 for s in longish if FUEL_CUE.search(s.notes))), len(longish))
        if longish
        else None
    )
    facts["sessions_with_steps_share"] = _ratio(
        float(sum(1 for _, s in plan.sessions if s.steps)), len(plan.sessions)
    )


def read(
    plan: Plan,
    recent_hours: dict[str, float],
    longest_recent_min: dict[str, int],
) -> tuple[Readings, Readings]:
    """Read every quantity off a plan.

    Args:
        plan: The plan in the generic table.
        recent_hours: The athlete's recent weekly hours per sport.
        longest_recent_min: The athlete's longest recent session per sport.

    Returns:
        ``(readings, facts)``: the readings the rulebook may grade, and the
        readings no rule grades.
    """
    if plan.goal not in GOALS:
        raise ValueError(f"unknown goal {plan.goal!r}")
    r: Readings = {}
    facts: Readings = {}
    _periodisation(plan, r, facts)
    _load(plan, r, facts, recent_hours)
    _intensity(plan, r, facts)
    _specificity(plan, r, facts)
    _sessions(plan, r, facts)
    fits: list[float] = []
    for sp, longest in longest_recent_min.items():
        if sp == "swim":
            # The evidence is running cohorts, and a swim's first session is
            # set by the pool, not by the athlete's longest: a 1500 m floor
            # is 30 minutes however short the recent swims were.
            continue
        first = next(
            (
                s.minutes
                for w in plan.weeks
                for s in w.sessions
                if s.sport == sp and s.kind == "long"
            ),
            None,
        )
        if first is not None and longest > 0:
            fits.append(first / longest)
    r["first_long_session_fit"] = max(fits) if fits else None
    return r, facts
