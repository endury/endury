/* Endury — a chart under a coach reply. The coach's analysis script
   describes it (endury/analysis/charts.py: kind, x, up to three series in
   one unit, a title); this draws it as an SVG string, so both shells can
   re-render the whole chat as they always do. Tapping or hovering the plot
   reads one point off it (bindChartReadout). Two sessions side by side
   (two bar series) carry the change above each pair; a name with " · "
   reads on two lines ("km 9 · 14%"). */

import { esc } from './util.js';

const W = 320;
const H = 168;
const PAD = { left: 38, right: 8, top: 8, bottom: 22 };
const SERIES = ['var(--cb-zone-2)', 'var(--cb-warn)', 'var(--cb-zone-4)'];
// Seconds per km or per 100 m, drawn as m:ss with faster higher up.
const PACE_UNITS = new Set(['min/km', 'min/100m']);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const isDate = (v) => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v);
// The extra height a second line of names takes.
const SECOND_LINE = 12;

/* Two sessions side by side: the change from the first to the second is
   printed above each pair (not on a flipped pace axis, nor past 12 pairs). */
const pairs = (spec) => spec.kind === 'bar' && spec.ys.length === 2 && !spec.stacked
  && spec.x.length <= 12 && !PACE_UNITS.has(spec.unit);
const twoLines = (spec) => spec.x.some((v) => typeof v === 'string' && v.includes(' · '));

function change(a, b) {
  if (a == null || b == null || !a) return '';
  const d = Math.round(100 * (b / a - 1));
  return `${d > 0 ? '+' : ''}${d}%`;
}

function mss(sec) {
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** One value in the chart's unit, as the readout and the axis print it. */
function valueText(v, unit) {
  if (v == null || !Number.isFinite(v)) return '–';
  if (PACE_UNITS.has(unit)) return mss(v);
  const n = Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100;
  return String(n);
}

function xText(v) {
  if (isDate(v)) return `${Number(v.slice(8, 10))} ${MONTHS[Number(v.slice(5, 7)) - 1] || ''}`;
  if (typeof v === 'string') return v.length > 9 ? `${v.slice(0, 8)}…` : v;
  return valueText(v, '');
}

/* Round tick values covering lo..hi, about n of them: the first at or
   below lo, the last at or above hi, so nothing is drawn past the axis. */
function ticks(lo, hi, n) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const first = Math.floor(lo / step);
  const last = Math.max(Math.ceil(hi / step), first + 1);
  const out = [];
  for (let i = first; i <= last; i += 1) out.push(+(i * step).toFixed(6));
  return out;
}

/* Scales for one spec: x by index for bars and names, by value otherwise. */
function layout(spec) {
  const n = spec.x.length;
  const banded = spec.kind === 'bar' || spec.x.some((v) => typeof v === 'string' && !isDate(v));
  const xs = spec.x.map((v, i) => (banded ? i : isDate(v) ? Date.parse(v) : v));
  const totals = spec.stacked
    ? spec.x.map((_, i) => spec.ys.reduce((sum, y) => sum + (y[i] || 0), 0))
    : [];
  const vals = [...spec.ys.flat(), ...totals].filter((v) => v != null && Number.isFinite(v));
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (spec.kind === 'bar') {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi) * (pairs(spec) ? 1.12 : 1);
  } else {
    const pad = (hi - lo) * 0.08 || 1;
    lo -= pad;
    hi += pad;
  }
  const t = ticks(lo, hi, 4);
  lo = t[0];
  hi = t[t.length - 1];
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const band = banded ? (W - PAD.left - PAD.right) / Math.max(n, 1) : 0;
  const X = (i) => (banded
    ? PAD.left + band * (i + 0.5)
    : PAD.left + ((xs[i] - x0) / ((x1 - x0) || 1)) * (W - PAD.left - PAD.right));
  const flip = PACE_UNITS.has(spec.unit);
  const Y = (v) => {
    const f = (v - lo) / ((hi - lo) || 1);
    return PAD.top + (flip ? f : 1 - f) * (H - PAD.top - PAD.bottom);
  };
  return { n, band, X, Y, ticks: t };
}

function plot(spec, { n, band, X, Y }) {
  let out = '';
  const stack = spec.x.map(() => 0);
  spec.ys.forEach((y, k) => {
    const color = SERIES[k];
    if (spec.kind === 'bar') {
      const w = Math.max(2, Math.min(18, (spec.stacked ? band : band / spec.ys.length) - 3));
      y.forEach((v, i) => {
        if (v == null) return;
        const from = spec.stacked ? stack[i] : 0;
        const to = from + v;
        if (spec.stacked) stack[i] = to;
        const x = spec.stacked ? X(i) - w / 2 : X(i) - (spec.ys.length * w) / 2 + k * w;
        const top = Math.min(Y(from), Y(to));
        out += `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.abs(Y(to) - Y(from)).toFixed(1)}" rx="1.5" fill="${color}"/>`;
      });
      return;
    }
    if (spec.kind === 'scatter') {
      y.forEach((v, i) => {
        if (v != null) out += `<circle cx="${X(i).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.6" fill="${color}"/>`;
      });
      return;
    }
    let d = '';
    let pen = false;
    y.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true;
    });
    if (spec.kind === 'area' && n > 1) {
      const floor = H - PAD.bottom;
      out += `<path d="${d}L${X(n - 1).toFixed(1)},${floor}L${X(0).toFixed(1)},${floor}Z" fill="${color}" fill-opacity=".14"/>`;
    }
    out += `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  return out;
}

/** One chart spec as an HTML string: title, legend, readout line, SVG. */
export function renderChart(spec) {
  if (!spec || !Array.isArray(spec.x) || !Array.isArray(spec.ys) || !spec.x.length) return '';
  const geo = layout(spec);
  const grid = geo.ticks.map((t) => (
    `<line x1="${PAD.left}" x2="${W - PAD.right}" y1="${geo.Y(t).toFixed(1)}" y2="${geo.Y(t).toFixed(1)}" class="cb-chart__grid"/>`
    + `<text x="${PAD.left - 4}" y="${(geo.Y(t) + 3).toFixed(1)}" text-anchor="end" class="cb-chart__tick">${esc(valueText(t, spec.unit))}</text>`
  )).join('');
  const zero = geo.ticks[0] < 0
    ? `<line x1="${PAD.left}" x2="${W - PAD.right}" y1="${geo.Y(0).toFixed(1)}" y2="${geo.Y(0).toFixed(1)}" class="cb-chart__zero"/>`
    : '';
  const lines = twoLines(spec);
  const height = H + (lines ? SECOND_LINE : 0);
  const every = Math.max(1, Math.ceil(geo.n / 6));
  let xLabels = '';
  for (let i = 0; i < geo.n; i += every) {
    const [first, second] = String(spec.x[i]).split(' · ');
    const x = geo.X(i).toFixed(1);
    xLabels += `<text x="${x}" y="${H - 7}" text-anchor="middle" class="cb-chart__tick">${esc(lines ? first : xText(spec.x[i]))}</text>`;
    if (lines && second) xLabels += `<text x="${x}" y="${H - 7 + SECOND_LINE}" text-anchor="middle" class="cb-chart__tick">${esc(second)}</text>`;
  }
  let deltas = '';
  if (pairs(spec)) {
    spec.x.forEach((_, i) => {
      const [a, b] = [spec.ys[0][i], spec.ys[1][i]];
      const top = Math.min(geo.Y(a ?? 0), geo.Y(b ?? 0));
      deltas += `<text x="${geo.X(i).toFixed(1)}" y="${(top - 4).toFixed(1)}" text-anchor="middle" class="cb-chart__tick">${esc(change(a, b))}</text>`;
    });
  }
  const legend = spec.labels && spec.ys.length > 1
    ? `<div class="cb-chart__legend">${spec.labels.map((label, k) => (
      `<span><i style="background:${SERIES[k]}"></i>${esc(label)}</span>`
    )).join('')}</div>`
    : '';
  const caption = [spec.unit, spec.x_label && `by ${spec.x_label}`].filter(Boolean).join(' · ');
  return `<figure class="cb-chart" data-chart="${esc(JSON.stringify(spec))}">`
    + `<figcaption class="cb-chart__title">${esc(spec.title || '')}</figcaption>`
    + legend
    + `<div class="cb-chart__readout" data-caption="${esc(caption)}">${esc(caption)}</div>`
    + `<svg class="cb-chart__plot" viewBox="0 0 ${W} ${height}" role="img" aria-label="${esc(spec.title || 'Chart')}">`
    + grid + zero + plot(spec, geo) + deltas + xLabels
    + `<line class="cb-chart__cursor" x1="0" x2="0" y1="${PAD.top}" y2="${H - PAD.bottom}" visibility="hidden"/>`
    + `</svg></figure>`;
}

const parsed = new WeakMap();

function readAt(svg, clientX) {
  const fig = svg.closest('.cb-chart');
  if (!parsed.has(fig)) parsed.set(fig, JSON.parse(fig.dataset.chart));
  const spec = parsed.get(fig);
  const geo = layout(spec);
  const box = svg.getBoundingClientRect();
  const px = ((clientX - box.left) / box.width) * W;
  let best = 0;
  for (let i = 1; i < geo.n; i += 1) {
    if (Math.abs(geo.X(i) - px) < Math.abs(geo.X(best) - px)) best = i;
  }
  const cursor = svg.querySelector('.cb-chart__cursor');
  cursor.setAttribute('x1', geo.X(best));
  cursor.setAttribute('x2', geo.X(best));
  cursor.setAttribute('visibility', 'visible');
  const values = spec.ys.map((y, k) => (
    `${spec.labels && spec.ys.length > 1 ? `${spec.labels[k]} ` : ''}${valueText(y[best], spec.unit)}`
  )).join(' · ');
  const unit = PACE_UNITS.has(spec.unit) ? spec.unit : spec.unit || '';
  const moved = spec.ys.length === 2 ? change(spec.ys[0][best], spec.ys[1][best]) : '';
  const where = String(spec.x[best]).includes(' · ') ? spec.x[best] : xText(spec.x[best]);
  fig.querySelector('.cb-chart__readout').textContent = `${where}${spec.x_label ? ` ${spec.x_label}` : ''} · ${values} ${unit}${moved ? ` (${moved})` : ''}`.trim();
}

let bound = false;

/** Read a point off any chart the pointer touches; binds once per page. */
export function bindChartReadout() {
  if (bound) return;
  bound = true;
  const onPoint = (ev) => {
    const svg = ev.target.closest?.('.cb-chart__plot');
    if (svg) readAt(svg, ev.clientX);
  };
  document.addEventListener('pointermove', onPoint);
  document.addEventListener('pointerdown', onPoint);
}
