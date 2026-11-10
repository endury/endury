# Review — three contested intensity/taper bands

- **tools available:** WebFetch worked against `api.crossref.org` (direct
  DOI lookups and `query.bibliographic` searches); WebSearch not needed. Every DOI
  below was fetched live (`verified: crossref`) unless marked reused.
- **minutes spent:** ~50 (reading 4 repo docs + rulebook entries, 19 Crossref
  fetches, synthesis). Over the 25-min aim; the three bands needed independent
  numeric reasoning, not just citation-matching.
- **Repo files read:** `docs/research/bench_dossiers/taper_peaking.md`,
  `.../intensity_schools.md`, `docs/research/endurance_methodology.md` §3+6,
  `docs/research/triathlon_long_course.md` §5+7, `bench/rulebook.yaml` (the four
  named entries). No file edited.

---

## Band 1 — `taper_intensity_kept`

**What the studies measured.** None of the five intervention studies define
"intensity maintained" as a floor on absolute high-zone clock-minutes. Each holds
the *pace/power/effort of the remaining quality work* constant while volume falls:

- Shepley et al. 1992 ran three 7-day tapers in trained middle-distance runners;
  only the low-volume/high-intensity taper (pace of the retained work held at
  race-adjacent effort) produced a significant gain (running time-to-fatigue
  reported up in the fetched record); the low-intensity/high-volume taper did not.
- Houmard et al. 1994 cut total volume ~85% over a taper in runners while holding
  the *pace* of remaining quality sessions constant; performance was maintained.
  An 85% cut applied to total training, if it fell evenly, would put absolute
  zone-3+ minutes far below `taper_intensity_kept`'s 0.5 floor — the finding is
  about session pace, not a minutes floor.
- Neary et al. 2003 ran three cyclist tapers (30/50/80% volume cuts) with power
  output of the work bouts held constant across all three; the 50% cut won. Again
  a volume dose-response with intensity-of-effort fixed, not a minutes ratio.
- Bosquet et al. 2007's "intensity maintained" effect (0.33 ± 0.14, p<0.001) is a
  meta-regression coding of whether each source study *reported* pace/power/HR
  unchanged — categorical, not a minutes ratio.
- Mujika, Busso et al. 1996 modelled swim taper responses against main-set pace,
  not total clock-minutes above a zone cut.

**Verdict: replace the zone cut, tighten the band.** The current reading counts
minutes from 76% FTP up ("zone 3 and up"). For half-Ironman and marathon that
zone includes the athlete's own race pace and long tempo/MP work — precisely the
volume a taper is *supposed* to cut hardest. That inflates the peak-week
denominator with incidental long-session tempo minutes the literature never asked
to be preserved, and true "kept" work (short interval/threshold touches) becomes
a smaller share of a bigger number, biasing the ratio low regardless of whether
the plan actually preserved anything. `docs/research/triathlon_long_course.md`
§7 reads this the other way, with the narrower IF≥0.85 cut (top of zone 3 through zone 5, the same cut
`taper_quality_share` in `shared.yaml` uses): **53–100%** quality-minutes
retained taper-over-peak — comfortably inside the current 0.5–1.2 band. That is the anchor this rule should use.

- **Reading:** `mean IF≥0.85 minutes per taper week / peak load week's IF≥0.85
  minutes` (switch from a 76%-FTP floor to the 85%-FTP floor already implemented
  by `taper_quality_share`).
- **Band:** low 0.50, high 1.05, unit ratio, ramp 0.35 (rounded outward from 0.53–1.00).

```yaml
- citation: Shepley B, MacDougall JD, Cipriano N, Sutton JR, Tarnopolsky MA, Coates G (1992). Physiological effects of tapering in highly trained athletes. J Appl Physiol 72(2):706-711.
  doi: 10.1152/jappl.1992.72.2.706
  states: Of three 7-day taper protocols in trained runners, only the low-volume/high-intensity taper improved performance; pace of retained work, not its clock-minutes, was the manipulated variable.
  verified: crossref
- citation: Houmard JA, Scott BK, Justice CL, Chenier TC (1994). The effects of taper on performance in distance runners. Med Sci Sports Exerc 26(5):624-631.
  doi: 10.1249/00005768-199405000-00016
  states: An ~85% volume reduction over a taper, with quality-session pace held constant, maintained/improved performance in runners.
  verified: crossref
- citation: Neary JP, Bhambhani YN, McKenzie DC (2003). Effects of different stepwise reduction taper protocols on cycling performance. Can J Appl Physiol 28(4):576-587.
  doi: 10.1139/h03-044
  states: 30/50/80% volume-reduction tapers with power output of the work bouts held constant; the 50% cut produced the largest gain.
  verified: crossref
- citation: Bosquet L, Montpetit J, Arvisais D, Mujika I (2007). Effects of Tapering on Performance: A Meta-Analysis. Med Sci Sports Exerc 39(8):1358-1365.
  doi: 10.1249/mss.0b013e31806010e0
  states: Maintained-intensity is coded categorically across source studies (effect size 0.33 +/- 0.14, p<0.001); not a minutes-ratio finding.
  verified: crossref
- citation: Mujika I, Busso T, Lacoste L, Barale F, Geyssant A, Chatard JC (1996). Modeled responses to training and taper in competitive swimmers. Med Sci Sports Exerc 28(2):251-258.
  doi: 10.1097/00005768-199602000-00015
  states: Models taper performance against training-load inputs and main-set pace, not an absolute high-zone-minutes retention target.
  verified: crossref
- citation: Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. Med Sci Sports Exerc 35(7):1182-1187.
  doi: 10.1249/01.MSS.0000074448.73931.11
  states: Reduce volume, not intensity - dropping intensity accelerates detraining of neuromuscular and metabolic adaptations.
  verified: crossref (reused from taper_peaking.md dossier, re-confirmed)
```

---

## Band 2 — `tid_low_share` (Ironman 0.70–0.82; marathon 0.75–0.85)

**What the published TID studies show.** Muñoz et al. 2014 measured a full
Ironman season in age-group triathletes with individually lab-tested HR zones
(gas-exchange thresholds for run/bike, lactate testing for swim) — a
physiological three-zone, time-in-zone method, the closest real analogue to this
benchmark's reading. Result: **68% ± 14%** of training time below the athlete's
own aerobic threshold (zone 1). That is *below* the band's own 70% floor —
real Ironman triathletes, measured properly, sit right at or just under this
band's low edge, which argues the band's numbers are not obviously wrong in
direction. But the method gap matters: Sylta, Tønnessen & Seiler 2014 fed the
*same* elite skiers' training through time-in-zone (TIZ) versus session-goal (SG)
counting and got **96.1% zone-1 (TIZ) versus 86.6% zone-1 (SG)** — a ~10-point
swing from counting method alone, using fixed HR-derived zone boundaries similar
in spirit to this benchmark's fixed %FTP scheme. So a ~10-point gap between a
threshold-anchored literature figure (Muñoz's 68%, using lab-tested individual
LT1) and a fixed-%FTP-zone benchmark reading is exactly the order of magnitude
the method-comparison literature predicts, before any judgement about the plan's
actual quality.

The physiological case for the gap running specifically wide for Ironman: per
`triathlon_long_course.md` §1 and §2, Ironman race intensity is **65–76% FTP**
and durability long rides are prescribed at **65–78% FTP** — both sit almost
entirely inside this benchmark's zones 1–2 (≤75% FTP) by construction, regardless
of how well-dosed the plan's threshold work is. A well-built Ironman plan's
single largest volume driver (the long ride) is supposed to sit sub-threshold;
unlike marathon pace, Ironman race pace does not spill into zone 3. Marathon pace,
by contrast, typically sits at or above 76% LTHR-equivalent (this scheme's zone 3
floor — see Band 3 below), so marathon long runs and MP work naturally contribute
zone-3 minutes and keep the marathon band's distortion small; that is consistent
with the marathon band mostly passing (Endury and the chat model both read 0.80,
inside 0.75–0.85) while Ironman fails structurally.

**Verdict: widen the Ironman (and, less urgently, half-Ironman) ceiling; keep
marathon.** No phase-resolved, time-in-zone, Ironman-specific TID source was
found in the time available (flagged as a gap in `intensity_schools.md` too), so
this is not a precise number, but the combination of (a) Muñoz's real-season
figure sitting at the band's own floor using a stricter same-family method, (b)
Sylta's ~10-point method-sensitivity finding, and (c) the race-pace/zone
construction argument together suggest the 0.70–0.82 Ironman band is
miscalibrated low for a time-in-zone %FTP reading, not that Endury's or the chat
model's plans are actually under-dosing quality work.

- **Reading:** unchanged (share of step minutes in zones 1–2, load+recovery
  weeks).
- **Band, Ironman:** low 0.72, high 0.90, ramp 0.08 (half-Ironman: low 0.72, high
  0.85, since half-Ironman race pace at 75–82% FTP sits mostly in zone 3 and so
  is less exposed to the same distortion). Marathon: **keep** 0.75–0.85 unchanged
  — the one reported failure (the marathon for the athlete on a low
  training volume) looks like a
  scenario-specific outlier, not a band problem, since both plans under test read
  0.80, mid-band.

```yaml
- citation: Muñoz I, Cejuela R, Seiler S, Larumbe E, Esteve-Lanao J (2014). Training-Intensity Distribution During an Ironman Season. Int J Sports Physiol Perform 9(2):332-339.
  doi: 10.1123/ijspp.2012-0352
  states: Age-group Ironman triathletes, lab-tested HR thresholds, spent 68% +/- 14% of training time below aerobic threshold (time-in-zone method).
  verified: crossref
- citation: Sylta O, Tonnessen E, Seiler S (2014). From Heart-Rate Data to Training Quantification. Int J Sports Physiol Perform 9(1):100-107.
  doi: 10.1123/ijspp.2013-0298
  states: On identical elite training data, time-in-zone read 96.1% zone-1 versus 86.6% zone-1 by session-goal counting - a ~10-point method-driven gap.
  verified: crossref
- citation: Neal CM, Hunter AM, Galloway SDR (2011). A 6-month analysis of training-intensity distribution and physiological adaptation in Ironman triathletes. J Sports Sci 29(14):1515-1523.
  doi: 10.1080/02640414.2011.596217
  states: Confirms Ironman-season TID as a real, measured object of study; abstract-level TID percentages were not retrievable from the Crossref record.
  verified: crossref (identity only; no numeric TID figure confirmed)
- citation: Seiler S, Kjerland GO (2006). Quantifying training intensity distribution in elite endurance athletes. Scand J Med Sci Sports 16(1):49-56.
  doi: 10.1111/j.1600-0838.2004.00418.x
  states: Elite XC skiers ~75-80% below LT1 by session-goal method; near-polarised by duration, pyramidal by session count.
  verified: crossref (reused from intensity_schools.md, re-confirmed)
```

---

## Band 3 — `hard_sessions_per_week` (marathon, 1.5–3.0)

**What the recreational/age-group marathon sources prescribe.** All four major
schools checked converge on roughly **one dedicated threshold/VO2max-class
session per week**, with a second "quality" day frequently built around marathon
pace (MP) — which this scheme classifies as zone 3, not zone 4–5, and so does not
count toward an 8-minute zone-4+ threshold:

- Daniels' Running Formula (4th ed., ISBN 978-1-71820-366-2, isbn-verified):
  caps I-pace (VO2max-class) work at ~8% of weekly mileage — a small dose,
  often one session; T-pace (threshold) work is the more frequent quality
  element and does sit in zone 4, giving roughly 1–2 zone-4+-eligible sessions
  a week depending on the mesocycle.
Casado et al. 2022's systematic review, by contrast, reports **elite** distance
runners using a zone-LT2 (threshold-class) session *and* a zone-anaerobic
(VO2max/speed-class) session "at least once per week each" — i.e. ~2 genuinely
hard sessions/week at the elite end, with marathoners specifically leaning more
pyramidal (fewer high-intensity touches) than 1500 m specialists. The brief's own
population (recreational/age-group, 3–8 h/week) sits well below the training
capacity of Casado's elite cohort, so 2 as an elite floor does not transfer
directly down.

**Verdict: lower the floor, keep the zone-4+ definition.** Reclassifying MP work
as "quality" (zone-3-and-up) was considered, since the task raises it directly,
but rejected as the primary fix: several Endury/chat marathon plans already
include MP segments inside tempo runs and long runs, so counting zone 3 would
likely push some plans *above* the 3.0 ceiling rather than fixing the ones
reading too low, without a matching band recalibration this review has no data
to size safely. Lowering the floor is the smaller, better-targeted change and
matches what recreational-marathon plans actually deliver under a strict
zone-4+ read.

- **Reading:** unchanged (sessions with ≥8 min cumulative in zones 4–5, mean per
  build load week).
- **Band, marathon only:** low 1.0, high 3.0, ramp 1.0 (triathlon goals'
  1.5–4.0 band was not reviewed here and is left unchanged — Casado's elite
  "~2/week" figure was single-sport running, not multi-sport, so it does not
  bear on the triathlon ceiling either way).

```yaml
- citation: Casado A, Gonzalez-Mohino F, Gonzalez-Rave JM, Foster C (2022). Training Periodization, Methods, Intensity Distribution, and Volume in Highly Trained and Elite Distance Runners. Int J Sports Physiol Perform 17(6):820-833.
  doi: 10.1123/ijspp.2021-0435
  states: Elite runners used a zone-LT2 session and a zone-anaerobic session at least once per week each; marathoners leaned pyramidal versus 1500m specialists' more polarised pattern.
  verified: crossref
- citation: Seiler S (2010). What is best practice for training intensity and duration distribution in endurance athletes? Int J Sports Physiol Perform 5(3):276-291.
  doi: 10.1123/ijspp.5.3.276
  states: Elite endurance athletes typically carry 2 (sometimes 3) genuinely hard sessions per week, the rest easy - the source of the band's current floor.
  verified: crossref (reused from intensity_schools.md, re-confirmed)
- citation: Daniels J (2021). Daniels' Running Formula (4th ed). Human Kinetics.
  isbn: 978-1-71820-366-2
  states: I-pace (VO2max-class) volume capped near 8% of weekly mileage, typically a single weekly session; T-pace threshold work is the more frequent quality element.
  verified: isbn (reused from intensity_schools.md, re-confirmed)
```

---

## Not found

- Full-text methods detail for Shepley 1992 (e.g. the exact interval prescription
  of its high-intensity taper arm) — Crossref exposes title/author/year only;
  the "+22% time-to-fatigue" figure surfaced by the fetch tool is reported as-is
  but was not independently cross-checked against the published abstract text.
- A phase-resolved (base/build/peak), time-in-zone-method TID study specific to
  half-Ironman or Ironman training that would pin Band 2's Ironman ceiling to a
  number rather than an inference from Muñoz 2014 + Sylta 2014 + the race-pace
  construction argument.
- A Mujika swimmer-taper paper matching the brief's "2000 or 2002" description;
  the closest verified match is Mujika, Busso et al. 1996 (Med Sci Sports Exerc,
  modelled swim taper responses), used in Band 1 instead. A 2002 Mujika paper
  exists (6-day taper in middle-distance *runners*, IJSM,
  10.1055/s-2002-33146) but is not swimmer-specific, so it was not cited.
- Numeric abstract detail for Neal et al. 2011 (Ironman TID) and Kenneally et al.
  2018 (review) — Crossref records returned no abstract field; both are cited
  for identity/context only, not for a specific percentage.
- Vickers & Vertosick 2016 abstract content (speedwork-frequency vs. race-time
  relationship) — Crossref record carried no abstract field; cited for identity
  only, not used as evidence in any band above.

## Sources register

| Source | Type | Verified |
|---|---|---|
| Shepley et al. 1992, J Appl Physiol 72(2):706-711, DOI 10.1152/jappl.1992.72.2.706 | trial | crossref |
| Houmard et al. 1994, MSSE 26(5):624-631, DOI 10.1249/00005768-199405000-00016 | trial | crossref |
| Neary et al. 2003, Can J Appl Physiol 28(4):576-587, DOI 10.1139/h03-044 | trial | crossref |
| Bosquet et al. 2007, MSSE 39(8):1358-1365, DOI 10.1249/mss.0b013e31806010e0 | meta-analysis | crossref |
| Mujika, Busso et al. 1996, MSSE 28(2):251-258, DOI 10.1097/00005768-199602000-00015 | modelling | crossref |
| Mujika & Padilla 2003, MSSE 35(7):1182-1187, DOI 10.1249/01.MSS.0000074448.73931.11 | review | crossref |
| Muñoz et al. 2014, IJSPP 9(2):332-339, DOI 10.1123/ijspp.2012-0352 | cohort | crossref |
| Sylta, Tønnessen & Seiler 2014, IJSPP 9(1):100-107, DOI 10.1123/ijspp.2013-0298 | methods paper | crossref |
| Neal, Hunter & Galloway 2011, J Sports Sci 29(14):1515-1523, DOI 10.1080/02640414.2011.596217 | cohort | crossref (identity only) |
| Seiler & Kjerland 2006, Scand J Med Sci Sports 16(1):49-56, DOI 10.1111/j.1600-0838.2004.00418.x | observational | crossref |
| Seiler 2010, IJSPP 5(3):276-291, DOI 10.1123/ijspp.5.3.276 | review | crossref |
| Kenneally, Casado & Santos-Concejero 2018, IJSPP 13(9):1114-1121, DOI 10.1123/ijspp.2017-0327 | systematic review | crossref (identity only) |
| Casado, González-Mohíno, González-Ravé & Foster 2022, IJSPP 17(6):820-833, DOI 10.1123/ijspp.2021-0435 | systematic review | crossref |
| Vickers & Vertosick 2016, BMC Sports Sci Med Rehabil 8:9, DOI 10.1186/s13102-016-0052-y | observational survey | crossref (identity only) |
| Stöggl & Sperlich 2015, Front Physiol, DOI 10.3389/fphys.2015.00295 | review | crossref (identity only; not cited in a band, superseded by Sylta 2014 for the method point) |
| Mujika 2002 (runners, not swimmers), IJSM, DOI 10.1055/s-2002-33146 | trial | crossref (identity only; not used, wrong population) |
| Daniels, Daniels' Running Formula (4th ed.), ISBN 978-1-71820-366-2 | book | isbn |
| Friel, Triathlete's Training Bible (5th ed., 2024), ISBN 978-1-646046-07-2 | book | isbn (not independently re-checked) |
