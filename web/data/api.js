/* Endury — API layer. The exports the shells call, answered by the
   Python worker (worker/py_worker.js) rather than a server: each call
   posts {method, params} — params named like a route's query/path
   params, a body's fields spread in — and unwraps the
   {ok, result | error, error_code} envelope endury.browser.rpc.dispatch
   answers with. A failed envelope throws an Error carrying `code` (the
   envelope's error_code). All shape mapping happens in adapters.js; nothing
   here transforms data. */

import { runInSandbox } from './analysis_sandbox.js';
import { bootSettings } from './settings.js';

let worker = null;
let nextId = 1;
const pending = new Map();
let onBootStatus = () => {};

/* What the banners show (components/runtime_banner.js):
   unsaved  — a change is not on intervals.icu yet; the next call retries it
   conflict — another device saved first; this copy must reload
   readOnly — another tab holds the writer lock, or the saved data is newer
              than this copy of Endury; writes are refused here */
const runtime = { unsaved: false, conflict: false, readOnly: false, readOnlyReason: '' };
const listeners = new Set();

const OTHER_TAB = 'Endury is open in another tab, so this one is read-only. '
  + 'Close the other tab and reload to make changes here.';
const NEWER_DATA = 'Your saved Endury data is newer than this copy of the app, so it '
  + 'is read-only. Reload to update.';

// The RPCs that write to intervals.icu or the store — refused up front in a
// read-only tab rather than half-run.
const MUTATING = new Set([
  'planner_commit', 'planner_delete', 'planner_push', 'replan', 'reschedule_session',
  'pair_activity', 'unlink_activity', 'put_self_assessment', 'put_memory', 'chat', 'apply_proposal',
  'delete_ad_hoc_workout', 'put_thresholds', 'save_activity_take', 'put_activity_verdict',
]);

/** Subscribe to the runtime status; `fn` runs now and on every change. */
export function onRuntimeChange(fn) {
  listeners.add(fn);
  fn(runtime);
  return () => listeners.delete(fn);
}

function setRuntime(patch) {
  if (Object.entries(patch).every(([k, v]) => runtime[k] === v)) return;
  Object.assign(runtime, patch);
  listeners.forEach((fn) => fn(runtime));
}

function startWorker() {
  const w = new Worker(new URL('../worker/py_worker.js', import.meta.url), { type: 'module' });
  w.onmessage = ({ data }) => {
    if ('status' in data) {
      onBootStatus(data.status);
      return;
    }
    if ('progress' in data) {
      pending.get(data.id)?.onProgress?.(data.progress);
      return;
    }
    if ('relay' in data) {
      // An analysis script from the chat call still running: through the
      // sandbox and back, answered under the relay's own id.
      runInSandbox(data.payload).then((answer) => w.postMessage({ relay_result: data.relay, ...answer }));
      return;
    }
    pending.get(data.id)?.resolve(data.envelope);
    pending.delete(data.id);
  };
  // Only a worker that could not even load lands here (Python errors come
  // back as envelopes): fail everything waiting, and everything after.
  w.onerror = (ev) => {
    const err = new Error(`Endury could not start: ${ev.message || 'the Python worker failed to load'}`);
    pending.forEach((p) => p.reject(err));
    pending.clear();
    worker = null;
  };
  return w;
}

/* `onProgress` receives the planner's progress frames the worker posts while
   this call runs (generate_plan and replan only). */
function post(method, params, onProgress) {
  if (!worker) return Promise.reject(new Error('Endury is not running — reload the page.'));
  const id = nextId++;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject, onProgress });
    worker.postMessage({ id, method, params });
  });
}

async function call(method, params = {}, { onProgress } = {}) {
  if (runtime.readOnly && MUTATING.has(method)) throw new Error(runtime.readOnlyReason);
  const env = await post(method, params, onProgress);
  if (typeof env.unsaved === 'boolean') setRuntime({ unsaved: env.unsaved });
  if (env.error_code === 'store_conflict') setRuntime({ conflict: true });
  if (env.read_only === true || env.error_code === 'store_read_only') {
    setRuntime({ readOnly: true, readOnlyReason: runtime.readOnlyReason || NEWER_DATA });
  }
  if (!env.ok) {
    const err = new Error(env.error || `${method} failed`);
    err.code = env.error_code;
    err.envelope = env;
    throw err;
  }
  return env.result;
}

/* One writer per browser: the first tab holds the lock for its whole life,
   a second one gets none and stays read-only. Without the Locks API (an
   insecure origin) every tab writes, as before. */
function holdWriterLock() {
  if (!navigator.locks) return Promise.resolve(true);
  return new Promise((resolve) => {
    navigator.locks.request('endury-writer', { ifAvailable: true }, (lock) => {
      resolve(Boolean(lock));
      return lock ? new Promise(() => {}) : undefined;
    }).catch(() => resolve(true));
  });
}

let writerLock = null;

// A boot this long without a status line or an answer is stuck: the
// worker died (a phone's browser can kill it for memory with no error
// event) or a request never came back. Generous, since a first visit
// downloads the 34 MB engine under one status line.
const BOOT_STALL_MS = 120_000;

/* What a failed boot shows under "Details for support": the build, the
   error, each status line with its second, and the browser. No settings. */
function bootReport(err, steps) {
  const standalone = typeof matchMedia === 'function' && matchMedia('(display-mode: standalone)').matches;
  return [
    'Endury boot report',
    `Build: ${new URL('..', import.meta.url).pathname.split('/').filter(Boolean).pop()} · ${new Date().toISOString()}`,
    `Error: ${err.message}${err.code ? ` [${err.code}]` : ''}`,
    'Steps:',
    ...steps.map((s) => `  ${s}`),
    `Browser: ${navigator.userAgent}`,
    `Online: ${navigator.onLine ? 'yes' : 'no'} · Memory: ${navigator.deviceMemory ? `${navigator.deviceMemory} GB` : 'unknown'}`
      + ` · Installed app: ${standalone ? 'yes' : 'no'} · Read-only: ${runtime.readOnly ? 'yes' : 'no'}`,
  ].join('\n');
}

/** Save a pending change now instead of on the next call (dispatch saves
    whenever the store is dirty). */
export function flushStore() {
  if (!runtime.unsaved || runtime.readOnly) return Promise.resolve();
  return call('health').catch(() => {});
}

/** Start the worker and boot Python: settings → intervals.icu client →
    store load → chat history (from intervals.icu) → coach. Resolves with
    boot's result; throws like any call (a `store_corrupt` code means the
    saved note is unreadable, and only the athlete's own click may pass
    `startFresh`), its error carrying `details`, the support report. A boot
    with no status line or answer for `stallMs` stops the worker and
    throws. `onStatus` receives the worker's progress lines. */
export async function bootRuntime({ settings, startFresh = false, onStatus, stallMs = BOOT_STALL_MS }) {
  const t0 = performance.now();
  const steps = [];
  let timer = null;
  let stalled = null;
  const stall = new Promise((_, reject) => { stalled = reject; });
  // Each status line restarts the clock; silence past it ends the worker.
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      worker?.terminate();
      worker = null;
      pending.clear();
      const last = steps.length ? steps[steps.length - 1].replace(/^[\d.]+ s {2}/, '') : 'starting';
      stalled(new Error(`Endury stopped responding while “${last}” (no answer for ${Math.round(stallMs / 1000)} s).`));
    }, stallMs);
  };
  onBootStatus = (text) => {
    steps.push(`${((performance.now() - t0) / 1000).toFixed(1)} s  ${text}`);
    arm();
    onStatus?.(text);
  };
  try {
    return await Promise.race([bootCall(settings, startFresh, arm), stall]);
  } catch (err) {
    err.details = bootReport(err, steps);
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

async function bootCall(settings, startFresh, arm) {
  writerLock ??= holdWriterLock().then((held) => {
    if (!held) setRuntime({ readOnly: true, readOnlyReason: OTHER_TAB });
    // A tab going to the background may never come back: flush then.
    if (held) {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') flushStore();
      });
    }
  });
  await writerLock;
  if (startFresh && runtime.readOnly) throw new Error(runtime.readOnlyReason);
  worker ??= startWorker();
  const params = { settings: bootSettings(settings) };
  if (startFresh) params.start_fresh = true;
  // A tab without the writer lock boots read-only, so its Python side never
  // saves either — not even what a calendar read derives.
  if (runtime.readOnly) params.read_only = true;
  arm();
  const result = await call('boot', params);
  // Data written by a newer Endury loads read-only: say so now, not on the
  // first refused write.
  if (result && result.read_only) {
    setRuntime({ readOnly: true, readOnlyReason: runtime.readOnlyReason || NEWER_DATA });
  }
  return result;
}

// A completed activity's trace is a full activity read plus its streams
// (~25 KB per stream-hour), and the session detail and the debrief one tap
// later both draw it — so the promise is kept per id for the page's life. A
// failed read is dropped again, so the next open retries.
const traceReads = new Map();
function activityTrace(id) {
  if (!traceReads.has(id)) {
    const read = call('activity_trace', { activity_id: String(id) });
    read.catch(() => traceReads.delete(id));
    traceReads.set(id, read);
  }
  return traceReads.get(id);
}

export const api = {
  calendarData: (startDate, endDate) =>
    call('calendar_data', { start_date: startDate, end_date: endDate }),
  plannerBootstrap: () => call('planner_bootstrap'),
  plannerActive: () => call('planner_active'),
  putSelfAssessment: (body) => call('put_self_assessment', body),
  plannerMacro: () => call('planner_macro'),
  plannerCommit: (body) => call('planner_commit', body),
  plannerDelete: () => call('planner_delete'),
  health: () => call('health'),
  welcome: () => call('welcome'),
  getInputs: () => call('get_inputs'),
  /* One sport's thresholds → every matching intervals.icu sport row;
     answers { types, inputs } with the fresh get_inputs payload. */
  putThresholds: (body) => call('put_thresholds', body),
  getRaces: () => call('get_races'),
  conversationRecent: (limit = 20) => call('conversation_recent', { limit }),
  resetChat: () => call('reset_chat'),
  getMemory: () => call('get_memory'),
  getMemoryHistory: () => call('get_memory_history'),
  putMemory: (content) => call('put_memory', { content }),
  cleanupMemory: () => call('cleanup_memory'),
  rescheduleSession: (id, date) =>
    call('reschedule_session', { session_id: Number(id), date }),
  deleteAdHocWorkout: (externalId) =>
    call('delete_ad_hoc_workout', { external_id: externalId }),
  activityDebrief: (id) => call('activity_debrief', { activity_id: String(id) }),
  activityTrace,
  /* The coach's saved takes on activities ({takes: [{activity_id, depth,
     text, created_at}]}); saving one answers the same shape. */
  activityTakes: () => call('activity_takes'),
  /* A brief's takes ride the same note: kind 'brief', the subject handle
     ('session:412', 'race:2026-10-24') in place of the activity id. */
  saveActivityTake: (id, depth, text, kind = 'take') =>
    call('save_activity_take', {
      activity_id: String(id), depth, text, ...(kind === 'take' ? {} : { kind }),
    }),
  /* The deterministic brief sheet for a coming session or race:
     { session_id } or { race_date }. */
  sessionBrief: (params) => call('session_brief', params),
  /* The athlete's own verdict on a completed activity: { activity_id, feel }
     (1 strong … 5 weak) and/or { activity_id, icu_rpe } (1–10); answers
     { feel, rpe } as intervals.icu now holds them. */
  putActivityVerdict: (body) => call('put_activity_verdict', body),
  pairActivity: (id, body) => call('pair_activity', { activity_id: String(id), ...body }),
  unlinkActivity: (id) => call('unlink_activity', { activity_id: String(id) }),
  /* Coach Apply cards: run a proposal's write, or drop it. Both answer with
     apply_proposal's result — a stale card comes back as a replacement. */
  applyProposal: (proposal) => call('apply_proposal', { proposal }),
  /* Bring an open card up to date after another card wrote the plan: the
     same result shape, 'current' when nothing moved. Never writes. */
  refreshProposal: (proposal) => call('refresh_proposal', { proposal }),
  dismissProposal: (proposal) => call('dismiss_proposal', { proposal }),
};

/* A coach turn is one call; its outcome is replayed as the events the chat
   route used to stream: one `tool` per tool that ran, then `done` with the
   reply, its Apply-card `proposals`, its `charts` and `plan_changed` — or
   one `error`. `isolated` runs the turn outside the conversation: the
   model sees no history and the turn is not kept (takes and briefs). */
export async function chatStream(message, onEvent, { isolated = false } = {}) {
  let result;
  try {
    result = await call('chat', { message, isolated });
  } catch (err) {
    onEvent({ type: 'error', text: err.message });
    return;
  }
  // A turn that failed inside Python (an LLM or tool error) comes back as an
  // ok envelope carrying the chat route's final error event, not a reply.
  if (result.type === 'error') {
    onEvent({ type: 'error', text: result.text || 'The coach could not reply.' });
    return;
  }
  for (const name of result.tools_used || []) onEvent({ type: 'tool', text: name });
  const done = {
    type: 'done',
    text: result.reply ?? result.text ?? '',
    proposals: result.proposals || [],
    charts: result.charts || [],
    plan_changed: Boolean(result.plan_changed),
  };
  if (result.reason) done.reason = result.reason;
  onEvent(done);
}

/* Generate and replan are single calls too. onEvent gets the planner's
   progress frames ({type:'progress', step, total, message}) as the worker
   posts them during the call, then once the planner's `result` payload or
   an `error` carrying error_message and error_code — the same two final
   events the SSE streams ended on. */
const resultEvent = (result) => ({ type: 'result', source: 'planner', ...result });
const errorEvent = (err) => ({
  type: 'error', source: 'planner', error_message: err.message, error_code: err.code,
});

export async function generatePlanStream(body, onEvent) {
  try {
    onEvent(resultEvent(await call('generate_plan', body, { onProgress: onEvent })));
  } catch (err) {
    onEvent(errorEvent(err));
  }
}

/* When the rewrite window holds intervals.icu events the athlete edited by
   hand, replan refuses with {error_code: "confirm_required", error_message,
   diff}. That refusal is returned (not passed to onEvent) so the caller can
   show the diff and repeat the call with {confirm: true}; a normal run
   resolves null. */
export async function replanStream(onEvent, { confirm = false } = {}) {
  let result;
  try {
    result = await call('replan', { confirm }, { onProgress: onEvent });
  } catch (err) {
    if (err.code === 'confirm_required') {
      return { error_code: err.code, error_message: err.message, diff: err.envelope.diff };
    }
    onEvent(errorEvent(err));
    return null;
  }
  if (result?.error_code === 'confirm_required') return result;
  onEvent(resultEvent(result));
  return null;
}

/* Sync the active plan's upcoming workouts to the intervals.icu calendar:
   {status, plan_id, push:{...}} or {status:"error"}. */
export const plannerPush = () => call('planner_push');

/* How the active plan's upcoming sessions stand on intervals.icu:
   {available, summary:{...}, out_of_sync, sessions:{slot_id:{state,...}}}. */
export const plannerSyncStatus = () => call('planner_sync_status');
