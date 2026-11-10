# Plan maturity

Every plan type carries one of three labels, set by the evidence in this
repository rather than by taste. `tests/planner/test_maturity_audit.py`
fails a label that claims more than its evidence, and one that claims less.

| Label | What it means for the athlete | The evidence |
| --- | --- | --- |
| Experimental | Built from training research; its sessions not yet reviewed one by one. | Any of the Alpha evidence missing. |
| Alpha | Its sessions reviewed one by one, and it passes the project's own plan-quality checks; no completed preparation on record yet. | Race goal: the rulebook (`bench/rulebook.yaml`) holds at least one band of the goal's own, and the scorecard matrix (`GOAL_MATRIX_HORIZONS`) grades every tier on offer. Maintenance block: a committed scenario for every tier on offer in `devtools/planner/_shared/scenarios.py`, and the test that its weekly volume converges on the tier's target. Both: a menu review line below for every tier on offer. |
| Beta | Passes the project's own plan-quality checks, and athletes have trained with it. | Alpha, plus a line below for a completed preparation or maintenance block. |

## Plans on offer

Endury builds a new plan only for the tiers a goal's menu offers
(`offered:` in its YAML under `endury/planner/sessions/menus/`). Six plans
are on offer, so that each can be finished before the next:

| Plan | Tier | Label | What Alpha still needs |
| --- | --- | --- | --- |
| Half marathon | Moderate | Alpha | — |
| Trail 20K | Basic | Alpha | — |
| Trail maintenance | Basic | Alpha | — |
| Half-distance triathlon | Moderate | Alpha | — |
| Full-distance triathlon | Moderate | Alpha | — |
| Triathlon maintenance | Moderate | Alpha | — |

Every other goal and tier keeps its menu, its sessions and its checks in
the repository and is not offered: the wizard does not show it, and the
coach and the plan guard refuse it (`endury/planning/offered_plans.py`). A
plan an athlete already has keeps working and replans, and a tune-up race
may be of any type. A menu goes back on offer with its `offered:` line.

The evidence for a label is asked of the tiers on offer, because a label
speaks for the plans an athlete can build. A goal that offers nothing is
not audited: its label waits with its menu. The scorecard matrix, the
card-honesty grid and the plan fingerprints cover the plans on offer too
(`make run-devtool TOOL=planner_fingerprints ARGS='--all'` compares every
scenario). None is Beta yet.

The scorecard judges a plan's weekly volume per sport, its intensity and
its load against the rulebook's bands. It does not judge
the sessions themselves: which workout sits on which day, how it is
built, and whether an athlete would choose to train that way. That takes
a person reading the menus, one goal and tier at a time.

The 10K's one band of its own is its race week (`docs/research/endurance_methodology.md` §6).

Trail 50K has the thinnest evidence. Its race-day freshness band is the maintainer's judgement; its other trail bands
(longest run, peak weekly hours, climbing in the last eight weeks,
easier-week long runs, the four-hour rehearsal) come from
`docs/research/trail_running.md`, most of them one coach's opinion there.
The audit counts any band of a goal's own, so it says nothing about how
strong the evidence behind it is.

## A plan can be Experimental for its conditions

A plan built from an Alpha goal is still Experimental when it leaves the
conditions that were checked (`endury/planning/plan_maturity.py`):

| Reason | When |
| --- | --- |
| `goal_experimental` | The goal itself is Experimental. |
| `short_notice_join` | The plan joins a preparation part-way through because the race is closer than a full preparation. |
| `horizon_out_of_range` | The weeks to race day lie outside the shortest and longest horizons the scorecard checks for the goal. |
| `recent_volume_below_range` | The athlete's recent weekly hours over the goal's sports are below the volume floor for the chosen tier. |

The scorecard checks a race goal at two families of horizon: its base
sweep (`GOAL_MATRIX_HORIZONS`) and a `_wide` family that completes the
range the planner itself offers for that goal — `min_horizon_weeks` to
`max_recommended_weeks` in `phase_tables.GOAL_PROFILES`. A plan is
`horizon_out_of_range` only outside the union of the two: for a marathon
that is 12-28 weeks, not the base sweep's 12-18. `endury/planning/plan_maturity.py`'s
`CHECKED_HORIZONS` holds that union (a copy `tests/planning/test_plan_maturity.py`
pins against the scorecard's own tables).

The volume floor is 0.85 of the chosen tier's own default recent volume —
the least the plan is written for: the trail 20K basic's 2.1 h a week is the
smallest week that holds its climb, long run and easy run. Below it the plan
names the floor and says some sessions will be short or optional; the
scorecard's `_low` cell family checks a tier's default athlete at that
fraction (`_LOW_VOLUME_FRACTION`, copied the same way as the horizons; 0.75,
below the floor, until 2026-10-08); a `_high`
family checks the same athlete at 1.5 times the default, where the menu's
caps bind, without a maturity rule of its own. A
maintenance block has no `_low` cells (the scorecard only grades race
preparations), but takes the same floor since 2026-10-08: 2.55 h for trail
maintenance basic, 7.2 h for triathlon maintenance moderate.

A maintenance block has no race and no horizon, so only the first and last
apply. Building an Experimental plan asks the athlete to confirm first; the
plan preview names the reasons. Nothing about it is stored with the plan.

## Menu reviews

One dated line per goal and ambition tier whose menus have been reviewed
session by session, added by hand: the date, the goal id, the tier, the
menu's fingerprint, then a colon and a short note, e.g.
`- 2026-10-01 ironman competitive [menu 0123456789ab]: …`. A goal needs
every tier it offers reviewed to be Alpha. The fingerprint is what keeps a
review honest: when a tier on offer changes, `tests/planner/test_maturity_audit.py`
fails and prints the new fingerprint, to go on a new line once the tier is
reviewed again. Lines from before 2026-10-21 carry none.

- 2026-10-01 ironman competitive: one easy day (a Monday swim), hard days spread by sport, three rides in base, VO2max on the bike only, a Sunday long run capped at 2:30 with a race-pace finish every other week, quality from the first base week so the step into build is +10 %; swims spread Tuesday and Friday on 2026-10-03.
- 2026-10-01 ironman moderate: the competitive shape with Monday off, three swims through peak, an easy early-peak Sunday long run, an easy swim in the later recovery weeks; swims spread Tuesday and Friday on 2026-10-03.
- 2026-10-03 ironman basic: the moderate shape, quality from the first base week so the step into build is +15 % instead of +50 %, tests spread over two recovery weeks, swims on Tuesday and Friday, base weeks alternating drill and pull swims and cadence and steady rides.
- 2026-10-10 half_ironman moderate: the Ironman week in the Ironman weekend order (Monday off, Tuesday bike quality and a drill swim, Wednesday run quality and the CSS swim, Thursday an easy ride, Friday the long swim and an easy run, Saturday the long ride with a brick from late base, Sunday the long run), four runs from the first week, a test week to open the plan (FTP, run threshold and CSS tests with easy sessions around them, the long ride and long run from week 2), tests again in the second base recovery week and once in build as key sessions, VO2max in early build only, a peak week near 13.5 hours with 2 x 20 minutes on the Tuesday bike, every pool visit at least 1500 m.
- 2026-10-02 trail_20k basic: four runs with one hard day (a Tuesday climb session alternating hill repeats and power hiking, a Friday rhythm run, a Sunday trail long run), a threshold test in the last base load week, a rehearsal of 110-120 minutes, optional rides on Monday and Saturday.
- 2026-10-03 trail_maintenance basic: the 20K week on a fixed six-week cycle, a threshold test every six weeks on the climb day, recovery weeks that keep a long run.
- 2026-10-08 trail_maintenance basic [menu b8e1e0fdc382]: an eight-week cycle for 3:1 (hills, fartlek, power hike, test every eight weeks; strides, steady and progression on Friday), a long run that opens from the athlete's longest and grows to 60-80 minutes, a session cut under its menu minimum stays whole and optional, a fixed three-hour block for stronger athletes.
- 2026-10-03 trail_50k basic: five days with Friday off, a Tuesday climb session, a Thursday rhythm run, a Sunday long run up to three hours, a rehearsal of 3 to 3.5 hours, a taper that keeps five days.
- 2026-10-05 trail_50k moderate: five days with Monday and Friday off in every week, a Tuesday climb session (power hike, hill repeats, later downhill repeats), a Thursday rhythm run (the steady run alternating with a progression run, a fartlek or threshold intervals by phase), a routine long run up to 3:15 and a four-hour rehearsal, recovery weeks with an easy run with strides.
- 2026-10-03 trail_100k basic: Monday and Friday off, a Tuesday rhythm run, a Wednesday climb session, a Saturday long run with a short easy Sunday, two back-to-back weekends (mid-build and the rehearsal), a rehearsal of 4 to 4.5 hours.
- 2026-10-13 triathlon_maintenance moderate [menu e873ad52856b]: the half-Ironman week at one session a day (Monday off, Tuesday bike quality, Wednesday the quality swim, Thursday run quality, Friday an endurance swim, Saturday the long ride with a short brick, Sunday the long run), 8.5 hours, a long ride and long run that open from the athlete's longest and grow to 2:30 and 90 minutes, the three tests in weeks 5 and 13 in the quality sessions' places, recovery weeks that keep a shorter long run, every pool visit at least 1500 m.
- 2026-11-03 triathlon_maintenance moderate [menu 280bd6f9faaf]: the 2026-10-13 review with sweet-spot efforts in the long ride in the second and third load weeks of each three, plain in the first and in the test week, at the same hours and session lengths.
- 2026-10-22 trail_20k basic [menu 6ef8bedd750c]: the 2026-10-02 review plus the maintainer's later changes (recovery weeks that scale with the athlete, one base test and a race week at half the peak on 2026-10-08, tests in week 1 and every eight weeks on 2026-10-11, the taper climb kept on 2026-10-06), reviewed as a whole.
- 2026-10-22 half_ironman moderate [menu 944202ec4740]: the 2026-10-10 review with the tests moved to week 1 and every eight weeks on 2026-10-11, reviewed as a whole.
- 2026-11-03 ironman moderate [menu b3aa9a2ccb02]: the half-distance week at full-distance sizes (Monday off, Tuesday bike quality and an easy swim, Wednesday run quality and the quality swim, Thursday an easy ride, Friday the long swim and an easy run, Saturday the long ride with a brick from mid-base, Sunday the long run), the three tests in week 1 and every eight weeks, the quality swim from week 2 and tempo runs in late base so the step into build is +10-16 %, sweet spot in the long ride after the first easier week, a short race-pace brick early in peak, recovery weeks with a tempo ride and a short hard swim, an easy Wednesday ride in build and peak recovery weeks, the half's taper week, and a race-pace ride that holds about three hours at race power.
- 2026-11-08 ironman moderate [menu df0b928194bc]: the 2026-11-03 review with the maintainer's two big weekends in every peak (the long race-pace ride and its brick, then the long run the day after, whatever week the recovery falls in; 2026-11-07), reviewed as a whole.
- 2026-11-09 running_half_marathon moderate [menu 5f312946ec39]: Monday and Saturday off; base runs Tuesday easy, a Wednesday steady tempo, Thursday strides, Friday easy and the Sunday long run, build puts tempo or fartlek on Tuesday and threshold or VO2max intervals on Thursday around Wednesday strides, peak keeps the two quality days and drops Wednesday; a test week to open the plan (the threshold test on Wednesday, an easy Sunday, the long run from week 2 from the athlete's longest) and a test every eight weeks in an easier week, never in peak; quality sessions at their cards' sizes (threshold 50-55 minutes); long runs keyed so a short week keeps them on the ramp, to about 100 minutes in peak; recovery weeks that keep a shorter long run; a taper that keeps the Tuesday tempo.

## Completed preparations

One dated line per completed preparation or maintenance block, added by
hand: the date it ended, the goal id, then a colon and a short note. No
names and no personal training data. A line here is what moves an Alpha
goal to Beta.
