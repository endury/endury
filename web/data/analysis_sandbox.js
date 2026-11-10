/* Endury — owner of the analysis sandbox (worker/analysis_worker.js), where
   the coach's analysis scripts run with no key and no network. The Python
   worker asks for a run through the page (api.js relays it here) while its
   chat call is still running. The sandbox starts on the first run and stays
   for the next; a script that runs too long ends the sandbox, and the next
   run starts a fresh one. */

// The first run also loads Pyodide and numpy into the sandbox.
const TIMEOUT_MS = 45_000;

let sandbox = null;
let nextRun = 0;
const runs = new Map();

function stop(reason) {
  sandbox?.terminate();
  sandbox = null;
  runs.forEach((run) => {
    clearTimeout(run.timer);
    run.resolve({ error: reason });
  });
  runs.clear();
}

function start() {
  const w = new Worker(new URL('../worker/analysis_worker.js', import.meta.url), { type: 'module' });
  w.onmessage = ({ data }) => {
    const run = runs.get(data.rid);
    if (!run) return;
    runs.delete(data.rid);
    clearTimeout(run.timer);
    run.resolve('error' in data ? { error: data.error } : { result: data.result });
  };
  w.onerror = (ev) => stop(`The analysis could not start: ${ev.message || 'the sandbox failed to load'}`);
  return w;
}

/** Run one analysis payload (a JSON string); resolves to {result} or {error}. */
export function runInSandbox(payload) {
  sandbox ??= start();
  const rid = ++nextRun;
  return new Promise((resolve) => {
    const timer = setTimeout(() => stop('The analysis ran too long and was stopped.'), TIMEOUT_MS);
    runs.set(rid, { resolve, timer });
    sandbox.postMessage({ rid, payload });
  });
}
