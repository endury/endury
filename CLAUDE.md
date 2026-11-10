@AGENTS.md

## Show plan changes as a picture

In Claude's desktop app, show any change to what an athlete sees (a menu, a planner rule) inline with the visualize widget (`show_widget`, after its `read_me`), not as text tables:

- a week-by-week calendar of one plan: weeks as rows, days as columns, one colour per session role (climb or hard day, rhythm or quality, easy, long, optional sessions dashed), the week's booked hours at the end;
- a Current / Proposed toggle over the same athlete and horizon;
- a line chart of the long run per week, current against proposed.

Build the data from `generate_plan` on the plan under review: `PlanResult.assignments` give each session's date and booked hours, and `PlacedSession.optional_reason` marks an optional one.
