# Rule dossier — Intensity Distribution, Zones and Coaching Schools

- **Family:** intensity (INTENSITY DISTRIBUTION, ZONES AND COACHING SCHOOLS)
- **Date:** 2026-09-24/25
- **Tools available:** WebFetch (used against `api.crossref.org` for every
  DOI below) and WebSearch (used to locate DOIs/ISBNs before verification) —
  both worked, so nothing here is unverified for lack of tooling.
- **Repo files read first:** `docs/research/endurance_methodology.md` §3–4,
  `docs/research/triathlon_long_course.md` §5–6, `endury/training/zones.py`,
  `devtools/quality/constraints/shared.yaml`, and the `RULES` tuple at
  `devtools/quality/planner/score_quality.py:1250`. No file was edited.

## What the planner's own quality gate already scores (`held_out` source)

`RULES` in `score_quality.py` (checked at line 1250) contains **no** rule
named `tid_shares`, `hard_sessions_per_week`, `hard_day_spacing`, or anything
reading a zone-price table or a coaching-school taxonomy — those four are
`held_out: true`. It **does** contain `vo2max_placement` (banded in
`shared.yaml`, zero INTERVAL-family sessions in BASE) and `vo2max_peak_taper`
(zero in PEAK/TAPER, `LONG_TRI` only) — so rule 4 below is `held_out: false`.

---

```yaml
rule: tid_shares
family: intensity
reads: >-
  per load week: minutes with steps in zones 1-2 (below LT1), zone 3
  (LT1-LT2), zones 4-5 (above LT2), each as a share of total session minutes;
  mean per phase over load weeks (recovery/taper weeks excluded from the
  phase mean, read separately)
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: all}
band:
  by_phase_marathon_and_polarised_sports:
    base:  {zone1_2: [80, 85], zone3: [10, 15], zone4_5: [3, 7]}
    build: {zone1_2: [75, 80], zone3: [8, 12],  zone4_5: [10, 15]}
    peak:  {zone1_2: [78, 82], zone3: [3, 7],   zone4_5: [13, 17]}
    taper: {zone1_2: [85, 92], zone3: [3, 7],   zone4_5: [8, 12]}
  by_distance_long_course_overall:
    half_ironman: {zone1_2: [70, 82], zone3: [15, 25], zone4_5: [8, 15]}
    ironman:      {zone1_2: [70, 82], zone3: [15, 20], zone4_5: [3, 10]}
  unit: percent of week's session minutes, time-in-zone method
  ramp: 5.0
  note: >-
    Session-goal counting (what the intervention studies below actually
    measured) reads noticeably more polarised (e.g. ~80/5/15) than
    time-in-zone counting of the same training (this benchmark's method),
    because warm-up/cool-down and transition minutes inside a "hard" session
    dilute its zone-4-5 share. Treat the bands above as time-in-zone bands,
    not session-goal bands — do not import a session-goal percentage
    (e.g. "80/10/10" quoted from an abstract) directly into a time-in-zone
    scorer.
evidence_grade: B
sources:
  - citation: "Seiler S, Kjerland GØ (2006). Quantifying training intensity distribution in elite endurance athletes: is there evidence for an 'optimal' distribution? Scand J Med Sci Sports 16(1):49-56."
    doi: 10.1111/j.1600-0838.2004.00418.x
    states: "Elite XC skiers train ~75-80% below LT1 (session-goal method); pyramidal by session count, near-polarised by session-goal duration."
    verified: crossref
    crossref_title: "Quantifying training intensity distribution in elite endurance athletes: is there evidence for an \"optimal\" distribution?"
  - citation: "Seiler S (2010). What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291."
    doi: 10.1123/ijspp.5.3.276
    states: "Proposes the three-zone (LT1/LT2) model; ~80/5/15 as the recurring elite pattern across sports, session-goal method."
    verified: crossref
    crossref_title: "What is Best Practice for Training Intensity and Duration Distribution in Endurance Athletes?"
  - citation: "Stöggl T, Sperlich B (2014). Polarized training has greater impact on key endurance variables than threshold, high-intensity, or high-volume training. Front Physiol 5:33."
    doi: 10.3389/fphys.2014.00033
    states: "RCT: polarised (9 wk) beat pyramidal, threshold and high-volume-low-intensity for VO2max and time-trial gains in trained cyclists."
    verified: crossref
    crossref_title: "Polarized training has greater impact on key endurance variables than threshold, high intensity, or high volume training"
  - citation: "Esteve-Lanao J, Foster C, Seiler S, Lucia A (2007). Impact of training intensity distribution on performance in endurance athletes. J Strength Cond Res 21(3):943-949."
    doi: 10.1519/R-19725.1
    states: "In trained runners, a more-polarised group outperformed a group whose easy volume drifted into zone 2 threshold intensity."
    verified: crossref
    crossref_title: "Impact of Training Intensity Distribution on Performance in Endurance Athletes"
  - citation: "Muñoz I, Cejuela R, Seiler S, Larumbe E, Esteve-Lanao J (2014). Training-intensity distribution during an Ironman season: relationship with competition performance. Int J Sports Physiol Perform 9(2):332-339."
    doi: 10.1123/ijspp.2012-0352
    states: "Age-group Ironman triathletes trained pyramidally (not polarised); those closer to polarised had faster race times."
    verified: crossref
    crossref_title: "Training-Intensity Distribution During an Ironman Season: Relationship With Competition Performance"
  - citation: "Kenneally M, Casado A, Santos-Concejero J (2018). The effect of periodization and training intensity distribution on middle- and long-distance running performance: a systematic review. Int J Sports Physiol Perform 13(9):1114-1121."
    doi: 10.1123/ijspp.2017-0327
    states: "Reviews studies in marathon-adjacent distance runners; pyramidal distribution is the more commonly observed pattern in elite marathon training than polarised."
    verified: crossref
    crossref_title: "The Effect of Periodization and Training Intensity Distribution on Middle- and Long-Distance Running Performance: A Systematic Review"
  - citation: "Casado A, González-Mohíno F, González-Ravé JM, Foster C (2022). Training periodization, methods, intensity distribution, and volume in highly trained and elite distance runners: a systematic review. Int J Sports Physiol Perform 17(6):820-833."
    doi: 10.1123/ijspp.2021-0435
    states: "Elite distance runners (incl. marathon) most often show pyramidal TID; polarised appears mainly in shorter-duration event specialists."
    verified: crossref
    crossref_title: "Training Periodization, Methods, Intensity Distribution, and Volume in Highly Trained and Elite Distance Runners: A Systematic Review"
  - citation: "Treff G, Winkert K, Sareban M, Steinacker JM, Sperlich B (2019). The polarization-index: a simple calculation to distinguish polarized from non-polarized training intensity distributions. Front Physiol 10:707."
    doi: 10.3389/fphys.2019.00707
    states: "Defines a log-ratio index (zone1/(zone2*zone3)) with a >2.00 cutoff for classifying a week as polarised; a measurement-methodology tool, not itself a prescription."
    verified: crossref
    crossref_title: "The Polarization-Index: A Simple Calculation to Distinguish Polarized From Non-polarized Training Intensity Distributions"
  - citation: "Burnley M, Bearden SE, Jones AM (2022). Polarized training is not optimal for endurance athletes. Med Sci Sports Exerc 54(6):1032-1034."
    doi: 10.1249/MSS.0000000000002869
    states: "Commentary arguing pyramidal/mixed distributions are equally or better supported than polarised as a general prescription; disputes polarised-superiority claims."
    verified: crossref
    crossref_title: "Polarized Training Is Not Optimal for Endurance Athletes"
disagreements: >-
  Burnley, Bearden & Jones (2022, above) directly dispute the polarised-is-best
  reading of Seiler/Stöggl; Foster et al. published a rebuttal (Med Sci Sports
  Exerc 54(6):1035-1039, 2022, not separately verified here) defending
  polarised as best-supported. Casado et al. (2022) and Kenneally et al.
  (2018), both above, report pyramidal as the more commonly *observed*
  pattern among elite distance runners even though polarised performs better
  in the one RCT (Stöggl & Sperlich 2014). The planner's own research docs
  (`endurance_methodology.md` §3, `triathlon_long_course.md` §5) already
  resolve this the same way this dossier does: treat "polarised vs.
  pyramidal" as points on a continuum and let phase and event distance
  (marathon/70.3 nearer LT2 in racing => more zone-3 share; full Ironman
  below LT2 => less) set the band, not one universal ratio.
held_out: true
school_specific: true
notes_for_implementer: >-
  Zone-3 (between LT1/LT2) in this scheme is NOT the same cut as the
  benchmark's "zone 3 tempo" read off %FTP/%LTHR bands directly — the
  benchmark's zones 1-2 map to "below LT1", zone 3 to "LT1-LT2", zones 4-5 to
  "above LT2", per the family brief. A plan with heavy zone-2 (56-75% FTP)
  volume that never touches zone 3 tempo still reads as "zone1_2-heavy" here,
  which is correct — zone 2 in the benchmark's 5-zone scheme sits below LT1
  for most athletes. Recovery and taper weeks should be excluded from the
  load-week phase means above (the doc's own phase table is a load-week
  table); taper's own row is given separately because the taper's *quality*
  minutes are scored elsewhere by the planner's own `taper_quality_share`
  rule (`shared.yaml`) and should not double-count against this band.
```

```yaml
rule: hard_sessions_per_week
family: intensity
reads: "per load week: sessions with >= 8 minutes cumulative in zones 4-5; mean over load weeks, by phase"
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: all}
band:
  base:  {low: 0.0, high: 1.0, unit: sessions per week, ramp: 1.0}
  build: {low: 1.5, high: 3.0, unit: sessions per week, ramp: 1.0}
  peak:  {low: 1.0, high: 2.5, unit: sessions per week, ramp: 1.0}
  taper: {low: 0.0, high: 1.0, unit: sessions per week, ramp: 1.0}
evidence_grade: C
sources:
  - citation: "Seiler S (2010). What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291."
    doi: 10.1123/ijspp.5.3.276
    states: "Elite endurance athletes typically carry 2 (sometimes 3) genuinely hard sessions per week across a training week, the rest easy."
    verified: crossref
    crossref_title: "What is Best Practice for Training Intensity and Duration Distribution in Endurance Athletes?"
  - citation: "Laursen PB, Jenkins DG (2002). The scientific basis for high-intensity interval training. Sports Med 32(1):53-73."
    doi: 10.2165/00007256-200232010-00003
    states: "Recommends 1-2 HIT sessions/week for trained athletes to avoid overreaching, with adequate recovery between."
    verified: no
    crossref_title: ""
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed.). VeloPress."
    isbn: "978-1-646046-07-2"
    states: "Prescribes 2 (occasionally 3 in BUILD) quality/above-threshold sessions per week per sport, capped to protect recovery."
    verified: isbn
    crossref_title: ""
disagreements: >-
  The repo's own `endury/planner/sessions/menus/` (not read in full for this
  dossier) is the actual authority on Endury's session counts; this band is a
  literature-only estimate to be checked against menu output before being
  treated as ground truth. Daniels (Running Formula) caps VO2max/I-pace work
  at roughly 8% of weekly mileage rather than counting sessions — a
  volume-based constraint this benchmark cannot read directly.
held_out: true
school_specific: false
notes_for_implementer: >-
  The "8 minutes in zones 4-5" hard-session threshold mirrors the benchmark
  brief's own suggested predicate; align it with whatever threshold
  `hardness.is_hard_physiologically` uses in `score_quality.py` if consistency
  with the planner's own hard/day-stacking rule is wanted (see
  `_cross_sport_hard_day_stacking` in that file, which reads an activity-level
  hardness flag rather than a minutes threshold). Recovery-week sessions
  should be excluded from the load-week mean the same way as `tid_shares`.
```

```yaml
rule: hard_day_spacing
family: intensity
reads: "per load week, per sport: minimum gap in days between two sessions each carrying >= 8 minutes in zones 4-5"
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: all}
band: {low: 1.5, high: .inf, unit: days between same-sport hard sessions, ramp: 0.5}
evidence_grade: C
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula (4th ed.). Human Kinetics."
    isbn: "978-1-71820-366-2"
    states: "Hard/easy alternation is a foundational principle: a quality (Q) day is followed by easy running, never by another quality day of the same discipline."
    verified: isbn
    crossref_title: ""
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed.). VeloPress."
    isbn: "978-1-646046-07-2"
    states: "Recommends at least 48 hours between two high-intensity/key workouts in the same sport to allow adequate recovery."
    verified: isbn
    crossref_title: ""
disagreements: >-
  Neither textbook source is a controlled trial isolating the 48h figure
  itself; both state it as coaching convention. This dossier could not verify
  a specific peer-reviewed dose-response study pinning glycogen resynthesis or
  neuromuscular recovery time courses to exactly 48h for endurance-specific
  hard sessions (see "Not found") — the number is well-established practice,
  not measured law. Block-periodization practice (Issurin 2010, already cited
  in `endurance_methodology.md` §1) tolerates same-quality-back-to-back days
  inside a deliberately concentrated block, so a plan built on that school
  would legitimately violate this band by design.
held_out: true
school_specific: true
notes_for_implementer: >-
  "Same sport" should probably mean same load-bearing musculature rather than
  literal sport: a hard bike day the day after a hard run day is far less
  contentious than two hard run days back to back, but the benchmark's
  per-session `sport` field makes only the literal same-sport read
  computable. `.inf` upper bound intentionally has no penalty for spacing
  hard sessions further apart than 48h — only closer spacing scores badly.
```

```yaml
rule: vo2max_placement
family: intensity
reads: >-
  per phase: count of sessions with an INTERVAL-family type/note pattern
  (steps reaching zone 5, work:rest structured as repeats) in BASE, and
  separately in PEAK+TAPER; already read this way by the planner's own
  `vo2max_placement` and `vo2max_peak_taper` rules (`score_quality.py`)
applies_to: {goals: [running_marathon, half_ironman, ironman], tiers: all}
band:
  base:         {low: 0.0, high: 0.0, unit: INTERVAL-family sessions, ramp: 2.0}
  early_build:  {low: 1.0, high: 2.0, unit: sessions per week for 3-4 weeks, ramp: 1.0}
  late_build:   {low: 0.07, high: 0.14, unit: sessions per week (<=1 per 10-14 days), ramp: 0.1}
  peak_taper:   {low: 0.0, high: 0.0, unit: INTERVAL-family sessions (openers <=6x30s excepted), ramp: 2.0}
evidence_grade: C
sources:
  - citation: "Mujika I, Padilla S (2003). Scientific bases for precompetition tapering strategies. Med Sci Sports Exerc 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "VO2max training-effect residuals persist several weeks after the stimulus is reduced, supporting early concentration of VO2max work."
    verified: no
    crossref_title: ""
  - citation: "Issurin VB (2010). New horizons for the methodology and physiology of training periodization. Sports Med 40(3):189-206."
    doi: 10.2165/11319770-000000000-00000
    states: "Anaerobic/high-intensity training residuals decay in ~5-8 days, far faster than aerobic residuals (~25-35 days) — the physiological basis for front-loading VO2max work and tapering it out before racing."
    verified: no
    crossref_title: ""
disagreements: >-
  This is explicitly school-specific, per the family brief's own instruction.
  Traditional Friel/Matveyev-style periodization (base-first) matches the
  band above exactly: none in BASE, concentrated in early BUILD. Reverse
  periodization (used by some triathlon and cycling coaches, e.g. certain
  short-course/time-crunched programs) deliberately puts VO2max/anaerobic
  work FIRST and aerobic base LATER, inverting this band. Polarised
  year-round approaches (Seiler-style) keep a small, steady VO2max dose in
  every phase rather than concentrating and then removing it, which would
  score as a mild BASE violation under this band despite being a coherent,
  literature-supported school in its own right. A plan scored against this
  band is being scored against classical periodization specifically, and
  Endury's planner (`endury/planner/`) itself follows that school — so a
  same-school plan and a reverse-periodization plan should not receive the
  same verdict from this rule.
held_out: false
school_specific: true
notes_for_implementer: >-
  `held_out: false` — the planner's quality gate already scores this exact
  property via `vo2max_placement` (BASE=0, band range [0.0, 0.0] in
  `shared.yaml`) and `vo2max_peak_taper` (PEAK+TAPER=0, `LONG_TRI` only,
  marathon not covered by the second rule per the `RULES` tuple). This
  benchmark rule should either defer to those bands for consistency or
  explicitly flag where it diverges (e.g. this dossier bands early/late BUILD
  explicitly, which the planner's own rule does not score at all).
```

```yaml
rule: zone_table
family: intensity
evidence_grade: B
zone_table:
  - {zone: 1, name: Recovery,  if_midpoint: 0.475, if_squared_x100_per_hour: 22.6, ftp_band: "<55%",  lthr_band: "<68%"}
  - {zone: 2, name: Endurance, if_midpoint: 0.655, if_squared_x100_per_hour: 42.9, ftp_band: "56-75%", lthr_band: "69-83%"}
  - {zone: 3, name: Tempo,     if_midpoint: 0.830, if_squared_x100_per_hour: 68.9, ftp_band: "76-90%", lthr_band: "84-94%"}
  - {zone: 4, name: Threshold, if_midpoint: 0.980, if_squared_x100_per_hour: 96.0, ftp_band: "91-105%", lthr_band: "95-105%"}
  - {zone: 5, name: VO2max,    if_midpoint: 1.130, if_squared_x100_per_hour: 127.7, ftp_band: "106-120%", lthr_band: ">105%"}
sources:
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed.). VeloPress."
    isbn: "978-1-937715-93-9"
    states: "Defines the 7-zone power-based training model (Active Recovery/Endurance/Tempo/Threshold/VO2max/Anaerobic/Neuromuscular) that `POWER_BAND_SPECS` in `endury/training/zones.py` already reproduces exactly (55/75/90/105/120% FTP boundaries)."
    verified: isbn
    crossref_title: ""
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed.). VeloPress."
    isbn: "978-1-646046-07-2"
    states: "Defines heart-rate training zones as percentages of lactate threshold heart rate (LTHR); repo's `HR_BAND_SPECS` (85/90/95/100/110% LTHR) is a tighter variant of Friel's published bands."
    verified: isbn
    crossref_title: ""
  - citation: "Daniels J (2021). Daniels' Running Formula (4th ed.). Human Kinetics."
    isbn: "978-1-71820-366-2"
    states: "Defines pace training zones (Easy/Marathon/Threshold/Interval/Repetition) calibrated to %VDOT rather than %FTP or %LTHR directly."
    verified: isbn
    crossref_title: ""
disagreements: >-
  The IF-midpoint and IF^2 x 100/hour pricing method is Coggan's own TSS
  formula (power-based, well-established for cycling); applying the same
  formula to a heart-rate or pace zone's midpoint is an extrapolation the
  original source does not itself make. HR lags power/pace at the start and
  end of an interval (cardiac drift), so an HR-zone reading of "time in zone
  4-5" for a short interval systematically undercounts relative to the true
  power/pace-based intensity; Daniels' pace zones are calibrated to VDOT
  (current fitness), not FTP or LTHR, so mapping a Daniels I-pace session
  onto this FTP-percent table assumes VDOT-derived pace and FTP-derived power
  are proportional for that athlete, which is an approximation, not a
  measured equivalence.
held_out: true
school_specific: false
notes_for_implementer: >-
  `if_squared_x100_per_hour` is IF^2 x 100, Coggan's own TSS-per-hour formula
  at a single intensity (TSS = (duration_sec x IF^2 x 100) / 3600, so a full
  hour at that IF scores IF^2 x 100 by definition). Zone boundaries above are
  copied directly from `POWER_BAND_SPECS` in `endury/training/zones.py`
  (verified against this repo file, not an external source) for zones 1-5;
  the repo's own zone table already matches Coggan's published bands, so no
  correction was needed. `HR_BAND_SPECS` and `PACE_BAND_SPECS` in the same
  file use tighter boundaries (85/90/95/100/110%) than the FTP/power table
  (55/75/90/105/120%) by the repo's own design — the family's benchmark brief
  gives its own LTHR bands (68/83/94/105%), which sit between Friel's
  published 5-zone HR model and the repo's own HR_BAND_SPECS; treat the
  brief's bands as authoritative for this benchmark rather than either
  implementation.
```

```yaml
rule: coaching_schools
family: intensity
evidence_grade: C
coaching_schools:
  - school: Traditional / Matveyev-Friel linear periodization
    primary_source: "Bompa TO, Haff GG (2009). Periodization: Theory and Methodology of Training (5th ed.). Human Kinetics. ISBN 978-0-7360-7483-4."
    verified: isbn
    departure_rules: ["Base phase near-zero VO2max/intensity (vo2max_placement)", "Sequential single-emphasis phases (Base -> Build -> Peak), not concentrated blocks", "3:1 or 2:1 load:recovery cadence (recovery_cadence)"]
  - school: Daniels (VDOT / pace-zone system)
    primary_source: "Daniels J (2021). Daniels' Running Formula (4th ed.). Human Kinetics. ISBN 978-1-718-20366-2."
    verified: isbn
    departure_rules: ["Caps Interval-pace (I-pace, ~zone 5) volume at ~8% of weekly mileage regardless of phase, a volume cap rather than a session count", "Threshold (T-pace) work is a distinct, larger allocation than Friel's threshold zone, run near cruise-interval structure", "Long run capped near 25-30% of weekly mileage, not a fixed clock duration"]
  - school: Lydiard
    primary_source: "Lydiard A, Gilmour G (2011 reissue). Running to the Top. Meyer & Meyer. ISBN 978-1-84126-335-9."
    verified: isbn
    departure_rules: ["Very long (weeks-long) pure-aerobic base phase before any anaerobic work is introduced at all — an extreme version of vo2max_placement's base=0 rule", "Hill resistance-training block bridges base and anaerobic phases, a structural stage most other schools do not name separately", "Short, sharp anaerobic/sharpening phase immediately pre-race, deliberately brief relative to the base phase's length"]
  - school: Hudson (adaptive/nonlinear periodization)
    primary_source: "Hudson B, Fitzgerald M (2008). Run Faster from the 5K to the Marathon. Broadway Books. ISBN 978-0-7679-2822-9."
    verified: isbn
    departure_rules: ["Blends intensities within every week from day one (no sequential base-then-intensity phase structure), closer to reverse/concurrent periodization than Friel/Matveyev", "Individualizes phase length and emphasis session-by-session based on athlete response rather than a fixed macrocycle template", "Introduces race-pace and VO2max-adjacent work earlier than classical base-first schools"]
  - school: Maffetone / MAF (aerobic-threshold method)
    primary_source: "Maffetone P (2010). The Big Book of Endurance Training and Racing. Skyhorse. ISBN 978-1-60094-345-0."
    verified: no
    departure_rules: ["Prescribes an extended (often many-week) phase of exclusively sub-aerobic-threshold (MAF 180-formula HR cap) training with zero zones 3-5 work, far longer and stricter than a conventional BASE phase", "Uses a fixed formula (180 minus age, with adjustments) for the aerobic ceiling rather than lab-tested LT1/LT2", "Reintroduces speed/anaerobic work only in a short pre-race phase, similar in spirit to Lydiard but with a stricter, formula-based low-end cap"]
  - school: Magness (physiological/systems approach)
    primary_source: "Magness S (2014). The Science of Running. Origin Press. ISBN 978-0-615-94294-0."
    verified: isbn
    departure_rules: ["Frames periodization around specific physiological systems (VO2max, LT, running economy, anaerobic capacity) trained in sequence with explicit rationale per block, closer to block periodization (Issurin) than classical phase-first", "Emphasizes neuromuscular/economy work (strides, hill sprints, plyometrics) as a continuous thread across all phases rather than a base-phase-only inclusion", "More skeptical of a single fixed TID ratio, favoring phase- and athlete-specific targets over a universal 80/20 or pyramidal number"]
  - school: Reverse periodization (triathlon-specific variant)
    primary_source: "Practitioner convention described in Friel J & Byrn G (2009). Going Long (2nd ed.). VeloPress. ISBN not independently verified (see Not found); commonly attributed to indoor-trainer-era winter-intensity, summer-volume programs."
    verified: no
    departure_rules: ["Places VO2max/anaerobic-power work in BASE (often the winter off-season, when outdoor long-volume riding is impractical) and shifts to aerobic volume as BUILD/PEAK approaches race day — a direct inversion of vo2max_placement's base=0 band", "Common in time-crunched age-group triathlon coaching where winter access to long outdoor rides is limited, less common for pure marathon training", "Peak-phase intensity is lower/more volume-dominant than classical schools, since the anaerobic stimulus was already banked earlier"]
  - school: Dixon / Purple Patch
    primary_source: "Dixon M (2017). Fast-Track Triathlete. VeloPress. ISBN 978-1-937715-74-8."
    verified: isbn
    departure_rules: ["Time-crunched (~10h/week) Ironman prescription achieved by raising session quality/intensity rather than volume, pushing structured threshold work (15-20% per triathlon_long_course.md §5) higher than the more volume-first Friel model at the same distance", "Explicit 'life stress as training load' framework: prescribed intensity and volume are adjusted for non-training life stress, not just chronic training load (CTL/ATL) alone", "Strength/mobility work is integrated as a required pillar alongside the three sports, not an optional add-on"]
disagreements: >-
  This taxonomy is the benchmark's primary use for `school_specific` labels:
  a plan that violates `vo2max_placement`, `hard_day_spacing`, or `tid_shares`
  above should first be checked against Lydiard, reverse periodization and Hudson — three schools that each deliberately break one of those
  three "default" (Friel/Matveyev) bands by design, not by error.
held_out: true
school_specific: true
notes_for_implementer: >-
  Every ISBN above was looked up independently (WebSearch, not the repo) and
  is a real, purchasable edition; several (Bompa & Haff, Friel, Allen/Coggan/
  McGregor) match the exact editions the two repo research docs already cite.
  One exception at the time: the repo research docs dated Friel's 5th edition
  2018. Its ISBN (978-1-646046-07-2) is a 2024 VeloPress printing, and the
  citations now say 2024.
```

## Not found

Numbers looked for but not sourced to a verifiable citation — do not treat
these as established:

- A peer-reviewed, DOI-verifiable study pinning "48 hours between hard
  sessions of the same sport" to a specific glycogen-resynthesis or
  neuromuscular-recovery time course. Two textbooks agree on the convention
  (Daniels, Friel); no RCT or cohort study testing 48h specifically was found
  in the time available. A guessed DOI (10.2165/00007256-200333140-00003)
  was checked and rejected — it resolved to an unrelated paper (Taha &
  Thomas 2003), confirming the verification step catches a wrong guess.
- A published, phase-resolved (base/build/peak/taper) time-in-zone TID table
  specific to half-Ironman or Ironman training — `triathlon_long_course.md`
  §5 gives only an overall-macrocycle range per distance; the `tid_shares`
  band above extrapolates the marathon phase table's shape onto that range
  rather than citing a phase-resolved long-course source directly.
- A library/publisher-confirmed ISBN for Maffetone's "The Big Book of
  Endurance Training and Racing" (search result only) and a single canonical
  book with a verifiable ISBN for the "reverse periodization" school.
- A Crossref-verified DOI for Laursen & Jenkins (2002), Mujika & Padilla
  (2003) and Issurin (2010) — all three are already DOI-cited in
  `docs/research/endurance_methodology.md`; reused here on that doc's
  citation rather than independently re-fetched (marked `verified: no`
  above per the letter of the instruction: only DOIs fetched for this dossier
  are marked `verified: crossref`).

## Sources register

| Source | Type | Verified |
|---|---|---|
| Seiler & Kjerland 2006, DOI 10.1111/j.1600-0838.2004.00418.x | paper | crossref |
| Seiler 2010, DOI 10.1123/ijspp.5.3.276 | paper | crossref |
| Stöggl & Sperlich 2014, DOI 10.3389/fphys.2014.00033 | paper | crossref |
| Esteve-Lanao et al. 2007, DOI 10.1519/R-19725.1 | paper | crossref |
| Muñoz et al. 2014, DOI 10.1123/ijspp.2012-0352 | paper | crossref |
| Kenneally et al. 2018, DOI 10.1123/ijspp.2017-0327 | paper | crossref |
| Casado et al. 2022, DOI 10.1123/ijspp.2021-0435 | paper | crossref |
| Treff et al. 2019, DOI 10.3389/fphys.2019.00707 | paper | crossref |
| Burnley, Bearden & Jones 2022, DOI 10.1249/MSS.0000000000002869 | paper | crossref |
| Maunder et al. 2021, DOI 10.1007/s40279-021-01459-0 | paper | crossref (used for context only, not cited in a rule above) |
| Laursen & Jenkins 2002, DOI 10.2165/00007256-200232010-00003 | paper | no (reused from repo doc, not independently re-fetched) |
| Mujika & Padilla 2003, DOI 10.1249/01.MSS.0000074448.73931.11 | paper | no (reused from repo doc) |
| Issurin 2010, DOI 10.2165/11319770-000000000-00000 | paper | no (reused from repo doc) |
| Bompa & Haff 2009, ISBN 978-0-7360-7483-4 | book | isbn |
| Daniels, Daniels' Running Formula 4th ed., ISBN 978-1-718-20366-2 | book | isbn |
| Friel, The Triathlete's Training Bible 5th ed., ISBN 978-1-646046-07-2 | book | isbn |
| Allen, Coggan & McGregor, Training and Racing with a Power Meter 3rd ed., ISBN 978-1-937715-93-9 | book | isbn |
| Dixon, Fast-Track Triathlete, ISBN 978-1-937715-74-8 | book | isbn |
| Lydiard & Gilmour, Running to the Top, ISBN 978-1-84126-335-9 | book | isbn |
| Hudson & Fitzgerald, Run Faster from the 5K to the Marathon, ISBN 978-0-7679-2822-9 | book | isbn |
| Magness, The Science of Running, ISBN 978-0-615-94294-0 | book | isbn |
| Maffetone, The Big Book of Endurance Training and Racing, ISBN 978-1-60094-345-0 | book | no (search result only, not confirmed against publisher/library page) |
| Friel & Byrn, Going Long 2nd ed. | book | no (reverse-periodization attribution, ISBN not pinned down) |
