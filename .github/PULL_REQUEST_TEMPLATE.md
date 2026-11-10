## What and why



## Verified

- [ ] I understand what this change does, whoever or whatever wrote it, and the description says what changes for the athlete
- [ ] Commits signed off (`git commit -s`), and the change copies no plan, book or third-party code beyond what its licence allows
- [ ] `make test`
- [ ] `make lint`
- [ ] Planner/menu change: pasted the readings from `planner_menu_diff`, `planner_fingerprints` and `planner_score_quality --matrix` (see CONTRIBUTING.md's validation ladder)
- [ ] Menu or band review, in this order: read `docs/quality/README.md` (what checks a plan); rendered the goal's plans with `make run-devtool TOOL=planner_sweep_site ARGS='--goals <goal>'` and read them as the athlete would; read the goal's rules in `docs/quality/rules.md` and fixed a wrong band in `bench/rulebook.yaml`; ran `make docs`
- [ ] Athlete-facing text checked: no citations or dev-speak (AGENTS.md, Coach invariants)
- [ ] Screenshots attached, for UI changes
