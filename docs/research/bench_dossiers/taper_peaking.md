# Rule Dossier — Taper and Peaking

- **family:** taper
- **date:** 2026-09-24
- **tools available:** WebFetch and WebSearch both worked (Crossref API reachable; two WebFetch calls to `api.crossref.org` timed out transiently and succeeded on retry — noted per source below).
- **minutes spent:** ~30 (research + verification + write-up)

All DOIs below were fetched live from `https://api.crossref.org/works/<DOI>` and the
returned title/year is recorded next to each. The one book source was checked
against its publisher/retailer ISBN listing. Rules are ordered to match the
eight numbered questions in the task (7 is split into a ratio form and a raw
form, since Endury implements only the ratio).

---

```yaml
rule: taper_duration
family: taper
reads: "days from the start of the first taper-or-later-mode week to race day (equivalently: 7 x count of consecutive taper/race-mode weeks immediately preceding the race)"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 8, high: 21, unit: days, ramp: 7}
evidence_grade: B
sources:
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Reviews taper studies converging on ~2-week (8-14 day) tapers as typical/optimal for most events."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
  - citation: "Mujika I (2009). Tapering and Peaking for Optimal Performance. Human Kinetics."
    isbn: "978-0-7360-7484-1"
    states: "Synthesises the 8-14 day convention and extends it to up to 3 weeks (21 days) for Ironman-distance events."
    verified: isbn
  - citation: "Wang Z, Wang Y, Gao W, Zhong Y (2023). Effects of tapering on performance in endurance athletes: a systematic review and meta-analysis. PLOS ONE 18(5):e0282838."
    doi: 10.1371/journal.pone.0282838
    states: "2023 update pools taper studies; duration is a moderator but no single day-count is shown superior within the 4-28 day range studied."
    verified: crossref
    crossref_title: "Effects of tapering on performance in endurance athletes: A systematic review and meta-analysis"
disagreements: >-
  Bosquet et al. (2007) and the 2023 update treat duration as a continuous
  moderator with a broad effective window (roughly 4-28 days), not a fixed
  best number; the 8-14-day (up to 21) figures are coaching-review synthesis
  (Mujika, grade B/C) layered on top of that window, not a meta-analytic
  optimum in days.
held_out: true
school_specific: false
notes_for_implementer: >-
  No rule reads taper span in days/weeks directly today — only the mean
  volume ratio across weeks already labelled taper (taper_volume_drop
  below). Per-goal windows if the benchmark wants tighter bands than the
  8-21 day union: marathon 8-14d (em Sec.6), half-Ironman 10-14d, Ironman
  14-21d (tlc Sec.7). Undefined if the plan has zero taper-labelled weeks —
  treat as a hard defect, not "not applicable."
```

```yaml
rule: taper_volume_drop
family: taper
reads: "mean taper-week training minutes (or hours) / peak load-week minutes"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.50, high: 0.70, unit: ratio, ramp: 0.40}
evidence_grade: A
sources:
  - citation: "Bosquet L, Montpetit J, Arvisais D, Mujika I (2007). Effects of Tapering on Performance: A Meta-Analysis. Med Sci Sports Exerc 39(8):1358-1365."
    doi: 10.1249/mss.0b013e31806010e0
    states: "Meta-analytic optimum is a 41-60% volume reduction (i.e., retaining ~40-59% of peak), the single most robust taper finding in the literature."
    verified: crossref
    crossref_title: "Effects of Tapering on Performance: A Meta-Analysis"
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Independently converges on 40-60% volume reduction across the tapering studies it reviews."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
disagreements: >-
  Endury's own implemented band (0.50-0.70 retained, i.e. a 30-50% cut) is
  shallower than Bosquet's meta-analytic optimum (retain 0.40-0.59, i.e. a
  41-60% cut) — the bands do not overlap in their lower half. This is intentional (docs/research/triathlon_long_course.md Sec.7): the implemented rule is calibrated to real practice, not to the meta-analytic optimum — worth stating
  explicitly since a benchmark that scores strictly against Bosquet's 41-60%
  window would flag plans Endury's own gate accepts.
held_out: false
school_specific: false
notes_for_implementer: >-
  Matches devtools/quality/constraints/shared.yaml `taper_volume_drop` (also
  overridden per-goal in ironman.yaml 0.50-0.70, half_ironman.yaml
  0.55-0.94, running_marathon.yaml 0.58-0.64 — all three sit shallower than
  Bosquet's 0.40-0.59 optimum). If the benchmark wants a literature-pure
  band rather than a practice-based band, 0.40-0.60 with a narrower ramp
  is the meta-analytic figure to use instead. Needs at least one taper week
  and one distinguishable peak load week; undefined (skip, don't score 0) if
  the plan has neither.
```

```yaml
rule: taper_frequency_kept
family: taper
reads: "mean taper-week session count / peak-week session count"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.85, high: 1.15, unit: ratio, ramp: 0.30}
evidence_grade: A
sources:
  - citation: "Bosquet L, Montpetit J, Arvisais D, Mujika I (2007). Effects of Tapering on Performance: A Meta-Analysis. Med Sci Sports Exerc 39(8):1358-1365."
    doi: 10.1249/mss.0b013e31806010e0
    states: "Maintained training frequency carried its own significant effect size (0.35 +/- 0.17, p<0.001) independent of the volume cut."
    verified: crossref
    crossref_title: "Effects of Tapering on Performance: A Meta-Analysis"
disagreements: "None found; frequency-maintenance is the least contested taper finding across sources reviewed."
held_out: false
school_specific: false
notes_for_implementer: >-
  Matches devtools/quality/constraints/shared.yaml `taper_session_frequency`
  exactly (0.85-1.15, ramp 0.30), and its own docstring/comment already cites
  Bosquet with the same effect size. No change recommended; this is the
  strongest-evidenced rule in the family (grade A, single-effect isolated by
  a meta-analysis, and the constraint band already matches the measured
  full-distance envelope of 0.89-1.10 per tlc Sec.7).
```

```yaml
rule: taper_intensity_kept
family: taper
reads: "mean zone-4-plus minutes per taper week / zone-4-plus minutes in the peak load week"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.50, high: 1.20, unit: ratio, ramp: 0.40}
evidence_grade: A
sources:
  - citation: "Bosquet L, Montpetit J, Arvisais D, Mujika I (2007). Effects of Tapering on Performance: A Meta-Analysis. Med Sci Sports Exerc 39(8):1358-1365."
    doi: 10.1249/mss.0b013e31806010e0
    states: "Maintained intensity carried a significant effect size (0.33 +/- 0.14, p<0.001) on its own, independent of the volume cut."
    verified: crossref
    crossref_title: "Effects of Tapering on Performance: A Meta-Analysis"
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Reduce volume, not intensity — dropping intensity accelerates detraining of neuromuscular and metabolic adaptations."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
disagreements: >-
  None on the direction (maintain intensity); disagreement is only in how to
  operationalise "intensity kept" from step data — see notes below.
held_out: false
school_specific: false
notes_for_implementer: >-
  Endury's implemented rule (`taper_quality_share` in shared.yaml,
  0.50-1.00, ramp 0.50; overridden per-goal in ironman.yaml, half_ironman.yaml,
  running_marathon.yaml) does not read "zone-4-plus minutes" literally — it
  reads minutes at IF>=0.85 of FTP/pace, which is the top of Zone 3
  (tempo, 76-90%) through Zone 5. That is a broader cut than a strict
  zone-4-plus (>=91%) definition and will count long tempo/sweet-spot work
  retained in taper that a stricter zone-4+ read would exclude, so a
  zone-4-plus-only version of this rule would likely read systematically
  lower ratios than taper_quality_share does on the same plans. If the
  benchmark's step data lets it choose either cut, note which one it used —
  the two are not interchangeable at the band edges. Also: this rule and
  taper_session_frequency should probably co-vary in a benchmark's weighting,
  since Bosquet found them as two independent but additive effects, not one
  combined effect.
```

```yaml
rule: taper_shape
family: taper
reads: "last taper week's training minutes / first taper week's training minutes, for plans with 2+ taper-labelled weeks (a ratio below 1 confirms decline; how far below 1 approximates exponential vs. flat/step)"
applies_to: {goals: [half_ironman, ironman], tiers: all}
band: {low: 0.50, high: 0.85, unit: ratio, ramp: 0.30}
evidence_grade: C
sources:
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Reviews modelling and dose-response evidence favouring exponential (progressive) taper shapes over linear or step reductions."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
  - citation: "Thomas L, Busso T (2005). A Theoretical Study of Taper Characteristics to Optimize Performance. Med Sci Sports Exerc 37(9):1615-1621."
    doi: 10.1249/01.mss.0000177461.94156.4b
    states: "Simulation using a nonlinear fitness-fatigue model finds fast exponential decay (time constant ~5 days) outperforms slower or linear/step tapers."
    verified: crossref
    crossref_title: "A Theoretical Study of Taper Characteristics to Optimize Performance"
disagreements: >-
  The exponential-is-best result comes from modelling/simulation (Thomas &
  Busso) and review synthesis (Mujika & Padilla), not a head-to-head RCT
  comparing shapes on athletes holding duration and volume constant — no
  such trial was found. Practical coaching texts (Friel) describe a
  "two-step" taper (tlc Sec.7) as the working approximation of exponential
  decay, which is coarser than a true continuous exponential.
held_out: true
school_specific: false
notes_for_implementer: >-
  Not read anywhere in devtools/quality/planner/score_quality.py — the
  existing taper_volume_drop rule scores only the *mean* across taper weeks,
  so a flat 60%-of-peak-every-week taper and a 75%-then-45% two-step taper
  with the same mean would score identically today; this rule would
  distinguish them. Needs >=2 taper-mode weeks to be computable; on a
  single-taper-week plan (common for shorter marathon builds) mark
  not-applicable rather than scoring. A stricter version could fit a decay
  time constant tau to 3+ weekly volumes and compare to ~5 days, but that
  needs more taper weeks than most half-Ironman/marathon plans carry, hence
  restricting applies_to to half_ironman/ironman where 2-3 taper weeks are
  more common (tlc Sec.7: half 10-14d, Ironman 14-21d).
```

```yaml
rule: race_week_volume
family: taper
reads: "race-week training minutes / peak load-week minutes"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.19, high: 0.40, unit: ratio, ramp: 0.20}
evidence_grade: B
sources:
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Race week sits at the deepest point of a two-step or exponential taper; sessions become short activation/openers only."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
disagreements: >-
  The review states the qualitative pattern (race week is the lowest-volume
  week) but does not give a numeric percentage-of-peak figure; the 0.19-0.40
  band is an empirical envelope (docs/research/triathlon_long_course.md Sec.7), not a literature-derived
  number. Grading this B rather than A/C reflects that split: qualitative
  direction is well-sourced, the specific numeric band is empirical.
held_out: false
school_specific: false
notes_for_implementer: >-
  Matches devtools/quality/constraints/shared.yaml `race_week_volume`
  exactly, and the per-goal overrides in ironman.yaml (0.19-0.40),
  half_ironman.yaml (0.27-0.50) and running_marathon.yaml (0.20-0.40) are all empirical envelopes rather than literature optima — flag this the same
  way as taper_volume_drop's disagreement above if the benchmark wants a
  literature-pure band instead of an empirical one, since none was
  found in the sources checked.
```

```yaml
rule: race_day_freshness_ratio
family: taper
reads: "race-day TSB (CTL - ATL) / race-day CTL, from the combined-sport PMC trajectory"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.16, high: 0.47, unit: ratio, ramp: 0.15}
evidence_grade: C
sources:
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed.). VeloPress."
    isbn: "978-1-937715-93-9"
    states: "States the +15 to +25 TSB convention for A-race freshness on the Performance Management Chart (raw TSS-equivalent units, not a CTL-normalised ratio)."
    verified: isbn
  - citation: "Banister EW, Calvert TW, Savage MV, Bach T (1975). A Systems Model of Training for Athletic Performance. Aust J Sports Med 7:57-61."
    states: "Origin of the fitness-fatigue (impulse-response) model that CTL/ATL/TSB operationalise; the paper predates DOIs and defines no TSB convention itself."
    verified: "no"
disagreements: >-
  The +15/+25 convention is a raw-units TrainingPeaks/Coggan heuristic (see
  race_day_tsb_absolute below), not a ratio against CTL. Endury's ratio form
  is a repo-specific normalisation (dividing by CTL so athletes with
  different absolute training loads are comparable) that has no direct
  literature source — the band values (0.16-0.47) are empirical, not from a
  peer-reviewed ratio threshold. No peer-reviewed source was found
  validating any TSB/CTL ratio target; the underlying raw-TSB convention
  itself is also not peer-reviewed (see next rule).
held_out: false
school_specific: true
notes_for_implementer: >-
  Matches devtools/quality/constraints/shared.yaml `race_day_freshness_ratio`
  (fallback) and the per-goal overrides in ironman.yaml (0.30-0.47),
  half_ironman.yaml (0.16-0.39) and running_marathon.yaml (0.30-0.47), all
  described in those files as "measured range... rounded outward" — i.e.
  empirical, not literature-derived. Because the underlying TSB convention
  is a single commercial-tool school (Coggan/TrainingPeaks), mark
  school_specific true even though Endury's specific ratio form is its own
  invention on top of that school. Needs both a computable CTL and ATL on
  race day (42/7-day EMAs); undefined on plans shorter than ~1 CTL time
  constant (42 days), which excludes some short marathon builds.
```

```yaml
rule: race_day_tsb_absolute
family: taper
reads: "race-day TSB (CTL - ATL) in raw TSS-equivalent units, single-day value, not normalised by CTL"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 15.0, high: 25.0, unit: TSS-equivalent points, ramp: 10.0}
evidence_grade: C
sources:
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed.). VeloPress."
    isbn: "978-1-937715-93-9"
    states: "States +15 to +25 TSB as the target freshness zone for an A-priority race on the Performance Management Chart."
    verified: isbn
  - citation: "Banister EW, Calvert TW, Savage MV, Bach T (1975). A Systems Model of Training for Athletic Performance. Aust J Sports Med 7:57-61."
    states: "Establishes the fitness-minus-fatigue construct that TSB operationalises; sets no numeric target itself."
    verified: "no"
disagreements: >-
  A single commercial-platform convention (TrainingPeaks, built on Coggan's
  PMC), reproduced in the Allen/Coggan/McGregor book — no peer-reviewed RCT
  was found validating +15/+25 as optimal for any distance.
  docs/research/endurance_methodology.md Sec.5 already states this candidly:
  "These are coaching heuristics, not peer-reviewed thresholds." Other
  coaches (Friel) describe differently-centred windows, not verified here.
held_out: true
school_specific: true
notes_for_implementer: >-
  Endury's RULES tuple has no rule reading raw single-day TSB in TSS-
  equivalent units — only the CTL-normalised ratio (race_day_freshness_ratio
  above). Absolute TSB is athlete-relative-load-dependent (an athlete with
  CTL 40 and one with CTL 120 read very differently at "TSB +20"), which is
  presumably why Endury normalised it; a benchmark grading LLM-written plans
  by absolute TSB should expect this to penalise low-CTL athletes' plans
  more harshly than the ratio form does for the same relative freshness.
  Needs the same 42/7-day PMC prerequisites as the ratio rule.
```

```yaml
rule: detraining_risk_taper_ceiling
family: taper
reads: "days from the last BASE/BUILD-mode (load) week to race day — i.e. the combined span of taper + peak(non-load) + race weeks, as an upper-bound check rather than an optimum window"
applies_to: {goals: [marathon, half_ironman, ironman], tiers: all}
band: {low: 0.0, high: 28.0, unit: days, ramp: 7.0}
evidence_grade: B
sources:
  - citation: "Mujika I, Padilla S (2000). Detraining: Loss of Training-Induced Physiological and Performance Adaptations. Part I: Short Term Insufficient Training Stimulus. Sports Med 30(2):79-87."
    doi: 10.2165/00007256-200030020-00002
    states: "Reviews physiological/performance decay in the first ~4 weeks of markedly reduced or ceased training; some markers (VO2max, enzymatic activity) begin measurable decline within 2-4 weeks."
    verified: crossref
    crossref_title: "Detraining: Loss of Training-Induced Physiological and Performance Adaptations. Part I"
  - citation: "Mujika I, Padilla S (2000). Detraining: Loss of Training-Induced Physiological and Performance Adaptations. Part II: Long Term Insufficient Training Stimulus. Sports Med 30(3):145-154."
    doi: 10.2165/00007256-200030030-00001
    states: "Extends the review past 4 weeks; longer reductions or full cessation produce larger, more consistent losses across cardiorespiratory and muscular markers."
    verified: crossref
    crossref_title: "Detraining: Loss of Training-Induced Physiological and Performance Adaptations. Part II"
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Cautions that tapers longer than the studied ~1-3 week range risk trading fatigue dissipation for genuine fitness loss, i.e. the taper stops being a taper and starts being detraining."
    verified: crossref
    crossref_title: "Scientific Bases for Precompetition Tapering Strategies"
disagreements: >-
  The two detraining reviews study reduced-or-ceased training generally, not
  a maintained-frequency/maintained-intensity taper specifically — a
  well-executed taper (Bosquet's frequency- and intensity-maintenance
  findings above) is explicitly designed to avoid the detraining stimulus
  those reviews describe, so the 28-day ceiling here is an inference bridging
  two literatures, not a number either review states directly for a taper.
  Treat the 28-day edge as conservative, not literature-measured.
held_out: true
school_specific: false
notes_for_implementer: >-
  No rule caps taper length from above; taper_duration above catches the
  same failure mode, so implementing both is likely redundant unless a plan
  has a long reduced-but-not-taper-labelled tail (e.g. a post-injury
  maintenance block) that a mode-based count would miss. If only one is
  implemented, prefer taper_duration (tighter band, clearer literature tie).
```

---

## Not found

- **Neary et al. (taper-specific dose-response study)**: named in the task's
  source list but not located with a confident author/year/journal match
  within the time budget; several "Neary" endurance-training papers exist
  (e.g. on HIT and overtraining) and picking the right one without
  misattributing a citation was not possible in the time available. Left out
  rather than guessed.
- **Thomas, Mujika & Busso (2008) modelling paper**: named in the task's
  source list; not located and verified within the time budget. Thomas &
  Busso (2005) above was found and verified instead and likely covers
  similar modelling ground, but the 2008 paper itself was not confirmed.
- **A literature-stated day-count for "how long can a taper run before it
  becomes detraining"**: no source gives this as a direct number for a
  taper (as opposed to a stopped or drastically-reduced training block); the
  28-day figure in `detraining_risk_taper_ceiling` above is an inference,
  not a reported result, and is flagged as such in that rule's
  `disagreements` field.
- **A peer-reviewed validation of any TSB/CTL ratio threshold**: only the
  raw-TSB convention (+15/+25, Allen/Coggan/McGregor) was found; no source
  validating a CTL-normalised ratio version of it (Endury's
  `race_day_freshness_ratio`) was located.
- **Busso (2003) applied specifically to taper shape**: the paper
  (verified above under taper methodology generally, cited in
  endurance_methodology.md Sec.5) is about variable dose-response dynamics,
  not tapering per se; it was not cited under `taper_shape` because Thomas &
  Busso (2005) is the more directly on-topic modelling paper.

## Sources register

| Source | Type | Verified |
|---|---|---|
| Bosquet et al. 2007, MSSE 39(8):1358-1365, DOI 10.1249/mss.0b013e31806010e0 | meta-analysis | crossref |
| Mujika & Padilla 2003, MSSE 35(7):1182-1187, DOI 10.1249/01.MSS.0000074448.73931.11 | review | crossref |
| Mujika & Padilla 2000 Part I, Sports Med 30(2):79-87, DOI 10.2165/00007256-200030020-00002 | review | crossref |
| Mujika & Padilla 2000 Part II, Sports Med 30(3):145-154, DOI 10.2165/00007256-200030030-00001 | review | crossref |
| Mujika 2009, *Tapering and Peaking for Optimal Performance*, Human Kinetics, ISBN 978-0-7360-7484-1 | book | isbn |
| Thomas & Busso 2005, MSSE 37(9):1615-1621, DOI 10.1249/01.mss.0000177461.94156.4b | modelling | crossref |
| Wang, Wang, Gao & Zhong 2023, PLOS ONE 18(5):e0282838, DOI 10.1371/journal.pone.0282838 | systematic review/meta-analysis | crossref |
| Allen, Coggan & McGregor 2019, *Training and Racing with a Power Meter* (3rd ed.), VeloPress, ISBN 978-1-937715-93-9 | book/coaching convention | isbn |
| Banister, Calvert, Savage & Bach 1975, Aust J Sports Med 7:57-61 | founding model paper | no (pre-DOI journal; no DOI found) |
| Le Meur et al. 2013 ("2012" in the task's source list; journal-published 2013), J Appl Physiol 114(3):411-420, DOI 10.1152/japplphysiol.01254.2012 | cohort/observational | crossref, not used in a rule card (see note) |

Note on Le Meur: verified via Crossref but not built into a rule card — it
characterises detecting overreaching *before* a taper starts using
physiological/cognitive test data the benchmark cannot read off a finished
plan, so it yielded no computable rule under this family's constraints.
