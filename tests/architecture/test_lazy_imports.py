"""Boot leaves the solver and the AI SDK unloaded.

Opening the installed app on a phone usually boots Pyodide again, because the
browser drops the backgrounded page. cvxpy (with scipy) and the openai SDK are
the costly imports there, and a stored plan needs neither to show the
calendar: they load on first use — a plan generation, a coach call — not with
the app.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.store.helpers import SIM_TODAY

REPO_ROOT = Path(__file__).resolve().parents[2]
HEAVY = ["cvxpy", "openai", "scipy"]

# A fresh interpreter: this test process has long imported all three.
_PROBE = f"""
import json, sys
from endury.browser import rpc

def loaded():
    return sorted({{m.split(".")[0] for m in sys.modules}} & set({HEAVY!r}))

after_import = loaded()

import asyncio
from tests.doubles import FakeIntervals
from tests.store.helpers import run_athlete

settings = {{"intervals_api_key": "fake-key", "openrouter_api_key": "sk-or-v1-fake"}}
fake = FakeIntervals(athlete=run_athlete())
envelope = asyncio.run(rpc.boot(settings, transport=fake.transport()))
assert envelope["ok"], envelope
assert rpc._app is not None and rpc._app.coach is not None, "the coach was not built"
print(json.dumps({{"after_import": after_import, "after_boot": loaded()}}))
"""


def test_boot_leaves_the_solver_and_the_ai_sdk_unloaded():
    env = {**os.environ, "COACHING_BUDDY_SIM_DATE": SIM_TODAY.isoformat()}
    env.pop("OPENROUTER_API_KEY", None)
    run = subprocess.run(
        [sys.executable, "-c", _PROBE],
        check=True,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
    )

    assert json.loads(run.stdout.strip().splitlines()[-1]) == {
        "after_import": [],
        "after_boot": [],
    }
