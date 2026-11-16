"""Run one model-written analysis script against one payload.

In the browser this runs inside the sandbox worker (``web/worker/
analysis_worker.js``), which holds no key and no network; natively (tests,
devtools) it runs in-process. Either way the result is plain JSON: what the
script printed, a short error if it failed, and its charts.
"""

from __future__ import annotations

import contextlib
import io
import traceback
from typing import Any

import numpy as np

from endury.analysis import library
from endury.analysis.charts import checked_specs

#: The model sees at most this much printed output per script.
MAX_OUTPUT_CHARS = 4000
#: Source lines and the closing frame kept from a traceback.
MAX_TRACE_LINES = 6

SCRIPT_NAME = "analysis.py"


def _short_error(exc: BaseException, code: str) -> str:
    """The script's own failing lines and the exception, never file paths."""
    frames = traceback.extract_tb(exc.__traceback__)
    source = code.splitlines()
    lines = [
        f"line {f.lineno}: {source[f.lineno - 1].strip()}"
        for f in frames
        if f.filename == SCRIPT_NAME and f.lineno and 0 < f.lineno <= len(source)
    ]
    if frames and frames[-1].filename != SCRIPT_NAME:
        lines.append(f"inside ana.{frames[-1].name}")
    return "\n".join(
        [*lines[-MAX_TRACE_LINES:], *traceback.format_exception_only(exc)]
    ).strip()


def run_script(payload: dict[str, Any]) -> dict[str, Any]:
    """Load the payload into the library, run its ``code``, return the result.

    Args:
        payload: ``code`` plus the data :func:`library.load` takes.

    Returns:
        ``{"stdout", "error", "charts"}``: the printed output (cut at
        :data:`MAX_OUTPUT_CHARS`), a short error or None, and the checked
        chart specs, those drawn before an error included.
    """
    code = str(payload.get("code") or "")
    library.load(payload)
    out = io.StringIO()
    error = None
    try:
        with contextlib.redirect_stdout(out):
            compiled = compile(code, SCRIPT_NAME, "exec")
            # intent: the script is the coach model's; it runs here only
            # inside the sandbox worker (no key, no network) or in tests.
            exec(compiled, {"ana": library, "np": np, "__name__": "__main__"})  # noqa: S102
    except Exception as exc:  # the error goes back to the model to fix
        error = _short_error(exc, code)
    text = out.getvalue()
    if len(text) > MAX_OUTPUT_CHARS:
        cut = f"[output cut at {MAX_OUTPUT_CHARS:,} of {len(text):,} characters]"
        text = f"{text[:MAX_OUTPUT_CHARS]}\n{cut}"
    return {
        "stdout": text,
        "error": error,
        # A chart drawn before a later line failed is whole: a script that
        # charted and then tripped over a print keeps its chart.
        "charts": checked_specs(list(library.CHARTS)),
    }


async def run_local(payload: dict[str, Any]) -> dict[str, Any]:
    """The native executor: :func:`run_script` in this process.

    For tests and devtools only; the browser runs scripts in the sandbox
    worker, never beside the keys.
    """
    return run_script(payload)
