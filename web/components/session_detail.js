/* Endury — session sheet: the overview, which says the session and answers
   each question in one line, and the pages behind its rows (sheet_pages.js).
   The sheet (mobile, bottom-anchored) and modal (web, centered) chrome is
   added by the shell via modal.js. */

import { esc, sportColor, TSS_HINT } from './util.js';
import { renderOutline } from './workout_outline.js';
import { renderTraceSlot } from './activity_trace.js';
import {
  isRoutable, renderActivityTakes, renderBriefedFold, renderDebriefSlot, renderDetailActions,
  renderVerdict, takeSummary, verdictSummary,
} from './activity_debrief.js';
import { briefSummary, renderBriefSlot, renderCoachBrief } from './session_brief.js';
import { badgeMeta } from './session_card.js';
import { sheetHead, sheetPage, sheetRow } from './sheet_pages.js';
import { icons } from './icons.js';
import { hintButton } from './tss_explainer.js';
import { fmtDayLabel, fmtDur } from '../data/format.js';

/** intervals.icu's own sport wording, for the post-test settings path. */
const TEST_SPORT_LABEL = { run: 'Running', bike: 'Cycling', swim: 'Swimming' };

const REST_STEPS = new Set(['recovery', 'rest']);
const ENDS = new Set(['warmup', 'cooldown']);

/** Flattened workout steps (incl. repeat groups) → row HTML.

    `hint` carries the single zone-explainer "?" across the recursion: it
    lands on the first step target rendered, wherever that turns out to be. */
function stepRows(steps, indent = false, hint = { pending: true }) {
  return steps.map((step) => {
    if (step.type === 'repeat') {
      if (!step.reps || step.reps <= 1) return stepRows(step.children, indent, hint);
      return `<div class="cb-workout-repeat-head">Repeat &times;${esc(step.reps)}</div>${stepRows(step.children, true, hint)}`;
    }
    const barStyle = `background:var(--cb-step-${esc(step.type)}, var(--cb-step-rest))`;
    let tip = '';
    if (hint.pending && step.target) {
      hint.pending = false;
      tip = hintButton('zones', 'What do the training zones mean?');
    }
    // "Easy warm-up — choice of stroke": the step, then how to swim it.
    const [name, ...how] = step.name.split(' — ');
    return `
      <div class="cb-workout-row${indent ? ' cb-workout-row--child' : ''}">
        <span class="cb-workout-bar" style="${barStyle}"></span>
        <div class="cb-workout-info">
          <div class="cb-workout-name">${esc(name)}</div>
          ${how.length ? `<div class="cb-workout-target">${esc(how.join(' — '))}</div>` : ''}
          ${step.target ? `<div class="cb-workout-target">${esc(step.target)}${tip}</div>` : ''}
        </div>
        <span class="cb-workout-dur">${esc(step.dur)}</span>
      </div>`;
  }).join('');
}

function statsGrid(stats, className) {
  // toSession()/actualStatsFor() put the plan-comparison value on `.plan`
  // (see data/adapters.js); the pre-formatted `sub` string is built here
  // instead of expected on the model.
  const items = stats.map((s) => `
    <div>
      <div class="cb-stat-label"${s.l === 'Load' ? ` title="${esc(TSS_HINT)}"` : ''}>${esc(s.l)}</div>
      <div class="cb-stat-value">${esc(s.v)}</div>
      ${s.plan ? `<div class="cb-stat-sub">plan ${esc(s.plan)}</div>` : ''}
    </div>`).join('');
  return `<div class="${className}">${items}</div>`;
}

/** "1:02 of 1:00 · 23 hard min of 20" — what was done against what the plan
    asked. Parts the activity does not carry are left out. */
export function executionLine(ex) {
  if (!ex) return '';
  if (ex.withheld) return 'Recorded, details not shared by intervals.icu';
  const parts = [ex.plannedSec ? `${fmtDur(ex.durationSec)} of ${fmtDur(ex.plannedSec)}` : fmtDur(ex.durationSec)];
  // An easy session with no hard time asked for or done has nothing to say here.
  if (ex.hard && (ex.hard.plannedMin || ex.hard.actualMin)) {
    parts.push(`${ex.hard.actualMin} hard min of ${ex.hard.plannedMin}`);
  }
  return parts.join(' · ');
}

/** The hero's one verdict on a done session: as planned only when its length
    and, where measured, its hard minutes both held. */
export function doneVerdict(ex) {
  if (!ex?.plannedSec || ex.durationSec == null) return { words: 'Done', asPlanned: false };
  if (ex.durationPct < 90) return { words: 'Done, shorter than planned', asPlanned: false };
  if (ex.durationPct > 115) return { words: 'Done, longer than planned', asPlanned: false };
  if (ex.hard && ex.hard.actualMin < ex.hard.plannedMin * 0.8) {
    return { words: 'Done, less hard time than planned', asPlanned: false };
  }
  return { words: 'Done as planned', asPlanned: true };
}

const LOAD_HINT = 'intervals.icu scores load against the thresholds in your settings; '
  + 'a stale threshold moves this number, not your effort.';

const withTarget = (step) => [step.short, step.target].filter(Boolean).join(' · ');

/** A repeat block in the hero's words; `sets` of the same block read as one. */
function repeatHero(block, sets) {
  const work = block.children.filter((c) => !REST_STEPS.has(c.type));
  const rest = block.children.find((c) => REST_STEPS.has(c.type));
  const [one] = work;
  if (work.length !== 1) return { big: `${block.reps} ×`, lines: work.map(withTarget) };
  const reps = `${block.reps} × ${one.short}`;
  // "Wall rest between reps — stay on pace clock": the first words say it.
  const restWords = rest?.name.split(/\s[—–-]\s|[:,]/)[0].toLowerCase();
  const between = restWords?.includes('between') ? '' : ' between reps';
  return {
    big: sets > 1 ? `${sets} × (${reps})` : reps,
    lines: [one.target].filter(Boolean),
    sub: rest ? [`${rest.short} ${restWords}${between}`] : [],
  };
}

/** The workout's main set as the hero says it: {big, lines, sub}, or null
    without steps. The longest repeat block leads, and the same block again
    counts as sets; without one, the longest step that is not a warm-up or a
    cool-down, with the others listed under it. */
export function mainSet(steps = []) {
  const flat = steps.flatMap((s) => (s.type === 'repeat' && s.reps <= 1 ? s.children : [s]));
  if (!flat.length) return null;
  const longest = (list) => list.reduce((a, b) => (b.sec > a.sec ? b : a));
  const blocks = flat.filter((s) => s.type === 'repeat');
  if (blocks.length) {
    const main = longest(blocks);
    const shape = (b) => JSON.stringify(b.children.map(withTarget)) + b.reps;
    const sets = blocks.filter((b) => shape(b) === shape(main)).length;
    const more = blocks.length - sets;
    const { big, lines, sub = [] } = repeatHero(main, sets);
    return { big, lines, sub: more ? [...sub, `plus ${more} more block${more === 1 ? '' : 's'}`] : sub };
  }
  const steady = flat.filter((s) => !ENDS.has(s.type));
  const main = longest(steady.length ? steady : flat);
  const others = steady.filter((s) => s !== main).slice(0, 2).map((s) => `plus ${withTarget(s)}`);
  return { big: main.short, lines: [main.target].filter(Boolean), sub: others };
}

/** The hero of a session still to come: the set and its shape; the steps
    under it say how it starts and ends. Without steps it says the session's
    length and intensity. */
function planHero(session) {
  const stat = (label) => session.stats?.find((s) => s.l === label)?.v;
  const set = mainSet(session.workout?.steps) || {
    big: stat('Duration'),
    lines: [stat('Intensity')].filter((v) => v && v !== '—'),
    sub: [],
  };
  if (!set.big) return '';
  const outline = session.outline?.length
    ? renderOutline(session.outline, { height: 36, sport: session.sport }) : '';
  return `
    <div class="cb-sheet-hero">
      <div class="cb-sheet-hero__big">${esc(set.big)}</div>
      ${set.lines.map((l) => `<div class="cb-sheet-hero__line">${esc(l)}</div>`).join('')}
      ${(set.sub || []).map((l) => `<div class="cb-sheet-hero__sub">${esc(l)}</div>`).join('')}
      ${outline}
    </div>`;
}

/** The hero of a done session: the verdict, what was done against the plan
    and the slot the shell fills with its trace (shared.js fillActivityTrace). */
function doneHero(session, activityId) {
  const ex = session.actual.execution;
  const { words, asPlanned } = doneVerdict(ex);
  const line = executionLine(ex);
  return `
    <div class="cb-sheet-hero">
      <div class="cb-sheet-hero__verdict">${asPlanned ? `<span class="cb-sheet-hero__check">${icons.check(24)}</span>` : ''}${esc(words)}</div>
      ${line ? `<div class="cb-sheet-hero__line" data-execution>${esc(line)}</div>` : ''}
      ${activityId ? renderTraceSlot() : ''}
    </div>`;
}

/** The facts line under the title: the date, then the session's numbers. */
function metaLine(session, isDone) {
  const stats = (isDone ? session.actual.stats.filter((s) => s.l !== 'Duration') : session.stats || [])
    .filter((s) => s.l !== 'Intensity' && s.v && s.v !== '—')
    .map((s) => (s.l === 'Load'
      ? `<span title="${esc(TSS_HINT)}">Load ${esc(s.v)}</span>` : esc(s.v)));
  return [
    session.date ? esc(fmtDayLabel(session.date)) : null,
    ...stats,
    session.actual?.garmin ? esc(session.actual.garmin) : null,
  ].filter(Boolean).join(' · ');
}

/** How a workout runs, as an "All steps and cues" row says it. */
function stepsSummary(session) {
  const steps = session.workout?.steps || [];
  const names = steps.flatMap((s) => {
    if (s.type === 'warmup') return ['Warm-up'];
    if (s.type === 'cooldown') return ['cool-down'];
    if (s.type === 'repeat') return s.reps > 1 ? [`${s.reps} reps`] : [];
    return s.dur ? [s.dur] : [];
  });
  const cues = session.cues?.length || 0;
  if (cues) names.push(`${cues} cue${cues === 1 ? '' : 's'}`);
  if (names.length) return names.join(', ');
  return steps.length ? `${steps.length} step${steps.length === 1 ? '' : 's'}` : '';
}

const whyBody = (why) => why.map((p) => `<p class="cb-why-para">${esc(p)}</p>`).join('');

/** The workout in full: every step, the session's idea and its cues. A
    session to come shows it on the overview, where the athlete follows it.
    Cues are server-authored HTML with <b> labels — compile-time app
    constants, not user input, so rendered without esc() (see
    endury/planner/activity/steps.py). */
function stepsBody(session) {
  const steps = session.workout?.steps || [];
  return [
    steps.length ? `<div class="cb-sheet-card">${stepRows(steps)}</div>` : '',
    session.note ? `<div><div class="cb-reschedule-label">The idea</div><p class="cb-why-para">${esc(session.note)}</p></div>` : '',
    session.cues?.length ? `<div><div class="cb-reschedule-label">Cues</div><div class="cb-detail-cues">${session.cues.map((c) => `<div class="cb-detail-cue">${c}</div>`).join('')}</div></div>` : '',
  ].filter(Boolean).join('');
}

/** A done session's steps behind a row, its reason for being here folded in. */
function stepsPage(session, back) {
  const body = stepsBody(session)
    + (session.why?.length ? `<div><div class="cb-reschedule-label">Why this session</div>${whyBody(session.why)}</div>` : '');
  return body ? sheetPage({ id: 'steps', title: 'All steps and cues', back, body }) : '';
}

/** Every number the activity carries, and the load against plan. */
function numbersPage(actual, back) {
  const stats = [...actual.stats, ...actual.recorded].filter((s) => s.v && s.v !== '—');
  if (!stats.length && actual.compliance == null) return '';
  const load = actual.compliance != null
    ? `<div class="cb-detail-note" title="${esc(LOAD_HINT)}">Load ${esc(actual.compliance)} % of planned</div>` : '';
  return sheetPage({
    id: 'numbers', title: 'All the numbers', back, body: statsGrid(stats, 'cb-detail-stats') + load,
  });
}

/** Three recorded numbers for the row, heart rate first and without its label. */
function numbersSummary(actual) {
  const recorded = actual.recorded.filter((s) => s.l !== 'Max HR').slice(0, 3)
    .map((s) => (s.l === 'Avg HR' ? s.v : `${s.l} ${s.v}`));
  if (recorded.length) return recorded.join(' · ');
  return actual.compliance != null ? `Load ${actual.compliance} % of planned` : '';
}

function movePage(moveDays, back) {
  const btns = moveDays.map((d) => `
    <button type="button" class="cb-reschedule-btn${d.current ? ' cb-reschedule-btn--current' : ''}"
      data-action="move-day" data-date="${esc(d.date)}"${d.disabled ? ' disabled' : ''}>${esc(d.dow)}</button>`).join('');
  return sheetPage({
    id: 'move', title: 'Move to another day', back, body: `<div class="cb-reschedule-row">${btns}</div>`,
  });
}

/** Render the session sheet for one Session. A completed session's activity
    gets its take and verdict pages, fed by `takes` and `pending`, and the
    slot its debrief fills. A session still to come gets the brief's slot and
    the Coach's brief, fed by `briefTakes` and `briefPending`; a done one folds
    the brief it was given above its take. */
export function renderSessionDetail(session, {
  moveDays = [], takes = [], pending = null, briefTakes = [], briefPending = null,
} = {}) {
  const isDone = Boolean(session.done && session.actual);
  const bm = badgeMeta(session);

  // Ad-hoc workouts live only on intervals.icu (no local plan row), so they
  // get a delete affordance plan sessions don't have — plan sessions are
  // skipped/swapped/replanned, never hard-deleted from the detail sheet.
  const trashBtn = session.status === 'adhoc'
    ? `<button type="button" class="cb-ib cb-sheet-ib" data-action="delete-adhoc" aria-label="Remove from calendar"
          data-external-id="${esc(session.externalId || '')}">${icons.trash(16)}</button>`
    : '';

  // A finished test only pays off once the athlete saves the new number:
  // the app stores no thresholds, it writes them to intervals.icu and reads
  // them back live.
  const testNote = isDone && session.kind === 'test'
    ? `<div class="cb-detail-note">Test done — set your new threshold in Planning Inputs
        (${esc(TEST_SPORT_LABEL[session.sport] || 'your sport')} → Edit)
        so the plan's targets pick it up.</div>`
    : '';

  // A completed session with no matched activity yet gets no debrief.
  const activityId = isDone && session.activityId ? String(session.activityId) : null;
  const name = session.title.replace(/^[^·]*·\s*/, '');
  const back = session.title;
  // Only a stored plan session (a numeric id) can be briefed.
  const handle = `session:${session.id}`;
  const stored = /^\d+$/.test(String(session.id));
  const upcoming = !isDone && !session.skipped && !session.missed && stored;
  // A plain planned or done session says so in its hero; a badge is news.
  const badge = !isDone && bm.text !== 'PLANNED'
    ? `<span class="cb-badge cb-badge--agenda ${bm.cls}">${esc(bm.text)}</span>` : '';
  const feel = session.actual?.execution?.feel ?? null;
  const rpe = session.actual?.execution?.rpe ?? null;
  const numbers = isDone ? numbersPage(session.actual, back) : '';
  const steps = isDone ? stepsPage(session, back) : '';
  const canMove = !isDone && moveDays.length > 0;

  const rows = [
    upcoming ? renderBriefSlot(handle) : '',
    activityId ? sheetRow({
      title: "Coach's take", summary: takeSummary(takes, pending), page: 'take', key: 'take', lines: 2,
    }) : '',
    activityId ? sheetRow({
      title: 'How you felt', summary: verdictSummary(feel, rpe), page: 'verdict', key: 'verdict',
    }) : '',
    activityId ? renderDebriefSlot(session.workBand) : '',
    numbers ? sheetRow({ title: 'All the numbers', summary: numbersSummary(session.actual), page: 'numbers' }) : '',
    !isDone && session.why?.length ? sheetRow({ title: 'Why this session', summary: session.why[0], page: 'why' }) : '',
    upcoming ? sheetRow({
      title: "Coach's brief", summary: briefSummary(briefTakes, briefPending), page: 'coach', key: 'brief',
    }) : '',
    steps ? sheetRow({ title: 'All steps and cues', summary: stepsSummary(session), page: 'steps' }) : '',
  ].filter(Boolean).join('');

  return `
    <div class="cb-sheet cb-detail">
      <section class="cb-sheet__page" data-sheet-page="overview">
        ${sheetHead({
    color: sportColor(session.sport), title: session.title, meta: metaLine(session, isDone), badge, extra: trashBtn,
  })}
        <div class="cb-sheet__body">
          ${isDone ? doneHero(session, activityId) : planHero(session) + stepsBody(session)}
          <div data-sheet-lead></div>
          ${testNote}
          ${isDone ? renderBriefedFold(briefTakes) : ''}
          ${rows ? `<div class="cb-sheet-list">${rows}</div>` : ''}
          <div class="cb-sheet__foot-note" data-sheet-note></div>
        </div>
        ${renderDetailActions({ name, date: session.date, activityId, canMove })}
      </section>
      ${!isDone && session.why?.length ? sheetPage({ id: 'why', title: 'Why this session', back, body: whyBody(session.why) }) : ''}
      ${steps}
      ${numbers}
      ${canMove ? movePage(moveDays, back) : ''}
      ${activityId ? sheetPage({
    id: 'take',
    title: "Coach's take",
    back,
    body: renderActivityTakes({
      activityId,
      name,
      date: session.date,
      takes,
      pending,
      briefHandle: stored ? handle : '',
      routable: isRoutable(session.actual?.type),
    }),
  }) : ''}
      ${activityId ? sheetPage({
    id: 'verdict', title: 'How you felt', back, body: renderVerdict({ activityId, feel, rpe }),
  }) : ''}
      ${upcoming ? sheetPage({
    id: 'coach',
    title: "Coach's brief",
    back,
    body: renderCoachBrief({
      handle, name, date: session.date, takes: briefTakes, pending: briefPending,
    }),
  }) : ''}
      <div data-sheet-pages></div>
    </div>`;
}
