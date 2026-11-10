/* Endury — Python worker. Runs the unchanged Python app (planner, coach,
   intervals.icu adapter, store) in Pyodide, off the UI thread.

   Boot: the site's own copy of Pyodide (`make site` stages the release
   under site/pyodide/, so the page fetches nothing from a third party),
   pinned to one version (so its lock pins every package version too), the
   packages the app imports, then the Endury bundle named by the site's
   version.json. The build reads the version and PACKAGES from this file.

   Protocol (see data/api.js):
     page → worker  {id, method, params}
     worker → page  {id, envelope}   envelope = endury.browser.rpc.dispatch's
                                     {ok, result | error, error_code, …}
                    {id, progress}   one of the planner's progress frames,
                                     posted while generate_plan / replan run
                    {status}         boot progress text for the boot screen
                    {relay, payload} an analysis script for the sandbox
                                     (data/analysis_sandbox.js), posted while
                                     the chat call that wrote it still runs
     page → worker  {relay_result, result | error}   its answer
   Calls run one at a time, in arrival order: the Python side holds one
   in-memory database and one store revision, and interleaving two calls
   through them would race. A relay's answer skips that queue: the call
   waiting for it holds the queue. A boot failure answers every call with
   the same error; the page recovers by reloading. */

// This file is served from site/<build-id>/worker/; the runtime sits at the
// site root, outside the immutable build dir, like version.json below.
const PYODIDE_URL = new URL('../../pyodide/v314.0.7/', self.location).href;
const PACKAGES = [
  'numpy', 'scipy', 'cvxpy-base', 'clarabel', 'pydantic', 'httpx', 'openai', 'pyyaml',
];

// JSON across the boundary both ways: plain data with Python's own None /
// dict / list semantics, and no PyProxy lifetimes to manage on the JS side.
const GLUE = `
import inspect
import json

import httpx._transports.jsfetch as jsfetch

from endury.browser.rpc import dispatch, set_analysis_executor

# httpx's synchronous requests suspend Python through WebAssembly stack
# switching where the browser offers it, and on Safari 27 such a request
# never returns. The synchronous XHR every browser's worker supports
# answers instead, with the client's timeout.
jsfetch.can_run_sync = lambda: False


async def run_in_sandbox(payload):
    return json.loads(await run_analysis(json.dumps(payload)))


set_analysis_executor(run_in_sandbox)


async def call(method, params_json, report):
    def progress(event):
        report(json.dumps(event))

    result = dispatch(method, json.loads(params_json), progress=progress)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, str) else json.dumps(result, default=str)


call
`;

const status = (text) => self.postMessage({ status: text });

/* The coach's analysis scripts run in the sandbox worker, which the page
   owns and which holds no key; GLUE awaits this from Python. */
let nextRelay = 0;
const relays = new Map();

function runAnalysis(payload) {
  const rid = ++nextRelay;
  self.postMessage({ relay: rid, payload });
  return new Promise((resolve, reject) => relays.set(rid, { resolve, reject }));
}

function settleRelay({ relay_result: rid, result, error }) {
  const relay = relays.get(rid);
  relays.delete(rid);
  if (error !== undefined) relay?.reject(new Error(error));
  else relay?.resolve(result);
}

async function fetchOk(url, init) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`Could not load ${url} (HTTP ${res.status})`);
  return res;
}

async function start() {
  status('Loading the Python runtime…');
  const { loadPyodide } = await import(`${PYODIDE_URL}pyodide.mjs`);
  const pyodide = await loadPyodide({ indexURL: PYODIDE_URL });
  status('Loading the planning libraries…');
  await pyodide.loadPackage(PACKAGES);
  status('Loading Endury…');
  // This file is served from site/<build-id>/worker/; version.json and the
  // bundle it names sit at the site root, outside the immutable build dir.
  const versionUrl = new URL('../../version.json', self.location);
  const version = await (await fetchOk(versionUrl, { cache: 'no-cache' })).json();
  // This worker lives at <site>/<build-id>/worker/. A page served from a
  // stale cache (an installed PWA ignores no-cache) must not run its old JS
  // against a newer Python bundle: the RPC surface may have moved.
  const ownBuild = new URL('..', self.location).pathname.split('/').filter(Boolean).pop();
  if (version.build && ownBuild !== version.build) {
    throw new Error('Endury was updated — reload the page to continue.');
  }
  const bundle = await fetchOk(new URL(version.bundle, versionUrl));
  pyodide.unpackArchive(await bundle.arrayBuffer(), 'zip');
  status('Starting Endury…');
  pyodide.globals.set('run_analysis', runAnalysis);
  return pyodide.runPython(GLUE);
}

/* A Python traceback's last line ("ValueError: …") is the message; the full
   traceback goes to the console. */
function errorText(err) {
  const lines = String(err?.message || err).trim().split('\n');
  return lines[lines.length - 1] || 'The Python worker failed';
}

let ready = null;

async function run(id, method, params) {
  try {
    ready ??= start();
    const call = await ready;
    if (method === 'boot') {
      status(params?.settings?.demo ? 'Building the demo athlete…' : 'Reading your data from intervals.icu…');
    }
    // The planner's progress frames reach the page under the call's id while
    // the call itself still runs.
    const report = (json) => self.postMessage({ id, progress: JSON.parse(json) });
    return JSON.parse(await call(method, JSON.stringify(params ?? {}), report));
  } catch (err) {
    console.error(err);
    return { ok: false, error: errorText(err), error_code: 'worker_error' };
  }
}

let queue = Promise.resolve();

self.onmessage = ({ data }) => {
  if ('relay_result' in data) {
    settleRelay(data);
    return;
  }
  const { id, method, params } = data;
  queue = queue
    .then(() => run(id, method, params))
    .then((envelope) => self.postMessage({ id, envelope }));
};
