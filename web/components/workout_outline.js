/* Endury — workout outline mini-bars (session card + detail sheet). */

import { sportRgb } from './util.js';

const KNOWN_TYPES = new Set(['warmup', 'interval', 'recovery', 'cooldown', 'rest']);

// The strip is the only unlabelled graphic in the card/detail vocabulary — it
// carries no legend anywhere, so it explains itself on hover and to a screen
// reader instead.
const OUTLINE_LABEL =
  'Workout shape: warm-up, work, recovery and cool-down steps, sized by length';

/** Render the outline mini-bar strip for a session's [{type, frac}] steps.
    sport tints every segment via rgb(var(--cb-sport-<sport>-rgb)); unrecognized
    step types fall back to the 'unknown' height/opacity preset. */
export function renderOutline(outline, { height = 13, sport } = {}) {
  const segs = (outline || []).map((seg) => {
    const type = KNOWN_TYPES.has(seg.type) ? seg.type : 'unknown';
    const basis = Math.max(0, (seg.frac || 0) * 100);
    return `<span class="cb-outline__seg cb-outline__seg--${type}" style="flex-basis:${basis}%"></span>`;
  }).join('');
  const radius = height >= 22 ? 5 : 4;
  const color = sport ? `rgb(${sportRgb(sport)})` : 'var(--cb-text-muted)';
  return `<div class="cb-outline" role="img" title="${OUTLINE_LABEL}" aria-label="${OUTLINE_LABEL}" `
    + `style="height:${height}px;border-radius:${radius}px;--cb-outline-color:${color}">${segs}</div>`;
}
