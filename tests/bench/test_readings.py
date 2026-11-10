from dataclasses import replace

import pytest

from bench.readings import read
from bench.schema import Plan, Session, Step, Week
from tests.bench.conftest import hard_run, marathon_plan, session, vo2_run

ATHLETE = {"run": 3.0}
LONGEST = {"run": 60}


def test_periodisation_and_taper_readings(marathon):
    r, facts = read(marathon, ATHLETE, LONGEST)
    assert r["recovery_cadence"] == pytest.approx(2 / 9)
    assert 0.5 < r["recovery_week_depth"] < 0.8
    assert r["recovery_label_honesty"] == 1.0
    assert r["taper_duration_days"] == 21
    peak = max((w for w in marathon.weeks if w.mode == "load"), key=lambda w: w.hours)
    tapers = [w for w in marathon.weeks if w.mode == "taper"]
    assert r["taper_session_frequency"] == pytest.approx(
        sum(len(w.sessions) for w in tapers) / len(tapers) / len(peak.sessions)
    )
    assert 0.3 < r["taper_volume_drop"] < 0.6
    assert r["race_week_volume"] < 0.2
    assert r["taper_shape"] == pytest.approx(tapers[-1].hours / tapers[0].hours)
    assert r["weekly_volume_ramp"] < 1.20
    assert r["first_week_fit"] == pytest.approx(marathon.weeks[0].hours / 3.0)
    assert r["first_long_session_fit"] == pytest.approx(68 / 60)
    assert facts["taper_weeks"] == 2
    assert facts["recovery_weeks"] == 2


def test_a_one_week_taper_is_read_with_its_race_week():
    """Bosquet's cut is the whole taper's mean, so one taper week pairs with race week."""
    plan = marathon_plan()
    first_taper = next(w for w in plan.weeks if w.mode == "taper")
    plan = replace(plan, weeks=[w for w in plan.weeks if w is not first_taper])
    r, facts = read(plan, ATHLETE, LONGEST)
    peak = max((w for w in plan.weeks if w.mode == "load"), key=lambda w: w.hours)
    taper, race = plan.weeks[-2], plan.weeks[-1]
    assert facts["taper_weeks"] == 1
    assert r["taper_volume_drop"] == pytest.approx(
        (taper.hours + race.hours) / 2 / peak.hours
    )
    assert r["race_week_volume"] == pytest.approx(race.hours / peak.hours)


def test_step_readings_see_intensity_the_labels_hide(marathon):
    r, facts = read(marathon, ATHLETE, LONGEST)
    assert r["tid_low_share"] > 0.8
    assert r["tid_high_share"] > 0
    assert r["hard_sessions_per_week_build"] == 1.0
    assert r["hard_day_adjacent_same_sport"] == 0
    assert r["vo2max_in_base"] == 0
    assert r["label_mismatches"] == 0
    assert r["warmup_share"] == 1.0
    assert facts["cooldown_share"] == 1.0
    assert r["easy_session_low_share"] > 0.9
    assert r["fuelling_cue_share"] == 1.0  # every 75'+ session carries a cue
    assert r["monotony"] is not None
    assert r["race_day_tsb"] is not None
    assert r["long_run_share"] > 0.3
    assert r["marathon_race_pace_sessions"] == 0


def test_a_mislabelled_and_stacked_week_is_caught():
    week = Week(
        1,
        "base",
        "load",
        sessions=[
            hard_run(0, "easy"),  # easy label, threshold steps
            hard_run(1),  # hard on the next day: adjacent same-sport hard
            vo2_run(2),  # VO2max in base
            session(
                2, "run", "intervals", 40, [Step(1, 10), Step(4, 25), Step(1, 5)]
            ),  # intervals without zone 5
        ],
    )
    plan = Plan(
        [week, Week(2, "taper", "race", sessions=[session(1, minutes=20)])],
        "running_marathon",
        "moderate",
        2,
    )
    r, _ = read(plan, ATHLETE, LONGEST)
    assert r["label_mismatches"] == 2
    assert r["hard_day_adjacent_same_sport"] >= 1
    assert r["vo2max_in_base"] == 1
    assert r["vo2max_work_rest_ratio"] == 1.0
    assert r["z5_minutes_per_session"] == 15


def test_triathlon_readings_bricks_mix_and_swims():
    def week(n: int, phase: str, brick: bool) -> Week:
        s = [
            session(0, "swim", "easy", 45, metres=2000),
            session(1, "bike", "easy", 60),
            session(2, "run", "easy", 45),
            session(4, "swim", "long", 70, metres=3000),
            session(5, "bike", "long", 180),
            session(6, "run", "long", 90),
        ]
        if brick:
            s.append(session(5, "run", "easy", 20))
        return Week(n, phase, "load", sessions=s)

    weeks = [
        week(1, "base", False),
        week(2, "build", True),
        week(3, "build", False),
        week(4, "peak", True),
    ]
    weeks.append(Week(5, "taper", "race", sessions=[session(1, "run", "easy", 20)]))
    plan = Plan(weeks, "ironman", "moderate", 5)
    r, facts = read(
        plan,
        {"run": 2.4, "bike": 4.4, "swim": 1.2},
        {"run": 60, "bike": 90, "swim": 30},
    )
    assert r["brick_weeks_share"] == pytest.approx(2 / 3)
    assert r["long_swim_peak_metres"] == 3000
    assert r["long_ride_peak_hours"] == 3.0
    assert r["sport_mix_bike"] + r["sport_mix_run"] + r[
        "sport_mix_swim"
    ] == pytest.approx(1.0)
    assert r["rest_days_per_week"] == pytest.approx(1.0)
    assert (
        r["peak_long_timing_weeks"] == 1.0
    )  # the longest ride is in every week; the last is week 4
    assert r["long_run_share"] is None
    assert facts["long_run_share_all_goals"] > 0.5
    assert r["marathon_race_pace_sessions"] is None


def test_a_plan_without_steps_leaves_step_readings_unread():
    plan = Plan(
        [
            Week(1, "base", "load", sessions=[Session(0, "run", "easy", 45)]),
            Week(2, "taper", "race", sessions=[Session(1, "run", "easy", 20)]),
        ],
        "running_marathon",
        "basic",
        2,
    )
    r, _ = read(plan, ATHLETE, LONGEST)
    assert r["tid_low_share"] is None
    assert r["warmup_share"] is None
    assert r["fuelling_cue_share"] is None
    assert r["peak_weekly_hours"] == 0.75


def test_unknown_goal_is_refused(marathon):
    marathon.goal = "duathlon"
    with pytest.raises(ValueError, match="unknown goal"):
        read(marathon, ATHLETE, LONGEST)


def test_long_sessions_fall_back_to_the_longest_session_when_nothing_is_labelled():
    plan = marathon_plan(8)
    longest = max(s.hours for w in plan.weeks for s in w.sessions if s.kind == "long")
    for w in plan.weeks:
        for s in w.sessions:
            if s.kind == "long":
                s.kind = "easy"
    r, _ = read(plan, ATHLETE, LONGEST)
    assert r["long_run_peak_hours"] == pytest.approx(longest)


def test_strides_are_not_read_as_vo2max_intervals():
    """Work to rest is read on VO2max sessions only (8+ minutes in zone 5).

    Six 30-second strides with 90 s easy are 3 minutes of zone 5 at a
    rest-over-work of 3; they sharpen, they are not a VO2max set.
    """
    from bench.schema import Repeat
    from tests.bench.conftest import vo2_run

    strides = (
        [Step(1, 20)] + [Step(5, 0.5, "on"), Step(1, 1.5, "off")] * 6 + [Step(1, 8)]
    )
    week = Week(
        1,
        "build",
        "load",
        sessions=[
            session(0, "run", "easy", 40, strides, repeats=[Repeat(6, 5, 0.5, 1, 1.5)]),
            vo2_run(2),
        ],
    )
    plan = Plan(
        [week, Week(2, "taper", "race", sessions=[session(1, minutes=20)])],
        "running_marathon",
        "moderate",
        2,
    )
    r, _ = read(plan, ATHLETE, LONGEST)
    assert r["vo2max_work_rest_ratio"] == 1.0  # the VO2max set alone
