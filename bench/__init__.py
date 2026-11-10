"""A benchmark for long endurance training plans.

Grades a complete plan, every week and every session with its steps, against
a public rulebook (``rulebook.yaml``) whose bands come from the coaching and
sports-science literature and the maintainer's judgement. Contestants are chat
models (through OpenRouter, the saved Claude answers through Claude Code) and any
local planner that exports into the
same plan table. The package imports nothing from ``endury``: Endury is one
contestant, exported by ``devtools/bench``.
"""

VERSION = "0.1"
