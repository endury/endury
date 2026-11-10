# Rule Dossier — Progression, Recovery and Training Load

- **Family:** load (progression, recovery, training-load rules for the Endury plan-quality benchmark)
- **Date:** 2026-09-25
- **Tools available:** WebFetch and WebSearch were both available and used. Every DOI below was
  fetched from `https://api.crossref.org/works/<DOI>` directly; every book was located by ISBN on
  a publisher, bookseller or library page via WebSearch. No source is cited without one of the two.
- **Scope note:** book verification confirms the edition *exists* (ISBN resolves to that title/
  edition/publisher); it does not confirm I re-read the exact page stating a given number. Where a
  number rests only on that weaker form of verification it is flagged in `notes_for_implementer`.

Existing Endury quality-gate rules consulted (from `RULES` in `score_quality.py` and
`devtools/quality/constraints/{shared,ironman,half_ironman,running_marathon}.yaml`):
`sport_mix_*`, `phase_share_base/build`, `peak_weekly_hours`, `race_day_freshness_ratio`,
`recovery_cadence`, `recovery_week_depth`, `recovery_week_test_frequency`, `taper_volume_drop`,
`race_week_volume`, `taper_session_frequency`, `taper_quality_share`, `long_bike_peak`,
`long_run_peak`, `long_swim_peak`, `race_pace_long_bike_peak`, `ctl_min_tsb`, `vo2max_placement`,
`vo2max_peak_taper`, `peak_long_session_timing`, `longest_long_session_in_peak`,
`build_brick_run_duration`, `race_pace_ride_ends_at_effort`, `run_long_fueling_cue_coverage`,
`vo2max_session_work_rest_ratio`, `brick_pairs_with_key_ride`, `weekly_rest_day_coverage`,
`cross_sport_hard_day_stacking`. Rules below are `held_out: false` only when one of these already
reads the same underlying property.

---

```yaml
rule: weekly_volume_ramp
family: load
reads: "per load-mode week (excludes recovery/taper/race): total minutes (or load); week-over-week ratio for consecutive load weeks"
applies_to: {goals: all, tiers: all}
band: {low: -1.0, high: 0.30, unit: fractional week-over-week change, ramp: 0.20}
evidence_grade: C
sources:
  - citation: "Buist I, Bredeweg SW, van Mechelen W, Lemmink KAPM, Pepping GJ, Diercks RL (2008). No Effect of a Graded Training Program on the Number of Running-Related Injuries in Novice Runners: A Randomized Controlled Trial. Am J Sports Med 36(1):33-39."
    doi: 10.1177/0363546507307505
    states: "RCT: a 13-week 'graded' (~10%-rule) program showed no injury-rate reduction vs. an 8-week standard program."
    verified: crossref
    crossref_title: "No Effect of a Graded Training Program on the Number of Running-Related Injuries in Novice Runners"
  - citation: "Nielsen RO, Parner ET, Nohr EA, Sorensen H, Lind M, Rasmussen S (2014). Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries. J Orthop Sports Phys Ther 44(10):739-747."
    doi: 10.2519/jospt.2014.5164
    states: "Cohort: >30% increase in weekly distance over 2 weeks raised risk for some (not all) injury types; risk did not rise linearly below that."
    verified: crossref
    crossref_title: "Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries: An Association Which Varies According to Type of Injury"
  - citation: "Damsted C, Glad S, Nielsen RO, Sorensen H, Malisoux L (2018). Is There Evidence for an Association Between Changes in Training Load and Running-Related Injuries? A Systematic Review. Int J Sports Phys Ther 13(6):931-942."
    doi: 10.26603/ijspt20180931
    states: "Systematic review: 'compiled evidence for an association between change in training load and running-related injury does not exist.'"
    verified: crossref
    crossref_title: "IS THERE EVIDENCE FOR AN ASSOCIATION BETWEEN CHANGES IN TRAINING LOAD AND RUNNING-RELATED INJURIES? A SYSTEMATIC REVIEW"
  - citation: "Ramskov D, Rasmussen S, Sorensen H, Parner ET, Lind M, Nielsen RO (2018). Progression in Running Intensity or Volume and Specific Injuries: Run Clever, a Randomized Trial. J Orthop Sports Phys Ther 48(10):740-748."
    doi: 10.2519/jospt.2018.8062
    states: "RCT testing intensity- vs. volume-based progression rules found neither prevented running injuries better than the other."
    verified: crossref
    crossref_title: "Progression in Running Intensity or Running Volume and the Development of Specific Injuries in Recreational Runners: Run Clever, a Randomized Trial Using Competing Risks"
disagreements: "The '10% rule' has no RCT support (Buist, Ramskov both null); Nielsen's >30%/2-week finding is the strongest single injury signal but is observational, injury-type-specific, and Damsted's systematic review concludes the pooled literature shows no clear load-change-to-injury association at all."
held_out: true
school_specific: false
notes_for_implementer: "Treat as a soft flag, not a hard defect: band is deliberately wide (ramp 0.20) and the floor is left open (any decrease is fine). Compare load-mode weeks only — recovery-week drops and post-recovery rebounds are not ramp violations. Nielsen's number was measured over a 2-week window on novice runners; applying it week-to-week on trained triathletes/marathoners is an extrapolation, not a direct read of the study."
```

```yaml
rule: acute_chronic_ratio
family: load
reads: "per day: load (derivable from steps). 7-day rolling sum / 28-day rolling mean-of-7-day-sums (uncoupled EWMA form preferred over the coupled rolling-average form); evaluated once >=28 days of plan+recent history exist"
applies_to: {goals: all, tiers: all}
band: {low: 0.8, high: 1.5, unit: ratio, ramp: 0.3}
evidence_grade: C
sources:
  - citation: "Gabbett TJ (2016). The Training-Injury Prevention Paradox: Should Athletes Be Training Smarter and Harder? Br J Sports Med 50(5):273-280."
    doi: 10.1136/bjsports-2015-095788
    states: "Proposed 0.8-1.3 as a 'sweet spot' acute:chronic workload ratio and >1.5 as a high-injury-risk zone, from team-sport (mostly cricket/football) cohorts."
    verified: crossref
    crossref_title: "The training-injury prevention paradox: should athletes be training smarter and harder?"
  - citation: "Impellizzeri FM, Woodcock S, Coutts AJ, Fanchini M, McCall A, Vigotsky AD (2020). Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls. Int J Sports Physiol Perform 15(6):907-913."
    doi: 10.1123/ijspp.2019-0864
    states: "ACWR mathematically couples numerator and denominator, is not a validated injury-risk model, and confuses correlation-of-convenience with causal load-injury inference."
    verified: crossref
    crossref_title: "Acute:Chronic Workload Ratio: Conceptual Issues and Fundamental Pitfalls"
  - citation: "Wang C, Vargas JT, Stokes T, Steele R, Shrier I (2020). Analyzing Activity and Injury: Lessons Learned from the Acute:Chronic Workload Ratio. Sports Med 50(7):1243-1254."
    doi: 10.1007/s40279-020-01280-1
    states: "Simulation study: ACWR-injury associations reported in the literature can arise from statistical artefacts (reverse causation, collider bias) rather than a true dose-risk relationship."
    verified: crossref
    crossref_title: "Analyzing Activity and Injury: Lessons Learned from the Acute:Chronic Workload Ratio"
disagreements: "Gabbett's band is widely reproduced in practice but Impellizzeri and Wang independently show the metric's own construction can manufacture the U-shaped risk curve it is credited with finding; neither critique is endurance-sport-specific evidence against ACWR, both are methodological critiques of the ratio itself."
held_out: true
school_specific: true
notes_for_implementer: "Compute and report it (it is cheap and athletes/coaches recognize it), but do not weight it like a validated rule: use a low weight and wide ramp, and label it in any output as descriptive/contested rather than a defect. Gabbett's cohorts were team-sport in-season load, not endurance base-building — treat the numeric band as an analogy, not a calibrated endurance threshold."
```

```yaml
rule: long_run_share
family: load
reads: "per load-mode week with a running LONG-type session: that session's minutes / total running minutes that week; also flag if minutes > 150 regardless of share"
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: all}
band: {low: 0.20, high: 0.30, unit: share of weekly running minutes, ramp: 0.10}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula (4th ed). Human Kinetics. ISBN 9781718203662."
    doi: null
    states: "Long run generally capped near 25% of weekly mileage, and at roughly 150 minutes regardless of total volume, to bound single-session damage."
    verified: isbn
    crossref_title: null
disagreements: "Daniels scales the long run as a percentage of weekly volume; other marathon schools prefer a lower, near-fixed absolute cap — a genuine school split, not a rounding difference."
held_out: true
school_specific: true
notes_for_implementer: "No rule in RULES reads long-run share today (long_run_peak checks absolute peak duration, not share of weekly volume). Score only load-mode weeks with meaningful weekly running volume (guard the denominator against near-zero taper/recovery weeks). Content of the percentage claims is sourced from established secondary consensus around these texts, not from re-reading the primary page — verification here is bibliographic (ISBN), not textual."
```

```yaml
rule: monotony
family: load
reads: "per week: mean of the 7 daily loads / their population standard deviation (rest days = 0 load, included in both moments); report the max over load weeks"
applies_to: {goals: all, tiers: all}
band: {low: 0.0, high: 2.0, unit: ratio, ramp: 1.0}
evidence_grade: B
sources:
  - citation: "Foster C (1998). Monitoring Training in Athletes with Reference to Overtraining Syndrome. Med Sci Sports Exerc 30(7):1164-1168."
    doi: 10.1097/00005768-199807000-00023
    states: "In a season-long case series of US speedskaters, weeks with monotony persistently above ~2.0 preceded illness/injury/performance-decrement episodes."
    verified: crossref
    crossref_title: "Monitoring training in athletes with reference to overtraining syndrome"
  - citation: "Foster C, Florhaug JA, Franklin J, Gottschall L, Hrovatin LA, Parker S, Doleshal P, Dodge C (2001). A New Approach to Monitoring Exercise Training. J Strength Cond Res 15(1):109-115."
    doi: 10.1519/00124278-200102000-00019
    states: "Validates session-RPE x duration as a practical daily-load unit, the input the monotony/strain calculation needs to be computed day by day."
    verified: crossref
    crossref_title: "A New Approach to Monitoring Exercise Training"
disagreements: "None found contesting the >2.0 threshold itself; the main caveat in later literature is that Foster's original evidence is a single-team case series (n small, uncontrolled), so the number is a useful heuristic, not a population-calibrated cutoff."
held_out: true
school_specific: false
notes_for_implementer: "Rest days must count as zero, not be excluded, or monotony is inflated by shrinking the sample rather than reflecting real day-to-day sameness. Only meaningful with >=4-5 training days in the week; a 2-day recovery week can produce a degenerate ratio and should probably be excluded from the max-over-weeks scan the way the taper/race weeks already are for other rules."
```

```yaml
rule: strain
family: load
reads: "per week: sum of the 7 daily loads x that week's monotony (Foster's product form); report as a ratio to the athlete's own trailing 6-week median strain, not as an absolute number"
applies_to: {goals: all, tiers: all}
band: {low: 0.0, high: 1.6, unit: ratio to trailing-median own-baseline strain, ramp: 0.6}
evidence_grade: D
sources:
  - citation: "Foster C (1998). Monitoring Training in Athletes with Reference to Overtraining Syndrome. Med Sci Sports Exerc 30(7):1164-1168."
    doi: 10.1097/00005768-199807000-00023
    states: "Weeks with a strain spike disproportionate to the athlete's own recent history preceded overtraining markers more than absolute load did."
    verified: crossref
    crossref_title: "Monitoring training in athletes with reference to overtraining syndrome"
disagreements: "None found; the gap is not disagreement but non-transferability — Foster's absolute strain numbers (illness cluster above roughly 6000 AU) are calibrated to his own session-RPE load unit, not to Endury's zone-midpoint-squared load formula, so importing his cutoff literally is not defensible."
held_out: true
school_specific: false
notes_for_implementer: "Do not port Foster's absolute AU threshold — Endury's load formula (minutes x IF^2 x 100/60) is on a different scale. Score strain relative to the athlete's own trailing distribution instead (self-referential z-score or ratio-to-median), which preserves Foster's actual finding (a strain spike relative to one's own pattern) without a false-precision absolute cutoff. This is an implementer judgment call, not a literature-sourced band — grade D."
```

```yaml
rule: recovery_week_cadence
family: load
reads: "count of recovery-mode weeks / count of (load-mode + recovery-mode) weeks, per mesocycle or over the whole plan"
applies_to: {goals: all, tiers: [basic, moderate, competitive]}
band: {low: {basic: 0.27, moderate: 0.20, competitive: 0.20}, high: {basic: 0.36, moderate: 0.30, competitive: 0.30}, unit: recovery weeks / (load + recovery) weeks, ramp: 0.10}
evidence_grade: C
sources:
  - citation: "Issurin VB (2010). New Horizons for the Methodology and Physiology of Training Periodization. Sports Med 40(3):189-206."
    doi: 10.2165/11319770-000000000-00000
    states: "Concentrated training blocks run 2-4 weeks before a deload/transition, framing the 2:1/3:1 cadence in residual-training-effect terms."
    verified: crossref
    crossref_title: "New Horizons for the Methodology and Physiology of Training Periodization"
  - citation: "Bompa TO, Buzzichelli C (2018). Periodization: Theory and Methodology of Training (6th ed). Human Kinetics. ISBN 9781492544807."
    doi: null
    states: "Prescribes 3:1 loading:unloading mesocycles as standard, 2:1 for athletes with reduced recovery capacity (masters, high life stress)."
    verified: isbn
    crossref_title: null
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed). VeloPress. ISBN 9781646046072."
    doi: null
    states: "Same 3:1/2:1 cadence recommendation, tied explicitly to athlete recovery capacity rather than a fixed rule for everyone."
    verified: isbn
    crossref_title: null
disagreements: "None of consequence; Matveyev's original linear model is the historical root of the 3:1 convention but I could not locate a citable, verifiable primary edition of Matveyev's own work within budget (see Sources register) — the lineage claim rests on Bompa/Issurin's secondary attribution, not a direct read."
held_out: false
school_specific: true
notes_for_implementer: "Endury's existing recovery_cadence band (shared.yaml: basic 0.27-0.36, moderate/competitive 0.20-0.30) already matches this literature almost exactly (3:1=0.25, 2:1=0.33); this entry mainly confirms the existing band rather than proposing a new one. No RCT anywhere in this family — grade C is a convention shared by 3+ independent coaching authors, not validated evidence."
```

```yaml
rule: recovery_week_depth
family: load
reads: "recovery-mode week total hours / immediately preceding load-mode week total hours"
applies_to: {goals: all, tiers: all}
band: {low: 0.50, high: 0.70, unit: recovery-week hours / preceding load-week hours, ramp: 0.20}
evidence_grade: C
sources:
  - citation: "Issurin VB (2010). New Horizons for the Methodology and Physiology of Training Periodization. Sports Med 40(3):189-206."
    doi: 10.2165/11319770-000000000-00000
    states: "Anaerobic/speed residual effects decay in ~5-8 days, so a deload should cut volume enough to dissipate fatigue while keeping some quality touches."
    verified: crossref
    crossref_title: "New Horizons for the Methodology and Physiology of Training Periodization"
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed). VeloPress. ISBN 9781646046072."
    doi: null
    states: "Recovery weeks cut volume to roughly 50-70% of the preceding load week while frequency is preserved."
    verified: isbn
    crossref_title: null
  - citation: "Bompa TO, Buzzichelli C (2018). Periodization: Theory and Methodology of Training (6th ed). Human Kinetics. ISBN 9781492544807."
    doi: null
    states: "Unloading microcycles reduce volume substantially (consistent with the 50-70% range) while maintaining movement frequency for coordination."
    verified: isbn
    crossref_title: null
disagreements: "None found; all three sources converge on the same 50-70% range, which is exactly what shared.yaml's existing recovery_week_depth band already encodes (0.55-0.70, a tightened subset)."
held_out: false
school_specific: true
notes_for_implementer: "This is purely a confirmation of Endury's existing band; no evidence found to widen or narrow it further. Still coaching convention (grade C), never an RCT — flag it as such rather than presenting it to benchmark users as settled physiology."
```

```yaml
rule: first_week_fit
family: load
reads: "plan week 1 total hours (or load) / athlete's known trailing recent weekly hours (per-sport, summed)"
applies_to: {goals: all, tiers: all}
band: {low: 0.7, high: 1.15, unit: ratio to recent trailing weekly hours, ramp: 0.3}
evidence_grade: D
sources:
  - citation: "Nielsen RO, Parner ET, Nohr EA, Sorensen H, Lind M, Rasmussen S (2014). Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries. J Orthop Sports Phys Ther 44(10):739-747."
    doi: 10.2519/jospt.2014.5164
    states: "The injury signal in this cohort was strongest for runners whose distance jumped sharply relative to their own recent baseline, not for any absolute volume."
    verified: crossref
    crossref_title: "Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries: An Association Which Varies According to Type of Injury"
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed). VeloPress. ISBN 9781646046072."
    doi: null
    states: "A new training block should start at or just below the athlete's current fitness/volume, not below it enough to waste weeks, nor above it."
    verified: isbn
    crossref_title: null
disagreements: "No source gives 'week 1 of a new plan' its own numeric target distinct from the general ramp-rate literature in weekly_volume_ramp above; this rule is an inference at the plan's start boundary, not a separately-studied question."
held_out: true
school_specific: false
notes_for_implementer: "This is the one place 'the athlete's recent weekly hours per sport' (given as known benchmark input) is load-bearing: without it the rule cannot be computed at all, since a plan's week 1 has no preceding plan week to ramp from. Treat a low ratio (conservative start) as harmless; only the high side is evidence-backed."
```

```yaml
rule: peak_volume_by_level
family: load
reads: "hours of the plan's single highest-hours load-mode week (race week excluded), tiered by declared athlete level"
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: [basic, moderate, competitive]}
band: {low: {basic: 8.0, moderate: 11.0, competitive: 14.0}, high: {basic: 14.0, moderate: 16.0, competitive: 18.0}, unit: hours, ramp: 2.0}
evidence_grade: C
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed). VeloPress. ISBN 9781646046072."
    doi: null
    states: "Peak weekly hours scale with athlete level: recreational lowest, competitive age-group highest, for both half- and full-distance racing."
    verified: isbn
    crossref_title: null
  - citation: "Dixon M (2017). Fast-Track Triathlete. VeloPress. ISBN 9781937715748."
    doi: null
    states: "Time-efficient Ironman preparation is workable at roughly 10-14 h/week peak for busy age-groupers, at the low end of the recreational tier."
    verified: isbn
    crossref_title: null
disagreements: "None found among the coaching texts on direction (more competitive = more peak hours); the exact tier boundaries differ slightly between authors, which is why Endury's own band is a synthesis rather than one book's number."
held_out: false
school_specific: false
notes_for_implementer: "This matches Endury's existing peak_weekly_hours exactly (same tiers, same hour bands from tlc doc §4/§8) — no new number to add. No checkable (DOI- or ISBN-bearing) observational study of actual age-group Ironman training hours was located; the Gulbin & Gaffney (1999) survey often cited for this is real (PubMed-indexed, J Sports Med Phys Fitness 39:12-15) but carries no verifiable DOI, so it is excluded — see Not found."
```

```yaml
rule: peak_plateau
family: load
reads: "count of consecutive load-mode weeks at or above 95% of the plan's peak-week hours, immediately before the first peak/taper-mode week"
applies_to: {goals: all, tiers: all}
band: {low: 1.0, high: 4.0, unit: weeks, ramp: 2.0}
evidence_grade: D
sources:
  - citation: "Issurin VB (2010). New Horizons for the Methodology and Physiology of Training Periodization. Sports Med 40(3):189-206."
    doi: 10.2165/11319770-000000000-00000
    states: "Concentrated training blocks run 2-4 weeks, long enough to realise a targeted adaptation before the next block shifts emphasis."
    verified: crossref
    crossref_title: "New Horizons for the Methodology and Physiology of Training Periodization"
disagreements: "No source studies 'plateau length before peak' as its own question; the 2-4 week band here is an analogy borrowed from block length for a different purpose (holding volume steady, not concentrating a new ability), so treat it as speculative."
held_out: true
school_specific: true
notes_for_implementer: "Weakest-evidenced rule in this dossier — grade D and an explicit analogy, not a measurement. Consider leaving it out of a scored composite and reporting it descriptively only until better evidence surfaces; block periodization is itself one school (Issurin) against the classical linear model (Matveyev/Bompa), which does not require a plateau at all."
```

```yaml
rule: tsb_floor
family: load
reads: "Banister CTL(42d)/ATL(7d) EMA trajectory from daily loads (same formula as ctl_min_tsb); TSB = CTL - ATL; report the minimum daily TSB over the whole plan"
applies_to: {goals: all, tiers: all}
band: {low: -30.0, high: .inf, unit: lowest daily TSB, ramp: 20.0}
evidence_grade: B
sources:
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed). VeloPress. ISBN 9781937715939."
    doi: null
    states: "Popularised -30 TSB as the conventional floor of productive overload before a training-camp-style block should end."
    verified: isbn
    crossref_title: null
  - citation: "Meeusen R, Duclos M, Foster C, Fry A, Gleeson M, Nieman D, Raglin J, Rietjens G, Steinacker J, Urhausen A (2013). Prevention, Diagnosis and Treatment of the Overtraining Syndrome. Eur J Sport Sci 13(1):1-24."
    doi: 10.1080/17461391.2012.730061
    states: "Sustained, unrecovered negative training-stress balance is the physiological substrate the FOR/NFOR/OTS continuum describes, though this consensus statement gives no PMC-specific numeric cutoff."
    verified: crossref
    crossref_title: "Prevention, diagnosis and treatment of the overtraining syndrome: Joint consensus statement of the European College of Sport Science (ECSS) and the American College of Sports Medicine (ACSM)"
disagreements: "The -30 number itself is Coggan/Allen convention (grade D on its own); Meeusen 2013 supports the qualitative mechanism at a stronger grade (consensus synthesis of controlled and cohort literature) but never endorses -30 specifically, since TSB/PMC is a commercial-tool operationalization the consensus statement does not reference."
held_out: false
school_specific: true
notes_for_implementer: "Matches Endury's existing ctl_min_tsb band exactly (shared.yaml low=-30.0, ramp=20.0); nothing to change. Report the two evidence layers separately if the benchmark ever needs to explain itself: the floor value is a single tool-vendor's heuristic (D), the underlying danger of sustained deep negative balance is consensus-backed (closer to A/B)."
```

---

## Not found

Numbers I looked for but could not source, so they are omitted rather than invented:

- **long_session_step** (week-over-week increase cap for the single longest run/ride, distinct
  from overall weekly-volume ramp). No study isolates this from the general ramp-rate literature
  in `weekly_volume_ramp`. Coaching lore ("add no more than ~1-2 miles per week to the long run")
  circulates widely but I could not trace it to a specific, checkable page in a verified source
  within budget, and the instructions say not to invent a number, so it is dropped rather than
  guessed.
- **A validated week-count for `peak_plateau`.** The 1-4 week band given is Issurin's block length
  repurposed by analogy (see that rule's caveats), not a direct measurement of plateau duration.
- **A peer-reviewed, checkable observational study of actual age-group Ironman training volumes.**
  Gulbin & Gaffney (1999, *J Sports Med Phys Fitness* 39:12-15, PubMed 10230162) is the classic
  citation for this and appears to be a real, PubMed-indexed paper, but Crossref has no DOI record
  for it and it predates most DOI backfilling; it could not be verified by DOI, so
  it is excluded from `peak_volume_by_level`'s sources rather than cited unverified.
- **An endurance-sport-specific ACWR injury threshold.** Every source found validating or
  critiquing ACWR numeric bands works from team-sport (cricket, football, rugby) cohorts; no
  endurance-specific (running/triathlon/cycling) cohort study was found recalibrating the 0.8-1.3/1.5
  band, which is itself contested (see `acute_chronic_ratio`).
- **A primary, directly-checkable edition of Matveyev's original periodization texts.** The 3:1
  cadence's historical root is universally attributed to Matveyev by every secondary source, but I
  could not locate a citable edition/ISBN within budget; the claim is carried only by Bompa and
  Issurin's secondary attribution.

## Sources register

| Source | Type | Verification |
|---|---|---|
| Buist et al. 2008, *Am J Sports Med* 36(1):33-39, DOI 10.1177/0363546507307505 | RCT | crossref |
| Nielsen et al. 2014, *JOSPT* 44(10):739-747, DOI 10.2519/jospt.2014.5164 | cohort | crossref |
| Damsted et al. 2018, *Int J Sports Phys Ther* 13(6):931-942, DOI 10.26603/ijspt20180931 | systematic review | crossref |
| Ramskov et al. 2018, *JOSPT* 48(10):740-748, DOI 10.2519/jospt.2018.8062 | RCT | crossref |
| Gabbett 2016, *Br J Sports Med* 50(5):273-280, DOI 10.1136/bjsports-2015-095788 | narrative review | crossref |
| Impellizzeri et al. 2020, *IJSPP* 15(6):907-913, DOI 10.1123/ijspp.2019-0864 | methodological critique | crossref |
| Wang et al. 2020, *Sports Med* 50(7):1243-1254, DOI 10.1007/s40279-020-01280-1 | simulation/methods paper | crossref |
| Foster 1998, *MSSE* 30(7):1164-1168, DOI 10.1097/00005768-199807000-00023 | case series | crossref |
| Foster et al. 2001, *JSCR* 15(1):109-115, DOI 10.1519/00124278-200102000-00019 | validation study | crossref |
| Issurin 2010, *Sports Med* 40(3):189-206, DOI 10.2165/11319770-000000000-00000 | narrative review | crossref |
| Meeusen et al. 2013, *Eur J Sport Sci* 13(1):1-24, DOI 10.1080/17461391.2012.730061 | consensus statement | crossref |
| Daniels, *Daniels' Running Formula* (4th ed, 2021), ISBN 9781718203662 | coaching text | isbn |
| Allen, Coggan & McGregor, *Training and Racing with a Power Meter* (3rd ed, 2019), ISBN 9781937715939 | coaching text | isbn |
| Bompa & Buzzichelli, *Periodization* (6th ed, 2018), ISBN 9781492544807 | coaching text | isbn |
| Friel, *The Triathlete's Training Bible* (5th ed), ISBN 9781646046072 | coaching text | isbn |
| Dixon, *Fast-Track Triathlete* (2017), ISBN 9781937715748 | coaching text | isbn |
| Matveyev (periodization lineage, secondary attribution only) | historical attribution | no |
| Gulbin & Gaffney 1999, *J Sports Med Phys Fitness* 39:12-15, PubMed 10230162, no DOI found | observational survey | no |
