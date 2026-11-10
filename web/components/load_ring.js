/* Endury — achieved/planned TSS progress ring (week header, rail card). */

import { esc, TSS_HINT } from './util.js';

/** Render the load ring SVG. size 60 (mobile) → r25/stroke5/15px value; size
    88 (web) → r37/stroke7/20px value — both derived from stroke = round(size/12),
    r = size/2 - stroke, so the two known presets fall out of one formula. */
export function renderLoadRing({ achieved, planned, size = 60 } = {}) {
  const big = size >= 80;
  const stroke = Math.round(size / 12);
  const r = size / 2 - stroke;
  const c = 2 * Math.PI * r;
  const pct = planned ? Math.max(0, Math.min(1, (achieved || 0) / planned)) : 0;
  const offset = c * (1 - pct);
  const cxy = size / 2;
  const valueFs = big ? 20 : 15;
  const sub = planned ? `of ${Math.round(planned)}` : 'load done';
  return `<div class="cb-ring" style="width:${size}px;height:${size}px">`
    + `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">`
    + `<circle cx="${cxy}" cy="${cxy}" r="${r}" fill="none" stroke="var(--cb-border)" stroke-width="${stroke}"/>`
    + `<circle cx="${cxy}" cy="${cxy}" r="${r}" fill="none" stroke="var(--cb-accent)" stroke-width="${stroke}" `
    + `stroke-linecap="round" stroke-dasharray="${c.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" `
    + `transform="rotate(-90 ${cxy} ${cxy})" class="cb-ring__progress"/>`
    + `</svg>`
    + `<div class="cb-ring__center">`
    + `<span class="cb-ring__value" style="font-size:${valueFs}px">${esc(Math.round(achieved || 0))}</span>`
    + `<span class="cb-ring__sub" title="${esc(TSS_HINT)}">${esc(sub)}</span>`
    + `</div>`
    + `</div>`;
}
