# Plan maturity

Every plan carries a label that says how well it has been checked. The
label follows from evidence in this repository:
`tests/planner/test_maturity_audit.py` fails a label that claims more than
its evidence, and one that claims less.

| Label | What it means for the athlete |
| --- | --- |
| Experimental | Built from training research. Its sessions are not yet reviewed one by one. |
| Alpha | Its sessions are reviewed one by one, and it passes Endury's own plan-quality checks. |
| Beta | Alpha, and an athlete has trained through a whole preparation with it. |

## What Alpha takes

The plan-quality checks judge a plan's weekly volume, intensity and load
against the rulebook. They cannot judge the sessions themselves: which
workout sits on which day, and whether an athlete would choose to train
that way. That takes a person reading the menu. So Alpha needs both:

- **The checks cover the plan.** For a race, the rulebook holds at least
  one band written for that goal, and the scorecard grades every tier on
  offer. For a maintenance block, a committed scenario and a test show
  that its weekly volume settles on the tier's target.
- **A person reviewed the menu.** Every tier on offer has a line under
  [Menu reviews](#menu-reviews).

The audit counts this evidence. It does not weigh how strong the research
behind a band is.

## Plans on offer

Endury builds a new plan only for the tiers a goal's menu offers, the
`offered:` line in its YAML under `endury/planner/sessions/menus/`. Six
plans are on offer:

| Plan | Tier | Label |
| --- | --- | --- |
| Half marathon | Moderate | Alpha |
| Trail 20K | Basic | Alpha |
| Trail maintenance | Basic | Alpha |
| Half-distance triathlon | Moderate | Alpha |
| Full-distance triathlon | Moderate | Alpha |
| Triathlon maintenance | Moderate | Alpha |

None is Beta yet. Every other goal and tier keeps its menu and its sessions
in the repository, but the wizard does not show it and the coach does not
build it. A plan an athlete already has keeps working, and a tune-up race
may be of any type. A menu goes on offer through its `offered:` line, after
its review.

## When an Alpha plan is still Experimental

A label speaks for the conditions that were checked. A plan that leaves
them is Experimental, whatever its goal:

| Condition | When |
| --- | --- |
| Short notice | The race is closer than a full preparation, so the plan joins part-way through. |
| Unusual length | The weeks to race day lie outside the range the scorecard checks for the goal. |
| Low recent volume | The athlete's recent weekly hours are below 85 % of what the tier is written for. |

A maintenance block has no race, so only the last applies to it. Before
building an Experimental plan, Endury names the reasons in the preview and
asks the athlete to confirm. The exact ranges are in
`endury/planning/plan_maturity.py`.

## Menu reviews

One line per goal and tier whose sessions the maintainer has reviewed one
by one: the date, the goal id, the tier, the menu's fingerprint and a
short note. The fingerprint keeps a review honest. When a tier on offer
changes, the audit fails and prints the new fingerprint; review the tier
again and add a line with it. The newest line for a tier is the one that
counts. A line without a fingerprint is older than the fingerprints, so
its tier needs a fresh review before it goes on offer.

- 2026-11-09 running_half_marathon moderate [menu 5f312946ec39]: Monday and Saturday off, two quality days from build, a test week to open, long runs to about 100 minutes.
- 2026-10-22 trail_20k basic [menu 6ef8bedd750c]: four runs with a Tuesday climb session, a Friday rhythm run and a Sunday trail long run.
- 2026-10-08 trail_maintenance basic [menu b8e1e0fdc382]: the 20K week on an eight-week cycle, with a test every eight weeks.
- 2026-10-22 half_ironman moderate [menu 944202ec4740]: Monday off, bike quality Tuesday, run quality Wednesday, long ride Saturday, long run Sunday.
- 2026-11-08 ironman moderate [menu df0b928194bc]: the half-distance week at full-distance sizes, with two big weekends in every peak.
- 2026-11-03 triathlon_maintenance moderate [menu 280bd6f9faaf]: the half-distance week at one session a day, 8.5 hours, tests in weeks 5 and 13.
- 2026-10-01 ironman competitive: one easy day, hard days spread by sport, a Sunday long run capped at 2:30.
- 2026-10-03 ironman basic: the moderate shape with a gentler step into build.
- 2026-10-03 trail_50k basic: five days with Friday off, a long run up to three hours.
- 2026-10-05 trail_50k moderate: five days with Monday and Friday off, a four-hour rehearsal.
- 2026-10-03 trail_100k basic: Monday and Friday off, two back-to-back weekends.

## Completed preparations

One line per completed preparation or maintenance block: the date it
ended, the goal id and a short note, with no names and no personal
training data. A line here is what moves an Alpha goal to Beta. There is
none yet.
