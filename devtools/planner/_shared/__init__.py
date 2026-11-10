"""Shared substrate for the planner devtools.

Both the user-facing report sweep (``report_sweep``) and the
plan-quality scorecard (``devtools.quality.planner``) build on these
modules: the pipeline adapter (``pipeline``), the scenario registry
(``scenarios``), the pipeline-run + plan-metrics engine (``metrics``),
and the session materialization / step-decomposition primitives
(``sessions``). Nothing here imports from either product package, so the
dependency graph stays a DAG (``endury -> _shared -> {report_sweep,
quality.planner}``).
"""
