# Benchmark rule dossiers

The research behind [`bench/rulebook.yaml`](../../../bench/rulebook.yaml):
one dossier per rule family, compiled from the notes in
`docs/research/` outward, with one YAML card per candidate rule. Every DOI
in a card was resolved through Crossref and every book through a
publisher's ISBN listing at the time of writing; each source records that
check under `verified`, which confirms a book exists, not that its content was
read, so a book's `states` is a second-hand report. The constraint files some
dossiers name were merged into the rulebook. Numbers that could not be sourced are listed under
"Not found" rather than guessed.

| Dossier | Family | Cards |
| --- | --- | --- |
| [taper_peaking.md](taper_peaking.md) | taper and peaking | 9 |
| [progression_load.md](progression_load.md) | progression, recovery and load | 11 |
| [intensity_schools.md](intensity_schools.md) | intensity distribution, zones, coaching schools | 6 |
| [specificity_longcourse.md](specificity_longcourse.md) | long sessions, bricks, sport mix, volume by level | 11 |
| [session_structure.md](session_structure.md) | what one workout should look like, fuelling | 10 |

Three review dossiers followed the first run, on the bands no contestant met:

| Review | Bands | Outcome |
| --- | --- | --- |
| [review_form.md](review_form.md) | race-day form, form floor | the absolute +15 to +25 form band demoted to a fact; the floor now reads a seven-day mean |
| [review_intensity.md](review_intensity.md) | taper intensity kept, easy share, hard sessions per week | taper read on quality sessions; Ironman easy-share ceiling 0.90; marathon hard-session floor 1.0 |
| [review_progression.md](review_progression.md) | long-run share, rest days, first long session | long-run share demoted to a fact; rest days running-only and one-sided; first-session ceiling 1.15 on a 2025 cohort |

The rulebook takes a subset of the cards. Some cards confirm bands that
Endury's own quality gate already holds, and some are descriptive only.
The bands are the benchmark's decisions, stated in each rule's `note`
where they depart from a card. The dossiers keep their cards and
reasoning so a band can be audited, with the compiling notes tidied out;
the rulebook is what runs.
