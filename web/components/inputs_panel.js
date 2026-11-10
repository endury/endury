/* Endury — Planning Inputs panel: per-sport thresholds/hours/CTL-ATL, the
   intervals.icu combined-CTL/ATL cross-check, a 7-day wellness strip, and
   any onward push errors. Pure function over the data/adapters.js
   toInputsPanel() model — no DOM, no fetching. Styles in
   components/inputs_panel.css (.cb-inputs and friends), reusing
   insights.css's .cb-insight/.cb-section-label/.cb-muted/.cb-warn-text and
   kit.css's .cb-badge primitives. */

import { esc, GARMIN_NOTE } from './util.js';
import { hintButton } from './tss_explainer.js';

/* The threshold edit, hidden until "Edit". A pace takes m:ss; a field left
   as it was is not sent (shells/shared.js showInputsPanel wires the form). */
function renderEditForm(sport) {
  const fields = sport.edit.map((f) => {
    const input = f.pace
      ? `type="text" pattern="\\d{1,2}:[0-5]\\d" placeholder="m:ss" data-pace="1"`
      : `type="number" inputmode="numeric" step="1" min="${f.min}" max="${f.max}"`;
    return `
      <label class="cb-inputs__field">
        <span class="cb-muted">${esc(f.label)} <span class="cb-inputs__unit">${esc(f.unit)}</span></span>
        <input class="cb-settings__input" name="${f.name}" ${input}
          value="${esc(f.value)}" data-initial="${esc(f.value)}" autocomplete="off">
      </label>`;
  }).join('');
  return `
    <form class="cb-inputs__edit" data-sport="${sport.key}" hidden>
      ${fields}
      <div class="cb-inputs__note">Saves to every ${esc(sport.label.toLowerCase())} sport in your
        intervals.icu settings, indoor included. Past activities keep their scores.</div>
      <div class="cb-inputs__note cb-warn-text" data-thresholds-error hidden></div>
      <div class="cb-inputs__edit-actions">
        <button type="button" class="cb-btn-secondary" data-thresholds-cancel>Cancel</button>
        <button type="submit" class="cb-btn-primary">Save</button>
      </div>
    </form>`;
}

function renderSportCard(sport) {
  const rows = sport.thresholds.map(([label, value]) => `
    <div class="cb-inputs__row"><span class="cb-muted">${esc(label)}</span><span class="cb-inputs__val">${esc(value)}</span></div>`).join('');
  const badge = sport.derived.length
    ? '<span class="cb-badge cb-badge--muted cb-badge--grid">Derived</span>' : '';
  const note = sport.derived.length
    ? `<div class="cb-inputs__note">${esc(sport.derived.join('; '))}</div>` : '';
  const hint = sport.hint
    ? `<div class="cb-inputs__note">${esc(sport.hint)}</div>` : '';
  return `
    <div class="cb-insight cb-inputs__card">
      <div class="cb-inputs__head">
        <span class="cb-section-label">${esc(sport.label)}</span>
        <span class="cb-inputs__head-end">${badge}<button type="button" class="cb-inputs__edit-btn" data-thresholds-edit>Edit</button></span>
      </div>
      ${rows}
      ${renderEditForm(sport)}
      <div class="cb-inputs__row"><span class="cb-muted">Recent weekly volume</span><span class="cb-inputs__val">${esc(sport.hours)}</span></div>
      <div class="cb-inputs__row"><span class="cb-muted">Recent weekly climb</span><span class="cb-inputs__val">${esc(sport.elevation)}</span></div>
      <div class="cb-inputs__row"><span class="cb-muted">Longest recent session</span><span class="cb-inputs__val">${esc(sport.longest)}</span></div>
      <div class="cb-inputs__row"><span class="cb-muted">Fitness / fatigue (ours, weekly per sport)${hintButton('fitness', 'What do fitness, fatigue and form mean?')}</span><span class="cb-inputs__val">${esc(sport.ctlAtl)}</span></div>
      ${note}
      ${hint}
      <div class="cb-inputs__source">${esc(sport.sourceLabel)}</div>
    </div>`;
}

function renderPushErrors(pushErrors) {
  if (!pushErrors.length) return '';
  const rows = pushErrors.map((e) => `
    <div class="cb-inputs__push-row">
      <div class="cb-inputs__push-name">${esc(e.name)} <span class="cb-muted">· ${esc(e.date)}</span></div>
      ${e.lines.map((l) => `<div class="cb-inputs__push-line">${esc(l)}</div>`).join('')}
    </div>`).join('');
  return `
    <div class="cb-insight cb-inputs__push-errors">
      <div class="cb-section-label cb-warn-text">Push errors</div>
      ${rows}
    </div>`;
}

/* Where intervals.icu forwards a pushed workout. Not Garmin-specific: it
   fans out to seven platforms, each toggled separately on intervals.icu.
   None enabled is the silent failure this row exists to expose — the plan
   syncs perfectly and still never reaches a device. */
function renderDestinations(destinations) {
  if (destinations.length) {
    return `
      <div class="cb-insight cb-inputs__destinations">
        <div class="cb-section-label">Planned workouts upload to</div>
        <div class="cb-inputs__dest-list">${destinations.map(esc).join(' · ')}</div>
      </div>`;
  }
  return `
    <div class="cb-insight cb-inputs__destinations">
      <div class="cb-section-label cb-warn-text">No upload destination</div>
      <div class="cb-inputs__dest-list cb-muted">Plans stay on intervals.icu and
        won't reach a watch or trainer. Enable "Upload planned workouts" for your
        device in intervals.icu settings.</div>
    </div>`;
}

/** model = toInputsPanel() output. Renders the setup card when the backend
    (or the fetch itself) reports unavailable, otherwise the full panel:
    one card per sport, the combined-CTL/ATL cross-check, the 7-day
    wellness strip, and push errors (only when non-empty). */
export function renderInputsPanel(model) {
  if (!model.available) {
    return `
      <div class="cb-insight cb-inputs__setup">
        <div class="cb-section-label cb-warn-text">intervals.icu not connected</div>
        <div class="cb-inputs__setup-text">${esc(model.reason)}</div>
        ${model.hint ? `<div class="cb-inputs__setup-hint">${esc(model.hint)}</div>` : ''}
      </div>`;
  }
  const cards = model.sports.map(renderSportCard).join('');
  const combined = `
    <div class="cb-insight cb-inputs__combined">
      <div class="cb-section-label">intervals.icu combined fitness / fatigue</div>
      <div class="cb-inputs__row"><span class="cb-muted">Cross-check (their model)</span><span class="cb-inputs__val">${esc(model.combined)}</span></div>
      <div class="cb-inputs__note">intervals.icu's daily model across all sports; ours is per sport on a
        weekly training-load scale (×7), so the two are not directly comparable.</div>
    </div>`;
  const wellness = `
    <div class="cb-insight cb-inputs__wellness">
      <div class="cb-section-label">Wellness · last 7 days</div>
      <div class="cb-inputs__chips">
        <span class="cb-inputs__chip">Sleep avg <b>${esc(model.wellness.sleepAvg)}</b></span>
        <span class="cb-inputs__chip">HRV <b>${esc(model.wellness.hrv)}</b></span>
        <span class="cb-inputs__chip">Resting HR <b>${esc(model.wellness.restingHr)}</b></span>
      </div>
      <div class="cb-inputs__note">${GARMIN_NOTE}</div>
    </div>`;
  return `<div class="cb-inputs">${cards}${combined}${wellness}`
    + `${renderDestinations(model.uploadDestinations)}`
    + `${renderPushErrors(model.pushErrors)}</div>`;
}
