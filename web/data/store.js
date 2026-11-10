/* Endury — calendar store: the single owner of session/plan state for
   both shells. Fetches via api.js, adapts via adapters.js, notifies
   subscribers on every change. Shell-local UI state (sheet position,
   drawers) does not live here. */

import { api } from './api.js';
import { buildWeek } from './adapters.js';
import { addDays, mondayOf, toIsoDay } from './format.js';

function todayIso() {
  return toIsoDay(new Date());
}

// Where the athlete was — the cursor week and the selected day — so a quick
// trip to another app lands there again: a phone's browser drops the
// backgrounded app, and every open then boots from scratch. Only the position
// is kept; every week is re-read. Anything longer than this opens on today,
// because that is what the calendar is opened for.
const PLACE_KEY = 'endury.place';
export const PLACE_KEEP_MS = 5 * 60 * 1000;
let lastPlace = '';

/** Where the calendar opens at `nowMs`: the place in `saved` if the athlete
    left it on this same day a moment ago, else today. An old, odd or missing
    record can only ever land on today. */
export function landingPlace(saved, today, nowMs) {
  const age = nowMs - saved?.at;
  const kept = saved?.today === today && age >= 0 && age < PLACE_KEEP_MS
    && /^\d{4}-\d{2}-\d{2}$/.test(saved.day) && mondayOf(saved.day) === saved.week;
  return kept ? { week: saved.week, day: saved.day } : { week: mondayOf(today), day: today };
}

function readPlace() {
  try {
    return JSON.parse(localStorage.getItem(PLACE_KEY));
  } catch {
    return null;
  }
}

/** Write the place when it changed, or when the athlete leaves (`force`): the
    time it keeps is when they were last here. */
function savePlace(state, force = false) {
  const place = JSON.stringify({ today: state.today, week: state.weekStart, day: state.selectedDay });
  if (!force && place === lastPlace) return;
  lastPlace = place;
  try {
    localStorage.setItem(PLACE_KEY, JSON.stringify({ ...JSON.parse(place), at: Date.now() }));
  } catch { /* blocked storage: the next start opens on today */ }
}

// The calendar is one continuous list, loaded in contiguous chunks: a chunk
// is one /calendar/data range read, and the window is the span of weeks the
// shells render. Boot opens on the cursor week with one week of history above.
const CHUNK_WEEKS = 4;
const WINDOW_BEFORE = 1;
const WINDOW_AFTER = 3;

// Chunk fetches in flight, keyed by their range: two scroll sentinels firing
// in the same frame must not read the same weeks twice. A forced read gets a
// key of its own so it never joins a stale in-flight promise.
const pendingChunks = {};
let forceSeq = 0;

/** goal id → {tier, note, offered}, read off bootstrap's own goal catalog.

    The backend owns which goals are experimental (GoalProfile.maturity)
    and which tiers are on offer (the menus); the wizard must never
    hand-maintain a second list here. null = never read, so nothing is
    labelled or hidden rather than guessed. */
function goalMaturityFrom(bootstrap) {
  const rows = bootstrap?.goal_types;
  if (!Array.isArray(rows)) return null;
  return Object.fromEntries(rows.map((g) => [g.id, {
    tier: g.maturity,
    note: g.maturity_note,
    offered: Array.isArray(g.offered_tiers) ? g.offered_tiers : null,
  }]));
}

export function createStore() {
  const today = todayIso();
  const place = landingPlace(readPlace(), today, Date.now());
  const state = {
    today,
    // The cursor week: the one the header, arrows, phase pill and rail
    // describe. Scroll sets it; it is not "the only week loaded".
    weekStart: place.week,
    selectedDay: place.day,
    weeks: {},            // weekStart → week model (adapted)
    windowFrom: null,     // Monday iso — first week rendered
    windowTo: null,       // Monday iso — last week rendered
    loadingWeeks: {},     // Monday iso → true while its chunk is in flight
    syncSessions: {},     // slot_id → intervals.icu sync state (see sync-status)
    plannerActive: null,  // /api/planner/active payload
    athleteData: null,    // /api/planner/bootstrap athlete_data (null = unknown)
    planLimits: null,     // /api/planner/bootstrap plan_limits (null = unknown)
    goalMaturity: null,   // /api/planner/bootstrap goal_types maturity (null = unknown)
    myRaces: [],          // /api/races: still-planned races strictly after today — the wizard's race chips
    takes: [],            // activity_takes: the coach's saved takes on activities, oldest first
    macro: null,          // /api/planner/macro payload
    // plan lifecycle: 'active' | 'none' | 'setup' | 'generating' | 'preview'
    planState: 'none',
    generation: { pct: 0, label: '' },
    preview: null,        // previewed plan payload awaiting commit
    error: null,
    // The /api/planner/active read failed: the plan state is UNKNOWN, which
    // is not the same as "no plan" — see init().
    plannerActiveFailed: false,
  };

  const listeners = new Set();
  const notify = () => {
    savePlace(state);
    listeners.forEach((fn) => fn(state));
  };

  const landListeners = new Set();
  let leftAt = null;

  /** Back on today: the cursor moves first, so a shell can land while the
      weeks load. A new day re-reads everything, since statuses move with it. */
  async function landOnToday(newDay) {
    state.weekStart = mondayOf(state.today);
    state.selectedDay = state.today;
    const outside = !state.windowFrom
      || state.weekStart < state.windowFrom || state.weekStart > state.windowTo;
    landListeners.forEach((fn) => fn());
    if (outside) await initWindow(state.weekStart);
    else notify();
    if (newDay) await store.init();
  }

  /** Every way back in asks the same two things: has the day moved on, and
      was the athlete away for longer than a place is kept? */
  function comeBack() {
    if (document.visibilityState !== 'visible') return Promise.resolve();
    const now = todayIso();
    const newDay = now !== state.today;
    const away = leftAt != null && Date.now() - leftAt >= PLACE_KEEP_MS;
    leftAt = null;
    if (!newDay && !away) return Promise.resolve();
    state.today = now;
    return landOnToday(newDay).catch((err) => {
      state.error = err.message;
      notify();
    });
  }

  const leave = () => {
    leftAt = Date.now();
    savePlace(state, true);
  };

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') leave();
    else comeBack();
  });
  window.addEventListener('pagehide', leave);
  // A page restored from the browser's cache and a window brought forward may
  // never change visibility, and a tab open through midnight or a laptop that
  // wakes on it fire nothing at all: the minute's check is what catches those.
  window.addEventListener('pageshow', comeBack);
  window.addEventListener('focus', comeBack);
  setInterval(comeBack, 60 * 1000);

  /** Every Monday from `fromMonday` to `toMonday`, inclusive, ascending. */
  function mondaysBetween(fromMonday, toMonday) {
    const out = [];
    for (let m = fromMonday; m <= toMonday; m = addDays(m, 7)) out.push(m);
    return out;
  }

  /** A list of Mondays → its runs of consecutive weeks (one range read each). */
  function contiguousRuns(mondays) {
    const runs = [];
    for (const m of mondays) {
      const run = runs[runs.length - 1];
      if (run && addDays(run[run.length - 1], 7) === m) run.push(m);
      else runs.push([m]);
    }
    return runs;
  }

  /** One /calendar/data read covering `chunkWeeks` weeks from `chunkStart`,
      split back into per-week models. */
  function fetchChunk(chunkStart, chunkWeeks, force) {
    const chunkEnd = addDays(chunkStart, (chunkWeeks - 1) * 7);
    const key = force
      ? `${chunkStart}:${chunkEnd}:${(forceSeq += 1)}`
      : `${chunkStart}:${chunkEnd}`;
    if (pendingChunks[key]) return pendingChunks[key];
    const mondays = mondaysBetween(chunkStart, chunkEnd);
    for (const m of mondays) state.loadingWeeks[m] = true;
    notify();
    pendingChunks[key] = (async () => {
      try {
        const payload = await api.calendarData(chunkStart, addDays(chunkEnd, 6));
        for (const monday of mondays) {
          const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
          const week = buildWeek(payload, monday, days, state.syncSessions);
          // /calendar/data answers 200 with {"error": ...} and empty lists when
          // it fails. The week is still built and cached — throwing here would
          // refetch it on every render and would fail callers that only
          // navigated — but it carries the reason, so a shell can say the
          // emptiness is a failed read and not a legitimately empty week. Dead
          // fetches still reject.
          if (payload?.error) {
            week.error = payload.error;
            state.error = payload.error;
          }
          state.weeks[monday] = week;
        }
      } finally {
        for (const m of mondays) delete state.loadingWeeks[m];
        delete pendingChunks[key];
        notify();
      }
    })();
    return pendingChunks[key];
  }

  /** Load every week in the range that isn't cached yet (all of them when
      `force`), in chunks of at most CHUNK_WEEKS. */
  async function ensureWeeks(fromMonday, toMonday, { force = false } = {}) {
    const mondays = mondaysBetween(fromMonday, toMonday);
    const wanted = mondays.filter((m) => force || !state.weeks[m]);
    const jobs = [];
    for (const run of contiguousRuns(wanted)) {
      for (let i = 0; i < run.length; i += CHUNK_WEEKS) {
        const chunk = run.slice(i, i + CHUNK_WEEKS);
        jobs.push(fetchChunk(chunk[0], chunk.length, force));
      }
    }
    await Promise.all(jobs);
    return mondays.map((m) => state.weeks[m]);
  }

  async function loadWeek(weekStart, opts = {}) {
    return (await ensureWeeks(weekStart, weekStart, opts))[0];
  }

  function windowMondays() {
    return state.windowFrom ? mondaysBetween(state.windowFrom, state.windowTo) : [];
  }

  /** Open a window centred on `anchorMonday` and load it. */
  async function initWindow(anchorMonday, opts = {}) {
    state.windowFrom = addDays(anchorMonday, -WINDOW_BEFORE * 7);
    state.windowTo = addDays(anchorMonday, WINDOW_AFTER * 7);
    notify();
    await ensureWeeks(state.windowFrom, state.windowTo, opts);
  }

  /** Widen the window by `weeks` in one direction; resolves to the new
      Mondays only. They render as skeletons from the notify() below until
      their chunk lands. */
  async function extendWindow(direction, weeks = CHUNK_WEEKS) {
    if (!state.windowFrom) return [];
    let added;
    if (direction === 'up') {
      const last = addDays(state.windowFrom, -7);
      state.windowFrom = addDays(state.windowFrom, -weeks * 7);
      added = mondaysBetween(state.windowFrom, last);
    } else {
      const first = addDays(state.windowTo, 7);
      state.windowTo = addDays(state.windowTo, weeks * 7);
      added = mondaysBetween(first, state.windowTo);
    }
    notify();
    await ensureWeeks(added[0], added[added.length - 1]);
    return added;
  }

  /** Re-read every rendered week — what a plan write invalidates. */
  async function reloadWindow() {
    if (!state.windowFrom) return [];
    return ensureWeeks(state.windowFrom, state.windowTo, { force: true });
  }

  /** Move the cursor, loading the week — and resetting the window around it
      when it lies outside the rendered span (a jump, not a scroll). */
  async function focusWeek(monday) {
    state.weekStart = monday;
    if (!state.windowFrom || monday < state.windowFrom || monday > state.windowTo) {
      await initWindow(monday);
      return;
    }
    notify();
    await ensureWeeks(monday, monday);
  }

  /** The cursor alone — this is what scrolling calls, so it never fetches. */
  function setCursorWeek(monday) {
    state.weekStart = monday;
    const end = addDays(monday, 6);
    if (state.selectedDay < monday || state.selectedDay > end) {
      state.selectedDay = state.today >= monday && state.today <= end ? state.today : monday;
    }
    notify();
  }

  const store = {
    state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    notify,

    /** `fn` runs each time the day or a long absence puts the calendar back on
        today, so the shell can bring the view there too. */
    onLand(fn) { landListeners.add(fn); },

    /** Initial load: the visible window + globals, in parallel.

        Re-run by every refresh path, so both failure flags are cleared first
        — a stale one would keep warning about a load that has since worked. */
    async init() {
      state.error = null;
      state.plannerActiveFailed = false;
      const jobs = [
        (state.windowFrom ? reloadWindow() : initWindow(state.weekStart))
          .catch((err) => { state.error = err.message; }),
        // A failed plan read leaves plannerActive null, which reads exactly
        // like "no plan yet" — the flag is what keeps the shells from
        // forcing the wizard on (and silently archiving) a real plan.
        api.plannerActive().then((p) => {
          state.plannerActive = p;
          state.planState = p?.active_plan ? 'active' : 'none';
        }).catch(() => { state.plannerActiveFailed = true; }),
        api.plannerMacro().then((m) => { state.macro = m; }).catch(() => {}),
        // Best-effort: without it the wizard simply never asks the
        // self-assessment questions and never hints at an unplannable
        // race date.
        api.plannerBootstrap().then((b) => {
          state.athleteData = b?.athlete_data || null;
          state.planLimits = b?.plan_limits || null;
          state.goalMaturity = goalMaturityFrom(b);
        }).catch(() => {}),
        // Best-effort: without it the wizard's "Your races" chip row simply
        // never renders (same fallback as the bootstrap read above).
        api.getRaces().then((r) => { state.myRaces = r?.races || []; }).catch(() => {}),
      ];
      await Promise.all(jobs);
      notify();
      // Not awaited, and asked last: the worker answers one call at a time,
      // and the cards' take marks can land after the calendar.
      api.activityTakes().then((t) => {
        state.takes = t?.takes || [];
        notify();
      }).catch(() => {});
    },

    loadWeek,
    ensureWeeks,
    windowMondays,
    initWindow,
    extendWindow,
    reloadWindow,
    focusWeek,
    setCursorWeek,

    week() { return state.weeks[state.weekStart] || null; },

    async gotoWeek(delta) {
      const monday = addDays(state.weekStart, delta * 7);
      state.selectedDay = monday;
      await focusWeek(monday);
    },

    async gotoDay(dayIso) {
      state.selectedDay = dayIso;
      await focusWeek(mondayOf(dayIso));
    },

    /** Today, as it is now: a day that moved on is caught here at the latest. */
    async gotoToday() {
      await comeBack();
      await store.gotoDay(state.today);
    },

    selectDay(dayIso) {
      state.selectedDay = dayIso;
      notify();
    },

    async refresh() {
      await Promise.all([
        loadWeek(state.weekStart, { force: true }),
        api.plannerActive().then((p) => {
          state.plannerActive = p;
          state.planState = p?.active_plan ? 'active' : state.planState;
        }),
        // A new intervals.icu upload can turn a questionnaire sport into a
        // measured one; refresh the wizard's athlete-data snapshot with it.
        api.plannerBootstrap().then((b) => {
          state.athleteData = b?.athlete_data || null;
          state.planLimits = b?.plan_limits || null;
          state.goalMaturity = goalMaturityFrom(b);
        }).catch(() => {}),
        api.getRaces().then((r) => { state.myRaces = r?.races || []; }).catch(() => {}),
      ]);
      notify();
    },

    setPlanState(next, extra = {}) {
      state.planState = next;
      Object.assign(state, extra);
      notify();
    },
  };
  return store;
}
