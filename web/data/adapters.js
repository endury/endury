/* Endury — adapters: wire payloads → view shapes.
   Session (planned ⊕ actual merged) and the week model. Pure functions, no
   fetching, no DOM. */

import {
  fmtDur, fmtDist, fmtPace, fmtSwimPace, fmtTss, fmtDayLabel,
  fmtThresholdPace, fmtCss,
} from './format.js';

// Sport + kind normalization

const SPORT_MAP = {
  running: 'run', trail_running: 'run', treadmill_running: 'run',
  track_running: 'run', street_running: 'run',
  cycling: 'bike', road_biking: 'bike', mountain_biking: 'bike',
  gravel_cycling: 'bike', indoor_cycling: 'bike', virtual_ride: 'bike',
  swimming: 'swim', lap_swimming: 'swim', open_water_swimming: 'swim',
  strength_training: 'strength', rest: 'rest',
  // Raw intervals.icu activity-type strings (endury/intervals/models.py's
  // SPORT_TYPE_MAP deliberately leaves non-endurance types unmapped, so
  // calendar_projection.py's _activity_payload puts them on the wire verbatim).
  WeightTraining: 'strength',
};

export function normSport(raw) {
  return SPORT_MAP[raw] || 'other';
}

export const SPORT_LABEL = {
  swim: 'Swim', bike: 'Bike', run: 'Run', strength: 'Strength', rest: 'Rest',
  // Unrecognized intervals.icu activity types (Yoga, WeightTraining, …) and
  // the literal "other" the backend sends when an activity has no type at all.
  other: 'Other',
};

// Workout steps → display list + outline mini-bars

// Repeat blocks carry `iterations` + nested `steps` (endury/training/workout.py
// schema); there is no `repeat_count`/`reps` on the wire.
function repeatCount(step) {
  return step.iterations || 1;
}

/** Leaf steps in document order, expanding each repeat block ONCE — for
    picking a representative main step (interval sessions nest the work step
    inside a repeat). Not for outline shares, which expand N times. */
function leafSteps(steps) {
  const out = [];
  for (const s of steps || []) {
    if (s.type === 'repeat' && s.steps) out.push(...leafSteps(s.steps));
    else out.push(s);
  }
  return out;
}

/** A step's length to the second: "30s", "2min 30s", else fmtDur's "15min".
    fmtDur rounds to whole minutes, which turns a 30/15 into "1min"/"0min". */
function fmtStepSeconds(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s % 60 && s < 3600) return `${Math.floor(s / 60)}min ${s % 60}s`;
  return fmtDur(s);
}

function fmtStepDur(step) {
  const d = step.duration || {};
  if (d.duration_type === 'time' && d.value_seconds) return fmtStepSeconds(d.value_seconds);
  if (d.duration_type === 'distance' && d.value_meters) return fmtDist(d.value_meters);
  if (step.duration_seconds) return fmtStepSeconds(step.duration_seconds);
  if (step.duration_meters) return fmtDist(step.duration_meters);
  // Self-paced steps (open duration) get a cue instead of a blank badge.
  if (d.duration_type === 'open' || step.duration_type === 'open') return 'As needed';
  return '';
}

/** A step's length as the sheet's hero says it: "2:30", else fmtStepDur's. */
function fmtStepShort(step) {
  const sec = step.duration?.value_seconds || step.duration_seconds;
  if (sec > 60 && sec < 3600 && sec % 60) return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
  return fmtStepDur(step);
}

/** minutes (pace) → "m:ss" tick label. */
function fmtPaceTick(minutes) {
  const total = Math.round(minutes * 60);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** seconds → compact elapsed-time tick ("20:00", "1:14"). */
function fmtTimeTick(sec) {
  if (!sec) return '0';
  if (sec >= 3600) {
    const m = Math.round(sec / 60);   // round total minutes first — no ":60"
    return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}`;
  }
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const THRESHOLD_PERCENT_METRIC_LABEL = { pace: 'pace', power: 'FTP', hr: 'LTHR' };

/** "Z3 · band", "Z3" or the band alone: a target whose band crosses the
    athlete's own intervals.icu zones carries no zone number. */
function zoneLabel(zone, band) {
  return [zone != null ? `Z${zone}` : '', band].filter(Boolean).join(' · ');
}

function fmtTarget(target) {
  if (!target) return '';
  switch (target.target_type) {
    case 'heart_rate_zone': {
      let band = '';
      if (target.low_bpm && target.high_bpm) band = `${target.low_bpm}–${target.high_bpm} bpm`;
      else if (target.high_bpm) band = `≤${target.high_bpm} bpm`;
      else if (target.low_bpm) band = `≥${target.low_bpm} bpm`;
      return zoneLabel(target.zone, band);
    }
    case 'heart_rate':
      return target.low_bpm && target.high_bpm
        ? `${target.low_bpm}–${target.high_bpm} bpm` : '';
    case 'pace': {
      if (target.low_min_per_km && target.high_min_per_km) {
        return `${fmtPaceTick(target.low_min_per_km)}–${fmtPaceTick(target.high_min_per_km)} /km`;
      }
      return '';
    }
    case 'pace_zone': return target.zone != null ? `pace Z${target.zone}` : '';
    case 'swim_pace_zone': {
      const band = target.low_sec_per_100m && target.high_sec_per_100m
        ? `${fmtTimeTick(target.low_sec_per_100m)}–${fmtTimeTick(target.high_sec_per_100m)} /100m` : '';
      return zoneLabel(target.zone, band);
    }
    case 'swim_pace':
      return target.low_sec_per_100m && target.high_sec_per_100m
        ? `${fmtTimeTick(target.low_sec_per_100m)}–${fmtTimeTick(target.high_sec_per_100m)} /100m` : '';
    case 'power_zone': {
      const band = target.low_watts && target.high_watts ? `${target.low_watts}–${target.high_watts} W` : '';
      return zoneLabel(target.zone, band);
    }
    case 'power':
      return target.low_watts && target.high_watts
        ? `${target.low_watts}–${target.high_watts} W` : '';
    case 'rpe': return `RPE ${target.rpe}`;
    case 'cadence':
      return target.low_rpm && target.high_rpm
        ? `${target.low_rpm}–${target.high_rpm} rpm` : '';
    // Coach-scheduled ad-hoc workouts (endury/planning/calendar_projection.py's
    // _ad_hoc_step_target): the raw threshold-percent band a step keeps when no
    // live threshold could resolve it into pace/HR/power above, metric-labelled
    // since there's no zone number to carry the unit the way the other cases do.
    case 'threshold_percent': {
      const label = THRESHOLD_PERCENT_METRIC_LABEL[target.metric] || target.metric;
      return target.low_percent != null && target.high_percent != null
        ? `${target.low_percent}–${target.high_percent}% ${label}` : '';
    }
    default: return '';
  }
}

/** One step → display shape {type, name, dur, short, sec, target, reps?, children?}. */
function toDisplayStep(step) {
  if (step.type === 'repeat' && step.steps) {
    return {
      type: 'repeat',
      reps: repeatCount(step),
      sec: stepSeconds(step),
      children: step.steps.map(toDisplayStep),
    };
  }
  let target = fmtTarget(step.target);
  // Runs: the pace range in additional_targets is the most actionable line.
  const pace = (step.additional_targets || []).find((t) => t.target_type === 'pace');
  if (pace && step.target?.target_type?.startsWith('heart_rate')) {
    const p = fmtTarget(pace);
    if (p) target = target ? `${target.split(' · ')[0]} · ${p}` : p;
  }
  // Cadence drills/sprints: the rpm band rides along whatever the primary is.
  const cadence = (step.additional_targets || []).find((t) => t.target_type === 'cadence');
  if (cadence) {
    const c = fmtTarget(cadence);
    if (c) target = target ? `${target} · ${c}` : c;
  }
  if (!target && step.secondary_target) {
    target = fmtTarget(step.secondary_target);
  } else if (
    target && /^(pace )?Z\d+$/.test(target)
    && step.secondary_target?.target_type === 'rpe'
  ) {
    // A bare zone label with no concrete band (no thresholds/zones yet)
    // isn't actionable — put the RPE cue next to it.
    target = `${target} · ${fmtTarget(step.secondary_target)}`;
  }
  return {
    type: step.type,
    name: step.description || step.type,
    dur: fmtStepDur(step),
    short: fmtStepShort(step),
    sec: stepSeconds(step),
    target,
  };
}

function stepSeconds(step) {
  if (step.type === 'repeat' && step.steps) {
    return repeatCount(step) * step.steps.reduce((s, c) => s + stepSeconds(c), 0);
  }
  return step.duration_seconds
    || step.duration?.value_seconds
    || (step.duration_meters ? step.duration_meters / 2 : 0)  // rough: for outline shares only
    || 300;
}

/** Flatten (expanding repeats) → outline segments [{type, frac}] for mini-bars.
    A block of more than 8 reps draws 8, each stretched so the block keeps
    its true length; the whole workout is drawn, cool-down included. */
function outlineSegments(steps) {
  const flat = [];
  const walk = (list, scale) => {
    for (const s of list) {
      if (s.type === 'repeat' && s.steps) {
        const reps = Math.min(repeatCount(s), 8);
        for (let i = 0; i < reps; i += 1) walk(s.steps, (scale * repeatCount(s)) / reps);
      } else {
        flat.push({ type: s.type, sec: stepSeconds(s) * scale });
      }
    }
  };
  walk(steps || [], 1);
  const total = flat.reduce((s, x) => s + x.sec, 0) || 1;
  return flat.map((x) => ({ type: x.type, frac: x.sec / total }));
}

/** A target as numbers a recorded rep can be read against, or null. */
function bandOf(target) {
  const t = target || {};
  const band = (metric, low, high) => (low && high ? { metric, low, high } : null);
  switch (t.target_type) {
    case 'pace': return band('pace', t.low_min_per_km * 60, t.high_min_per_km * 60);
    case 'swim_pace':
    case 'swim_pace_zone': return band('swim', t.low_sec_per_100m, t.high_sec_per_100m);
    case 'heart_rate':
    case 'heart_rate_zone': return band('hr', t.low_bpm, t.high_bpm);
    case 'power':
    case 'power_zone': return band('power', t.low_watts, t.high_watts);
    default: return null;
  }
}

/** The one band all of a workout's work reps are asked to hold, with how many
    reps and how the plan says it, or null: a pyramid or a rep without a
    measurable band has no single band to read the session against. */
function workBand(steps) {
  const work = [];
  const walk = (list, times) => {
    for (const s of list || []) {
      if (s.type === 'repeat' && s.steps) walk(s.steps, times * repeatCount(s));
      else if (s.type === 'interval') work.push({ step: s, times });
    }
  };
  walk(steps, 1);
  if (!work.length) return null;
  const bands = work.map(({ step }) => bandOf(step.target));
  const [first] = bands;
  if (!first || bands.some((b) => JSON.stringify(b) !== JSON.stringify(first))) return null;
  const reps = work.reduce((sum, { times }) => sum + times, 0);
  const lengths = new Set(work.map(({ step }) => fmtStepShort(step)));
  const what = lengths.size === 1 ? `${reps} × ${[...lengths][0]}` : `${reps} reps`;
  return { ...first, reps, plan: `${what} at ${fmtTarget(work[0].step.target)}` };
}

// Session (planned ⊕ actual)

/** intervals.icu's feel scale, 1 (best) … 5 (worst); the same words as the coach's FEEL_WORDS (activity_tools.py). */
const FEEL_WORDS = { 1: 'strong', 2: 'good', 3: 'normal', 4: 'poor', 5: 'weak' };

const ZONE_METRIC = { heart_rate_zone: 'hr', pace_zone: 'pace', swim_pace_zone: 'pace' };

/** {plannedMin, actualMin} of work in zone 3 and above — or null. Honest or
    absent: only when every leaf step is timed and names a zone number of one
    metric (heart rate or pace) and the activity carries that metric's time
    in zones. A bare zone band, a power zone or a distance step gives null. */
function hardMinutes(steps, act) {
  let metric = null;
  let plannedSec = 0;
  const walk = (list, times) => {
    for (const s of list) {
      if (s.type === 'repeat' && s.steps) {
        if (!walk(s.steps, times * repeatCount(s))) return false;
        continue;
      }
      const m = ZONE_METRIC[s.target?.target_type];
      const sec = s.duration_seconds || s.duration?.value_seconds;
      if (!m || s.target.zone == null || !sec || (metric && m !== metric)) return false;
      metric = m;
      if (s.target.zone >= 3) plannedSec += sec * times;
    }
    return true;
  };
  if (!steps?.length || !walk(steps, 1)) return null;
  const zones = act[`${metric}_zone_times`];
  if (!Array.isArray(zones) || !zones.length) return null;
  const actualSec = zones.slice(2).reduce((a, b) => a + (b || 0), 0);
  return { plannedMin: Math.round(plannedSec / 60), actualMin: Math.round(actualSec / 60) };
}

/** The credit intervals.icu's API terms ask for on Garmin-sourced data: the
    device by name, or null for another source. */
function garminLabel(act) {
  if (/garmin/i.test(act.device_name || '')) return act.device_name;
  return act.source === 'GARMIN_CONNECT' ? 'Garmin' : null;
}

/** What was done against what the plan asked, plus the watch's own verdict. */
function executionFor(act, plannedSec, workout) {
  return {
    durationSec: act.duration ?? null,
    plannedSec,
    durationPct: plannedSec && act.duration != null
      ? Math.round((act.duration / plannedSec) * 100) : null,
    feel: act.feel ?? null,
    feelWord: FEEL_WORDS[act.feel] || null,
    rpe: act.rpe ?? null,
    // intervals.icu shows only source, start and a note for an activity it
    // does not pass on (Strava's today): ridden, not missed.
    withheld: !act.type && act.duration == null,
    hard: hardMinutes(workout?.steps, act),
  };
}

function actualStatsFor(sport, act, planned) {
  const stats = [
    { l: 'Duration', v: fmtDur(act.duration), plan: planned?.duration_sec ? fmtDur(planned.duration_sec) : undefined },
    { l: 'Distance', v: fmtDist(act.distance) },
  ];
  if (sport === 'bike') {
    stats.push({ l: 'Avg Power', v: act.average_power ? `${act.average_power} W` : '—' });
  } else if (sport === 'run') {
    stats.push({ l: 'Pace', v: fmtPace(act.average_speed_mps) });
  } else if (sport === 'swim') {
    stats.push({ l: 'Pace', v: fmtSwimPace(act.average_speed_mps) });
  }
  return stats;
}

function recordedExtrasFor(sport, act) {
  const rec = [];
  const runOrRide = sport === 'run' || sport === 'bike';
  if (act.average_hr) rec.push({ l: 'Avg HR', v: `${Math.round(act.average_hr)} bpm` });
  if (act.max_hr) rec.push({ l: 'Max HR', v: `${Math.round(act.max_hr)} bpm` });
  if (sport === 'run' && act.gap_mps) rec.push({ l: 'GAP', v: fmtPace(act.gap_mps) });
  if (sport === 'bike' && act.normalized_power) rec.push({ l: 'Weighted power', v: `${act.normalized_power} W` });
  if (sport === 'bike' && act.intensity_factor) rec.push({ l: 'Intensity', v: act.intensity_factor.toFixed(2) });
  if (runOrRide && act.decoupling_pct != null) rec.push({ l: 'Decoupling', v: `${act.decoupling_pct.toFixed(1)} %` });
  // A run's cadence comes per leg; steps per minute count both.
  if (runOrRide && act.average_cadence) {
    rec.push({ l: 'Cadence', v: sport === 'run' ? `${Math.round(act.average_cadence * 2)} spm` : `${Math.round(act.average_cadence)} rpm` });
  }
  if (act.elevation_gain_meters) rec.push({ l: 'Elev', v: `${Math.round(act.elevation_gain_meters)} m` });
  if (act.calories) rec.push({ l: 'Calories', v: String(Math.round(act.calories)) });
  return rec;
}

function primaryIntensity(workout) {
  const steps = leafSteps(workout?.steps);
  const main = steps.find((s) => s.type === 'interval') || steps[0];
  if (!main) return '—';
  const t = fmtTarget(main.target) || fmtTarget(main.secondary_target);
  return t ? t.split(' · ')[0] : '—';
}

/** Merge one /calendar/data plans[] row (+ its matched activity) → Session.
    Also accepts the generate-preview wire shape (serialize_planned_session), which
    carries slot_id + workout.name instead of id + description + status. */
export function toSession(planRow, activitiesById, syncBySlot) {
  const sport = normSport(planRow.sport);
  const act = planRow.matched_activity_id
    ? activitiesById[planRow.matched_activity_id] : null;
  const plannedTss = planRow.target_tss ?? planRow.planned_metrics?.tss ?? null;
  const durationSec = planRow.planned_metrics?.duration_sec
    ?? (planRow.duration_minutes ? planRow.duration_minutes * 60 : null);
  const plannedDistM = planRow.planned_metrics?.distance_m
    ?? planRow.distance_meters ?? null;
  const description = planRow.description ?? planRow.workout?.name ?? '';

  const session = {
    id: String(planRow.id ?? planRow.slot_id),
    date: planRow.date,
    sport,
    kind: planRow.family || null,
    activityKey: planRow.activity_key || null,
    title: `${SPORT_LABEL[sport]} · ${description}`,
    tss: plannedTss,
    durationSec,
    note: planRow.coaching_intent || null,
    cues: planRow.coaching_notes || [],
    why: planRow.why_details || [],
    stats: [
      { l: 'Duration', v: fmtDur(durationSec) },
      // Swim steps are distance-based, so the planned total is real there;
      // run/bike steps are time-based and carry no planned distance.
      ...(plannedDistM ? [{ l: 'Distance', v: fmtDist(plannedDistM) }] : []),
      { l: 'Load', v: fmtTss(plannedTss) },
      { l: 'Intensity', v: primaryIntensity(planRow.workout) },
    ],
    workout: planRow.workout
      ? { name: planRow.workout.name, steps: (planRow.workout.steps || []).map(toDisplayStep) }
      : null,
    outline: outlineSegments(planRow.workout?.steps),
    workBand: workBand(planRow.workout?.steps),
    status: planRow.status,
    // Outside the plan's load: planned totals leave it out.
    optional: Boolean(planRow.optional),
    done: planRow.status === 'completed' || Boolean(act),
    missed: planRow.status === 'missed',
    skipped: planRow.status === 'skipped',
    activityId: planRow.matched_activity_id || null,
    activityUrl: planRow.intervals_activity_url || null,
    // Only upcoming sessions are classified server-side, so a past
    // session simply has no entry and keeps its DONE/MISSED badge.
    sync: (syncBySlot || {})[planRow.slot_id] || null,
    anchor: false,   // stamped per-week in buildWeek
  };

  if (act) addActual(session, act, planRow.workout, planRow.planned_metrics);
  return session;
}

/** Stamp the recorded side onto a planned session (planned ⊕ actual).
    `workout` is the wire workout (raw steps), not the display one. */
function addActual(session, act, workout, plannedMetrics) {
  const actualTss = act.tss ?? null;
  session.actualTss = actualTss;
  session.actual = {
    compliance: session.tss && actualTss
      ? Math.round((actualTss / session.tss) * 100)
      : null,
    execution: executionFor(act, session.durationSec, workout),
    stats: actualStatsFor(session.sport, act, plannedMetrics),
    recorded: recordedExtrasFor(session.sport, act),
    type: act.type ?? null,
    garmin: garminLabel(act),
  };
}

/** An activity with no planned session → actual-only Session (merged model,
    never a separate log). */
function activityOnlySession(act) {
  const sport = normSport(act.sport);
  return {
    id: `act-${act.id}`,
    date: (act.start_time || '').slice(0, 10),
    sport,
    kind: null,
    activityKey: null,
    title: `${SPORT_LABEL[sport]} · ${act.name}`,
    tss: null,
    durationSec: act.duration ?? null,
    note: null,
    stats: [],
    workout: null,
    outline: [],
    status: 'unplanned',
    done: true,
    missed: false,
    activityId: act.id,
    anchor: false,
    actualTss: act.tss ?? null,
    actual: {
      compliance: null,
      execution: executionFor(act, null, null),
      stats: actualStatsFor(sport, act, null),
      recorded: recordedExtrasFor(sport, act),
      type: act.type ?? null,
      garmin: garminLabel(act),
    },
  };
}

/** One workout that lives only on intervals.icu, never in the local plan — a
    coach-scheduled ad-hoc workout, or a finished session of a plan the app
    has since replaced or deleted — → Session, planned ⊕ actual when paired.
    `eventId` keeps it out of the local-plan verbs (reschedule/drag PATCH the
    plans table by row id, which these rows don't have) and pairs it by the
    event; status 'adhoc' marks an open coach extra, the one kind the detail
    sheet can remove. Sync state comes from sync-status, which keys these
    rows by external_id. */
function calendarOnlySession(e, activitiesById, syncBySlot) {
  const sport = normSport(e.sport);
  const act = e.matched_activity_id ? activitiesById[e.matched_activity_id] : null;
  const session = {
    id: `event-${e.id}`,
    externalId: e.external_id,
    eventId: e.id,
    // A coach extra (no plan behind it) never takes the week's anchor.
    extra: e.plan_id == null,
    date: e.date,
    sport,
    kind: null,
    activityKey: null,
    title: `${SPORT_LABEL[sport]} · ${e.name}`,
    tss: e.tss ?? null,
    durationSec: e.duration ?? null,
    note: e.note || null,
    cues: [],
    why: [],
    stats: [
      { l: 'Duration', v: fmtDur(e.duration) },
      { l: 'Load', v: fmtTss(e.tss) },
    ],
    workout: e.workout
      ? { name: e.workout.name, steps: (e.workout.steps || []).map(toDisplayStep) }
      : null,
    outline: outlineSegments(e.workout?.steps),
    workBand: workBand(e.workout?.steps),
    status: e.status,
    done: e.status === 'completed' || Boolean(act),
    missed: e.status === 'missed',
    activityId: e.matched_activity_id || null,
    activityUrl: e.intervals_activity_url || null,
    sync: (syncBySlot || {})[e.external_id] || null,
    anchor: false,
  };
  if (act) addActual(session, act, e.workout, { duration_sec: e.duration ?? null });
  return session;
}

// Week model (calendar unit for both shells)

/** /calendar/data payload for one Mon–Sun range → week model. The payload
    may span a wider range; only rows inside weekDays count. */
export function buildWeek(payload, weekStart, weekDays, syncBySlot) {
  const inWeek = new Set(weekDays);
  const activitiesById = {};
  for (const a of payload.activities || []) activitiesById[a.id] = a;

  const sessions = [
    ...(payload.plans || [])
      .filter((p) => inWeek.has(p.date))
      .map((p) => toSession(p, activitiesById, syncBySlot)),
    ...(payload.calendar_only || [])
      .filter((e) => inWeek.has(e.date))
      .map((e) => calendarOnlySession(e, activitiesById, syncBySlot)),
  ];
  const matchedIds = new Set(sessions.map((s) => s.activityId).filter(Boolean));
  for (const a of payload.activities || []) {
    if (matchedIds.has(a.id)) continue;
    const s = activityOnlySession(a);
    if (inWeek.has(s.date)) sessions.push(s);
  }

  // Anchor = the biggest planned session of the week (gold emphasis).
  // Coach-scheduled extras never take it from the plan's key session.
  let anchor = null;
  for (const s of sessions) {
    if (s.extra || s.optional) continue;
    if (s.tss != null && (!anchor || s.tss > anchor.tss)) anchor = s;
  }
  if (anchor) anchor.anchor = true;

  const days = weekDays.map((date) => ({
    date,
    sessions: sessions
      .filter((s) => s.date === date)
      .sort((a, b) => (b.tss ?? 0) - (a.tss ?? 0)),
  }));

  const plannedTss = sessions.reduce((t, s) => t + (s.optional ? 0 : s.tss ?? 0), 0);
  const doneTss = sessions.reduce((t, s) => t + (s.done ? (s.actualTss ?? s.tss ?? 0) : 0), 0);

  const currentPhase = (payload.phases || []).find((p) => p.is_current) || null;
  const race = (payload.races || [])[0] || null;

  return {
    start: weekStart,
    days,
    sessions,
    plannedTss: Math.round(plannedTss),
    doneTss: Math.round(doneTss),
    ringPct: plannedTss ? Math.min(1, doneTss / plannedTss) : 0,
    phase: currentPhase,
    // The whole plan's phase ranges ride along on every payload, so the phase
    // a week sits in is a lookup — `phase` above is today's, for the headers.
    phases: payload.phases || [],
    weekPhase: (payload.phases || []).find(
      (p) => p.start_date <= weekStart && weekStart <= p.end_date,
    ) || null,
    races: payload.races || [],
    race,
    activePlan: payload.active_plan || null,
    rawPlans: payload.plans || [],
  };
}

// Planning Inputs panel (GET /api/inputs)

const INPUTS_SPORT_ORDER = ['running', 'cycling', 'swimming'];
const INPUTS_SPORT_LABELS = { running: 'Running', cycling: 'Cycling', swimming: 'Swimming' };
const INPUTS_SOURCE_LABELS = {
  history: 'From training history', self_report: 'Self-reported', default: 'Default estimate',
};

/** {ftp,lthr,threshold_pace_sec_per_km,css_sec_per_100m} → the threshold
    rows relevant to one sport, in display order. */
function inputsThresholdRows(sport, t) {
  const rows = [];
  if (sport === 'running' || sport === 'cycling') {
    rows.push(['Threshold HR', t.lthr != null ? `${t.lthr} bpm` : '—']);
  }
  if (sport === 'cycling') rows.push(['FTP', t.ftp != null ? `${t.ftp} W` : '—']);
  if (sport === 'running') rows.push(['Threshold pace', fmtThresholdPace(t.threshold_pace_sec_per_km)]);
  if (sport === 'swimming') rows.push(['Swim CSS', fmtCss(t.css_sec_per_100m)]);
  return rows;
}

function mmss(sec) {
  if (!sec) return '';
  const total = Math.round(sec);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** The thresholds one sport's edit form sets: the ones its plan targets use,
    bounded like put_thresholds. A derived value starts empty, so saving the
    form never writes our estimate to intervals.icu. */
function inputsEditFields(sport, t) {
  const derived = (t.derived || []).length > 0;
  const lthr = {
    name: 'lthr_bpm', label: 'Threshold HR', unit: 'bpm', min: 80, max: 230,
    value: t.lthr != null && !derived ? String(t.lthr) : '',
  };
  if (sport === 'cycling') {
    return [{ name: 'ftp_watts', label: 'FTP', unit: 'W', min: 50, max: 1000,
      value: t.ftp != null ? String(t.ftp) : '' }, lthr];
  }
  if (sport === 'running') {
    return [{ name: 'threshold_pace_sec_per_km', label: 'Threshold pace', unit: 'min/km',
      pace: true, value: mmss(t.threshold_pace_sec_per_km) }, lthr];
  }
  return [{ name: 'css_sec_per_100m', label: 'Swim CSS', unit: 'min/100m',
    pace: true, value: mmss(t.css_sec_per_100m) }];
}

function fmtCtlAtl(ctl, atl) {
  return `${ctl == null ? '—' : ctl} / ${atl == null ? '—' : atl}`;
}

/** GET /api/inputs payload → Planning Inputs panel view model. Also accepts
    a synthetic {available:false, reason} shape for a frontend fetch error,
    so the same "setup card" renders for both a degraded backend response
    and a network failure. */
export function toInputsPanel(payload) {
  if (!payload || !payload.available) {
    const reason = payload?.reason || 'intervals.icu is unavailable.';
    const missingKey = reason.includes('not configured');
    const authRejected = reason.includes('rejected Endury');
    return {
      available: false,
      reason,
      hint: missingKey
        ? 'Add your intervals.icu API key in Settings.'
        : authRejected
          ? 'Open Endury’s Settings and connect intervals.icu again, or paste a new key from intervals.icu → Settings → Developer Settings.'
          : 'Check your network connection and intervals.icu’s status, then reopen this panel.',
    };
  }
  const sports = INPUTS_SPORT_ORDER
    .filter((key) => payload.sports?.[key])
    .map((key) => {
      const s = payload.sports[key];
      return {
        key,
        label: INPUTS_SPORT_LABELS[key],
        thresholds: inputsThresholdRows(key, s.thresholds || {}),
        edit: inputsEditFields(key, s.thresholds || {}),
        derived: s.thresholds?.derived || [],
        hint: key === 'running' && s.thresholds?.threshold_pace_sec_per_km == null
          ? 'No threshold pace on intervals.icu — runs show heart-rate and RPE targets only. '
            + 'Set one with Edit, or tell the coach a recent race time and it will propose one.'
          : '',
        hours: s.recent_weekly_hours != null ? `${s.recent_weekly_hours}h/week` : '—',
        elevation: s.recent_weekly_elevation_m != null ? `${s.recent_weekly_elevation_m} m/week` : '—',
        longest: s.longest_recent_min
          ? (s.longest_recent_min >= 60
            ? `${Math.floor(s.longest_recent_min / 60)}h ${s.longest_recent_min % 60}m`
            : `${s.longest_recent_min} min`)
          : '—',
        ctlAtl: fmtCtlAtl(s.ctl, s.atl),
        sourceLabel: INPUTS_SOURCE_LABELS[s.source] || s.source,
      };
    });
  const combined = payload.combined || {};
  const wellness = payload.wellness_last7 || {};
  return {
    available: true,
    sports,
    combined: fmtCtlAtl(
      combined.intervals_ctl != null ? Math.round(combined.intervals_ctl) : null,
      combined.intervals_atl != null ? Math.round(combined.intervals_atl) : null,
    ),
    wellness: {
      sleepAvg: wellness.sleep_hours_avg != null ? `${wellness.sleep_hours_avg}h` : '—',
      hrv: wellness.hrv_latest != null ? `${Math.round(wellness.hrv_latest)} ms` : '—',
      restingHr: wellness.resting_hr_latest != null ? `${Math.round(wellness.resting_hr_latest)} bpm` : '—',
    },
    pushErrors: (payload.push_errors || []).map((e) => ({
      name: e.name || 'Workout',
      date: e.date ? fmtDayLabel(String(e.date).slice(0, 10)) : '—',
      lines: (e.errors || []).map((err) => `${err.service}: ${err.message}`),
    })),
    // Empty is a real finding, not missing data: pushed plans then live on
    // intervals.icu and never reach a watch or trainer.
    uploadDestinations: payload.upload_destinations || [],
  };
}
