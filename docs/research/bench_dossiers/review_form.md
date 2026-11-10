# Review: race_day_form and form_floor

- **tools available:** WebFetch and WebSearch both worked. 9 Crossref fetches
  succeeded first try. One ResearchGate WebFetch returned HTTP 403; noted below.
- **minutes spent:** ~55 (arithmetic simulation against `bench/load.py`'s own
  constants, 6 WebSearch rounds, 9 crossref verifications, 5 targeted fetches)

All arithmetic reuses `CTL_DAYS=42`, `ATL_DAYS=7` and the exact EMA recursion
from `bench/load.py::trajectory`, run standalone against a steady pre-taper
load equal to fitness (`ctl=atl=F`) — reproducible from the repo's own model.

---

## Band 1 — `race_day_form` (bench/rulebook.yaml:452)

### Arithmetic

`CTL_DECAY=exp(-1/42)=0.976472`, `ATL_DECAY=exp(-1/7)=0.866878`. Simulating a
Bosquet-compliant taper (41–60% volume cut, maintained intensity) from
`ctl=atl=F`, read at day 14:

| F | cut 41% | cut 50% | cut 60% | TSB/F range |
|---|---|---|---|---|
| 88 | 21.0 | 25.6 | 30.7 | 0.238–0.349 |
| 52 | 12.4 | 15.1 | 18.1 | 0.238–0.349 |
| 36 | 8.6 | 10.5 | 12.6 | 0.238–0.349 |

Confirms the brief's arithmetic almost exactly. Two further checks: (1) peak
TSB isn't reached until ~day 15 regardless of retention, and **even
complete cessation** (retention 0 — itself violating the frequency/intensity
evidence below) tops out at TSB/F=0.58 for F=88; reaching absolute +35
(Endury's Ironman) needs a 68–78% cut sustained 14–21 days — over-tapering,
not under-tapering. (2) **F=52 cannot reach +39 (the chat model's reading)
at any retention/duration tested** — zero training for 21 days peaks near 29
— implying the pre-taper ATL was already below CTL, not a taper effect.
F=36 needs a 72–82% cut to reach even the band's own +15 floor. **The
absolute band is unreachable by a literature-compliant taper below roughly
CTL 60–70, and over-reachable only by tapering harder than evidence
recommends.**

### (a) Origin of +15/+25

Traces most directly to Joe Friel, not a peer-reviewed source. The
TrainingPeaks-hosted "Applying the Numbers Part 3: TSB" article is
Friel-authored and states +15 to +25 as the author's own observation, a rough guide that
depends on the athlete and on which CTL/ATL constants are used — no fitness
range or normalisation stated. TrainingPeaks' own separate explainer ("The
Science of the Performance Manager") gives *different, looser* numbers
(below −10 not fresh, above +10 usually fresh, −10..+10 neutral) and never
states +15/+25 or −30. Other coaching sources repeating "the convention"
disagree with each other (+10/+25; +5/+25; 0/+20; −5/+20) — evidence this is
a loosely-transmitted heuristic, not one canonical figure. The repo's own
dossier attributes it to Allen/Coggan/McGregor's book (ISBN verified); no web
excerpt of the book's own page text was found to confirm wording beyond that
existing citation. Everywhere found, it is stated as **absolute TSS-points,
for no stated fitness range, with no CTL normalisation**.

### (b) Peer-reviewed link to a numeric TSB or TSB/CTL target

**None found.** Busso (2003) and Thomas & Busso (2005, and 2008 — located
and verified below; the repo's dossier had marked 2008 "not found") optimize
*simulated performance directly* from fitted parameters; they report optimal
load-reduction magnitude and taper duration, never a target TSB value. Le
Meur et al. (2013, crossref-verified) detects overreaching via
physiological/hormonal/cognitive markers, not any PMC number, and reports no
TSB values. No case series of real athletes' CTL/ATL/TSB preceding a
best-ever endurance performance was located. Skiba's published alternative
(W′-balance) models intra-workout anaerobic depletion — a different
construct, no TSB-threshold alternative. No Coggan white paper giving a
derivation (vs. a restated convention) was found.

### (c) Ratio vs. absolute

My mechanical ratio (0.238–0.349 at day 14) sits almost entirely inside
Endury's **already-implemented** `race_day_freshness_ratio` rule
(`bench/rulebook.yaml`: 0.16–0.47 fallback; 0.30–0.47 ironman/marathon;
0.16–0.39 half_ironman, per `taper_peaking.md`). A ratio band is better
supported and doesn't penalise low-CTL athletes the way the absolute band
structurally does — but Endury doesn't need a *second* one.

### Verdict: **demote to a reported fact**

The absolute convention isn't peer-reviewed, isn't stated for any fitness
range, and is mechanically unreachable by a compliant taper outside a narrow
CTL band — the exact failure the benchmark observed. The ratio rule that
should replace it already exists and is scored (`race_day_freshness_ratio`);
a second rule duplicating it is redundant. Keep the raw TSB number as an
unscored diagnostic only.

```yaml
sources:
  - citation: "Bosquet L et al. (2007). Effects of Tapering on Performance: A Meta-Analysis. MSSE 39(8):1358-1365."
    doi: 10.1249/mss.0b013e31806010e0
    states: "41-60% volume cut is the meta-analytic optimum; basis for the taper simulated above."
    verified: crossref
  - citation: "Mujika I, Padilla S (2003). Scientific Bases for Precompetition Tapering Strategies. MSSE 35(7):1182-1187."
    doi: 10.1249/01.MSS.0000074448.73931.11
    states: "Independently converges on 40-60% volume reduction, ~2-week taper duration."
    verified: crossref
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed.). VeloPress."
    isbn: "978-1-937715-93-9"
    states: "Repo-cited source of the +15/+25 convention; exact page wording not independently confirmed."
    verified: isbn
  - citation: "Busso T (2003). Variable Dose-Response Relationship. MSSE 35(7):1188-1195."
    doi: 10.1249/01.MSS.0000074465.13621.37
    states: "Refines fitness-fatigue dose-response; optimizes performance directly, no TSB target given."
    verified: crossref
  - citation: "Thomas L, Busso T (2005). A Theoretical Study of Taper Characteristics. MSSE 37(9):1615-1621."
    doi: 10.1249/01.mss.0000177461.94156.4b
    states: "Favors fast exponential taper decay (~5-day constant); no numeric TSB target reported."
    verified: crossref
  - citation: "Thomas L, Mujika I, Busso T (2008). Optimal Training Reduction in Pre-Event Taper, Elite Swimmers. J Sports Sci 26(6)."
    doi: 10.1080/02640410701716782
    states: "Per-swimmer optimal load reduction from fitted dose-response; no TSB checkpoint value."
    verified: crossref
  - citation: "Le Meur Y et al. (2013). Multidisciplinary Overreaching Detection in Endurance Athletes. J Appl Physiol 114(3):411-420."
    doi: 10.1152/japplphysiol.01254.2012
    states: "Detects overreaching via performance/hormonal/cognitive markers, not a TSB threshold."
    verified: crossref
  - citation: "Friel J. Applying the Numbers Part 3: Training Stress Balance (TrainingPeaks-hosted; joefrieltraining.com)."
    states: "States +15 to +25 as his own observation, explicitly not to be applied too literally."
    verified: "web fetch, no doi/isbn"
```

---

## Band 2 — `form_floor` (bench/rulebook.yaml:472)

### Evidence

`bench/load.py`'s `min_tsb` reads one calendar day's entering-form, not a
window. The rulebook's own note concedes −30 is "a convention" and only
"sustained negative balance" is consensus. The Ironman example (−41 after
one 5.5h ride + 2.5h run weekend, F≈88) is by construction a single-day
trough after one hard weekend.

### (a) Source and meaning of −30

Same Friel material as Band 1: "negative 10 to negative 30... during hard,
focused training" is his *productive* range; "below negative 30 greatly
increases your risk of injury or illness" — with **no duration qualifier**
separating one extreme day from weeks spent there. Independent coaching
sources (a TrainerRoad forum thread, cycling-coaching blogs) describe **−10
to −30 as the "Optimal Training Zone" itself** — where "the most effective
training occurs" — i.e. −30 is conventionally the floor of *productive*
loading, not a never-touch line. The ambiguity the task poses is unresolved
in the primary popular source itself.

### (b) Published PMC data on typical minima in big weeks

No case series giving typical minimum daily TSB in trained
triathletes'/marathoners' peak weeks was located and verified. Two
candidate triathlon load-distribution studies surfaced by name in search but
weren't confirmed to report TSB minima — listed under Not Found, not cited.
The closest verified, relevant source is professional grand-tour load
research (Sanders et al. 2017, DOI verified) studying multi-week
intensity/load ratios in elite riders performing throughout a sustained
negative-balance period — but a ResearchGate fetch for its specific numbers
returned HTTP 403; its numbers are Not Found, only its relevance verified.

### (c) Overreaching consensus mapping

Meeusen et al. (2013, DOI verified) defines FOR→NFOR→OTS by **duration and
planning**, not by any load number: FOR is a short, planned high-load block
with days-to-~2-week recovery and an expected rebound; NFOR/OTS require
weeks-to-months of *sustained*, unplanned decrement. This is exactly what
the Ironman example collapses: one big weekend at −41 is textbook FOR, which
the consensus statement treats as productive, not the pathology a daily-min
rule scores it as. Halson (2014, DOI verified) reviews load-monitoring
markers broadly and gives **no numeric TSB/CTL/ATL threshold** anywhere in
the text fetched — no reviewed monitoring literature anchors −30 as a number.

### Verdict: **change to a multi-day mean, same threshold number**

Keep −30 (a repeatedly-cited convention, nothing found contradicts it as a
floor for a *sustained* state) but change `min_tsb` from a single calendar
day to a rolling window, so one hard weekend's FOR spike stops tripping a
rule meant for NFOR/OTS.

```yaml
proposed_rule: form_floor
reading: "min 7-day rolling mean of daily TSB (that day + preceding 6)"
band: {low: -30, high: null, unit: "daily TSS-equivalent points, 7-day mean", ramp: 15.0}
note: >-
  Same -30 line; ramp tightened 20->15 since a 7-day mean is already
  smoothed. Starting proposal, not recalibrated --
  no day-by-day trajectory for the cited Ironman example was available here.
```

```yaml
sources:
  - citation: "Allen H, Coggan A, McGregor S (2019). Training and Racing with a Power Meter (3rd ed.). VeloPress."
    isbn: "978-1-937715-93-9"
    states: "Repo-cited source of -30 as conventional floor of productive overload before ending a block."
    verified: isbn
  - citation: "Meeusen R et al. (2013). Prevention, Diagnosis and Treatment of Overtraining Syndrome. Eur J Sport Sci 13(1):1-24."
    doi: 10.1080/17461391.2012.730061
    states: "Defines FOR/NFOR/OTS by duration and planning, not a load-metric number; no PMC cutoff given."
    verified: crossref
  - citation: "Halson SL (2014). Monitoring Training Load to Understand Fatigue in Athletes. Sports Med 44(Suppl 2):139-147."
    doi: 10.1007/s40279-014-0253-z
    states: "Reviews load-monitoring markers broadly; gives no numeric TSB/CTL/ATL overreaching threshold."
    verified: crossref
  - citation: "Sanders D et al. (2017). Analysing a Cycling Grand Tour: Monitoring Fatigue with Intensity/Load Ratios. J Sports Sci."
    doi: 10.1080/02640414.2017.1388669
    states: "Studies sustained multi-week load/fatigue ratios in grand-tour pros; specific TSB numbers not retrieved."
    verified: crossref
  - citation: "Friel J. Managing Training Using TSB (joefrieltraining.com; TrainingPeaks-hosted)."
    states: "Frames -10..-30 as the productive hard-training range, below -30 as elevated risk, no duration qualifier."
    verified: "web fetch, no doi/isbn"
```

---

## Not found

- Exact page text of Allen/Coggan/McGregor (2019) stating +15/+25 or −30.
- A peer-reviewed case series of real athletes' CTL/ATL/TSB before a
  best-ever marathon or Ironman performance.
- Numeric TSB minima from Sanders et al. (2017) — DOI verified, ResearchGate
  copy returned HTTP 403, no alternate full text reached in time.
- TSB-specific content from triathlon intensity-distribution studies
  surfaced by name in search — not verified, not cited.
- A peer-reviewed TSB/CTL ratio threshold (matches the repo's own
  `taper_peaking.md` gap for `race_day_freshness_ratio`).
- A Skiba metric comparable to TSB — it targets intra-workout W′ depletion,
  a different construct.

## Sources register

| Source | Type | Verified |
|---|---|---|
| Bosquet et al. 2007, MSSE 39(8):1358-1365, DOI 10.1249/mss.0b013e31806010e0 | meta-analysis | crossref |
| Mujika & Padilla 2003, MSSE 35(7):1182-1187, DOI 10.1249/01.MSS.0000074448.73931.11 | review | crossref |
| Busso 2003, MSSE 35(7):1188-1195, DOI 10.1249/01.MSS.0000074465.13621.37 | modelling | crossref |
| Thomas & Busso 2005, MSSE 37(9):1615-1621, DOI 10.1249/01.mss.0000177461.94156.4b | modelling | crossref |
| Thomas, Mujika & Busso 2008, J Sports Sci 26(6), DOI 10.1080/02640410701716782 | modelling | crossref |
| Le Meur et al. 2013, J Appl Physiol 114(3):411-420, DOI 10.1152/japplphysiol.01254.2012 | observational | crossref |
| Meeusen et al. 2013, Eur J Sport Sci 13(1):1-24, DOI 10.1080/17461391.2012.730061 | consensus statement | crossref |
| Halson 2014, Sports Med 44(Suppl 2):139-147, DOI 10.1007/s40279-014-0253-z | review | crossref |
| Sanders et al. 2017, J Sports Sci, DOI 10.1080/02640414.2017.1388669 | observational | crossref |
| Allen, Coggan & McGregor 2019, *Training and Racing with a Power Meter* (3rd ed.), VeloPress, ISBN 978-1-937715-93-9 | book/convention | isbn |
| Friel, "Applying the Numbers Part 3: TSB" / "Managing Training Using TSB" | coaching blog, no DOI/ISBN | web fetch |
