/* Endury — recorded intensity trace for a done session's sheet: one bar
   per time bucket of GET /api/activity/{id}/trace, height = the sport's main
   quantity as a share of the athlete's threshold, colour = zone. */

import { esc, sportColor, zoneColor } from './util.js';
import { fmtPace, fmtSwimPace } from '../data/format.js';

// Plain words only: this caption is the athlete's first meeting with the
// number, so it never spells a training acronym.
const METRIC_LABEL = { power: 'Power', pace: 'Pace', hr: 'Heart rate' };

/** The metric's athlete-facing name ('pace' splits on grade adjustment). */
function metricName(trace) {
  if (trace.metric === 'pace' && trace.grade_adjusted) return 'Grade-adjusted pace';
  return METRIC_LABEL[trace.metric];
}

/** The caption under the trace — also the string the scrub readout restores. */
function captionFor(trace) {
  const by = trace.source === 'intervals' ? ' · by interval' : '';
  return `${metricName(trace)} · % of threshold${by}`;
}

/** Seconds into the session → "32:10" ("1:12:04" past the hour). */
function clock(sec) {
  const t = Math.round(sec);
  const ss = String(t % 60).padStart(2, '0');
  const mm = Math.floor(t / 60) % 60;
  if (t < 3600) return `${mm}:${ss}`;
  return `${Math.floor(t / 3600)}:${String(mm).padStart(2, '0')}:${ss}`;
}

/** One bucket's measured value in the metric's own unit (pace arrives as
    speed in m/s, and the sport decides which pace idiom reads it). The
    intervals source scores a row off its own `intensity` when the sport's
    field is empty, so a bucket can have a percent and no measured number —
    that one contributes nothing to the readout rather than a "null W". */
function valueText(trace, sport, v) {
  if (v == null) return '';
  if (trace.metric === 'power') return `${v} W`;
  if (trace.metric === 'hr') return `${v} bpm`;
  return sport === 'swim' ? fmtSwimPace(v) : fmtPace(v);
}

/** The scrub line for one bucket: "32:10 · 245 W · 79% · Z3". */
function readout(trace, sport, i, midSec) {
  const at = clock(midSec);
  if (trace.pct[i] == null) return `${at} · rest`;
  const parts = [at, valueText(trace, sport, trace.value[i]), `${Math.round(trace.pct[i])}%`];
  if (trace.zone[i] != null) parts.push(`Z${trace.zone[i]}`);
  if (trace.elevation) parts.push(`${trace.elevation[i]} m`);
  return parts.filter(Boolean).join(' · ');
}

/** The bar strip plus the cumulative right-edge fractions the scrub
    hit-tests against (equal widths for streams, `frac` shares per interval
    row). Returns {html, cuts}. */
function buildBars(trace, sport) {
  const { lo, hi } = trace.scale;
  const n = trace.pct.length;
  const cuts = [];
  let cum = 0;
  const html = trace.pct.map((pct, i) => {
    const share = trace.frac ? trace.frac[i] : 1 / n;
    const mid = (cum + share / 2) * trace.duration_s;
    cum += share;
    cuts.push(cum.toFixed(4));
    const basis = trace.frac ? `flex-basis:${(share * 100).toFixed(4)}%;` : '';
    const read = esc(readout(trace, sport, i, mid));
    if (pct == null) {
      // A rest/no-data bucket has no measured height to carry: CSS owns it.
      return `<span class="cb-trace__bar cb-trace__bar--rest" style="${basis}" data-read="${read}"></span>`;
    }
    const h = Math.min(1, Math.max(0.04, (pct - lo) / (hi - lo)));
    const fill = trace.zone[i] == null ? sportColor(sport) : zoneColor(trace.zone[i]);
    return `<span class="cb-trace__bar" style="${basis}height:${(h * 100).toFixed(1)}%;background:${fill}" data-read="${read}"></span>`;
  }).join('');
  return { html, cuts };
}

/** Elevation silhouette under the trace, normalised to the activity's own
    min/max. The backend only sends the array when the climb is worth a
    picture (30 m of range), so there is no flat-profile case to guard. */
function elevationSilhouette(trace) {
  const metres = trace.elevation;
  if (!metres) return '';
  const lo = Math.min(...metres);
  const hi = Math.max(...metres);
  const last = metres.length - 1;
  const points = metres
    .map((m, i) => `${i},${(100 - ((m - lo) / (hi - lo)) * 100).toFixed(1)}`)
    .join(' ');
  const label = `Elevation through the session, ${lo} to ${hi} metres`;
  return `<svg class="cb-trace__elev" viewBox="0 0 ${last} 100" preserveAspectRatio="none"
        role="img" aria-label="${esc(label)}"><polygon class="cb-trace__elev-fill"
        points="0,100 ${points} ${last},100"></polygon></svg>`;
}

/** Fixed-height placeholder the session sheet renders straight away, so
    filling the trace in later never shifts the sheet under the athlete's
    thumb. */
export function renderTraceSlot() {
  return '<div data-trace-slot><div class="cb-trace-skeleton" aria-hidden="true"></div></div>';
}

/** Render the trace for one /trace payload. */
export function renderActivityTrace(trace, { sport } = {}) {
  const { lo, hi } = trace.scale;
  const { html, cuts } = buildBars(trace, sport);
  const caption = captionFor(trace);
  const aria = `${metricName(trace)} through the session as a share of your threshold, `
    + 'one bar per time slice, coloured by training zone';
  const showThreshold = lo < 100 && hi > 100;
  const threshold = showThreshold
    ? `<div class="cb-trace__thresh" style="bottom:${(((100 - lo) / (hi - lo)) * 100).toFixed(1)}%"></div>`
    : '';

  return `
    <div class="cb-trace">
      <div class="cb-trace__plot">
        <div class="cb-trace__box" role="img" aria-label="${esc(aria)}" data-trace-box>
          ${threshold}
          <div class="cb-trace__bars${trace.frac ? '' : ' cb-trace__bars--even'}"
            data-trace-bars data-cuts="${cuts.join(',')}">${html}</div>
          <div class="cb-trace__cursor" data-trace-cursor hidden></div>
        </div>
        ${elevationSilhouette(trace)}
      </div>
      <div class="cb-trace__foot">
        <span data-trace-caption data-caption="${esc(caption)}">${esc(caption)}</span>
        ${showThreshold ? '<span class="cb-trace__legend" aria-hidden="true"><span class="cb-trace__legend-rule"></span>threshold</span>' : ''}
      </div>
    </div>`;
}

/** Wire the scrub: a pointer over (or a finger dragged across) the trace box
    parks a cursor line on the hovered bucket and swaps the caption for that
    bucket's numbers; leaving restores the caption. Each bar carries its own
    readout, so the handler only has to find the index. */
export function bindTraceScrub(rootEl) {
  const box = rootEl.querySelector('[data-trace-box]');
  if (!box) return;
  const barsEl = box.querySelector('[data-trace-bars]');
  const cursor = box.querySelector('[data-trace-cursor]');
  const caption = rootEl.querySelector('[data-trace-caption]');
  const cuts = barsEl.dataset.cuts.split(',').map(Number);

  function scrub(e) {
    const rect = box.getBoundingClientRect();
    const at = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const idx = cuts.findIndex((c) => at <= c);
    cursor.style.left = `${(at * 100).toFixed(2)}%`;
    cursor.hidden = false;
    caption.textContent = barsEl.children[idx < 0 ? cuts.length - 1 : idx].dataset.read;
  }
  function release() {
    cursor.hidden = true;
    caption.textContent = caption.dataset.caption;
  }
  box.addEventListener('pointerdown', scrub);
  box.addEventListener('pointermove', scrub);
  box.addEventListener('pointerleave', release);
  box.addEventListener('pointercancel', release);
}
