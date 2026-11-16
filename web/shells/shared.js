/* Endury — logic shared by the web and mobile shells: goal catalogs,
   plan-editor state/model builders, and the plan-lifecycle write paths
   (move/apply/refresh/archive). Rendering stays in components/; composition
   stays in each shell. */

import { addDays, fmtDayLabel, fmtDayLabelYear, mondayOf, parseDay } from '../data/format.js';
import { api, plannerSyncStatus } from '../data/api.js';
import { normSport, toInputsPanel } from '../data/adapters.js';
import { showToast } from '../components/toast.js';
import { openConfirm } from '../components/modal.js';
import { esc } from '../components/util.js';
import { pushToastText } from '../components/push_status.js';
import { bindTraceScrub, renderActivityTrace } from '../components/activity_trace.js';
import {
  raceLabel, renderActivityDebrief, renderActivityTakes, renderVerdict, takeSummary, verdictSummary,
} from '../components/activity_debrief.js';
import { briefSummary, renderBriefSheet, renderCoachBrief } from '../components/session_brief.js';
import { addSheetPages, setSummary, sheetBackLabel } from '../components/sheet_pages.js';
import { renderInputsPanel } from '../components/inputs_panel.js';

export const GOAL_LABELS = {
  ironman: 'Full-distance Triathlon', half_ironman: 'Half-distance Triathlon', olympic_triathlon: 'Standard-distance Triathlon',
  sprint_triathlon: 'Sprint Triathlon', running_marathon: 'Marathon',
  running_half_marathon: 'Half Marathon', running_10k: '10K', running_5k: '5K',
  trail_20k: 'Trail 20K', trail_50k: 'Trail 50K',
  trail_100k: 'Trail 100K',
  running_maintenance: 'Maintenance', trail_maintenance: 'Maintenance',
  triathlon_maintenance: 'Maintenance',
};
const TRI_GOALS = ['ironman', 'half_ironman', 'olympic_triathlon', 'sprint_triathlon', 'triathlon_maintenance'];
const RUN_GOALS = ['running_marathon', 'running_half_marathon', 'running_10k', 'running_5k', 'running_maintenance'];
// Trail is its own wizard family, not a tail on the road list: the goals
// share SportType.RUNNING on the backend, but they are a different
// discipline to pick and nine pills in one row read as noise. Ascending
// distance, matching the GoalType enum order.
const TRAIL_GOALS = ['trail_20k', 'trail_50k', 'trail_100k', 'trail_maintenance'];

// The wizard's three families. Ordering here is the row's own ordering.
const FAMILY_GOALS = { triathlon: TRI_GOALS, running: RUN_GOALS, trail: TRAIL_GOALS };
const FAMILY_LABELS = { triathlon: 'Triathlon', running: 'Running', trail: 'Trail Running' };

/** The goals one family offers (unknown family → triathlon, the seed goal's). */
function goalsForFamily(family) {
  return FAMILY_GOALS[family] || TRI_GOALS;
}

/** The family a goal belongs to — the wizard's grouping, not a backend sport. */
function familyForGoal(goal) {
  return Object.keys(FAMILY_GOALS).find((f) => FAMILY_GOALS[f].includes(goal)) || 'triathlon';
}

/** The tiers on offer for a goal, from bootstrap's goal catalog (the menus
    own it). null = never read: nothing is hidden then, and the backend
    still refuses a plan that is not on offer. */
const offeredTiers = (e, goal) => e.goalMaturity?.[goal]?.offered ?? null;
const isOfferedGoal = (e, goal) => (offeredTiers(e, goal)?.length ?? 1) > 0;

/** Move the editor onto a plan on offer: the goal to the family's first
    offered one (else any family's), the tier to the goal's first offered
    one. Runs from `editorModel`, before every render, so whatever set the
    goal (a family switch, the active plan, the defaults) the wizard never
    shows a plan it cannot build. */
function snapToOffered(e) {
  if (!isOfferedGoal(e, e.goal)) {
    const next = goalsForFamily(e.family).find((g) => isOfferedGoal(e, g))
      || Object.values(FAMILY_GOALS).flat().find((g) => isOfferedGoal(e, g));
    if (next) {
      e.goal = next;
      e.family = familyForGoal(next);
    }
  }
  const tiers = offeredTiers(e, e.goal);
  if (tiers?.length && !tiers.includes(e.ambition)) e.ambition = tiers[0];
}

// The goal a family preselects when it is switched to. Trail picks the 50K
// rather than its list head: the 20K blueprint has the thinner evidence.
const FAMILY_DEFAULT_GOAL = { triathlon: TRI_GOALS[0], running: RUN_GOALS[0], trail: 'trail_50k' };
export const defaultGoalForFamily = (family) => FAMILY_DEFAULT_GOAL[family] || TRI_GOALS[0];

// The goal type a freshly added tune-up race starts as, per family.
const FAMILY_TUNEUP_GOAL = { triathlon: 'olympic_triathlon', running: 'running_10k', trail: 'trail_20k' };
export const defaultTuneUpGoalFor = (family) => FAMILY_TUNEUP_GOAL[family] || 'olympic_triathlon';

// UTMB km-effort taxonomy: trail categories are keyed by
// distance_km + gain_m / 100, not by distance (mirrors the backend's
// GoalType doctrine) — a 45 km race with 4,000 m of gain is a 50K-band
// problem, not a marathon-plus. From 170 km-effort there is no plan (null):
// Endury does not plan the 100-mile band.
const trailKmEffort = (km, gainM) => km + gainM / 100;
function trailBandForEffort(kmEffort) {
  if (kmEffort >= 170) return null;
  if (kmEffort >= 100) return 'trail_100k';
  if (kmEffort >= 50) return 'trail_50k';
  return 'trail_20k';
}

/** The wizard's optional race-profile inputs → suggested trail band, or
    null until both fields hold usable numbers. Comma decimals are read
    as dot decimals, same as the self-assessment fields (assessNum). */
function trailProfileSuggestion(editor) {
  const num = (raw) => {
    const s = String(raw ?? '').trim();
    if (!s) return null;
    const v = Number(s.replace(',', '.'));
    return Number.isFinite(v) ? v : null;
  };
  const km = num(editor.trailKm);
  const gain = num(editor.trailGain);
  if (km === null || km <= 0 || gain === null || gain < 0) return null;
  const effort = trailKmEffort(km, gain);
  return { effort, goal: trailBandForEffort(effort) };
}

/** Hint line under the race-profile fields. The suggestion reads as the
    plan only while it is also the selected goal; when the athlete has a
    different pill selected it stays visibly a suggestion, so the hint
    never contradicts the highlighted pill (stale-state trap on family/
    goal switches). */
function trailProfileHint(editor) {
  const s = trailProfileSuggestion(editor);
  if (!s) return '';
  if (!s.goal) return `≈ ${Math.round(s.effort)} km-effort — longer than any Endury trail plan`;
  const label = GOAL_LABELS[s.goal];
  if (!isOfferedGoal(editor, s.goal)) {
    return `≈ ${Math.round(s.effort)} km-effort — a ${label} race; Endury is not offering that plan at the moment`;
  }
  return s.goal === editor.goal
    ? `≈ ${Math.round(s.effort)} km-effort → ${label} plan`
    : `≈ ${Math.round(s.effort)} km-effort — suggests ${label}`;
}

/** Keystroke handler for the wizard's trail race-profile fields: updates
    editor state, patches the km-effort hint in place, and re-aims the
    goal pills at the suggested band — no re-render, so focus survives
    (same reason the self-assessment fields avoid re-rendering). Returns
    true when the input was one of the trail fields.

    Both shells route every editor `input` event through this one call, so
    the hold-volume checkbox is read here too rather than growing a
    second branch in each shell. */
export function handleTrailProfileInput(editor, input) {
  if (input.dataset.action === 'hold-volume') {
    editor.holdVolume = input.checked === true;
    return true;
  }
  const key = { 'trail-km': 'trailKm', 'trail-gain': 'trailGain' }[input.dataset.action];
  if (!key) return false;
  editor[key] = input.value.trim();
  const s = trailProfileSuggestion(editor);
  // Auto-select the suggested band only while the goal is still the
  // default — a pill the athlete clicked by hand (goalPickedByHand) is
  // never silently overwritten by typing in the optional fields.
  if (s && s.goal && s.goal !== editor.goal && !editor.goalPickedByHand
    && isOfferedGoal(editor, s.goal)) {
    editor.goal = s.goal;
    editor.shortHorizonAck = false;
    editor.experimentalAck = false;
    document.querySelectorAll('[data-action="set-goal"]').forEach((b) => {
      b.setAttribute('aria-checked', String(b.dataset.goal === editor.goal));
    });
  }
  const hintEl = document.querySelector('[data-trail-hint]');
  if (hintEl) hintEl.textContent = trailProfileHint(editor);
  return true;
}

/** Chip tap on a stored race (step 10's "Your races" row, `GET /api/races`):
    fills the A-race when the editor doesn't have one yet — family + goal
    only when the race's goal_type is one the wizard knows, the date always,
    and any stored trail profile into the race-profile fields (a race whose
    plan is not on offer fills nothing and says so) — or, when an
    A-race is already set and this race falls before it, adds it as a
    tune-up at its own stored priority (never re-defaulted to "C" the way
    the "+ Add" button does) — except a stored "A", which joins as a B:
    two A-races is a goal set the planner refuses outright. A chip whose
    race is not earlier than an already-set A-race, or one already added
    from this same chip or already present at the same date and goal,
    does nothing.

    Mutates the editor in place; both shells re-render after calling it. */
export function applyRaceChip(editor, race) {
  // A maintenance block has no A race to fill: every race joins it as a
  // tune-up, and the block's own end is checked when the plan is built.
  const maintenance = isMaintenanceGoal(editor.goal);
  if (!editor.aRaceDate && !maintenance) {
    const known = race.goal_type && GOAL_LABELS[race.goal_type];
    if (known && !isOfferedGoal(editor, race.goal_type)) {
      showToast(`No ${GOAL_LABELS[race.goal_type]} plan is on offer at the moment.`);
      return;
    }
    if (known) {
      editor.family = familyForGoal(race.goal_type);
      editor.goal = race.goal_type;
      editor.goalPickedByHand = true;
    }
    editor.aRaceDate = race.race_date;
    if (race.distance_km != null) editor.trailKm = String(race.distance_km);
    if (race.climb_m != null) editor.trailGain = String(race.climb_m);
    editor.shortHorizonAck = false;
    editor.experimentalAck = false;
    return;
  }
  if (!maintenance && race.race_date >= editor.aRaceDate) {
    showToast(`${race.name} is not before your A race, and a plan ends at its A race.`);
    return;
  }
  const goalType = race.goal_type || defaultTuneUpGoalFor(editor.family);
  const id = `race-${race.id}`;
  // upsert_race stores "A" by default, so a second A-race is easy to end up
  // with — and the pipeline rejects that goal set ("A plan builds toward
  // one A race"). An earlier race can only be a tune-up, so it joins at
  // add_race_to_plan's own default priority instead.
  const priority = race.priority === 'A' ? 'B' : race.priority;
  // By id as well as by (date, goal): the row's date is editable, and a
  // second push under the same id would make remove/date/priority act on
  // the wrong row.
  const alreadyThere = editor.races
    .some((r) => r.id === id || (r.date === race.race_date && r.goal_type === goalType));
  if (alreadyThere) return;
  editor.races.push({ id, goal_type: goalType, date: race.race_date, priority });
}

// Raceless fixed-length blocks: the wizard collects a duration in weeks
// instead of a race date (the block-end date is synthesized).
const MAINTENANCE_GOALS = ['running_maintenance', 'trail_maintenance', 'triathlon_maintenance'];
export const isMaintenanceGoal = (g) => MAINTENANCE_GOALS.includes(g);
const MAINT_WEEKS_MIN = 4;
const MAINT_WEEKS_MAX = 16;
const MAINT_WEEKS_DEFAULT = 8;

// Peak h/wk illustration for the wizard preview.
// Maintenance entries mirror MAINTENANCE_WEEKLY_HOURS (steady, not peak).
const PEAK_HOURS = {
  ironman: { basic: 10, moderate: 14, competitive: 18 },
  half_ironman: { basic: 7, moderate: 10, competitive: 13 },
  olympic_triathlon: { basic: 5, moderate: 7, competitive: 9 },
  sprint_triathlon: { basic: 4, moderate: 5.5, competitive: 7 },
  running_marathon: { basic: 5, moderate: 7, competitive: 9 },
  running_half_marathon: { basic: 4, moderate: 5.5, competitive: 7 },
  running_10k: { basic: 3, moderate: 4.5, competitive: 6 },
  running_5k: { basic: 2.5, moderate: 3.5, competitive: 5 },
  // Trail rows: midpoint of each tier band in docs/research/trail_running.md,
  // "Peak weekly hours by tier" (20K 4-6/6-8/8-10, 50K 5-7/7-9/9-12,
  // 100K 8-10/10-12/12-15).
  trail_20k: { basic: 5, moderate: 7, competitive: 9 },
  trail_50k: { basic: 6, moderate: 8, competitive: 10.5 },
  trail_100k: { basic: 9, moderate: 11, competitive: 13.5 },
  running_maintenance: { basic: 3, moderate: 4.5, competitive: 6 },
  trail_maintenance: { basic: 3, moderate: 4.5, competitive: 6 },
  triathlon_maintenance: { basic: 5, moderate: 8.5, competitive: 10 },
};
const RAMP = [0.5, 0.6, 0.68, 0.5, 0.72, 0.8, 0.88, 0.6, 0.92, 1, 0.72, 0.45];
// Steady 3:1 wave for the maintenance preview (no ramp, no taper).
const MAINT_RAMP = [0.85, 0.85, 0.85, 0.5, 0.85, 0.85, 0.85, 0.5, 0.85, 0.85, 0.85, 0.5];

// Sports the self-assessment questionnaire can ask about (mirrors the
// backend's PUT /api/planner/self-assessment sport vocabulary).
const SPORT_LABELS = { running: 'Running', cycling: 'Cycling', swimming: 'Swimming' };

/** The Monday a freshly generated plan starts on: this week's Monday up to
    Tuesday, next Monday from Wednesday on. Week 1 opens with a Tuesday test,
    which a later start this week would lose. A start this week keeps whole
    Monday-based weeks; the days already behind us simply carry no sessions
    (run_generate drops them, so nothing is previewed, stored, or pushed).
    Mirrors `_default_training_start` in endury/planning/create_plan.py. */
function planStartMonday(todayIso) {
  const weekday = (parseDay(todayIso).getDay() + 6) % 7;
  return addDays(todayIso, weekday > 1 ? 7 - weekday : -weekday);
}

/** Ending model for a plan whose goal date has passed, else null.

    Both shells swap their plan surface (phase header, replan affordance)
    for this once the A race — or a maintenance block's end — is behind us. */
export function planEndedModel(store) {
  const plan = store.state.plannerActive?.active_plan;
  if (!plan?.goal_date || plan.goal_date >= store.state.today) return null;
  const maint = isMaintenanceGoal(plan.goal_type);
  return {
    label: maint ? 'Maintenance plan' : 'Race plan',
    title: maint ? 'Block finished' : 'Race completed',
    line: maint
      ? `Your block ended ${fmtDayLabel(plan.goal_date)} — start the next one?`
      : `Congratulations on your ${GOAL_LABELS[plan.goal_type] || plan.goal_type}`
        + ` on ${fmtDayLabel(plan.goal_date)}. Ready for what's next?`,
  };
}

// Confirm-dialog copy shared by both shells (title/body/confirmLabel only —
// mobile spreads these and adds its own `variant: 'sheet'`).
const REPLACE_PLAN_CONFIRM = {
  title: 'Replace your current plan?',
  body: 'Applying this preview archives your current active plan and its remaining sessions. Completed workouts stay on the calendar.',
  confirmLabel: 'Replace plan',
};
// Same decision, but the boot read of /api/planner/active failed: we cannot
// say whether there is a plan to replace, so the copy must not claim there is.
const REPLACE_UNKNOWN_PLAN_CONFIRM = {
  title: 'Apply this plan?',
  body: "We couldn't read your current plan just now. If one is active, applying this preview archives it and its remaining sessions. Completed workouts stay on the calendar.",
  confirmLabel: 'Apply plan',
};
// Body is per-call (renderReplanDiff(refusal.diff)) — only title/confirmLabel move.
export const REPLAN_OVERWRITE_CONFIRM = {
  title: 'Overwrite your edited workouts?',
  confirmLabel: 'Overwrite and replan',
};
const ARCHIVE_PLAN_CONFIRM = {
  title: 'Archive this plan?',
  body: 'The finished plan leaves endury. Its workouts stay on your intervals.icu calendar, next to your activities.',
  confirmLabel: 'Archive plan',
};
// The chat has no history browser by design, so a cleared thread is gone from
// the UI for good — an icon-only button must not do that on a stray tap.
export const NEW_CHAT_CONFIRM = {
  title: 'Start a new chat?',
  body: 'The current conversation is cleared and your coach starts fresh. Anything saved to Memory stays.',
  confirmLabel: 'Start new chat',
};
const DELETE_PLAN_CONFIRM = {
  title: 'Delete this plan?',
  body: 'The plan is removed from endury, and its upcoming workouts and races from your intervals.icu calendar. Earlier workouts and your activities stay.',
  confirmLabel: 'Delete plan',
};
const DELETE_ADHOC_CONFIRM = {
  title: 'Remove this workout?',
  body: 'This coach-scheduled workout is removed from your intervals.icu calendar. Your training plan is not affected.',
  confirmLabel: 'Remove workout',
};

export function defaultEditor(store) {
  const active = store.state.plannerActive;
  const macro = store.state.macro?.macro_plan;
  const goal = active?.active_plan?.goal_type || 'ironman';
  const athlete = store.state.athleteData;
  // A finished plan's goal date must never seed the next plan's race date,
  // and neither must the date of a plan that is no longer on offer: the
  // wizard would aim another plan at that race.
  const goalDate = active?.active_plan?.goal_date || '';
  const seedsDate = !isMaintenanceGoal(goal) && goalDate > store.state.today
    && isOfferedGoal({ goalMaturity: store.state.goalMaturity }, goal);
  return {
    family: familyForGoal(goal),
    goal,
    ambition: macro?.ambition_level || 'moderate',
    aRaceDate: seedsDate ? goalDate : '',
    maintWeeks: isMaintenanceGoal(goal) && macro?.total_weeks
      ? Math.min(MAINT_WEEKS_MAX, Math.max(MAINT_WEEKS_MIN, macro.total_weeks))
      : MAINT_WEEKS_DEFAULT,
    // Optional trail race profile (distance km / gain m) — feeds the
    // km-effort band suggestion; never sent to the backend.
    trailKm: '',
    trailGain: '',
    // True once the athlete picks a goal pill themselves (or is editing
    // an existing plan, whose goal was already a deliberate choice) —
    // gates the race-profile auto-selection.
    goalPickedByHand: Boolean(active?.active_plan),
    // Tune-ups: {id, goal_type, date, priority}, reseeded from the active
    // plan's own request races so editing a plan never silently drops them.
    // Past dates are left out — the planner rejects a race before the start.
    races: (active?.races || [])
      .filter((r) => r.priority !== 'A' && r.race_date > store.state.today)
      .map((r, i) => ({
        id: `seed-${i}`, goal_type: r.goal_type, date: r.race_date, priority: r.priority,
      })),
    // Self-assessment answers, keyed by sport: {hours, longest} as typed.
    // A missing bootstrap (goalSports null) means the questionnaire is
    // never shown — the wizard behaves exactly as it did before.
    assess: Object.fromEntries(Object.entries(athlete?.self_assessment || {})
      .map(([sport, a]) => [sport, {
        hours: String(a.weekly_hours ?? ''),
        longest: String(a.longest_session_min ?? ''),
      }])),
    measuredSports: athlete?.measured_sports || [],
    // sport → measured weekly hours, for the sports intervals.icu could
    // measure. Sizes the maintenance tier default and the volume hold.
    measuredHours: athlete?.measured_weekly_hours || {},
    // The goal the tier default has already fired for. A stored plan's own
    // tier was a deliberate choice, so editing one never re-seeds it.
    ambitionAutoFor: active?.active_plan ? goal : null,
    // The tier the default last wrote, or the one the editor opened on. Any
    // other current ambition means the athlete picked it themselves.
    ambitionAutoValue: macro?.ambition_level || 'moderate',
    // "Hold my weekly volume": opt-in, never remembered across plans.
    holdVolume: false,
    goalSports: athlete?.goal_sports || null,
    // Horizon limits from bootstrap's plan_limits (null = never read, so
    // the wizard hints at nothing rather than guessing a minimum).
    planLimits: store.state.planLimits || null,
    // goal id → {tier, note}, also from bootstrap (null = never read).
    // The goal pill badges only "experimental"; an alpha plan says so in its
    // preview note.
    goalMaturity: store.state.goalMaturity || null,
    // The athlete's stored races (GET /api/races), for the "Your races"
    // chip row — re-synced on every editor open by both shells, same as
    // athlete data (syncEditorAthleteData), since the editor persists
    // across open/close.
    myRaces: store.state.myRaces || [],
    // Why nothing could be measured (no key, unreachable service), or null
    // when intervals.icu was read and simply had no history.
    availableReason: athlete?.available_reason || null,
    forcedSports: [],   // sports the generate gate reported as missing
    // Confirmed the short-horizon "join mid-preparation" dialog for the
    // current goal + A-race date. Reset by both shells whenever either
    // changes, so a stale ack never leaks onto a different request.
    shortHorizonAck: false,
    // Confirmed the "this plan is experimental" dialog. Reset by both
    // shells wherever shortHorizonAck is, plus on an ambition change (the
    // verdict also reads the tier's own recent-volume floor).
    experimentalAck: false,
  };
}

/** Re-sync an existing editor's read-only athlete-data snapshot.

    The editor persists across open/close (user-typed fields must survive),
    but measured sports and stored answers can change mid-session — a
    new intervals.icu upload can turn a questionnaire sport into a measured
    one. Called
    by both shells whenever the editor (re)opens; typed answers are never
    clobbered, and forced sports that became measured are released. */
export function syncEditorAthleteData(e, athleteData) {
  if (!e || !athleteData) return;
  e.measuredSports = athleteData.measured_sports || [];
  e.measuredHours = athleteData.measured_weekly_hours || {};
  e.goalSports = athleteData.goal_sports || null;
  e.availableReason = athleteData.available_reason || null;
  e.forcedSports = (e.forcedSports || []).filter((sp) => !e.measuredSports.includes(sp));
  for (const [sport, a] of Object.entries(athleteData.self_assessment || {})) {
    const cur = e.assess[sport];
    if (!cur || (cur.hours === '' && cur.longest === '')) {
      e.assess[sport] = {
        hours: String(a.weekly_hours ?? ''),
        longest: String(a.longest_session_min ?? ''),
      };
    }
  }
}

/** Goal sports the questionnaire must ask about (mirrors the backend gate). */
function assessSportsFor(e) {
  const forced = e.forcedSports || [];
  if (!e.goalSports) return forced;
  return (e.goalSports[e.goal] || [])
    .filter((sp) => forced.includes(sp) || !e.measuredSports.includes(sp));
}

/** A typed answer as a number inside the backend's range, else null.
    Comma decimals ("4,5") are read as dot decimals — inputmode=decimal
    keypads produce them in most European locales. */
function assessNum(raw, max) {
  if (raw == null || raw === '') return null;
  const v = Number(String(raw).replace(',', '.'));
  return Number.isFinite(v) && v >= 0 && v <= max ? v : null;
}

/** PUT /api/planner/self-assessment body, or null when nothing is asked. */
export function buildSelfAssessmentRequest(e) {
  const sports = assessSportsFor(e);
  if (!sports.length) return null;
  return {
    sports: Object.fromEntries(sports.map((sport) => {
      const longest = assessNum(e.assess[sport]?.longest, 600);
      return [sport, {
        weekly_hours: assessNum(e.assess[sport]?.hours, 40),
        longest_session_min: longest === null ? null : Math.round(longest),
      }];
    })),
  };
}

/** Weekly hours this athlete trains today, across the goal's own sports:
    intervals.icu's measured hours where there are any, the questionnaire's
    answers for the sports it could not measure. 0 when nothing is known
    yet (no bootstrap, no history, no answers). */
function currentWeeklyHours(e) {
  return (e.goalSports?.[e.goal] || []).reduce((sum, sport) => {
    const measured = e.measuredHours?.[e.goal]?.[sport];
    const hours = typeof measured === 'number' ? measured : assessNum(e.assess[sport]?.hours, 40);
    return sum + (hours || 0);
  }, 0);
}

/** The ambition tier a maintenance block should start at for an athlete
    already training `currentWeeklyHours`: the highest tier whose steady
    volume is within reach (<= 1.25x today's hours), else basic — a block
    that asks for double what someone trains is not maintenance. Null when
    the goal is not a maintenance block, the tier volumes were never served,
    or the athlete's volume is unknown. */
function maintenanceTierFor(e) {
  const tiers = e.planLimits?.maintenance_weekly_hours?.[e.goal];
  const hours = currentWeeklyHours(e);
  if (!tiers || hours <= 0) return null;
  return ['competitive', 'moderate', 'basic'].find((t) => tiers[t] <= 1.25 * hours) || 'basic';
}

/** Seed a maintenance block's ambition from the athlete's own volume, once
    per goal.

    The shells own every click handler, so this runs from `editorModel` —
    the one call both shells make before every render — and records both the
    goal it fired for and the tier it wrote: an ambition that no longer
    matches what it wrote was picked by hand, and a hand pick stands for the
    rest of the session, on this goal and on every goal the athlete moves to
    afterwards. The ambition cards re-aim in place the way the goal pills do
    on a trail suggestion, so state and DOM cannot disagree. */
function applyMaintenanceTierDefault(e) {
  if (!isMaintenanceGoal(e.goal) || e.ambitionAutoFor === e.goal) return;
  if (e.ambition !== e.ambitionAutoValue) return;
  const tier = maintenanceTierFor(e);
  if (!tier) return;
  e.ambitionAutoFor = e.goal;
  e.ambitionAutoValue = tier;
  if (tier === e.ambition) return;
  e.ambition = tier;
  document.querySelectorAll('[data-action="set-ambition"]').forEach((b) => {
    b.setAttribute('aria-checked', String(b.dataset.amb === e.ambition));
  });
}

/** "Sized from …" provenance line for the preview strip ('' when unknown). */
export function planBasisLabel(athleteState) {
  const sources = Object.values(athleteState?.sources || {});
  if (!sources.length || sources.includes('default')) return '';
  const history = sources.includes('history');
  const self = sources.includes('self_report');
  if (history && self) return 'Sized from your training history and your answers';
  if (self) return 'Sized from your answers';
  return history ? 'Sized from your training history' : '';
}

/** Training weeks a race date buys, mirroring the phase planner's own week
    math (validate_phase_request): a plan starts on planStartMonday, and the
    race's own week counts — so total_weeks = (race - start).days // 7 + 1. */
function horizonWeeks(raceDateIso, todayIso) {
  const days = Math.round((parseDay(raceDateIso) - parseDay(planStartMonday(todayIso))) / 86400000);
  return Math.floor(days / 7) + 1;
}

/** Short-horizon "join a full preparation mid-way through" numbers for this
    editor's A-race, or null when the date is not in that band — either far
    enough out for a normal plan, or too soon even for a mid-join (the hard
    "Too soon" tier below `min_join_weeks`). Backs both the wizard's advisory
    hint and the short-notice confirm dialog, so both read the same numbers
    the backend's own `horizon_too_short` refusal would compute. */
export function shortHorizonJoin(e, todayIso) {
  const limits = e.planLimits;
  if (!limits || !e.aRaceDate || isMaintenanceGoal(e.goal)) return null;
  const weeks = horizonWeeks(e.aRaceDate, todayIso);
  const min = limits.min_horizon_weeks?.[e.goal];
  const minJoin = limits.min_join_weeks?.[e.goal];
  if (min == null || weeks >= min) return null;
  if (minJoin != null && weeks < minJoin) return null;
  return { min, weeks, deficit: min - weeks };
}

/** "week" vs "weeks" for a dynamic count — every hint/dialog string this
    feature renders carries at least one. */
const weekPlural = (n) => (n === 1 ? '' : 's');

/** Why the planner will refuse or flag this A-race date, or null when it
    won't. {text, tone: 'alert' | 'warn'} — 'alert' blocks (too soon to even
    join a preparation mid-way, or too far out), 'warn' is advisory (a
    short-notice mid-join the athlete will still confirm before it's built).

    The limits are the backend's own tables, served in bootstrap's
    plan_limits; without that snapshot the wizard stays quiet rather than
    guessing a minimum. Maintenance blocks have no race date to judge. */
function raceDateHint(e, todayIso) {
  const limits = e.planLimits;
  if (!limits || !e.aRaceDate || isMaintenanceGoal(e.goal)) return null;
  const weeks = horizonWeeks(e.aRaceDate, todayIso);
  const min = limits.min_horizon_weeks?.[e.goal];
  const minJoin = limits.min_join_weeks?.[e.goal];
  const max = limits.max_horizon_weeks;
  if (minJoin != null && weeks < minJoin) {
    const earliest = addDays(planStartMonday(todayIso), (minJoin - 1) * 7);
    return {
      tone: 'alert',
      text: `Too soon: a ${GOAL_LABELS[e.goal] || e.goal} plan takes ${min} week${weekPlural(min)},`
        + ` and even a short-notice plan needs at least ${minJoin} — a plan starting this Monday`
        + ` gets ${Math.max(0, weeks)}. Earliest race day: ${fmtDayLabel(earliest)}.`,
    };
  }
  const join = shortHorizonJoin(e, todayIso);
  if (join) {
    return {
      tone: 'warn',
      text: `Short prep: a ${GOAL_LABELS[e.goal] || e.goal} plan normally takes ${join.min}`
        + ` week${weekPlural(join.min)} — this one will join a full preparation part-way through,`
        + ` skipping ${join.deficit} early base week${weekPlural(join.deficit)}.`
        + ' You\'ll confirm before it\'s built.',
    };
  }
  if (max != null && weeks > max) {
    return { tone: 'alert', text: `Too far out: a plan spans at most ${max} weeks and this date is ${weeks} out.` };
  }
  return null;
}

/** Confirm-dialog copy for building a short-notice "join mid-preparation"
    plan — shared by the wizard's pre-generate prompt and the belt-and-braces
    retry when the backend's own `horizon_too_short` refusal fires (e.g. a
    stale limits snapshot). `join` is `{min, weeks, deficit}`, either from
    `shortHorizonJoin` or built from a `GenerateError`'s
    `min_weeks`/`actual_weeks`/`deficit_weeks`. */
export function shortHorizonConfirmCopy(goal, join) {
  return {
    title: 'Short preparation',
    body: `A ${GOAL_LABELS[goal] || goal} plan normally takes ${join.min} week${weekPlural(join.min)};`
      + ` your race is ${join.weeks} week${weekPlural(join.weeks)} away. I can build the full plan`
      + ` and drop you in at week ${join.deficit + 1}, skipping the early base phase. Recommended`
      + ' only if you\'ve been training consistently. Alternatives: pick a later race, lower the'
      + ' ambition, or run a maintenance block toward a later goal.',
    confirmLabel: 'Build short-notice plan',
    cancelLabel: 'Change date',
  };
}

// Mirrors endury.planning.plan_maturity.CONFIRM_TAIL — stripped from an
// `experimental_plan` refusal's `error_message` before it becomes a
// confirm dialog's body, whose own buttons already ask the question.
const EXPERIMENTAL_CONFIRM_TAIL = ' You can still build it. Confirm to go ahead.';

/** Confirm-dialog copy for building a plan the backend graded Experimental
    (the `experimental_plan` refusal). `message` is that refusal's own
    `error_message` — the reason sentences the backend built, plus the
    confirm-tail this dialog's own buttons make redundant. */
export function experimentalPlanConfirmCopy(message) {
  const body = message.endsWith(EXPERIMENTAL_CONFIRM_TAIL)
    ? message.slice(0, -EXPERIMENTAL_CONFIRM_TAIL.length)
    : message;
  return {
    title: 'This plan is experimental',
    body: esc(body),
    confirmLabel: 'Build it anyway',
    cancelLabel: 'Go back',
  };
}

/** True when the backend graded this goal's plan blueprint "experimental".

    The grade is GoalProfile.maturity, served in bootstrap's goal_types —
    only experimental wears a badge; an alpha plan says so in its preview
    note. */
function isExperimentalGoal(e, goal) {
  return e.goalMaturity?.[goal]?.tier === 'experimental';
}

/** Weekly hours for the wizard's copy: whole where whole, one decimal
    otherwise ("3 h/week", "4.5 h/week"). */
const tierHours = (h) => (Number.isInteger(h) ? String(h) : h.toFixed(1));

/** Editor state → renderPlanEditor model. */
export function editorModel(e, todayIso) {
  applyMaintenanceTierDefault(e);
  snapToOffered(e);
  const tiersOnOffer = offeredTiers(e, e.goal);
  const goals = goalsForFamily(e.family)
    .filter((g) => isOfferedGoal(e, g))
    .map((g) => ({
      id: g,
      label: GOAL_LABELS[g].replace(' Triathlon', ''),
      active: g === e.goal,
      experimental: isExperimentalGoal(e, g),
      maturityNote: e.goalMaturity?.[g]?.note || null,
    }));
  const peak = PEAK_HOURS[e.goal]?.[e.ambition] ?? 10;
  const weeksAway = e.aRaceDate
    ? Math.max(0, Math.round((parseDay(e.aRaceDate) - parseDay(todayIso)) / (7 * 86400000)))
    : null;
  const raceWeekOf = (date) => {
    if (!e.aRaceDate || !date || !weeksAway) return null;
    const frac = (parseDay(date) - parseDay(todayIso)) / (parseDay(e.aRaceDate) - parseDay(todayIso));
    return Math.min(11, Math.max(0, Math.round(frac * 11)));
  };
  const markers = RAMP.map(() => ({ dot: null }));
  markers[11].dot = 'a';
  // Each bar carries its own hover label — the chart draws no axis, so the
  // week and its hours are otherwise unreadable.
  const barLabel = (f, i) => `Wk ${i + 1} · ${(f * peak).toFixed(1)}h`;
  const rampBars = RAMP.map((f, i) => ({
    frac: f,
    color: i === 11 ? 'a' : (i === 9 ? 'peak' : null),
    label: barLabel(f, i),
  }));
  for (const r of e.races) {
    const w = raceWeekOf(r.date);
    if (w != null) {
      markers[w].dot = r.priority.toLowerCase();
      rampBars[w].color = r.priority.toLowerCase();
    }
  }
  // The planner never generates strength sessions — the mix below covers only
  // sports it actually schedules (percentages renormalized to 100 without it).
  // Trail shares the running mix — same single-sport plans, same illustration.
  const split = e.family === 'triathlon'
    ? [{ sport: 'swim', label: 'Swim', pct: 21 }, { sport: 'bike', label: 'Bike', pct: 53 }, { sport: 'run', label: 'Run', pct: 26 }]
    : [{ sport: 'run', label: 'Run', pct: 87 }, { sport: 'bike', label: 'Cross-train', pct: 13 }];
  const fmtH = (h) => {
    const m = Math.round(h * 60);
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}`;
  };
  const isMaint = isMaintenanceGoal(e.goal);
  // Tier volumes come from the backend's own maintenance table (bootstrap),
  // never from a second copy in JS.
  const tierVolumes = e.planLimits?.maintenance_weekly_hours?.[e.goal] || null;
  const ambSub = (id, fallback) => (
    tierVolumes?.[id] != null ? `${tierHours(tierVolumes[id])} h/week` : fallback
  );
  const nowHours = currentWeeklyHours(e);
  const assessSports = assessSportsFor(e);
  const assessComplete = assessSports.every((sp) => assessNum(e.assess[sp]?.hours, 40) !== null
    && assessNum(e.assess[sp]?.longest, 600) !== null);
  const words = assessSports.map((sp) => (SPORT_LABELS[sp] || sp).toLowerCase());
  return {
    families: Object.keys(FAMILY_GOALS)
      .map((id) => ({ id, label: FAMILY_LABELS[id], active: e.family === id }))
      .filter((f) => goalsForFamily(f.id).some((g) => isOfferedGoal(e, g))),
    goals,
    // Optional trail race profile: distance + climb → km-effort band
    // suggestion. Trail race goals only — maintenance has no race.
    trailProfile: e.family === 'trail' && !isMaint ? {
      km: e.trailKm ?? '',
      gain: e.trailGain ?? '',
      hint: trailProfileHint(e),
    } : null,
    // A maintenance tier is a weekly volume, so the card says so;
    // race tiers keep their qualitative sub, since their volume ramps.
    ambitions: [
      { id: 'basic', label: 'Basic', sub: ambSub('basic', 'Conservative') },
      { id: 'moderate', label: 'Moderate', sub: ambSub('moderate', 'Balanced') },
      { id: 'competitive', label: 'Competitive', sub: ambSub('competitive', 'Maximum') },
    ].filter((a) => !tiersOnOffer || tiersOnOffer.includes(a.id))
      .map((a) => ({ ...a, active: e.ambition === a.id })),
    // "Hold my weekly volume": offered only once we know what the
    // athlete actually trains — there is nothing to hold otherwise.
    holdVolume: nowHours > 0 ? {
      hours: tierHours(nowHours),
      checked: e.holdVolume === true,
    } : null,
    // "Your races" (step 10): chips built from the athlete's stored race
    // table (GET /api/races); null hides the row entirely — an athlete with
    // no races stored sees the wizard without that row.
    yourRaces: e.myRaces.length ? {
      note: isMaint
        ? 'Tap a race to add it to the block as a tune-up race.'
        : 'Tap a race to fill in its date, and its goal when it has one. Check the goal and ambition below.',
      chips: e.myRaces.map((r) => ({
        id: r.id,
        label: `${r.name} · ${fmtDayLabelYear(r.race_date, todayIso)}`,
      })),
    } : null,
    // Every race date the wizard collects lies in the future — a plan can
    // only schedule days it still has, so today is already too late to race.
    minDate: addDays(todayIso, 1),
    aRace: isMaint ? null : {
      dateLabel: e.aRaceDate || 'Set race date',
      awayLabel: weeksAway != null ? `${weeksAway} weeks` : '—',
      dateValue: e.aRaceDate,
      hint: raceDateHint(e, todayIso),
    },
    maintenance: isMaint ? {
      weeks: e.maintWeeks || MAINT_WEEKS_DEFAULT,
      min: MAINT_WEEKS_MIN,
      max: MAINT_WEEKS_MAX,
      endLabel: addDays(planStartMonday(todayIso), (e.maintWeeks || MAINT_WEEKS_DEFAULT) * 7 - 1),
    } : null,
    races: e.races.map((r) => ({
      id: r.id,
      goalType: r.goal_type,
      dateValue: r.date || '',
      priority: r.priority,
    })),
    // A tune-up can be any race type, of any family, offered or not: the
    // plan races it, it does not build towards it.
    raceGoals: Object.entries(FAMILY_GOALS).map(([family, ids]) => ({
      label: FAMILY_LABELS[family],
      goals: ids.filter((g) => !isMaintenanceGoal(g)).map((g) => ({ id: g, label: GOAL_LABELS[g] })),
    })),
    assess: assessSports.length ? {
      // An availableReason means intervals.icu was never read (no key, or
      // unreachable) — claiming "no history found" would assert a fact we
      // never established. The raw reason stays out of the wizard; the
      // Planning Inputs panel surfaces it for diagnosis.
      note: e.availableReason
        ? 'Your intervals.icu history could not be read, so nothing was measured'
          + ' — tell me what you\'ve been doing so the plan starts at the right level.'
        : `No recent ${words.length > 1 ? `${words.slice(0, -1).join(', ')} or ${words[words.length - 1]}` : words[0]}`
          + ' history found — tell me what you\'ve been doing so the plan starts at the right level.',
      sports: assessSports.map((sport) => ({
        id: sport,
        label: SPORT_LABELS[sport] || sport,
        hours: e.assess[sport]?.hours ?? '',
        longest: e.assess[sport]?.longest ?? '',
      })),
    } : null,
    preview: {
      // "up to ~N" — generated plans don't always reach the tier's nominal
      // peak/steady figure, so the copy reads as a ceiling, not a promise.
      peakLabel: `up to ~${Number.isInteger(peak) ? peak : peak.toFixed(1)}`,
      peakEyebrow: isMaint ? 'steady week' : 'peak week',
      peakUnit: isMaint ? 'h/wk steady' : 'h/wk at peak',
      rampBars: isMaint
        ? MAINT_RAMP.map((f, i) => ({ frac: f, color: null, label: barLabel(f, i) }))
        : rampBars,
      markers: isMaint ? MAINT_RAMP.map(() => ({ dot: null })) : markers,
      phases: isMaint ? ['Maintain'] : ['Base', 'Build', 'Peak', 'Taper'],
      showLegend: !isMaint,
      sportRows: split.map((sp) => ({ ...sp, hoursText: fmtH((peak * sp.pct) / 100) })),
      // Said once, right above the CTA, for an experimental plan type only.
      note: isExperimentalGoal(e, e.goal)
        ? 'Experimental plan type — its sessions have not yet been reviewed'
          + ' one by one.'
        : null,
    },
    ctaLabel: isMaint
      ? `Preview ${e.maintWeeks || MAINT_WEEKS_DEFAULT}-week maintenance block`
      : `Preview ${GOAL_LABELS[e.goal]} plan`,
    ctaDisabled: (isMaint ? false : !e.aRaceDate) || !assessComplete,
  };
}

/** `weekly_hours_cap` for the generate body, or nothing at all: an
    unchecked hold must leave the field off the request, not send a null
    the backend would have to read as "no cap" anyway. */
function volumeCap(e) {
  const hours = currentWeeklyHours(e);
  return e.holdVolume && hours > 0
    ? { weekly_hours_cap: Number(hours.toFixed(1)) }
    : {};
}

export function buildGenerateRequest(e, todayIso) {
  const start = planStartMonday(todayIso);
  const tuneUps = e.races.filter((r) => r.date && r.goal_type)
    .map((r) => ({ goal_type: r.goal_type, race_date: r.date, priority: r.priority }));
  if (isMaintenanceGoal(e.goal)) {
    // No A race: race_date is the block-end Sunday, synthesized from the
    // chosen duration, and the tune-up races ride along. Maintenance goals never trigger a short
    // horizon, but the flag is still shaped like every other request.
    const weeks = Math.min(MAINT_WEEKS_MAX, Math.max(MAINT_WEEKS_MIN, e.maintWeeks || MAINT_WEEKS_DEFAULT));
    return {
      races: [{ goal_type: e.goal, race_date: addDays(start, weeks * 7 - 1), priority: 'A' }, ...tuneUps],
      training_start: start,
      ambition_level: e.ambition,
      accept_short_horizon: e.shortHorizonAck === true,
      accept_experimental: e.experimentalAck === true,
      ...volumeCap(e),
    };
  }
  return {
    races: [{ goal_type: e.goal, race_date: e.aRaceDate, priority: 'A' }, ...tuneUps],
    training_start: start,
    ambition_level: e.ambition,
    accept_short_horizon: e.shortHorizonAck === true,
    accept_experimental: e.experimentalAck === true,
    ...volumeCap(e),
  };
}

// Plan-lifecycle writes: move/apply/refresh/archive, and the
// one cache-eviction rule behind all of them.

/** The one rule every plan-write path must follow: a write to the plan makes
    every cached week stale. Every write path calls this rather than
    repeating the eviction itself. */
function evictPlanCaches(ui, store) {
  store.state.weeks = {};
}

/** GET /api/planner/sync-status, refreshed after anything that writes to the
    calendar — sync state is read, never assumed. */
export async function refreshSyncStatus(ui, store) {
  try {
    ui.sync = await plannerSyncStatus();
  } catch {
    ui.sync = null; // an unreachable check must not blank the plan view
  }
  store.state.syncSessions = (ui.sync && ui.sync.sessions) || {};
}

/** Reloads everything the calendar shows after a plan write that did not
    originate in this shell's own UI (a replan, or a coach tool call in
    chat) — one path for both callers. */
export async function refreshPlanViews(store, ui, renderAll) {
  evictPlanCaches(ui, store);
  await refreshSyncStatus(ui, store);
  await store.init();
  renderAll();
}

/** The header's refresh button. A watch upload lands on intervals.icu, not
    here, and the calendar caches every week it has shown — so re-read the
    week, the sync state and the plan through the same path a replan uses,
    and say so: a read that changed nothing must still visibly finish. */
export async function refreshFromIntervals(store, ui, btn, renderAll) {
  btn.disabled = true;
  try {
    await refreshPlanViews(store, ui, renderAll);
    showToast(store.state.error
      ? `Refresh failed: ${store.state.error}`
      : 'Refreshed from intervals.icu');
  } finally {
    btn.disabled = false;
  }
}

/** Move one planned session to a new date (drag-and-drop, or the session
    detail's "Move to…" row). A drop can land in any rendered week, so the
    visible window is force-reloaded instead of one week. */
export async function moveSession(store, ui, sessionId, date, renderAll, {
  confirmVariant = 'center',
} = {}) {
  const weekday = (iso) => ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'][parseDay(iso).getDay()];
  const partner = swapPartner(store, sessionId, date);
  let swap = false;
  if (partner) {
    swap = await openConfirm({
      title: `Swap with ${weekday(date)}'s ${esc(partner.other.name)}?`,
      body: `${weekday(date)} already has your ${esc(partner.other.name)}. Swap the two and it moves to ${weekday(partner.from)}, or keep both on ${weekday(date)}.`,
      confirmLabel: 'Swap days',
      cancelLabel: 'Keep both',
      variant: confirmVariant,
    });
  }
  try {
    const res = await api.rescheduleSession(sessionId, date);
    if (res.status !== 'ok') {
      showToast(res.message || 'Could not move the session');
      return;
    }
    const back = swap ? await api.rescheduleSession(partner.other.id, partner.from) : null;
    ui.modal?.close();
    // Before the reload: the week model bakes in the sync state, and a
    // move whose calendar write failed has just drifted this session.
    await refreshSyncStatus(ui, store);
    await store.reloadWindow();
    renderAll();
    if (back) {
      showToast(back.status === 'ok'
        ? `Swapped: ${partner.other.name} moved to ${weekday(partner.from)}`
        : `Moved to ${weekday(date)}, but ${partner.other.name} stayed: ${back.message}`);
      return;
    }
    showToast(res.warning ? `Moved to ${weekday(date)} — ${res.warning}` : `Moved to ${weekday(date)}`);
  } catch (err) {
    showToast(`Could not move the session: ${err.message}`);
  }
}

/** The open plan session of the same sport already on `date`, which a move
    of `sessionId` there could swap with — or null. Only a session moving
    from today or later offers it, since the other one takes its old day. */
function swapPartner(store, sessionId, date) {
  const sessions = Object.values(store.state.weeks || {}).flatMap((w) => w.sessions || []);
  const moving = sessions.find((s) => s.id === String(sessionId));
  if (!moving || moving.date < store.state.today) return null;
  const other = sessions.find((s) => s.id !== moving.id && s.date === date
    && s.sport === moving.sport && s.status === 'pending' && !s.done && /^\d+$/.test(String(s.id)));
  return other
    ? { other: { id: other.id, name: other.title.replace(/^[^·]*·\s*/, '') }, from: moving.date }
    : null;
}

/** Remove one coach-scheduled ad-hoc workout (the session detail's trash
    button). The event lives only on intervals.icu, so the remote delete is
    the whole operation — same cache/reload footprint as moveSession. */
export async function deleteAdHocSession(store, ui, externalId, renderAll, {
  confirmVariant = 'center',
} = {}) {
  const ok = await openConfirm({ ...DELETE_ADHOC_CONFIRM, variant: confirmVariant });
  if (!ok) return;
  try {
    const res = await api.deleteAdHocWorkout(externalId);
    if (res.status !== 'ok') {
      showToast(res.message || 'Could not remove the workout');
      return;
    }
    ui.modal?.close();
    await store.reloadWindow();
    renderAll();
    showToast('Workout removed from your calendar');
  } catch (err) {
    showToast(`Could not remove the workout: ${err.message}`);
  }
}

/** The athlete's own ad-hoc workouts a completed activity could pair with:
    same day, same sport, not done. They live only on intervals.icu and pair
    by event id; plan sessions come with the debrief (`plan_link`), which
    reaches the whole plan rather than the weeks loaded here. */
function pairCandidates(week, view) {
  if (!week || !view?.date) return [];
  const sport = normSport(view.sport);
  return week.sessions
    .filter((s) => s.date === view.date && s.sport === sport && !s.done
      && s.status === 'adhoc' && s.eventId)
    .map((s) => ({
      body: { event_id: s.eventId },
      label: s.title.replace(/^[^·]*·\s*/, ''),
    }));
}

const LANDING_GESTURES = ['pointerdown', 'touchstart', 'wheel', 'keydown'];
const LANDING_CALM_MS = 1500;
const LANDING_MAX_MS = 60000;

/** Keep the calendar where a landing put it until the page has settled.
    iOS Safari has no scroll anchoring, so a font, a banner or a late row above
    today would slide today away for good. `place` puts the position back and
    says whether it had to move; `isBusy` says weeks are still on their way. A
    touch, wheel or key ends it at once: the athlete has taken over. `onDone`
    runs when it ends. Returns what ends it early. */
export function holdLanding(place, isBusy, onDone) {
  let done = false;
  const started = performance.now();
  let calmSince = started;
  const stop = () => {
    if (done) return;
    done = true;
    LANDING_GESTURES.forEach((g) => window.removeEventListener(g, stop, true));
    onDone();
  };
  LANDING_GESTURES.forEach((g) => window.addEventListener(g, stop, { capture: true, passive: true }));
  const tick = () => {
    if (done) return;
    const now = performance.now();
    if (place() || isBusy()) calmSince = now;
    if (now - calmSince > LANDING_CALM_MS || now - started > LANDING_MAX_MS) stop();
    else requestAnimationFrame(tick);
  };
  tick();
  return stop;
}

/** Run `fn` once no sheet covers the calendar: closing one puts back the
    scroll it saved, which would undo a landing made behind it. */
export function afterSheets(fn) {
  if (document.querySelector('.cb-modal-overlay')) setTimeout(() => afterSheets(fn), 400);
  else fn();
}

/** Fill a done session's debrief slot, after the sheet is already open: the
    race the activity was or what it counts as in the plan, then its zones,
    intervals and splits. Like the trace, this never blocks the open and
    never toasts: an error, or nothing to show, drops the slot. A race also
    tells the Coach's take section, so a take asked here is judged as the
    race, against the race's brief. */
export async function fillActivityDebrief(store, modalEl, activityId) {
  const slot = modalEl.querySelector('[data-debrief-slot]');
  if (!slot) return;
  let view;
  try {
    view = await api.activityDebrief(activityId);
  } catch {
    slot.remove();
    return;
  }
  // The athlete may have closed or replaced the sheet while we waited.
  if (!slot.isConnected) return;
  const raceHandle = view.race ? `race:${view.date}` : '';
  const { lead, rows, pages } = renderActivityDebrief(view, {
    pairable: pairCandidates(store.state.weeks[mondayOf(view.date)] || store.week(), view),
    briefTakes: raceHandle ? briefsFor(store, raceHandle).briefTakes : [],
    band: JSON.parse(slot.dataset.band || 'null'),
    back: sheetBackLabel(modalEl),
  });
  slot.outerHTML = rows;
  const leadEl = modalEl.querySelector('[data-sheet-lead]');
  if (leadEl) leadEl.innerHTML = lead;
  addSheetPages(modalEl, pages);
  const takes = modalEl.querySelector('[data-takes-slot]');
  if (raceHandle && takes) {
    takes.dataset.race = raceLabel(view.race);
    takes.dataset.briefHandle = raceHandle;
    redrawTakes(store, String(activityId));
  }
}

/** Fill a done session's trace slot, after the sheet is already open.
    The streams read is the slowest thing on the surface and the sheet is
    complete without it, so this never blocks the open and never toasts: an
    error, or an activity with nothing drawable, just drops the slot. `sport`
    is the normalised name ('run' | 'bike' | 'swim' | …): normSport is not
    idempotent, so the caller normalises a raw wire sport exactly once. */
export async function fillActivityTrace(modalEl, activityId, { sport } = {}) {
  const slot = modalEl.querySelector('[data-trace-slot]');
  if (!slot) return;
  let trace;
  try {
    trace = await api.activityTrace(activityId);
  } catch {
    slot.remove();
    return;
  }
  // The athlete may have closed or replaced the sheet while we waited.
  if (!slot.isConnected) return;
  if (!trace.source) {
    slot.remove();
    return;
  }
  slot.innerHTML = renderActivityTrace(trace, { sport });
  bindTraceScrub(slot);
}

// Activity id → the take depth the coach is writing, so a sheet closed and
// reopened while it writes still shows the thinking line.
const pendingTakes = new Map();

/** One activity's saved takes and the depth in flight, for its sheet. */
export function takesFor(store, activityId) {
  const id = String(activityId);
  return {
    takes: store.state.takes.filter((t) => t.kind !== 'brief' && t.activity_id === id),
    pending: pendingTakes.get(id) || null,
  };
}

/** Whether a completed session's activity has a saved take: its card's mark. */
export function hasTake(store, activityId) {
  return Boolean(activityId)
    && store.state.takes.some((t) => t.kind !== 'brief' && t.activity_id === String(activityId));
}

/** Re-render the Coach's take page of activity `id` and its row's answer, if
    its sheet is open now (the athlete may have closed or reopened it
    meanwhile). */
function redrawTakes(store, id, extra = {}) {
  const slot = [...document.querySelectorAll('[data-takes-slot]')]
    .find((el) => el.dataset.activityId === id);
  if (!slot) return;
  const { name, date, briefHandle, race, routable } = slot.dataset;
  const sheet = slot.closest('.cb-sheet');
  const held = takesFor(store, id);
  slot.outerHTML = renderActivityTakes({
    activityId: id, name, date, briefHandle, race, routable: routable === '1', ...held, ...extra,
  });
  setSummary(sheet, 'take', takeSummary(held.takes, held.pending));
}

/** A feel or effort chip in an activity sheet: write the athlete's verdict
    to intervals.icu, mark the chip and its row's answer, and bring the
    calendar up to date. */
export async function saveActivityVerdict(store, btn, renderAll) {
  const slot = btn.closest('[data-verdict-slot]');
  const { activityId } = slot.dataset;
  const { field, value } = btn.dataset;
  const sheet = slot.closest('.cb-sheet');
  let saved;
  try {
    saved = await api.putActivityVerdict({ activity_id: activityId, [field]: Number(value) });
  } catch (err) {
    showToast(`Could not save this: ${err.message}`);
    return;
  }
  slot.outerHTML = renderVerdict({ activityId, feel: saved.feel ?? null, rpe: saved.rpe ?? null });
  setSummary(sheet, 'verdict', verdictSummary(saved.feel ?? null, saved.rpe ?? null));
  await store.reloadWindow()
    .catch((err) => showToast(`Saved, but the calendar did not reload: ${err.message}`));
  renderAll();
}

// Subject handle → the brief depth the coach is writing (as pendingTakes).
const pendingBriefs = new Map();

/** One subject's saved briefs and the depth in flight, for its sheet. */
export function briefsFor(store, handle) {
  return {
    briefTakes: store.state.takes.filter((t) => t.kind === 'brief' && t.activity_id === handle),
    briefPending: pendingBriefs.get(handle) || null,
  };
}

/** The race of this date in the weeks the page holds, for its sheet. */
export function findRace(store, date) {
  return store.windowMondays().flatMap((m) => store.state.weeks[m]?.races || [])
    .find((r) => r.race_date === date) || null;
}

/** Fill the brief slot of subject `handle` in `root` with the rows and pages
    of the sheet the worker builds: "session:412" or "race:2026-10-24". */
export async function fillBrief(root, handle) {
  const slot = [...root.querySelectorAll('[data-brief-slot]')].find((el) => el.dataset.handle === handle);
  if (!slot) return;
  const [kind, id] = handle.split(':');
  try {
    const sheet = await api.sessionBrief(kind === 'race' ? { race_date: id } : { session_id: Number(id) });
    if (!slot.isConnected) return;
    const { rows, pages, foot } = renderBriefSheet(sheet, { back: sheetBackLabel(root) });
    slot.classList.remove('cb-brief--loading');
    slot.innerHTML = rows;
    addSheetPages(root, pages);
    const footEl = root.querySelector('[data-sheet-note]');
    if (footEl) footEl.textContent = foot;
  } catch (err) {
    if (!slot.isConnected) return;
    slot.classList.remove('cb-brief--loading');
    slot.innerHTML = `<div class="cb-sheet-row cb-sheet-row--plain"><span class="cb-sheet-row__answer cb-sheet-row__answer--whole">Could not prepare this brief: ${esc(err.message)}</span></div>`;
  }
}

/** Re-render the Coach's brief page of `handle` and its row's answer, if its
    sheet is open. */
function redrawBrief(store, handle, extra = {}) {
  const slot = [...document.querySelectorAll('[data-brief-takes-slot]')]
    .find((el) => el.dataset.handle === handle);
  if (!slot) return;
  const { name, date } = slot.dataset;
  const sheet = slot.closest('.cb-sheet');
  const takes = briefsFor(store, handle).briefTakes;
  const pending = pendingBriefs.get(handle) || null;
  slot.outerHTML = renderCoachBrief({ handle, name, date, takes, pending, ...extra });
  setSummary(sheet, 'brief', briefSummary(takes, pending));
}

/** The brief button in a session or race sheet: ask the coach for that
    depth, save the reply under the subject's handle, show it in place. */
export async function requestBrief(store, coach, btn, renderAll) {
  const slot = btn.closest('[data-brief-takes-slot]');
  const { handle, name, date } = slot.dataset;
  const { depth } = btn.dataset;
  if (pendingBriefs.has(handle)) return;
  pendingBriefs.set(handle, depth);
  redrawBrief(store, handle);
  const reply = await coach.brief(depth, { handle, name, date });
  pendingBriefs.delete(handle);
  let error = '';
  if (!reply || reply.failed) {
    error = reply?.text || 'The coach could not reply. Try again in a moment.';
  } else {
    try {
      store.state.takes = (await api.saveActivityTake(handle, depth, reply.text, 'brief')).takes;
      renderAll();
    } catch (err) {
      error = `Could not save this brief: ${err.message}`;
    }
  }
  redrawBrief(store, handle, { error, opened: depth, proposed: Boolean(reply?.proposals?.length) });
}

/** What "Compare on this route" asks, in the athlete's words: the chat shows
    it as their message, and "show me" asks for the chart. */
export function compareRoutePrompt({ activityId, date }) {
  return `Compare my session on ${date} (activity ${activityId}) with my earlier `
    + 'sessions on the same route: where did I gain or lose time? Show me.';
}

/** The take button in an activity sheet: ask the coach for that depth,
    save the reply with the activity, and show it in place. The coach
    answers outside the conversation; only a card it proposes goes to the
    chat, where it can be applied. */
export async function requestActivityTake(store, coach, btn, renderAll) {
  const slot = btn.closest('[data-takes-slot]');
  const { activityId: id, name, date, briefHandle, race } = slot.dataset;
  const { depth } = btn.dataset;
  if (pendingTakes.has(id)) return;
  pendingTakes.set(id, depth);
  redrawTakes(store, id);
  const briefs = briefHandle ? briefsFor(store, briefHandle).briefTakes : [];
  const brief = (briefs.find((t) => t.depth === 'quick') || briefs[0])?.text || null;
  const reply = await coach.take(depth, { id, name, date, race }, { brief: depth === 'full' ? brief : null });
  pendingTakes.delete(id);
  let error = '';
  if (!reply || reply.failed) {
    error = reply?.text || 'The coach could not reply. Try again in a moment.';
  } else {
    try {
      store.state.takes = (await api.saveActivityTake(id, depth, reply.text)).takes;
      renderAll();
    } catch (err) {
      error = `Could not save this take: ${err.message}`;
    }
  }
  redrawTakes(store, id, { error, opened: depth, proposed: Boolean(reply?.proposals?.length) });
}

/** Render the Planning Inputs panel into `el` and wire its threshold edits.
    Handlers are properties, so rendering into the same element again never
    stacks them. */
export function showInputsPanel(el, payload) {
  el.innerHTML = renderInputsPanel(toInputsPanel(payload));
  el.onclick = (event) => {
    const btn = event.target.closest('[data-thresholds-edit], [data-thresholds-cancel]');
    if (!btn) return;
    const form = btn.closest('.cb-inputs__card').querySelector('.cb-inputs__edit');
    form.hidden = btn.hasAttribute('data-thresholds-cancel');
    if (!form.hidden) form.querySelector('input')?.focus();
  };
  el.onsubmit = (event) => {
    event.preventDefault();
    saveThresholds(el, event.target);
  };
}

/** Send the fields the athlete changed (a pace as m:ss → seconds), then
    re-render the panel from the fresh inputs the call answers. */
async function saveThresholds(el, form) {
  const body = { sport: form.dataset.sport };
  for (const input of form.querySelectorAll('input')) {
    const text = input.value.trim();
    if (!text || text === input.dataset.initial) continue;
    const [min, sec] = text.split(':').map(Number);
    body[input.name] = input.dataset.pace ? min * 60 + sec : Number(text);
  }
  if (Object.keys(body).length === 1) {
    form.hidden = true;
    return;
  }
  const error = form.querySelector('[data-thresholds-error]');
  const save = form.querySelector('[type="submit"]');
  save.disabled = true;
  try {
    const result = await api.putThresholds(body);
    showInputsPanel(el, result.inputs);
    showToast(`Saved on intervals.icu: ${result.types.join(', ')}`);
  } catch (err) {
    error.textContent = `Could not save: ${err.message}`;
    error.hidden = false;
    save.disabled = false;
  }
}

/** Link one completed activity with a planned session (moved onto the
    activity's day when it was planned for another) or an ad-hoc workout,
    then reload the visible window so the merged card (and its compliance)
    shows. */
export async function pairActivity(store, ui, activityId, body, label, renderAll) {
  let result;
  try {
    result = await api.pairActivity(activityId, body);
  } catch (err) {
    showToast(`Could not link: ${err.message}`);
    return;
  }
  const moved = result?.moved_from ? `, moved to ${fmtDayLabel(result.date)}` : '';
  showToast(result?.warning || `Linked to ${label}${moved}`);
  await store.reloadWindow()
    .catch((err) => showToast(`Linked, but the calendar did not reload: ${err.message}`));
  renderAll();
}

/** Undo an activity's link: its session reads as not done again. */
export async function unlinkActivity(store, ui, activityId, renderAll) {
  try {
    await api.unlinkActivity(activityId);
  } catch (err) {
    showToast(`Could not unlink: ${err.message}`);
    return;
  }
  showToast('Unlinked from the plan');
  await store.reloadWindow()
    .catch((err) => showToast(`Unlinked, but the calendar did not reload: ${err.message}`));
  renderAll();
}

/** Approve the previewed plan: commits it, evicts the caches, and reloads
    the calendar. Shell differences (the confirm dialog's variant, and
    mobile's extra `ui.newPlanFromEnded` reset) are threaded through via
    `opts`. */
export async function applyPlan(store, ui, opts) {
  const {
    notePush, renderAll, coachPostCommitTake, confirmVariant = 'center', resetExtra = () => {},
  } = opts;
  if (!ui.preview || !ui.previewRequest) return;
  // A failed plan read must not skip the confirm: committing archives
  // whatever plan is active, whether or not this shell could see it.
  // A finished plan has nothing left to replace: its workouts are all
  // behind today and stay on the calendar.
  const known = Boolean(store.state.plannerActive?.active_plan);
  if ((known && !planEndedModel(store)) || store.state.plannerActiveFailed) {
    const copy = known ? REPLACE_PLAN_CONFIRM : REPLACE_UNKNOWN_PLAN_CONFIRM;
    const ok = await openConfirm({ ...copy, variant: confirmVariant });
    if (!ok) return;
  }
  try {
    const res = await api.plannerCommit({
      name: 'Training Plan',
      generate_request: ui.previewRequest,
      generate_result: ui.preview,
    });
    if (res.status !== 'ok') {
      showToast(res.message || 'Commit failed');
      renderAll();
      return;
    }
    notePush(res.push);
    ui.preview = null;
    ui.previewRequest = null;
    ui.editor = null;
    resetExtra();
    evictPlanCaches(ui, store);
    store.setPlanState('active');
    await refreshSyncStatus(ui, store);
    await store.init();
    showToast(pushToastText('Plan applied', res.push));
    coachPostCommitTake();
  } catch (err) {
    showToast(`Commit failed: ${err.message}`);
  }
  renderAll();
}

/** One backend verb (DELETE /api/planner/active) under two framings: the
    post-race "Archive plan" and the mid-plan "Delete plan". Shell
    differences (confirm variant, closing the modal/drawer only when
    deleting, mobile's extra `ui.newPlanFromEnded` reset and drawer
    re-render) are threaded through via `opts`. */
export async function archivePlan(store, ui, opts) {
  const {
    mode = 'archive', notePush, renderAll, confirmVariant = 'center',
    onDeleteClose = () => {}, resetExtra = () => {}, afterRender = () => {},
  } = opts;
  const deleting = mode === 'delete';
  const copy = deleting ? DELETE_PLAN_CONFIRM : ARCHIVE_PLAN_CONFIRM;
  const ok = await openConfirm({ ...copy, variant: confirmVariant });
  if (!ok) return;
  try {
    const res = await api.plannerDelete();
    // Nothing left to re-push: the plan is archived either way, so a
    // failed remote delete is reported without a retry button.
    notePush(res.remote_delete, { retry: false });
    if (deleting) onDeleteClose();
    ui.editor = null;
    resetExtra();
    evictPlanCaches(ui, store);
    await store.init();
    showToast(pushToastText(deleting ? 'Plan deleted' : 'Plan archived', res.remote_delete));
  } catch (err) {
    showToast(`Could not ${deleting ? 'delete' : 'archive'} the plan: ${err.message}`);
  }
  renderAll();
  afterRender();
}
