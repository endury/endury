/* Endury — per-week summary column for the web shell's continuous calendar:
   plan week, phase, planned-vs-done load and hours, sport mix, status. Pure
   function over a model the shell builds from one week model — no DOM, no
   fetching. Styles in shells/web/web.css (.cbw-weeksum*). */

import { esc, sportColor, TSS_HINT } from './util.js';

const SPORT_ORDER = ['swim', 'bike', 'run', 'strength', 'other'];

function statusClass(status) {
  if (status === 'Done') return 'cb-good-text';
  if (status === 'This week') return 'cb-accent-text';
  return 'cb-muted';
}

function renderMix(sportSplit) {
  const segs = SPORT_ORDER
    .filter((sp) => sportSplit && sportSplit[sp])
    .map((sp) => `<span class="cbw-weeksum__seg" style="flex:${sportSplit[sp]};background:${sportColor(sp)}"></span>`)
    .join('');
  return segs ? `<div class="cbw-weeksum__mix" aria-hidden="true">${segs}</div>` : '';
}

/** model: {weekLabel, phase: {key, label} | null, status, plannedTss, doneTss,
    plannedHours, doneHours, compliance (pct | null), sportSplit, race, current}.
    `status` is anchored to today, never to the cursor week. */
export function renderWeekSummary(model) {
  const {
    weekLabel, phase, status, plannedTss, doneTss, plannedHours, doneHours,
    compliance, sportSplit, race, current,
  } = model;
  const now = current ? '<span class="cbw-weeksum__now" aria-label="current week">→</span>' : '';
  const phasePill = phase
    ? `<span class="cb-phase-pill cb-phase-pill--${esc(phase.key)}">${esc(phase.label)}</span>`
    : '';
  const racePill = race
    ? `<span class="cb-race-pill cb-race-pill--${esc(String(race).toLowerCase())}">${esc(race)}</span>`
    : '';
  const load = status === 'Planned' ? `${plannedTss}` : `${doneTss} of ${plannedTss}`;
  const hours = status === 'Planned' ? `${plannedHours}` : `${doneHours} of ${plannedHours}`;
  const pct = compliance == null ? '' : ` · ${esc(compliance)}%`;
  return `<div class="cbw-weeksum__head"><span class="cbw-weeksum__wk">${now}${esc(weekLabel)}</span>${racePill}</div>
    ${phasePill}
    <div class="cbw-weeksum__row" title="${esc(TSS_HINT)}"><span class="cbw-weeksum__k">Load</span><span class="cbw-weeksum__v">${esc(load)}</span></div>
    <div class="cbw-weeksum__row"><span class="cbw-weeksum__k">Hours</span><span class="cbw-weeksum__v">${esc(hours)} h</span></div>
    ${renderMix(sportSplit)}
    <div class="cbw-weeksum__status ${statusClass(status)}">${esc(status)}${pct}</div>`;
}
