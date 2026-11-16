/* Endury — web shell: 3-pane composition (insights rail · calendar ·
   coach column), one continuous scroller of week rows in two densities,
   plan lifecycle, and all event wiring. Components render HTML; this shell
   owns state + events. */

import { createStore } from '../../data/store.js';
import {
  api, generatePlanStream, replanStream, plannerPush,
} from '../../data/api.js';
import {
  fmtDur, parseDay, addDays, mondayOf, fmtDayLabel, DAY_NAMES, MONTH_NAMES,
} from '../../data/format.js';
import { toSession } from '../../data/adapters.js';
import { esc, sportColor, GARMIN_NOTE, TSS_HINT } from '../../components/util.js';
import { icons } from '../../components/icons.js';
import { createCoach } from '../../components/coach.js';
import { renderSessionCard } from '../../components/session_card.js';
import { renderLoadRing } from '../../components/load_ring.js';
import { renderMessage, renderThinking } from '../../components/coach_msg.js';
import { showToast } from '../../components/toast.js';
import { openModal, openConfirm } from '../../components/modal.js';
import { renderWeekSummary } from '../../components/week_summary.js';
import { renderSessionDetail } from '../../components/session_detail.js';
import { renderRaceDetail } from '../../components/session_brief.js';
import { closeSheetPage, openSheetPage } from '../../components/sheet_pages.js';
import { renderPlanEditor } from '../../components/plan_editor.js';
import {
  needsPushNotice, pushToastText, renderPushNotice, renderReplanDiff, renderSyncChip,
  renderSyncOverwriteBody, renderSyncStatus,
} from '../../components/push_status.js';
import { openMemoryPanel } from '../../components/memory_panel.js';
import { openSettingsPanel, settingsIcon } from '../../components/settings_panel.js';
import { openTssExplainer } from '../../components/tss_explainer.js';
import { openCoachIdeas } from '../../components/coach_ideas.js';

import {
  GOAL_LABELS, defaultGoalForFamily, defaultTuneUpGoalFor,
  defaultEditor as sharedDefaultEditor,
  editorModel as sharedEditorModel, buildGenerateRequest,
  isMaintenanceGoal, buildSelfAssessmentRequest, planBasisLabel,
  syncEditorAthleteData, planEndedModel,
  shortHorizonJoin, shortHorizonConfirmCopy, experimentalPlanConfirmCopy,
  REPLAN_OVERWRITE_CONFIRM,
  NEW_CHAT_CONFIRM,
  refreshSyncStatus, refreshPlanViews as sharedRefreshPlanViews, refreshFromIntervals,
  moveSession as sharedMoveSession, applyPlan as sharedApplyPlan,
  archivePlan as sharedArchivePlan, handleTrailProfileInput,
  deleteAdHocSession as sharedDeleteAdHocSession,
  pairActivity as sharedPairActivity, unlinkActivity as sharedUnlinkActivity,
  showInputsPanel,
  fillActivityDebrief, fillActivityTrace, applyRaceChip,
  takesFor, hasTake, requestActivityTake, saveActivityVerdict, compareRoutePrompt,
  briefsFor, findRace, fillBrief, requestBrief, holdLanding, afterSheets,
} from '../shared.js';

export function mount(root) {
  const store = createStore();
  const coach = createCoach(() => renderCoachMsgs(), refreshPlanViews);

  const ui = {
    landing: false,           // the calendar is being held where a landing put it
    stopLanding: null,        // ends that hold early
    booted: false,            // the boot's reads are done
    density: 'detail',       // 'detail' (session cards) | 'compact' (chips + load bars)
    rows: new Map(),         // Monday iso → {el, model, stamp} of a rendered week row
    extending: { up: false, down: false },
    scrollTop: 0,            // scroller position held across a modal
    genPct: 0,
    genLabel: '',
    preview: null,           // GenerateSuccess payload awaiting commit
    previewRequest: null,
    editor: null,            // wizard state while plan editor open
    modal: null,             // {close} of the open modal
    hintModal: null,         // {close} of the metrics explainer, layered over ui.modal
    push: null,              // last failed/skipped intervals.icu write
    sync: null,              // GET /api/planner/sync-status, refreshed on plan reads
    pushRetry: true,         // whether that write can be re-pushed
  };

  // Skeleton
  root.innerHTML = `
    <div class="cbw-app">
      <header class="cbw-topbar">
        <div class="cbw-topbar-left">
          <div class="cbw-logo">E</div>
          <div>
            <div class="cbw-topbar-race" id="cbw-race"></div>
            <div class="cbw-topbar-title">Training Schedule</div>
          </div>
          <span class="cbw-phase-pill" id="cbw-phase" hidden></span>
        </div>
        <div class="cbw-topbar-right">
          <div class="cbw-seg" role="group" aria-label="Calendar density">
            <button type="button" data-action="set-density" data-density="detail" aria-pressed="true">Detail</button>
            <button type="button" data-action="set-density" data-density="compact" aria-pressed="false">Compact</button>
          </div>
          <button type="button" class="cbw-navbtn" id="cbw-today" data-action="today">Today</button>
          <div style="display:flex;align-items:center;gap:8px">
            <button type="button" class="cbw-arrow" data-action="prev-week" aria-label="Previous week">‹</button>
            <span class="cbw-range" id="cbw-range"></span>
            <button type="button" class="cbw-arrow" data-action="next-week" aria-label="Next week">›</button>
          </div>
          <div class="cbw-divider-v"></div>
          <span id="cbw-sync" class="cbw-sync-slot" role="status"></span>
          <button type="button" class="cbw-iconbtn" data-action="refresh" title="Refresh from intervals.icu" aria-label="Refresh from intervals.icu">${icons.refresh(17)}</button>
          <button type="button" class="cbw-iconbtn" data-action="open-inputs" title="Planning Inputs" aria-label="Planning Inputs">${icons.gauge(17)}</button>
          <button type="button" class="cbw-iconbtn" data-action="open-settings" title="Settings" aria-label="Settings">${settingsIcon(17)}</button>
        </div>
      </header>
      <div id="cbw-strips"></div>
      <div class="cbw-body">
        <aside class="cbw-rail" id="cbw-rail" aria-label="Insights"></aside>
        <main class="cbw-center" id="cbw-center">
          <div class="cbw-loading" id="cbw-loading">Loading calendar…</div>
          <div class="cbw-scroller" id="cbw-scroller">
            <div class="cbw-sentinel cbw-sentinel--top" id="cbw-sentinel-top" data-dir="up"></div>
            <div class="cbw-sentinel cbw-sentinel--bottom" id="cbw-sentinel-bottom" data-dir="down"></div>
          </div>
        </main>
        <aside class="cbw-coach" aria-label="Coach">
          <div class="cbw-coach-head">
            <span class="cbw-coach-dot"></span>
            <div style="flex:1">
              <div class="cbw-coach-name">Endury</div>
              <div class="cbw-coach-sub" title="${GARMIN_NOTE}">AI coach · can be wrong</div>
            </div>
            <button type="button" class="cbw-digest-btn" data-action="digest" title="Weekly digest">${icons.digest(13)}Digest</button>
            <button type="button" class="cbw-memory-btn" data-action="open-memory" title="What your coach remembers">${icons.bookmark(13)}Memory</button>
            <button type="button" class="cbw-newchat-btn" data-action="new-chat" title="New chat" aria-label="Start a new chat">${icons.newChat(13)}</button>
          </div>
          <div class="cbw-msgs" id="cbw-msgs" role="log" aria-live="polite" aria-label="Coach conversation"></div>
          <div class="cbw-inputwrap">
            <div class="cbw-inputrow">
              <button type="button" class="cbw-ideas-btn" data-action="coach-ideas" aria-label="What you can ask" title="What you can ask">${icons.bulb(15)}</button>
              <textarea rows="1" id="cbw-draft" placeholder="Ask your coach, or ask for a chart…" aria-label="Message your coach"></textarea>
              <button type="button" class="cbw-send" data-action="send" aria-label="Send">${icons.send(15)}</button>
            </div>
          </div>
        </aside>
      </div>
    </div>`;

  const $ = (id) => root.querySelector(`#${id}`);
  const els = {
    race: $('cbw-race'), phase: $('cbw-phase'), range: $('cbw-range'),
    today: $('cbw-today'), sync: $('cbw-sync'),
    strips: $('cbw-strips'), rail: $('cbw-rail'), center: $('cbw-center'),
    loading: $('cbw-loading'), scroller: $('cbw-scroller'),
    topSentinel: $('cbw-sentinel-top'), bottomSentinel: $('cbw-sentinel-bottom'),
    msgs: $('cbw-msgs'), draft: $('cbw-draft'),
  };

  // Renderers

  function weekLabel() {
    const start = parseDay(store.state.weekStart);
    const end = parseDay(addDays(store.state.weekStart, 6));
    const sameMonth = start.getMonth() === end.getMonth();
    return `${MONTH_NAMES[start.getMonth()]} ${start.getDate()} – `
      + `${sameMonth ? '' : `${MONTH_NAMES[end.getMonth()]} `}${end.getDate()}, ${end.getFullYear()}`;
  }

  function renderTopbar() {
    const week = store.week();
    const plan = week?.activePlan || store.state.plannerActive?.active_plan;
    els.race.textContent = plan ? (GOAL_LABELS[plan.goal_type] || plan.goal_type) : 'No active plan';
    els.range.textContent = weekLabel();
    const phase = week?.phase;
    // A plan whose goal date has passed has no live phase to advertise.
    if (phase && plan && !planEndedModel(store)) {
      const wkIdx = Math.floor(
        (parseDay(store.state.weekStart) - parseDay(mondayOf(phase.start_date))) / (7 * 86400000),
      ) + 1;
      els.phase.hidden = false;
      // Neutral chrome — the word carries the phase; the
      // --cb-phase-* fills stay a chart encoding (block-overview bands),
      // so gold is never a phase colour in the header.
      els.phase.textContent = `${phase.name} · Wk ${Math.max(1, wkIdx)}`;
    } else {
      els.phase.hidden = true;
    }
    root.querySelectorAll('[data-action="set-density"]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.density === ui.density));
    });
    // Nothing to go back to while the cursor is already on this week.
    els.today.classList.toggle('is-hidden', store.state.weekStart === mondayOf(store.state.today));
  }

  function renderStrips() {
    const s = store.state;
    let html = '';
    if (s.planState === 'generating') {
      html = `<div class="cbw-genstrip" role="status">
        <div class="cbw-genbar"><div class="cbw-genfill" style="width:${ui.genPct}%"></div></div>
        <span>${esc(ui.genLabel || 'Generating plan preview…')}</span></div>`;
    } else if (s.planState === 'preview' && ui.preview) {
      const mp = ui.preview.macro_plan || {};
      const diff = `${mp.total_weeks ?? '—'} weeks · ${esc(mp.ambition_level ?? '')} · `
        + `${esc(GOAL_LABELS[ui.previewRequest?.races?.[0]?.goal_type] ?? '')}`;
      const warning = ui.preview.warning;
      const maturityNote = ui.preview.maturity_note;
      const basis = planBasisLabel(ui.preview.athlete_state);
      html = `<div class="cbw-previewbar">
        <div style="flex:1;min-width:0">
          <span class="cbw-pv-title">Plan preview ready — needs your approval</span>
          <span class="cbw-pv-diff">${diff}</span>
          ${basis ? `<div class="cbw-pv-diff">${esc(basis)}</div>` : ''}
          ${maturityNote ? `<div class="cbw-pv-warning">${esc(maturityNote)}</div>` : ''}
          ${warning ? `<div class="cbw-pv-warning">${esc(warning)}</div>` : ''}
        </div>
        <button type="button" class="cbw-btn-ghost" data-action="discard-plan">Discard</button>
        <button type="button" class="cbw-btn-accent" data-action="apply-plan">Apply plan</button>
      </div>`;
    }
    // renderStrips (not renderTopbar) is deliberate: it is the function every
    // ui.sync refresh already calls, so the always-present header slot
    // updates here too.
    els.sync.innerHTML = renderSyncStatus(ui.sync);
    const chip = renderSyncChip(ui.sync);
    const chipRow = chip
      ? `<div class="cbw-sync-row">${chip}</div>`
      : '';
    els.strips.innerHTML =
      html + chipRow + renderPushNotice(ui.push, { retry: ui.pushRetry });
  }

  // The rail's "This week" ring must always reflect the real current week,
  // not whatever week the user has navigated to. Cache lookups persist once
  // loaded (store.js never evicts), so this is a no-op fetch after boot.
  let loadingThisWeek = false;
  function thisWeekModel() {
    const real = mondayOf(store.state.today);
    const cached = store.state.weeks[real];
    if (cached) return cached;
    // Guard on the cache, not on the cursor week: scrolling changes the
    // cursor constantly, and a stale `real !== weekStart` guard would race
    // the window reload with redundant single-week reads.
    if (!store.state.loadingWeeks[real] && !loadingThisWeek) {
      loadingThisWeek = true;
      store.loadWeek(real).finally(() => { loadingThisWeek = false; });
    }
    return null;
  }

  function renderRail() {
    const week = store.week();
    const s = store.state;
    const parts = [];
    const thisWeek = thisWeekModel();
    if (thisWeek) {
      const planStart = s.macro?.macro_plan?.plan_start;
      const planNotStarted = Boolean(s.plannerActive?.active_plan && planStart && s.today < planStart);
      // Lead with plain language, say the ratio in words, and give
      // the acronym one visible anchor (the ? opens the explainer modal) —
      // every downstream surface keeps the bare number and inherits its
      // meaning from here.
      const doneTss = Math.round(thisWeek.doneTss || 0);
      const plannedTss = Math.round(thisWeek.plannedTss || 0);
      const pctDone = plannedTss ? Math.round((doneTss / plannedTss) * 100) : 0;
      parts.push(`<div class="cbw-ring-card">
        ${renderLoadRing({ achieved: thisWeek.doneTss, planned: thisWeek.plannedTss, size: 88 })}
        <div>
          <div class="cb-section-label">This week</div>
          <div style="font-size:15px;font-weight:600;margin-top:3px">Weekly load<button type="button" class="cbw-hint-btn" data-action="explain-tss" aria-label="What is training load?">?</button></div>
          <div style="font-size:11px;color:var(--cb-text-muted);margin-top:3px" title="${esc(TSS_HINT)}">${doneTss} of ${plannedTss} load · ${pctDone}% done</div>
          ${planNotStarted ? `<div style="font-size:10px;color:var(--cb-text-muted);margin-top:3px">Plan starts ${esc(fmtDayLabel(planStart))}</div>` : ''}
        </div>
      </div>`);
    }
    // Once the goal date is behind us the plan card becomes the plan's
    // ending: congratulations + new plan / archive, never a replan.
    const ended = planEndedModel(store);
    if (ended) {
      parts.push(`<div class="cb-insight cb-insight--rail" style="background:var(--cb-surface-2);padding:16px">
        <div class="cb-section-label">${esc(ended.label)}</div>
        <div style="font-size:13px;font-weight:600;margin:10px 0 4px">${esc(ended.title)}</div>
        <div style="font-size:11px;color:var(--cb-text-muted);margin-bottom:12px;line-height:1.4">${esc(ended.line)}</div>
        <button type="button" class="cb-btn-primary cb-btn-primary--block" data-action="new-plan">Start a new plan</button>
        <button type="button" class="cb-btn-secondary" style="width:100%;margin-top:8px" data-action="archive-plan">Archive plan</button>
      </div>`);
    }
    // Race plan card: A race + readiness + tune-ups + Edit (the plan opener).
    if (!ended) {
      const plan = week?.activePlan || s.plannerActive?.active_plan;
      // Upcoming races only, whichever weeks the calendar has loaded:
      // `week.races` is just the scrolled window and keeps past races.
      const races = s.myRaces || [];
      const aRace = races.find((r) => r.priority === 'A');
      const tuneUps = races.filter((r) => r.priority !== 'A');
      const isMaint = plan && isMaintenanceGoal(plan.goal_type);
      const PRI = { A: 'var(--cb-race-a)', B: 'var(--cb-race-b)', C: 'var(--cb-race-c)' };
      const raceDateLabel = (iso) => {
        const d = parseDay(iso);
        return `${MONTH_NAMES[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`;
      };
      parts.push(`<div class="cb-insight cb-insight--rail" style="background:var(--cb-surface-2);padding:16px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <span class="cb-section-label">${!plan ? 'Training plan' : (isMaint ? 'Maintenance plan' : 'Race plan')}</span>
          <div style="display:flex;gap:6px">
            ${plan ? '<button type="button" data-action="replan" style="font-size:11px;font-weight:600;color:var(--cb-text-muted);background:var(--cb-surface-2);border:1px solid var(--cb-border);border-radius:7px;padding:4px 9px;cursor:pointer;font-family:inherit">Replan</button>' : ''}
            <button type="button" data-action="open-plan" style="font-size:11px;font-weight:600;color:var(--cb-accent);background:var(--cb-surface-2);border:1px solid var(--cb-border);border-radius:7px;padding:4px 9px;cursor:pointer;font-family:inherit">${plan ? 'Edit' : 'Create'}</button>
          </div>
        </div>
        ${aRace ? `<div style="display:flex;align-items:center;gap:9px;padding:9px 11px;background:var(--cb-surface-3);border:1px solid rgb(from var(--cb-race-a) r g b / .4);border-radius:9px;margin-bottom:8px">
          <span style="font-size:9px;font-weight:700;color:var(--cb-accent-ink);background:var(--cb-race-a);border-radius:5px;padding:2px 6px">A</span>
          <div style="flex:1"><div style="font-size:12px;font-weight:600">${esc(GOAL_LABELS[plan?.goal_type] || aRace.name)}</div>
          <div style="font-size:9.5px;color:var(--cb-text-muted)">${esc(raceDateLabel(aRace.race_date))}</div></div>
        </div>` : isMaint ? `<div style="display:flex;align-items:center;gap:9px;padding:9px 11px;background:var(--cb-surface-3);border:1px solid var(--cb-border);border-radius:9px;margin-bottom:8px">
          <span style="font-size:9px;font-weight:700;color:#fff;background:var(--cb-phase-maintain);border-radius:5px;padding:2px 6px">M</span>
          <div style="flex:1"><div style="font-size:12px;font-weight:600">Holding fitness — no race scheduled</div>
          ${plan?.goal_date ? `<div style="font-size:9.5px;color:var(--cb-text-muted)">Block ends ${esc(raceDateLabel(plan.goal_date))}</div>` : ''}</div>
        </div>` : `<div style="font-size:11px;color:var(--cb-text-muted);margin-bottom:8px">No active plan — create one to fill your calendar.</div>`}
        ${tuneUps.length ? '<div class="cb-section-label" style="margin-top:12px">Tune-up races</div>' : ''}
        ${tuneUps.map((r) => `<div style="display:flex;align-items:center;gap:9px;padding:8px 11px;background:var(--cb-surface-3);border:1px solid var(--cb-border);border-radius:9px;margin-top:7px">
          <span style="width:9px;height:9px;border-radius:50%;background:${PRI[r.priority]};flex:none"></span>
          <div style="flex:1;min-width:0"><div style="font-size:12px;font-weight:500">${esc(r.name)}</div>
          <div style="font-size:9.5px;color:var(--cb-text-muted)">${esc(raceDateLabel(r.race_date))}</div></div>
          <span style="font-size:9px;font-weight:700;color:#fff;background:${PRI[r.priority]};border-radius:5px;padding:2px 7px">${esc(r.priority)}</span>
        </div>`).join('')}
      </div>`);
    }
    els.rail.innerHTML = parts.join('');
  }

  function sessionsForRender(week) {
    if (store.state.planState === 'preview' && ui.preview?.sessions) {
      // Overlay the preview only from its training_start on; earlier days
      // keep their committed sessions (the preview plan starts next Monday).
      const previewStart = ui.previewRequest?.training_start || '';
      const inWeek = new Set(week.days.map((d) => d.date));
      const previewSessions = ui.preview.sessions
        .filter((p) => inWeek.has(p.date))
        .map((p) => toSession(p, {}));
      return week.days.map((d) => (d.date >= previewStart
        ? {
          date: d.date,
          preview: true,
          sessions: previewSessions.filter((x) => x.date === d.date),
        }
        : d));
    }
    return week.days;
  }

  // The calendar: one continuous scroller of week rows

  const SPORT_ORDER = ['swim', 'bike', 'run', 'strength', 'other'];
  // The line down the scrollport that decides which row the header describes.
  // A web week row is ~300px against an ~830px port, so 35% would land in
  // the second visible row — the header would name next week while this
  // week sits at the top. 15% keeps it on the row you are reading.
  const CURSOR_LINE = 0.15;

  // Every mutation of the scroller runs through this: the row owning the top
  // of the viewport keeps its offset, so prepending four weeks above never
  // moves what the athlete is reading. Its own scrollTop reassignment still
  // dispatches a genuine, trusted 'scroll' event even when the visual
  // position doesn't change (e.g. real content collapsing to skeletons
  // shrinks the layout above the anchor) — the boot-arm listener below reads
  // this timestamp to tell that apart from an actual user scroll.
  let lastAnchorAdjustAt = 0;
  function withScrollAnchor(fn) {
    const scroller = els.scroller;
    const rows = [...scroller.querySelectorAll('[data-week-start]')];
    const anchor = rows.find((el) => el.offsetTop + el.offsetHeight > scroller.scrollTop);
    const offset = anchor ? anchor.offsetTop - scroller.scrollTop : 0;
    fn();
    if (anchor && anchor.isConnected) {
      scroller.scrollTop = anchor.offsetTop - offset;
      lastAnchorAdjustAt = performance.now();
    }
  }

  function monthOpenedBy(monday) {
    for (let i = 0; i < 7; i += 1) {
      const d = parseDay(addDays(monday, i));
      if (d.getDate() === 1) return `${MONTH_NAMES[d.getMonth()]} ${d.getFullYear()}`;
    }
    return null;
  }

  function phaseInfo(phase) {
    if (!phase) return null;
    const p = String(phase.phase_type || '').toLowerCase();
    const key = ['recovery', 'peak', 'taper', 'maintain'].includes(p) ? p : 'build';
    return { key, label: phase.name || (p ? p.charAt(0).toUpperCase() + p.slice(1) : 'Build') };
  }

  // A week inside the plan can still carry no phase range of its own (the
  // fallback builder derives ranges from the first/last session dates), so
  // today's phase stands in for today's week and nothing is drawn otherwise.
  // Status is anchored to today, never to the cursor: browsing must not turn
  // a Done week into a Planned one.
  function weekSummaryModel(monday, week, days) {
    const planStart = store.state.macro?.macro_plan?.plan_start;
    // A week after race day, or after a block's last day, is no plan week.
    const planEnd = store.state.plannerActive?.active_plan?.goal_date;
    const todayMonday = mondayOf(store.state.today);
    const idx = planStart && !(planEnd && monday > planEnd)
      ? Math.round((parseDay(monday) - parseDay(mondayOf(planStart))) / (7 * 86400000)) + 1
      : null;
    const d = parseDay(monday);
    const dl = `${MONTH_NAMES[d.getMonth()]} ${d.getDate()}`;
    const sessions = days.flatMap((x) => x.sessions);
    const plannedTss = Math.round(
      sessions.reduce((t, x) => t + (x.optional ? 0 : x.tss ?? 0), 0),
    );
    const doneTss = Math.round(
      sessions.reduce((t, x) => t + (x.done ? (x.actualTss ?? x.tss ?? 0) : 0), 0),
    );
    const plannedSec = sessions.reduce(
      (t, x) => t + (x.optional ? 0 : x.durationSec ?? 0), 0,
    );
    const doneSec = sessions.reduce((t, x) => t + (x.done ? (x.durationSec ?? 0) : 0), 0);
    const sportSplit = {};
    for (const x of sessions) {
      if (!SPORT_ORDER.includes(x.sport)) continue;
      sportSplit[x.sport] = (sportSplit[x.sport] || 0) + (x.durationSec || x.tss || 1);
    }
    const inWeek = new Set(days.map((x) => x.date));
    const race = (week.races || []).find((r) => inWeek.has(r.race_date)) || null;
    const status = monday < todayMonday
      ? 'Done' : (monday === todayMonday ? 'This week' : 'Planned');
    return {
      weekLabel: idx && idx > 0 ? `Wk ${idx} · ${dl}` : dl,
      phase: phaseInfo(week.weekPhase ?? (monday === todayMonday ? week.phase : null)),
      status,
      plannedTss,
      doneTss,
      plannedHours: (plannedSec / 3600).toFixed(1),
      doneHours: (doneSec / 3600).toFixed(1),
      compliance: status !== 'Planned' && plannedTss
        ? Math.round((doneTss / plannedTss) * 100) : null,
      sportSplit,
      race: race ? (race.priority || 'A') : null,
      current: monday === todayMonday,
    };
  }

  function loadBarHtml(tss, maxDayTss) {
    const pct = maxDayTss ? Math.round((tss / maxDayTss) * 100) : 0;
    return `<div class="cbw-loadbar" aria-hidden="true"><span class="cbw-loadbar__fill" style="width:${pct}%"></span></div>`;
  }

  function chipHtml(session) {
    return `<button type="button" class="cbw-chip" data-action="open-session" data-id="${esc(session.id)}" style="border-left-color:${sportColor(session.sport)}">`
      + `<span class="cbw-chip__dot" style="background:${sportColor(session.sport)}"></span>`
      + `<span class="cbw-chip__label">${esc(session.title)}</span></button>`;
  }

  function dayColHtml(d, i, week, previewing, maxDayTss) {
    const isToday = d.date === store.state.today;
    const anchor = d.sessions.some((x) => x.anchor);
    const race = (week.races || []).find((r) => r.race_date === d.date) || null;
    const tss = Math.round(d.sessions.reduce((a, x) => a + (x.optional ? 0 : x.tss ?? 0), 0));
    const dt = parseDay(d.date);
    const cls = ['cbw-daycol', isToday && 'is-today', anchor && 'is-anchor',
      race && 'is-race', d.preview && 'cbw-preview'].filter(Boolean).join(' ');
    const dayLabel = race ? 'Race day' : (tss ? `${tss} load` : 'Rest');
    const dayTitle = !race && tss ? ` title="${esc(TSS_HINT)}"` : '';
    const raceCard = race
      ? `<button type="button" class="cbw-racecard" data-action="open-race" data-date="${esc(d.date)}"><span class="cbw-racecard-badge">${esc(race.priority || 'A')}</span>${esc(race.name || 'Race')}</button>`
      : '';
    const body = ui.density === 'compact'
      ? loadBarHtml(tss, maxDayTss) + raceCard + d.sessions.map(chipHtml).join('')
      : raceCard + d.sessions.map((x) => renderSessionCard(x, {
        variant: 'grid',
        draggable: !previewing && !x.done && x.status !== 'unplanned' && !x.eventId,
        hasTake: hasTake(store, x.activityId),
      })).join('');
    return `<div class="${cls}" data-date="${d.date}" data-dropday="${d.date}">
        <button type="button" class="cbw-dayhead" data-action="select-day" data-date="${d.date}">
          <span class="cbw-dayhead-row"><span class="cbw-dayhead-dow">${DAY_NAMES[i].toUpperCase()}</span>
          <span class="cbw-dayhead-num">${dt.getDate()}</span></span>
          <span class="cbw-daytss${race ? ' is-race' : ''}"${dayTitle}>${dayLabel}</span>
        </button>
        <div class="cbw-daycards">${body}</div>
      </div>`;
  }

  // A Monday in the window whose chunk hasn't landed renders as a skeleton of
  // the same row: loading and loaded are one render path, so a row swaps to
  // its content in place when the fetch resolves.
  function weekRowHtml(monday, week, maxDayTss) {
    const month = monthOpenedBy(monday);
    const label = month ? `<div class="cbw-monthlabel">${esc(month)}</div>` : '';
    if (!week) {
      const blanks = Array.from({ length: 7 }, () => '<div class="cbw-daycol"></div>').join('');
      return `${label}<div class="cbw-weekgrid" aria-hidden="true">${blanks}</div>`
        + '<aside class="cbw-weeksum" aria-hidden="true"></aside>';
    }
    const previewing = store.state.planState === 'preview';
    const days = sessionsForRender(week);
    const cols = days.map((d, i) => dayColHtml(d, i, week, previewing, maxDayTss)).join('');
    return `${label}<div class="cbw-weekgrid">${cols}</div>`
      + `<aside class="cbw-weeksum">${renderWeekSummary(weekSummaryModel(monday, week, days))}</aside>`;
  }

  // Compact load bars are scaled to the heaviest day currently rendered, so a
  // taper week still shows internal contrast instead of flattening.
  function maxDayLoad() {
    let max = 0;
    for (const monday of store.windowMondays()) {
      for (const d of store.state.weeks[monday]?.days || []) {
        max = Math.max(max, d.sessions.reduce((a, x) => a + (x.optional ? 0 : x.tss ?? 0), 0));
      }
    }
    return Math.round(max);
  }

  // Everything a row's inner HTML depends on besides its week model and the
  // saved takes (the cards' marks). Those two are their own revision tokens:
  // every (re)fetch or save writes a new object.
  // The macro's plan start numbers the week label, and it loads in parallel
  // with the weeks — a row rendered before it lands would otherwise keep its
  // bare date until the next reload.
  function rowStamp(maxDayTss) {
    const preview = store.state.planState === 'preview'
      ? `${ui.previewRequest?.training_start || ''}:${ui.preview?.sessions?.length ?? 0}`
      : '';
    const planStart = store.state.macro?.macro_plan?.plan_start || '';
    // A new date (a tab left open past midnight) moves the today mark.
    return `${ui.density}:${maxDayTss}:${preview}:${planStart}:${store.state.today}`;
  }

  // Cursor, current-week, skeleton, density and day selection are classes, not
  // content — stamping them keeps scrolling and selecting off the render path.
  function stampRow(el, monday, week) {
    el.classList.toggle('is-cursor', monday === store.state.weekStart);
    el.classList.toggle('is-current', monday === mondayOf(store.state.today));
    el.classList.toggle('is-skeleton', !week);
    el.classList.toggle('is-compact', ui.density === 'compact');
    el.querySelectorAll('.cbw-daycol').forEach((col) => {
      col.classList.toggle('is-selected', col.dataset.date === store.state.selectedDay);
    });
  }

  function newRowEl(monday) {
    const holder = document.createElement('div');
    holder.innerHTML = `<section class="cbw-weekrow" data-week-start="${esc(monday)}"></section>`;
    return holder.firstElementChild;
  }

  function renderCenter() {
    const mondays = store.windowMondays();
    els.loading.hidden = mondays.length > 0;
    const maxDayTss = ui.density === 'compact' ? maxDayLoad() : 0;
    const stamp = rowStamp(maxDayTss);
    const keep = new Set(mondays);
    withScrollAnchor(() => {
      for (const [monday, row] of [...ui.rows]) {
        if (keep.has(monday)) continue;
        row.el.remove();
        ui.rows.delete(monday);
      }
      let prev = els.topSentinel;
      for (const monday of mondays) {
        const week = store.state.weeks[monday] || null;
        let row = ui.rows.get(monday);
        if (!row) {
          row = { el: newRowEl(monday), model: undefined, stamp: null };
          ui.rows.set(monday, row);
        }
        if (row.model !== week || row.stamp !== stamp || row.takes !== store.state.takes) {
          row.el.innerHTML = weekRowHtml(monday, week, maxDayTss);
          row.model = week;
          row.stamp = stamp;
          row.takes = store.state.takes;
        }
        stampRow(row.el, monday, week);
        if (prev.nextElementSibling !== row.el) prev.after(row.el);
        prev = row.el;
      }
    });
  }

  /** Put a week's row at the top of the scroller; say whether that moved it. */
  function placeWeek(monday) {
    const row = ui.rows.get(monday);
    if (!row || Math.abs(row.el.offsetTop - els.scroller.scrollTop) < 1) return false;
    const before = els.scroller.scrollTop;
    els.scroller.scrollTop = row.el.offsetTop;
    lastAnchorAdjustAt = performance.now();
    return els.scroller.scrollTop !== before;
  }

  // The calendar put somewhere on purpose stays there until the page settles
  // or the athlete moves, and the cursor does not read it back off the scroll.
  function landOnWeek(monday) {
    ui.stopLanding?.();
    ui.landing = true;
    ui.stopLanding = holdLanding(
      () => placeWeek(monday),
      () => !ui.booted || Object.keys(store.state.loadingWeeks).length > 0,
      () => { ui.landing = false; },
    );
  }

  // The header follows whichever row owns the CURSOR_LINE of the scrollport —
  // cheap, stable, and how a calendar is read top-down.
  let cursorFrame = 0;
  function updateCursorFromScroll() {
    if (cursorFrame) return;
    cursorFrame = requestAnimationFrame(() => {
      cursorFrame = 0;
      const mid = els.scroller.scrollTop + els.scroller.clientHeight * CURSOR_LINE;
      const row = [...ui.rows.entries()].find(([, r]) => r.el.offsetTop <= mid
        && mid < r.el.offsetTop + r.el.offsetHeight);
      const placed = ui.landing || performance.now() - lastAnchorAdjustAt < 50;
      if (!placed && row && row[0] !== store.state.weekStart) store.setCursorWeek(row[0]);
    });
  }
  els.scroller.addEventListener('scroll', updateCursorFromScroll);

  // One chunk per trigger, 600px before the edge. extendWindow widens the
  // window and notifies before it awaits, so the new rows are already on
  // screen as skeletons — a rejected chunk would leave them there forever
  // with no word of why, hence the catch.
  async function onSentinel(entries) {
    for (const entry of entries) {
      if (!entry.isIntersecting || !store.state.windowFrom) continue;
      const dir = entry.target.dataset.dir;
      if (ui.extending[dir]) continue;
      ui.extending[dir] = true;
      try {
        await store.extendWindow(dir);
      } catch (err) {
        showToast(`Could not load more weeks: ${err.message}`);
      } finally {
        ui.extending[dir] = false;
      }
    }
  }
  // Observation starts on the first user scroll after boot (see the boot
  // path below), not here: a sentinel can sit inside the 600px margin of the
  // boot window, so observing immediately would fire an extendWindow before
  // any user input.
  const io = new IntersectionObserver(onSentinel, {
    root: els.scroller, rootMargin: '600px 0px',
  });

  // A modal mounts on <body> and leaves the scroller in place, but a notify()
  // behind it (a move, a refresh) re-renders rows underneath — hold the
  // position and put it back once the modal is gone.
  function keepScroll() {
    ui.scrollTop = els.scroller.scrollTop;
    return () => requestAnimationFrame(() => { els.scroller.scrollTop = ui.scrollTop; });
  }

  function renderCoachMsgs() {
    const st = coach.state;
    els.msgs.innerHTML = st.messages.map(renderMessage).join('')
      + (st.thinking ? renderThinking(st.thinkingText) : '');
    els.msgs.scrollTop = els.msgs.scrollHeight;
  }

  function renderAll() {
    renderTopbar();
    renderStrips();
    renderRail();
    renderCenter();
  }

  // Surfaces: session detail, plan editor

  // A drop, a chip or a card can come from any rendered row, so the lookup is
  // over the whole window, not just the cursor week.
  function findSession(id) {
    return store.windowMondays()
      .flatMap((m) => store.state.weeks[m]?.sessions || [])
      .find((x) => x.id === id)
      || (store.state.planState === 'preview' && ui.preview?.sessions
        ? ui.preview.sessions.map((p) => toSession(p, {}))
          .find((x) => x.id === String(id))
        : null);
  }

  function moveDaysFor(session) {
    const week = store.state.weeks[mondayOf(session.date)] || store.week();
    if (!week || session.done || session.status === 'unplanned'
      || session.eventId || session.skipped
      || store.state.planState === 'preview') return [];
    const raceDates = new Set((week.races || []).map((r) => r.race_date));
    return week.days.map((d, i) => ({
      date: d.date,
      dow: DAY_NAMES[i],
      current: d.date === session.date,
      disabled: d.date < store.state.today || raceDates.has(d.date),
    }));
  }

  function openSessionDetail(id) {
    const session = findSession(id);
    if (!session) return;
    const html = renderSessionDetail(session, {
      moveDays: moveDaysFor(session), ...takesFor(store, session.activityId),
      ...briefsFor(store, `session:${session.id}`),
    });
    ui.modal = openModal(html, {
      variant: 'center', label: 'Session detail', width: 460, onClose: keepScroll(), focusDialog: true,
    });
    ui.modal.el.dataset.sessionId = session.id;
    if (session.done && session.activityId) {
      fillActivityTrace(ui.modal.el, session.activityId, { sport: session.sport });
      fillActivityDebrief(store, ui.modal.el, session.activityId);
    }
    fillBrief(ui.modal.el, `session:${session.id}`);
  }

  // The race day's own sheet: its brief and the Coach's brief.
  function openRaceDetail(date) {
    const race = findRace(store, date);
    if (!race) return;
    const html = renderRaceDetail({ ...race, goalLabel: GOAL_LABELS[race.goal_type] || null }, {
      todayIso: store.state.today, ...briefsFor(store, `race:${date}`),
    });
    ui.modal = openModal(html, {
      variant: 'center', label: 'Race detail', width: 460, onClose: keepScroll(), focusDialog: true,
    });
    fillBrief(ui.modal.el, `race:${date}`);
  }

  function moveSession(sessionId, date) {
    return sharedMoveSession(store, ui, sessionId, date, renderAll);
  }

  // Fetches on open (no polling, no caching) — the panel is a point-in-time
  // snapshot of intervals.icu, not a live view.
  async function openInputsPanel() {
    const html = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
        <div style="font-size:17px;font-weight:600">Planning Inputs</div>
        <button type="button" class="cb-ib" data-action="close-modal" aria-label="Close"
          style="width:30px;height:30px;border-radius:8px;border:1px solid var(--cb-border);background:var(--cb-surface-3);color:var(--cb-text-muted);display:flex;align-items:center;justify-content:center;cursor:pointer">${icons.close(15)}</button>
      </div><div id="cbw-inputs-body" style="padding:24px 4px;color:var(--cb-text-muted);font-size:13px">Loading planning inputs…</div>`;
    ui.modal = openModal(html, { variant: 'center', label: 'Planning Inputs', width: 480 });
    try {
      const payload = await api.getInputs();
      showInputsPanel(ui.modal.el.querySelector('#cbw-inputs-body'), payload);
    } catch (err) {
      showInputsPanel(ui.modal.el.querySelector('#cbw-inputs-body'), {
        available: false, reason: err.message,
      });
    }
  }

  // Plan editor / lifecycle

  const defaultEditor = () => sharedDefaultEditor(store);
  const editorModel = () => sharedEditorModel(ui.editor, store.state.today);

  function openPlanEditor() {
    ui.editor = ui.editor || defaultEditor();
    syncEditorAthleteData(ui.editor, store.state.athleteData);
    ui.editor.myRaces = store.state.myRaces || [];
    const running = Boolean(store.state.plannerActive?.active_plan) && !planEndedModel(store);
    const title = running ? 'Edit plan' : 'Choose a plan';
    // The running plan's exit door: quiet, below the wizard, never the CTA.
    const deleteRow = running
      ? `<div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--cb-border)">
          <button type="button" data-action="delete-plan" style="width:100%;font:inherit;font-size:12px;font-weight:600;color:var(--cb-text-muted);background:var(--cb-surface-3);border:1px solid var(--cb-border);border-radius:9px;padding:10px;cursor:pointer">Delete plan</button>
          <div style="font-size:10px;color:var(--cb-text-muted);margin-top:6px;line-height:1.4">Removes this plan from endury and its upcoming workouts from your intervals.icu calendar. Earlier workouts and your activities stay.</div>
        </div>`
      : '';
    const html = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">
        <div style="font-size:17px;font-weight:600">${title}</div>
        <button type="button" class="cb-ib" data-action="close-modal" aria-label="Close"
          style="width:30px;height:30px;border-radius:8px;border:1px solid var(--cb-border);background:var(--cb-surface-3);color:var(--cb-text-muted);display:flex;align-items:center;justify-content:center;cursor:pointer">${icons.close(15)}</button>
      </div>${renderPlanEditor(editorModel())}${deleteRow}`;
    ui.modal = openModal(html, { variant: 'center', label: title, width: 600 });
  }

  function rerenderPlanEditor() {
    if (!ui.modal?.el) return;
    // The wizard is rebuilt on every change; keep the athlete where they were.
    const { scrollTop } = ui.modal.el;
    ui.modal.close();
    openPlanEditor();
    ui.modal.el.scrollTop = scrollTop;
  }

  // isRetry guards the belt-and-braces horizon_too_short retry below from
  // re-showing the confirm dialog it was itself raised from, and from
  // looping if the backend refuses a second time. The experimental_plan
  // retry below guards itself the same way, off experimentalAck instead —
  // a short-notice plan on an Experimental goal meets both refusals in
  // turn, so it can't share isRetry with the horizon dialog.
  async function startGenerate(isRetry = false) {
    const e = ui.editor;
    if (!e || (!e.aRaceDate && !isMaintenanceGoal(e.goal))) return;
    if (!isRetry && !e.shortHorizonAck) {
      const join = shortHorizonJoin(e, store.state.today);
      if (join) {
        const ok = await openConfirm(shortHorizonConfirmCopy(e.goal, join));
        if (!ok) return;
        e.shortHorizonAck = true;
      }
    }
    const assessment = buildSelfAssessmentRequest(e);
    if (assessment) {
      try {
        const saved = await api.putSelfAssessment(assessment);
        if (store.state.athleteData) store.state.athleteData.self_assessment = saved.self_assessment;
      } catch (err) {
        showToast(`Could not save your training answers: ${err.message}`);
        return;
      }
    }
    const req = buildGenerateRequest(e, store.state.today);
    ui.modal?.close();
    ui.genPct = 5;
    ui.genLabel = 'Generating plan preview…';
    store.setPlanState('generating');
    renderStrips();
    try {
      let result = null;
      await generatePlanStream(req, (ev) => {
        if (ev.type === 'progress') {
          ui.genPct = Math.round(((ev.step || 1) / (ev.total || 4)) * 90);
          ui.genLabel = `${ev.message || 'Working'}…`;
          renderStrips();
        } else if (ev.type === 'result' || ev.type === 'error') {
          result = ev;
        }
      });
      if (result?.status === 'ok') {
        ui.preview = result;
        ui.previewRequest = req;
        store.setPlanState('preview');
        showToast('Plan preview ready');
        // Jump to the preview's first week (the plan starts next Monday).
        await store.gotoDay(req.training_start);
      } else {
        // Belt-and-braces: a stale plan_limits snapshot let a short-horizon
        // date through the wizard's own check. Show the same dialog once,
        // with the backend's own numbers, and retry with the flag — never
        // loop, so a second refusal falls through to the toast below. Only
        // for a date the local hint would have called soft (or when
        // plan_limits was never read at all) — a date the hint already
        // called blocking must fall through to the plain error instead.
        if (result?.error_code === 'horizon_too_short' && !isRetry
          && (shortHorizonJoin(e, store.state.today) || !e.planLimits)) {
          const ok = await openConfirm(shortHorizonConfirmCopy(e.goal, {
            min: result.min_weeks, weeks: result.actual_weeks, deficit: result.deficit_weeks,
          }));
          if (ok) {
            e.shortHorizonAck = true;
            return startGenerate(true);
          }
        }
        // The pipeline graded the plan Experimental (its goal, a
        // short-notice join, an out-of-range horizon, or recent volume
        // below what was checked) — ask before building it. Guarded by
        // experimentalAck rather than isRetry: a short-notice plan on an
        // Experimental goal meets this refusal only after the horizon
        // dialog above already ran once.
        if (result?.error_code === 'experimental_plan' && !e.experimentalAck) {
          const ok = await openConfirm(experimentalPlanConfirmCopy(result.error_message || ''));
          if (ok) {
            e.experimentalAck = true;
            return startGenerate(true);
          }
          // A "no" here answers a question, it isn't a failed generation:
          // back to the editor with the athlete's inputs untouched, no
          // error toast.
          store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
          openPlanEditor();
          renderAll();
          return;
        }
        ui.preview = null;
        ui.previewRequest = null;
        store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
        showToast(`Plan generation failed: ${result?.error_message || 'unknown error'}`);
        // The gate's sports win over the bootstrap snapshot the editor holds.
        if (result?.error_code === 'athlete_data_required') {
          ui.editor.forcedSports = result.missing_sports || [];
        }
        openPlanEditor();
      }
    } catch (err) {
      ui.preview = null;
      ui.previewRequest = null;
      store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
      showToast(`Plan generation failed: ${err.message}`);
      openPlanEditor();
    }
    renderAll();
  }

  // The push block a commit/replan/push answers with: a successful mirror is
  // only a toast, anything else stays on screen as a dismissible notice.
  function notePush(block, { retry = true } = {}) {
    ui.push = needsPushNotice(block) ? block : null;
    ui.pushRetry = retry;
  }

  async function pushAgain() {
    // A push rewrites every upcoming event, so anything the athlete changed
    // on intervals.icu themselves is destroyed by it — ask first.
    const overwrite = renderSyncOverwriteBody(ui.sync?.summary || {});
    if (overwrite) {
      const ok = await openConfirm({
        title: 'Overwrite your edited workouts?',
        body: overwrite,
        confirmLabel: 'Overwrite and sync',
      });
      if (!ok) {
        showToast('Sync cancelled — your intervals.icu edits are untouched');
        return;
      }
    }
    try {
      const res = await plannerPush();
      if (res.status !== 'ok') {
        showToast(res.message || 'Could not push to intervals.icu');
        return;
      }
      notePush(res.push);
      showToast(pushToastText('Calendar updated', res.push));
    } catch (err) {
      showToast(`Could not push to intervals.icu: ${err.message}`);
    }
    await refreshSync();
    renderAll();
  }

  // Sync state is read, never assumed: after anything that writes to the
  // calendar we re-ask the server rather than predicting the new state.
  function refreshSync() {
    return refreshSyncStatus(ui, store);
  }

  // Everything the calendar shows is cached, so a plan write that did not
  // come from this shell (a replan, or a coach tool call in chat) has to
  // evict those caches and re-read — one path for both.
  function refreshPlanViews() {
    return sharedRefreshPlanViews(store, ui, renderAll);
  }

  function applyPlan() {
    return sharedApplyPlan(store, ui, {
      notePush, renderAll, coachPostCommitTake: () => coach.postCommitTake(),
    });
  }

  // Returns the backend's confirm_required refusal (hand-edited intervals.icu
  // events in the rewrite window), or null once the replan has run.
  async function runReplan(confirm) {
    ui.genPct = 5;
    ui.genLabel = 'Replanning from next Monday…';
    store.setPlanState('generating');
    renderStrips();
    try {
      let result = null;
      const refusal = await replanStream((ev) => {
        if (ev.type === 'progress') {
          ui.genPct = Math.round(((ev.step || 1) / (ev.total || 4)) * 90);
          ui.genLabel = `${ev.message || 'Working'}…`;
          renderStrips();
        } else if (ev.type === 'result' || ev.type === 'error') {
          result = ev;
        }
      }, { confirm });
      if (refusal) {
        store.setPlanState('active');
        renderAll();
        return refusal;
      }
      if (result?.status === 'ok') {
        notePush(result.push);
        store.setPlanState('active');
        await refreshPlanViews();
        showToast(pushToastText('Plan updated from next Monday', result.push));
      } else {
        store.setPlanState('active');
        showToast(`Replan failed: ${result?.error_message || 'unknown error'}`);
      }
    } catch (err) {
      store.setPlanState('active');
      showToast(`Replan failed: ${err.message}`);
    }
    renderAll();
    return null;
  }

  async function startReplan() {
    const ok = await openConfirm({
      title: 'Replan from next Monday?',
      body: 'The rest of your plan is regenerated from next Monday based on what you actually trained — this week keeps its sessions. Manual reschedules in future weeks will be reset. The updated plan is applied directly, and your intervals.icu calendar is updated with it.',
      confirmLabel: 'Replan',
    });
    if (!ok) return;
    const refusal = await runReplan(false);
    if (!refusal) return;
    const overwrite = await openConfirm({
      ...REPLAN_OVERWRITE_CONFIRM,
      body: renderReplanDiff(refusal.diff),
    });
    if (!overwrite) {
      showToast('Replan cancelled — your intervals.icu edits are untouched');
      return;
    }
    await runReplan(true);
  }

  // Clearing the thread is one-way from the UI (no history browser), so it
  // asks first — unless there is nothing of the athlete's to lose yet.
  async function startNewChat() {
    if (coach.hasUserTurns()) {
      const ok = await openConfirm(NEW_CHAT_CONFIRM);
      if (!ok) return;
    }
    coach.newChat();
  }

  // One backend verb (DELETE /api/planner/active) under two framings: the
  // post-race "Archive plan" and the mid-plan "Delete plan".
  function archivePlan({ mode = 'archive' } = {}) {
    return sharedArchivePlan(store, ui, {
      mode, notePush, renderAll, onDeleteClose: () => ui.modal?.close(),
    });
  }

  function discardPlan() {
    ui.preview = null;
    ui.previewRequest = null;
    store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
    showToast(store.state.plannerActive?.active_plan ? 'Preview discarded — keeping your current plan' : 'Preview discarded');
    renderAll();
  }

  // Week navigation: a dead fetch rejects, and a 200-with-error read caches a
  // week marked with the reason (see store.loadWeek). Either way the athlete
  // has just landed on a blank week and must be told why.
  async function navigateWeek(pending) {
    try {
      await pending;
    } catch (err) {
      showToast(`Could not load this week: ${err.message}`);
      return;
    }
    const failure = store.week()?.error;
    if (failure) showToast(`Could not load this week: ${failure}`);
  }

  // Events

  // Delegation lives on document: modals/toasts mount on <body>,
  // outside the app root, and their clicks must still reach this handler.
  document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const a = btn.dataset.action;
    switch (a) {
      case 'set-density': ui.density = btn.dataset.density; renderTopbar(); renderCenter(); break;
      case 'today':
        await navigateWeek(store.gotoToday());
        landOnWeek(mondayOf(store.state.today));
        break;
      case 'prev-week':
        await navigateWeek(store.gotoWeek(-1));
        landOnWeek(store.state.weekStart);
        break;
      case 'next-week':
        await navigateWeek(store.gotoWeek(1));
        landOnWeek(store.state.weekStart);
        break;
      case 'select-day': store.selectDay(btn.dataset.date); break;
      case 'goto-day':
        await navigateWeek(store.gotoDay(btn.dataset.date));
        landOnWeek(mondayOf(btn.dataset.date));
        break;
      case 'open-session': openSessionDetail(btn.dataset.id); break;
      case 'delete-adhoc':
        sharedDeleteAdHocSession(store, ui, btn.dataset.externalId, renderAll);
        break;
      case 'pair-activity':
        ui.modal?.close();
        sharedPairActivity(store, ui, btn.dataset.activityId, JSON.parse(btn.dataset.body), btn.dataset.label, renderAll);
        break;
      case 'unlink-activity':
        ui.modal?.close();
        sharedUnlinkActivity(store, ui, btn.dataset.activityId, renderAll);
        break;
      case 'explain-tss': ui.hintModal?.close(); ui.hintModal = openTssExplainer({ topic: btn.dataset.topic || 'tss' }); break;
      case 'ask-coach': {
        const when = btn.dataset.date ? ` on ${btn.dataset.date}` : '';
        ui.modal?.close();
        els.draft.value = `About my ${btn.dataset.name}${when}: `;
        els.draft.focus();
        break;
      }
      case 'activity-take': requestActivityTake(store, coach, btn, renderAll); break;
      case 'activity-brief': requestBrief(store, coach, btn, renderAll); break;
      case 'open-race': openRaceDetail(btn.dataset.date); break;
      case 'activity-verdict': saveActivityVerdict(store, btn, renderAll); break;
      case 'toggle-splits': {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', String(!expanded));
        const rows = btn.parentElement?.querySelector('.cb-workout-steps');
        if (rows) rows.hidden = expanded;
        const label = btn.querySelector('.cb-workout-toggle-label');
        if (label) label.textContent = expanded ? btn.dataset.showLabel : btn.dataset.hideLabel;
        break;
      }
      case 'move-day': {
        const sessionId = ui.modal?.el?.dataset.sessionId;
        if (sessionId && !btn.disabled) moveSession(sessionId, btn.dataset.date);
        break;
      }
      case 'sheet-open': openSheetPage(btn); break;
      case 'sheet-back': closeSheetPage(btn); break;
      case 'toggle-workout': {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', String(!expanded));
        const target = btn.parentElement?.querySelector(btn.dataset.target);
        if (target) target.hidden = expanded;
        const label = btn.querySelector('.cb-workout-toggle-label');
        if (label) label.textContent = expanded ? btn.dataset.showLabel : btn.dataset.hideLabel;
        break;
      }
      case 'open-plan':
      case 'new-plan': openPlanEditor(); break;
      case 'open-inputs': openInputsPanel(); break;
      case 'open-settings': ui.modal = openSettingsPanel({ variant: 'center' }); break;
      case 'refresh': refreshFromIntervals(store, ui, btn, renderAll); break;
      case 'archive-plan': archivePlan(); break;
      case 'delete-plan': archivePlan({ mode: 'delete' }); break;
      case 'set-family':
        ui.editor.family = btn.dataset.fam;
        ui.editor.goal = defaultGoalForFamily(ui.editor.family);
        ui.editor.goalPickedByHand = false;
        ui.editor.shortHorizonAck = false;
        ui.editor.experimentalAck = false;
        rerenderPlanEditor();
        break;
      case 'set-goal':
        ui.editor.goal = btn.dataset.goal;
        ui.editor.goalPickedByHand = true;
        ui.editor.shortHorizonAck = false;
        ui.editor.experimentalAck = false;
        rerenderPlanEditor();
        break;
      case 'set-ambition':
        ui.editor.ambition = btn.dataset.amb;
        ui.editor.experimentalAck = false;
        rerenderPlanEditor();
        break;
      case 'maint-weeks-adjust':
        ui.editor.maintWeeks = Math.min(16, Math.max(4,
          (ui.editor.maintWeeks || 8) + parseInt(btn.dataset.delta, 10)));
        rerenderPlanEditor();
        break;
      case 'add-race':
        ui.editor.races.push({
          id: `r${Math.round(performance.now())}`,
          goal_type: defaultTuneUpGoalFor(ui.editor.family),
          date: '',
          priority: 'C',
        });
        rerenderPlanEditor();
        break;
      case 'remove-race':
        ui.editor.races = ui.editor.races.filter((r) => r.id !== btn.dataset.id);
        rerenderPlanEditor();
        break;
      case 'set-priority': {
        const race = ui.editor.races.find((r) => r.id === btn.dataset.id);
        if (race) race.priority = btn.dataset.pri;
        rerenderPlanEditor();
        break;
      }
      case 'race-chip': {
        const race = (ui.editor.myRaces || []).find((r) => String(r.id) === btn.dataset.id);
        if (race) applyRaceChip(ui.editor, race);
        rerenderPlanEditor();
        break;
      }
      case 'preview-plan': startGenerate(); break;
      case 'apply-plan': applyPlan(); break;
      case 'discard-plan': discardPlan(); break;
      case 'replan': startReplan(); break;
      case 'push-again': case 'sync-plan': pushAgain(); break;
      case 'dismiss-push': ui.push = null; renderStrips(); break;
      case 'digest': coach.digest(Boolean(store.state.plannerActive?.active_plan) && !planEndedModel(store)); break;
      case 'open-memory': ui.modal = openMemoryPanel({ variant: 'center' }); break;
      case 'new-chat': startNewChat(); break;
      case 'chip': coach.send(btn.dataset.text); break;
      case 'coach-ideas': ui.modal = openCoachIdeas({ variant: 'center', messages: coach.state.messages }); break;
      case 'use-idea':
        ui.modal?.close();
        els.draft.value = btn.dataset.text;
        els.draft.focus();
        break;
      case 'compare-route':
        ui.modal?.close();
        coach.send(compareRoutePrompt(btn.closest('[data-takes-slot]').dataset));
        break;
      case 'send': {
        const text = els.draft.value;
        els.draft.value = '';
        coach.send(text);
        break;
      }
      default: break;
    }
  });

  // Drag-to-reschedule (scope delta: static guards only — past days and race
  // days are blocked; the server re-validates via PATCH …/schedule).
  let dragId = null;

  function clearDropMarks() {
    root.querySelectorAll('.cbw-daycol').forEach((col) => {
      col.classList.remove('is-droptarget', 'is-dropblocked', 'cb-drop');
    });
  }

  document.addEventListener('dragstart', (ev) => {
    const card = ev.target.closest?.('.cb-card[draggable="true"]');
    if (!card) return;
    dragId = card.dataset.id;
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', dragId);
    // The payload's races cover the whole plan, so any window week answers
    // for every rendered row — a drop may land several weeks away.
    const raceDates = new Set(store.windowMondays()
      .flatMap((m) => store.state.weeks[m]?.races || [])
      .map((r) => r.race_date));
    root.querySelectorAll('.cbw-daycol').forEach((col) => {
      const date = col.dataset.dropday;
      const valid = date >= store.state.today && !raceDates.has(date);
      col.classList.toggle('is-droptarget', valid);
      col.classList.toggle('is-dropblocked', !valid);
    });
  });

  document.addEventListener('dragover', (ev) => {
    if (!dragId) return;
    const col = ev.target.closest?.('.cbw-daycol.is-droptarget');
    if (!col) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    root.querySelectorAll('.cbw-daycol.cb-drop').forEach((c) => {
      if (c !== col) c.classList.remove('cb-drop');
    });
    col.classList.add('cb-drop');
  });

  document.addEventListener('drop', (ev) => {
    if (!dragId) return;
    const col = ev.target.closest?.('.cbw-daycol.is-droptarget');
    const id = dragId;
    dragId = null;
    clearDropMarks();
    if (!col) return;
    ev.preventDefault();
    const session = findSession(id);
    if (!session || session.date === col.dataset.dropday) return;
    moveSession(id, col.dataset.dropday);
  });

  document.addEventListener('dragend', () => {
    dragId = null;
    clearDropMarks();
  });

  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('[data-action]');
    if (!input || !ui.editor) return;
    if (input.dataset.action === 'a-race-date') {
      ui.editor.aRaceDate = input.value;
      ui.editor.shortHorizonAck = false;
      ui.editor.experimentalAck = false;
      rerenderPlanEditor();
    } else if (input.dataset.action === 'race-date') {
      const race = ui.editor.races.find((r) => r.id === input.dataset.id);
      if (race) race.date = input.value;
      rerenderPlanEditor();
    } else if (input.dataset.action === 'race-goal') {
      const race = ui.editor.races.find((r) => r.id === input.dataset.id);
      if (race) race.goal_type = input.value;
      rerenderPlanEditor();
    }
  });

  // Self-assessment fields update state per keystroke; re-rendering the editor
  // here would close+reopen the modal and drop focus, so only the CTA's
  // enabled state is synced in place.
  document.addEventListener('input', (ev) => {
    const input = ev.target.closest('[data-action]');
    if (!input || !ui.editor) return;
    if (handleTrailProfileInput(ui.editor, input)) return;
    const key = { 'assess-hours': 'hours', 'assess-longest': 'longest' }[input.dataset.action];
    if (!key) return;
    (ui.editor.assess[input.dataset.sport] ||= { hours: '', longest: '' })[key] = input.value.trim();
    const cta = document.querySelector('[data-action="preview-plan"]');
    if (cta) cta.disabled = editorModel().ctaDisabled;
  });

  els.draft.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      const text = els.draft.value;
      els.draft.value = '';
      coach.send(text);
    }
  });

  // Boot
  store.subscribe(renderAll);
  store.onLand(() => afterSheets(() => landOnWeek(mondayOf(store.state.today))));
  store.init().then(async () => {
    // A boot read that failed has to say so: silence here reads as "no plan
    // yet" or as an eternal "Loading calendar…" with nothing naming the
    // cause.
    if (store.state.plannerActiveFailed) {
      showToast('Could not read your training plan — reload to try again');
    } else if (store.state.error) {
      showToast(`Could not load your calendar: ${store.state.error}`);
    }
    // A missing plan is a legitimate calendar state, not a wall: completed
    // activities are read live from intervals.icu, so past weeks stay
    // meaningful with nothing planned. Creating one stays one tap away via
    // the rail's "Create" action, without blocking navigation behind it.
    // Sync state is only knowable by asking intervals.icu, so the plan
    // view reads it once on boot. The weeks cached before it arrived are
    // dropped so their sessions re-adapt with it — and the window is
    // re-opened straight away, since an empty cache renders as "Loading
    // calendar…" and nothing else on the boot path would ever refill it.
    await refreshSync();
    store.state.weeks = {};
    await store.initWindow(store.state.weekStart, { force: true })
      .catch((err) => showToast(`Could not load your calendar: ${err.message}`));
    renderAll();
    // The boot scroll can land a sentinel inside the 600px rootMargin (e.g. a
    // short week above today's), so observing right away would fire an
    // upward extendWindow before the user has touched the scroller. Wait for
    // the first real scroll instead — but a 'scroll' event alone isn't proof
    // of one: withScrollAnchor's own scrollTop reassignment (see above) fires
    // one too. A real scroll never lands within the same frame as that
    // reassignment, so ignore any 'scroll' event that does.
    requestAnimationFrame(() => {
      function armOnRealScroll() {
        if (performance.now() - lastAnchorAdjustAt < 50) return;
        els.scroller.removeEventListener('scroll', armOnRealScroll);
        io.observe(els.topSentinel);
        io.observe(els.bottomSentinel);
      }
      els.scroller.addEventListener('scroll', armOnRealScroll);
    });
  }).finally(() => { ui.booted = true; });
  coach.init();
  renderAll();
  // The store opens on this week, or on the week a quick return remembered:
  // land there from the first frame, so the weeks fill in around it.
  landOnWeek(store.state.weekStart);
}
