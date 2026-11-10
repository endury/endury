"""The prose restatement of a planned session's step targets."""

from endury.coach.tools.prescription_summary import summarize_prescribed_steps

# A power Z3 primary target with an HR Z2 secondary target: each band must
# render next to its own zone label.
_TEMPO = [
    {
        "type": "warmup",
        "duration_seconds": 900.0,
        "target": {"target_type": "power", "low_watts": 133, "high_watts": 142},
        "secondary_target": {
            "target_type": "heart_rate_zone",
            "zone": 1,
            "high_bpm": 132,
        },
        "additional_targets": [
            {"target_type": "rpe", "rpe": 2},
            {"target_type": "power_zone", "zone": 1},
        ],
    },
    {
        "type": "interval",
        "duration_seconds": 2220.0,
        "target": {"target_type": "power", "low_watts": 211, "high_watts": 224},
        "secondary_target": {
            "target_type": "heart_rate_zone",
            "zone": 2,
            "low_bpm": 132,
            "high_bpm": 140,
        },
        "additional_targets": [
            {"target_type": "rpe", "rpe": 6},
            {"target_type": "power_zone", "zone": 3},
        ],
    },
]


def test_each_band_sits_next_to_its_own_zone_label():
    assert summarize_prescribed_steps(_TEMPO) == [
        "warmup 15 min — 133-142 W (power Z1), under 132 bpm (HR Z1), RPE 2",
        "interval 37 min — 211-224 W (power Z3), 132-140 bpm (HR Z2), RPE 6",
    ]


def test_pace_and_swim_bands_and_zone_only_targets():
    steps = [
        {
            "type": "interval",
            "duration_seconds": 600,
            "target": {
                "target_type": "pace",
                "low_min_per_km": 4.0,
                "high_min_per_km": 4.25,
            },
            "secondary_target": {"target_type": "heart_rate_zone", "zone": 4},
            "additional_targets": [{"target_type": "pace_zone", "zone": 4}],
        },
        {
            "type": "interval",
            "duration_seconds": 300,
            "target": {
                "target_type": "swim_pace_zone",
                "zone": 3,
                "low_sec_per_100m": 106.0,
                "high_sec_per_100m": 111.0,
            },
        },
        {
            "type": "recovery",
            "duration_seconds": 120,
            "target": {"target_type": "swim_pace_zone", "zone": 1},
        },
        {"type": "rest", "duration_seconds": 60, "target": {"target_type": "pace"}},
    ]
    assert summarize_prescribed_steps(steps) == [
        "interval 10 min — 4:00-4:15 /km, HR Z4",
        "interval 5 min — 1:46-1:51 /100m (swim Z3)",
        "recovery 2 min — swim Z1",
        "rest 1 min",
    ]


def test_an_effort_led_step_keeps_its_flat_pace_out_of_the_line():
    """A hill rep leads on RPE; its pace extra is the flat-ground equivalent,
    not a target the coach may judge the climb against."""
    steps = [
        {
            "type": "interval",
            "duration_seconds": 180,
            "target": {"target_type": "rpe", "rpe": 8},
            "secondary_target": {
                "target_type": "heart_rate_zone",
                "zone": 4,
                "low_bpm": 157,
                "high_bpm": 165,
            },
            "additional_targets": [
                {
                    "target_type": "pace",
                    "low_min_per_km": 5.33,
                    "high_min_per_km": 5.67,
                },
                {"target_type": "pace_zone", "zone": 4},
            ],
        }
    ]
    assert summarize_prescribed_steps(steps) == [
        "interval 3 min — RPE 8, 157-165 bpm (HR Z4)"
    ]


def test_repeat_blocks_carry_their_count():
    steps = [
        {
            "type": "repeat",
            "iterations": 4,
            "steps": [
                {
                    "type": "interval",
                    "duration_seconds": 180,
                    "target": {"target_type": "rpe", "rpe": 8},
                },
                {
                    "type": "recovery",
                    "duration_seconds": 120,
                    "target": {"target_type": "power"},
                },
            ],
        }
    ]
    assert summarize_prescribed_steps(steps) == [
        "4x interval 3 min — RPE 8",
        "4x recovery 2 min",
    ]


def test_unknown_or_bare_targets_render_nothing():
    steps = [
        {
            "type": "interval",
            "duration_seconds": 60,
            "target": {"target_type": "cadence"},
        }
    ]
    assert summarize_prescribed_steps(steps) == ["interval 1 min"]
