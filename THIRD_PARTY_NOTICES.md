<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Third-party notices

Endury is licensed under the AGPL-3.0-or-later (see [NOTICE](NOTICE) and
[LICENSE](LICENSE)). This page is for people checking what else ships with it.

## In the repository

| Material | Licence | Where |
| --- | --- | --- |
| Inter variable font | SIL Open Font License 1.1 | `web/vendor/inter/`, with its licence text |
| Code of Conduct | Creative Commons Attribution 4.0 | Adapted from the Contributor Covenant 2.1 |
| Saved AI answers | Not Endury's; see their notice | `bench/results/raw/`, [notice](bench/results/README.md) |
| Licence texts of the runtime | Each its own | `devtools/site/runtime_licenses/` |

## Served with the site

Endury's Python runs in a Pyodide Web Worker. `make site` copies Pyodide and a
fixed set of Python packages into `site/pyodide/`, so the page loads them from
the site itself. `web/worker/py_worker.js` pins them.

`make site` also writes `THIRD_PARTY_LICENSES.txt` into the site from what it
staged: every wheel with its exact version, licence and full licence text, the
crates compiled into the Rust wheels, and the texts for the runtime. A wheel
with no licence file stops the build, so the list cannot fall behind.

These wheels are Pyodide's own builds, pinned by the sha256 in its lock, not the
files on PyPI. Some are patched by Pyodide, and its `httpx` comes from a fork.
Their exact sources are the [Pyodide 314.0.7 tag](https://github.com/pyodide/pyodide/tree/314.0.7)
and [pyodide-recipes](https://github.com/pyodide/pyodide-recipes), which holds
each package's source URL and patches.

The site also serves `LICENSE`, `NOTICE` and `source/endury-source.tar.gz`, the
source of the commit `version.json` names, so the offer of source does not depend
on any one host.

## Build and test dependencies

The Python dependencies used to build the site and run its tests are pinned in
`poetry.lock`. None of them ship in the built site.
