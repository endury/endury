# Review — long_run_share, rest_days, first_long_session_fit

- **Tools available:** WebFetch and WebSearch, both used. Every DOI below was fetched from
  `https://api.crossref.org/works/<DOI>`; every book checked against a bookseller/publisher ISBN
  listing found via WebSearch.
- Existing repo evidence (already verified there, reused here without re-fetching): Daniels, Nielsen 2014, Damsted 2018, Ramskov 2018 — all cited in
  `docs/research/bench_dossiers/{progression_load,specificity_longcourse}.md` and `bench/rulebook.yaml`.

---

## Band 1 — long_run_share (marathon)

**(a) Daniels on long run vs. weekly volume, and a low-volume exception.**
Daniels' rule is *volume-dependent*, not a flat 25%: for runners at or above 40 mi/week (~64 km),
the long run is capped at the **lesser of 25% of weekly mileage or 150 minutes**; **below 40
mi/week, Daniels allows up to 30%** of weekly mileage, with no stated absolute-minutes cap at that
volume. This was corroborated by three independent secondary running-coaching sources converging
on the same 40-mile threshold and the same 25%/30% split; the primary page could not be accessed
directly, so the number itself carries the same "bibliographic, not textual" caveat the repo
dossier already flags for this book.

**(b) Low-volume marathoners (30–50 km / 3–6 h/week).** No specific numeric long-run-share rule could be
sourced from Hudson, and no dedicated ACSM position stand on marathon long-run share was found — both
dropped rather than invented (see Not found). The most directly relevant injury evidence is
Fokkema et al. 2020: a prospective cohort of 556 half-marathon and 441 marathon recreational
entrants found a longer "longest endurance run" was associated with a *faster* finish, and — key
for (c) — **no training characteristic, including the longest run, was associated with
running-related injury** in either group.

**(c) Evidence a long run above 30% harms outcomes?** None found. Fokkema 2020 found no
injury association for the longest-run variable at all. Nielsen 2014 (repo-verified) ties
injury risk to a >30% jump in *weekly distance* over 2 weeks, not to the long run's share of a
stable week — a different variable. The 25–30% figure reads as a convention calibrated to
sub-3-hour marathoners running 40–70+ mi/week (both source texts describe their long runs in
that regime), not a threshold with its own outcome evidence.

**Verdict: replace with a volume-dependent band.** `low: 0.20, high: 0.30` for running volume
≥40 mi/week (~65 km); `low: 0.20, high: 0.33` below that, matching Daniels' own text (30%, not
25%) rather than inventing a new number. Keep `school_specific: true` and `held_out: true`, and
flag it as descriptive rather than a hard defect given Fokkema's null injury finding. This does **not** rescue Endury's or the
chat model's marathon plans: the athlete here (3 h/week rising to 6.5–8 h/week) is under 40
mi/week for nearly the whole plan, so the widened 0.33 ceiling is the one that applies — and the
observed 0.41–0.58 still blows past it by 10–25 points. The band was reading real volume-share
math correctly; it was just narrower than even the low-volume literature supports, and the
plans fail regardless.

**Proposed reading/band:** `long_run_share`, unit = share of weekly running minutes, ramp 0.10,
by weekly-running-volume tier: `{<40 mi/wk (~65 km): [0.20, 0.33], >=40 mi/wk: [0.20, 0.30]}`.

```yaml
sources:
  - citation: "Daniels J (2021). Daniels' Running Formula (4th ed). Human Kinetics."
    doi: null
    isbn: "9781718203662"
    states: "Long run capped at lesser of 25% weekly mileage / 150 min at >=40 mi/wk; up to 30% below 40 mi/wk."
    verified: isbn
  - citation: "Fokkema T, van Damme ADN, Fornerod MWJ, de Vos RJ, Bierma-Zeinstra SMA, van Middelkoop M (2020). Training for a (half-)marathon: Training volume and longest endurance run related to performance and running injuries. Scand J Med Sci Sports 30(9):1692-1704."
    doi: 10.1111/sms.13725
    states: "Cohort of 997 recreational (half-)marathoners: longer longest-run distance predicted a faster finish; no training characteristic, including longest run, predicted injury."
    verified: crossref
  - citation: "Nielsen RO, Parner ET, Nohr EA, Sorensen H, Lind M, Rasmussen S (2014). Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries. J Orthop Sports Phys Ther 44(10):739-747."
    doi: 10.2519/jospt.2014.5164
    states: ">30% increase in weekly distance over 2 weeks raised risk for some injury types; tests weekly-distance jump, not long-run share."
    verified: crossref
```

---

## Band 2 — rest_days

**(a) Complete rest days per week — coaching texts.** Friel (5th ed., 2024, VeloPress, ISBN
9781646046072 — confirmed current via bookseller listings)
recommends at least one fully unstructured day/week, more for masters or high-life-stress
athletes; the rulebook already reads this as the band's only source (grade D, single-source).
Lydiard's classic base method runs 6–7 days/week by design, calibrating recovery through
session-to-session load rather than a fixed weekly day off — a school that trains daily. I found no specific numeric day-off rule from
Daniels, Dixon, or Going Long distinct from Friel's own guidance (Going Long shares Friel as
co-author) within budget — dropped rather than invented.

**(b) Injury/overtraining evidence on 7-day training vs. a day off.** Van Gent et al. 2007 found
strong evidence that **higher weekly running-training frequency is a risk factor for injury in
men**; limited evidence linked a recent *increase* in training days/week to injury too. This is
real, on-point evidence for running frequency — but it counts *running* days, not a
mixed-modality triathlon week. Meeusen et al. 2013 (both journal co-publications checked)
establishes the FOR/NFOR/OTS continuum and the need for adequate recovery between loading
stimuli, but gives no day-count number and does not rule out daily training if load is otherwise
managed. Andersen et al. 2013 (174 Ironman-distance triathletes, 26-week cohort) found 56%
overuse-injury prevalence dominated by overuse over acute injury — real evidence that iron-distance
training carries a heavy overuse burden, but I could not find that this study isolates
"days off per week" as a tested predictor; cited here for injury-burden context, not for the
frequency question. Halson 2014 reviews load monitoring and argues the field's preferred lever is
individualized monitoring (HRV, wellness, TSB), not a fixed rest-day count.

**(c) Does the literature distinguish a triathlete's 7-day, 3-sport week from a runner's?**
No. Every source found states its recommendation in single-sport or sport-agnostic terms.
Van Gent's frequency-risk finding is running-specific and doesn't test whether rotating
swim/bike/run across 7 days carries the same same-tissue repetitive load a 7-day running week
would. That rotation lowering the risk is a plausible mechanistic inference, not something any
source here actually measured.

**Verdict: change to a triathlon-specific reading.** Keep `weekly_rest_days` for marathon/running
goals close to the current band (0.75–2.25 h/week; ramp 1.5) — Friel's number and van Gent's
frequency-risk evidence are both single-sport-applicable there. For triathlon goals, either demote
to a descriptive fact or replace with a modality-aware reading (e.g., no single sport trained on
all 7 days, or a cap on consecutive high-load days) instead of counting "any day with any
session" against the plan — the evidence base doesn't support penalizing a competitive Ironman
plan for having near-zero fully-empty days when no single discipline is loaded daily.

**Proposed reading/band:** running_marathon: `weekly_rest_days`, unit = empty days/week, band
`[0.75, 2.25]`, ramp 1.5 (unchanged, single-source-D). Triathlon goals: descriptive only, or
`hardest_single_sport_frequency` (days/week any one discipline is trained), band left open pending
a source that actually measures it (none found here).

```yaml
sources:
  - citation: "Friel J (2024). The Triathlete's Training Bible (5th ed). VeloPress."
    doi: null
    isbn: "9781646046072"
    states: "Recommends at least one fully unstructured day per week, more for masters/high-stress athletes."
    verified: isbn
  - citation: "van Gent RN, Siem D, van Middelkoop M, van Os AG, Bierma-Zeinstra SMA, Koes BW (2007). Incidence and determinants of lower extremity running injuries in long distance runners: a systematic review. Br J Sports Med 41(8):469-480."
    doi: 10.1136/bjsm.2006.033548
    states: "Strong evidence that higher weekly running-training frequency raises injury risk in men."
    verified: crossref
  - citation: "Meeusen R et al. (2013). Prevention, Diagnosis and Treatment of the Overtraining Syndrome. Med Sci Sports Exerc 45(1):186-205 (co-published Eur J Sport Sci 13(1):1-24)."
    doi: 10.1249/MSS.0b013e318279a10a
    states: "Defines the FOR/NFOR/OTS continuum; no specific day-off number given."
    verified: crossref
  - citation: "Andersen CA, Clarsen B, Johansen TV, Engebretsen L (2013). High prevalence of overuse injury among iron-distance triathletes. Br J Sports Med 47(13):857-861."
    doi: 10.1136/bjsports-2013-092397
    states: "26-week cohort of 174 iron-distance triathletes: 56% overuse-injury prevalence, dominated by overuse over acute injury."
    verified: crossref
  - citation: "Halson SL (2014). Monitoring Training Load to Understand Fatigue in Athletes. Sports Med 44(Suppl 2):139-147."
    doi: 10.1007/s40279-014-0253-z
    states: "Argues individualized load monitoring, not a fixed rest-day count, is the field's preferred overreaching safeguard."
    verified: crossref
```

---

## Band 3 — first_long_session_fit

**(a) How much may a single long session grow from the athlete's current longest?** No source
gives the first-session ratio its own number distinct from general ramp-rate literature (repo
dossier already reaches this conclusion for `long_session_step`, and I could not improve on it
for Friel's "first long ride" specifically — not found within budget). The most directly relevant
evidence is newer than anything in the repo dossiers: Frandsen et al. 2025 (5,200-runner, 18-month
Garmin cohort) tested three candidate metrics — week-to-week mileage change, acute:chronic
workload ratio, and single-session distance relative to the athlete's own longest run in the prior
30 days — and found **only the single-session-to-recent-longest ratio predicted injury**. A
spike of just 10–30% above the trailing longest run (ratio ≈1.10–1.30) already showed a
significantly elevated injury rate (HRR ≈1.64); spikes over 100% (ratio ≥2.0) more than doubled it
(HRR 2.28).

**(b) Does the injury literature isolate single-session jumps from weekly ramp?** Yes — this is
exactly what Frandsen 2025 isolates, and it found the single-session ratio was the *only* one of
the three tested metrics that predicted injury (weekly mileage change and ACWR did not). Nielsen
2014 and Damsted 2018 (both repo-verified) still apply as caveats: Nielsen's own signal is a
weekly-volume analog, and Damsted's systematic review found the pooled load-change literature
inconclusive overall, so Frandsen's single-cohort numbers shouldn't be treated as a settled
causal threshold either.

**Correction to the brief's supplied DOI:** the brief's candidate DOI for "Nielsen et al. 2013,
training errors and running injuries" (10.1007/s40279-013-0055-8) resolves via Crossref to an
unrelated 2013 *Sports Medicine* paper on soccer match-play analysis (Carling, not Nielsen) — not
verified, not cited. The actual training-errors systematic review (Nielsen RO, Buist I, Sørensen
H, Lind M, Rasmussen S, 2012, *Int J Sports Phys Ther* 7(1):58-75) appears to carry no
Crossref-registered DOI, the same unverifiable-but-real situation the repo's own
`progression_load.md` dossier already flags for Gulbin & Gaffney 1999 — dropped rather than cited
unverified.

**(c) Is 1.3 defensible, or should the rule read the week-over-week step instead?** Frandsen 2025
is a close structural match to the *current* metric (ratio of a session to the athlete's recent
longest), so I'd keep the ratio framing rather than switching to a week-over-week step rule — but
its own numbers argue for **tightening the ceiling toward ~1.10–1.15, not keeping it at 1.3**: a
1.3 ratio sits inside the range this cohort already found significantly risky (10–30% spikes,
HRR≈1.64), not safely below it. Either way, this doesn't rescue the plans in the brief: Endury's
first long ride at 165 min against a 90-min recent longest is a 1.83 ratio, and the chat model's
first long swim at 60 min against a 30-min recent longest is a 2.0 ratio — both fail overwhelmingly
whether the ceiling is 1.3 or tightened to 1.1–1.15. The low bound (0.8) has no source bearing on
it either way; treat a low ratio (conservative first session) as harmless, consistent with the
repo's own `first_week_fit` entry.

**Verdict: keep the ratio structure, tighten the band.** `low: 0.8, high: 1.15` (down from 1.3),
ramp 0.3 (down from 0.5, to keep the ramp proportionate to the tighter ceiling). Raise
`evidence_grade` from D to C given Frandsen 2025 is now a large, recent, directly on-point cohort
for the metric's shape — though still not RCT-level and still a single cohort (Damsted's caution
applies).

```yaml
sources:
  - citation: "Frandsen JSB, Hulme A, Parner ET, Moller M, Lindman I, Abrahamson J, Simonsen NS, Jacobsen JS, Ramskov D, Skejo S, Malisoux L, Bertelsen ML, Nielsen RO (2025). How much running is too much? Identifying high-risk running sessions in a 5200-person cohort study. Br J Sports Med 59(17):1203-1210."
    doi: 10.1136/bjsports-2024-109380
    states: "Only single-session distance relative to the athlete's own trailing-30-day longest run predicted injury; week-to-week change and ACWR did not. 10-30% spikes already carried HRR~1.64."
    verified: crossref
  - citation: "Nielsen RO, Parner ET, Nohr EA, Sorensen H, Lind M, Rasmussen S (2014). Excessive Progression in Weekly Running Distance and Risk of Running-Related Injuries. J Orthop Sports Phys Ther 44(10):739-747."
    doi: 10.2519/jospt.2014.5164
    states: "Weekly-distance-jump analog to the single-session finding; >30%/2wk raised risk for some injury types."
    verified: crossref
  - citation: "Damsted C, Glad S, Nielsen RO, Sorensen H, Malisoux L (2018). Is There Evidence for an Association Between Changes in Training Load and Running-Related Injuries? A Systematic Review. Int J Sports Phys Ther 13(6):931-942."
    doi: 10.26603/ijspt20180931
    states: "Pooled load-change-to-injury literature is inconclusive as a whole — a caution against treating any one cohort's cutoff as settled."
    verified: crossref
```

---

## Not found

- **Hudson's specific long-run percentage rule** — a generic "no run exceeds one-third of weekly
  mileage" appeared in a secondary source without clear attribution to Hudson; not cited.
- **A dedicated ACSM position stand on marathon long-run share.** ACSM's public position-stand
  index does not list one; only general exercise-prescription and injury-related documents.
- **Friel's specific first-long-ride growth-rate number** (e.g., "+X min/week"). Not located
  within budget on joefrieltraining.com or elsewhere; matches the repo dossier's own
  `long_session_step` "Not found" entry.
- **A Crossref DOI for Nielsen RO, Buist I, Sorensen H, Lind M, Rasmussen S (2012), Training
  errors and running related injuries: a systematic review, Int J Sports Phys Ther 7(1):58-75.**
  The brief's candidate DOI (10.1007/s40279-013-0055-8) resolves to an unrelated paper (see Band 3).
- **A study isolating a triathlete's rotated-modality 7-day week from a runner's single-sport
  7-day week for injury/frequency risk.** Not found; flagged as a plausible mechanistic inference
  rather than sourced evidence in Band 2(c).
- **Dixon (Fast-Track Triathlete) and Daniels on a specific weekly rest-day count**, distinct from
  Friel's number. Not located within budget.

## Sources register

| Source | Type | Verified |
|---|---|---|
| Daniels, *Daniels' Running Formula* (4th ed, 2021), ISBN 9781718203662 | coaching text | isbn |
| Friel, *The Triathlete's Training Bible* (5th ed, 2024), ISBN 9781646046072 | coaching text | isbn |
| Fokkema et al. 2020, *Scand J Med Sci Sports* 30(9):1692-1704, DOI 10.1111/sms.13725 | prospective cohort | crossref |
| Nielsen et al. 2014, *JOSPT* 44(10):739-747, DOI 10.2519/jospt.2014.5164 | cohort | crossref |
| Damsted et al. 2018, *Int J Sports Phys Ther* 13(6):931-942, DOI 10.26603/ijspt20180931 | systematic review | crossref |
| Ramskov et al. 2018, *JOSPT* 48(10):740-748, DOI 10.2519/jospt.2018.8062 | RCT | crossref |
| van Gent et al. 2007, *Br J Sports Med* 41(8):469-480, DOI 10.1136/bjsm.2006.033548 | systematic review | crossref |
| Meeusen et al. 2013, *MSSE* 45(1):186-205, DOI 10.1249/MSS.0b013e318279a10a | consensus statement | crossref |
| Andersen et al. 2013, *Br J Sports Med* 47(13):857-861, DOI 10.1136/bjsports-2013-092397 | prospective cohort | crossref |
| Halson 2014, *Sports Med* 44(Suppl 2):139-147, DOI 10.1007/s40279-014-0253-z | narrative review | crossref |
| Frandsen et al. 2025, *Br J Sports Med* 59(17):1203-1210, DOI 10.1136/bjsports-2024-109380 | prospective cohort | crossref |
| Lydiard base-training method (secondary sources: Lydiard Foundation, Science of Running) | historical coaching method | no |
| Nielsen et al. 2012, *Int J Sports Phys Ther* 7(1):58-75 (no Crossref DOI found) | systematic review | no |
