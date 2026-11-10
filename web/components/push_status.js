/* Endury — intervals.icu calendar-write feedback: the toast line for a
   plan that reached the calendar, the non-blocking notice for one that did
   not (with a "Push again" retry), and the read-back diff a replan shows
   before it overwrites hand-edited workouts.
   The backend's status blocks are rendered as they arrive: commit/replan/
   push carry `push` ({status, pushed, errors, message}), plan delete carries
   `remote_delete` ({status, deleted, errors, message}), and the replan 400
   carries {error_message, diff:[{date, name, changed_fields}]}.
   The persistent plan-header sync affordance is two pieces: `renderSyncStatus`
   is the always-visible state line (quiet dot + text, healthy or not), and
   `renderSyncChip` is the action row — silent on the happy path, and only
   rendering (badge + "Sync now") when there is something to do. */

import { esc } from './util.js';
import { fmtDayLabel } from '../data/format.js';

const FIELD_LABELS = {
  start_date_local: 'moved to another day',
  name: 'renamed',
  description: 'workout details edited',
};

/** Plural-safe event count ("1 event", "4 events"). */
function events(n) {
  return `${n} event${n === 1 ? '' : 's'}`;
}

/** Toast line for a plan write: `base`, plus what reached intervals.icu.
    A skipped/failed push adds nothing here — the notice card carries it. */
export function pushToastText(base, block) {
  if (!block || block.status !== 'ok') return base;
  if (block.deleted !== undefined) {
    return `${base} — ${events(block.deleted)} removed from intervals.icu`;
  }
  return `${base} — ${events(block.pushed ?? 0)} pushed to intervals.icu`;
}

/** Whether a push/delete block needs the notice: it failed, was skipped,
    or landed but left replaced workouts on the calendar (`leftover`). */
export function needsPushNotice(block) {
  return Boolean(block && (block.status !== 'ok' || block.leftover));
}

/** Non-blocking notice for a push/delete that failed or was skipped, or
    whose cleanup of replaced workouts failed. Returns '' for a clean (or
    absent) block, so callers can render it unconditionally. `retry` adds
    the "Push again" button — a plan whose remote events could not be
    deleted has nothing to re-push. */
export function renderPushNotice(block, { retry = true } = {}) {
  if (!needsPushNotice(block)) return '';
  const skipped = block.status === 'skipped';
  const title = skipped ? 'Not synced to intervals.icu'
    : block.status === 'ok' ? 'Replaced workouts still on intervals.icu'
      : 'intervals.icu sync failed';
  // The skipped message is the backend's log line ("skipped: no
  // INTERVALS_API_KEY") — say what it means instead.
  const detail = skipped
    ? 'No intervals.icu API key is configured, so your calendar was not updated.'
    : (block.message || 'Your calendar on intervals.icu was not updated.');
  const errors = (block.errors || []).slice(0, 3)
    .map((e) => `<div class="cb-muted" style="font-size:var(--cb-fs-micro);margin-top:var(--cb-sp-1)">${esc(e)}</div>`)
    .join('');
  const again = retry && !skipped
    ? `<button type="button" class="cb-btn-secondary" style="font-size:var(--cb-fs-meta);padding:var(--cb-sp-1-5) var(--cb-sp-3)" data-action="push-again">Push again</button>`
    : '';
  return `<div class="cb-insight" style="display:flex;align-items:flex-start;gap:var(--cb-sp-3);margin:var(--cb-sp-2) 0" role="status">
      <div style="flex:1;min-width:0">
        <div class="cb-section-label cb-warn-text">${esc(title)}</div>
        <div style="font-size:var(--cb-fs-meta);color:var(--cb-text-muted);margin-top:var(--cb-sp-1);line-height:1.4">${esc(detail)}</div>
        ${errors}
      </div>
      ${again}
      <button type="button" class="cb-btn-secondary" style="font-size:var(--cb-fs-meta);padding:var(--cb-sp-1-5) var(--cb-sp-3)" data-action="dismiss-push">Dismiss</button>
    </div>`;
}

/* Wording per out-of-sync state, singular/plural. Order matters: the first
   non-zero state names the chip, so a fault is announced before an edit. */
const SYNC_WORDING = [
  ['missing', (n) => `${n} workout${n === 1 ? '' : 's'} not on intervals.icu`],
  ['leftover', (n) => `${n} replaced workout${n === 1 ? '' : 's'} still on intervals.icu`],
  ['delivery_failed', (n) => `${n} workout${n === 1 ? " didn't" : "s didn't"} reach your device`],
  ['removed', (n) => `${n} workout${n === 1 ? '' : 's'} deleted on intervals.icu`],
  ['edited', (n) => `${n} workout${n === 1 ? '' : 's'} edited on intervals.icu`],
];

/** The chip phrase for one out-of-sync state, or '' for an unknown state. */
function syncPhrase(state, n) {
  const entry = SYNC_WORDING.find(([key]) => key === state);
  return entry ? entry[1](n) : '';
}

/** Every out-of-sync phrase present in a sync-status summary, in
    `SYNC_WORDING` order (a fault named before an edit) — the shared list
    both the status line's title and the chip's title/label build from. */
function syncPhrases(summary) {
  return SYNC_WORDING
    .filter(([state]) => summary[state])
    .map(([state, phrase]) => phrase(summary[state]));
}

/** Confirm-dialog body for "Sync now" when the athlete's own intervals.icu
    changes would be destroyed by it. Returns '' when nothing of theirs is
    at stake, so callers can treat '' as "no confirmation needed".

    Built from the summary counts alone: the per-session sync entries carry
    only {state, services, changed_fields}, so there is no name or date to
    list the way `renderReplanDiff` does. */
export function renderSyncOverwriteBody(summary) {
  const counts = summary || {};
  const edited = counts.edited || 0;
  const removed = counts.removed || 0;
  if (!edited && !removed) return '';
  const lines = [];
  if (edited) {
    lines.push(`${syncPhrase('edited', edited)} — syncing replaces them with the `
      + `plan's version, so your intervals.icu changes are lost.`);
  }
  if (removed) {
    lines.push(`${syncPhrase('removed', removed)} — syncing recreates them.`);
  }
  return lines
    .map((line) => `<div style="margin-top:var(--cb-sp-2)">${esc(line)}</div>`)
    .join('');
}

/** Persistent plan-header state line: is this plan mirrored on
    intervals.icu? Always visible — renders from
    `GET /api/planner/sync-status` on every plan view, the state an athlete
    otherwise has no way to see. Returns '' when the check could not run (no
    key, no plan, offline), so an unconfigured instance shows nothing rather
    than a false alarm. `compact` shortens the text for narrow layouts (the
    mobile shell), where the full "intervals.icu · …" phrase does not fit. */
export function renderSyncStatus(status, { compact = false } = {}) {
  if (!status || !status.available) return '';
  const summary = status.summary || {};
  const out = status.out_of_sync || 0;
  if (!out) {
    const text = compact ? 'In sync' : 'intervals.icu · in sync';
    return `<span class="cb-sync-status" title="Every upcoming workout matches your intervals.icu calendar"><span class="cb-sync-status__dot"></span><span class="cb-sync-status__text">${esc(text)}</span></span>`;
  }
  const title = syncPhrases(summary).join(' · ');
  const text = compact ? 'Out of sync' : `intervals.icu · ${out} out of sync`;
  return `<span class="cb-sync-status cb-sync-status--warn" title="${esc(title)}"><span class="cb-sync-status__dot"></span><span class="cb-sync-status__text">${esc(text)}</span></span>`;
}

/** Persistent plan-header action row: only what needs doing about
    intervals.icu sync. Unlike `renderSyncStatus`, which always shows the
    current state, this is silent on the happy path — it renders the "out of
    sync" badge and "Sync now" button only when there is something to fix. */
export function renderSyncChip(status) {
  if (!status || !status.available) return '';
  const summary = status.summary || {};
  const out = status.out_of_sync || 0;
  if (!out) return '';
  const parts = syncPhrases(summary);
  return `<span class="cb-badge cb-badge--agenda cb-badge--warn" title="${esc(parts.join(' · '))}">${esc(parts[0])}</span>`
    + `<button type="button" class="cb-btn-secondary" style="font-size:var(--cb-fs-meta);padding:var(--cb-sp-1) var(--cb-sp-2-5);margin-left:var(--cb-sp-2)" data-action="sync-plan">Sync now</button>`;
}

/** Confirm-dialog body listing the hand-edited workouts a replan would
    overwrite (the backend's `confirm_required` read-back diff). */
export function renderReplanDiff(diff) {
  const rows = (diff || []).map((entry) => {
    const day = String(entry.date || '').slice(0, 10);
    const changes = (entry.changed_fields || [])
      .map((field) => FIELD_LABELS[field] || field).join(' · ');
    return `<div style="display:flex;gap:var(--cb-sp-2);padding:var(--cb-sp-2) 0;border-top:1px solid var(--cb-border)">
        <span style="flex:none;color:var(--cb-text-strong)">${esc(day ? fmtDayLabel(day) : '—')}</span>
        <span style="flex:1;min-width:0">
          <span style="color:var(--cb-text-strong)">${esc(entry.name || 'Workout')}</span>
          ${changes ? `<span class="cb-warn-text" style="display:block;font-size:var(--cb-fs-meta)">${esc(changes)}</span>` : ''}
        </span>
      </div>`;
  }).join('');
  return `<div>These manually-edited future sessions will be overwritten:</div>${rows}`;
}
