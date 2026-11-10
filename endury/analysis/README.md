# endury/analysis

The coach's deep analysis. When a question needs more than a fixed tool
answers (how a race was paced on its climbs, whether easy runs got more
efficient, a week's hours by sport), the coach's AI model writes a short
numpy script against this library and asks for it to run. The script
reads the athlete's history, thresholds and the streams and laps of the
activities it names, prints compact results for the model to read, and
can draw up to three charts that appear under the reply.

The helpers own the definitions: what counts as uphill or walking, a fair
comparison and its sample size, whether a trend is larger than the scatter
between sessions. Left to raw arrays, the AI model picked its own
definitions, and the same question got opposite answers from one run to
the next.

Two files on the same ground compare from their GPS tracks, for any sport.
Each track is resampled every 10 m of its own path, so neither device's
odometer matters; points within 25 m heading the same way are the same
ground, and a run of them moving forward together is a shared stretch,
which survives a GPS gap or a short detour. The shared ground is cut into
climbs, descents and level stretches, and both files are timed over each
piece. Climb numbers would not do: one file finds a climb with a dip as
two. The sandbox never sees a coordinate, only metres from a point the
files of one run share.

The package imports numpy and the standard library only, because the
browser runs these scripts in a sandbox worker
(`web/worker/analysis_worker.js`) that loads nothing else and holds no
key and no network. Natively, tests and devtools run them in-process.

| Module | What it does |
| --- | --- |
| `library.py` | The helpers a script calls as `ana`: per-sport summaries, splits, climbs, laps, best efforts, drift, trends, weekly totals, the overview, route comparison. |
| `charts.py` | The chart spec: kinds, one unit per chart, thinning; checked in the sandbox and again before a chart reaches the reply. |
| `runner.py` | Runs one script against one payload and returns its printed output, a short error and its charts. |

The coach's side, which gathers the data and hands script and data to the
sandbox, is `endury/coach/tools/analysis_tools.py`.

Start reading:
- [library.py](library.py) — `overview` shows every helper at work.
- [runner.py](runner.py) — what a script can see and what comes back.
