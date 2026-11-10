# Rule Dossier — RACE SPECIFICITY (marathon, half-Ironman, Ironman)

- **Family:** specificity (long sessions, bricks, sport mix, volume by level, phase structure)
- **Date:** 2026-09-25
- **Tools available:** WebSearch and WebFetch were both available; every DOI below was
  fetched from `https://api.crossref.org/works/<DOI>` and every book was checked against
  a publisher or bookseller listing for its ISBN.
- **Minutes spent:** ~30 (read repo docs + constraint files ~8 min, literature search and
  Crossref/ISBN verification ~17 min, write-up ~5 min).
- **Repo rules already read:** `docs/research/triathlon_long_course.md` §2,3,4,6,8;
  `docs/research/triathlon.md` §2,4; `docs/research/endurance_methodology.md` §1,2;
  `devtools/quality/constraints/{shared,ironman,half_ironman,running_marathon}.yaml`;
  `RULES` tuple at `devtools/quality/planner/score_quality.py:1250`.

---

```yaml
rule: long_ride_peak
family: specificity
reads: "hours of the longest ride labelled long (fallback: the longest ride)"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 4.5, high: 6.0, unit: hours, ramp: 2.0}   # ironman band; half-ironman is 3.0-4.25h, see notes
evidence_grade: C
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Longest Ironman ride reaches ~5-6 h; longest half-Ironman ride ~3-4 h, peaking weeks before race."
    verified: isbn
  - citation: "Friel J & Byrn G (2009). Going Long, 2nd ed. VeloPress."
    isbn: "9781934030066"
    states: "Prescribes the season's longest ride at 5-6 h (Ironman) or up to 100 miles for time-limited athletes."
    verified: isbn
  - citation: "Vleck VE, Bentley DJ, Millet GP & Cochrane T (2010). Triathlon Event Distance Specialization: Training and Injury Effects. J Strength Cond Res, 24(1), 30-36."
    doi: "10.1519/JSC.0b013e3181bd4cc8"
    states: "British national-squad Ironman-distance triathletes reported longer weekly 'long bike' sessions than Olympic-distance triathletes."
    verified: crossref
    crossref_title: "Triathlon Event Distance Specialization: Training and Injury Effects"
disagreements: >-
  No source disputes the range; Vleck et al. is qualitative (IR > OD long-ride duration)
  and does not itself supply an hour ceiling, so it corroborates the shape of the rule
  without independently verifying 4.5-6.0 h.
held_out: false   # devtools/quality/constraints/ironman.yaml:long_bike_peak and half_ironman.yaml:long_bike_peak both exist
school_specific: false
notes_for_implementer: >-
  No numeric peer-reviewed source for the hour ceiling was found; the band is coaching
  convention (two independent VeloPress-lineage authors, so still counted C not D).
  Half-Ironman band: low 3.0, high 4.25 h (Friel + Dixon, same evidence grade).
  Edge case: a plan with no session typed "long" should fall back to the longest bike
  session of any type, or the rule cannot score a plan that uses only "endurance" labels.
```

```yaml
rule: long_run_peak
family: specificity
reads: "hours of the longest run labelled long (fallback: the longest run)"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 2.5, high: 3.0, unit: hours, ramp: 1.5}   # ironman band; half-ironman is 1.5-2.25h, see notes
evidence_grade: C
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Longest Ironman run should not exceed ~3 h; longest half-Ironman run ~1.5-2 h."
    verified: isbn
  - citation: "Friel J & Byrn G (2009). Going Long, 2nd ed. VeloPress."
    isbn: "9781934030066"
    states: "Caps the longest run below 3 h, citing injury/recovery cost beyond that point for full-distance athletes."
    verified: isbn
disagreements: "None found; no independent peer-reviewed ceiling located (see Not found)."
held_out: false   # ironman.yaml:long_run_peak and half_ironman.yaml:long_run_peak both exist
school_specific: false
notes_for_implementer: >-
  Both sources share an author (Friel), so this is effectively single-school agreement
  padded by a second title, not two independent schools; treat evidence_grade C as
  optimistic. Half-Ironman band: low 1.5, high 2.25 h. Edge case: bike volume is said to
  substitute for run volume (tlc §4) — a plan with a short longest run but very long rides
  should not be penalized without checking total durability stimulus.
```

```yaml
rule: long_swim_peak
family: specificity
reads: "metres of the longest swim labelled long (fallback: the longest swim)"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 3800.0, high: 4500.0, unit: metres, ramp: 1500.0}   # ironman; half-ironman is 2000-3500 m, see notes
evidence_grade: D
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Peak swim volume reaches or slightly exceeds race distance (3.8 km Ironman, 1.9 km half)."
    verified: isbn
  - citation: "Dixon M (2017). Fast-Track Triathlete. VeloPress."
    isbn: "9781937715748"
    states: "Time-efficient long-course prep still rehearses a continuous swim at or near race distance before taper."
    verified: isbn
disagreements: "None found."
held_out: false   # ironman.yaml:long_swim_peak and half_ironman.yaml:long_swim_peak both exist
school_specific: false
notes_for_implementer: >-
  Weakest-evidenced rule in this family: no peer-reviewed source gives a peak swim
  distance number, only two coaching texts (Friel, Dixon) converge on "near race
  distance." Half-Ironman band: low 2000, high 3500 m (already wider than the IM band's
  proportional equivalent because practice varies more). Edge case: open-water
  swims logged by time, not metres, need a pace conversion before this rule can read them.
```

```yaml
rule: peak_long_timing
family: specificity
reads: "weeks between the longest ride/run and race week"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 4.0, high: 6.0, unit: weeks, ramp: 4.0}   # ironman; half-ironman is 2-4 weeks, see notes
evidence_grade: D
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "The longest ride/run should land 4-6 weeks before an Ironman, then taper begins."
    verified: isbn
disagreements: "None found; no second source located (see Not found)."
held_out: false   # ironman.yaml:peak_long_session_timing and half_ironman.yaml:peak_long_session_timing exist
school_specific: true
notes_for_implementer: >-
  Single-source rule: only Friel gives an explicit weeks-out number for the long-session
  peak. Going Long and Dixon discuss taper length (see race_day_freshness_ratio, out of
  this family's scope) but not specifically when the longest session should fall. Flag as
  school_specific because a different periodization school (e.g. block-training,
  Issurin-style) could plausibly place the longest session later given a shorter taper.
  Half-Ironman band: low 2.0, high 4.0 weeks (empirical, not literature). Edge case: a plan whose longest ride and longest run peak
  in different weeks needs a rule for which one is "the" long session, or both must clear
  the band independently.
```

```yaml
rule: brick_sessions
family: specificity
reads: "a bike session immediately followed by a run session on the same day; its duration, and how many such days appear per phase per week"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 33.0, high: 42.0, unit: "minutes of the longest BUILD brick run", ramp: 15.0}
evidence_grade: B
sources:
  - citation: "Millet GP & Vleck VE (2000). Physiological and Biomechanical Adaptations to the Cycle to Run Transition in Olympic Triathlon. Br J Sports Med, 34(5), 384-390."
    doi: "10.1136/bjsm.34.5.384"
    states: "Documents altered running kinematics (forward lean, stride frequency, delayed VO2 kinetics) immediately after cycling; the effect diminishes with practice."
    verified: crossref
    crossref_title: "Physiological and biomechanical adaptations to the cycle to run transition in Olympic triathlon: review and practical recommendations for training"
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Prescribes weekly bricks in Build/Peak, run duration rising from ~15-20 to 40+ min as race day nears."
    verified: isbn
  - citation: "Dixon M (2017). Fast-Track Triathlete. VeloPress."
    isbn: "9781937715748"
    states: "Treats the brick as a specificity tool: short, focused transition runs, not volume accumulation."
    verified: isbn
disagreements: >-
  Millet & Vleck studied Olympic-distance (sprint-length) bricks; extrapolating the
  transition-adaptation mechanism to long-course brick *duration* prescriptions is the
  coaching layer's inference, not something the paper itself tested at Ironman durations.
held_out: false   # devtools/quality/constraints/shared.yaml:build_brick_run_duration and
                   # ironman.yaml/half_ironman.yaml:brick_pairs_with_key_ride exist, but no
                   # rule reads brick *frequency per week by phase* — that fact is unread.
school_specific: false
notes_for_implementer: >-
  This dossier item bundles two distinct facts the prompt asked about: (1) the
  physiological case for bricks at all — well supported (grade B, Millet & Vleck) — and
  (2) the specific minute band and weekly frequency by phase — coaching convention only
  (grade C/D). The frequency-per-week-by-phase table in tlc §6 / tri §4 has no
  corresponding scorer rule at all (fully held out); only BUILD brick-run duration and
  PEAK brick-pairing (brick_pairs_with_key_ride) are graded. Edge case: a same-day bike
  then run with a rest block between them (not a true transition) should not count as a
  brick; the benchmark's definition ("same day, bike then run") cannot distinguish a
  true brick from two unrelated sessions without a transition-time or note check.
```

```yaml
rule: race_pace_rehearsal
family: specificity
reads: "hours of the longest race-pace-labelled ride/run in PEAK, and its zone/effort profile"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 4.0, high: 5.5, unit: hours, ramp: 2.0}   # ironman race-pace long ride; half-ironman is 2.5-3.5h, see notes
evidence_grade: D
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Peak-phase includes race-pace long rides and race-simulation bricks covering ~70-80% of race bike duration."
    verified: isbn
  - citation: "Friel J & Byrn G (2009). Going Long, 2nd ed. VeloPress."
    isbn: "9781934030066"
    states: "Prescribes race-simulation days combining race-pace nutrition, pacing, and duration rehearsal before taper."
    verified: isbn
disagreements: "None found; no peer-reviewed source ties a specific duration or race-distance share to race-pace rehearsal."
held_out: false   # ironman.yaml:race_pace_long_bike_peak and the UNBANDED race_pace_ride_ends_at_effort rule both exist
school_specific: false
notes_for_implementer: >-
  Run-walk pacing research (Périard 2015 on heat; general Ironman run-walk practice, see
  tlc §10) bears on race-day pacing strategy but not on how much race-pace rehearsal
  volume a plan should schedule, so it was not counted as a source for this band.
  Half-Ironman band: low 2.5, high 3.5 h. Edge case: a plan that rehearses race pace in
  short repeated intervals rather than one continuous long effort satisfies the
  physiological intent but would not read as a single qualifying session under this rule.
```

```yaml
rule: sport_mix
family: specificity
reads: "swim/bike/run share of total weekly training hours, averaged over load weeks"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {bike: [0.41, 0.55], run: [0.25, 0.38], swim: [0.15, 0.24], unit: "share of plan hours", ramp: "0.10 bike/run, 0.05 swim"}
evidence_grade: C
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Swim ~15-20%, bike ~45-55%, run ~25-35% of weekly hours, adjusted for the athlete's limiters."
    verified: isbn
  - citation: "Dixon M (2017). Fast-Track Triathlete. VeloPress."
    isbn: "9781937715748"
    states: "Time-constrained long-course athletes still protect bike volume as the highest training-ROI discipline."
    verified: isbn
  - citation: "Gilinsky N, Hawkins KR, Tokar TN & Cooper JA (2014). Predictive variables for half-Ironman triathlon performance. J Sci Med Sport, 17(3), 300-305."
    doi: "10.1016/j.jsams.2013.04.014"
    states: "Regression study of 209 half-Ironman finishers relating training characteristics by discipline to race performance."
    verified: crossref
    crossref_title: "Predictive variables for half-Ironman triathlon performance"
disagreements: >-
  Gilinsky et al. is cited as corroborating evidence that discipline-specific training
  volume predicts performance; this dossier did not obtain the paper's full text and
  cannot confirm it reports the same 15/50/30% split, so it should not be read as
  independently verifying the exact percentages, only the general principle.
held_out: false   # shared.yaml:sport_mix_bike/run/swim all exist, covers=TRIATHLON
school_specific: false
notes_for_implementer: >-
  Friel explicitly frames this as a starting point overridden by an athlete's
  "limiters," so a plan that deviates for a documented weak discipline is not
  necessarily a defect — the scorer cannot see the athlete's limiter without an
  additional signal. Edge case: strength/mobility minutes logged as a fourth "sport"
  would deflate all three shares unless excluded from the denominator.
```

```yaml
rule: peak_weekly_hours_by_level
family: specificity
reads: "hours of the single biggest training week (race week excluded), by athlete tier"
applies_to: {goals: [ironman, half_ironman, marathon], tiers: [basic, moderate, competitive]}
band:
  ironman: {basic: [10.0, 14.0], moderate: [12.0, 16.0], competitive: [14.0, 18.0]}
  half_ironman: {basic: [8.0, 12.0], moderate: [11.0, 15.0], competitive: [14.0, 18.0]}
  marathon: {range: [7.5, 8.5], unit: hours, note: "untiered, thin"}
evidence_grade: B
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Recreational Ironman 10-14 h/week peak, competitive age-group 14-18 h/week, similar half-Ironman tiers."
    verified: isbn
  - citation: "Sinisgalli R, de Lira CAB, Vancini RL, Puccinelli PJ, Hill L, Knechtle B, Nikolaidis PT & Andrade MS (2021). Impact of training volume and experience on amateur Ironman triathlon performance. Physiol Behav, 232, 113344."
    doi: "10.1016/j.physbeh.2021.113344"
    states: "99 amateur Ironman finishers trained 9-20 h/week pre-race; more than ~14 h/week showed no extra performance benefit."
    verified: crossref
    crossref_title: "Impact of training volume and experience on amateur Ironman triathlon performance"
  - citation: "Knechtle B, Wirth A, Baumann B, Knechtle P, Rosemann T & Senn O (2010). Differential Correlations Between Anthropometry, Training Volume, and Performance in Male and Female Ironman Triathletes. J Strength Cond Res."
    doi: "10.1519/JSC.0b013e3181c643b6"
    states: "27 male and 16 female amateur Ironman finishers averaged 14.8 h/week and 13.9 h/week training, respectively."
    verified: crossref
    crossref_title: "Differential Correlations Between Anthropometry, Training Volume, and Performance in Male and Female Ironman Triathletes"
  - citation: "Vickers AJ & Vertosick EA (2016). An empirical study of race times in recreational endurance runners. BMC Sports Sci Med Rehabil, 8, 51."
    doi: "10.1186/s13102-016-0052-y"
    states: "2,303 recreational runners: weekly training mileage was strongly and independently associated with race time at all distances, including marathon."
    verified: crossref
    crossref_title: "An empirical study of race times in recreational endurance runners"
disagreements: >-
  Sinisgalli et al. and Knechtle et al. both cluster amateur Ironman finishers around
  9-15 h/week rather than spanning the full 10-22 h Friel three-tier range; they cannot
  distinguish "basic" from "moderate" tiers because their cohorts were not tier-labelled,
  so they corroborate the lower/middle band but do not independently test the
  competitive-tier 14-18 h ceiling.
held_out: false   # ironman.yaml/half_ironman.yaml:peak_weekly_hours and running_marathon.yaml:peak_weekly_hours all exist
school_specific: false
notes_for_implementer: >-
  Best-evidenced rule in this family (two independent peer-reviewed cohorts plus a
  2,303-runner survey). Marathon band is thin (untiered) — a third
  independent source (Daniels, see marathon_long_run_peak) gives
  qualitative tiering but this dossier found no third quantitative peak-hours survey
  broken out by finishing-time tier. Edge case: swim/bike/run hours from a wetsuit-legal
  open-water swim vs. pool session should use one hour-counting convention, or cross-plan comparison is invalid.
```

```yaml
rule: phase_shares
family: specificity
reads: "share of prep weeks (excluding taper) spent in BASE vs BUILD vs PEAK"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {phase_share_base: [0.35, 0.55], phase_share_build: [0.27, 0.45], unit: "share of BASE+BUILD+PEAK weeks", ramp: "0.20 base, 0.18 build"}
evidence_grade: C
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Horizon-aware Base/Build/Peak+Taper split, roughly 40/40/20 for short preps and 50-58/27-32/15-18 for longer ones."
    verified: isbn
  - citation: "Issurin VB (2010). New Horizons for the Methodology and Physiology of Training Periodization. Sports Med, 40(3), 189-206."
    doi: "10.2165/11319770-000000000-00000"
    states: "Proposes concentrated 2-4 week training blocks by ability rather than a smooth classical phase progression, citing differing residual-effect half-lives."
    verified: crossref
    crossref_title: "New Horizons for the Methodology and Physiology of Training Periodization"
  - citation: "Bompa TO & Buzzichelli C (2018). Periodization: Theory and Methodology of Training, 6th ed. Human Kinetics."
    isbn: "9781492544807"
    states: "General Preparation (Base-equivalent) phase should dominate early in a macrocycle before narrowing to specific/competitive phases."
    verified: isbn
disagreements: >-
  Issurin's block model does not use Base/Build/Peak language at all and argues against
  the classical smooth-transition assumption this rule encodes; it supports allocating
  a large early-phase share to aerobic work but is not a direct test of the 35-55%/
  27-45% numbers, which remain Friel-specific convention corroborated qualitatively by
  Bompa & Buzzichelli's classical model.
held_out: false   # ironman.yaml/half_ironman.yaml:phase_share_base and phase_share_build both exist
school_specific: true
notes_for_implementer: >-
  Flag school_specific: the numeric shares are one periodization school's (Friel's)
  convention; Issurin's block school and Bompa's classical school agree on the
  qualitative shape (front-load general/aerobic work) but neither independently
  validates these percentages. Edge case: a plan built as Issurin-style blocks may
  legitimately alternate Base-like and Build-like weeks rather than running one
  continuous Base phase, which this rule cannot detect from phase labels alone.
```

```yaml
rule: marathon_long_run_peak
family: specificity
reads: "hours (and, if present, distance) of the longest run labelled long"
applies_to: {goals: [running_marathon], tiers: all}
band: {low: 3.0, high: 3.5, unit: hours, ramp: 1.5}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula, 4th ed. Human Kinetics."
    isbn: "9781718203662"
    states: "Caps the long run near 2.5 h or 25-30% of weekly mileage, whichever is smaller, to limit injury/recovery cost."
    verified: isbn
  - citation: "Vickers AJ & Vertosick EA (2016). An empirical study of race times in recreational endurance runners. BMC Sports Sci Med Rehabil, 8, 51."
    doi: "10.1186/s13102-016-0052-y"
    states: "Weekly training mileage (not long-run length specifically) was the strongest training predictor of marathon finishing time."
    verified: crossref
    crossref_title: "An empirical study of race times in recreational endurance runners"
disagreements: >-
  A distance cap (32-35 km) can exceed 3 h for slower finishers, while Daniels emphasizes a duration cap (~2.5 h) that can fall short of
  32 km for faster ones — the two textbooks disagree on which variable (distance or
  time) should govern the ceiling, which the current single-hours band cannot represent.
held_out: false   # running_marathon.yaml:long_run_peak exists (measured band, narrower than literature: 3.0-3.5h)
school_specific: false
notes_for_implementer: >-
  Endury's own band (3.0-3.5 h) sits inside the
  literature's wider range; the literature alone would support a wider low bound
  (Daniels allows ~2.5 h for slower runners training a matching total-mileage cap).
  Edge case: this rule cannot distinguish a duration cap from a distance cap without
  also reading the athlete's expected pace, so a slow finisher's time-capped long run
  may read as "too short" against an hours-only band even though it is correct by
  Daniels' own rule.
```

```yaml
rule: weekly_rest_days
family: specificity
reads: "count of days per week with no scheduled session, averaged over load and recovery weeks"
applies_to: {goals: [ironman, half_ironman], tiers: all}
band: {low: 0.75, high: 2.25, unit: "empty days per load/recovery week", ramp: 1.5}
evidence_grade: D
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Recommends at least one fully unstructured day per week for recovery, more for masters or high-life-stress athletes."
    verified: isbn
disagreements: "None found; see Not found for the missing independent source."
held_out: partial   # shared.yaml:weekly_rest_day_coverage exists but covers=TRIATHLON only;
                     # running_marathon has no rule reading this fact at all (fully held out for marathon)
school_specific: true
notes_for_implementer: >-
  Single-source rule (Friel only); this dossier could not find a second independent
  coaching text or study giving a specific day-count recommendation — generic
  periodization sources describe "a rest day" per microcycle as convention without a
  number the benchmark could bound. The 0.75-2.25 band actually in shared.yaml is empirical, not from literature. Edge case: a day with
  only a strength session and no swim/bike/run would count as "empty" under a naive
  sport-only reading; the rule needs to define whether strength-only days count as rest.
```

---

## Not found

Numbers actively searched for but not sourced to an independent, verifiable citation:

- **Peak swim distance, independent of Friel/Dixon.** No peer-reviewed survey or study
  reporting a specific peak long-swim distance for Ironman or half-Ironman age-groupers
  was found. Knechtle et al. (2010) reports weekly swim training *speed*, not peak
  session distance.
- **Peak-long-session timing (weeks out), independent of Friel.** No second source with
  an explicit week-count was found for either distance.
- **Brick frequency per week by phase, as a validated number.** The phase table in
  `triathlon.md` §4 (rare in Base, moderate in Build, race-simulation in Peak) is
  Friel/Dixon coaching convention; no training-intervention study tests brick frequency
  against an outcome.
- **Race-pace rehearsal duration/share, independent source.** Only Friel and Going Long
  (same author) were found; run-walk pacing literature (Périard 2015, already in
  `triathlon_long_course.md` §10) addresses race-day execution, not training-plan
  rehearsal volume.
- **A single number for "days off per week," independent of Friel.** Generic
  periodization sources describe "a rest day" as a convention of the microcycle but do
  not give a number comparable to Friel's guidance.
- **A quantitative, independently-sourced swim/bike/run percentage split**, distinct
  from Friel and Dixon's shared VeloPress lineage. Gilinsky et al. (2014) was located as
  a plausible independent source but this dossier could not access its reported
  discipline-hour breakdown to confirm it states the same split.

## Sources register

| Source | Type | Verified |
|---|---|---|
| Friel J (2024). *The Triathlete's Training Bible*, 5th ed. VeloPress. ISBN 9781646046072 | Book | isbn |
| Friel J & Byrn G (2009). *Going Long*, 2nd ed. VeloPress. ISBN 9781934030066 | Book | isbn |
| Dixon M (2017). *Fast-Track Triathlete*. VeloPress. ISBN 9781937715748 | Book | isbn |
| Daniels J (2021). *Daniels' Running Formula*, 4th ed. Human Kinetics. ISBN 9781718203662 | Book | isbn |
| Bompa TO & Buzzichelli C (2018). *Periodization*, 6th ed. Human Kinetics. ISBN 9781492544807 | Book | isbn |
| Millet GP & Vleck VE (2000). BJSM 34(5), 384-390. DOI 10.1136/bjsm.34.5.384 | Paper | crossref |
| Vleck VE, Bentley DJ, Millet GP & Cochrane T (2010). JSCR 24(1), 30-36. DOI 10.1519/JSC.0b013e3181bd4cc8 | Paper | crossref |
| Knechtle B et al. (2010). JSCR. DOI 10.1519/JSC.0b013e3181c643b6 | Paper | crossref |
| Gilinsky N et al. (2014). J Sci Med Sport 17(3), 300-305. DOI 10.1016/j.jsams.2013.04.014 | Paper | crossref |
| Sinisgalli R et al. (2021). Physiol Behav 232, 113344. DOI 10.1016/j.physbeh.2021.113344 | Paper | crossref |
| Vickers AJ & Vertosick EA (2016). BMC Sports Sci Med Rehabil 8, 51. DOI 10.1186/s13102-016-0052-y | Paper | crossref |
| Issurin VB (2010). Sports Med 40(3), 189-206. DOI 10.2165/11319770-000000000-00000 | Paper | crossref |

**Friel edition:** the repository's research docs once dated the 5th edition 2018.
Bookseller and publisher listings show a 4th edition published November 2016 (ISBN
9781937716844) and the 5th edition published January 2024 (ISBN 9781646046072); the
citations here and in the research docs now give 2024.
