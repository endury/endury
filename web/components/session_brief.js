/* Endury — the coach's brief for a coming session or race: the rows and pages
   the page builds without an AI model (renderBriefSheet, filled into a slot by
   the shells), the Coach's brief page that mirrors the Coach's take
   (renderCoachBrief), and the race sheet (renderRaceDetail). A subject is
   named by its handle: "session:412" or "race:2026-10-24". */

import { esc, GARMIN_NOTE } from './util.js';
import { previewText, sheetHead, sheetPage, sheetRow } from './sheet_pages.js';
import { renderThinking } from './coach_msg.js';
import { renderDetailActions, takeBlock } from './activity_debrief.js';
import { fmtDayLabel, fmtDur, parseDay } from '../data/format.js';

const BRIEF_LABEL = { quick: 'Quick brief', full: 'Full brief' };

const range = (pair, unit) => (pair ? `${pair[0]}–${pair[1]}${unit}` : null);
const signed = (n) => (n > 0 ? `+${n}` : String(n));
const minutes = (m) => fmtDur(m * 60);

/** One stat tile (label, value, optional sub), the sheet's own vocabulary. */
const tile = (label, value, sub = null) => `
    <div>
      <div class="cb-stat-label">${esc(label)}</div>
      <div class="cb-stat-value">${esc(value)}</div>
      ${sub ? `<div class="cb-stat-sub">${esc(sub)}</div>` : ''}
    </div>`;

const tiles = (items) => `<div class="cb-detail-stats">${items.join('')}</div>`;
const lines = (rows) => (rows.length
  ? `<div class="cb-detail-cues">${rows.map((r) => `<div class="cb-detail-cue">${esc(r)}</div>`).join('')}</div>`
  : '');
const note = (text) => (text ? `<div class="cb-detail-note">${esc(text)}</div>` : '');
const section = (label, body) => `<div><div class="cb-reschedule-label">${esc(label)}</div>${body}</div>`;

/** "11 Oct · 24.1 km · 1,110 m · 2:39" — one past session of a list. */
function effortRow(r) {
  return [
    fmtDayLabel(r.date),
    r.distance_km != null ? `${r.distance_km} km` : null,
    r.climb_m != null ? `${r.climb_m.toLocaleString('en-US')} m` : null,
    r.duration_min != null ? minutes(r.duration_min) : null,
  ].filter(Boolean).join(' · ');
}

/** A form read's first words: "Steady: carrying…" → "steady". */
const readWords = (read) => (read ? read.split(/[:,]/)[0].toLowerCase() : '');

/** Where you stand in one line: a wellness flag first, then the form that
    counts for the day, then the night's sleep. */
function standSummary(st) {
  const sleep = st.wellness?.sleep_h;
  let form = null;
  if (st.projected) form = `Form on the day ${signed(st.projected.tsb)}`;
  else if (st.tsb != null) form = [`Form ${signed(st.tsb)}`, readWords(st.tsb_read)].filter(Boolean).join(', ');
  return [(st.flags || [])[0], form, sleep != null ? `Slept ${sleep} h` : null].filter(Boolean).slice(0, 2).join(' · ');
}

/** The athlete's form going in, today's wellness and the latest sessions. */
function standPart(st) {
  const w = st.wellness || {};
  const items = [
    st.tsb != null ? tile('Form today', signed(st.tsb), st.tsb_read) : null,
    st.projected ? tile('Form on the day', signed(st.projected.tsb)) : null,
    w.hrv != null ? tile('HRV', String(w.hrv), w.hrv_baseline != null ? `usual ${w.hrv_baseline}` : null) : null,
    w.sleep_h != null ? tile('Sleep', `${w.sleep_h} h`) : null,
  ].filter(Boolean);
  const recent = (st.recent || []).map((r) => [
    fmtDayLabel(r.date), r.name, minutes(r.duration_min),
    r.avg_hr != null ? `${r.avg_hr} bpm` : null,
  ].filter(Boolean).join(' · '));
  return {
    id: 'stand',
    title: 'Where you stand',
    summary: standSummary(st),
    body: (items.length ? tiles(items) : '') + note((st.flags || []).join(' '))
      + (recent.length ? section('Latest sessions', lines(recent)) : '') + note(GARMIN_NOTE),
  };
}

/** An anchor's band as the athlete reads it: "≤150 bpm", "152–158 W". */
function anchorValue(a) {
  if (a.open) return 'Open';
  const unit = a.w_max != null || a.w_min != null ? 'W' : 'bpm';
  const lo = a.hr_min ?? a.w_min;
  const hi = a.hr_max ?? a.w_max;
  return lo != null && hi != null ? `${lo}–${hi} ${unit}` : `≤${hi} ${unit}`;
}

function provenRow(p) {
  return `${p.avg_hr} bpm for ${minutes(p.duration_min)} · ${p.name}, ${fmtDayLabel(p.date)}`;
}

/** "Best normalized power: 271 W for an hour, 236 W for three; FTP 310 W." */
function powerWords(p) {
  if (!p) return '';
  const held = [[p.np_60, 'an hour'], [p.np_120, 'two hours'], [p.np_180, 'three hours']]
    .filter(([w]) => w != null).map(([w, span]) => `${w} W for ${span}`);
  const parts = [
    held.length ? `Best normalized power: ${held.join(', ')}` : null,
    p.ftp != null ? `FTP ${p.ftp} W` : null,
  ].filter(Boolean);
  return parts.length ? `${parts.join('; ')}.` : '';
}

/** What to hold: anchor tiles, then the efforts that ground them. The card's
    own steps have their page, so a sheet with nothing else offers no row. */
function holdPart(h) {
  if (!h) return null;
  const anchors = h.anchors || [];
  const proven = (h.proven || []).length ? section('Proven ground', lines(h.proven.map(provenRow))) : '';
  const power = powerWords(h.power);
  const climb = h.climb_rate_m_per_h
    ? `You climb about ${h.climb_rate_m_per_h} m an hour at long-run effort.` : '';
  const body = (anchors.length ? tiles(anchors.map((a) => tile(a.label, anchorValue(a)))) : '')
    + proven + note(power) + note(climb);
  if (!body) return null;
  const summary = anchors.slice(0, 2).map((a) => `${a.label}: ${anchorValue(a)}`).join(' · ')
    || (h.proven || []).map(provenRow)[0] || power || climb;
  return { id: 'hold', title: 'What to hold', summary, body };
}

/** The fuel in one line; a session water alone covers has its note and no page. */
function fuelPart(f) {
  const items = [
    f.carbs_g_per_h ? tile('Carbs', range(f.carbs_g_per_h, ' g'), 'an hour') : null,
    f.carbs_total_g ? tile('In total', range(f.carbs_total_g, ' g')) : null,
    f.fluid_ml_per_h ? tile('Fluid', range(f.fluid_ml_per_h, ' ml'), 'an hour') : null,
  ].filter(Boolean);
  const load = f.carb_load
    ? `Day before: ${range(f.carb_load.g_per_kg, ' g')} of carbs per kg`
      + `${f.carb_load.g ? `, ${range(f.carb_load.g, ' g')} in all` : ''}.` : '';
  const said = [f.before, load, ...(f.notes || [])].filter(Boolean);
  if (!items.length && said.length <= 1) {
    return { id: 'fuel', title: 'Fuel and water', summary: said[0] || '', body: '' };
  }
  const summary = f.carbs_g_per_h
    ? [`${range(f.carbs_g_per_h, ' g')} of carbs`, f.fluid_ml_per_h ? `${range(f.fluid_ml_per_h, ' ml')} of fluid` : null]
      .filter(Boolean).join(', ').concat(' an hour')
    : said[0];
  return {
    id: 'fuel', title: 'Fuel and water', summary, body: (items.length ? tiles(items) : '') + said.map(note).join(''),
  };
}

function kitPart(c) {
  const temp = c.recent_temp_c ? `Recent sessions ran at ${range(c.recent_temp_c, ' °C')}.` : '';
  const kit = c.kit || [];
  // Nothing known: the sheet's thin line says why, so no empty row.
  if (!temp && !kit.length) return null;
  return {
    id: 'kit', title: 'Kit and conditions', summary: kit[0] || temp, body: note(temp) + lines(kit),
  };
}

function estimatePart(e) {
  const finish = `${minutes(e.range_min[0])}–${minutes(e.range_min[1])}`;
  return {
    id: 'finish',
    title: 'Finish time',
    summary: finish,
    body: tiles([tile('Range', finish)]) + note(e.method) + lines((e.basis || []).map(effortRow)),
  };
}

/** The rubric: what the debrief will grade the session against. */
function successPart(rubric) {
  const rows = rubric.map((r) => `<div class="cb-detail-cue">${esc(r.label)}</div>`).join('');
  return {
    id: 'success',
    title: 'What success looks like',
    summary: rubric.map((r) => r.label).join(' · '),
    body: `<div class="cb-brief__success">${rows}</div>`,
  };
}

/** The deterministic brief as {rows, pages, foot}: a row per part the sheet
    carries, none for a part it leaves null, each opening a page when it has
    more to say than its line. How to do it leads and where the athlete
    stands follows. `foot` is the credit its figures need. `back` names the
    way back from a page. */
export function renderBriefSheet(sheet, { back = 'Back' } = {}) {
  const routes = sheet.routes || [];
  const thin = sheet.thin || [];
  const parts = [
    holdPart(sheet.hold),
    (sheet.rubric || []).length && successPart(sheet.rubric),
    sheet.fuel && fuelPart(sheet.fuel),
    sheet.conditions && kitPart(sheet.conditions),
    routes.length && {
      id: 'routes', title: 'Routes that fit', summary: effortRow(routes[0]), body: lines(routes.map(effortRow)),
    },
    sheet.estimate && estimatePart(sheet.estimate),
    sheet.logistics && {
      id: 'logistics', title: 'Logistics', summary: sheet.logistics.notes, body: note(sheet.logistics.notes),
    },
    sheet.state && standPart(sheet.state),
    thin.length && {
      id: 'thin', title: 'Not enough data yet', summary: thin[0], body: thin.map(note).join(''),
    },
  ].filter(Boolean);
  return {
    rows: parts.map((p) => sheetRow({ title: p.title, summary: p.summary, page: p.body ? p.id : null })).join(''),
    pages: parts.filter((p) => p.body)
      .map((p) => sheetPage({ id: p.id, title: p.title, back, body: p.body })).join(''),
    foot: sheet.state ? GARMIN_NOTE : '',
  };
}

/** The place the shell fills with the brief's rows (shared.js fillBrief). */
export function renderBriefSlot(handle) {
  return `<div class="cb-brief cb-brief--loading" data-brief-slot data-handle="${esc(handle)}">${renderThinking('Preparing your brief…')}</div>`;
}

/** The Coach's brief row's answer: what the coach wrote, or what it can write. */
export function briefSummary(takes = [], pending = null) {
  if (pending) return `Writing your ${BRIEF_LABEL[pending].toLowerCase()}…`;
  const brief = takes.find((t) => t.depth === 'quick') || takes[0];
  return brief ? previewText(brief.text) : 'Ask for a quick or a full brief';
}

/** The "Coach's brief" page: the twin of the Coach's take, for a coming
    session or race. `handle` — the subject; `takes` — its saved briefs;
    `pending` — the depth in flight; `error` / `opened` / `proposed` as in
    renderActivityTakes. */
export function renderCoachBrief({
  handle, name, date, takes = [], pending = null, error = '', opened = null, proposed = false,
}) {
  const byDepth = Object.fromEntries(takes.map((t) => [t.depth, t]));
  const blocks = ['quick', 'full'].filter((d) => byDepth[d])
    .map((d) => takeBlock(byDepth[d], opened === 'full' || !byDepth.quick, BRIEF_LABEL)).join('');
  const buttons = ['quick', 'full'].map((d) => `<button type="button" class="cb-btn-secondary"
      data-action="activity-brief" data-depth="${d}">${byDepth[d] ? `Redo ${BRIEF_LABEL[d].toLowerCase()}` : BRIEF_LABEL[d]}</button>`).join('');
  return `
    <div class="cb-takes" data-brief-takes-slot data-handle="${esc(handle)}"
      data-name="${esc(name)}" data-date="${esc(date)}">
      ${blocks}
      ${error ? `<div class="cb-detail-note">${esc(error)}</div>` : ''}
      ${proposed ? '<div class="cb-detail-note">Your coach suggested a plan change: apply it in the chat.</div>' : ''}
      ${pending ? renderThinking(`Writing your ${BRIEF_LABEL[pending].toLowerCase()}…`) : `<div class="cb-takes__actions">${buttons}</div>`}
    </div>`;
}

const daysAway = (date, todayIso) => Math.round((parseDay(date) - parseDay(todayIso)) / 86400000);

/** How far off the race is, as its hero says it. */
function raceWhen(days) {
  if (days === 0) return 'Race day';
  const span = `${Math.abs(days)} day${Math.abs(days) === 1 ? '' : 's'}`;
  return days > 0 ? `In ${span}` : `${span} ago`;
}

/** The race sheet: the overview with the brief's rows and the Coach's brief,
    and their pages. `race` — {name, race_date, priority, distance_km, climb_m,
    goalLabel}; `todayIso` dates the countdown. */
export function renderRaceDetail(race, { briefTakes = [], briefPending = null, todayIso }) {
  const prio = (race.priority || 'A').toLowerCase();
  const name = race.name || 'Race';
  const handle = `race:${race.race_date}`;
  const size = [
    race.distance_km ? `${race.distance_km} km` : null,
    race.climb_m ? `${race.climb_m.toLocaleString('en-US')} m` : null,
  ].filter(Boolean).join(' · ');
  const kind = [`${race.priority || 'A'} race`, race.goalLabel].filter(Boolean).join(' · ');
  return `
    <div class="cb-sheet cb-detail">
      <section class="cb-sheet__page" data-sheet-page="overview">
        ${sheetHead({ color: `var(--cb-race-${esc(prio)})`, title: name, meta: esc(fmtDayLabel(race.race_date)) })}
        <div class="cb-sheet__body">
          <div class="cb-sheet-hero">
            <div class="cb-sheet-hero__big">${esc(raceWhen(daysAway(race.race_date, todayIso)))}</div>
            ${size ? `<div class="cb-sheet-hero__line">${esc(size)}</div>` : ''}
            <div class="cb-sheet-hero__sub">${esc(kind)}</div>
          </div>
          <div class="cb-sheet-list">
            ${renderBriefSlot(handle)}
            ${sheetRow({
    title: "Coach's brief", summary: briefSummary(briefTakes, briefPending), page: 'coach', key: 'brief',
  })}
          </div>
          <div class="cb-sheet__foot-note" data-sheet-note></div>
        </div>
        ${renderDetailActions({ name, date: race.race_date })}
      </section>
      ${sheetPage({
    id: 'coach',
    title: "Coach's brief",
    back: name,
    body: renderCoachBrief({
      handle, name, date: race.race_date, takes: briefTakes, pending: briefPending,
    }),
  })}
      <div data-sheet-pages></div>
    </div>`;
}
