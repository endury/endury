/* Node smoke test for the /v2 planned-workout display path.

   pytest does not cover the static JavaScript, so this is the regression
   net for the planner workout → display adapter layer. It feeds REAL
   materialized session payloads (serialize_planned_session shape, see
   static_adapters_fixtures.json — regenerate via
   scratchpad materialize_sessions dump) through the actual adapter +
   renderer modules and asserts the repeat/interval structure survives.

   Run:  node devtools/frontend/static_adapters_smoke.mjs

   Guards the schema contract: repeat blocks carry `iterations` + `steps`
   (endury/training/workout.py), NOT `repeat_count`/`reps`, and the interval
   work step is nested INSIDE the repeat, not at the top level. */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const ADAPTERS = join(HERE, '../../web/data/adapters.js');
const DETAIL = join(HERE, '../../web/components/session_detail.js');
const FIXTURES = join(HERE, 'static_adapters_fixtures.json');

const { toSession, buildWeek } = await import(ADAPTERS);
const { renderSessionDetail, mainSet, doneVerdict } = await import(DETAIL);

const fixtures = JSON.parse(readFileSync(FIXTURES, 'utf8'));

let failures = 0;
const fail = (name, msg) => { failures += 1; console.error(`  ✗ ${name}: ${msg}`); };

// --- independent oracle (recomputes truth from the raw wire payload) -------

/** First `interval` step at any depth, else first leaf step. */
function oracleMainStep(steps) {
  for (const s of steps || []) {
    if (s.type === 'interval') return s;
    if (s.type === 'repeat' && s.steps) {
      const inner = oracleMainStep(s.steps);
      if (inner) return inner;
    }
  }
  for (const s of steps || []) {
    if (s.type === 'repeat' && s.steps) return oracleMainStep(s.steps);
    return s;
  }
  return null;
}

/** Expected "Intensity" chip for an HR/power/pace-zone primary target. */
function oracleIntensity(step) {
  const t = step && step.target;
  if (!t) return null;
  if (/_zone$/.test(t.target_type) && t.zone != null) return `Z${t.zone}`;
  return null;
}

/** Count leaf segments after expanding repeats N times (cap 8, like the UI). */
function oracleOutlineIntervalSegs(steps) {
  let n = 0;
  const walk = (list) => {
    for (const s of list) {
      if (s.type === 'repeat' && s.steps) {
        const reps = Math.min(s.iterations || 1, 8);
        for (let i = 0; i < reps; i += 1) walk(s.steps);
      } else if (s.type === 'interval') {
        n += 1;
      }
    }
  };
  walk(steps || []);
  return n;
}

// --- assertions ------------------------------------------------------------

console.log(`static adapters smoke — ${fixtures.length} real repeat-bearing sessions\n`);

for (const fx of fixtures) {
  const name = `${fx.sport}/${fx.activity_key}`;
  const rawSteps = fx.workout.steps;
  const rawRepeat = rawSteps.find((s) => s.type === 'repeat');
  if (!rawRepeat) { fail(name, 'fixture has no top-level repeat block'); continue; }

  const session = toSession(fx, {});

  // 1. Repeat rep count survives (schema field is `iterations`).
  const dispRepeat = (session.workout.steps || []).find((s) => s.type === 'repeat');
  if (!dispRepeat) { fail(name, 'display workout dropped the repeat block'); continue; }
  if (dispRepeat.reps !== rawRepeat.iterations) {
    fail(name, `repeat reps = ${dispRepeat.reps}, expected ${rawRepeat.iterations} (iterations field)`);
  }

  // 2. Children render ONCE (a rep header, not N duplicated copies).
  if (dispRepeat.children.length !== rawRepeat.steps.length) {
    fail(name, `repeat children = ${dispRepeat.children.length}, expected ${rawRepeat.steps.length} (rendered once)`);
  }

  // 3. Detail modal shows the true "Repeat ×N" header + a child row.
  const html = renderSessionDetail(session);
  if (rawRepeat.iterations > 1 && !html.includes(`Repeat &times;${rawRepeat.iterations}`)) {
    fail(name, `detail HTML missing "Repeat ×${rawRepeat.iterations}" header`);
  }
  // A row names the step and sets what follows " — " (how to do it) on its own line.
  const childDesc = rawRepeat.steps.find((c) => c.description)?.description.split(' — ')[0];
  if (childDesc && !html.includes(childDesc.slice(0, 12))) {
    fail(name, 'detail HTML missing repeat child row');
  }

  // 4. "Intensity" stat comes from the nested interval, not the warmup.
  const mainStep = oracleMainStep(rawSteps);
  const wantIntensity = oracleIntensity(mainStep);
  const gotIntensity = session.stats.find((s) => s.l === 'Intensity')?.v;
  if (wantIntensity && gotIntensity !== wantIntensity) {
    fail(name, `Intensity = ${gotIntensity}, expected ${wantIntensity} (from nested interval)`);
  }

  // 5. Outline expands the repeat: interval segments = min(iterations,8) × #interval children.
  const wantSegs = oracleOutlineIntervalSegs(rawSteps);
  const outline = session.outline;
  const gotSegs = outline.filter((o) => o.type === 'interval').length;
  if (gotSegs !== wantSegs) {
    fail(name, `outline interval segments = ${gotSegs}, expected ${wantSegs}`);
  }
  const fracSum = outline.reduce((s, o) => s + o.frac, 0);
  if (outline.length && Math.abs(fracSum - 1) > 0.02) {
    fail(name, `outline fracs sum to ${fracSum.toFixed(3)}, expected ~1.0`);
  }

  console.log(`  · ${name.padEnd(28)} reps=${rawRepeat.iterations} intensity=${gotIntensity} outlineSegs=${gotSegs}`);
}

// 6. An optional session (R-SESS-037) stays out of the week's planned load
//    and never becomes its key session.
{
  const row = (id, tss, optional) => ({
    id, date: '2026-03-02', sport: 'running', family: 'easy', target_tss: tss,
    duration_minutes: 30, status: 'pending', optional,
  });
  const week = buildWeek(
    { plans: [row(1, 40, false), row(2, 60, true)] },
    '2026-03-02', ['2026-03-02'], {},
  );
  if (week.plannedTss !== 40) fail('optional', `plannedTss = ${week.plannedTss}, expected 40`);
  const anchor = week.sessions.find((x) => x.anchor);
  if (!anchor || anchor.optional) fail('optional', 'the optional session took the anchor');
  console.log(`  · optional session            plannedTss=${week.plannedTss}`);
}

// 7. A zone target without a number (its band crosses the athlete's own
//    intervals.icu zones) shows the band alone — never "Zundefined".
{
  const step = (type, target, secondary) => ({
    type, duration_seconds: 600, duration: { duration_type: 'time', value_seconds: 600 },
    target, secondary_target: secondary,
  });
  const row = {
    slot_id: 'z', date: '2026-03-02', sport: 'running', family: 'easy',
    target_tss: 30, duration_minutes: 30,
    workout: {
      name: 'Easy Run',
      steps: [
        step('warmup', { target_type: 'heart_rate_zone', high_bpm: 143 }, { target_type: 'rpe', rpe: 2 }),
        step('interval', { target_type: 'heart_rate_zone', low_bpm: 118, high_bpm: 134 }, { target_type: 'rpe', rpe: 3 }),
        step('interval', { target_type: 'swim_pace_zone', low_sec_per_100m: 118, high_sec_per_100m: 125 }),
        step('interval', { target_type: 'power_zone', low_watts: 232, high_watts: 246 }),
        step('cooldown', { target_type: 'heart_rate_zone' }, { target_type: 'rpe', rpe: 2 }),
      ],
    },
  };
  const session = toSession(row, {});
  const targets = session.workout.steps.map((x) => x.target);
  const want = ['≤143 bpm', '118–134 bpm', '1:58–2:05 /100m', '232–246 W', 'RPE 2'];
  if (JSON.stringify(targets) !== JSON.stringify(want)) {
    fail('no zone number', `step targets = ${JSON.stringify(targets)}, expected ${JSON.stringify(want)}`);
  }
  const intensity = session.stats.find((x) => x.l === 'Intensity')?.v;
  if (intensity !== '118–134 bpm') fail('no zone number', `Intensity = ${intensity}, expected the bpm band`);
  const html = renderSessionDetail(session);
  if (/undefined|null/.test(html)) fail('no zone number', 'detail HTML prints "undefined" or "null"');
  console.log(`  · zone-less targets           intensity=${intensity}`);
}

// 8. A done session shows what was done against what the plan asked: hard
//    minutes only when every step names a zone of one metric and the activity
//    has that metric's time in zones; the badge drops the load percentage.
{
  const { badgeMeta } = await import(join(HERE, '../../web/components/session_card.js'));
  const step = (type, sec, zone) => ({
    type, duration_seconds: sec, target: { target_type: 'heart_rate_zone', zone },
  });
  const row = (steps) => ({
    id: 8, date: '2026-03-02', sport: 'running', family: 'threshold', target_tss: 80,
    duration_minutes: 60, status: 'completed', matched_activity_id: 'a1',
    workout: { name: 'Threshold', steps },
  });
  const steps = [
    step('warmup', 1200, 2),
    { type: 'repeat', iterations: 2, steps: [step('interval', 600, 4), step('recovery', 300, 1)] },
    step('cooldown', 900, 1),
  ];
  const act = {
    id: 'a1', sport: 'running', duration: 3720, tss: 57, feel: 2, rpe: 7,
    source: 'GARMIN_CONNECT', hr_zone_times: [600, 1500, 900, 500, 120, 0, 0],
  };
  const ex = toSession(row(steps), { a1: act }).actual.execution;
  const want = { durationSec: 3720, plannedSec: 3600, durationPct: 103, feel: 2, feelWord: 'good', rpe: 7, withheld: false, hard: { plannedMin: 20, actualMin: 25 } };
  if (JSON.stringify(ex) !== JSON.stringify(want)) fail('execution', `got ${JSON.stringify(ex)}`);
  const session = toSession(row(steps), { a1: act });
  const html = renderSessionDetail(session);
  // The hero says length and hard minutes alone; feel and effort are the How you felt row's.
  if (!html.includes('data-execution>1:02 of 1h · 25 hard min of 20<')) fail('execution', 'the hero line is not length and hard minutes alone');
  if (!html.includes('data-summary="verdict">Good · Effort 7/10<')) fail('execution', 'the How you felt row lacks the watch\'s verdict');
  if (!html.includes('Load 71 % of planned')) fail('execution', 'load line missing from the numbers page');
  const upload = toSession(row(steps), { a1: { ...act, source: 'UPLOAD', device_name: 'Garmin Forerunner 965' } });
  if (upload.actual.garmin !== 'Garmin Forerunner 965') fail('execution', 'an uploaded Garmin file is not credited');
  if (toSession(row(steps), { a1: { ...act, source: 'WAHOO', device_name: 'ELEMNT' } }).actual.garmin) fail('execution', 'another brand credited to Garmin');
  const stub = toSession(row(steps), { a1: { id: 'a1', sport: 'other', source: 'STRAVA' } });
  if (!renderSessionDetail(stub).includes('details not shared by intervals.icu')) fail('execution', 'a withheld activity reads as nothing');
  if (badgeMeta(session).text !== 'Done') fail('execution', `badge = ${badgeMeta(session).text}`);
  const short = toSession(row(steps), { a1: { ...act, duration: 2880 } });
  const sb = badgeMeta(short);
  if (sb.text !== 'Done \u00b7 48 min of 60' || sb.cls !== 'cb-badge--muted') fail('execution', `short badge = ${JSON.stringify(sb)}`);
  const noZones = toSession(row(steps), { a1: { ...act, hr_zone_times: null } }).actual.execution;
  const noNumber = toSession(row([step('warmup', 600, null)]), { a1: act }).actual.execution;
  if (noZones.hard !== null || noNumber.hard !== null) fail('execution', 'hard must be null without zone times or a zone number');
  const bare = toSession(row(steps), { a1: { id: 'a1', sport: 'running', duration: 3600 } }).actual.execution;
  if (bare.feelWord !== null || bare.rpe !== null || bare.fromGarmin) fail('execution', 'missing keys must read as absent');
  console.log(`  · execution                   hard=${ex.hard.actualMin}/${ex.hard.plannedMin} badge=${badgeMeta(session).text}`);
}

// 8a. The Recorded grid: max heart rate everywhere; GAP on a run; decoupling
//     and cadence on a run or ride, a run's per-leg cadence counted as steps
//     per minute; none of them on a swim.
{
  const recorded = (sport, extra) => toSession({
    id: 81, date: '2026-03-02', sport, target_tss: 50, duration_minutes: 60,
    status: 'completed', matched_activity_id: 'a1',
  }, { a1: { id: 'a1', sport, duration: 3600, average_hr: 150, max_hr: 171, decoupling_pct: 3.24, average_cadence: 86.4, ...extra } })
    .actual.recorded.map((s) => `${s.l} ${s.v}`).join(' | ');
  const run = recorded('running', { gap_mps: 3.4 });
  if (run !== 'Avg HR 150 bpm | Max HR 171 bpm | GAP 4:54 /km | Decoupling 3.2 % | Cadence 173 spm') fail('recorded', `run = ${run}`);
  const ride = recorded('cycling', { normalized_power: 210 });
  if (ride !== 'Avg HR 150 bpm | Max HR 171 bpm | Weighted power 210 W | Decoupling 3.2 % | Cadence 86 rpm') fail('recorded', `ride = ${ride}`);
  const swim = recorded('swimming', { gap_mps: 1.2 });
  if (swim !== 'Avg HR 150 bpm | Max HR 171 bpm') fail('recorded', `swim = ${swim}`);
  console.log(`  · recorded                    ${run}`);
}

// 8b. The hero's one verdict on a done session (doneVerdict): as planned only
//     when its length and, where measured, its hard minutes both held; the
//     length speaks first; a session with no plan is just done.
{
  const ex = (pct, hard = null) => ({ durationSec: 36 * pct, plannedSec: 3600, durationPct: pct, hard });
  const hard = (actualMin) => ({ plannedMin: 20, actualMin });
  const cases = [
    ['as planned', ex(103, hard(25)), 'Done as planned', true],
    ['90 % of the length', ex(90), 'Done as planned', true],
    ['115 % of the length', ex(115), 'Done as planned', true],
    ['under 90 %', ex(89), 'Done, shorter than planned', false],
    ['over 115 %', ex(116), 'Done, longer than planned', false],
    ['80 % of the hard minutes', ex(100, hard(16)), 'Done as planned', true],
    ['under 80 % of the hard minutes', ex(100, hard(15)), 'Done, less hard time than planned', false],
    ['hard minutes not measured', ex(100), 'Done as planned', true],
    ['shorter and softer', ex(80, hard(5)), 'Done, shorter than planned', false],
    ['no plan', { durationSec: 3000, plannedSec: null, durationPct: null, hard: null }, 'Done', false],
    ['no length', { durationSec: null, plannedSec: 3600, durationPct: null, hard: null }, 'Done', false],
    ['no execution', null, 'Done', false],
  ];
  for (const [what, e, words, asPlanned] of cases) {
    const got = doneVerdict(e);
    if (got.words !== words || got.asPlanned !== asPlanned) fail('done verdict', `${what}: ${JSON.stringify(got)}`);
  }
  // On the sheet: the verdict heads the hero and only "as planned" gets the check.
  const heroOf = (html) => (html.match(/cb-sheet-hero__verdict">(?:<span class="cb-sheet-hero__check">[\s\S]*?<\/span>)?([^<]*)</) || [])[1];
  const tempo = { type: 'interval', duration_seconds: 3600, target: { target_type: 'heart_rate_zone', zone: 3 } };
  const planned = (status = 'completed') => ({
    id: 8, date: '2026-03-02', sport: 'running', family: 'threshold', target_tss: 60, duration_minutes: 60,
    status, matched_activity_id: 'a1', workout: { name: 'Tempo', steps: [tempo] },
  });
  const done = (duration, zones) => renderSessionDetail(toSession(planned(), {
    a1: { id: 'a1', sport: 'running', duration, tss: 50, hr_zone_times: zones },
  }));
  const seen = [
    ['as planned', done(3600, [0, 0, 3600, 0]), 'Done as planned', true],
    ['shorter', done(2700, [0, 0, 2700, 0]), 'Done, shorter than planned', false],
    ['longer', done(4500, [0, 0, 4500, 0]), 'Done, longer than planned', false],
    ['less hard time', done(3600, [0, 0, 1800, 0]), 'Done, less hard time than planned', false],
  ];
  for (const [what, html, words, check] of seen) {
    if (heroOf(html) !== words || html.includes('cb-sheet-hero__check') !== check) fail('done verdict', `sheet ${what}: ${heroOf(html)}`);
  }
  const unplanned = buildWeek({
    activities: [{ id: 'a9', name: 'Lunch run', sport: 'running', start_time: '2026-03-02T12:00:00', duration: 1800, tss: 30 }],
  }, '2026-03-02', ['2026-03-02'], {}).sessions[0];
  const bare = renderSessionDetail(unplanned);
  if (heroOf(bare) !== 'Done' || bare.includes('cb-sheet-hero__check')) fail('done verdict', `unplanned sheet: ${heroOf(bare)}`);
  const fromGarmin = buildWeek({
    activities: [{ id: 'a9', name: 'Lunch run', sport: 'running', start_time: '2026-03-02T12:00:00', duration: 1800, source: 'GARMIN_CONNECT' }],
  }, '2026-03-02', ['2026-03-02'], {}).sessions[0];
  if (!renderSessionDetail(fromGarmin).includes('· Garmin</div>')) fail('done verdict', 'an unplanned Garmin activity is not credited');
  console.log(`  · done verdict               ${cases.length} cases, sheet=${seen.map((s) => s[2]).join(' / ')}`);
}

// 9. A 30/15 VO₂max ride: every step shows its own length, seconds
//    included, and the outline draws the whole ride — all three sets and the
//    cool-down — at its true length, however many reps a set holds.
{
  const step = (type, sec) => ({
    type, duration_seconds: sec, duration: { duration_type: 'time', value_seconds: sec },
    target: { target_type: 'power', low_watts: 280, high_watts: 295 },
  });
  const set = { type: 'repeat', iterations: 13, steps: [step('interval', 30), step('recovery', 15)] };
  const steps = [
    step('warmup', 900), set, step('recovery', 150), set, step('recovery', 150), set, step('cooldown', 600),
  ];
  const session = toSession({
    slot_id: 'v', date: '2026-03-02', sport: 'cycling', family: 'interval', target_tss: 62,
    duration_minutes: 59.25, workout: { name: 'Short VO₂max Intervals', steps },
  }, {});
  const durs = session.workout.steps.flatMap((x) => x.children || [x]).map((x) => x.dur);
  const want = ['15min', '30s', '15s', '2min 30s', '30s', '15s', '2min 30s', '30s', '15s', '10min'];
  if (JSON.stringify(durs) !== JSON.stringify(want)) fail('30/15', `step lengths = ${JSON.stringify(durs)}`);
  const total = 900 + 3 * 13 * 45 + 2 * 150 + 600;
  const share = (type) => session.outline.filter((o) => o.type === type).reduce((s, o) => s + o.frac, 0);
  const last = session.outline[session.outline.length - 1];
  if (last.type !== 'cooldown') fail('30/15', `outline ends on ${last.type}, not the cool-down`);
  if (Math.abs(share('interval') - (3 * 13 * 30) / total) > 0.01) fail('30/15', `work share = ${share('interval').toFixed(3)}`);
  if (Math.abs(share('warmup') - 900 / total) > 0.01) fail('30/15', `warm-up share = ${share('warmup').toFixed(3)}`);
  console.log(`  · 30/15 ride                  steps=${durs.slice(1, 3).join('/')} outlineSegs=${session.outline.length}`);
}

// 9a. The hero's main set (mainSet), read off the display steps toSession
//     builds: the longest repeat block leads, the same block again counts as
//     sets, a steady session says its length and target, and a session
//     without steps has none.
{
  const step = (type, sec, target) => ({
    type, duration_seconds: sec, duration: { duration_type: 'time', value_seconds: sec }, target,
  });
  const set = (iterations, ...steps) => ({ type: 'repeat', iterations, steps });
  const sessionOf = (sport, steps, workout = { name: 'Main set', steps }) => toSession({
    slot_id: 'm', date: '2026-03-02', sport, family: 'interval', target_tss: 60, duration_minutes: 60, workout,
  }, {});
  const same = (what, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) fail('main set', `${what} = ${JSON.stringify(got)}`);
  };
  const pace = { target_type: 'pace', low_min_per_km: 4, high_min_per_km: 4.25 };
  const zone2 = { target_type: 'heart_rate_zone', zone: 2, low_bpm: 130, high_bpm: 145 };

  const reps = sessionOf('running', [step('warmup', 900), set(10, step('interval', 150, pace), step('recovery', 20)), step('cooldown', 600)]);
  same('10 × 2:30', mainSet(reps.workout.steps), {
    big: '10 × 2:30', lines: ['4:00–4:15 /km'], sub: ['20s recovery between reps'],
  });
  const html = renderSessionDetail(reps);
  ['cb-sheet-hero__big">10 × 2:30<', 'cb-sheet-hero__line">4:00–4:15 /km<', 'cb-sheet-hero__sub">20s recovery between reps<',
    'cb-workout-dur">15min<', 'cb-workout-dur">10min<'].forEach((t) => { if (!html.includes(t)) fail('main set', `the sheet lacks "${t}"`); });

  const power = { target_type: 'power', low_watts: 280, high_watts: 295 };
  const block = set(13, step('interval', 30, power), step('recovery', 15));
  const ride = sessionOf('cycling', [step('warmup', 900), block, step('recovery', 150), block, step('recovery', 150), block, step('cooldown', 600)]);
  same('30/15 ride', mainSet(ride.workout.steps), {
    big: '3 × (13 × 30s)', lines: ['280–295 W'], sub: ['15s recovery between reps'],
  });

  const steady = sessionOf('running', [step('interval', 5400, zone2)]);
  same('steady', mainSet(steady.workout.steps), { big: '1:30', lines: ['Z2 · 130–145 bpm'], sub: [] });
  const tempo = sessionOf('running', [
    step('warmup', 600), step('interval', 2400, { ...zone2, zone: 3, low_bpm: 150, high_bpm: 160 }),
    step('interval', 600, zone2), step('cooldown', 300),
  ]);
  same('steady with a second step', mainSet(tempo.workout.steps), {
    big: '40min', lines: ['Z3 · 150–160 bpm'], sub: ['plus 10min · Z2 · 130–145 bpm'],
  });

  if (mainSet(sessionOf('running', [], { name: 'Easy', steps: [] }).workout.steps) !== null) fail('main set', 'a session with no steps has a main set');
  if (mainSet(sessionOf('running', [], null).workout?.steps) !== null) fail('main set', 'a session with no workout has a main set');
  const bare = renderSessionDetail(sessionOf('running', [], null));
  if (!bare.includes('cb-sheet-hero__big">1h<')) fail('main set', 'a session with no steps does not say its length');
  console.log(`  · main set                    ${mainSet(reps.workout.steps).big} / ${mainSet(ride.workout.steps).big} / ${mainSet(steady.workout.steps).big}`);
}

// 10. Every sheet ends on the same row in one order — Move (a planned session
//     that can still move), Ask coach, then the activity's intervals.icu page —
//     and a done session keeps the slot its debrief is filled into, so there
//     is no second sheet to open.
{
  const row = (status, activityId) => ({
    id: 10, date: '2026-03-02', sport: 'running', family: 'easy', target_tss: 40, duration_minutes: 45,
    status, matched_activity_id: activityId, workout: { name: 'Easy Run', steps: [] },
  });
  const labels = (html) => [...html.match(/<div class="cb-detail-actions">[\s\S]*?<\/div>/)[0]
    .matchAll(/>([^<]+)<\/(?:button|a)>/g)].map((m) => m[1].trim());
  const same = (got, want, what) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) fail('action row', `${what} = ${JSON.stringify(got)}`);
  };
  const done = toSession(row('completed', 'a1'), { a1: { id: 'a1', sport: 'running', duration: 2700, tss: 38 } });
  const doneHtml = renderSessionDetail(done);
  same(labels(doneHtml), ['Ask coach', 'intervals.icu'], 'done session');
  if (!doneHtml.includes('href="https://intervals.icu/activities/a1"')) fail('action row', 'done session lacks the intervals.icu link');
  const name = done.title.replace(/^[^·]*·\s*/, '');
  if (!new RegExp(`data-action="ask-coach"\\s+data-name="${name}" data-date="2026-03-02"`).test(doneHtml)) {
    fail('action row', 'Ask coach must carry the session name and date for its draft');
  }
  const plannedHtml = renderSessionDetail(toSession(row('planned', null), {}));
  same(labels(plannedHtml), ['Ask coach'], 'planned session');
  if (!doneHtml.includes('data-debrief-slot') || plannedHtml.includes('data-debrief-slot')) {
    fail('action row', 'only a done session keeps a debrief slot');
  }
  const moveDays = [{ date: '2026-03-03', dow: 'Tue', current: false, disabled: false }];
  const movable = renderSessionDetail(toSession(row('planned', null), {}), { moveDays });
  same(labels(movable), ['Move', 'Ask coach'], 'planned session with days to move to');
  if (!movable.includes('data-sheet-page="move"') || !movable.includes('data-action="move-day" data-date="2026-03-03"')) {
    fail('action row', 'Move opens no page of days');
  }
  same(labels(renderSessionDetail(done, { moveDays })), ['Ask coach', 'intervals.icu'], 'done session offered days');
  console.log(`  · action row                  done=${labels(doneHtml).join(' | ')}`);
}

// 10a. What the activity counts as. The session the watch ran is one tap
//      away in the lead under the hero, every other choice is folded; an
//      activity that already counts as the right session gets a quiet
//      "Counts as" row and page instead; a race shows none of it.
{
  const { renderActivityDebrief } = await import(join(HERE, '../../web/components/activity_debrief.js'));
  const longRun = { session_id: 477, title: 'Long Trail Run', date: '2026-11-01', duration_minutes: 42 };
  const extra = { session_id: 566, title: 'Easy Run', date: '2026-10-26', duration_minutes: 14 };
  const sheet = (plan_link, extraView = {}, opts = {}) => renderActivityDebrief({
    activity_id: 'a1', name: 'Hillside - Long Trail Run', sport: 'running', date: '2026-10-26', plan_link, ...extraView,
  }, opts);
  const everything = (d) => d.lead + d.rows + d.pages;
  const line = (html) => (html.match(/<div class="cb-link-line">([^<]*)<\/div>/) || [])[1];
  const folded = (html) => /<div class="cb-link-options" hidden>/.test(html);
  const countsRow = (d) => d.rows.includes('cb-sheet-row__title">Counts as<');

  const wrong = sheet({ linked: extra, candidates: [longRun], looks_like: 477 });
  if (line(wrong.lead) !== 'Counts as Easy Run · Mon, Oct 26 · 14min') fail('plan link', `mismatch line = ${line(wrong.lead)}`);
  if (!/class="cb-btn-primary" data-action="pair-activity"[\s\S]*?session_id&quot;:477[\s\S]*?>Link to Long Trail Run \(Sun, Nov 1\) instead</.test(wrong.lead)) {
    fail('plan link', 'the session the watch ran is not one tap away');
  }
  if (!folded(wrong.lead) || !wrong.lead.includes('data-action="unlink-activity"')) fail('plan link', 'other choices are not folded');
  if (countsRow(wrong) || wrong.pages.includes('data-sheet-page="counts"')) fail('plan link', 'a mismatch also gets the quiet row');

  const open = sheet({ linked: null, candidates: [longRun], looks_like: 477 });
  if (line(open.lead) !== 'Looks like Long Trail Run · Sun, Nov 1 · 42min') fail('plan link', `unlinked line = ${line(open.lead)}`);
  if (!open.lead.includes('>Link to Long Trail Run</button>')) fail('plan link', 'an unlinked activity lacks its one tap');
  if (countsRow(open)) fail('plan link', 'an unlinked activity gets the quiet row');

  // Linked and nothing better: the lead stays empty, the row says it, its page holds the choices.
  const right = sheet({ linked: longRun, candidates: [extra], looks_like: null }, {}, { back: 'Hillside' });
  if (right.lead !== '') fail('plan link', `a right link still shows under the hero: ${right.lead.slice(0, 60)}`);
  if (!countsRow(right) || !right.rows.includes('data-page="counts"') || !right.rows.includes('>Long Trail Run · Sun, Nov 1 · 42min<')) {
    fail('plan link', 'a linked activity lacks its Counts as row');
  }
  if (!right.pages.includes('data-sheet-page="counts"') || line(right.pages) !== 'Counts as Long Trail Run · Sun, Nov 1 · 42min') {
    fail('plan link', `the Counts as page lacks its line: ${line(right.pages)}`);
  }
  if (!right.pages.includes('data-show-label="Change"') || !right.pages.includes('data-action="unlink-activity"')) {
    fail('plan link', 'the Counts as page lacks its way to change');
  }
  if (!right.pages.includes('<span>Hillside</span>')) fail('plan link', 'the page does not name the way back');
  if (everything(right).includes('cb-btn-primary" data-action="pair-activity"')) fail('plan link', 'a linked activity offers a primary link');

  const nothing = sheet({ linked: null, candidates: [], looks_like: null });
  if (everything(nothing).includes('cb-link') || countsRow(nothing)) fail('plan link', 'nothing to offer still renders');
  const ridge = { race: { name: 'Ridge', priority: 'A' } };
  const raceLinked = sheet({ linked: extra, candidates: [], looks_like: null }, ridge);
  if (everything(raceLinked).includes('cb-link') || countsRow(raceLinked)) fail('plan link', 'a race sheet offers plan sessions');
  const brief = { depth: 'quick', created_at: '2026-10-25T08:00:00', text: 'Go out easy.' };
  const race = sheet(undefined, ridge, { briefTakes: [brief] });
  if (!race.lead.includes('Ridge (A race)') || !race.lead.includes('Go out easy.')) fail('plan link', 'a race lacks its line or its brief in the lead');
  console.log('  · plan link                   looks like → one tap, linked → one row');
}

// 10b. Compare on this route: offered on a done session's sheet with a GPS
//      track, never on an indoor one; it asks in the athlete's words, with
//      "show me".
{
  const { compareRoutePrompt } = await import(join(HERE, '../../web/shells/shared.js'));
  const offers = (html) => html.includes('data-action="compare-route"');
  const row = { id: 10, date: '2026-10-15', sport: 'cycling', family: 'easy', target_tss: 40, duration_minutes: 60,
    status: 'completed', matched_activity_id: 'a1', workout: { name: 'Endurance Ride', steps: [] } };
  const ride = (type) => renderSessionDetail(toSession(row, { a1: { id: 'a1', sport: 'cycling', type, duration: 3600, tss: 50 } }));
  if (!offers(ride('Ride')) || offers(ride('VirtualRide'))) fail('compare route', 'the session sheet does not follow the type');
  if (!ride('Ride').includes('data-routable="1"')) fail('compare route', 'the slot does not keep it for a redraw');
  const ask = compareRoutePrompt({ activityId: 'a1', name: 'Loop', date: '2026-10-15' });
  if (!ask.includes('(activity a1)') || !ask.endsWith('Show me.')) fail('compare route', ask);
  console.log('  · compare on this route       outdoor yes, indoor no');
}

// 10c. The reps against the plan's band (repsAgainstBand), with the band
//      toSession reads off the workout (workBand): a rep one second, beat or
//      watt outside still counts as inside; the reading is none unless
//      intervals.icu found exactly the reps the plan asked for; a pyramid has
//      no single band.
{
  const { repsAgainstBand, renderActivityDebrief } = await import(join(HERE, '../../web/components/activity_debrief.js'));
  const step = (type, sec, target) => ({
    type, duration_seconds: sec, duration: { duration_type: 'time', value_seconds: sec }, target,
  });
  const set = (iterations, ...steps) => ({ type: 'repeat', iterations, steps });
  const bandOf = (sport, steps) => toSession({
    slot_id: 'b', date: '2026-03-02', sport, family: 'interval', target_tss: 60, duration_minutes: 60,
    workout: { name: 'Intervals', steps },
  }, {}).workBand;
  const work = (n, target) => set(n, step('interval', 240, target), step('recovery', 90));
  const same = (what, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) fail('reps vs band', `${what} = ${JSON.stringify(got)}`);
  };

  // Pace 4:15–4:30 /km: 255 to 270 s, quicker the lower.
  const pace = { target_type: 'pace', low_min_per_km: 4.25, high_min_per_km: 4.5 };
  const paceBand = bandOf('running', [step('warmup', 600), work(4, pace), step('cooldown', 600)]);
  same('pace band', paceBand, { metric: 'pace', low: 255, high: 270, reps: 4, plan: '4 × 4min at 4:15–4:30 /km' });
  const rep = (p, hr = 165) => ({ type: 'WORK', pace_min_km: p, avg_hr: hr });
  const lap = { type: 'RECOVERY', pace_min_km: '6:30', avg_hr: 140 };
  const run = (paces, band = paceBand) => repsAgainstBand({ intervals: paces.map((p) => rep(p)) }, band);
  const held = (paces, want, what) => {
    const reading = run(paces);
    same(what, reading && [reading.held, reading.count], want);
  };

  const inside = run(['4:26', '4:20', '4:18', '4:29']);
  same('four of four', [inside.held, inside.count, inside.mean], [4, 4, '4:23 /km']);
  same('rep one', inside.reps[0], { n: 1, main: '4:26 /km', other: '165 bpm', at: '36.0' });
  same('ends', inside.ends, ['Slower', 'Faster']);
  held(['4:26', '4:20', '4:45', '4:29'], [3, 4], 'a rep outside counts against it');
  held(['4:26', '4:20', '4:31', '4:14'], [4, 4], 'one second out on either side is inside');
  held(['4:26', '4:20', '4:32', '4:29'], [3, 4], 'two seconds slow is outside');
  held(['4:26', '4:20', '4:13', '4:29'], [3, 4], 'two seconds fast is outside');
  same('far outside stays on the track', run(['4:26', '4:20', '4:18', '5:30']).reps[3].at, '3.0');
  // Recovery laps are not reps; a different count, a rep without the number or no band is no reading.
  same('laps between the reps', repsAgainstBand({ intervals: [rep('4:26'), lap, rep('4:20'), lap, rep('4:18'), lap, rep('4:29')] }, paceBand)?.count, 4);
  if (run(['4:26', '4:20', '4:18']) !== null) fail('reps vs band', 'three reps read against four');
  if (run(['4:26', '4:20', '4:18', '4:29', '4:25']) !== null) fail('reps vs band', 'five reps read against four');
  if (repsAgainstBand({ intervals: [rep('4:26'), rep('4:20'), rep('4:18'), { type: 'WORK', avg_hr: 160 }] }, paceBand) !== null) {
    fail('reps vs band', 'a rep without a pace read against a pace band');
  }
  if (run(['4:26', '4:20', '4:18', '4:29'], null) !== null) fail('reps vs band', 'no band still reads');

  // A pyramid asks a different band of each rep: nothing to read the session against.
  const target = (lo, hi) => ({ target_type: 'pace', low_min_per_km: lo, high_min_per_km: hi });
  const pyramid = bandOf('running', [
    step('interval', 120, target(4.5, 4.75)), step('recovery', 60), step('interval', 240, target(4.25, 4.5)), step('recovery', 60),
    step('interval', 360, target(4, 4.25)), step('recovery', 60), step('interval', 240, target(4.25, 4.5)),
  ]);
  if (pyramid !== null) fail('reps vs band', `a pyramid has a band: ${JSON.stringify(pyramid)}`);

  // Heart rate and power read avg_hr and avg_watts, the other figure riding along.
  const hrBand = bandOf('running', [work(3, { target_type: 'heart_rate_zone', zone: 4, low_bpm: 160, high_bpm: 170 })]);
  same('HR band', [hrBand.metric, hrBand.low, hrBand.high, hrBand.reps], ['hr', 160, 170, 3]);
  const hrRun = (bpms) => repsAgainstBand({ intervals: bpms.map((hr) => rep('4:26', hr)) }, hrBand);
  const byHr = hrRun([165, 171, 159]);
  same('HR held', [byHr.held, byHr.count, byHr.mean], [3, 3, '165 bpm']);
  same('HR rep', [byHr.reps[0], byHr.ends], [{ n: 1, main: '165 bpm', other: '4:26 /km', at: '50.0' }, ['Lower', 'Higher']]);
  same('HR two out', hrRun([165, 172, 158]).held, 1);
  const wattBand = bandOf('cycling', [work(3, { target_type: 'power', low_watts: 250, high_watts: 270 })]);
  same('power band', [wattBand.metric, wattBand.low, wattBand.high, wattBand.reps], ['power', 250, 270, 3]);
  const wattRun = (watts) => repsAgainstBand({ intervals: watts.map((w) => ({ type: 'WORK', avg_watts: w, avg_hr: 150 })) }, wattBand);
  same('power held', [wattRun([260, 271, 249]).held, wattRun([260, 272, 248]).held], [3, 1]);
  same('power rep', wattRun([260, 260, 260]).reps[0], { n: 1, main: '260 W', other: '150 bpm', at: '50.0' });
  if (repsAgainstBand({ intervals: [1, 2, 3].map(() => rep('4:26', 150)) }, wattBand) !== null) fail('reps vs band', 'reps without watts read against a power band');

  // In the debrief: the Intervals row and its page say it, and a pyramid falls back to the splits.
  const view = (paces) => ({ activity_id: 'a1', sport: 'running', date: '2026-03-02', intervals: paces.flatMap((p, i) => [{ n: 2 * i + 1, ...rep(p) }, { n: 2 * i + 2, ...lap }]) });
  const read = renderActivityDebrief(view(['4:26', '4:20', '4:18', '4:29']), { band: paceBand });
  ['4 of 4 in the band · 4:23 /km', 'Plan: 4 × 4min at 4:15–4:30 /km', 'cb-rep__mark'].forEach((t) => {
    if (!(read.rows + read.pages).includes(t)) fail('reps vs band', `the debrief lacks "${t}"`);
  });
  const fallback = renderActivityDebrief(view(['4:26', '4:20', '4:18', '4:29']), { band: pyramid });
  if ((fallback.rows + fallback.pages).includes('in the band') || !fallback.rows.includes('8 splits')) fail('reps vs band', 'no band must fall back to the splits');
  console.log(`  · reps vs band                ${inside.held}/${inside.count} mean ${inside.mean}, hr ${byHr.held}/${byHr.count}`);
}

// 11. A workout that lives only on intervals.icu — a finished session of a
//     replaced or deleted plan, or a coach extra — keeps its planned side:
//     paired, it is one done session with its compliance (never a second,
//     unplanned entry); unpaired and past, it is missed; and a coach extra
//     never takes the week's anchor, however big.
{
  const row = (id, externalId, planId, tss, status, matched) => ({
    id, external_id: externalId, plan_id: planId, date: '2026-03-02', sport: 'running',
    name: 'Easy Run', note: null, duration: 3600, tss, workout: null, status,
    ...(matched ? { matched_activity_id: matched, compliance_pct: 90 } : {}),
  });
  const act = (id, tss) => ({ id, sport: 'running', duration: 3300, tss, start_time: '2026-03-02T07:00:00' });
  const week = buildWeek({
    activities: [act('a1', 55), act('a2', 70)],
    calendar_only: [
      row(601, 'coachie:6:s1', 6, 60, 'completed', 'a1'),
      row(602, 'coachie:6:s2', 6, 40, 'missed'),
      row(603, 'coachie:adhoc:2026-03-02:ab12cd34', null, 80, 'completed', 'a2'),
    ],
  }, '2026-03-02', ['2026-03-02'], {});
  const byId = Object.fromEntries(week.sessions.map((s) => [s.id, s]));
  if (week.sessions.length !== 3) fail('calendar-only', `${week.sessions.length} sessions, expected 3 (no unplanned twins)`);
  const done = byId['event-601'];
  if (!done?.done || done.actual?.compliance !== 92 || done.activityId !== 'a1') {
    fail('calendar-only', `paired session: done=${done?.done} compliance=${done?.actual?.compliance}`);
  }
  if (!byId['event-602']?.missed || byId['event-602'].done) fail('calendar-only', 'the unpaired past session is not missed');
  const anchor = week.sessions.find((s) => s.anchor);
  if (anchor?.id !== 'event-601') fail('calendar-only', `anchor = ${anchor?.id}, expected the former plan's session`);
  if (week.plannedTss !== 180 || week.doneTss !== 125) fail('calendar-only', `planned/done = ${week.plannedTss}/${week.doneTss}`);
  console.log(`  · calendar-only rows          sessions=${week.sessions.length} anchor=${anchor?.id} done=${week.doneTss}/${week.plannedTss}`);
}

// 12. The brief: the deterministic sheet gives one row per part the sheet
//     carries (none for a null one), the page behind each row that has more
//     to say, and the credit its figures need; an upcoming plan session —
//     never a done one — holds the slot and the Coach's brief.
{
  const { renderBriefSheet, renderRaceDetail } = await import(join(HERE, '../../web/components/session_brief.js'));
  const sheets = JSON.parse(readFileSync(join(HERE, 'brief_sheet_fixture.json'), 'utf8'));
  const titlesOf = (html) => [...html.matchAll(/cb-sheet-row__title">([^<]+)</g)].map((m) => m[1]);
  const pagesOf = (html) => [...html.matchAll(/data-sheet-page="([^"]+)"/g)].map((m) => m[1]);
  const labelsOf = (html) => [...html.matchAll(/cb-reschedule-label">([^<]+)</g)].map((m) => m[1]);
  const has = (html, what, text) => { if (!html.includes(text)) fail('brief', `${what} lacks "${text}"`); };
  const session = renderBriefSheet(sheets.session, { back: 'Long trail run' });
  const want = ['What to hold', 'What success looks like', 'Fuel and water', 'Kit and conditions',
    'Routes that fit', 'Where you stand', 'Not enough data yet'];
  if (JSON.stringify(titlesOf(session.rows)) !== JSON.stringify(want)) fail('brief', `session rows = ${titlesOf(session.rows)}`);
  const wantPages = ['hold', 'success', 'fuel', 'kit', 'routes', 'stand', 'thin'];
  if (JSON.stringify(pagesOf(session.pages)) !== JSON.stringify(wantPages)) fail('brief', `session pages = ${pagesOf(session.pages)}`);
  const all = session.rows + session.pages;
  ['≤150 bpm', '152–158 bpm', '60–80 g', 'First third under 155 bpm', 'Finish feeling you had another 5 km in you',
    'Coastal Trail Running', '4:30', '7–17 °C', 'No run over three hours'].forEach((t) => has(all, 'session sheet', t));
  // Proven ground is a subsection of the hold page; the card's own steps are on the sheet.
  if (!labelsOf(session.pages).includes('Proven ground') || labelsOf(session.rows).length) fail('brief', 'Proven ground is not a subsection of the hold page');
  if (all.includes('Easy warm-up 10 min')) fail('brief', 'the hold card still renders');
  if (!session.pages.includes('<span>Long trail run</span>')) fail('brief', 'a page does not name the way back');
  if (session.foot !== 'May include data from Garmin devices.') fail('brief', `foot = ${JSON.stringify(session.foot)}`);
  const race = renderBriefSheet(sheets.race);
  ['Finish time', 'Logistics', '3:35–3:50', 'Start 08:00', 'Open'].forEach((t) => has(race.rows + race.pages, 'race sheet', t));
  if (titlesOf(race.rows).includes('Routes that fit')) fail('brief', 'a race sheet shows no routes');
  const empty = renderBriefSheet(sheets.empty);
  if (empty.rows !== '' || empty.pages !== '') fail('brief', `an all-null sheet renders ${JSON.stringify((empty.rows + empty.pages).slice(0, 60))}`);
  const row = (status, activityId) => ({
    id: 10, date: '2026-03-02', sport: 'running', family: 'easy', target_tss: 40, duration_minutes: 45,
    status, matched_activity_id: activityId, workout: { name: 'Easy Run', steps: [] },
  });
  const upcoming = renderSessionDetail(toSession(row('planned', null), {}));
  ['data-brief-slot', 'data-handle="session:10"', '>Quick brief<', '>Full brief<'].forEach((t) => has(upcoming, 'upcoming sheet', t));
  const done = renderSessionDetail(toSession(row('completed', 'a1'), { a1: { id: 'a1', sport: 'running', duration: 2700, tss: 38 } }));
  if (done.includes('data-brief-slot') || done.includes('Quick brief')) fail('brief', 'a done session shows the brief');
  const raceSheet = renderRaceDetail({ name: 'Forest Trail', race_date: '2026-10-17', priority: 'A', distance_km: 50, climb_m: 1800 },
    { todayIso: '2026-10-15' });
  ['data-brief-slot', 'data-handle="race:2026-10-17"', '2 days', '1,800 m', '>Quick brief<', 'var(--cb-race-a)'].forEach((t) => has(raceSheet, 'race detail', t));
  console.log(`  · brief sheet                 rows=${titlesOf(session.rows).length} race=${titlesOf(race.rows).length} empty=${empty.rows.length + empty.pages.length}`);
}

// 12a. The fuel row. A session that water alone covers says so in full on a
//      plain row with nothing to open; a fuel plan with carbs, or more than
//      one thing to say, opens a page.
{
  const { renderBriefSheet } = await import(join(HERE, '../../web/components/session_brief.js'));
  const sheets = JSON.parse(readFileSync(join(HERE, 'brief_sheet_fixture.json'), 'utf8'));
  const fuel = (over) => ({
    carbs_g_per_h: null, carbs_total_g: null, fluid_ml_per_h: null, before: null, carb_load: null, notes: [], ...over,
  });
  const water = 'Water is enough for this one; no carbs needed on the way.';
  const plain = renderBriefSheet({ fuel: fuel({ notes: [water] }) });
  if (!plain.rows.includes('cb-sheet-row__title">Fuel and water<')) fail('fuel row', 'a water-only session has no Fuel and water row');
  if (!plain.rows.includes(`cb-sheet-row__answer cb-sheet-row__answer--whole">${water}<`)) fail('fuel row', 'the water note is not on the row, in full');
  if (plain.rows.includes('data-action="sheet-open"') || !plain.rows.includes('cb-sheet-row--plain')) fail('fuel row', 'a water-only row opens something');
  if (plain.pages !== '') fail('fuel row', `a water-only session has a page: ${plain.pages.slice(0, 60)}`);

  const carbs = renderBriefSheet({ fuel: sheets.session.fuel });
  if (!carbs.rows.includes('data-action="sheet-open" data-page="fuel"')) fail('fuel row', 'a carb plan does not open a page');
  ['60–80 g of carbs, 400–700 ml of fluid an hour'].forEach((t) => { if (!carbs.rows.includes(t)) fail('fuel row', `the carb row lacks "${t}"`); });
  ['data-sheet-page="fuel"', '>Carbs<', '>In total<', '180–240 g', '>Fluid<', 'A normal meal 2-3 hours before.',
    'Up to 90 g an hour only if you have practised it.'].forEach((t) => { if (!carbs.pages.includes(t)) fail('fuel row', `the fuel page lacks "${t}"`); });

  const said = renderBriefSheet({ fuel: fuel({ before: 'A normal meal 2-3 hours before.', notes: ['Sip little and often.'] }) });
  if (!said.rows.includes('data-page="fuel"') || !said.pages.includes('Sip little and often.')) fail('fuel row', 'two things to say do not open a page');
  console.log('  · fuel row                    water → plain row, carbs → page');
}

// 12b. Every state of a session sheet, and the race sheet, renders without
//      printing "undefined", "null" or "NaN": planned, done with its activity,
//      done with no plan row, missed, skipped, ad hoc, and a race.
{
  const { renderRaceDetail, renderBriefSheet } = await import(join(HERE, '../../web/components/session_brief.js'));
  const { renderActivityDebrief } = await import(join(HERE, '../../web/components/activity_debrief.js'));
  const sheets = JSON.parse(readFileSync(join(HERE, 'brief_sheet_fixture.json'), 'utf8'));
  const junk = /.{0,30}(?:undefined|null|NaN).{0,30}/;
  const clean = (what, html, wants = []) => {
    const found = html.match(junk);
    if (found) fail('sheet states', `${what} prints …${found[0]}…`);
    wants.forEach((t) => { if (!html.includes(t)) fail('sheet states', `${what} lacks "${t}"`); });
  };
  const steps = [
    { type: 'warmup', duration_seconds: 600, target: { target_type: 'heart_rate_zone', zone: 2, low_bpm: 130, high_bpm: 145 } },
    {
      type: 'repeat',
      iterations: 4,
      steps: [
        { type: 'interval', duration_seconds: 240, description: 'Tempo — hold it steady', target: { target_type: 'pace', low_min_per_km: 4.25, high_min_per_km: 4.5 } },
        { type: 'recovery', duration_seconds: 90 },
      ],
    },
    { type: 'cooldown', duration_seconds: 600 },
  ];
  const plan = (status, over = {}) => ({
    id: 10, date: '2026-03-02', sport: 'running', family: 'threshold', target_tss: 60, duration_minutes: 60, status,
    coaching_intent: 'Hold the band.', coaching_notes: ['<b>Cue:</b> relax the shoulders'], why_details: ['Builds threshold.'],
    workout: { name: 'Threshold', steps }, ...over,
  });
  const act = {
    id: 'a1', sport: 'running', type: 'Run', duration: 3700, distance: 11000, average_speed_mps: 3, tss: 55, feel: 2, rpe: 7,
    average_hr: 158, max_hr: 176, source: 'GARMIN_CONNECT', hr_zone_times: [300, 900, 1500, 900, 100, 0, 0],
  };
  const takes = [
    { depth: 'quick', created_at: '2026-03-02T18:00:00', text: '**Solid.** Held the band.' },
    { depth: 'full', created_at: '2026-03-02T18:05:00', text: 'The long debrief.\n- one thing' },
  ];
  const briefTakes = [{ depth: 'quick', created_at: '2026-03-01T08:00:00', text: 'Go out easy.' }];
  const moveDays = [{ date: '2026-03-03', dow: 'Tue', current: false, disabled: false }, { date: '2026-03-04', dow: 'Wed', current: true, disabled: true }];

  // A session to come shows its steps, idea and cues on the overview; a done one keeps them behind a row.
  const planned = renderSessionDetail(toSession(plan('planned'), {}), { moveDays, briefTakes });
  clean('planned', planned, ['data-brief-slot', 'Why this session', 'cb-workout-row', 'Hold the band.', 'relax the shoulders', 'Move to another day']);
  if (planned.includes('All steps and cues')) fail('sheet states', 'a planned session hides its steps behind a row');
  clean('planned, brief in flight', renderSessionDetail(toSession(plan('planned'), {}), { moveDays, briefPending: 'full' }),
    ['Writing your full brief…']);
  clean('planned, little known', renderSessionDetail(toSession({ id: 11, date: '2026-03-02', sport: 'cycling', status: 'planned' }, {})), ['data-brief-slot']);
  const finished = plan('completed', { matched_activity_id: 'a1' });
  clean('done', renderSessionDetail(toSession(finished, { a1: act }), { takes, briefTakes }),
    ['Done as planned', 'data-debrief-slot', 'All the numbers', 'Briefed', 'All steps and cues']);
  clean('done, take in flight', renderSessionDetail(toSession(finished, { a1: act }), { pending: 'quick' }), ['Writing your quick take…']);
  clean('done, activity barely known', renderSessionDetail(toSession(finished, { a1: { id: 'a1', sport: 'running' } })), ['data-debrief-slot']);
  clean('done, activity withheld', renderSessionDetail(toSession(finished, { a1: { id: 'a1', sport: 'other', source: 'STRAVA' } })),
    ['details not shared by intervals.icu']);
  const week = buildWeek({
    activities: [{ id: 'a9', name: 'Lunch run', sport: 'running', start_time: '2026-03-02T12:00:00', duration: 1800, tss: 30 }],
    calendar_only: [{
      id: 701, external_id: 'coachie:adhoc:2026-03-02:ab12cd34', plan_id: null, date: '2026-03-02', sport: 'running',
      name: 'Easy spin', note: 'Keep it light.', duration: 1800, tss: 25, status: 'adhoc', workout: { name: 'Easy spin', steps },
    }],
  }, '2026-03-02', ['2026-03-02'], {});
  const [adhoc, unplanned] = ['event-701', 'act-a9'].map((id) => week.sessions.find((s) => s.id === id));
  const unplannedHtml = renderSessionDetail(unplanned, { takes });
  clean('done, no plan row', unplannedHtml, ['Done', 'data-debrief-slot']);
  if (unplannedHtml.includes('Why this session') || unplannedHtml.includes('data-brief-slot')) fail('sheet states', 'a session with no plan row has a plan side');
  clean('missed', renderSessionDetail(toSession(plan('missed'), {}), { moveDays }), ['MISSED']);
  clean('skipped', renderSessionDetail(toSession(plan('skipped'), {}), { moveDays }), ['SKIPPED']);
  clean('ad hoc', renderSessionDetail(adhoc), ['data-action="delete-adhoc"', 'data-external-id="coachie:adhoc:2026-03-02:ab12cd34"']);

  const race = { name: 'Forest Trail', race_date: '2026-10-17', priority: 'B', distance_km: 50, climb_m: 1800, goalLabel: 'Trail 50K' };
  clean('race', renderRaceDetail(race, { briefTakes, todayIso: '2026-10-15' }), ['In 2 days', 'B race · Trail 50K', 'Go out easy.']);
  clean('race, brief in flight', renderRaceDetail(race, { briefPending: 'quick', todayIso: '2026-10-15' }), ['Writing your quick brief…']);
  clean('race today', renderRaceDetail(race, { todayIso: '2026-10-17' }), ['Race day']);
  clean('race behind us', renderRaceDetail(race, { todayIso: '2026-10-20' }), ['3 days ago']);
  clean('race, little known', renderRaceDetail({ race_date: '2026-10-17' }, { todayIso: '2026-10-15' }), ['A race']);

  // The rows and pages the shells fill in once the worker answers.
  for (const [what, sheet] of Object.entries(sheets)) {
    const { rows, pages, foot } = renderBriefSheet(sheet, { back: 'Back' });
    clean(`brief ${what}`, rows + pages + foot);
  }
  const view = {
    activity_id: 'a1', sport: 'running', date: '2026-03-02', hr_zone_minutes: [5, 15, 25, 15, 2],
    groups: [{ label: 'Tempo', type: 'WORK', count: 4, pace_min_km: '4:26', avg_hr: 165, duration: '4:00' }],
    intervals: [{ n: 1, type: 'WORK', pace_min_km: '4:26', avg_hr: 165, duration: '4:00', distance_km: 0.9 }],
    interval_summary: ['4 × 4:00 at 4:26 /km'], plan_link: { linked: null, candidates: [], looks_like: null },
  };
  const debrief = renderActivityDebrief(view, { back: 'Running · Threshold' });
  clean('debrief', debrief.lead + debrief.rows + debrief.pages, ['Time in zones', 'Intervals']);
  console.log('  · sheet states                planned, done, unplanned, missed, skipped, ad hoc, race');
}

// 12c. A coach reply's opening as a row's answer (previewText): no bold marks,
//      bullets, table rows or chart lines, on one line of at most 200 characters.
{
  const { previewText } = await import(join(HERE, '../../web/components/sheet_pages.js'));
  const { takeSummary } = await import(join(HERE, '../../web/components/activity_debrief.js'));
  const reply = [
    '**Good session.** Steady all the way.', '- Held the band', '- Fuelled well', '| Km | Pace |', '|----|------|',
    '| 1 | 4:20 |', '  | 2 | 4:21 |', '%%CHART%%{"kind":"line","title":"Pace"}', '', 'Last line **bold**.',
  ].join('\n');
  const got = previewText(reply);
  if (got !== 'Good session. Steady all the way. Held the band Fuelled well Last line bold.') fail('preview', `got ${JSON.stringify(got)}`);
  if (previewText('word '.repeat(100)).length !== 200) fail('preview', 'a long reply is not cut at 200 characters');
  if (takeSummary([{ depth: 'full', text: '**Full** debrief' }, { depth: 'quick', text: '**Quick** take' }]) !== 'Quick take') {
    fail('preview', 'the take row does not preview the quick take');
  }
  console.log(`  · preview text                ${JSON.stringify(got.slice(0, 30))}…`);
}

// 12d. Where the calendar opens: today, unless the athlete left another place
//      on this same day a moment ago. Nothing else may keep it from today.
{
  const { landingPlace, PLACE_KEEP_MS } = await import(join(HERE, '../../web/data/store.js'));
  const today = '2026-11-03';
  const home = { week: '2026-11-02', day: today };
  const at = 1_000_000_000;
  const left = { today, week: '2026-11-23', day: '2026-11-25', at };
  const same = (what, got, want) => {
    if (JSON.stringify(got) !== JSON.stringify(want)) fail('landing', `${what} = ${JSON.stringify(got)}`);
  };
  same('a moment ago', landingPlace(left, today, at + 60_000), { week: left.week, day: left.day });
  same('just inside', landingPlace(left, today, at + PLACE_KEEP_MS - 1), { week: left.week, day: left.day });
  same('hours later the same day', landingPlace(left, today, at + PLACE_KEEP_MS), home);
  same('yesterday', landingPlace({ ...left, today: '2026-11-02' }, today, at + 1000), home);
  same('nothing saved', landingPlace(null, today, at), home);
  same('saved before the time was kept', landingPlace({ today, week: left.week, day: left.day }, today, at), home);
  same('a clock set back', landingPlace(left, today, at - 1000), home);
  same('a day outside its week', landingPlace({ ...left, day: '2026-11-03' }, today, at + 1000), home);
  same('junk', landingPlace({ today, week: 7, day: {}, at }, today, at), home);
  const sunday = landingPlace(null, '2026-11-08', at);
  same('a Sunday stays in its own week', sunday, { week: '2026-11-02', day: '2026-11-08' });
  console.log(`  · landing                     keeps a place for ${PLACE_KEEP_MS / 60000} min, then today`);
}

// --- 13. A chart under a coach reply (chat_chart.js) ------------------------
{
  const { renderChart } = await import(join(HERE, '../../web/components/chat_chart.js'));
  const { renderMessage } = await import(join(HERE, '../../web/components/coach_msg.js'));
  const has = (html, name, text) => { if (!html.includes(text)) fail('chart', `${name} lacks ${JSON.stringify(text)}`); };
  const pace = renderChart({
    kind: 'line', title: 'Pace <per km>', x_label: 'km', unit: 'min/km', labels: null, stacked: false,
    x: [1, 2, 3], ys: [[250, 262, 281]],
  });
  ['<svg class="cb-chart__plot"', 'Pace &lt;per km&gt;', '>4:', 'min/km · by km', '<path d="M'].forEach((t) => has(pace, 'pace line', t));
  // Faster sits higher: the 250 s point is drawn above the 281 s one.
  const ys = [...pace.matchAll(/[ML][\d.]+,([\d.]+)/g)].map((m) => Number(m[1]));
  if (!(ys[0] < ys[2])) fail('chart', `pace axis not flipped: ${ys}`);
  const bars = renderChart({
    kind: 'bar', title: 'Hours per sport', x_label: 'week', unit: 'h', stacked: true,
    labels: ['swim', 'ride', 'run'], x: ['2026-10-12', '2026-10-19'], ys: [[1, 1.2], [4, 2.1], [3.4, 5.4]],
  });
  const rects = (bars.match(/<rect /g) || []).length;
  if (rects !== 6) fail('chart', `stacked bars drew ${rects} rects, want 6`);
  // The tallest stack (8.7 h) stays inside the plot: no bar starts above it.
  const tops = [...bars.matchAll(/<rect x="[\d.]+" y="([\d.-]+)"/g)].map((m) => Number(m[1]));
  if (Math.min(...tops) < 8) fail('chart', `a stacked bar is clipped at the top: y=${Math.min(...tops)}`);
  ['cb-chart__legend', '12 Oct', '>ride<'].forEach((t) => has(bars, 'stacked bars', t));
  if (renderChart({ kind: 'line', x: [], ys: [[]] }) !== '') fail('chart', 'an empty spec draws something');
  // Two sessions on the same climbs: one colour each, the change above each pair, names on two lines.
  const pair = renderChart({
    kind: 'bar', title: 'Same climbs', x_label: '', unit: 'm/h', stacked: false, labels: ['25 Jul', '3 Oct'],
    x: ['km 5 · 8%', 'km 9 · 14%'], ys: [[629, 892], [606, 800]],
  });
  ['>-4%<', '>-10%<', '>km 9<', '>14%<', '>25 Jul<', 'var(--cb-warn)'].forEach((t) => has(pair, 'paired bars', t));
  const gap = renderChart({ kind: 'line', title: 'Gap', unit: 's', x: [0, 1, 2], ys: [[0, -30, 20]], labels: null });
  has(gap, 'time gap', 'cb-chart__zero');
  const msg = renderMessage({ role: 'coach', text: 'See below.', charts: [{ kind: 'scatter', title: 'T', unit: '', x: ['2026-10-01'], ys: [[1]] }] });
  if (msg.indexOf('cb-chart') < msg.indexOf('See below.')) fail('chart', 'the chart is not under the reply text');
  console.log(`  · chat chart                  pace=${pace.length}B bars=${rects} rects`);
}

// --- 14. A boot that stops answering fails with a support report (api.js) ---
{
  // A worker that reports two steps, then goes silent: what a phone's
  // browser leaves when it kills the worker without an error event.
  let terminated = false;
  globalThis.Worker = class {
    postMessage({ method }) {
      if (method !== 'boot') return;
      setTimeout(() => this.onmessage({ data: { status: 'Loading Endury…' } }), 5);
      setTimeout(() => this.onmessage({ data: { status: 'Reading your data from intervals.icu…' } }), 10);
    }
    terminate() { terminated = true; }
  };
  globalThis.document ??= { addEventListener() {}, visibilityState: 'visible' };
  const { bootRuntime } = await import(join(HERE, '../../web/data/api.js'));
  const seen = [];
  const err = await bootRuntime({
    settings: { intervals_api_key: 'k' }, onStatus: (s) => seen.push(s), stallMs: 60,
  }).then(() => null, (e) => e);
  if (!err) fail('boot watchdog', 'a silent worker never failed the boot');
  else {
    if (!err.message.includes('Reading your data from intervals.icu')) fail('boot watchdog', `message names no step: ${err.message}`);
    const report = err.details || '';
    ['Endury boot report', 'Loading Endury…', 'Reading your data from intervals.icu…', 'Browser:'].forEach((t) => {
      if (!report.includes(t)) fail('boot watchdog', `report lacks ${JSON.stringify(t)}`);
    });
    if (report.includes("'k'") || report.includes('"k"')) fail('boot watchdog', 'report carries a setting');
  }
  if (!terminated) fail('boot watchdog', 'the stuck worker was not stopped');
  if (seen.length !== 2) fail('boot watchdog', `onStatus saw ${seen.length} lines, want 2`);
  console.log(`  · boot watchdog               report=${(err?.details || '').split('\n').length} lines`);
}

// --- 15. A take or a brief runs outside the chat (coach.js → api.js → worker) -
{
  const chats = [];
  let answer = { reply: 'The whole debrief.' };
  let release = null;
  globalThis.Worker = class {
    async postMessage({ id, method, params }) {
      if (method === 'chat') {
        chats.push(params);
        if (release) await release;
      }
      const result = method === 'chat' ? answer : {};
      this.onmessage({ data: { id, envelope: { ok: true, result } } });
    }
    terminate() {}
  };
  const { bootRuntime } = await import(join(HERE, '../../web/data/api.js'));
  const { createCoach } = await import(join(HERE, '../../web/components/coach.js'));
  await bootRuntime({ settings: { intervals_api_key: 'k' } });

  let dots = false;
  const coach = createCoach(() => { dots ||= coach.state.thinking; });
  const activity = { id: 'a1', name: 'Run', date: '2026-03-02' };

  const reply = await coach.take('full', activity);
  if (chats.at(-1)?.isolated !== true) fail('isolated turn', 'a take must reach the worker as isolated');
  if (reply?.text !== 'The whole debrief.') fail('isolated turn', 'the sheet must still get the whole reply');
  if (coach.state.messages.length) fail('isolated turn', 'a take without a card must leave the chat alone');
  if (coach.state.replies) fail('isolated turn', 'a take without a card must not light the coach button');
  if (dots) fail('isolated turn', 'a take must not show the thinking dots in the chat');

  await coach.brief('quick', { handle: 'session 1', name: 'Run', date: '2026-03-02' });
  if (chats.at(-1)?.isolated !== true) fail('isolated turn', 'a brief must reach the worker as isolated');

  // A card is the one thing that crosses over, without the debrief around it.
  answer = { reply: 'The whole debrief.', proposals: [{ id: 'p1', tool: 'swap_planned_session' }] };
  const carded = await coach.take('quick', activity);
  const [cardMsg] = coach.state.messages;
  if (carded?.text !== 'The whole debrief.') fail('isolated card', 'the sheet must still get the whole reply');
  if (cardMsg?.proposals?.[0]?.proposal?.id !== 'p1') fail('isolated card', 'the card must reach the chat');
  if (cardMsg?.text.includes('whole debrief')) fail('isolated card', 'the debrief text must stay on its sheet');
  if (coach.state.replies !== 1) fail('isolated card', 'a card in the chat must light the coach button');

  // A message sent behind a take says so at once, since the take shows nothing.
  answer = { reply: 'Hello back.' };
  let open = null;
  release = new Promise((resolve) => { open = resolve; });
  const take = coach.take('quick', activity);
  await new Promise((resolve) => { setTimeout(resolve, 5); });
  const sent = coach.send('Hello');
  if (!coach.state.thinking) fail('chat behind a take', 'a waiting message must show the dots');
  open();
  release = null;
  await Promise.all([take, sent]);
  if (coach.state.thinking) fail('chat behind a take', 'the dots must end with the turn');
  if (chats.at(-1)?.isolated !== false) fail('chat turn', 'a message from the athlete stays in the conversation');
  if (coach.state.messages.at(-1)?.text !== 'Hello back.') fail('chat turn', 'a chat reply must join the chat');

  // An empty draft waits for nothing.
  coach.send('   ');
  if (coach.state.thinking) fail('chat turn', 'an empty message must not show the dots');
  console.log(`  · isolated takes              chat calls=${chats.length}`);
}

console.log('');
if (failures) {
  console.error(`FAILED: ${failures} assertion(s) failed`);
  process.exit(1);
}
console.log('OK: all planned-workout display assertions passed');
