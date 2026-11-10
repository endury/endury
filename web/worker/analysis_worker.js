/* Endury — analysis sandbox. Runs the coach's analysis scripts: short numpy
   scripts the AI model writes against the athlete's data (endury/analysis/).

   A second Pyodide with numpy and the Endury bundle, holding no key: the
   page (data/analysis_sandbox.js) hands it one payload at a time — the
   script and the data it may read — and it answers with what the script
   printed and the charts it made. Once loaded it removes its own network
   and storage, so a script can read only what it was given. The site's
   Content Security Policy completes the seal, so the site needs it on every
   host (README). The Pyodide version and the bundle come from the site's
   version.json, so py_worker.js stays their one home.

   Protocol: page → sandbox {rid, payload}   payload = the run's JSON
             sandbox → page {rid, result}    result  = run_script's JSON
                            {rid, error}     the sandbox itself failed */

const RUNNER = `
import json

from endury.analysis.runner import run_script


def run(payload_json):
    return json.dumps(run_script(json.loads(payload_json)))


run
`;

// What a script could reach the world or the app's storage through.
const SEALED = [
  'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'WebTransport', 'importScripts',
  'Worker', 'SharedWorker', 'BroadcastChannel', 'indexedDB', 'caches', 'Request',
  'WebSocketStream', 'FontFace', 'fonts', 'createImageBitmap',
];

/* Remove each capability from the global and from every prototype that
   carries it, so `js.fetch` and its prototype route both come up empty. */
function seal() {
  for (const name of SEALED) {
    for (let obj = self; obj; obj = Object.getPrototypeOf(obj)) {
      if (Object.prototype.hasOwnProperty.call(obj, name)) {
        try { delete obj[name]; } catch { /* non-configurable: shadowed below */ }
      }
    }
    try {
      Object.defineProperty(self, name, { value: undefined, writable: false, configurable: false });
    } catch { /* already gone */ }
  }
  try { Object.defineProperty(navigator, 'locks', { value: undefined }); } catch { /* absent */ }
}

async function start() {
  // Served from site/<build-id>/worker/; version.json sits at the site root.
  const versionUrl = new URL('../../version.json', self.location);
  const version = await (await fetch(versionUrl, { cache: 'no-cache' })).json();
  const base = new URL(`../../pyodide/v${version.pyodide}/`, self.location).href;
  const { loadPyodide } = await import(`${base}pyodide.mjs`);
  const pyodide = await loadPyodide({ indexURL: base });
  await pyodide.loadPackage(['numpy']);
  const bundle = await fetch(new URL(version.bundle, versionUrl));
  pyodide.unpackArchive(await bundle.arrayBuffer(), 'zip');
  const run = pyodide.runPython(RUNNER);
  seal();
  return run;
}

let ready = null;

self.onmessage = async ({ data }) => {
  const { rid, payload } = data;
  try {
    ready ??= start();
    const run = await ready;
    self.postMessage({ rid, result: run(payload) });
  } catch (err) {
    console.error(err);
    self.postMessage({ rid, error: String(err?.message || err).trim().split('\n').pop() });
  }
};
