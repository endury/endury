# Rule Dossier — Session Structure and Fuelling

- **Family:** session (what a single workout should look like)
- **Date:** 2026-09-25
- **Tools available:** WebFetch and WebSearch were both available this run.
  Every DOI below was checked by fetching `https://api.crossref.org/works/<DOI>`;
  every book was checked against a publisher, bookseller, or library ISBN
  listing. None are asserted from memory alone.
- **Repo context read:** `docs/research/endurance_methodology.md` §4,
  `docs/research/triathlon.md` §3/4/7, `docs/research/triathlon_long_course.md`
  §6, `docs/specs/planner/activity_library.md`,
  `devtools/quality/constraints/shared.yaml` (158 lines, full file), and the
  `RULES` tuple in `devtools/quality/planner/score_quality.py` (~line 1250).
  No goal-specific YAML files (`devtools/quality/constraints/<goal>.yaml`)
  were read; a rule marked `held_out: true` here could in principle already
  have a goal-scoped band I didn't see — everything reachable from
  `shared.yaml` and the `RULES` registry itself was checked.

---

```yaml
rule: warmup_before_hard
family: session
reads: "for every session with >= 8 min combined in zones 4-5: minutes in zones 1-3 before the first zone-4/5 step"
applies_to: {goals: all, tiers: all}
band: {low: 10, high: 30, unit: minutes, ramp: 10}
evidence_grade: A
sources:
  - citation: "McGowan CJ, Pyne DB, Thompson KG, Rattray B (2015). Warm-up strategies for sport and exercise: mechanisms and applications. Sports Med 45(11):1523-1546."
    doi: 10.1007/s40279-015-0376-x
    states: "A general + sport-specific active warm-up (raising muscle temp, priming metabolic/neural pathways) improves subsequent high-intensity performance; reviewed protocols cluster around 10-20 min."
    verified: crossref
    crossref_title: "Warm-Up Strategies for Sport and Exercise: Mechanisms and Applications"
  - citation: "Bishop D (2003). Warm Up I: potential mechanisms and the effects of passive warm up on exercise performance. Sports Med 33(6):439-454; Warm Up II: performance changes following active warm up. Sports Med 33(7):483-498."
    doi: "10.2165/00007256-200333060-00005 (I); 10.2165/00007256-200333070-00002 (II)"
    states: "Active warm-up of low-to-moderate intensity for roughly 5-20 min, ending close to the main-set start, maximises subsequent high-intensity output without pre-fatiguing."
    verified: crossref
    crossref_title: "Warm Up I: ... / Warm Up II: ..."
  - citation: "Laursen PB, Buchheit M (2019). Science and Application of High-Intensity Interval Training. Human Kinetics."
    isbn: "9781492552123"
    states: "Coaching-convention protocols precede VO2max/threshold work with a 10-20 min progressive general warm-up plus activation strides."
    verified: isbn
disagreements: "None on the need for a warm-up before hard work; disagreement is only over exact minutes and whether the mechanism is thermal, metabolic, or neural (McGowan and Bishop both hedge on a single number)."
held_out: true
school_specific: false
notes_for_implementer: >-
  Swims: read minutes, not metres, for the warm-up gate (steady easy metres
  convert to minutes via the activity's own pace). Exclude sessions whose
  only zone-4/5 content is a strides/openers block below the rule-11
  minimum-block-length gate — those are not "hard sessions" requiring a
  dedicated warm-up beyond normal easy-session build-in. A fixed test
  protocol (type: test) should get its own, usually shorter, warm-up
  convention rather than this band.
```

```yaml
rule: cooldown_after_hard
family: session
reads: "for every session with >= 8 min combined in zones 4-5: minutes in zones 1-2 after the last zone-4/5 step"
applies_to: {goals: all, tiers: all}
band: {low: 5, high: 15, unit: minutes, ramp: 10}
evidence_grade: C
sources:
  - citation: "Van Hooren B, Peake JM (2018). Do we need a cool-down after exercise? A narrative review of the psychophysiological effects and the effects on performance, injuries and the long-term adaptive response. Sports Med 48(7):1575-1595."
    doi: 10.1007/s40279-018-0916-2
    states: "No good evidence that active cool-down speeds physiological recovery, reduces injury, or changes long-term adaptation; some support for reduced perceived soreness and psychological 'closure'."
    verified: crossref
    crossref_title: "Do We Need a Cool-Down After Exercise? A Narrative Review of the Psychophysiological Effects and the Effects on Performance, Injuries and the Long-Term Adaptive Response"
  - citation: "Daniels J (2021). Daniels' Running Formula, 4th ed. Human Kinetics."
    isbn: "9781718203662"
    states: "Prescribes an easy jog after quality sessions (roughly 5-15 min) as a practical convention, independent of a specific physiological claim."
    verified: isbn
  - citation: "Laursen PB, Buchheit M (2019). Science and Application of High-Intensity Interval Training. Human Kinetics."
    isbn: "9781492552123"
    states: "Recommends a brief active cool-down after HIIT sessions mainly for gradual HR/lactate decline and practice logistics, not for a proven adaptive benefit."
    verified: isbn
disagreements: "Van Hooren & Peake's review is the most rigorous source here and finds no clear performance/adaptation benefit — this rule is convention (grade C), not physiology (grade A), and the dossier should say so plainly rather than borrow the warm-up rule's evidence grade."
held_out: true
school_specific: false
notes_for_implementer: >-
  Do not grade this as harshly as warmup_before_hard: a missing cooldown is
  a much weaker defect given the review above. Consider a lower rule
  weight than warmup_before_hard if the benchmark composite lets rules
  carry different weights. Swims: a short easy-pace cooldown length in
  metres, converted to minutes.
```

```yaml
rule: vo2max_interval_structure
family: session
reads: "for repeat blocks whose on: step lands in zone 5: (a) off-minutes / on-minutes per rep, (b) sum of on-minutes across the session"
applies_to: {goals: all, tiers: all}
band:
  work_rest_ratio: {low: 0.4, high: 1.5, unit: "off-minutes / on-minutes", ramp: 0.5}
  zone5_minutes_per_session: {low: 12, high: 25, unit: minutes, ramp: 8}
evidence_grade: A
sources:
  - citation: "Billat LV (2001). Interval training for performance: a scientific and empirical practice. Sports Med 31(1):13-31."
    doi: 10.2165/00007256-200131010-00002
    states: "3-5 min work bouts at vVO2max with roughly equal recovery maximise time spent above 90% VO2max; total work volume of ~15-25 min per session is typical."
    verified: crossref
    crossref_title: "Interval Training for Performance: A Scientific and Empirical Practice"
  - citation: "Buchheit M, Laursen PH (2013). HIIT, solutions to the programming puzzle, Part I. Sports Med 43(5):313-338; Part II. Sports Med 43(10):927-954."
    doi: "10.1007/s40279-013-0029-x (I); 10.1007/s40279-013-0066-5 (II)"
    states: "Programming framework confirming work:rest near 1:1 for long intervals and a session work-volume envelope of roughly 12-25 min at VO2max intensity."
    verified: crossref
    crossref_title: "High-Intensity Interval Training, Solutions to the Programming Puzzle (Part I / Part II)"
  - citation: "Seiler S, Jøranson K, Olesen BV, Hetlelid KJ (2013). Adaptations to aerobic interval training: interactive effects of exercise intensity and total work duration. Scand J Med Sci Sports 23(1):74-83."
    doi: 10.1111/j.1600-0838.2011.01351.x
    states: "4x8 min at ~90% HRmax (2 min recovery, ratio ~0.25) drove larger VO2max gains than shorter-duration matched protocols — a valid long-interval variant outside a strict 1:1 ratio."
    verified: crossref
    crossref_title: "Adaptations to aerobic interval training: interactive effects of exercise intensity and total work duration"
  - citation: "Rønnestad BR, Hansen J, Vegge G, Tønnessen E, Slettaløkken G (2015). Short intervals induce superior training adaptations compared with long intervals in cyclists. Scand J Med Sci Sports 25(2):143-151."
    doi: 10.1111/sms.12165
    states: "30s-on/15s-off short-short intervals (ratio 0.5), 3 sets of 13 reps with 3 min between sets, accumulate substantial time above 90% VO2max and are well tolerated."
    verified: crossref
    crossref_title: "Short intervals induce superior training adaptations compared with long intervals in cyclists – An effort-matched approach"
disagreements: >-
  Endury's own `vo2max_session_work_rest_ratio` scorecard rule already
  bands this ratio at [0.0, 1.5] (rest:work of the worst rendered rep).
  Its floor of 0.0 is more permissive than the literature supports — even
  the short-short protocol keeps ratio >= 0.5 (30s/15s); a ratio
  approaching 0 (near-continuous effort) is drifting toward a threshold
  or tempo session, not classical VO2max work. Worth a look, not a fix
  made here. The literature also splits into two legitimate families
  (long intervals ~0.75-1.25, short-short ~0.4-0.6) rather than one band.
held_out: false
school_specific: false
notes_for_implementer: >-
  The rest:work-ratio read overlaps Endury's existing
  `vo2max_session_work_rest_ratio`; the zone5-minutes-per-session read
  does not exist anywhere in `shared.yaml` or the `RULES` registry — that
  half of this rule is genuinely new. The two reads are independent and
  can be scored as two separate benchmark rules if that's easier than one
  compound rule. Helgerud et al. 2007 (4x4 min @ 90-95% HRmax, 3 min
  recovery — DOI 10.1249/mss.0b013e3180304570, verified: crossref, title
  "Aerobic High-Intensity Intervals Improve V̇O2max More Than Moderate
  Training") is the standard-reference variant and sits inside both bands.
```

```yaml
rule: threshold_session_structure
family: session
reads: "for sessions typed tempo|threshold: (a) total minutes with IF in zone 3-4, (b) for repeat blocks whose on: step lands in zone 4, off-minutes/on-minutes"
applies_to: {goals: all, tiers: all}
band:
  threshold_minutes_per_session: {low: 20, high: 60, unit: minutes, ramp: 15}
  cruise_interval_rest_ratio: {low: 0.10, high: 0.25, unit: "off-minutes / on-minutes", ramp: 0.10}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula, 4th ed. Human Kinetics."
    isbn: "9781718203662"
    states: "Threshold (\"T-pace\") work totals 20-60 min per session, run either continuously or as cruise intervals with short recovery (roughly 1 min rest per mile of work, i.e. a small fraction of the work time)."
    verified: isbn
  - citation: "Billat LV (2001). Interval training for performance: a scientific and empirical practice. Sports Med 31(1):13-31."
    doi: 10.2165/00007256-200131010-00002
    states: "Long, lightly-broken efforts near maximal lactate steady state accumulate comparable metabolic stimulus to continuous tempo work while limiting neuromuscular cost."
    verified: crossref
    crossref_title: "Interval Training for Performance: A Scientific and Empirical Practice"
disagreements: >-
  This is a single-school (Daniels) prescription for the exact rest ratio;
  other well-known run coaches favour longer continuous tempo blocks (20-40 min unbroken) over broken
  cruise intervals, and cycling/triathlon coaching more often prescribes
  sweet-spot/threshold as continuous or 2x20 min blocks rather than
  cruise-interval style. The 20-60 min total-time band is the more
  broadly supported number; the exact cruise-interval ratio is narrower
  evidence (grade C, one primary textbook).
held_out: true
school_specific: true
notes_for_implementer: >-
  Nothing in `shared.yaml` or `RULES` reads total threshold minutes or a
  cruise-interval rest ratio — this is new territory for the scorecard.
  Bike sweet-spot/threshold rides (e.g. 2x20 min) should read as
  continuous threshold_minutes, not as a repeat block requiring the
  rest-ratio sub-band; only apply the ratio band when the session is
  explicitly broken into repeats.
```

```yaml
rule: long_session_fuelling
family: session
reads: "for sessions with minutes >= 60: whether the free-text note carries a carbohydrate/fuelling cue; for minutes >= 150 the cue should also name a rate (g/h) or product"
applies_to: {goals: all, tiers: all}
band: {low: 60, high: 90, unit: "minutes above which a fuelling cue is expected", ramp: 30}
evidence_grade: A
sources:
  - citation: "Jeukendrup AE (2014). A step towards personalized sports nutrition: carbohydrate intake during exercise. Sports Med 44(Suppl 1):S25-S33."
    doi: 10.1007/s40279-014-0148-z
    states: "~30 g/h for 1-2 h sessions, ~60 g/h for 2-3 h, up to 90 g/h beyond ~2.5-3 h using multiple-transportable-carbohydrate blends."
    verified: crossref
    crossref_title: "A Step Towards Personalized Sports Nutrition: Carbohydrate Intake During Exercise"
  - citation: "Thomas DT, Erdman KA, Burke LM (2016). Nutrition and athletic performance (ACSM/AND/DC joint position stand). Med Sci Sports Exerc 48(3):543-568."
    doi: 10.1249/MSS.0000000000000852
    states: "Endorses graded carbohydrate-intake targets scaling with exercise duration and intensity for events beyond ~1 hour."
    verified: crossref
    crossref_title: "Nutrition and Athletic Performance"
  - citation: "Burke LM, Hawley JA, Wong SHS, Jeukendrup AE (2011). Carbohydrates for training and competition. J Sports Sci 29(Suppl 1):S17-S27."
    doi: 10.1080/02640414.2011.585473
    states: "IOC-consensus-aligned graded intake targets by duration, the basis for the 30/60/90 g/h steps."
    verified: crossref
    crossref_title: "Carbohydrates for training and competition"
  - citation: "Jeukendrup AE (2017). Training the gut for athletes. Sports Med 47(Suppl 1):101-110."
    doi: 10.1007/s40279-017-0690-6
    states: "Gut tolerance of high carbohydrate intake is trainable; race-rate fuelling should be rehearsed in the long sessions that mimic race duration, not introduced on race day."
    verified: crossref
    crossref_title: "Training the Gut for Athletes"
disagreements: "None on the duration-scaling principle; sources differ only on exact g/h boundaries by a few percent."
held_out: false
school_specific: false
notes_for_implementer: >-
  Endury's own `run_long_fueling_cue_coverage` scorecard rule already
  requires a fuelling cue on every 75'+ long/brick RUN (band [1.0, 1.0]).
  This dossier rule is broader in two ways the scorecard doesn't cover:
  it starts lower (60-90 min, not fixed at 75) and it applies to bike and
  swim sessions too, which Endury's rule does not touch at all. A 60-90
  min bike or swim carrying no fuelling cue is currently unscored. Keep
  the athlete-facing note itself short (a dozen words) — "carry ~60g/h
  carbs" satisfies the rule; a paragraph does not, per the repo's own
  athlete-facing-text convention.
```

```yaml
rule: recovery_run_intensity
family: session
reads: "for sessions typed recovery: (a) total minutes, (b) share of step-minutes at zone 1"
applies_to: {goals: all, tiers: all}
band:
  duration_minutes: {low: 20, high: 45, unit: minutes, ramp: 15}
  zone1_share: {low: 0.95, high: 1.0, unit: "share of session minutes in zone 1", ramp: 0.05}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula, 4th ed. Human Kinetics."
    isbn: "9781718203662"
    states: "Recovery runs are short (roughly 20-40 min) and held at the easiest end of the easy range, well below aerobic-threshold effort, purely to promote blood flow without adding load."
    verified: isbn
  - citation: "Friel J (2024). The Triathlete's Training Bible, 5th ed. VeloPress."
    isbn: "9781646046072"
    states: "Recovery sessions (run or ride) are short, entirely in the lowest zone, and scheduled the day after or between hard sessions to aid recovery, not to add training stress."
    verified: isbn
disagreements: "None between the two textbooks on the qualitative prescription; neither gives a controlled-trial number, so this stays grade C."
held_out: true
school_specific: false
notes_for_implementer: >-
  No scorecard rule in `shared.yaml` or the `RULES` registry reads
  recovery-session duration or its zone-1 share directly. Distinguish
  from `easy_session_intensity` below: a recovery session should be
  essentially 100% zone 1 (no zone-2 float at all), while an "easy" or
  "long" session tolerates a small zone-2 fraction.
```

```yaml
rule: easy_session_intensity
family: session
reads: "for sessions typed easy|long: share of step-minutes in zones 1-2, excluding any strides/openers block that fails the rule-11 minimum-block-length gate"
applies_to: {goals: all, tiers: all}
band: {low: 0.90, high: 1.0, unit: "share of session minutes in zones 1-2", ramp: 0.10}
evidence_grade: B
sources:
  - citation: "Seiler S (2010). What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291."
    doi: 10.1123/ijspp.5.3.276
    states: "Elite endurance athletes across sports converge on ~80% low-intensity training; the discipline is per-session — easy days stay easy rather than drifting into moderate 'grey zone' effort."
    verified: crossref
    crossref_title: "What is Best Practice for Training Intensity and Duration Distribution in Endurance Athletes?"
  - citation: "Esteve-Lanao J, Foster C, Seiler S, Lucia A (2007). Impact of training intensity distribution on performance in endurance athletes. J Strength Cond Res 21(3):943-949."
    doi: 10.1519/R-19725.1
    states: "A group shifting time from zone-1 toward unstructured zone-2 (eroded easy-day discipline) underperformed a more polarised group over the same total volume."
    verified: crossref
    crossref_title: "Impact of Training Intensity Distribution on Performance in Endurance Athletes"
disagreements: >-
  The 80/20 figure is a WEEKLY/macro distribution across all sessions,
  not strictly a per-session share — applying it session-by-session to
  an individual easy/long session (as this rule does) is a reasonable but
  extrapolated reading, not a direct restatement of Seiler's finding.
  Flagging this so the benchmark doesn't overstate the evidence grade.
held_out: true
school_specific: false
notes_for_implementer: >-
  No scorecard rule reads a per-session zone-1/2 share; Endury's quality
  gate has no macro training-intensity-distribution (TID) rule in
  `shared.yaml` either, so this is new on both the per-session and macro
  axes. Must exclude short zone-5 strides blocks (rule 11) from the
  denominator or every easy run with 4x20s strides fails by construction.
```

```yaml
rule: brick_run_duration
family: session
reads: "for BRICK-family run sessions: minutes, grouped by phase (BASE, BUILD, PEAK)"
applies_to: {goals: [half_ironman, ironman], tiers: all}
band:
  base_phase_minutes: {low: 0, high: 15, unit: minutes, ramp: 10}
  peak_phase_minutes: {low: 45, high: 90, unit: minutes, ramp: 20}
evidence_grade: C
sources:
  - citation: "Friel J & Byrn G (2009). Going Long: Training for Triathlon's Ultimate Challenge, 2nd ed. VeloPress."
    isbn: "9781934030066"
    states: "Base bricks, if used at all, are short transition runs (10-15 min easy); peak bricks rehearse race-duration or race-pace segments off the bike."
    verified: isbn
  - citation: "Dixon M (2017). Fast-Track Triathlete: Balancing a Big Life with Big Performance in Long-Course Triathlon. VeloPress."
    isbn: "9781937715748"
    states: "Peak-phase key bricks extend the off-the-bike run toward 45-90 min at race effort to rehearse the specific fatigue of running under load, well beyond the 20-40 min build-phase brick run."
    verified: isbn
disagreements: "None between the two independent coaching authors on the base-short/peak-long shape; exact peak-phase minutes vary by athlete level and race distance more than the sources specify precisely."
held_out: false
school_specific: false
notes_for_implementer: >-
  Endury's own `build_brick_run_duration` scorecard rule already covers
  BUILD-phase brick run duration (band [33.0, 42.0] min, the top of the
  20-40 min literature range). This dossier rule fills the two phases
  Endury's gate does not score: BASE (should stay short or absent) and
  PEAK (should extend well past the BUILD ceiling toward race-pace
  rehearsal). Goal-scoped to half/full triathlon only, matching Endury's
  own `covers=TRIATHLON` pattern for the sibling rule.
```

```yaml
rule: long_swim_and_swim_session_structure
family: session
reads: "for swim sessions in BUILD/PEAK phase: longest unbroken zone 1-2 block (metres), and whether the session has a distinct main set (CSS-interval or broken structure) beyond warmup/cooldown"
applies_to: {goals: [half_ironman, ironman], tiers: all}
band: {low: 800, high: 1500, unit: metres (longest continuous block in a long-swim session), ramp: 400}
evidence_grade: C
sources:
  - citation: "Newsome P, Young A (2012). Swim Smooth: The Complete Coaching System for Swimmers and Triathletes. Fernhurst Books."
    isbn: "9781119963196"
    states: "Long-course swim programming includes a periodic continuous long swim to build aerobic durability, alongside CSS-paced interval main sets for threshold development."
    verified: isbn
  - citation: "Friel J (5th ed. printing, VeloPress). The Triathlete's Training Bible."
    isbn: "9781646046072"
    states: "Swim sessions for long-course athletes progress from technique/drill-heavy base work to threshold broken sets and open-water race-distance rehearsal in build/peak."
    verified: isbn
disagreements: "None between the two sources on the qualitative structure; neither gives a single numeric metres threshold for what counts as a 'long swim', so the band above is this dossier's own interpolation from race-distance benchmarks in docs/research/triathlon_long_course.md §4, not a literal number either textbook states."
held_out: true
school_specific: false
notes_for_implementer: >-
  Endury's `long_swim_peak` scorecard rule (referenced in the `RULES`
  registry) grades the TIMING of the longest swim relative to PEAK, not
  its internal structure — this rule is about what's inside that
  session (a continuous block, a real main set) rather than when it
  falls. Swims are metres, not minutes — read the step list's
  `distance_m` (or equivalent) for the continuous-block length.
```

```yaml
rule: strides_and_neuromuscular_touches
family: session
reads: "minimum total on-time (minutes, summed across all reps) of a zone-4/5 repeat block for the benchmark to classify it as hard/interval work rather than strides or race-day openers"
applies_to: {goals: all, tiers: all}
band: {low: 2.0, high: 4.0, unit: "minutes of cumulative zone 4-5 on-time in a session", ramp: 2.0}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula, 4th ed. Human Kinetics."
    isbn: "9781718203662"
    states: "Strides are brief (roughly 15-20s) near-top-speed reps with full recovery, run for coordination and economy, not prescribed as a VO2max training stimulus."
    verified: isbn
  - citation: "Laursen PB, Buchheit M (2019). Science and Application of High-Intensity Interval Training. Human Kinetics."
    isbn: "9781492552123"
    states: "Distinguishes short neuromuscular/speed reps (well under 10s, negligible metabolic loading) from genuine VO2max reps (30s and up, run at a prescribed cumulative volume)."
    verified: isbn
disagreements: "Neither source gives one crisp cutoff; the band above (2-4 cumulative minutes) is this dossier's synthesis, not a number either textbook states outright — flagging so it is not overstated as a directly-cited figure."
held_out: true
school_specific: false
notes_for_implementer: >-
  Rep length alone is not a sufficient gate: Rønnestad's short-short
  VO2max protocol (rule 3) uses 30s reps too, but repeated ~39 times
  across 3 sets for ~19.5 cumulative minutes at zone 5 — that is
  legitimate interval work, not strides, despite the short individual
  rep. Gate on cumulative on-time (this rule), not individual rep length.
  Endury's own quality gate never reads raw block length for this
  purpose — it classifies hardness from a fixed per-activity descriptor
  (`hardness.is_hard_physiologically` in the activity registry) set at
  authoring time, which an LLM-generated plan has no equivalent of. The
  benchmark must derive this gate itself from steps, since it cannot read
  Endury's internal catalog metadata for a non-Endury plan.
```

---

## Not found

- **hard_minutes_cap** — no source gives a defensible single-session upper
  bound on total zone-4+ minutes for age-group athletes, distinct from the
  per-session-type bounds already covered by `vo2max_interval_structure`
  (12-25 min at zone 5) and `threshold_session_structure` (20-60 min at
  zone 3-4). Several general HIIT/VO2max reviews mention a minimum
  cumulative time above 90% VO2max (commonly cited informally as ~10 min
  for endurance athletes) but that is a *floor* for training effect, not a
  *ceiling* for the benchmark to penalise past — the opposite of what
  `hard_minutes_cap` asks for. Not inventing a ceiling number; dropped.
- An exact numeric warm-up-duration recommendation independent of session
  type: McGowan et al. and Bishop's two-part review both describe
  mechanisms and typical protocol lengths (5-20 min) rather than
  prescribing one number; the 10-30 min band under `warmup_before_hard`
  is this dossier's synthesis across those ranges plus coaching
  convention, not a figure any single source states outright.
- A peer-reviewed, non-convention source for recovery-run duration
  specifically (as opposed to recovery-*week* volume, which
  `endurance_methodology.md` §2 already sources well) — both citations
  under `recovery_run_intensity` are coaching textbooks, grade C.

## Sources register

| Source | Type | Verified |
|---|---|---|
| McGowan et al. 2015, *Sports Med* 45(11):1523-1546 | DOI 10.1007/s40279-015-0376-x | crossref |
| Bishop 2003, *Sports Med* 33(6):439-454 (Warm Up I) | DOI 10.2165/00007256-200333060-00005 | crossref |
| Bishop 2003, *Sports Med* 33(7):483-498 (Warm Up II) | DOI 10.2165/00007256-200333070-00002 | crossref |
| Van Hooren & Peake 2018, *Sports Med* 48(7):1575-1595 | DOI 10.1007/s40279-018-0916-2 | crossref |
| Buchheit & Laursen 2013, *Sports Med* 43(5):313-338 (Part I) | DOI 10.1007/s40279-013-0029-x | crossref |
| Buchheit & Laursen 2013, *Sports Med* 43(10):927-954 (Part II) | DOI 10.1007/s40279-013-0066-5 | crossref |
| Seiler et al. 2013, *Scand J Med Sci Sports* 23(1):74-83 | DOI 10.1111/j.1600-0838.2011.01351.x | crossref (Crossref lists online-first year 2011) |
| Rønnestad et al. 2015, *Scand J Med Sci Sports* 25(2):143-151 | DOI 10.1111/sms.12165 | crossref |
| Billat 2001, *Sports Med* 31(1):13-31 | DOI 10.2165/00007256-200131010-00002 | crossref |
| Helgerud et al. 2007, *Med Sci Sports Exerc* 39(4):665-671 | DOI 10.1249/mss.0b013e3180304570 | crossref |
| Jeukendrup 2014, *Sports Med* 44(S1):S25-S33 | DOI 10.1007/s40279-014-0148-z | crossref |
| Jeukendrup 2017, *Sports Med* 47(S1):101-110 | DOI 10.1007/s40279-017-0690-6 | crossref |
| Burke, Hawley, Wong, Jeukendrup 2011, *J Sports Sci* 29(S1):S17-S27 | DOI 10.1080/02640414.2011.585473 | crossref |
| Thomas, Erdman, Burke 2016, *Med Sci Sports Exerc* 48(3):543-568 | DOI 10.1249/MSS.0000000000000852 | crossref |
| Maunder et al. 2021, *Sports Med* 51(8):1619-1628 | DOI 10.1007/s40279-021-01459-0 | crossref (re-verified; already used elsewhere in repo docs) |
| Seiler 2010, *IJSPP* 5(3):276-291 | DOI 10.1123/ijspp.5.3.276 | crossref |
| Esteve-Lanao et al. 2007, *J Strength Cond Res* 21(3):943-949 | DOI 10.1519/R-19725.1 | crossref |
| Daniels J, *Daniels' Running Formula*, 4th ed., Human Kinetics 2021 | ISBN 9781718203662 | isbn |
| Laursen PB & Buchheit M, *Science and Application of HIIT*, Human Kinetics 2019 | ISBN 9781492552123 | isbn |
| Newsome P & Young A, *Swim Smooth*, Fernhurst Books 2012 | ISBN 9781119963196 | isbn |
| Friel J, *The Triathlete's Training Bible*, 5th ed., VeloPress | ISBN 9781646046072 | isbn (bookseller listing shows a 2024 print date) |
| Friel J & Byrn G, *Going Long*, 2nd ed., VeloPress 2009 | ISBN 9781934030066 | isbn |
| Dixon M, *Fast-Track Triathlete*, VeloPress 2017 | ISBN 9781937715748 | isbn |
