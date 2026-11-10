"""Run the benchmark with Endury as a local contestant.

Usage::

    poetry run python -m devtools.bench.run_bench --contestants endury --scenarios headline
    poetry run python -m devtools.bench.run_bench --contestants endury,google/gemini-3.5-flash-lite --n 5 --ceiling 3

The OpenRouter key comes from ``.env`` through the coach config unless
``--api-key`` is given; without a key only saved answers are graded.
"""

from __future__ import annotations

import sys

from bench.run import main as bench_main
from devtools.bench.endury_contestant import endury_plan


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--api-key" not in argv:
        try:
            from endury.wiring.config import get_coach_config

            key = get_coach_config().get_openrouter_api_key()
        except Exception:
            key = None
        if key:
            argv += ["--api-key", key]
    bench_main(argv, local={"endury": endury_plan})


if __name__ == "__main__":
    main()
