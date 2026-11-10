/* Endury — display formatting helpers shared by all components. */

/** 5400 → "1:30", 2700 → "45min" */
export function fmtDur(seconds) {
  if (seconds == null) return '—';
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}min`;
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return mm ? `${h}:${String(mm).padStart(2, '0')}` : `${h}h`;
}

/** 46554 → "46.6 km", 900 → "900 m" */
export function fmtDist(meters) {
  if (meters == null) return '—';
  if (meters < 2000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

/** m/s → "5:12 /km" (run) */
export function fmtPace(mps) {
  if (!mps) return '—';
  const total = Math.round(1000 / mps);   // round total seconds — no ":60"
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')} /km`;
}

/** m/s → "1:47 /100m" (swim) */
export function fmtSwimPace(mps) {
  if (!mps) return '—';
  const total = Math.round(100 / mps);   // round total seconds — no ":60"
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')} /100m`;
}

/** sec/km (threshold pace) → "4:30 /km" — converts to m/s and reuses fmtPace. */
export function fmtThresholdPace(secPerKm) {
  return secPerKm ? fmtPace(1000 / secPerKm) : '—';
}

/** sec/100m (swim CSS) → "1:47 /100m" — converts to m/s and reuses fmtSwimPace. */
export function fmtCss(secPer100m) {
  return secPer100m ? fmtSwimPace(100 / secPer100m) : '—';
}

export function fmtTss(tss) {
  return tss == null ? '—' : String(Math.round(tss));
}

/** "2026-07-20" → local Date (no TZ surprises) */
export function parseDay(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function toIsoDay(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** Monday of the week containing iso day */
export function mondayOf(iso) {
  const d = parseDay(iso);
  const dow = (d.getDay() + 6) % 7;
  d.setDate(d.getDate() - dow);
  return toIsoDay(d);
}

export function addDays(iso, n) {
  const d = parseDay(iso);
  d.setDate(d.getDate() + n);
  return toIsoDay(d);
}

export const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
export const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2026-07-20" → "Mon, Jul 20" */
export function fmtDayLabel(iso) {
  const d = parseDay(iso);
  return `${DAY_NAMES[(d.getDay() + 6) % 7]}, ${MONTH_NAMES[d.getMonth()]} ${d.getDate()}`;
}

/** fmtDayLabel, plus the year when it differs from todayIso's — a race
    dated next year must not read as "this year" for want of one. */
export function fmtDayLabelYear(iso, todayIso) {
  const year = parseDay(iso).getFullYear();
  return year === parseDay(todayIso).getFullYear()
    ? fmtDayLabel(iso)
    : `${fmtDayLabel(iso)}, ${year}`;
}
