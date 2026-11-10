# Benchmark results

Measured 2026-11-03; rulebook version 6, registration `56843d954a2cd566`. Rules met counts every applicable rule; strong counts the rules with A or B evidence. The composite is the mean distance from the bands, equal weight per family; 0 is inside every band. The answers under `raw/` are AI-generated, kept unedited as evidence, and are not training advice. The Claude models ran through Claude Code on a subscription: their cost is an estimate at list price and their time is not comparable.

## marathon_18wk_moderate

running_marathon in 18 weeks, moderate tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 26 (26-26) of 26 | 11 of 11 | 0.000 | 76 h, $0.000 |
| google/gemini-3.5-flash-lite | 5 | 4 | 22 (20-23) of 28 | 8 of 13 | 0.082 | 73 h, $0.036 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 19 (19-19) of 28 | 9 of 13 | 0.086 | 97 h, $0.006 |
| google/gemini-3.8-flash | 1 | 1 | 21 (21-21) of 28 | 9 of 13 | 0.067 | 80 h, $0.042 |
| x-ai/grok-4.7 | 1 | 1 | 21 (21-21) of 28 | 8 of 13 | 0.069 | 95 h, $0.089 |
| openai/gpt-6-sol | 1 | 1 | 19 (19-19) of 26 | 8 of 11 | 0.080 | 77 h, $0.046 |
| anthropic/claude-opus-5.5 | 1 | 1 | 23 (23-23) of 28 | 11 of 13 | 0.040 | 78 h, $0.304 |
| anthropic/claude-sonnet-5 | 1 | 1 | 22 (22-22) of 28 | 9 of 13 | 0.071 | 69 h, $0.217 |

Off band, first sample of each contestant:

- **endury**: none
- **google/gemini-3.5-flash-lite**: recovery_week_depth 0.017, first_long_session_fit 1.0, taper_volume_drop 0.075, taper_intensity_kept 1.0, tid_low_share 0.292, tid_high_share 0.293, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: recovery_cadence 0.125, recovery_week_depth 0.258, first_week_fit 0.056, first_long_session_fit 0.333, taper_volume_drop 0.506, taper_intensity_kept 0.5, tid_low_share 0.22, vo2max_in_base 0.333, fuelling_cues 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.184, first_long_session_fit 0.056, tid_low_share 0.366, tid_high_share 0.318, hard_sessions_per_week 0.25, vo2max_in_base 0.333, fuelling_cues 1.0
- **x-ai/grok-4.7**: recovery_cadence 0.125, first_long_session_fit 0.056, taper_volume_drop 0.488, taper_intensity_kept 1.0, tid_low_share 0.168, tid_high_share 0.222, fuelling_cues 0.743
- **openai/gpt-6-sol**: recovery_week_depth 0.525, taper_volume_drop 0.083, tid_low_share 0.615, tid_high_share 0.586, hard_sessions_per_week 0.5, threshold_minutes 0.013, fuelling_cues 0.4
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.251, first_long_session_fit 0.056, taper_volume_drop 0.223, vo2max_in_base 0.333, fuelling_cues 0.667
- **anthropic/claude-sonnet-5**: taper_volume_drop 0.182, taper_session_frequency 0.333, race_week_volume 0.338, tid_low_share 0.684, tid_high_share 0.269, fuelling_cues 1.0

## ironman_32wk_competitive

ironman in 32 weeks, competitive tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 37 (37-37) of 38 | 14 of 14 | 0.005 | 372 h, $0.000 |
| google/gemini-3.5-flash-lite | 5 | 4 | 20 (17-23) of 37 | 6 of 13 | 0.215 | 386 h, $0.135 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 24 (24-24) of 38 | 7 of 14 | 0.244 | 524 h, $0.052 |
| google/gemini-3.8-flash | 1 | 1 | 31 (31-31) of 38 | 8 of 14 | 0.077 | 404 h, $0.058 |
| x-ai/grok-4.7 | 1 | 1 | 27 (27-27) of 38 | 10 of 14 | 0.112 | 383 h, $0.192 |
| openai/gpt-6-sol | 1 | 1 | 28 (28-28) of 38 | 8 of 14 | 0.090 | 429 h, $0.148 |
| anthropic/claude-opus-5.5 | 1 | 1 | 32 (32-32) of 38 | 10 of 14 | 0.071 | 434 h, $0.657 |
| anthropic/claude-sonnet-5 | 1 | 1 | 29 (29-29) of 38 | 10 of 14 | 0.108 | 287 h, $0.294 |

Off band, first sample of each contestant:

- **endury**: rest_days 0.293
- **google/gemini-3.5-flash-lite**: peak_weekly_hours 0.125, taper_duration 1.0, taper_volume_drop 0.077, taper_intensity_kept 0.427, taper_shape 0.143, race_week_volume 0.364, tid_low_share 1.0, hard_sessions_per_week 0.5, long_swim_peak 0.067, brick_weeks 1.0, sport_mix_bike 0.076, sport_mix_run 0.815, sport_mix_swim 0.677, warmup_before_hard 0.897, vo2max_minutes 0.127, threshold_minutes 1.0, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: recovery_cadence 0.214, first_long_session_fit 0.981, peak_weekly_hours 1.0, taper_duration 1.0, taper_intensity_kept 0.271, race_week_volume 0.215, monotony 0.832, tid_low_share 0.185, hard_sessions_per_week 0.056, long_swim_peak 0.5, peak_long_timing 0.25, rest_days 0.5, threshold_minutes 0.218, fuelling_cues 1.0
- **google/gemini-3.8-flash**: first_long_session_fit 0.889, peak_weekly_hours 0.229, taper_duration 1.0, taper_intensity_kept 0.097, race_week_volume 0.113, warmup_before_hard 0.345, fuelling_cues 1.0
- **x-ai/grok-4.7**: taper_duration 1.0, taper_volume_drop 0.016, race_week_volume 0.068, monotony 0.177, long_swim_peak 0.4, peak_long_timing 0.25, brick_weeks 1.0, sport_mix_bike 0.328, rest_days 0.5, warmup_before_hard 0.058, fuelling_cues 1.0
- **openai/gpt-6-sol**: first_long_session_fit 0.611, peak_weekly_hours 0.542, tid_low_share 0.132, hard_sessions_per_week 0.167, long_ride_peak 0.167, sport_mix_bike 0.07, warmup_before_hard 0.67, vo2max_minutes 0.146, fuelling_cues 1.0, label_honesty 0.2
- **anthropic/claude-opus-5.5**: first_long_session_fit 0.611, peak_weekly_hours 0.538, long_ride_peak 0.25, rest_days 0.5, warmup_before_hard 0.629, fuelling_cues 1.0
- **anthropic/claude-sonnet-5**: first_week_fit 0.381, taper_volume_drop 0.55, taper_session_frequency 0.583, taper_intensity_kept 0.606, taper_shape 0.812, race_week_volume 0.436, brick_weeks 0.385, warmup_before_hard 0.427, fuelling_cues 1.0

## marathon_12wk_moderate

running_marathon in 12 weeks, moderate tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 24 (24-24) of 26 | 11 of 11 | 0.022 | 43 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 19 (18-19) of 26 | 7 of 11 | 0.094 | 50 h, $0.016 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 19 (19-19) of 28 | 9 of 13 | 0.149 | 67 h, $0.013 |
| google/gemini-3.8-flash | 1 | 1 | 19 (19-19) of 28 | 9 of 13 | 0.075 | 48 h, $0.013 |
| x-ai/grok-4.7 | 1 | 1 | 22 (22-22) of 28 | 8 of 13 | 0.068 | 49 h, $0.072 |
| openai/gpt-6-sol | 1 | 1 | 18 (18-18) of 26 | 7 of 11 | 0.064 | 46 h, $0.050 |
| anthropic/claude-opus-5.5 | 1 | 1 | 22 (22-22) of 28 | 9 of 13 | 0.026 | 50 h, $0.273 |
| anthropic/claude-sonnet-5 | 1 | 1 | 22 (22-22) of 28 | 11 of 13 | 0.063 | 44 h, $0.175 |

Rules no contestant met (review the band before counting them): taper_volume_drop.

Off band, first sample of each contestant:

- **endury**: taper_volume_drop 0.097, long_run_peak 0.408
- **google/gemini-3.5-flash-lite**: first_week_fit 0.056, first_long_session_fit 0.333, taper_volume_drop 0.376, tid_low_share 0.606, tid_high_share 0.492, hard_sessions_per_week 0.667, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: recovery_week_depth 0.031, first_week_fit 1.0, first_long_session_fit 0.056, taper_volume_drop 0.445, taper_intensity_kept 0.231, tid_low_share 0.279, vo2max_in_base 0.5, fuelling_cues 0.35, label_honesty 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.069, first_long_session_fit 0.056, taper_volume_drop 0.312, tid_low_share 0.341, tid_high_share 0.175, hard_sessions_per_week 0.333, vo2max_in_base 0.5, threshold_minutes 0.067, fuelling_cues 1.0
- **x-ai/grok-4.7**: first_long_session_fit 0.333, taper_volume_drop 0.482, taper_intensity_kept 0.25, tid_low_share 0.472, tid_high_share 0.196, fuelling_cues 0.933
- **openai/gpt-6-sol**: recovery_week_depth 0.386, first_long_session_fit 0.056, taper_volume_drop 0.315, tid_low_share 0.362, tid_high_share 0.48, hard_sessions_per_week 0.333, threshold_minutes 0.022, fuelling_cues 0.267
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.295, first_long_session_fit 0.056, taper_volume_drop 0.2, tid_low_share 0.169, tid_high_share 0.004, fuelling_cues 0.267
- **anthropic/claude-sonnet-5**: recovery_week_depth 0.129, taper_volume_drop 0.2, tid_low_share 0.209, vo2max_in_base 0.5, long_run_peak 0.222, fuelling_cues 1.0

## marathon_24wk_moderate

running_marathon in 24 weeks, moderate tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 26 (26-26) of 26 | 11 of 11 | 0.000 | 106 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 2 | 20 (19-20) of 28 | 7 of 13 | 0.111 | 101 h, $0.032 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 19 (19-19) of 28 | 9 of 13 | 0.140 | 148 h, $0.008 |
| google/gemini-3.8-flash | 1 | 1 | 21 (21-21) of 28 | 10 of 13 | 0.114 | 116 h, $0.028 |
| x-ai/grok-4.7 | 1 | 1 | 18 (18-18) of 28 | 7 of 13 | 0.105 | 134 h, $0.095 |
| openai/gpt-6-sol | 1 | 1 | 22 (22-22) of 28 | 10 of 13 | 0.060 | 106 h, $0.071 |
| anthropic/claude-opus-5.5 | 1 | 1 | 25 (25-25) of 28 | 12 of 13 | 0.029 | 116 h, $0.355 |
| anthropic/claude-sonnet-5 | 1 | 1 | 24 (24-24) of 28 | 11 of 13 | 0.036 | 100 h, $0.234 |

Off band, first sample of each contestant:

- **endury**: none
- **google/gemini-3.5-flash-lite**: first_long_session_fit 1.0, taper_duration 1.0, taper_volume_drop 0.188, race_week_volume 0.375, tid_low_share 0.458, tid_high_share 0.085, hard_sessions_per_week 0.333, vo2max_minutes 0.207, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: first_week_fit 0.056, first_long_session_fit 0.333, taper_volume_drop 0.033, taper_intensity_kept 0.25, race_week_volume 0.25, tid_low_share 0.555, long_run_peak 0.111, fuelling_cues 1.0, label_honesty 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.173, taper_volume_drop 0.024, tid_low_share 0.577, tid_high_share 0.263, hard_sessions_per_week 0.167, fuelling_cues 0.933, label_honesty 0.8
- **x-ai/grok-4.7**: recovery_week_depth 0.014, first_long_session_fit 0.333, taper_volume_drop 0.262, taper_session_frequency 0.167, taper_intensity_kept 0.732, tid_low_share 0.666, tid_high_share 0.231, hard_sessions_per_week 0.167, vo2max_in_base 0.5, fuelling_cues 0.975
- **openai/gpt-6-sol**: recovery_week_depth 0.388, taper_volume_drop 0.019, tid_low_share 0.639, tid_high_share 0.436, hard_sessions_per_week 0.333, fuelling_cues 0.337
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.239, taper_volume_drop 0.048, fuelling_cues 0.904
- **anthropic/claude-sonnet-5**: first_long_session_fit 0.056, taper_volume_drop 0.251, race_week_volume 0.178, fuelling_cues 1.0

## ironman_24wk_competitive

ironman in 24 weeks, competitive tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 37 (37-37) of 38 | 14 of 14 | 0.004 | 267 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 2 | 24 (24-24) of 36 | 9 of 12 | 0.217 | 267 h, $0.055 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 27 (27-27) of 38 | 9 of 14 | 0.279 | 342 h, $0.019 |
| google/gemini-3.8-flash | 1 | 1 | 27 (27-27) of 38 | 8 of 14 | 0.170 | 309 h, $0.052 |
| x-ai/grok-4.7 | 1 | 1 | 32 (32-32) of 38 | 12 of 14 | 0.045 | 253 h, $0.167 |
| openai/gpt-6-sol | 1 | 0 | - | - | - | no plan: invalid JSON: Expecting ',' delimiter at char 8868 |
| anthropic/claude-opus-5.5 | 1 | 1 | 34 (34-34) of 38 | 11 of 14 | 0.049 | 301 h, $0.539 |
| anthropic/claude-sonnet-5 | 1 | 1 | 29 (29-29) of 38 | 9 of 14 | 0.082 | 197 h, $0.294 |

Off band, first sample of each contestant:

- **endury**: rest_days 0.278
- **google/gemini-3.5-flash-lite**: first_long_session_fit 1.0, taper_volume_drop 0.153, taper_intensity_kept 0.712, race_week_volume 0.465, hard_sessions_per_week 0.5, long_run_peak 0.222, long_swim_peak 0.067, brick_weeks 1.0, sport_mix_bike 0.903, sport_mix_swim 1.0, fuelling_cues 1.0, label_honesty 1.0
- **deepseek/deepseek-v4.1-flash**: first_long_session_fit 1.0, peak_weekly_hours 0.729, taper_duration 1.0, race_week_volume 0.173, monotony 0.673, long_run_peak 0.222, long_swim_peak 0.933, brick_weeks 0.4, rest_days 0.5, fuelling_cues 1.0, label_honesty 1.0
- **google/gemini-3.8-flash**: first_long_session_fit 0.611, peak_weekly_hours 0.417, taper_duration 1.0, taper_volume_drop 0.127, taper_intensity_kept 0.209, race_week_volume 0.251, long_swim_peak 0.067, sport_mix_swim 0.887, warmup_before_hard 0.371, fuelling_cues 1.0, label_honesty 1.0
- **x-ai/grok-4.7**: first_long_session_fit 0.611, taper_volume_drop 0.125, race_week_volume 0.061, long_swim_peak 0.4, sport_mix_bike 0.018, fuelling_cues 1.0
- **anthropic/claude-opus-5.5**: first_long_session_fit 0.056, rest_days 0.5, warmup_before_hard 0.689, fuelling_cues 1.0
- **anthropic/claude-sonnet-5**: first_week_fit 0.502, peak_weekly_hours 0.167, taper_volume_drop 0.094, taper_session_frequency 0.048, taper_intensity_kept 1.0, race_week_volume 0.512, peak_long_timing 0.25, warmup_before_hard 0.371, fuelling_cues 1.0

## ironman_28wk_competitive

ironman in 28 weeks, competitive tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 37 (37-37) of 38 | 14 of 14 | 0.005 | 320 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 2 | 21 (19-23) of 36 | 8 of 12 | 0.224 | 318 h, $0.079 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 29 (29-29) of 38 | 12 of 14 | 0.147 | 330 h, $0.030 |
| google/gemini-3.8-flash | 1 | 1 | 31 (31-31) of 38 | 9 of 14 | 0.120 | 374 h, $0.054 |
| x-ai/grok-4.7 | 1 | 1 | 23 (23-23) of 38 | 7 of 14 | 0.179 | 326 h, $0.188 |
| openai/gpt-6-sol | 1 | 1 | 29 (29-29) of 38 | 10 of 14 | 0.073 | 388 h, $0.147 |
| anthropic/claude-opus-5.5 | 1 | 1 | 31 (31-31) of 38 | 11 of 14 | 0.061 | 332 h, $0.555 |
| anthropic/claude-sonnet-5 | 1 | 0 | - | - | - | the answer exceeded the 64000 output token maximum before any plan was written |

Off band, first sample of each contestant:

- **endury**: rest_days 0.287
- **google/gemini-3.5-flash-lite**: phase_share_base 0.011, first_long_session_fit 1.0, peak_weekly_hours 0.062, taper_duration 1.0, taper_volume_drop 0.58, taper_intensity_kept 0.749, taper_shape 0.063, race_week_volume 0.608, tid_high_share 0.089, long_run_peak 0.889, peak_long_timing 0.75, brick_weeks 1.0, sport_mix_bike 0.093, sport_mix_swim 0.042, rest_days 0.5, fuelling_cues 1.0, label_honesty 1.0
- **deepseek/deepseek-v4.1-flash**: first_long_session_fit 1.0, taper_shape 0.047, long_ride_peak 0.75, long_swim_peak 0.533, peak_long_timing 0.25, sport_mix_bike 0.033, rest_days 0.5, fuelling_cues 1.0, label_honesty 1.0
- **google/gemini-3.8-flash**: first_long_session_fit 0.611, peak_weekly_hours 0.292, taper_intensity_kept 0.164, race_week_volume 0.037, warmup_before_hard 0.175, fuelling_cues 1.0, label_honesty 1.0
- **x-ai/grok-4.7**: phase_share_base 0.083, first_long_session_fit 0.611, peak_weekly_hours 0.167, taper_duration 1.0, taper_volume_drop 0.536, taper_session_frequency 0.444, taper_intensity_kept 0.135, race_week_volume 0.236, long_ride_peak 0.083, peak_long_timing 1.0, sport_mix_bike 0.308, rest_days 0.361, warmup_before_hard 0.276, fuelling_cues 1.0, label_honesty 0.8
- **openai/gpt-6-sol**: recovery_week_depth 0.097, phase_share_base 0.15, phase_share_build 0.167, first_long_session_fit 0.241, peak_weekly_hours 0.667, race_week_volume 0.063, warmup_before_hard 0.406, fuelling_cues 1.0, label_honesty 0.2
- **anthropic/claude-opus-5.5**: phase_share_base 0.15, phase_share_build 0.167, first_long_session_fit 0.611, peak_long_timing 0.25, rest_days 0.34, warmup_before_hard 0.455, fuelling_cues 1.0

## half_24wk_moderate

half_ironman in 24 weeks, moderate tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 37 (37-37) of 37 | 14 of 14 | 0.000 | 225 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 25 (24-29) of 38 | 6 of 14 | 0.211 | 195 h, $0.055 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 29 (29-29) of 38 | 10 of 14 | 0.128 | 252 h, $0.051 |
| google/gemini-3.8-flash | 1 | 1 | 32 (32-32) of 38 | 10 of 14 | 0.124 | 199 h, $0.056 |
| x-ai/grok-4.7 | 1 | 1 | 28 (28-28) of 38 | 8 of 14 | 0.107 | 187 h, $0.163 |
| openai/gpt-6-sol | 1 | 1 | 30 (30-30) of 38 | 9 of 14 | 0.080 | 230 h, $0.119 |
| anthropic/claude-opus-5.5 | 1 | 1 | 30 (30-30) of 37 | 10 of 14 | 0.079 | 229 h, $0.468 |
| anthropic/claude-sonnet-5 | 1 | 1 | 26 (26-26) of 38 | 8 of 14 | 0.070 | 143 h, $0.305 |

Off band, first sample of each contestant:

- **endury**: none
- **google/gemini-3.5-flash-lite**: first_long_session_fit 0.611, taper_intensity_kept 0.25, race_week_volume 0.35, tid_high_share 0.206, peak_long_timing 0.5, brick_weeks 1.0, warmup_before_hard 0.76, vo2max_minutes 0.274, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: peak_weekly_hours 0.312, race_week_volume 0.427, tid_high_share 0.222, long_run_peak 0.556, peak_long_timing 0.25, sport_mix_swim 0.121, warmup_before_hard 0.503, fuelling_cues 1.0, label_honesty 0.8
- **google/gemini-3.8-flash**: taper_intensity_kept 0.131, tid_high_share 0.1, peak_long_timing 1.0, warmup_before_hard 0.3, fuelling_cues 1.0, label_honesty 1.0
- **x-ai/grok-4.7**: recovery_week_depth 0.089, peak_weekly_hours 0.125, taper_intensity_kept 0.388, tid_low_share 0.511, tid_high_share 0.888, hard_sessions_per_week 0.167, peak_long_timing 1.0, rest_days 0.024, warmup_before_hard 0.425, fuelling_cues 1.0
- **openai/gpt-6-sol**: recovery_week_depth 0.18, first_long_session_fit 0.056, race_week_volume 0.35, tid_low_share 0.214, tid_high_share 0.925, peak_long_timing 0.25, warmup_before_hard 0.356, fuelling_cues 1.0
- **anthropic/claude-opus-5.5**: taper_intensity_kept 0.331, race_week_volume 0.046, monotony 0.152, peak_long_timing 0.25, rest_days 0.348, warmup_before_hard 0.618, fuelling_cues 1.0
- **anthropic/claude-sonnet-5**: first_week_fit 0.145, peak_weekly_hours 0.479, taper_volume_drop 0.014, taper_intensity_kept 0.319, race_week_volume 0.341, long_ride_peak 0.083, peak_long_timing 0.25, brick_weeks 0.6, warmup_before_hard 0.447, vo2max_work_rest 0.133, vo2max_minutes 0.103, fuelling_cues 0.6

## ironman_32wk_competitive_light

ironman in 32 weeks, competitive tier, athlete on a low training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 35 (35-35) of 38 | 13 of 14 | 0.006 | 342 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 2 | 22 (21-22) of 36 | 5 of 12 | 0.235 | 282 h, $0.064 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 28 (28-28) of 38 | 9 of 14 | 0.070 | 350 h, $0.022 |
| google/gemini-3.8-flash | 1 | 0 | - | - | - | incomplete plan: weeks delivered: 1 of 32; race-mode weeks: [] (expected only week 32) |
| x-ai/grok-4.7 | 1 | 1 | 25 (25-25) of 38 | 8 of 14 | 0.102 | 333 h, $0.207 |
| openai/gpt-6-sol | 1 | 1 | 28 (28-28) of 38 | 10 of 14 | 0.056 | 353 h, $0.138 |
| anthropic/claude-opus-5.5 | 1 | 1 | 33 (33-33) of 38 | 10 of 14 | 0.066 | 366 h, $0.562 |
| anthropic/claude-sonnet-5 | 1 | 1 | 26 (26-26) of 38 | 6 of 14 | 0.125 | 257 h, $0.321 |

Rules no contestant met (review the band before counting them): first_long_session_fit.

Off band, first sample of each contestant:

- **endury**: first_week_fit 0.1, first_long_session_fit 0.031, rest_days 0.224
- **google/gemini-3.5-flash-lite**: phase_share_base 0.269, first_long_session_fit 1.0, peak_weekly_hours 0.042, taper_duration 1.0, taper_volume_drop 0.437, taper_intensity_kept 0.477, taper_shape 0.325, race_week_volume 0.468, tid_low_share 1.0, brick_weeks 1.0, sport_mix_swim 0.331, warmup_before_hard 0.436, easy_days_easy 1.0, fuelling_cues 1.0, label_honesty 0.6
- **deepseek/deepseek-v4.1-flash**: first_long_session_fit 1.0, peak_weekly_hours 0.354, taper_volume_drop 0.102, taper_intensity_kept 0.161, race_week_volume 0.113, long_run_peak 0.222, long_swim_peak 0.4, sport_mix_swim 0.083, warmup_before_hard 0.133, fuelling_cues 1.0
- **x-ai/grok-4.7**: first_long_session_fit 0.333, taper_duration 1.0, taper_volume_drop 0.301, taper_intensity_kept 0.464, taper_shape 0.06, race_week_volume 0.256, monotony 0.027, tid_high_share 0.031, long_swim_peak 0.533, peak_long_timing 0.5, sport_mix_bike 0.054, rest_days 0.5, fuelling_cues 1.0
- **openai/gpt-6-sol**: recovery_week_depth 0.124, phase_share_build 0.182, first_long_session_fit 0.179, peak_weekly_hours 0.083, race_week_volume 0.109, hard_sessions_per_week 0.136, long_run_peak 0.056, peak_long_timing 0.25, warmup_before_hard 0.467, fuelling_cues 1.0
- **anthropic/claude-opus-5.5**: first_long_session_fit 1.0, form_floor 0.142, rest_days 0.5, warmup_before_hard 0.564, fuelling_cues 1.0
- **anthropic/claude-sonnet-5**: first_long_session_fit 1.0, peak_weekly_hours 0.283, taper_duration 1.0, taper_volume_drop 0.193, taper_session_frequency 0.028, taper_intensity_kept 1.0, taper_shape 0.297, race_week_volume 0.471, long_run_peak 0.111, warmup_before_hard 0.589, vo2max_minutes 0.059, fuelling_cues 1.0

## ironman_32wk_competitive_heavy

ironman in 32 weeks, competitive tier, athlete on a high training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 37 (37-37) of 38 | 14 of 14 | 0.005 | 385 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 2 | 19 (18-20) of 38 | 7 of 14 | 0.263 | 455 h, $0.092 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 24 (24-24) of 38 | 7 of 14 | 0.163 | 597 h, $0.019 |
| google/gemini-3.8-flash | 1 | 1 | 26 (26-26) of 38 | 7 of 14 | 0.172 | 442 h, $0.066 |
| x-ai/grok-4.7 | 1 | 1 | 29 (29-29) of 38 | 8 of 14 | 0.109 | 476 h, $0.216 |
| openai/gpt-6-sol | 1 | 1 | 24 (24-24) of 38 | 6 of 14 | 0.160 | 544 h, $0.174 |
| anthropic/claude-opus-5.5 | 1 | 1 | 32 (32-32) of 38 | 10 of 14 | 0.142 | 480 h, $0.633 |
| anthropic/claude-sonnet-5 | 1 | 1 | 27 (27-27) of 38 | 9 of 14 | 0.143 | 308 h, - |

Off band, first sample of each contestant:

- **endury**: rest_days 0.293
- **google/gemini-3.5-flash-lite**: first_long_session_fit 1.0, peak_weekly_hours 0.792, taper_duration 1.0, taper_volume_drop 0.268, taper_intensity_kept 0.219, taper_shape 0.6, race_week_volume 0.537, monotony 0.082, tid_high_share 0.419, hard_sessions_per_week 1.0, long_ride_peak 0.125, long_swim_peak 0.2, peak_long_timing 0.75, sport_mix_bike 0.322, sport_mix_swim 0.089, rest_days 0.5, fuelling_cues 1.0, label_honesty 1.0
- **deepseek/deepseek-v4.1-flash**: first_long_session_fit 0.611, peak_weekly_hours 1.0, taper_volume_drop 0.273, taper_session_frequency 1.0, taper_intensity_kept 0.171, race_week_volume 0.132, form_floor 0.209, monotony 0.048, vo2max_in_base 1.0, long_swim_peak 0.2, peak_long_timing 0.25, rest_days 0.5, threshold_minutes 1.0, fuelling_cues 1.0
- **google/gemini-3.8-flash**: first_long_session_fit 0.241, peak_weekly_hours 0.458, taper_duration 1.0, taper_volume_drop 0.032, taper_intensity_kept 0.497, race_week_volume 0.131, peak_long_timing 0.75, rest_days 0.5, warmup_before_hard 0.3, vo2max_minutes 0.167, fuelling_cues 1.0, label_honesty 1.0
- **x-ai/grok-4.7**: first_long_session_fit 0.611, peak_weekly_hours 0.75, taper_duration 1.0, taper_volume_drop 0.357, taper_intensity_kept 0.718, long_swim_peak 0.133, peak_long_timing 1.0, vo2max_minutes 0.036, fuelling_cues 1.0
- **openai/gpt-6-sol**: recovery_week_depth 0.008, phase_share_base 0.371, first_long_session_fit 0.241, peak_weekly_hours 1.0, taper_intensity_kept 0.386, race_week_volume 0.243, monotony 0.447, tid_low_share 0.051, tid_high_share 0.191, hard_sessions_per_week 0.167, long_ride_peak 0.25, rest_days 0.5, warmup_before_hard 0.276, fuelling_cues 1.0
- **anthropic/claude-opus-5.5**: peak_weekly_hours 0.458, taper_intensity_kept 0.093, monotony 0.671, peak_long_timing 0.25, rest_days 0.5, fuelling_cues 1.0
- **anthropic/claude-sonnet-5**: first_week_fit 0.551, taper_duration 1.0, taper_volume_drop 0.47, taper_session_frequency 0.583, taper_intensity_kept 0.775, taper_shape 0.349, race_week_volume 0.498, long_swim_peak 0.3, vo2max_minutes 0.958, threshold_minutes 0.279, fuelling_cues 1.0

## marathon_18wk_moderate_light

running_marathon in 18 weeks, moderate tier, athlete on a low training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 20 (20-20) of 26 | 10 of 11 | 0.067 | 56 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 18 (18-18) of 26 | 7 of 11 | 0.123 | 64 h, $0.020 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 20 (20-20) of 28 | 9 of 13 | 0.140 | 88 h, $0.009 |
| google/gemini-3.8-flash | 1 | 1 | 19 (19-19) of 28 | 10 of 13 | 0.098 | 61 h, $0.017 |
| x-ai/grok-4.7 | 1 | 1 | 20 (20-20) of 28 | 10 of 13 | 0.079 | 67 h, $0.083 |
| openai/gpt-6-sol | 1 | 1 | 18 (18-18) of 26 | 8 of 11 | 0.103 | 59 h, $0.053 |
| anthropic/claude-opus-5.5 | 1 | 1 | 22 (22-22) of 28 | 10 of 13 | 0.042 | 64 h, $0.311 |
| anthropic/claude-sonnet-5 | 1 | 1 | 20 (20-20) of 28 | 10 of 13 | 0.081 | 58 h, $0.192 |

Rules no contestant met (review the band before counting them): taper_volume_drop, tid_low_share.

Off band, first sample of each contestant:

- **endury**: weekly_volume_ramp 0.296, first_week_fit 1.0, taper_volume_drop 0.256, tid_low_share 0.01, long_run_peak 0.404, threshold_minutes 0.108
- **google/gemini-3.5-flash-lite**: first_week_fit 0.333, first_long_session_fit 0.796, taper_volume_drop 0.669, tid_low_share 0.269, tid_high_share 0.03, hard_sessions_per_week 0.4, fuelling_cues 1.0, label_honesty 0.6
- **deepseek/deepseek-v4.1-flash**: first_week_fit 1.0, taper_volume_drop 0.532, taper_intensity_kept 1.0, tid_low_share 0.772, tid_high_share 0.199, hard_sessions_per_week 0.4, vo2max_in_base 0.4, fuelling_cues 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.118, first_week_fit 0.025, taper_volume_drop 0.021, tid_low_share 0.759, tid_high_share 0.273, hard_sessions_per_week 0.25, threshold_minutes 0.19, fuelling_cues 1.0, label_honesty 0.4
- **x-ai/grok-4.7**: recovery_cadence 0.667, recovery_week_depth 0.198, first_week_fit 0.025, taper_volume_drop 0.181, tid_low_share 0.645, tid_high_share 0.408, hard_sessions_per_week 0.4, fuelling_cues 0.327
- **openai/gpt-6-sol**: recovery_week_depth 0.332, first_week_fit 0.025, taper_volume_drop 0.015, taper_intensity_kept 1.0, tid_low_share 0.701, tid_high_share 0.765, hard_sessions_per_week 0.6, threshold_minutes 0.283
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.161, taper_volume_drop 0.332, taper_intensity_kept 0.173, tid_low_share 0.095, long_run_peak 0.111, fuelling_cues 0.711
- **anthropic/claude-sonnet-5**: recovery_week_depth 0.139, first_week_fit 0.488, taper_volume_drop 0.158, tid_low_share 0.486, hard_sessions_per_week 0.25, long_run_peak 0.222, warmup_before_hard 0.3, fuelling_cues 0.85

## marathon_18wk_moderate_heavy

running_marathon in 18 weeks, moderate tier, athlete on a high training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 26 (26-26) of 26 | 11 of 11 | 0.000 | 83 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 21 (17-22) of 28 | 9 of 13 | 0.110 | 97 h, $0.025 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 21 (21-21) of 28 | 9 of 13 | 0.141 | 109 h, $0.006 |
| google/gemini-3.8-flash | 1 | 1 | 19 (19-19) of 28 | 9 of 13 | 0.090 | 94 h, $0.018 |
| x-ai/grok-4.7 | 1 | 1 | 21 (21-21) of 28 | 9 of 13 | 0.067 | 93 h, $0.075 |
| openai/gpt-6-sol | 1 | 1 | 21 (21-21) of 28 | 10 of 13 | 0.081 | 106 h, $0.061 |
| anthropic/claude-opus-5.5 | 1 | 1 | 24 (24-24) of 28 | 11 of 13 | 0.025 | 97 h, $0.323 |
| anthropic/claude-sonnet-5 | 1 | 1 | 22 (22-22) of 28 | 10 of 13 | 0.054 | 92 h, $0.208 |

Off band, first sample of each contestant:

- **endury**: none
- **google/gemini-3.5-flash-lite**: taper_volume_drop 0.621, tid_low_share 0.626, tid_high_share 0.504, hard_sessions_per_week 0.4, vo2max_minutes 0.025, fuelling_cues 1.0
- **deepseek/deepseek-v4.1-flash**: taper_volume_drop 0.483, taper_intensity_kept 0.442, vo2max_in_base 0.333, vo2max_work_rest 0.067, vo2max_minutes 0.531, fuelling_cues 1.0, label_honesty 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.184, taper_volume_drop 0.015, taper_intensity_kept 0.444, race_week_volume 0.012, tid_low_share 0.649, tid_high_share 0.342, hard_sessions_per_week 0.25, fuelling_cues 1.0, label_honesty 0.2
- **x-ai/grok-4.7**: taper_volume_drop 0.23, taper_intensity_kept 0.671, race_week_volume 0.048, tid_low_share 0.242, tid_high_share 0.158, vo2max_in_base 0.333, fuelling_cues 1.0
- **openai/gpt-6-sol**: recovery_week_depth 0.357, taper_volume_drop 0.604, taper_intensity_kept 1.0, race_week_volume 0.141, tid_low_share 0.135, vo2max_in_base 0.5, fuelling_cues 0.48
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.195, taper_volume_drop 0.053, tid_low_share 0.207, fuelling_cues 0.5
- **anthropic/claude-sonnet-5**: recovery_week_depth 0.25, taper_volume_drop 0.056, tid_low_share 0.415, tid_high_share 0.015, vo2max_in_base 0.333, fuelling_cues 1.0

## ironman_32wk_competitive_cap12

ironman in 32 weeks, competitive tier, athlete on a typical training volume, weekly cap 12.0 h.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 34 (34-34) of 38 | 13 of 14 | 0.010 | 340 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 24 (17-25) of 38 | 8 of 14 | 0.288 | 350 h, $0.080 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 26 (26-26) of 38 | 9 of 14 | 0.135 | 329 h, $0.014 |
| google/gemini-3.8-flash | 1 | 1 | 29 (29-29) of 36 | 7 of 12 | 0.131 | 329 h, $0.061 |
| x-ai/grok-4.7 | 1 | 1 | 31 (31-31) of 38 | 9 of 14 | 0.077 | 316 h, $0.201 |
| openai/gpt-6-sol | 1 | 1 | 28 (28-28) of 38 | 7 of 14 | 0.099 | 314 h, $0.138 |
| anthropic/claude-opus-5.5 | 1 | 1 | 33 (33-33) of 38 | 10 of 14 | 0.056 | 326 h, $0.569 |
| anthropic/claude-sonnet-5 | 1 | 0 | - | - | - | the answer exceeded the 64000 output token maximum before any plan was written |

Off band, first sample of each contestant:

- **endury**: peak_weekly_hours 0.122, taper_volume_drop 0.064, long_run_peak 0.111, rest_days 0.293
- **google/gemini-3.5-flash-lite**: phase_share_base 0.321, first_long_session_fit 0.611, taper_duration 1.0, taper_volume_drop 0.155, taper_intensity_kept 0.11, taper_shape 0.167, race_week_volume 0.278, hard_sessions_per_week 0.722, long_run_peak 0.111, long_swim_peak 0.2, sport_mix_bike 0.106, rest_days 0.5, fuelling_cues 1.0, label_honesty 1.0
- **deepseek/deepseek-v4.1-flash**: first_long_session_fit 1.0, peak_weekly_hours 0.5, taper_intensity_kept 0.583, vo2max_in_base 1.0, long_ride_peak 0.25, long_run_peak 0.333, long_swim_peak 0.8, peak_long_timing 0.25, brick_weeks 1.0, sport_mix_swim 0.048, warmup_before_hard 0.065, fuelling_cues 1.0
- **google/gemini-3.8-flash**: first_long_session_fit 0.611, peak_weekly_hours 0.083, taper_intensity_kept 0.191, long_run_peak 0.111, warmup_before_hard 0.153, fuelling_cues 1.0, label_honesty 1.0
- **x-ai/grok-4.7**: first_long_session_fit 0.241, peak_weekly_hours 0.5, taper_duration 1.0, long_ride_peak 0.125, peak_long_timing 0.5, warmup_before_hard 0.415, fuelling_cues 1.0
- **openai/gpt-6-sol**: first_long_session_fit 0.241, peak_weekly_hours 0.5, taper_duration 1.0, tid_low_share 0.285, tid_high_share 0.206, hard_sessions_per_week 0.5, long_run_peak 0.333, rest_days 0.024, warmup_before_hard 0.3, fuelling_cues 1.0
- **anthropic/claude-opus-5.5**: first_long_session_fit 0.241, peak_weekly_hours 0.5, rest_days 0.385, warmup_before_hard 0.557, fuelling_cues 1.0

## marathon_18wk_moderate_cap5

running_marathon in 18 weeks, moderate tier, athlete on a typical training volume, weekly cap 5.0 h.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 24 (24-24) of 26 | 11 of 11 | 0.010 | 73 h, $0.000 |
| google/gemini-3.5-flash-lite | 3 | 3 | 23 (18-23) of 26 | 9 of 11 | 0.094 | 65 h, $0.020 |
| deepseek/deepseek-v4.1-flash | 1 | 1 | 21 (21-21) of 28 | 10 of 13 | 0.095 | 71 h, $0.004 |
| google/gemini-3.8-flash | 1 | 1 | 19 (19-19) of 28 | 8 of 13 | 0.093 | 71 h, $0.042 |
| x-ai/grok-4.7 | 1 | 1 | 21 (21-21) of 28 | 8 of 13 | 0.072 | 70 h, $0.063 |
| openai/gpt-6-sol | 1 | 1 | 19 (19-19) of 26 | 8 of 11 | 0.075 | 68 h, $0.051 |
| anthropic/claude-opus-5.5 | 1 | 1 | 23 (23-23) of 28 | 11 of 13 | 0.038 | 72 h, $0.312 |
| anthropic/claude-sonnet-5 | 1 | 1 | 22 (22-22) of 28 | 9 of 13 | 0.067 | 66 h, $0.194 |

Off band, first sample of each contestant:

- **endury**: taper_volume_drop 0.214, long_run_peak 0.111
- **google/gemini-3.5-flash-lite**: first_long_session_fit 1.0, taper_volume_drop 0.003, taper_session_frequency 0.167, taper_intensity_kept 0.599, tid_low_share 0.014, long_run_peak 0.167, fuelling_cues 1.0, label_honesty 0.4
- **deepseek/deepseek-v4.1-flash**: recovery_cadence 0.125, taper_volume_drop 0.304, tid_low_share 0.811, tid_high_share 0.398, hard_sessions_per_week 0.25, vo2max_in_base 0.667, fuelling_cues 1.0
- **google/gemini-3.8-flash**: recovery_week_depth 0.191, first_long_session_fit 0.056, taper_volume_drop 0.025, taper_intensity_kept 1.0, tid_low_share 0.505, tid_high_share 0.06, hard_sessions_per_week 0.25, fuelling_cues 1.0, label_honesty 0.2
- **x-ai/grok-4.7**: recovery_week_depth 0.118, first_long_session_fit 0.333, taper_intensity_kept 0.219, tid_low_share 0.634, tid_high_share 0.224, hard_sessions_per_week 0.2, fuelling_cues 1.0
- **openai/gpt-6-sol**: recovery_week_depth 0.371, taper_volume_drop 0.233, tid_low_share 0.563, tid_high_share 0.65, hard_sessions_per_week 0.667, threshold_minutes 0.078, fuelling_cues 0.062
- **anthropic/claude-opus-5.5**: recovery_week_depth 0.181, taper_volume_drop 0.338, tid_low_share 0.197, long_run_peak 0.111, fuelling_cues 0.6
- **anthropic/claude-sonnet-5**: recovery_week_depth 0.165, taper_intensity_kept 0.167, tid_low_share 0.424, tid_high_share 0.225, long_run_peak 0.333, fuelling_cues 1.0

## ironman_22wk_competitive

ironman in 22 weeks, competitive tier, athlete on a typical training volume.

| Contestant | Samples | Valid | Rules met, median (range) | Strong met | Composite | Total hours, cost |
| --- | --- | --- | --- | --- | --- | --- |
| endury | 1 | 1 | 36 (36-36) of 38 | 13 of 14 | 0.026 | 249 h, $0.000 |
| google/gemini-3.5-flash-lite | 0 | 0 | - | - | - | ceiling or dry run |
| deepseek/deepseek-v4.1-flash | 0 | 0 | - | - | - | ceiling or dry run |
| google/gemini-3.8-flash | 0 | 0 | - | - | - | ceiling or dry run |
| x-ai/grok-4.7 | 0 | 0 | - | - | - | ceiling or dry run |
| openai/gpt-6-sol | 0 | 0 | - | - | - | ceiling or dry run |
| anthropic/claude-opus-5.5 | 0 | 0 | - | - | - | ceiling or dry run |
| anthropic/claude-sonnet-5 | 0 | 0 | - | - | - | ceiling or dry run |

Rules no contestant met (review the band before counting them): rest_days, taper_intensity_kept.

Off band, first sample of each contestant:

- **endury**: taper_intensity_kept 1.0, rest_days 0.325
