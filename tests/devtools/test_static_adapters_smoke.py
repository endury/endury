"""Gate for the Node smoke check of the static wire->view adapters.

pytest only greps the static JavaScript as text; this script is the one
offline check that executes ``data/adapters.js``. Nothing else runs it, so
without this gate it rots silently (an unused-export sweep broke its import
and the whole suite stayed green).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE = "devtools/frontend/static_adapters_smoke.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_static_adapters_smoke_passes() -> None:
    proc = subprocess.run(
        ["node", SMOKE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"{SMOKE} failed:\n{proc.stdout}\n{proc.stderr}"
