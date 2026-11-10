import json

from bench.parse import parse_plan

ANSWER = {
    "weeks": [
        {
            "week": 1,
            "phase": "Foundation",
            "mode": "normal",
            "total_minutes": 300,
            "sessions": [
                {
                    "day": "Monday",
                    "sport": "cycling",
                    "type": "endurance",
                    "minutes": 60,
                    "steps": [[1, 10], [2, 45], [1, 5]],
                },
                {
                    "day": "wed",
                    "sport": "run",
                    "type": "VO2max",
                    "minutes": 50,
                    "steps": [
                        [1, 15],
                        {"reps": 5, "on": [5, 3], "off": [1, 3]},
                        [1, 5],
                    ],
                },
                {
                    "day": "fri",
                    "sport": "swim",
                    "type": "long",
                    "minutes": 60,
                    "metres": 2800,
                    "steps": [[1, 400], [2, 2000], [1, 400]],
                },
                {
                    "day": "sat",
                    "sport": "bike",
                    "type": "long",
                    "minutes": 240,
                    "steps": [[1, 15], [2, 2400], [1, 15]],
                },
                {"day": "sun", "sport": "run", "type": "rest"},
                {
                    "day": "tue",
                    "sport": "bike",
                    "type": "easy",
                    "minutes": 30,
                    "steps": [[7, 30]],
                },
            ],
        },
        {
            "week": 2,
            "phase": "race",
            "mode": "race_week",
            "sessions": [{"day": "tue", "sport": "run", "type": "easy", "minutes": 30}],
        },
    ]
}


def test_labels_steps_and_defects_are_read():
    plan, structure = parse_plan(
        "Here you go:\n```json\n" + json.dumps(ANSWER) + "\n```",
        "ironman",
        "moderate",
        2,
    )

    assert plan is not None
    w1 = plan.weeks[0]
    assert (w1.phase, w1.mode) == ("base", "load")
    ride, vo2, swim, long_ride, weird = w1.sessions
    assert (ride.day, ride.sport, ride.kind) == (0, "bike", "easy")
    assert vo2.kind == "intervals"
    assert len(vo2.steps) == 12
    assert vo2.repeats[0].reps == 5
    assert vo2.zone_minutes(5) == 15
    # swim steps in metres are scaled to the session's minutes
    assert swim.metres == 2800
    assert abs(sum(s.minutes for s in swim.steps) - 60) < 1e-6
    assert abs(swim.zone_minutes(2, 2) - 60 * 2000 / 2800) < 1e-6
    # a step that does not sum to the session is scaled to it and counted
    assert abs(sum(s.minutes for s in long_ride.steps) - 240) < 1e-6
    assert structure.step_sum_mismatches == 1
    # an out-of-range zone is a malformed step; the session keeps its minutes
    assert weird.steps == []
    assert structure.malformed_steps == 1
    assert structure.stated_total_mismatches == 1  # 300 stated, 440 summed
    assert structure.unknown_labels == 0
    assert plan.weeks[-1].mode == "race"
    assert structure.race_week_last


def test_missing_weeks_and_prose_only_answers():
    plan, structure = parse_plan(
        '{"weeks": [{"week": 1, "sessions": []}]}', "ironman", "basic", 3
    )
    assert plan is not None
    assert structure.weeks_delivered == 1
    assert not structure.numbering_ok
    assert not structure.valid

    plan, structure = parse_plan("I cannot write plans.", "ironman", "basic", 3)
    assert plan is None
    assert structure.defects == ["no JSON object in the answer"]


def test_a_plan_without_steps_is_still_a_plan():
    text = json.dumps(
        {
            "weeks": [
                {
                    "week": 1,
                    "phase": "base",
                    "mode": "load",
                    "sessions": [
                        {"day": "mon", "sport": "run", "type": "easy", "minutes": 45}
                    ],
                }
            ]
        }
    )
    plan, structure = parse_plan(text, "running_marathon", "moderate", 1)
    assert plan is not None
    assert structure.sessions_without_steps == 1
    assert structure.valid


def test_a_trailing_comma_is_repaired_and_counted_as_a_defect():
    text = '{"weeks": [{"week": 1, "phase": "base", "mode": "load", "sessions": [{"day": "mon", "sport": "run", "type": "easy", "minutes": 45,},],}]}'
    plan, structure = parse_plan(text, "running_marathon", "moderate", 1)
    assert plan is not None
    assert structure.defects[0].startswith("json repaired: trailing commas")
    plan, structure = parse_plan(
        '{"weeks": [{"week": 1, "sessions": [{"day": "mon", "notes": "unterminated}]}]}',
        "running_marathon",
        "moderate",
        1,
    )
    assert plan is None
