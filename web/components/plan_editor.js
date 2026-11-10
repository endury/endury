/* Endury — plan editor drawer/modal content (goal, ambition, A-race,
   tune-up races, hourly preview, self-assessment, CTA). The drawer (mobile, slide-in) and
   modal (web, centered two-column) chrome is added by the shell; this
   renders the content only, as a single flowing column — the web shell's
   own CSS is responsible for promoting `.cb-preview` etc. into a second
   column, since this function takes no shell param and content is
   identical between mobile and web. */

import { esc, sportColor } from './util.js';

/* One selection primitive for all three single-select rows:
   each control is a role="radio" .cb-select whose selected state is the
   same outline + tint grammar; variants change size/layout only. */
function famBtn(f) {
  return `<button type="button" class="cb-select cb-select--fill" data-action="set-family" data-fam="${esc(f.id)}" role="radio" aria-checked="${f.active}">${esc(f.label)}</button>`;
}

/* An experimental goal wears the kit's existing badge vocabulary — no new
   chip class — so a less-validated plan type is visible while it is picked,
   not only in the preview note below. The badge's title is the backend's
   own maturity_note, read on hover/focus like the ramp bar's own title. */
function goalPill(g) {
  const title = g.maturityNote ? ` title="${esc(g.maturityNote)}"` : '';
  const chip = g.experimental
    ? ` <span class="cb-badge cb-badge--grid cb-badge--muted"${title}>Experimental</span>`
    : '';
  return `<button type="button" class="cb-select cb-select--sm" data-action="set-goal" data-goal="${esc(g.id)}" role="radio" aria-checked="${g.active}">${esc(g.label)}${chip}</button>`;
}

/* Optional trail race profile: distance + climb → km-effort band hint.
   The suggestion text is patched in place on keystroke (data-trail-hint)
   so typing never re-renders the editor and drops focus — same pattern
   as the self-assessment fields. */
function trailProfileRow(t) {
  if (!t) return '';
  const field = (kind, label, value) => `
      <div>
        <div class="cb-arace-label">${esc(label)}</div>
        <input type="text" inputmode="decimal" class="cb-arace-date-input" style="width:58px"
          data-action="trail-${kind}" value="${esc(value)}" aria-label="Race ${esc(label)}">
      </div>`;
  return `
    <div>
      <div class="cb-race-row">
        <div class="cb-race-info"><div class="cb-race-label">Race profile (optional)</div></div>
        ${field('km', 'distance km', t.km)}
        ${field('gain', 'climb m', t.gain)}
      </div>
      <div class="cb-arace-hint cb-arace-hint--warn" data-trail-hint>${esc(t.hint)}</div>
    </div>`;
}

function ambCard(a) {
  return `
    <button type="button" class="cb-select cb-select--stacked" data-action="set-ambition" data-amb="${esc(a.id)}" role="radio" aria-checked="${a.active}">
      <span class="cb-amb-label">${esc(a.label)}</span>
      <span class="cb-amb-sub">${esc(a.sub)}</span>
    </button>`;
}

/* "Hold my weekly volume". A plain checkbox in the races-row shell:
   the kit has no checkbox vocabulary, and inventing one for a single
   control would add a second selection grammar beside .cb-select. */
function holdVolumeRow(h) {
  if (!h) return '';
  return `
    <label class="cb-race-row" style="margin-top:var(--cb-sp-2)">
      <input type="checkbox" data-action="hold-volume"${h.checked ? ' checked' : ''}>
      <div class="cb-race-info">
        <div class="cb-race-label">Hold my weekly volume at ${esc(h.hours)} h/week</div>
      </div>
    </label>`;
}

function arace(a, minDate) {
  if (!a) return '';
  // The hint (set only when the planner would refuse or flag this date)
  // sits with the row, not a flex-gap away from it.
  return `
    <div>
      <div class="cb-arace">
        <div>
          <div class="cb-arace-tag">
            <span class="cb-arace-badge">A</span>
            <span class="cb-arace-label">Race day</span>
          </div>
          <input type="date" class="cb-arace-date-input" data-action="a-race-date"
            min="${esc(minDate)}" value="${esc(a.dateValue || '')}" aria-label="A race date">
        </div>
        <div class="cb-arace-away">
          <div class="cb-arace-label">Away</div>
          <div class="cb-arace-away-val">${esc(a.awayLabel)}</div>
        </div>
      </div>
      ${a.hint ? `<div class="cb-arace-hint cb-arace-hint--${esc(a.hint.tone)}">${esc(a.hint.text)}</div>` : ''}
    </div>`;
}

function maintDuration(m) {
  if (!m) return '';
  const step = (delta, label, disabled) => `<button type="button" class="cb-maint-step"
    data-action="maint-weeks-adjust" data-delta="${delta}" aria-label="${label}"${disabled ? ' disabled' : ''}>${delta > 0 ? '+' : '−'}</button>`;
  return `
    <div class="cb-arace">
      <div>
        <div class="cb-arace-tag">
          <span class="cb-arace-badge cb-arace-badge--maint">M</span>
          <span class="cb-arace-label">Block duration</span>
        </div>
        <div class="cb-maint-stepper" role="group" aria-label="Maintenance block duration">
          ${step(-1, 'One week shorter', m.weeks <= m.min)}
          <span class="cb-maint-weeks">${esc(m.weeks)} wk</span>
          ${step(1, 'One week longer', m.weeks >= m.max)}
        </div>
      </div>
      <div class="cb-arace-away">
        <div class="cb-arace-label">Ends</div>
        <div class="cb-arace-away-val">${esc(m.endLabel || '')}</div>
      </div>
    </div>`;
}

function assessRow(s) {
  const field = (kind, label, value, hint) => `
      <div>
        <div class="cb-arace-label">${esc(label)}</div>
        <input type="text" inputmode="decimal" class="cb-arace-date-input" style="width:58px"
          data-action="assess-${kind}" data-sport="${esc(s.id)}" value="${esc(value)}"
          aria-label="${esc(s.label)} ${esc(hint)}">
      </div>`;
  return `
    <div class="cb-race-row">
      <div class="cb-race-info"><div class="cb-race-label">${esc(s.label)}</div></div>
      ${field('hours', 'h / week (0–40)', s.hours, 'weekly hours, 0 to 40')}
      ${field('longest', 'longest min (0–600)', s.longest, 'longest session in minutes, 0 to 600')}
    </div>`;
}

function assessSection(a) {
  if (!a) return '';
  return `
      <div>
        <div class="cb-races-head">
          <span class="cb-races-label">About your training</span>
        </div>
        <div class="cb-races-list">
          <div class="cb-assess-note">${esc(a.note)}</div>
          ${a.sports.map(assessRow).join('')}
        </div>
      </div>`;
}

/* "Your races" (step 10): chips from the athlete's stored race table
   (GET /api/races), at the top of the form so a race is the first thing to
   pick. Tapping one fills the A-race or adds a tune-up — shared.js's applyRaceChip owns what
   a tap does; this only renders the row, reusing the tune-ups section's
   own header (.cb-races-head/.cb-races-label) and the goal-pill row's flex-
   wrap layout (.cb-goal-row) rather than adding new CSS for one more row of
   buttons. Null hides the row entirely. */
function yourRacesRow(yours) {
  if (!yours) return '';
  const chip = (r) => `<button type="button" class="cb-select cb-select--sm" data-action="race-chip" data-id="${esc(r.id)}">${esc(r.label)}</button>`;
  return `
    <div>
      <div class="cb-races-head">
        <span class="cb-races-label">Start from one of your races</span>
      </div>
      <div class="cb-preview-note">${esc(yours.note)}</div>
      <div class="cb-goal-row">${yours.chips.map(chip).join('')}</div>
    </div>`;
}

/* One tune-up race: its type (grouped by family), its date and its priority.
   B and C are two buttons rather than one badge that cycles on a tap, so the
   choice is visible before it is made. */
function raceRow(r, minDate, raceGoals) {
  const pri = String(r.priority).toLowerCase();
  const option = (g) => `<option value="${esc(g.id)}"${g.id === r.goalType ? ' selected' : ''}>${esc(g.label)}</option>`;
  const groups = raceGoals.map((f) => `<optgroup label="${esc(f.label)}">${f.goals.map(option).join('')}</optgroup>`).join('');
  const priButton = (p) => {
    const on = p === r.priority;
    return `<button type="button" class="cb-race-pri${on ? ` cb-pri-${p.toLowerCase()}` : ''}" role="radio" aria-checked="${on}" data-action="set-priority" data-id="${esc(r.id)}" data-pri="${p}" aria-label="${p} race">${p}</button>`;
  };
  return `
    <div class="cb-race-row">
      <span class="cb-race-dot cb-pri-${pri}"></span>
      <div class="cb-race-info">
        <select class="cb-race-field" data-action="race-goal" data-id="${esc(r.id)}" aria-label="Race type">${groups}</select>
        <input type="date" class="cb-race-field cb-race-date" data-action="race-date" data-id="${esc(r.id)}"
          min="${esc(minDate)}" value="${esc(r.dateValue || '')}" aria-label="Race date">
      </div>
      <div class="cb-race-pris" role="radiogroup" aria-label="Priority">${['B', 'C'].map(priButton).join('')}</div>
      <button type="button" class="cb-race-remove" data-action="remove-race" data-id="${esc(r.id)}" aria-label="Remove race">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
      </button>
    </div>`;
}

function markerCell(m) {
  const dot = m.dot ? `<span class="cb-marker-dot cb-pri-${esc(m.dot)}"></span>` : '';
  return `<span class="cb-marker-cell">${dot}</span>`;
}

function rampBar(b) {
  const h = Math.max(9, Math.round((b.frac || 0) * 100));
  const cls = b.color ? ` cb-pri-${esc(b.color)}` : '';
  // The chart has no axis and no per-bar figure; the label is the only way to
  // read a week off it.
  const title = b.label ? ` title="${esc(b.label)}"` : '';
  return `<span class="cb-ramp-bar${cls}"${title} style="height:${h}%"></span>`;
}

function sportSeg(s) {
  return `<span style="width:${esc(s.pct)}%;background:${sportColor(s.sport)}"></span>`;
}

function sportRow(s) {
  return `
    <div class="cb-sport-row">
      <span class="cb-sport-dot" style="background:${sportColor(s.sport)}"></span>
      <span class="cb-sport-label">${esc(s.label)}</span>
      <span class="cb-sport-hours">${esc(s.hoursText)}</span>
      <span class="cb-sport-pct">${esc(s.pct)}%</span>
    </div>`;
}

/** Render the plan editor drawer/modal content for one wizard state. */
export function renderPlanEditor(model) {
  const {
    families, goals, trailProfile, ambitions, holdVolume, aRace, maintenance, races, raceGoals,
    yourRaces, assess, preview, ctaLabel, ctaDisabled, minDate,
  } = model;

  const priorityNote = races.length
    ? '<div class="cb-preview-note">B: a short taper, so you race it fresh. C: a lighter training week with the race in it.</div>'
    : '';
  const tuneUps = `
      <div>
        <div class="cb-races-head">
          <span class="cb-races-label">Tune-up races</span>
          <button type="button" class="cb-races-add" data-action="add-race">+ Add</button>
        </div>
        ${priorityNote}
        <div class="cb-races-list">${races.map((r) => raceRow(r, minDate, raceGoals)).join('')}</div>
      </div>`;
  const legend = preview.showLegend === false ? '' : `
        <div class="cb-race-legend">
          <span class="cb-legend-item"><span class="cb-legend-dot cb-pri-a"></span>A race</span>
          <span class="cb-legend-item"><span class="cb-legend-dot cb-pri-b"></span>B</span>
          <span class="cb-legend-item"><span class="cb-legend-dot cb-pri-c"></span>C</span>
        </div>`;

  return `
    <div class="cb-plan">
      ${yourRacesRow(yourRaces)}
      <div>
        <div class="cb-section-label">Goal</div>
        <div class="cb-fam-row" role="radiogroup" aria-label="Sport">${families.map(famBtn).join('')}</div>
        <div class="cb-goal-row" role="radiogroup" aria-label="Race distance">${goals.map(goalPill).join('')}</div>
        ${trailProfileRow(trailProfile)}
      </div>
      <div>
        <div class="cb-section-label">Ambition</div>
        <div class="cb-amb-row" role="radiogroup" aria-label="Ambition">${ambitions.map(ambCard).join('')}</div>
        ${holdVolumeRow(holdVolume)}
      </div>
      ${maintenance ? maintDuration(maintenance) : arace(aRace, minDate)}
      ${tuneUps}
      <div class="cb-preview">
        <div class="cb-preview-head">
          <span class="cb-preview-title">Hourly Preview</span>
          <span class="cb-preview-eyebrow">${esc(preview.peakEyebrow || 'peak week')}</span>
        </div>
        <div class="cb-preview-peak">
          <span class="cb-preview-peak-num">${esc(preview.peakLabel)}</span>
          <span class="cb-preview-peak-label">${esc(preview.peakUnit || 'h/wk at peak')}</span>
        </div>
        <div class="cb-marker-row">${preview.markers.map(markerCell).join('')}</div>
        <div class="cb-ramp-row">${preview.rampBars.map(rampBar).join('')}</div>
        <div class="cb-phase-labels">${preview.phases.map((p) => `<span>${esc(p)}</span>`).join('')}</div>
        ${legend}
        <div class="cb-divider"></div>
        <div class="cb-sport-strip">${preview.sportRows.map(sportSeg).join('')}</div>
        <div class="cb-sport-rows">${preview.sportRows.map(sportRow).join('')}</div>
        ${preview.note ? `<div class="cb-preview-note">${esc(preview.note)}</div>` : ''}
      </div>
      ${assessSection(assess)}
      <button type="button" class="cb-btn-primary cb-btn-primary--block" data-action="preview-plan"${ctaDisabled ? ' disabled' : ''}>${esc(ctaLabel)}</button>
    </div>`;
}
