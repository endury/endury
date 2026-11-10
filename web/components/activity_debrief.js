/* Endury — in-app activity debrief: the projection served by
   GET /api/activity/{id}/debrief (the same one the coach's
   get_activity_detail tool reads), rendered into a done session's sheet as
   rows and the pages behind them: what the activity counts as in the plan
   (or the race it was), its reps against the plan's band, time in zones,
   collapsible per-rep splits; plus the Coach's take, the athlete's own
   verdict and the action row every sheet ends on. */

import { esc, zoneColor } from './util.js';
import { formatBody, renderThinking } from './coach_msg.js';
import { previewText, sheetPage, sheetRow } from './sheet_pages.js';
import { normSport } from '../data/adapters.js';
import { fmtDayLabel, fmtDist, fmtDur } from '../data/format.js';
// The demo's activities exist only in this tab: no intervals.icu page to open.
import { isDemo, loadSettings } from '../data/settings.js';

/** "5:12" (backend M:SS pace) → "5:12 /km"; null-safe. */
const perKm = (p) => (p ? `${p} /km` : null);

/** "1:55" (backend M:SS pace) → "1:55 /100m"; null-safe. */
const per100m = (p) => (p ? `${p} /100m` : null);

/** Time-in-zones source: HR first, then pace, then power (minus SS overlay). */
function zoneMinutes(view) {
  if (view.hr_zone_minutes?.length) {
    return { title: 'Time in HR zones', mins: view.hr_zone_minutes };
  }
  if (view.pace_zone_minutes?.length) {
    return { title: 'Time in pace zones', mins: view.pace_zone_minutes };
  }
  if (view.power_zone_minutes) {
    const mins = Object.entries(view.power_zone_minutes)
      .filter(([id]) => /^Z\d$/.test(id))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, m]) => m);
    if (mins.length) return { title: 'Time in power zones', mins };
  }
  return null;
}

/** The zones with time in them, as a bar and what it totals. */
function zoneBar(zones) {
  const total = zones.mins.reduce((a, b) => a + b, 0);
  const segs = zones.mins.map((min, i) => ({ z: i + 1, min })).filter((s) => s.min > 0);
  const bar = segs.map((s) =>
    `<span class="cb-zonebar__seg" style="flex:${(s.min / total).toFixed(4)};background:${zoneColor(s.z)}"></span>`).join('');
  return { total, segs, bar: `<span class="cb-zonebar">${bar}</span>` };
}

/** The Time in zones row and its page; none without time in a zone. */
function zonePart(view, back) {
  const zones = zoneMinutes(view);
  if (!zones) return null;
  const { total, segs, bar } = zoneBar(zones);
  if (!total) return null;
  const top = segs.reduce((a, b) => (b.min > a.min ? b : a));
  const legend = segs.map((s) =>
    `<span class="cb-zonebar-item"><span class="cb-zonebar-dot" style="background:${zoneColor(s.z)}"></span>Z${s.z} ${esc(fmtDur(s.min * 60))}</span>`).join('');
  return {
    row: sheetRow({
      title: 'Time in zones', summary: `Most in Z${top.z} · ${fmtDur(top.min * 60)}`, page: 'zones', body: bar,
    }),
    page: sheetPage({
      id: 'zones', title: zones.title, back, body: `<div>${bar}<div class="cb-zonebar-legend">${legend}</div></div>`,
    }),
  };
}

/** Pace/power/HR fragments for one interval row, joined with dots. */
function rowTarget(row, sport) {
  const parts = [
    sport === 'run' ? perKm(row.pace_min_km) : null,
    sport === 'swim' ? per100m(row.pace_min_100m) : null,
    sport === 'bike' && row.avg_watts ? `${row.avg_watts} W` : null,
    sport === 'bike' && !row.avg_watts && row.speed_kmh ? `${row.speed_kmh} km/h` : null,
    row.avg_hr ? `${row.avg_hr} bpm` : null,
    row.zone ? `Z${row.zone}` : null,
  ];
  return parts.filter(Boolean).join(' · ');
}

function rowBarStyle(row) {
  if (row.zone) return `background:${zoneColor(row.zone)}`;
  if (row.type === 'RECOVERY') return 'background:var(--cb-step-recovery)';
  if (row.type === 'WORK') return 'background:var(--cb-step-interval)';
  return 'background:var(--cb-step-rest)';
}

function intervalRow(row, sport, name) {
  return `
    <div class="cb-workout-row">
      <span class="cb-workout-bar" style="${rowBarStyle(row)}"></span>
      <div class="cb-workout-info">
        <div class="cb-workout-name">${esc(name)}</div>
        <div class="cb-workout-target">${esc(rowTarget(row, sport))}</div>
      </div>
      <span class="cb-workout-dur">${esc(row.duration || '')}</span>
    </div>`;
}

function groupsSection(view, sport) {
  const groups = view.groups || [];
  if (!groups.length) return '';
  return `<div class="cb-sheet-card">${groups.map((g) => intervalRow(g, sport, groupName(g))).join('')}</div>`;
}

const groupName = (g) => {
  const label = g.label || g.type || 'Interval';
  return g.count && g.count > 1 ? `${g.count} × ${label}` : label;
};

function splitsSection(view, sport) {
  const splits = view.intervals || [];
  if (!splits.length) return '';
  const rows = splits.map((s) => {
    const dist = s.distance_km ? ` · ${fmtDist(s.distance_km * 1000)}` : '';
    return intervalRow(s, sport, `#${s.n}${dist}`);
  }).join('');
  const omitted = view.intervals_omitted
    ? `<div class="cb-workout-target" style="margin-top:6px">…${esc(view.intervals_omitted)} more on intervals.icu</div>`
    : '';
  return `
    <div>
      <button type="button" class="cb-workout-toggle" data-action="toggle-splits"
        data-show-label="Show all splits" data-hide-label="Hide splits" aria-expanded="false">
        <span class="cb-workout-toggle-label">Show all splits</span>
        <svg class="cb-workout-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
      </button>
      <div class="cb-workout-steps" hidden>${rows}${omitted}</div>
    </div>`;
}

/** "4:26" (backend M:SS pace) → seconds. */
const paceSeconds = (pace) => {
  const [m, s] = String(pace).split(':').map(Number);
  return m * 60 + s;
};
const clock = (seconds) => {
  const t = Math.round(seconds);
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
};
const bpm = (n) => (n ? `${n} bpm` : '');
const watts = (n) => (n ? `${n} W` : '');

/* How a rep is read against each kind of band: its number, that number as
   the athlete reads it, what else the rep carries, and which way is quicker
   or harder along the rep's track. */
const REP_READS = {
  pace: {
    value: (r) => r.pace_min_km && paceSeconds(r.pace_min_km),
    say: (v) => `${clock(v)} /km`,
    other: (r) => bpm(r.avg_hr),
    ends: ['Slower', 'Faster'],
  },
  swim: {
    value: (r) => r.pace_min_100m && paceSeconds(r.pace_min_100m),
    say: (v) => `${clock(v)} /100m`,
    other: (r) => bpm(r.avg_hr),
    ends: ['Slower', 'Faster'],
  },
  hr: {
    value: (r) => r.avg_hr,
    say: (v) => bpm(Math.round(v)),
    other: (r) => perKm(r.pace_min_km) || per100m(r.pace_min_100m) || watts(r.avg_watts),
    ends: ['Lower', 'Higher'],
  },
  power: {
    value: (r) => r.avg_watts,
    say: (v) => watts(Math.round(v)),
    other: (r) => bpm(r.avg_hr),
    ends: ['Lower', 'Higher'],
  },
};

/** One second, beat or watt of rounding counts as inside the band. */
const BAND_GRACE = 1;
/* The plan's band sits in the middle 60 % of a rep's track. */
const TRACK_BAND_START = 20;
const TRACK_BAND_SHARE = 60;

/** The work reps against the plan's band: how many held it, where each sits on
    its track and their mean. Null unless intervals.icu found exactly the reps
    the plan asked for, each with the band's number: a guess at which lap was
    which rep would be no reading at all. */
export function repsAgainstBand(view, band) {
  const work = (view.intervals || []).filter((r) => r.type === 'WORK');
  if (!band || work.length !== band.reps) return null;
  const read = REP_READS[band.metric];
  const values = work.map(read.value);
  if (values.some((v) => !v)) return null;
  const { low, high } = band;
  const faster = read.ends[1] === 'Faster';
  const reps = work.map((rep, i) => {
    const share = (values[i] - low) / (high - low);
    const at = TRACK_BAND_START + TRACK_BAND_SHARE * (faster ? 1 - share : share);
    return {
      n: i + 1,
      main: read.say(values[i]),
      other: read.other(rep),
      at: Math.min(97, Math.max(3, at)).toFixed(1),
    };
  });
  return {
    held: values.filter((v) => v >= low - BAND_GRACE && v <= high + BAND_GRACE).length,
    count: work.length,
    mean: read.say(values.reduce((a, b) => a + b, 0) / values.length),
    reps,
    ends: read.ends,
  };
}

function repRows(reading) {
  const rows = reading.reps.map((r) => `
      <div class="cb-rep">
        <div class="cb-rep__line">
          <span class="cb-rep__n">Rep ${r.n}</span>
          <span class="cb-rep__main">${esc(r.main)}</span>
          <span class="cb-rep__n">${esc(r.other)}</span>
        </div>
        <div class="cb-rep__track"><i class="cb-rep__band"></i><i class="cb-rep__mark" style="left:${r.at}%"></i></div>
      </div>`).join('');
  return `<div class="cb-sheet-card">${rows}<div class="cb-rep__ends"><span>${reading.ends[0]}</span><span>${reading.ends[1]}</span></div></div>`;
}

/** The Intervals row and its page: the reps against the plan's band when they
    can be read so, else what intervals.icu grouped; the splits fold under it. */
function intervalsPart(view, sport, band, back) {
  const groups = view.groups || [];
  const splits = view.intervals || [];
  const recap = view.interval_summary || [];
  if (!groups.length && !splits.length) return null;
  const reading = repsAgainstBand(view, band);
  const note = view.note ? `<div class="cb-detail-note">${esc(view.note)}</div>` : '';
  const recapNote = !reading && recap.length ? `<div class="cb-detail-note">${esc(recap.join(' · '))}</div>` : '';
  const body = (reading ? repRows(reading) : recapNote + groupsSection(view, sport)) + splitsSection(view, sport) + note;
  let summary = recap.join(' · ') || `${splits.length} split${splits.length === 1 ? '' : 's'}`;
  if (reading) summary = `${reading.held} of ${reading.count} in the band · ${reading.mean}`;
  else if (groups.length) summary = [groupName(groups[0]), rowTarget(groups[0], sport)].filter(Boolean).join(' · ');
  return {
    row: sheetRow({ title: 'Intervals', summary, page: 'intervals' }),
    page: sheetPage({
      id: 'intervals',
      title: reading ? `${reading.held} of ${reading.count} in the band` : 'Intervals',
      sub: reading ? `Plan: ${band.plan}` : '',
      back,
      body,
    }),
  };
}

const TAKE_LABEL = { quick: 'Quick take', full: 'Full debrief' };

/* intervals.icu's feel scale, 1 (best) … 5 (worst). */
const FEEL_CHIPS = ['strong', 'good', 'normal', 'poor', 'weak'];
const RPE_CHIPS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

/** The How you felt row's answer. */
export function verdictSummary(feel, rpe) {
  const word = FEEL_CHIPS[feel - 1];
  const parts = [word ? word[0].toUpperCase() + word.slice(1) : null, rpe != null ? `Effort ${rpe}/10` : null];
  return parts.filter(Boolean).join(' · ') || 'Not answered yet';
}

/** The athlete's own verdict: a chip per feel word and per effort step, the
    saved ones selected. A tap writes it to intervals.icu (shared.js). */
export function renderVerdict({ activityId, feel = null, rpe = null }) {
  const chip = (field, value, label, on) => `<button type="button" class="cb-reschedule-btn${on ? ' cb-reschedule-btn--current' : ''}"
      data-action="activity-verdict" data-field="${field}" data-value="${value}" aria-pressed="${on}">${label}</button>`;
  return `
    <div class="cb-verdict" data-verdict-slot data-activity-id="${esc(activityId)}">
      <div class="cb-verdict__feel">${FEEL_CHIPS.map((w, i) => chip('feel', i + 1, w, feel === i + 1)).join('')}</div>
      <div class="cb-reschedule-label">How hard was it? 1 easy · 10 all-out</div>
      <div class="cb-verdict__effort">${RPE_CHIPS.map((n) => chip('icu_rpe', n, n, rpe === n)).join('')}</div>
      <div class="cb-detail-note">Saved to intervals.icu. A Garmin watch fills this in when you answer it on the device.</div>
    </div>`;
}

/** One saved take as a coach message; the full debrief folds once a quick
    take sits above it, unless it was just asked for. */
export function takeBlock(take, open, labels = TAKE_LABEL) {
  const meta = `AI · ${labels[take.depth]} · ${fmtDayLabel(take.created_at.slice(0, 10))}`;
  const text = `<div class="cb-msg__text">${formatBody(take.text)}</div>`;
  if (take.depth === 'quick') {
    return `<div class="cb-msg cb-msg--coach" data-ai-generated="true"><div class="cb-msg__meta">${esc(meta)}</div>${text}</div>`;
  }
  return `<details class="cb-msg cb-msg--coach" data-ai-generated="true"${open ? ' open' : ''}>`
    + `<summary class="cb-msg__meta">${esc(meta)}</summary>${text}</details>`;
}

/** The brief a finished session was given, folded above its Coach's take:
    the full one when both exist, else the quick one. */
export function renderBriefedFold(briefTakes) {
  const brief = briefTakes.find((t) => t.depth === 'full') || briefTakes[0];
  if (!brief) return '';
  return `<div class="cb-takes"><details class="cb-msg cb-msg--coach">`
    + `<summary class="cb-msg__meta">${esc(`Briefed · ${fmtDayLabel(brief.created_at.slice(0, 10))}`)}</summary>`
    + `<div class="cb-msg__text">${formatBody(brief.text)}</div></details></div>`;
}

/** The race an activity was, as the coach is told it: "Ridge Marathon (A race)". */
export const raceLabel = (race) => (race ? `${race.name} (${race.priority} race)` : '');

/* intervals.icu types recorded outdoors with a GPS track, the ones another
   session can share a route with; indoor types (VirtualRide …) have none. */
const ROUTE_TYPES = new Set(['Run', 'TrailRun', 'Ride', 'GravelRide', 'MountainBikeRide', 'Hike', 'Walk']);

/** Whether an activity of intervals.icu `type` can be compared on its route. */
export const isRoutable = (type) => ROUTE_TYPES.has(type);

/** The Coach's take row's answer: what the coach wrote, or what it can write. */
export function takeSummary(takes = [], pending = null) {
  if (pending) return `Writing your ${TAKE_LABEL[pending].toLowerCase()}…`;
  const take = takes.find((t) => t.depth === 'quick') || takes[0];
  return take ? previewText(take.text) : 'Ask for a quick take or a full debrief';
}

/** The "Coach's take" page of one activity: its saved takes, then a button
    per depth (Redo once that depth has a take), or the thinking line while
    one is asked for. Re-rendered in place by the shells, so the slot carries
    the activity it belongs to.
    `takes` — this activity's saved takes; `pending` — the depth in flight;
    `error` — why the last ask failed; `opened` — the depth just asked for;
    `proposed` — the reply came with a plan change to apply in the chat;
    `briefHandle` — the brief this session was given, which a full debrief is
    graded against; `race` — the race's label when the activity was one;
    `routable` — it has a GPS route, so it offers Compare on this route
    (shells, `compare-route`), which asks in the chat, where the charts are
    drawn. */
export function renderActivityTakes({
  activityId, name, date, takes = [], pending = null, error = '', opened = null, proposed = false,
  briefHandle = '', race = '', routable = false,
}) {
  const byDepth = Object.fromEntries(takes.map((t) => [t.depth, t]));
  const blocks = ['quick', 'full'].filter((d) => byDepth[d])
    .map((d) => takeBlock(byDepth[d], opened === 'full' || !byDepth.quick)).join('');
  const buttons = ['quick', 'full'].map((d) => `<button type="button" class="cb-btn-secondary"
      data-action="activity-take" data-depth="${d}">${byDepth[d] ? `Redo ${TAKE_LABEL[d].toLowerCase()}` : TAKE_LABEL[d]}</button>`).join('')
    + (routable ? `<button type="button" class="cb-btn-secondary cb-takes__compare"
      data-action="compare-route">Compare on this route</button>` : '');
  return `
    <div class="cb-takes" data-takes-slot data-activity-id="${esc(activityId)}"
      data-name="${esc(name)}" data-date="${esc(date)}"
      data-brief-handle="${esc(briefHandle)}" data-race="${esc(race)}" data-routable="${routable ? 1 : 0}">
      ${blocks}
      ${error ? `<div class="cb-detail-note">${esc(error)}</div>` : ''}
      ${proposed ? '<div class="cb-detail-note">Your coach suggested a plan change: apply it in the chat.</div>' : ''}
      ${pending ? renderThinking(`Writing your ${TAKE_LABEL[pending].toLowerCase()}…`) : `<div class="cb-takes__actions">${buttons}</div>`}
    </div>`;
}

/** The row every sheet ends on, in one order, so each button is where it
    was last time: Move for a session that can still move, Ask coach, then —
    for an activity on intervals.icu, never the demo's — the link to its page
    there. Ask coach opens the chat with a draft about the session (shells,
    `ask-coach`): what the coach thinks of a done one is the Coach's take
    page's job. */
export function renderDetailActions({ name, date, activityId = null, canMove = false }) {
  const link = activityId && !isDemo(loadSettings())
    ? `<a class="cb-btn-secondary" href="https://intervals.icu/activities/${esc(activityId)}"
          target="_blank" rel="noopener">intervals.icu</a>`
    : '';
  const move = canMove
    ? '<button type="button" class="cb-btn-secondary" data-action="sheet-open" data-page="move">Move</button>'
    : '';
  return `
      <footer class="cb-sheet__foot"><div class="cb-detail-actions">
        ${move}
        <button type="button" class="cb-btn-secondary" data-action="ask-coach"
          data-name="${esc(name)}" data-date="${esc(date || '')}">Ask coach</button>
        ${link}
      </div></footer>`;
}

/** "Long Trail Run · Sun, Nov 1 · 42min" — a planned session as the link row names it. */
function sessionLabel(s) {
  return [s.title, fmtDayLabel(s.date), s.duration_minutes ? fmtDur(s.duration_minutes * 60) : null]
    .filter(Boolean).join(' · ');
}

function linkButton(activityId, body, label, text, cls = 'cb-btn-secondary') {
  return `<button type="button" class="${cls}" data-action="pair-activity"
    data-activity-id="${esc(activityId)}" data-body="${esc(JSON.stringify(body))}"
    data-label="${esc(label)}">${esc(text)}</button>`;
}

/** One line on what this activity counts as in the plan, under the title.
    The session the watch ran (its name carries the workout's title) gets the
    one-tap link; every other open session of the sport within a week, the
    athlete's own ad-hoc workouts that day, and "not a planned session" wait
    behind a fold, so the sheet stays the activity's. A link moves the
    session onto this day when it was planned for another. */
function planLinkSection(view, adHoc) {
  const link = view.plan_link || { linked: null, candidates: [], looks_like: null };
  const id = view.activity_id;
  const looks = link.candidates.find((c) => c.session_id === link.looks_like);
  const options = [
    ...link.candidates.filter((c) => c !== looks).map((c) => linkButton(
      id, { session_id: c.session_id }, c.title, sessionLabel(c),
    )),
    ...adHoc.map((c) => linkButton(id, c.body, c.label, c.label)),
    ...(link.linked ? [`<button type="button" class="cb-btn-secondary" data-action="unlink-activity"
      data-activity-id="${esc(id)}">Not a planned session</button>`] : []),
  ];
  if (!link.linked && !looks && !options.length) return '';
  let line = 'Not linked to a planned session';
  if (link.linked) line = `Counts as ${sessionLabel(link.linked)}`;
  else if (looks) line = `Looks like ${sessionLabel(looks)}`;
  let fold = 'Link to a planned session';
  if (looks) fold = 'Other sessions';
  else if (link.linked) fold = 'Change';
  // Linked elsewhere, the button names the day too: the line shows the other one.
  const lookText = looks && link.linked
    ? `Link to ${looks.title} (${fmtDayLabel(looks.date)}) instead`
    : `Link to ${looks?.title}`;
  return `
      <div class="cb-link">
        <div class="cb-link-line">${esc(line)}</div>
        ${looks ? linkButton(id, { session_id: looks.session_id }, looks.title, lookText, 'cb-btn-primary') : ''}
        ${options.length ? `
        <button type="button" class="cb-workout-toggle cb-link-toggle" data-action="toggle-workout" aria-expanded="false"
          data-target=".cb-link-options" data-show-label="${esc(fold)}" data-hide-label="Hide sessions">
          <span class="cb-workout-toggle-label">${esc(fold)}</span>
          <svg class="cb-workout-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
        </button>
        <div class="cb-link-options" hidden>${options.join('')}</div>` : ''}
      </div>`;
}

/** The place a done session's sheet keeps for its debrief rows, filled once
    the sheet is open (shells/shared.js fillActivityDebrief): the debrief
    reads the activity from intervals.icu, and the sheet must not wait on it.
    It carries the band the plan set the reps, which the debrief reads them
    against. */
export function renderDebriefSlot(band = null) {
  const carried = band ? ` data-band="${esc(JSON.stringify(band))}"` : '';
  return `<div class="cb-sheet-row cb-sheet-row--plain" data-debrief-slot${carried}><span class="cb-sheet-row__answer">Reading the session…</span></div>`;
}

/** The debrief of one payload, for a done session's sheet: {lead, rows, pages}.
    `lead` sits under the hero: the race it was (with the brief it was given,
    `briefTakes`), or the session the watch ran, one tap from being linked.
    `rows` are its Intervals, Time in zones and Counts as rows; `pages` what
    they open. `band` is what the plan asked of the reps; `pairable` are
    that day's ad-hoc workouts the activity could pair with; `back` names the
    way back from a page. */
export function renderActivityDebrief(view, {
  pairable = [], briefTakes = [], band = null, back = 'Back',
} = {}) {
  const sport = normSport(view.sport);
  const race = view.race
    ? `<div class="cb-detail-note">${esc(raceLabel(view.race))}${view.elapsed_time ? ` · finished in ${esc(view.elapsed_time)}` : ''}</div>
      ${renderBriefedFold(briefTakes)}`
    : '';
  const link = view.race ? '' : planLinkSection(view, pairable);
  const { linked = null, looks_like: looksLike = null, candidates = [] } = view.plan_link || {};
  // Only the session the watch ran asks for a tap; every other link is a row.
  const quiet = !candidates.some((c) => c.session_id === looksLike);
  const counts = link && quiet ? {
    row: sheetRow({
      title: 'Counts as', summary: linked ? sessionLabel(linked) : 'Not linked to a planned session', page: 'counts',
    }),
    page: sheetPage({ id: 'counts', title: 'Counts as', back, body: link }),
  } : null;
  const parts = [intervalsPart(view, sport, band, back), zonePart(view, back), counts].filter(Boolean);
  return {
    lead: race || (quiet ? '' : link),
    rows: parts.map((p) => p.row).join(''),
    pages: parts.map((p) => p.page).join(''),
  };
}
