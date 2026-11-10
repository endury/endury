/* Endury — session card: agenda (mobile feed) + grid (web day column). */

import { esc, sportRgb, TSS_HINT } from './util.js';
import { icons } from './icons.js';
import { renderOutline } from './workout_outline.js';

/* Sync states, for upcoming sessions only. The happy path stays quiet:
   an in-sync session keeps reading PLANNED, so a badge that isn't grey
   always means something needs attention. A device rejection outranks a
   manual edit — the edit is the athlete's own choice, the rejection means
   the workout won't be on their device. */
const SYNC_BADGES = {
  missing: { text: 'NOT SYNCED', cls: 'cb-badge--warn' },
  removed: { text: 'REMOVED', cls: 'cb-badge--muted' },
  edited: { text: 'EDITED', cls: 'cb-badge--muted' },
};

function syncBadge(sync) {
  if (!sync || sync.state === 'in_sync') return null;
  if (sync.state === 'delivery_failed') {
    const where = (sync.services || []).join(', ');
    return { text: where ? `${where} FAILED`.toUpperCase() : 'NOT ON DEVICE',
             cls: 'cb-badge--warn' };
  }
  return SYNC_BADGES[sync.state] || null;
}

/** Session → badge {text, cls}. cls is a cb-badge--* color modifier; the
    caller adds the cb-badge + size-variant classes.

    Completed and upcoming sessions never compete for the slot: DONE /
    MISSED / the length done against planned only apply once a session is in
    the past, and sync state only matters before it. */
export function badgeMeta(session) {
  const ex = session.done && session.actual && session.actual.execution;
  if (ex && ex.plannedSec && ex.durationSec != null) {
    if (ex.durationPct >= 90 && ex.durationPct <= 115) return { text: 'Done', cls: 'cb-badge--good' };
    const min = (sec) => Math.round(sec / 60);
    return { text: `Done · ${min(ex.durationSec)} min of ${min(ex.plannedSec)}`, cls: 'cb-badge--muted' };
  }
  if (session.done) return { text: 'DONE', cls: 'cb-badge--done' };
  // A skipped session is the one state a coach app must not let blend in —
  // its own fill, distinct from PLANNED's neutral grey and from NOT SYNCED's
  // warn terracotta (a sync problem is not the same as a missed workout).
  if (session.skipped) return { text: 'SKIPPED', cls: 'cb-badge--skip' };
  if (session.missed) return { text: 'MISSED', cls: 'cb-badge--miss' };
  return syncBadge(session.sync) || { text: 'PLANNED', cls: 'cb-badge--muted' };
}

function metricsHtml(session) {
  const stats = (session.actual && session.actual.stats) || session.stats || [];
  const credit = session.actual?.garmin ? '<span>Garmin</span>' : '';
  return stats.filter((m) => m.v && m.v !== '—')
    .map((m) => `<span>${esc(m.v)}</span>`).join('') + credit;
}

/** The mark beside a card's title when the coach's take on its activity is saved. */
const TAKE_MARK = `<span class="cb-card__take" title="Coach's take saved" aria-label="Coach's take saved">${icons.chat(11)}</span>`;

function agendaBody(session, badge, mark) {
  const note = session.note ? `<div class="cb-card__note">${esc(session.note)}</div>` : '';
  const outline = session.outline && session.outline.length
    ? renderOutline(session.outline, { height: 13, sport: session.sport })
    : '';
  return `<div class="cb-card__row">`
    + `<span class="cb-card__title">${esc(session.title)}${mark}</span>`
    + `<span class="cb-badge cb-badge--agenda ${badge.cls}">${esc(badge.text)}</span>`
    + `</div>`
    + `<div class="cb-card__metrics">${metricsHtml(session)}</div>`
    + note
    + outline;
}

function gridBody(session, badge, mark) {
  const tss = session.done && session.actualTss != null
    ? Math.round(session.actualTss) : Math.round(session.tss || 0);
  const stats = (session.actual && session.actual.stats) || session.stats || [];
  const first = (stats[0] && stats[0].v) || '';
  // Distance is the headline number for a swim ("2500 m" says more than
  // "50min") — ride it along in the compact grid meta when planned.
  const dist = stats.find((m) => m.l === 'Distance' && m.v && m.v !== '—');
  const parts = [first !== '—' ? esc(first) : '', dist ? esc(dist.v) : '',
    `<span title="${esc(TSS_HINT)}">${tss} load</span>`];
  const meta = parts.filter(Boolean).join(' · ');
  const outline = session.outline && session.outline.length
    ? renderOutline(session.outline, { height: 10, sport: session.sport })
    : '';
  return `<span class="cb-card__title">${esc(session.title)}${mark}</span>`
    + `<span class="cb-badge cb-badge--grid ${badge.cls}">${esc(badge.text)}</span>`
    + `<div class="cb-card__meta">${meta}</div>`
    + outline;
}

/** Render a session card. variant: 'agenda' (mobile feed) or 'grid' (web day column).

    Sport identity rides the left-edge stripe only — a full-body tint would
    make adjacent cards read as competing skins instead of one neutral
    surface with a colour-coded edge. The card body is the shared
    --cb-surface-3; identity belongs on edges and markers. `hasTake` marks a
    completed session whose activity has a saved coach's take. */
export function renderSessionCard(session, { variant = 'agenda', draggable = false, hasTake = false } = {}) {
  const badge = badgeMeta(session);
  const rgb = sportRgb(session.sport);
  const cls = ['cb-card', `cb-card--${variant}`, session.anchor ? 'cb-card--anchor' : '']
    .filter(Boolean).join(' ');
  const mark = hasTake ? TAKE_MARK : '';
  const body = variant === 'grid' ? gridBody(session, badge, mark) : agendaBody(session, badge, mark);
  const dragAttr = draggable ? ' draggable="true"' : '';
  return `<button type="button" class="${cls}" data-action="open-session" `
    + `data-id="${esc(session.id)}"${dragAttr} `
    + `style="background:var(--cb-surface-3);border-left-color:rgb(${rgb})">${body}</button>`;
}
