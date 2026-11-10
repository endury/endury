/* Endury — tiny shared helpers for the component layer. */

/** Plain-language gloss for "TSS", the one acronym the UI can't avoid.
    Attach it as a title= at the surfaces where the athlete meets the number
    first (week load ring, day totals, card meta, month cell) — not inside
    modals that already sit downstream of one of those. */
/** For figures that may mix sources: intervals.icu's API terms ask for the credit. */
export const GARMIN_NOTE = 'May include data from Garmin devices.';

export const TSS_HINT = 'Training load: how much strain a workout puts on you';

/** Escape text for interpolation into HTML strings. */
export function esc(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

/** Sport → CSS color var reference. */
export function sportColor(sport) {
  return `var(--cb-sport-${sport})`;
}

/** Sport → rgb triplet var (for alpha tints: rgba(var(--…-rgb), .12)). */
export function sportRgb(sport) {
  return `var(--cb-sport-${sport}-rgb)`;
}

/** Training zone → CSS color var reference, clamped to the 5-zone palette. */
export function zoneColor(z) {
  return `var(--cb-zone-${Math.min(Math.max(z, 1), 5)})`;
}
