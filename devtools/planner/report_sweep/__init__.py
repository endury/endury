"""User-facing planner plan report — "the sweep".

The product half of the planner devtools: render a generated plan for
human inspection. ``report_cli`` runs a scenario and produces the console
summary, diagnostic charts, and calendar grid (CLI:
``planner_scenario_report``); ``sweep_site`` writes every goal x ambition plan
into one interactive HTML page, week by week (``planner_sweep_site``). Imports
only from ``_shared``.
"""
