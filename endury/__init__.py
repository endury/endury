"""Endury — a free, open-source endurance coach that runs in the browser.

A deterministic training planner, an intervals.icu adapter (the sole
training-data and storage platform) and an optional chat coach backed by
OpenRouter. The package runs unchanged natively under pytest and inside a
Pyodide Web Worker, where :mod:`endury.browser` is the entry point.

Sub-packages:
    browser: the worker's RPC surface — boot, the method table, the envelope.
    store: the durable tables mirrored to one hidden intervals.icu note.
    coach: the conversational coach (turn loop, tools, Apply cards, LLM client).
    planner: deterministic session-menu training-plan engine.
    planning: the application layer around a stored plan (generate, commit,
        push, replan, adjust, swap, sync status) and its wire models.
    intervals: the intervals.icu client, workout DSL, and calendar push.
    database: SQLite layer with the repository pattern (in memory in the
        browser).
    domain: shared domain models and zone analysis.
    training: host-free training-science kernel (load, zones, workouts).
    common: shared clock, exceptions and memory-document conventions.
    wiring: configuration and dependency assembly.
"""
