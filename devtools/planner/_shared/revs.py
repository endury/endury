"""The Python package a git rev carries: ``endury``, or ``coachie`` in older revs.

The planner oracles (``menu_canon``, ``menu_diff``, ``plan_fingerprints``)
run the same dump against the working tree and a ``git archive`` of an older
rev, so they must find the package under whichever name that rev used.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Package directory names, newest first. Older revs carry ``coachie``; the
#: intervals.icu identifiers still do (``coachie:store``).
PACKAGE_NAMES = ("endury", "coachie")

#: Prelude for the oracles' ``python -c`` dump programs: binds ``PKG`` to the
#: package in the current directory, asserts it resolved from there (not from
#: an installed copy), and offers ``_mod("planner.x")`` for imports under it.
PACKAGE_PRELUDE = f"""
import importlib, os
PKG = next(name for name in {PACKAGE_NAMES!r} if os.path.isdir(name))
_pkg = importlib.import_module(PKG)
assert os.path.abspath(_pkg.__file__).startswith(os.getcwd()), (
    PKG + " resolved outside the dump directory: " + _pkg.__file__
)

def _mod(path):
    return importlib.import_module(PKG + "." + path)
"""


def package_at(repo_root: Path, rev: str) -> str:
    """Return the package directory name present at git *rev*.

    Raises:
        RuntimeError: The rev carries none of :data:`PACKAGE_NAMES`.
    """
    for name in PACKAGE_NAMES:
        found = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-e", f"{rev}:{name}"],
            capture_output=True,
            check=False,
        )
        if found.returncode == 0:
            return name
    raise RuntimeError(f"no package directory at {rev!r} (looked for {PACKAGE_NAMES})")
