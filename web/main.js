/* Endury — boot: pick the shell by viewport (or ?shell= override),
   register the service worker, finish a "Connect OpenRouter" round trip
   (?code=), show the front door when there is no intervals.icu key (the
   landing: the demo, the guided connect flow, or straight to the key),
   start the Python worker, show the ready screen after a first connect,
   then hand off to the shell module. */

import { api, bootRuntime } from './data/api.js';
import {
  loadSettings, saveSettings, hasRequiredSettings, isDemo,
} from './data/settings.js';
import { finishConnect } from './data/openrouter_connect.js';
import { createBootScreen } from './components/boot_screen.js';
import { showLanding, runConnect, showReady } from './components/front_door.js';
import { mountRuntimeBanner } from './components/runtime_banner.js';

const params = new URLSearchParams(location.search);
const override = params.get('shell');
const isMobile = override ? override === 'mobile'
  : window.matchMedia('(max-width: 768px)').matches;

const shellPath = isMobile ? './shells/mobile/index.js' : './shells/web/index.js';

document.documentElement.dataset.shell = isMobile ? 'mobile' : 'web';

/* Resolves once a service worker controls the page (at most ~2 s): a worker
   started under it has Pyodide and the bundle cached on the first visit
   already, not only from the second. */
function registerServiceWorker() {
  if (!('serviceWorker' in navigator)) return Promise.resolve();
  // Relative to the page, not this module: sw.js sits beside index.html so
  // its scope covers the whole site, under a Pages sub-path too.
  const registered = navigator.serviceWorker.register('./sw.js');
  if (navigator.serviceWorker.controller) {
    registered.catch(() => { /* non-fatal */ });
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true });
    // No controllerchange is coming when registration fails, or when an
    // already-active worker skips this page (a hard reload): stop waiting.
    registered.then((reg) => { if (!reg.installing && !reg.waiting) resolve(); }, resolve);
    setTimeout(resolve, 2000);
  });
}

// Nothing is cached until the visitor has started: a returning athlete at
// once, a new one when they pick a door.
let swReady = null;
const startCaching = () => (swReady ??= registerServiceWorker());
const shell = import(shellPath);
const screen = createBootScreen({ firstVisit: !navigator.serviceWorker?.controller });

let settings = loadSettings();
let connectError = null;
try {
  const key = await finishConnect(settings.openrouter_base_url, screen.status);
  if (key) {
    settings = { ...settings, openrouter_api_key: key };
    saveSettings(settings);
  }
} catch (err) {
  connectError = err.message;
}
// Whether the athlete came through the connect flow: they then see the
// ready screen once the first boot is done. A corrected key reloads the
// page first, so the flag rides across in sessionStorage.
const READY_KEY = 'endury.ready';
let connected = false;
try {
  connected = sessionStorage.getItem(READY_KEY) === '1';
  sessionStorage.removeItem(READY_KEY);
} catch { /* blocked storage: no ready screen after a corrected key */ }
if (connectError || !hasRequiredSettings(settings)) {
  for (;;) {
    const door = await showLanding(screen.body, { error: connectError });
    connectError = null;
    if (door === 'demo') {
      settings = { ...settings, demo: '1' };
      saveSettings(settings);
      break;
    }
    const next = await runConnect(screen.body, settings, { startAt: door === 'connect' ? 'log' : 'key' });
    if (next) {
      settings = next;
      connected = true;
      break;
    }
  }
}

await startCaching();
let startFresh = false;
for (;;) {
  screen.status(startFresh ? 'Starting fresh…' : 'Starting…');
  try {
    await bootRuntime({ settings, startFresh, onStatus: screen.status });
    break;
  } catch (err) {
    const choice = await screen.fail(err.message, { corrupt: err.code === 'store_corrupt', details: err.details });
    if (choice === 'fresh') {
      startFresh = true;
      continue;
    }
    // The worker was booted with the old keys: new ones need a new worker.
    if (choice === 'settings') {
      const next = await runConnect(screen.body, settings, { startAt: 'key', intro: 'Check your key.' });
      try {
        if (next) sessionStorage.setItem(READY_KEY, '1');
      } catch { /* blocked storage: no ready screen after the reload */ }
    }
    location.reload();
    await new Promise(() => {});
  }
}

let answer = 'look';
if (connected) {
  screen.status('Checking your intervals.icu…');
  // Best-effort each: a failed read shows its calm or warn variant.
  const [bootstrap, inputs] = await Promise.all([
    api.plannerBootstrap().catch(() => null),
    api.getInputs().catch(() => null),
  ]);
  answer = await showReady(screen.body, {
    settings, bootstrap, inputs, recheck: () => api.getInputs(),
  });
}

screen.close();
mountRuntimeBanner(document.getElementById('cb-banner'), { demo: isDemo(settings) });
const { mount } = await shell;
mount(document.getElementById('app'));
// "Plan my race": the shells' own open-plan action (the race pill, the
// rail's Create button), delegated on document.
if (answer === 'plan') document.querySelector('[data-action="open-plan"]')?.click();
