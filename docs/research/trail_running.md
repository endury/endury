# Trail and Ultramarathon — 20K, 50K, 100K, 100M

Distance-specific training methodology for the four UTMB race categories.
Cited from planner session menus as **`tr §N`** (the same convention by which
menus cite `triathlon_long_course.md` as `tlc §N` and
`endurance_methodology.md` as `em §N`).

This document builds on:

- [endurance_methodology.md](endurance_methodology.md) — periodization,
  intensity distribution, fitness modelling, taper, strength, overtraining.
- [triathlon_long_course.md](triathlon_long_course.md) — durability, the
  multi-hour-event framing, and the measured-reference-plan taper analysis,
  all of which transfer to ultra distance.

Only topics requiring trail/ultra-specific treatment are covered here: the
km-effort race taxonomy, vertical gain, power-hiking, eccentric/downhill
conditioning, back-to-back long runs, time-on-feet long-run prescription, and
the per-category planning parameters.

### Epistemic register

Ultra training literature is thin where it matters most for a deterministic
planner. Every quantitative claim below carries its tier inline:

| Tier | Meaning |
|------|---------|
| **[PR]** | Peer-reviewed (or a peer-reviewed source read via a citing secondary) |
| **[PC]** | Practitioner consensus — several independent coaching sources agree |
| **[SC]** | Single-coach opinion — one named coach's stated heuristic, self-described as judgement |
| **[EX]** | Extrapolation by this document — no source states it; the bridge is shown |

Do not launder **[SC]** figures into planner constants as though they were
measured. Several of the most planner-usable numbers in this document (the
long-run ladder, the back-to-back cadence, the weekly-hours floors) are
**[SC]** — one coach, no trial, no control group. They are the best available,
and they are still one person's opinion.

---

## 1. Race Categories and the km-effort Metric

UTMB classifies every World Series race by *km-effort*, which folds vertical
gain into an equivalent horizontal distance **[PC]**:

```
km-effort = distance_km + (elevation_gain_m / 100)
```

A race must exceed 20 km-effort to be classified at all. UTMB's own worked
example: 50 km with 2,500 m gain = 75 km-effort **[PC]**.

| Category | km-effort band | Typical race | Stones (standard / Major) |
|----------|---------------|--------------|---------------------------|
| **20K** | > 20, < 50 | 25–45 km trail race, 1,000–2,500 m D+ | 1 / 2 |
| **50K** | 50 – 100 | OCC: 57 km, 3,500 m D+ → 92 | 2 / 4 |
| **100K** | 100 – 170 | CCC: 101 km, 6,050 m D+ → 161.5 | 3 / 6 |
| **100M** | ≥ 170 | UTMB: 171.5 km, 9,915 m D+ → 270.6 | 4 / 8 |

> **Boundary caveat.** UTMB publishes the formula, the four labels, and the
> 20 km-effort floor — but *not* the numeric cut points between categories.
> Neither utmb.world/sports-system nor the Wikipedia UTMB Index page states a
> `category → [min, max)` table. The bands above are the planner's working
> assumption; they are internally consistent with UTMB's 75 km-effort worked
> example landing in the 50K band, but they are **not textually corroborated**
> **[EX]**. If UTMB later publishes them and they differ, every downstream
> parameter in §13 shifts.

Two consequences for a planner. First, km-effort is the right axis: a 45 km
race with 4,000 m of gain (85 km-effort) is a 50K-category problem, not a
marathon-plus. Second, category is a *demand* label, not a distance — the same
category spans a 2:1 range of finish times at its edges (§13.1).

Structural break point: training approaches are broadly similar up to 100K, and
change qualitatively at 100 miles — specifically in the need for trained
power-hiking rather than running fitness, and for genuine time-on-feet
preparation **[SC]**. The planner's phase and session logic should treat
20K/50K/100K as a scaling family and 100M as a different shape.

---

## 2. Race Demands by Category

### 2.1 Duration, attrition, and the shape of the field

| Parameter | 20K | 50K | 100K | 100M |
|-----------|-----|-----|------|------|
| Mid-pack finish (planner default) | ~3.0 h **[EX]** | ~7.5 h **[EX]** | ~15.5 h **[EX]** | ~30 h **[SC]** |
| Plausible mid-pack range | 2–4.5 h **[EX]** | 5–10 h **[EX]** | 12–20 h **[PC/SC]** | 24–44 h **[PC]** |
| Reference DNF rate | — | OCC 2025: ~10% | CCC: 18% (2022) → 28% (2024) | UTMB 2024: 36% |
| Reference cutoff | — | OCC 14:30 | CCC 26:30 | UTMB 46:30 |

The derivation of the duration row is in §13.1 — it matters enough to show the
arithmetic rather than assert a number.

Hard data exists for exactly one category. The 2024 UTMB field (2,761 starters,
1,760 finishers, 1,001 DNF) has a full published distribution **[PC]**: 10th
percentile 29:40, **median 40:33**, mean 38:55, 75th-percentile threshold
35:15, and 25% of finishers slower than 44:00 against a 46:30 cutoff. The DNF
rate splits 42% women / 35% men. Winning times sit near 20 h (men) and 22 h
(women) — i.e. **the median finisher takes twice the winner's time**, which is
the single most important fact about ultra field structure and the reason a
planner must never scale race-duration parameters off elite results.

CCC (100K) and OCC (50K) publish winner times, starter/finisher counts, and
cutoffs, but no field-wide median or mean could be located **[PC]** — a real
gap, bridged in §13.1.

### 2.2 Terrain distribution — climbing dominates the clock

Uphill consumes time far out of proportion to its share of distance. A 60 km
loop that is 40% uphill by distance can be 60–75% of race time spent climbing
**[PC]**; Ruth Croft's 2024 Transvulcania 100K was over 5 hours — 66% of total
race time — going uphill **[PC]**. Descending is the opposite: at the optimum
grade, descent costs ~9.2 J·kg⁻¹ per vertical metre versus ~44.9 for ascent —
roughly **5× cheaper per vertical metre down than up** **[PR]**.

The planner implication is direct: prescribing by *distance* systematically
under-doses climbing-heavy races, and prescribing quality work on the flat
trains the minority of race time. See §7.2.

### 2.3 Descent is limited by damage, not by aerobic capacity

Minetti et al. compared metabolically predicted maximum vertical speed against
actual raced speed **[PR]**. Uphill-only races matched prediction almost
exactly (predicted/measured 0.950 ± 0.130, n=8 events). Downhill-only races
were predicted at **3.4× the actual raced speed** (3.446 ± 1.324, n=5). Athletes
descend far slower than their aerobic system would allow — the limiter is
technical control and eccentric tolerance.

That limiter is trainable. The same study found elite mountain runners' cost of
running downhill was ~40% lower than published sedentary values, attributed to
technique reducing stabilising co-contraction **[PR]**.

Mechanical load explains the damage: flat running at 9:00 min/mi produces ~3×
body weight impact; the same pace on a 9% descent more than doubles that; a
technical trail descent reaches 5–6× body weight per foot strike **[PC]**. Post
-100-mile creatine kinase runs 10,000–50,000+ U/L against a <198 U/L reference
**[PR]**.

### 2.4 Pace decay and pacing discipline

Within-race decay is severe and roughly monotone. In an instrumented elite 24-h
case (n=1, VO2max 67.6), distance per 6-hour quarter fell 54.5 → 48.0 → 39.2 →
30.1 km — a **~45% speed drop** first quarter to last **[PR]**. Mean race HR was
120 bpm, only 64% of HRmax, over the full 24 hours **[PR]** — the constraint at
this duration is not cardiovascular.

Age decay across the UTMB family, usable as a pace-adjustment curve **[PR]**:

| Age band | CCC (100K) top-10% speed | OCC (50K) top-10% speed |
|----------|--------------------------|--------------------------|
| 23–39 | 7.4 ± 0.7 km/h | 8.6 ± 0.7 km/h |
| 40–49 | 6.4 ± 0.7 (−13%) | 7.6 ± 0.8 (−12%) |
| 50–59 | 5.7 ± 0.4 (−23%) | 6.7 ± 0.5 (−22%) |
| 60–69 | ~−30% | 6.2 ± 0.3 (−28%) |

One counterintuitive pacing finding: injury risk is lowest for competitors
racing *close to the field-average pace*, and rises for those notably faster
**and** notably slower than average **[PR]**. Slow does not mean safe.

---

## 3. Macrocycle Structure

### 3.1 Where periodization sits in the priority stack

Before tuning phase ratios, note what the leading ultra coaching voice says
about their importance. Koop's Hierarchy of Ultramarathon Training Needs
(adapted from Seiler) puts total annual volume first, phase periodization 9th
of 11 and the taper last **[SC]**. The source's point is that a
roughly right periodization is close enough **[SC]**.

Read that as a design constraint, not as licence to skip structure: the planner
should spend its complexity budget on total volume, recovery cadence, terrain
specificity, and gut/fuelling rehearsal before it spends it on phase-ratio
precision.

### 3.2 Preparation length

| Source | Claim | Tier |
|--------|-------|------|
| TrainingPeaks coach blog | 12–16 wk for half/marathon; **20–24 wk for a 50–100 mile race** | [SC] |
| vert.run 100-mile guide | 16–24 wk with existing base; or 6–12 wk general prep + 12–16 wk race-specific | [SC] |
| vert.run 100K guide | 14-wk skeleton: base 4–8, early specific 3–4, peak specific 4–5, taper 2 | [SC] |
| Torrence (iRunFar) | Base 1–3 months, Introductory 4–6 wk, Specific 8–12 wk (taper is its last 1–3 wk), then a 1–2 month recovery phase | [SC] |
| Torrence, first ultra | 4–6 h/wk of consistent training for at least 3 months is enough to finish a first 50K | [SC] |
| CTS peak-block minimum | 50K/50M: ≥6 h/wk for ≥3 consecutive weeks, starting 6 wk out. 100K/100M: ≥9 h/wk for ≥6 consecutive weeks, starting 9 wk out | [SC] |
| Uphill Athlete (trekking) | Minimum prep block 8 wk; optimal 16–24 wk | [SC] |

The CTS peak-block minimums are the most planner-usable because they are
*horizon-implying*: a 100K/100M prep whose peak block alone is 9 weeks cannot
have a viable horizon shorter than roughly peak-block + build + taper.
Consolidated per-category figures are in §13.2.

### 3.3 Phase model and its ultra-specific deformation

The four-phase skeleton from `em §1` applies unchanged in ordering
(BASE → BUILD → PEAK → TAPER). Two ultra-specific deformations:

**Specificity is concentrated late.** Race-specific work belongs in the final
6–13 weeks; adding it earlier risks overtraining, burnout and injury
**[SC]**. This aligns with the vertical-adaptation timing in §7.3 — vert-heavy
training is short-lived and belongs in the last 8 weeks.

**Reverse periodization is a live option at ultra distance.** In reverse
periodization the base period carries short, low-volume, high-intensity work
above threshold, and the build period drops intensity below threshold and
extends duration **[PR]**. The one documented end-to-end elite ultra prep in the
literature used it: a 29-week block to an 11th place at the 2023 World Mountain
and Trail Running Championships, averaging 8 h 53 min / 87.2 km / 3,755 m D+
per week, hardest week 14 h 58 min / 145 km / 7,552 m **[PR]** (n=1 — a case
study, not a norm).

The planner's default should stay traditional. Reverse periodization is worth
documenting because it explains why the intensity-distribution *trajectory*
(§10.3) legitimately runs in either direction depending on the model chosen.

### 3.4 Recovery cadence and block loading

Recovery cadence follows `em §1`: 3:1 is the default microcycle unit and
appears in both the reverse-periodization example plan and the road-runner RCT
of §10.1 as "3 weeks build + 1 week recovery" **[SC][PR]**. Torrence's
operational form is a **10–15% volume increase for 2–3 weeks, then a recovery
week, then repeat** — worked example: 50 mi/wk (8 h) baseline → wk 1–3 at
55–58 mi (8:45–9:15) → wk 4 at 50 mi (8:00) → wk 5–7 at 60–67 mi (9:30–10:45) →
wk 8 at 55 mi (8:45) **[SC]**. When overtraining signs appear: cut volume 50%
for 7–10 days **[SC]**. Uphill Athlete's trekking-prep variant is every 3rd or
4th week at ~50% total time **[SC]**.

**Block (concentrated-load) training** is a documented ultra variant **[SC]**:
3–4 week blocks, front-loaded so the first (sometimes second) week carries a
disproportionate share of key sessions. Its structural signature is
redistribution rather than addition — 14 h/wk delivered as ~1.2 h/day Mon–Fri
plus 4 h Saturday and 4 h Sunday, instead of ~2 h every day. Two guardrails
that must travel with it: consecutive hard days are tolerated far better on
short reps (800 m, 3-min efforts) than on long intervals (2-mile, 15-min
efforts); and athletes injured more often than roughly **once per 9 months
should be screened out entirely**. The claimed benefit — 2–6% over a season —
is labelled by its own author as an estimate, not a study
result. Treat block training as an opt-in for injury-resilient athletes, never
as a default.

---

## 4. Volume Envelopes

### 4.1 Weekly hours

Sources disagree, and the disagreement is informative rather than noise.

| Source | Figure | Tier |
|--------|--------|------|
| CTS floors | 50K/50M ≥6 h/wk × ≥3 wk; 100K/100M ≥9 h/wk × ≥6 wk. Explicitly **not** broken out by athlete tier — the author's position is that real-world constraints, not fitness needs, set the ceiling | [SC] |
| vert.run 100K | Base 4–7 → early specific 6–9 → peak specific 8–12 h/wk | [SC] |
| vert.run 100M | Base 5–7 (4–5 acceptable from a low base) → 6–9 by phase end → race-specific 8–12 → **peak 10–14+** h/wk | [SC] |
| Torrence ceiling | Above roughly 60–70 mi/wk or 8–10 h/wk, the risk of harm starts to catch up with the benefit | [SC] |
| Elite ultratrail case (n=1) | 29-wk average 8 h 53 min/wk; hardest week 14 h 58 min | [PR] |

**The tension worth flagging:** Torrence puts the injury fine line at 8–10 h/wk,
while vert.run peaks a 100-mile build at 10–14+ h/wk and a world-championship
ultratrail athlete averaged 8h53 with a 15 h peak. These are not reconcilable
by arithmetic. The most defensible reading is that Torrence's ceiling describes
*running-mileage-driven* musculoskeletal load, whereas ultra-trail peak weeks
are largely Zone 1 time-on-feet with a substantial hiking fraction — different
tissue load per hour. A planner should therefore cap on *load*, not on hours,
and treat 8–10 h/wk as the point where the ramp rate must slow, not stop
**[EX]**.

Per-tier consolidation is in §13.3.

### 4.2 Doubles

Double-run days are a niche tool, not a volume default **[SC]**. The stated
gate: prescribe them only when they add **>25% more volume for the month** than
the athlete's current single-run pattern would produce, with a **second run of
60–90 minutes** (shorter and it does not earn its recovery cost). Cadence: 1–2
double days per week, sustained **≥8 consecutive weeks** before expecting
adaptation. The author reports applying this to very few of the
athletes coached. For a single-user planner, doubles should be an explicit
opt-in, never auto-generated.

### 4.3 Session-type vocabulary

The duration definitions underpinning every volume figure above **[SC]**:

| Type | Duration | Effort |
|------|----------|--------|
| Easy run | up to ~90 min | easy; the most frequent run type |
| Recovery run | 20–45 min | easier than easy; day after a taxing session |
| Long run (steady-state) | hours; built in small increments every few weeks | easy |
| Fast-finish long run | final 2–10 miles | near marathon race pace |

---

## 5. The Long Run — Time on Feet

### 5.1 Why time, not distance

Trail long runs are prescribed in hours, not kilometres, for the same reason
km-effort exists: a 4-hour mountain long run and a 4-hour road long run are
comparable training stimuli at wildly different distances. Torrence prescribes
road races in miles and trail races in hours **[SC]**; §13.5 holds the trail ceilings.

### 5.2 There is no universal long-run fraction

Across athletes who successfully completed their target ultra, observed longest
runs ranged from **20% to 80% of race distance** **[SC]**. No fixed
percentage-of-race-distance ceiling survives contact with the data. Long-run
length should be sized to the athlete's *recent (roughly last 10 weeks)
training load*, not to the goal-race distance **[SC]**.

Reinforcing this from the opposite end: the elite ultratrail athlete in the
29-week case study had a longest single trail run of **4 h 18 min** on the way
to 11th at the World Championships **[PR]**. Long runs are not where the
adaptation lives; total volume is (§3.1, priority 1).

### 5.3 The long-run progression ladder

A named "Long Run Reasonability Test" progression, from a 4-hour baseline
**[SC]**:

| Weeks | Weekly increment |
|-------|------------------|
| 1–2 | ~20% |
| 3–4 | 10%, then 0% |
| 5–7 | 5% |
| 8+ | 2.5%, then 1.25% |

Reaching roughly an **8-hour long run by week 16**. Two dosing rules travel
with it: only **1–2 long runs per month** should sit at or near the athlete's
reasonable maximum; the other weekly long runs sit at **50–75% of that
maximum**. And a corrective on how much any single long run matters — in the
worked example it was ~2.5% of the cycle's total training volume **[SC]**.

### 5.4 The four-hour floor

**4 hours is the minimum long-run duration to rehearse race nutrition**, for
nearly every ultra runner **[SC]**. This is the single most
actionable long-run constant in the ultra literature, because it converts a
duration into a *session purpose*: a long run under 4 h trains aerobic
durability; a long run at or over 4 h is also the only vehicle for gut training
(§12). For the 20K category — whose whole race lasts ~3 h — this floor is why
gut training is a non-issue there and a first-order concern from 50K up.

### 5.5 The race-simulation "Epic Mountain Day"

For 100M mountain prep specifically **[SC]**: 8–10 hours, entirely Zone 1 /
easy effort, on terrain mimicking the goal race (worked example: 37 mi /
9,500 ft over 8–9 h). Frequency: **only 1–2 in the entire build-up**, optionally
one more 8–12 weeks out, with the primary one **4–6 weeks before race day**.
Buffer with two full easy or rest days *both before and after*.

This session is the ultra analogue of the long-course triathlon durability
rehearsal (`tlc §2.4`) and carries the same recovery cost profile. It is the
only place in this document where a session over 6 hours is endorsed, and it is
endorsed at most twice per macrocycle.

---

## 6. Back-to-Back Long Runs

Consecutive-day long runs rehearse running on pre-fatigued legs — the specific
state the second half of an ultra is run in — without the recovery cost of a
single session twice the length.

### 6.1 Dosing

| Parameter | Guidance | Tier |
|-----------|----------|------|
| Day 2 sizing | **Cut 25–50% off day 1's time**: 4 h Saturday → 2–3 h Sunday | [SC] |
| Day 1 / Day 2 roles | Day 1 is the longer/harder run; day 2 is deliberately slow/easy | [PC] |
| 100K worked pair | Sat 3–4 h at race-matched Mountain Index; Sun 2–2.5 h at RPE 4–5 | [SC] |
| 100M worked pair | Day 1: 3–5 h easy-to-moderate; day 2: 2–4 h easy | [SC] |
| Spacing | **Every 2–3 weeks** *(Torrence)* … every 3–4 weeks at most, built up over time *(Powell)* | [SC] |
| Count per cycle | 2–3 back-to-back blocks across a full cycle, each adequately recovered | [PC] |
| Timing | 3–6 weeks before the target ultra, especially a first one; the single biggest B2B weekend **4–5 weeks out** | [PC][SC] |
| After-care | Follow with **at least 2 easy days**; stay hypervigilant for injury on day 2 and after | [PC] |
| Not scheduled every week | Explicit in the 100K plan skeleton | [SC] |

### 6.2 When they are not indicated

Torrence's table (§5.1) says back-to-backs are **not necessary for 50K**, road
or trail — they enter at 50M/100K and stay through 100M **[SC]**. This is the
cleanest category-scaled B2B signal available and §13 follows it.

Contradicting it: a survey-style secondary source reports **73% of first-time
50K finishers used back-to-back long runs** in their build-up **[PC, provenance
unverifiable]** — methodology not recoverable from the page. Read as evidence
that B2Bs are *common* at 50K, not that they are *necessary*.

### 6.3 Risk framing

Back-to-backs are the highest-risk structure in this document relative to how
thin its evidence base is. Every number in §6.1 is single-coach opinion or
practitioner consensus; there is no controlled trial of back-to-back long-run
dosing anywhere in the sourced literature. The consistent thread across sources
is caution — infrequent, built up gradually, extra recovery afterwards,
especially for athletes without high-mileage history.

---

## 7. Vertical Gain

### 7.1 Uphill intervals

The dominant coaching position is that mountain-running quality work belongs on
the climb: **~80% of all interval workouts prescribed on uphill grades** **[SC]**
— consistent with §2.2, where climbing consumes most of race time.

A structured UTMB-prep uphill-interval progression **[SC]**:

- **Grade**: 6–8%
- **Progression**: start at 10 minutes of total uphill interval work
  (e.g. 5 × 2 min), build to ~20 minutes total (e.g. 6 × 3 min)
- **Recovery**: easy running or walking back down between reps
- **Intensity control**: effort, form and cadence — *not* pace (pace is
  meaningless on variable grade)

**Combo fatigue-resistance sessions** (hill intervals plus tempo in one
session), each bracketed by 20 min easy warm-up and 15 min easy cool-down
**[SC]**:

- 5 × 90 s hill intervals → 5 min easy → 20 min tempo
- 30 min tempo → 5 min easy → 4 × 1 min hill intervals
- 5 × 3 min hill intervals → 5 min easy → 15 min moderate/hard

Cadence: **at most every other week** during the final 3 months, with the last
one **≥3 weeks before race day**.

### 7.2 Matching vertical density — the Mountain Index

The most transferable vert-prescription tool is a density ratio rather than an
absolute **[SC]**:

```
Mountain Index (MI) = race elevation gain (m) / race distance (km)
```

Worked example: 4,000 m over 100 km → MI 40, so a 25–30 km long run should
carry **~1,000–1,200 m of gain** to match the race's per-km vertical density.
This converts a race profile directly into a long-run vert target and is the
recommended mechanism for a planner that already knows the goal race's D+ and
distance.

Complementary scaling rule **[SC]**: weekly distance and vertical gain start at
roughly **50% of the goal event's single biggest day**, and progress toward
**90–100%** of it for longer events (and may exceed 100% for shorter events).

### 7.3 Progression rate and timing

- **Ramp cap**: ~**20% increase in feet-per-mile per week** is described as the
  aggressive upper bound, not a target — worked progression 100 → 120 →
  150 ft/mile, with the author noting this rate is not sustainable for all
  athletes **[SC]**.
- **Goal state**: build toward being able to complete the goal event's *total*
  vertical gain within a single training week **[SC]**.
- **Timing**: vertical-specific adaptation comes and goes quickly — the
  **final 8 weeks** before the race are the key window for concentrated
  vert-heavy work, rather than spreading it evenly across the whole prep
  **[SC]**.

### 7.4 Weekly vertical norms — a derived table

**No source in the reviewed literature gives weekly vertical-gain norms bucketed
by UTMB category.** This is the largest single gap in the evidence base. The
table in §13.6 is therefore built by explicit derivation **[EX]** from three
anchors: the race-vert-in-a-training-week goal state (§7.3), representative
course profiles per category (§1), and the one measured elite data point
(29-week average 3,755 m/wk, hardest week 7,552 m, for a world-championship
ultratrail athlete **[PR]**). The derivation is shown at §13.6; the numbers are
not measured norms and must not be presented to an athlete as such.

---

## 8. Power-Hiking

Hiking is not a fallback at ultra distance — it is a trained gait with its own
economy curve, and above a certain grade it is strictly superior to running.

### 8.1 The walk/run crossover

Three distinct thresholds, easily conflated and worth keeping separate:

| Concept | Value | Meaning | Tier |
|---------|-------|---------|------|
| Walk/run economy crossover | **~15.8° (≈28% grade)** — walking becomes >8% more metabolically efficient than running | Gait-selection threshold at ordinary paces | [PR, via secondary] |
| Pace-dependence of the crossover | At 9.4° (~18% grade), *running* at 2.14 m/s (~12:30 min/mi) is still more efficient — but only at that elite pace | The crossover moves with speed; slower athletes should hike sooner | [PR, via secondary] |
| Very steep grades | At 30° (58%), all tested runners were more economical **walking** below ~0.9 m/s (~30:00 min/mi) | At ultra paces on steep ground, running is essentially never worth it | [PR, via secondary] |
| Optimum mountain-path gradient | **20–30%** minimises the vertical cost of transport, for both gaits and both directions | A route/pacing number, **not** a gait threshold | [PR, primary] |
| Flight-phase loss | Above ~20% grade, running no longer has a flight phase — the walk/run distinction blurs | Explains why the crossover is fatigue- and athlete-dependent | [SC] |

> The crossover figures come from Giovanelli et al. (2016) and Ortiz et al.
> (2017) as cited by secondary coaching sources with volume/page references —
> the primary papers were not read directly. Spot-verify before hard-coding.

### 8.2 Poles

| Finding | Detail | Tier |
|---------|--------|------|
| Grade floor | Below ~10% grade (~6°), poles do **not** reduce metabolic cost | [PR, via secondary] |
| Benefit zone | Above 20° grade, poles lower both RPE and vertical cost of transport | [PR, via secondary] |
| Max-effort gain | Field hill climb (433 m over 1.3 km): poles ~2.5% (~30 s) faster | [PR, via secondary] |
| Submaximal gain | At 80%-effort hiking, poles made **no** difference to finish time | [PR, via secondary] |
| Coaching rule | Recommended for inclines steeper than ~20% | [SC] |

Practical reading for a planner: poles are a steep-terrain tool whose measured
benefit appears at maximal effort, and their real value in a 30-hour race is
more plausibly damage-sparing and postural than metabolic. Pole use should be
rehearsed on the same sessions where the goal race would use them.

### 8.3 Training the hike

The explicit requirement to *train* hiking rather than assume it appears at the
100-mile distance, alongside time-on-feet preparation **[SC]**. Two structures
transfer from the mountaineering-prep literature **[SC]**:

- **Weighted-pack progression, 8–10 weeks**: weeks 1–4 bodyweight/gear only;
  weeks 5–6 add ~5% body weight; weeks 7–8 ~10%; weeks 9–10 ~15–20%. Target is
  the actual race carry, and a conservative variant trains at 10–15% *below*
  expected load to reduce injury risk. For trail ultras the analogue is the
  loaded race vest, not a trekking pack — the same ramp shape applies.
- **Hike-specific base structure**: 4 aerobic days (Z1–Z2) + 2 strength days +
  1 rest day; volume 5–8 → 10–12+ h/wk; longest single session ~50% of weekly
  training time; every 3rd–4th week at ~50% volume.

Sequencing note: strength work is best placed **within 3–24 hours following** a
hill session, to compound the neuromuscular stimulus rather than compete with
it **[SC]**. Compare `em §7`, which prefers 6+ hours of separation for
general endurance/strength concurrency — the mountain-specific advice
deliberately clusters the load instead.

---

## 9. Downhill and Eccentric Conditioning

This is the best-evidenced section in the document — descent conditioning has a
real controlled literature behind it, unlike back-to-backs or weekly vert.

### 9.1 What a downhill bout does

Typical research protocol across the reviewed literature **[PR]**: slope −12%
(IQR −15% to −10%), duration 40 min (IQR 30–45), speed 11.3 km/h (IQR
9.8–12.9).

Acute cost **[PR]**:

| Marker | Effect |
|--------|--------|
| Knee-extensor MVC torque | −14% to −55% immediately post |
| Peak creatine kinase | +346.5% to +370.7% at 24 h |
| DOMS | peaks 48–72 h post |
| Full functional recovery | 4–5 days |
| Trained vs untrained | immediate MVC loss −16.4% vs −23.5%; strength restored by 72 h vs 96 h |

### 9.2 What it builds

A 4-week, 10-session protocol (1–2 rest days between sessions; 60–65% VO2max;
slopes −5%, −10%, −15%; 8.4–13.0 km/h; duration progressing 15 → 30 min; n=12
untrained) produced **[PR]**:

- Eccentric MVT **+8.6 ± 11.3% at 2 weeks**, **+15.2 ± 11.3% at 4 weeks**
- Isometric MVT +9.7 ± 11.2%; concentric MVT +12.9 ± 11.8% at 4 weeks
- Vastus lateralis volume +6.6 ± 3.2%; fascicle length +2.7 ± 2.2%; pennation
  angle +5.8 ± 3.8%
- **VO2max unchanged** (49.4 vs 49.7 ml·kg⁻¹·min⁻¹, p=0.73)

That last row is the design constraint: downhill sessions are a *strength and
architecture* stimulus, not an aerobic one. They must be budgeted against the
week's quality allowance, not against its easy volume.

Corroborating dose-response **[PR]**: 3 sessions/week for 5 weeks at −10%,
5–20 min per session → +9% to +24% maximal knee-extensor torque; 5
sessions/week for 1–2 weeks reduced DOMS, with a larger effect after 2 weeks
than 1.

### 9.3 The repeated-bout effect and its spacing window

The repeated-bout effect (RBE) is what makes descent conditioning tractable in
a plan: one bout protects against the next.

| Gap between bouts | Protection | Tier |
|-------------------|-----------|------|
| 5 days | **None additional** — MVC torque decrement unchanged | [PR] |
| 3 weeks | Present and large — in trained female distance runners, significantly lower soreness in gastrocnemius (p=0.001, ηp²=0.711), quadriceps (p=0.001, ηp²=0.710), hamstrings (p=0.001, ηp²=0.694), plus lower RPE and blood lactate in bout 2 | [PR] |
| 3, 6, 9 weeks | Still present after a prior 30-min downhill run | [PR] |
| > 9 weeks with no eccentric work between | **Washed out** | [PR] |

Repeated exposures confer more protection than a single bout **[PR]**.

This produces a hard scheduling window for a planner: **descent-specific
sessions must be spaced ≥ ~2 weeks apart to be separate stimuli rather than
compounded damage, and ≤ 9 weeks apart for the protection to still be live at
the next one.**

### 9.4 Session prescription and cadence

| Parameter | Guidance | Tier |
|-----------|----------|------|
| Grade (race-specific session) | Steep and sustained, **>20%** descents | [SC] |
| Cadence cue | 20+ strides/min faster than on the flat | [SC] |
| Technique cue | Understriding reduced MVC loss to −8.8% vs −15.5% with preferred stride over a 45-min bout | [PR] |
| Frequency | **At most every 2–3 weeks**, confined to the 2–3 months before the race | [SC] |
| Minimum effective dose | 1–2 sessions are enough for an ultra runner to adapt | [SC] |
| Recovery | **≥2–3 easy days** after a hard downhill effort | [SC] |
| Worked example | 7,000–10,000 ft of steep descending across a 20–25 mile run | [SC] |
| Caution | Hard/fast downhill efforts are considered injury-risky; terrain-matched endurance running is the preferred default | [SC] |

**Synthesis for the planner [EX]:** schedule 2–4 descent-focused sessions in the
final 8–12 weeks, spaced 2–3 weeks apart, with the last one **3–4 weeks before
race day** — far enough out that the 4–5 day damage window and the 48–72 h DOMS
peak have fully cleared, close enough that the 3–9 week RBE protection is still
live on race day. No source states this timing directly; it is the intersection
of §9.3's RBE window with §9.4's recovery requirement.

---

## 10. Intensity Distribution

### 10.1 What the distributions actually mean numerically

The only controlled trial with exact zone percentages and a performance outcome
is in well-trained *road* distance runners, not ultrarunners — but it is the
strongest quantitative anchor available **[PR]**: 16 weeks, 4 arms, n=56
completers, VO2peak 68 ± 4, structured as two 8-week mesocycles of
3-weeks-build + 1-week-recovery.

| Distribution | Zone 1 | Zone 2 | Zone 3 |
|--------------|--------|--------|--------|
| Pyramidal | 77% | 17% | 6% |
| Polarized | 80% | 6% | 14% |

Weekly volume did not differ (~358 vs ~348 min). The arm that **switched
pyramidal → polarized** across the two mesocycles gained most: ~1.5% in 5 km
time trial, ~3.0% in relative VO2peak, ~1.7% in velocity at 2 mmol/L, ~1.5% at
4 mmol/L, group×time p<0.0001 for VO2peak, vBLa2, vBLa4 and 5 km.

Looser practitioner definitions for the same two shapes **[PC]**: pyramidal
≈ 50–75% Z1 / 25–30% Z2 / 5–10% Z3; polarized ≈ 75–80% / ~5% / 15–20%. The
source itself notes the defining percentages are not yet settled.

### 10.2 What ultrarunners actually do

Three elite mountain-ultra athletes' logged distributions, in a four-zone
system **[PC]**:

| Athlete | Z1 | Z2 | Z3 | Z4 |
|---------|----|----|----|----|
| A | 40% | 52% | 8% | <1% |
| B | 35% | 54% | 9% | <2% |
| C | 45% | 44% | 7% | <1% |

Z1+Z2 (i.e. everything below threshold) is 89–92% — the same easy-dominant
picture as Seiler's three-zone data, split differently. In-race distributions
during 100 km+ events shift sharply into Z2: 6/76/18 and 18/62/19 **[PC]**.

Coaching envelopes from the same source **[PC]**: high-level runners fit ~2+
hours/week of Z3; more than **30–40 min/week of Z4** is described as challenging
for even well-conditioned athletes.

The convergent quantitative claim: high-intensity work is about 20% of
sessions and about 10% of yearly training hours
(citing Seiler & Kjerland 2006) — for a 10 h/week (600 min) runner, hitting 10%
means averaging just **60 minutes per week above lactate threshold** **[PR/SC]**.

### 10.3 Trajectory across the macrocycle

The one documented elite ultra prep moved from a more polarized to a
more pyramidal distribution across 29 weeks, as running volume rose and
sessions became more race-specific (more hiking, mixed terrain, longer long
runs) toward the target races **[PR]**.

That is the **opposite direction** to the winning arm of the road-runner RCT in
§10.1 (pyramidal → polarized). Both are defensible: the RCT optimises a 5 km
time trial in a reverse-order-agnostic build, while the ultra case study runs
*reverse* periodization (§3.3), where intensity is front-loaded by design and
the late-phase pyramid is the race-specific end state. For a 100K/100M goal
whose race intensity sits deep below threshold, the ultra direction — ending
pyramidal — is the one to follow **[EX]**.

### 10.4 A caution on intensity

Ultrarunners with a recent injury history had training that included
proportionally **more high-intensity units** and more regular resistance
training than uninjured runners **[PR]** (association, not causation; no
percentages reported). Combined with the §9.2 finding that downhill sessions
build strength without aerobic benefit, the practical rule is that a trail
plan's "hard" budget must be spent across uphill intervals, descent
conditioning *and* threshold work — not accumulated as if these were separate
allowances.

Note also that plasma CK after an ultra is **not** predicted by training
background — training experience, years of participation, and highest recorded
weekly mileage were each uncorrelated with post-race CK **[PR]**. Conditioning
reduces functional loss and speeds recovery (§9.1); it does not prevent the
damage.

---

## 11. Taper

General taper mechanics are in `em §6`, and `tlc §7` carries the measured
reference-plan analysis showing that real plans taper *shallower* than
taper prose claims and hold session frequency and quality minutes almost
intact. Both apply here. Ultra-specific application:

### 11.1 Length scales with distance

| Category | Taper length | Tier |
|----------|-------------|------|
| 50K | 10–14 days; one week of taper is often enough up to 50 miles | [PC] |
| 100K | 2–3 weeks if the peak block had big mileage, long climbs and back-to-backs; an experienced athlete with a strong base might use 1 week; an athlete new to the distance 8–10 days | [PC] |
| 100M | about three weeks | [PC] |
| General | Begin taper 2–3 weeks before a goal event; the fitness-building window closes 2–3 weeks out. Longer race → longer taper; more experienced → shorter; newer → longer | [PC] |

Individual variation is large and openly acknowledged by its practitioners: one
veteran's two best 100-mile peaks used a **9-day** taper and a **15-day** taper
respectively, with an explicit refusal to prescribe a fixed length **[SC]**. The
one documented elite ultratrail prep started tapering **12 days** out **[PR]**.

### 11.2 Volume shape — two competing protocols

**Aggressive (Koop), 3-week, from a 10 h/week baseline [PC]:**

| Taper week | Volume |
|-----------|--------|
| 1 | 4–5 h (40–50% of normal) |
| 2 | 2–3 h (20–30% of normal) |
| 3 (race week) | ≤2 h (<20% of normal) |

Explicitly modelled on Mujika & Padilla's four taper shapes, favouring the
nonlinear fast-decay exponential.

**Shallower (vert.run), 2-week [SC]:**

| Taper week | Volume |
|-----------|--------|
| 1 | 60–70% of peak, with light quality work |
| 2 | 30–40% of peak, easy only |
| Race week | 2–3 short shakeout runs |

Overall taper band 50–70% of peak volume. The 100-mile guide gives 50–70% of
peak over 2–4 weeks **[SC]**.

**These do not agree**, and the disagreement matters: week 1 is 40–50% under
one protocol and 60–70% under the other. The long-course bands in `tlc §7` sit nearer the shallow end (taper weeks at 50–75% of peak; race week
19–40%), and the one measured ultra data point — an elite 24-h racer whose
final five weeks ran 135, 132, 100, 81, 24 km — puts the race week at ~18% of
peak with a graded two-week run-down before it **[PR]**. A reasonable planner
default is the shallow protocol for the taper weeks and the aggressive number
for race week only **[EX]**.

A third, self-reported variant at the long end: 3 weeks for 100M+, reducing
volume 25% / 25% / 30% week by week, with intensity and terrain unchanged
**[SC]**.

### 11.3 What is preserved

**Intensity is preserved; the volume of intensity is cut** — the same principle
as `em §6` and `tlc §7`. Worked example from a 4 × 10 min tempo baseline
**[SC]**: taper week 1 → 3 × 5 min (37.5% of normal tempo volume); week 2 →
2 × 5 min (25%); final week → 1 × 5 min (12.5%). The *zone* never changes.

**Frequency is maintained or reduced by at most one day per week** **[SC]**.

**Terrain and vertical specificity are maintained unchanged through the
taper** — the explicit anti-example being an athlete switching from mountain
trails to a flat track before UTMB **[SC]**. This is trail-specific and matters:
a taper that removes vert removes the exact adaptation §7.3 says decays fastest.

### 11.4 How much the taper is worth

Modest, and worth stating plainly to an athlete. The same coach gives two
figures in two articles: a properly constructed taper improves performance
**~3%** with a sensible taper capturing 1.5–2% of that **[PR, citing
Mujika & Padilla 2003]**; elsewhere, a perfect taper might add 1–2%
**[SC]**. Treat as one
finding described loosely twice.

The reason it still matters at ultra distance is arithmetic rather than
physiology: 3% of a 30-hour 100-mile effort is **more than 50 minutes** — which
at the back of the field is the difference between a finish and a cutoff DNF
**[PC]**.

---

## 12. Fuelling and Gut Training as a Session Concern

Gut training ranks **6th** in the hierarchy of §3.1 — above terrain
specificity, periodization and taper **[SC]**. The justification:
stomach trouble is the most common reason for not finishing an ultra
**[SC]**, and the gut must be trained to handle the intake volume
required over a 6-, 12- or 30-hour effort — i.e. the requirement scales directly
with the category durations of §2.1.

### 12.1 Targets

| Figure | Value | Tier |
|--------|-------|------|
| Working race-day target | **60–90 g/h** carbohydrate (lower end for shorter/steadier efforts, upper for long/hard efforts specifically trained for) | [PC] |
| Extended target | 80–120 g/h using 2:1 glucose:fructose products, for moderate-pace ultras | [PC] |
| Single-source ceiling | Glucose alone saturates the SGLT1 transporter at ~**60 g/h regardless of gut training** | [PC] |
| Multi-transporter ceiling | Adding fructose (GLUT5) raises the trainable ceiling to 90, and up to 120 g/h | [PC] |
| Elite benchmark | Elite 24-h ultrarunners commonly exceed 60 g/h | [PR] |
| Recreational benchmark | General recreational-endurance guidance 30–50 g/h | [PR] |
| Measured elite 24-h case | 48 g/h overall; by 6-h quarter 61, 57, 36, 42 g/h; total 1,174 g CHO / 6,322 kcal / 6,300 mL over 24 h | [PR] |
| Pre-race loading | 7.2 ± 0.8 g/kg/day | [PR] |
| In-race blood glucose | mean 134 mg/dL, min 114 (hour 14), max 148 (hour 23) | [PR] |

Note the gap between the 60–90 g/h target and the 48 g/h an elite actually
achieved across 24 hours — intake degrades with duration exactly as pace does
(§2.4). Plan for the target; expect the decay.

### 12.2 The gut-training progression

A ~4-week protocol, run **exclusively during long runs** **[PC]**: start at
30 g/h and step up 10–15 g/h every 1–2 weeks toward the 60–90 g/h race-day
target. The final long runs before the race should rehearse the **full**
race-day fuelling load — same products, same proportions, at race effort and
race time-of-day where possible.

### 12.3 Planner implication

This is why §5.4's four-hour floor is a *session-purpose* rule rather than a
duration trivium. Concretely:

- Long runs **≥4 h** carry a fuelling-rehearsal role and should be tagged as
  such; long runs under 4 h cannot serve it **[SC]**.
- The gut-training ladder rides on the long-run ladder, occupying roughly the
  final 4–6 weeks of BUILD into early PEAK, so that the last two or three long
  runs are full-dress rehearsals **[EX, composing §12.2 with §5.3]**.
- For the **20K** category (~3 h race, §2.1) gut training is not a planning
  concern; from **50K** upward it is, and by **100M** it outranks periodization
  in the priority stack.

---

## 13. Per-Category Planning Parameters

Everything below is intended to be read directly into `GoalProfile` fields and
session-menu bounds. Tier markers are load-bearing: **[EX]** rows are this
document's arithmetic, not anybody's published recommendation.

### 13.1 Race duration (`race_duration`, hours)

Only the 100M category has a published field-wide distribution. The other three
are derived, and the derivation is shown so it can be replaced when better data
appears.

**Step 1 — the one measured anchor.** UTMB 2024: 270.65 km-effort covered in a
median 40:33 (40.55 h) = **6.68 km-effort/h**; 10th percentile 29:40 (29.67 h)
= 5.78 km/h raw speed vs. the median's 4.23 km/h. Median-to-top-decile speed
ratio = **0.732** **[PC]**.

**Step 2 — transfer that ratio to the categories with only top-decile data.**
Applying 0.732 to the peer-reviewed age-graded top-10% speeds for the 23–39
band (§2.4) **[EX]**:

| Race | Top-10% speed | × 0.732 | Derived median | km-effort/h |
|------|--------------|---------|----------------|-------------|
| CCC (100K, 161.5 km-eff) | 7.4 km/h | 5.41 km/h | ~18.7 h | 8.6 |
| OCC (50K, 92 km-eff) | 8.6 km/h | 6.29 km/h | ~9.1 h | 10.2 |
| UTMB (100M, 270.6 km-eff) | — | — | 40.55 h (measured) | 6.7 |

The derived medians are, if anything, **optimistic**: the CCC/OCC top-decile
figures are for the 23–39 age band only, while UTMB's 10th percentile is
whole-field. Round toward slower.

**Step 3 — apply the km-effort/h ladder to each band's midpoint** **[EX]**:

| Category | Band midpoint | Rate used | Derived duration | **Planner default** |
|----------|--------------|-----------|------------------|---------------------|
| 20K | 35 km-effort | ~11 km-eff/h (extrapolated above the 10.2 trend) | 3.2 h | **3.25 h** |
| 50K | 75 km-effort | 10.2 | 7.4 h | **7.5 h** |
| 100K | 135 km-effort | 8.6 | 15.7 h | **15.75 h** |
| 100M | 201 km-effort (161 km + 4,000 m — a representative mountain 100-miler) | 6.7 | 30.0 h | **30 h** |

**Step 4 — independent corroboration.** The 100K figure is bracketed by an
independent coaching estimate of 12–18 hours for the majority of
100K finishers (9–10 h fastest, 20+ h for slower/technical mountain 100Ks)
**[SC]**. The 100M figure of 30 h matches the 30-hour 100-mile
effort used as the worked example in the taper literature **[PC]**. The whole
ladder is corroborated qualitatively by the gut-training framing of a 6-, 12-
or 30-hour effort mapping onto 50K / 100K / 100M **[SC]**.

**The 20K row has no corroboration of any kind** — it is a pure downward
extrapolation of the km-effort/h trend, and should be treated as the weakest
number in this document.

### 13.2 Horizon and taper (`min_horizon_weeks`, `max_recommended_weeks`, `taper_weeks`)

| Category | Min prep | Typical prep | Suggested max | Taper weeks | Basis |
|----------|---------|--------------|---------------|-------------|-------|
| **20K** | **8 wk** | 12–16 wk | 20 wk | **1–2** (use 2) | **[EX]** — no source covers this category. Bridged from the half/marathon 12–16 wk figure **[SC]** and the 8-week minimum-prep-block floor **[SC]**; taper extrapolated between the ≤50-mile "one week is often sufficient" rule **[PC]** and the repo's own half-marathon profile, biased upward one week because a 20K trail race carries far more eccentric load than a road half |
| **50K** | **12 wk** | 16–20 wk | 24 wk | **2** | Torrence: 4–6 h/wk for 3+ months → a successful first 50K **[SC]**; CTS peak-block floor 6 h/wk × 3 wk from 6 wk out **[SC]**; taper 10–14 days **[PC]** |
| **100K** | **14 wk** | 20–24 wk | 28 wk | **2** (3 after a heavy peak block) | vert.run's complete 14-week skeleton **[SC]**; TrainingPeaks 20–24 wk for 50–100 mile **[SC]**; CTS peak-block floor 9 h/wk × 6 wk from 9 wk out **[SC]**; taper 2–3 wk **[PC]** |
| **100M** | **16 wk** | 20–28 wk | 32 wk | **3** | vert.run 16–24 wk with existing base, or 6–12 general + 12–16 specific **[SC]**; Torrence's total base+intro+specific spans 16–31 wk **[SC]**; taper about three weeks **[PC]** |

`max_recommended_weeks` is **[EX]** throughout — no ultra source states a
ceiling. The values follow the repo's existing convention of capping a single
focused macrocycle (`tlc §3`, Friel 20–36 wk) and extend it by category.

Recovery after a B/C tune-up race: tune-up ultras are used deliberately
as supported long runs — an athlete targeting a 100-miler in June might race a
50K in March and one or two 50-milers in April/May; a 50-mile target might use
one or two 50Ks 1–3 months out **[SC]**. Caveat that travels with it: tune-ups
must not be run at 100% effort, or recovery takes well over a week
**[SC]**. The planner gives a B tune-up longer than 3 h (a trail 20K and
up) one easier week after it and no other recovery
(`docs/specs/planner/phase_planner.md` R-PHASE-010); a C tune-up trains
straight through.

### 13.3 Phase ratios (base / build / peak+taper)

| Category | Base | Build | Peak + Taper | Basis |
|----------|------|-------|--------------|-------|
| **20K** | ~40% | ~40% | ~20% | **[EX]** from the repo's own half-marathon/marathon profiles; no ultra source covers this category |
| **50K** | ~45% | ~35% | ~20% | **[EX]** interpolating Torrence's base 1–3 mo / intro 4–6 wk / specific 8–12 wk model **[SC]** toward the shorter end |
| **100K** | ~48% | ~32% | ~20% | vert.run's 14-wk skeleton maps to base 4–8 / early-specific 3–4 / peak-specific 4–5 / taper 2 **[SC]** |
| **100M** | ~50% | ~30% | ~20% | vert.run's 6–12 wk general + 12–16 wk race-specific split **[SC]**; Koop's volume-first hierarchy argues for the longest base of the four **[SC]** |

Apply the same horizon-aware shift as `tlc §3`: longer preparations move share
into base, shorter ones compress base and enter build earlier **[EX]**. And
apply §3.1's caution — these ratios are the least evidence-backed numbers in
§13 and should not be tuned to three decimal places.

`min_build_weeks` / `min_peak_weeks` follow from the CTS peak-block floors: a
100K/100M peak block is ≥6 consecutive weeks at ≥9 h/wk **[SC]**, so
`min_peak_weeks` ≥ 4 with the remainder in late build; 50K's is ≥3 weeks at
≥6 h/wk **[SC]**, so `min_peak_weeks` ≥ 2 **[EX]**.

### 13.4 Peak weekly hours by tier

Tiers map to the repo's ambition levels (BASIC / MODERATE / COMPETITIVE).

| Category | Recreational | Moderate | Competitive | Basis |
|----------|-------------|----------|-------------|-------|
| **20K** | 4–6 h | 6–8 h | 8–10 h | **[EX]** — extrapolated below the 50K row; no source covers this category |
| **50K** | 5–7 h | 7–9 h | 9–12 h | Torrence's viable-first-50K floor of 4–6 h/wk **[SC]**; CTS floor 6 h/wk × 3 wk **[SC]** |
| **100K** | 8–10 h | 10–12 h | 12–15 h | vert.run peak specific 8–12 h/wk **[SC]**; CTS floor 9 h/wk × 6 wk **[SC]** |
| **100M** | 9–12 h | 12–15 h | 14–18 h | vert.run peak 10–14+ h/wk **[SC]**; elite ultratrail case avg 8h53 with a 14h58 peak week **[PR]** |

Two guardrails on this table. First, the CTS floors are explicitly **not**
tier-scaled — their author's position is that constraints, not fitness needs,
determine the ceiling **[SC]**, so tier-splitting them is this document's
imposition **[EX]**. Second, Torrence's 8–10 h/wk "fine line" **[SC]** sits
*below* the moderate 100M row: past ~10 h/wk the planner's ramp rate should
tighten and the added hours should be Z1 time-on-feet and hiking, not running
mileage (§4.1).

### 13.5 Longest-run ceiling (hours)

| Category | Routine long-run ceiling | Peak / simulation session | Basis |
|----------|-------------------------|---------------------------|-------|
| **20K** | 2–3 h | — | **[EX]**, extrapolated below the trail-50K row |
| **50K** | **3–4 h** | 4 h (the nutrition-rehearsal floor, §5.4) | Torrence's trail-50K row **[SC]** |
| **100K** | **4–5 h** | 5–6 h race-simulation, ≤2 per cycle **[EX]** | Routine ceiling: Torrence's trail 50M/100K row **[SC]**. The simulation extension past the routine ceiling is this document's analogy to the 50K peak session's §5.4 anchor — no source states a 100K simulation duration or cadence |
| **100M** | **5–6 h** | 8–10 h Epic Mountain Day, 1–2 per build, primary 4–6 wk out | Torrence's trail-100M row **[SC]**; Uphill Athlete **[SC]** |

Cross-checks worth carrying: an elite ultratrail World Championships prep had a
longest trail run of **4 h 18 min** **[PR]**; a 24-h specialist's longest single
session was 80 km about 2 months out **[PR]**; observed successful longest runs
span 20–80% of race distance **[SC]**. There is no evidence that pushing past
these ceilings pays.

Frequency rule for all categories: **1–2 long runs per month at/near the
ceiling; the rest at 50–75% of it** **[SC]**.

### 13.6 Weekly vertical gain (m/week at peak)

**No source publishes this by category (§7.4).** The table is derived
**[EX]** from: (a) the goal state of covering the race's total D+ within a
single training week **[SC]**; (b) the 50% → 90–100%-of-biggest-race-day
progression rule **[SC]**; (c) representative course profiles (§1); and (d) the
one measured elite data point — 3,755 m/wk averaged over 29 weeks with a
7,552 m hardest week **[PR]**.

| Category | Representative race D+ | Recreational | Moderate | Competitive |
|----------|----------------------|--------------|----------|-------------|
| **20K** | 1,000–2,500 m | 800–1,200 m | 1,200–1,800 m | 1,800–2,500 m |
| **50K** | 2,000–3,500 m | 1,500–2,000 m | 2,000–2,800 m | 2,800–3,500 m |
| **100K** | 4,000–6,000 m | 2,500–3,500 m | 3,500–4,500 m | 4,500–6,000 m |
| **100M** | 5,000–10,000 m | 3,000–4,500 m | 4,500–6,000 m | 6,000–7,500 m |

The 100M row deliberately does **not** reach the race's total D+ — a 9,915 m
training week is not survivable for the athletes this planner serves, and the
elite reference peaked at 7,552 m. Above 100K, the "race vert in one week" goal
state breaks down and the 50–100%-of-biggest-day rule governs instead.

Progression: cap weekly vertical growth at ~**20% in feet-per-mile** **[SC]**,
and concentrate vert-heavy work in the **final 8 weeks** **[SC]**. Prefer the
Mountain Index method (§7.2) whenever the goal race's distance and D+ are known
— it produces a per-session target rather than a weekly aggregate and is the
better mechanism for a planner that already holds the race profile.

### 13.7 Back-to-back long runs and downhill session cadence

| Category | Back-to-backs | Downhill sessions |
|----------|--------------|-------------------|
| **20K** | **No** **[EX]** — below the 50K row where sources already say "not necessary" | 1–2 in the final 8 weeks, spaced ≥2 wk **[EX from §9]** |
| **50K** | **Not necessary** **[SC]**; optional if the athlete tolerates them (73% of first-time 50K finishers report using them **[PC, weak]**) | 2 in the final 8 weeks, spaced 3 wk **[EX from §9]** |
| **100K** | **Yes** — Sat 3–4 h at race-matched MI, Sun 2–2.5 h at RPE 4–5; every 2–3 wk; 2–3 blocks per cycle **[SC][PC]** | 2–3 in the final 8–12 weeks, spaced 2–3 wk, last one 3–4 wk out **[SC + EX]** |
| **100M** | **Yes** — day 1 3–5 h easy-to-moderate, day 2 2–4 h easy; biggest pair 4–5 wk out; every 2–3 wk (or no more than every 3–4 wk for athletes with limited high-mileage history) **[SC]** | 3–4 in the final 8–12 weeks, spaced 2–3 wk, last one 3–4 wk out **[SC + EX]** |

Universal B2B rules regardless of category: day 2 is **25–50% shorter** than day
1 **[SC]**; day 1 is the harder run and day 2 is deliberately easy **[PC]**;
follow the pair with **≥2 easy days** **[PC]**; never schedule them every week
**[SC]**.

Universal downhill rules: **≥2 weeks** between sessions (a 5-day gap adds
nothing **[PR]**), **≤9 weeks** between them for the RBE to persist **[PR]**,
**≥2–3 easy days** after each **[SC]**, and the last one 3–4 weeks out
**[EX, §9.4]**.

---

## 14. Evidence Gaps and Epistemic Status

Named honestly, because several §13 parameters will be read as if they were
measured and they are not.

**1. The category boundaries themselves.** UTMB publishes the km-effort formula
and the four labels but not the numeric cut points. The 20K<50 / 50K 50–100 /
100K 100–170 / 100M ≥170 bands are the planner's assumption, corroborated only
by a single worked example. Every parameter in §13 is indexed on them.

**2. Back-to-back long-run dosing rests entirely on coach opinion.** There is no
controlled trial of B2B dosing, spacing, or day-2 sizing anywhere in the
sourced literature. The sizing rule (25–50% shorter), the spacing rule (every
2–3 vs. 3–4 weeks — two coaches who do not agree), and the timing rule (3–6
weeks out) are three coaches' heuristics. The one prevalence figure (73% of
first-time 50K finishers) has unverifiable provenance. This is the widest gap
between how confidently the technique is prescribed and how thinly it is
evidenced.

**3. Hiking-economy crossover figures were read via secondary sources.** The
~15.8°/28% crossover (Giovanelli 2016), the 30°/0.9 m/s finding (Ortiz 2017),
and the pole results (Giovanelli 2019, 2021) are all peer-reviewed, but were
read through coaching articles citing them with volume/page references rather
than through the primary papers. Spot-verify before hard-coding. Separately, do
not conflate the *gait-selection* crossover with Minetti's *optimum path
gradient* of 20–30% — that one is read directly from the primary paper and
answers a different question.

**4. 100M taper length is practitioner consensus with wide individual scatter.**
About three weeks is the consensus; the documented individual cases inside
that consensus used 9 days, 12 days, 15 days and 21 days. The two published
week-by-week volume protocols disagree by 20 percentage points in taper week 1
(40–50% vs 60–70% of normal). The planner's 3-week default for 100M is a
midpoint of a genuinely unsettled question.

**5. No weekly vertical-gain norms exist by category.** §13.6 is derived
arithmetic, not measured practice. Closing this properly requires analysing
actual UTMB/CCC/OCC/TDS course profiles against athlete training logs — a data
project, not a literature search.

**6. Mid-pack finish times exist for exactly one category.** UTMB (100M) has full
percentile data because one analyst published a distribution study of the 2024
results. CCC (100K) and OCC (50K) publish only winner times, cutoffs, and
starter/finisher counts. The 50K and 100K durations in §13.1 are ratio
transfers; the **20K duration has no anchor at all** and is a trend
extrapolation.

**7. The 20K category is unserved by the ultra literature end to end.** Not one
reviewed source gives distance-specific protocols below 50K. Every 20K row in
§13 is extrapolation — downward from 50K, or sideways from road half-marathon
and marathon norms. If the planner ships a 20K goal type, this is the row to
revisit first.

**8. Peer-reviewed ultra sources are thin on prescription.** The two strongest
peer-reviewed ultra papers here are an injury/physiology survey (rich on CK,
body composition, injury correlates; near-silent on training protocols) and an
n=1 24-hour case study. The one end-to-end elite prep with a full volume
trajectory is also n=1. The controlled intensity-distribution trial with exact
zone percentages is in **road** runners over 16 weeks with a 5 km outcome —
useful as a definition anchor, weak as an ultra prescription.

**9. Two internal contradictions are left unreconciled on purpose.** Torrence's
8–10 h/wk injury "fine line" versus vert.run's 10–14+ h/wk 100M peak weeks
(§4.1); and the polarized→pyramidal ultra trajectory versus the RCT's winning
pyramidal→polarized road trajectory (§10.3). Both are recorded as tensions
rather than averaged away, because averaging them would manufacture a number
neither source supports.

**10. Block training's claimed benefit is self-labelled as a guess.** The 2–6%
season-level advantage is the author's own estimate, not a
study result. Its injury-screening guardrail (exclude athletes injured more than
~once per 9 months) is the part worth keeping.

---

## References — Citation Register

Access levels: **Free** = openly readable; **Free (secondary)** = the
peer-reviewed finding was read through a freely accessible article citing it,
not the primary paper.

### Peer-reviewed

| # | Source | URL | Access | Tier |
|---|--------|-----|--------|------|
| 1 | Minetti, A.E., Moia, C., Roi, G.S., Susta, D. & Ferretti, G. (2002). Energy cost of walking and running at extreme uphill and downhill slopes. *J Appl Physiol*, 93, 1039–1046. | [doi.org](https://doi.org/10.1152/japplphysiol.01177.2001) | Paywalled | peer-reviewed |
| 2 | Giovanelli, N. et al. (2016). *J Appl Physiol*, 120, 370–375 — walk/run economy crossover across 9.4°–39.2°. | [via trainright.com](https://trainright.com/the-science-behind-when-to-run-vs-when-to-hike/) | Free (secondary) | peer-reviewed |
| 3 | Ortiz, A. et al. (2017). *Eur J Appl Physiol*, 117 — walking vs running economy at 30° incline. | [via trainright.com](https://trainright.com/the-science-behind-when-to-run-vs-when-to-hike/) | Free (secondary) | peer-reviewed |
| 4 | Giovanelli, N. et al. (2019, 2021) — trekking-pole effects on vertical cost of transport, RPE, and hill-climb performance. | [via freetrail.com](https://freetrail.com/climb-the-mountain-when-to-run-hike-or-pole/) | Free (secondary) | peer-reviewed |
| 5 | The time course of different neuromuscular adaptations to short-term downhill running training and their specific relationships with strength gains (PMC8927009). | [pmc.ncbi.nlm.nih.gov/articles/PMC8927009](https://pmc.ncbi.nlm.nih.gov/articles/PMC8927009/) | Free | peer-reviewed |
| 6 | Repeated Bout Effect of Downhill Running on Physiological Markers of Effort and Post-Exercise Perception of Soreness in Trained Female Distance Runners (PMC11209549). | [pmc.ncbi.nlm.nih.gov/articles/PMC11209549](https://pmc.ncbi.nlm.nih.gov/articles/PMC11209549/) | Free | peer-reviewed |
| 7 | Downhill Running: What Are The Effects and How Can We Adapt? A Narrative Review (PMC7674385). Includes Byrnes, Chen, Toyomura, Schwane RBE/dose findings. | [pmc.ncbi.nlm.nih.gov/articles/PMC7674385](https://pmc.ncbi.nlm.nih.gov/articles/PMC7674385/) | Free | peer-reviewed |
| 8 | Effects of 16 weeks of pyramidal and polarized training intensity distributions in well-trained endurance runners (PMC9299127). | [pmc.ncbi.nlm.nih.gov/articles/PMC9299127](https://pmc.ncbi.nlm.nih.gov/articles/PMC9299127/) | Free | peer-reviewed |
| 9 | Ultramarathon injury/physiology survey — CK, body composition, injury correlates, pacing-deviation risk (*Frontiers in Physiology*, 10.3389/fphys.2021.738665). | [frontiersin.org](https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2021.738665/full) | Free | peer-reviewed |
| 10 | 24-hour ultramarathon case study — training trajectory, taper, in-race CHO, pace decay, HR (PMC9609733). n=1. | [pmc.ncbi.nlm.nih.gov/articles/PMC9609733](https://pmc.ncbi.nlm.nih.gov/articles/PMC9609733/) | Free | peer-reviewed |
| 11 | Jaén-Carrillo, D. & Margarit-Boscà (2024). Reverse periodization in ultratrail: the road to the 2023 World Mountain and Trail Running Championships of an elite female ultrarunner. *Current Issues in Sport Science*. n=1. | [ciss-journal.org/article/view/11560](https://ciss-journal.org/article/view/11560) | Free | peer-reviewed |
| 12 | Age-related performance at the Ultra-Trail du Mont-Blanc — age-graded speeds for CCC and OCC. | [doi.org](https://doi.org/10.1051/sm/2019015) | Publisher | peer-reviewed |
| 13 | Mujika, I. & Padilla, S. (2003); Hickson et al. (1985); Sheply et al. (1992) — taper shape and intensity-maintenance principles, cited within source #20. | see `em §6` for primary Mujika & Padilla entry | — | peer-reviewed |
| 14 | Seiler, S. & Kjerland, G.Ø. (2006) — ~10% of annual training hours at high intensity, cited within source #21. | see `em §3` for the Seiler primary entries | — | peer-reviewed |
| 15 | Millet, G. (2011) — post-100-mile creatine kinase magnitudes, cited within source #24. | [via trainright.com](https://trainright.com/downhill-running-training-go-faster-hurt-less/) | Free (secondary) | peer-reviewed |

### Practitioner / governing body

| # | Source | URL | Access | Tier |
|---|--------|-----|--------|------|
| 16 | UTMB World Series — Sports System (km-effort formula, categories, Running Stones, Index minimums). | [utmb.world/sports-system](https://utmb.world/sports-system) | Free | practitioner-consensus |
| 17 | UTMB Index (Wikipedia) — checked for the category cut points; does not publish them. | [en.wikipedia.org/wiki/UTMB_Index](https://en.wikipedia.org/wiki/UTMB_Index) | Free | practitioner-consensus |
| 18 | "What It Takes to Finish UTMB" — statistical analysis of official 2024 UTMB results (full finish-time distribution). | [fredericspast.substack.com](https://fredericspast.substack.com/p/what-it-takes-to-finish-utmb) | Free | practitioner-consensus |
| 19 | CCC and OCC official race pages (distance, D+, cutoffs, field sizes); 2024 OCC results (iRunFar). | [montblanc.utmb.world/races/CCC](https://montblanc.utmb.world/races/CCC) · [irunfar.com OCC 2024](https://www.irunfar.com/2024-occ-results-hemming-and-yao-win-utmb-50k-world-series-final) | Free | practitioner-consensus |
| 20 | Koop, J. — Tapering for Ultrarunning: Boost Race Performance and Prevent Taper Tantrums (CTS/TrainRight), incl. reader-comment 100M+ taper variant. | [trainright.com](https://trainright.com/tapering-ultrarunning-prevent-taper-tantrum/) | Free | practitioner-consensus |
| 21 | Koop, J. — The Hierarchy of Ultramarathon Training Needs (CTS/TrainRight). | [trainright.com](https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/) | Free | single-coach-opinion |
| 22 | Koop, J. — The Longest Run in Ultramarathon Training (CTS/TrainRight). | [trainright.com](https://trainright.com/longest-run-ultramarathon-training/) | Free | single-coach-opinion |
| 23 | Koop, J. — Block Training for Ultrarunning (CTS/TrainRight). | [trainright.com](https://trainright.com/block-training-ultrarunning-ultramarathon/) | Free | single-coach-opinion |
| 24 | Koop, J. — Downhill Running Training: Go Faster, Hurt Less (CTS/TrainRight). | [trainright.com](https://trainright.com/downhill-running-training-go-faster-hurt-less/) | Free | single-coach-opinion |
| 25 | Koop, J. — Training to Run Uphill Faster (CTS/TrainRight). | [trainright.com](https://trainright.com/training-to-run-uphill-faster/) | Free | single-coach-opinion |
| 26 | Koop, J. — The Science Behind When to Run vs. When to Hike (CTS/TrainRight). | [trainright.com](https://trainright.com/the-science-behind-when-to-run-vs-when-to-hike/) | Free | single-coach-opinion |
| 27 | Koop, J. — Do's and Don'ts of Double-Day Training for Ultrarunners (CTS/TrainRight). | [trainright.com](https://trainright.com/double-day-training-ultrarunner/) | Free | single-coach-opinion |
| 28 | CTS/TrainRight — How Much Do You Need to Train for a 100-Mile Ultramarathon? (peak-block hour floors). | [trainright.com](https://trainright.com/ultramarathon-training-time-required/) | Free | single-coach-opinion |
| 29 | Torrence, I. — Running Periodization: It Isn't Rocket Science (iRunFar). | [irunfar.com](https://www.irunfar.com/periodization-it-isnt-rocket-science) | Free | single-coach-opinion |
| 30 | Torrence, I. — Ultramarathon Training Volume (iRunFar) — the long-run/B2B distance-scaled table. | [irunfar.com](https://www.irunfar.com/ultramarathon-training-volume) | Free | single-coach-opinion |
| 31 | Powell, B. (summarizing Torrence) — Ultramarathon Training: A Guide to Everything (iRunFar). | [irunfar.com](https://www.irunfar.com/ultramarathon-training-a-guide-to-everything) | Free | single-coach-opinion |
| 32 | Jones-Wilkins, A. — On Peaking for an Ultramarathon (iRunFar). | [irunfar.com](https://www.irunfar.com/on-peaking-for-an-ultramarathon) | Free | single-coach-opinion |
| 33 | Uphill Athlete — UTMB Five Training Tips (uphill intervals, descent sessions, Epic Mountain Days, combo workouts). | [uphillathlete.com](https://uphillathlete.com/trail-running/utmb-five-training-tips/) | Free | single-coach-opinion |
| 34 | Uphill Athlete — Training for Trekking and Hiking (pack progression, hike-base structure). | [uphillathlete.com](https://uphillathlete.com/trekking/training-for-trekking-and-hiking/) | Free | single-coach-opinion |
| 35 | Johnston, S. — Training for Mountain Running (Evoke Endurance): elite TID logs, impact forces, uphill time share. | [evokeendurance.com](https://evokeendurance.com/resources/training-for-mountain-running/) | Free | practitioner-consensus |
| 36 | vert.run — 100km Training Plan: Complete Ultra Guide (14-wk skeleton, Mountain Index, taper %). | [vert.run/100k-training-plan](https://vert.run/100k-training-plan/) | Free | single-coach-opinion |
| 37 | vert.run — 100 Mile Training Plan: Complete 100-Miler Guide (phase hour bands, B2B structure). | [vert.run/100-miles-training-guide](https://vert.run/100-miles-training-guide/) | Free | single-coach-opinion |
| 38 | Trail Runner — The Ultramarathon Training and Racing Survival Guide (B2B timing and after-care). | [trailrunnermag.com](https://www.trailrunnermag.com/training/trail-tips-training/the-ultramarathon-survival-guide/) | Free | practitioner-consensus |
| 39 | Trail Runner — How to Taper for Long Races (taper length by distance band). | [trailrunnermag.com](https://trailrunnermag.com/training/trail-tips-training/how-to-taper-for-long-races/) | Free | practitioner-consensus |
| 40 | Runners Connect — Back to Back Long Runs for Runners (the 73% first-50K figure; provenance unverifiable). | [runnersconnect.net](https://runnersconnect.net/long-runs-training/) | Free | practitioner-consensus (weak) |
| 41 | TrainingPeaks coach blog — How and When to Use Reverse Periodization (prep-length benchmarks, annual macrocycle example). | [trainingpeaks.com](https://www.trainingpeaks.com/coach-blog/how-and-when-to-use-reverse-periodization/) | Free | single-coach-opinion |
| 42 | Running Explained — Training Intensity Distribution: Pyramidal vs. Polarized (zone-percentage definitions). | [runningexplained.com](https://www.runningexplained.com/post/training-intensity-distribution) | Free | practitioner-consensus |
| 43 | Marathon Handbook — Ultramarathon Nutrition Guide (60–90 g/h target). | [marathonhandbook.com](https://marathonhandbook.com/ultramarathon-nutrition/) | Free | practitioner-consensus |
| 44 | Flaming Phoenix — Gut Training for Ultras (progression protocol, SGLT1/GLUT5 ceilings). | [flaming-phoenix.co.uk](https://flaming-phoenix.co.uk/pages/gut-training-for-ultras) | Free | practitioner-consensus |

Shared references — Seiler, Mujika & Padilla, Bosquet, Issurin, Meeusen,
Banister, Berryman, Rønnestad — live in
[endurance_methodology.md](endurance_methodology.md) and are not repeated here.
