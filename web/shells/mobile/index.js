/* Endury — mobile shell: app bar + week strip, continuous agenda feed,
   full-screen coach, plan/inputs drawers. */

import { createStore } from '../../data/store.js';
import {
  api, generatePlanStream, replanStream, plannerPush,
} from '../../data/api.js';
import {
  parseDay, addDays, mondayOf, DAY_NAMES, MONTH_NAMES, fmtDayLabel,
} from '../../data/format.js';
import { toSession } from '../../data/adapters.js';
import { esc, GARMIN_NOTE, TSS_HINT } from '../../components/util.js';
import { icons } from '../../components/icons.js';
import { createCoach } from '../../components/coach.js';
import { renderSessionCard } from '../../components/session_card.js';
import { renderLoadRing } from '../../components/load_ring.js';
import { renderMessage, renderThinking } from '../../components/coach_msg.js';
import { showToast } from '../../components/toast.js';
import { openModal, openConfirm } from '../../components/modal.js';
import { renderSessionDetail } from '../../components/session_detail.js';
import { renderRaceDetail } from '../../components/session_brief.js';
import { closeSheetPage, openSheetPage } from '../../components/sheet_pages.js';
import { renderPlanEditor } from '../../components/plan_editor.js';
import {
  needsPushNotice, pushToastText, renderPushNotice, renderReplanDiff, renderSyncChip,
  renderSyncOverwriteBody, renderSyncStatus,
} from '../../components/push_status.js';
import { openMemoryPanel } from '../../components/memory_panel.js';
import { legalLinksHtml, openSettingsPanel, settingsIcon } from '../../components/settings_panel.js';
import { openTssExplainer } from '../../components/tss_explainer.js';
import { openCoachIdeas } from '../../components/coach_ideas.js';
import {
  GOAL_LABELS, defaultGoalForFamily, defaultTuneUpGoalFor, defaultEditor, editorModel,
  buildGenerateRequest, isMaintenanceGoal,
  buildSelfAssessmentRequest, planBasisLabel, syncEditorAthleteData, planEndedModel,
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

const DOW1 = ['M', 'T', 'W', 'T', 'F', 'S', 'S'];

export function mount(root) {
  const store = createStore();
  const coach = createCoach(() => renderCoachMsgs(), refreshPlanViews);

  const ui = {
    landing: false,           // the calendar is being held where a landing put it
    stopLanding: null,        // ends that hold early
    booted: false,            // the boot's reads are done
    coachOpen: false,         // full-screen coach panel open?
    coachReplies: 0,          // last-seen coach.state.replies, for the unread edge
    drawer: null,             // 'plan' | 'inputs' | null
    density: 'detail',        // 'detail' | 'compact'
    rows: new Map(),          // weekStart iso → { el, model }
    renderKey: null,          // density/preview signature; a change forces every row to rebuild
    extending: { up: false, down: false },
    scrollTop: null,          // feed scrollTop saved while a session sheet is open
    genPct: 0,
    genLabel: '',
    preview: null,
    previewRequest: null,
    editor: null,
    modal: null,
    hintModal: null,          // {close} of the metrics explainer, layered over ui.modal
    newPlanFromEnded: false,  // "Start a new plan" tapped on the ended state
    push: null,               // last failed/skipped intervals.icu write
    pushRetry: true,          // whether that write can be re-pushed
    sync: null,               // GET /api/planner/sync-status, refreshed on plan reads
  };

  root.innerHTML = `
    <div class="cbm-app">
      <div class="cbm-appbar">
        <div class="cbm-appbar-top">
          <div style="min-width:0">
            <button type="button" class="cbm-race-pill" data-action="open-plan"><span id="cbm-race">Plan</span>${icons.chevronRight(12)}</button>
            <div class="cbm-weeknav">
              <button type="button" class="cbm-arrow" data-action="prev-week" aria-label="Previous week">${icons.chevronLeft(13, 2.4)}</button>
              <span class="cbm-weeknav-title" id="cbm-weektitle">This Week</span>
              <button type="button" class="cbm-arrow" data-action="next-week" aria-label="Next week">${icons.chevronRight(13, 2.4)}</button>
            </div>
            <div class="cbm-subline" id="cbm-subline"></div>
          </div>
          <div class="cbm-appbar-right">
            <div class="cbm-ringwrap" id="cbm-ring" title="Achieved vs planned load this week"></div>
            <button type="button" class="cbm-iconbtn" data-action="open-inputs" title="Planning Inputs" aria-label="Planning Inputs">${icons.gauge(21)}</button>
          </div>
        </div>
        <div class="cbm-strip" id="cbm-strip"></div>
        <div class="cbm-viewrow">
          <div class="cbm-viewseg" role="radiogroup" aria-label="Calendar density">
            <button type="button" class="cb-select cb-select--sm" data-action="set-density" data-density="detail" role="radio" aria-checked="true">Detail</button>
            <button type="button" class="cb-select cb-select--sm" data-action="set-density" data-density="compact" role="radio" aria-checked="false">Compact</button>
          </div>
          <div class="cbm-viewrow-right">
            <span id="cbm-sync" class="cbm-sync-slot" role="status"></span>
            <button type="button" class="cbm-refresh-btn" data-action="refresh" title="Refresh from intervals.icu">${icons.refresh(12)}Refresh</button>
          </div>
        </div>
      </div>
      <div id="cbm-strips"></div>
      <div class="cbm-feed" id="cbm-feed">
        <div class="cbm-sentinel cbm-sentinel--top" data-dir="up"></div>
        <div class="cbm-sentinel cbm-sentinel--bottom" data-dir="down"></div>
      </div>
      <button type="button" class="cbm-today-pill" id="cbm-today-pill" data-action="today" hidden>Today</button>
      <button type="button" class="cbm-coach-fab" id="cbm-coach-fab" data-action="open-coach" aria-label="Open coach">${icons.chat(22)}</button>

      <div class="cbm-sheet" id="cbm-sheet" role="dialog" aria-label="Coach">
        <div class="cbm-sheet-head">
          <span class="cbm-sheet-dot"></span>
          <span class="cbm-sheet-name" title="${GARMIN_NOTE}">Endury · AI coach</span>
          <button type="button" class="cbm-digest-btn" data-action="digest" title="Weekly digest">${icons.digest(14)}Digest</button>
          <button type="button" class="cbm-memory-btn" data-action="open-memory" title="What your coach remembers">${icons.bookmark(14)}Memory</button>
          <button type="button" class="cbm-newchat-btn" data-action="new-chat" title="New chat" aria-label="Start a new chat">${icons.newChat(14)}</button>
          <button type="button" class="cbm-sheet-close" data-action="close-coach" aria-label="Close coach">${icons.close(16)}</button>
        </div>
        <div class="cbm-msgs" id="cbm-msgs" role="log" aria-live="polite" aria-label="Coach conversation"></div>
        <div class="cbm-inputwrap">
          <div class="cbm-inputrow">
            <button type="button" class="cbm-ideas-btn" data-action="coach-ideas" aria-label="What you can ask" title="What you can ask">${icons.bulb(18)}</button>
            <textarea rows="1" id="cbm-draft" placeholder="Ask your coach…" aria-label="Message your coach"></textarea>
            <button type="button" class="cbm-send" data-action="send" aria-label="Send">${icons.send(16)}</button>
          </div>
        </div>
      </div>

      <button type="button" class="cbm-scrim" id="cbm-scrim" data-action="close-drawer" aria-label="Close panel" tabindex="-1"></button>
      <div class="cbm-drawer cbm-drawer--left" id="cbm-plan" aria-label="Choose a plan" role="dialog">
        <div class="cbm-drawer-head">
          <div class="cbm-drawer-title">Choose a plan</div>
          <button type="button" class="cbm-drawer-close" data-action="close-drawer" aria-label="Close">${icons.close(15)}</button>
        </div>
        <div class="cbm-drawer-body" id="cbm-plan-body"></div>
      </div>
      <div class="cbm-drawer cbm-drawer--right" id="cbm-inputs" aria-label="Planning Inputs" role="dialog">
        <div class="cbm-drawer-head">
          <div class="cbm-drawer-title">Planning Inputs</div>
          <button type="button" class="cb-settings-entry" data-action="open-settings">${settingsIcon(13)}Settings</button>
          <button type="button" class="cbm-drawer-close" data-action="close-drawer" aria-label="Close">${icons.close(15)}</button>
        </div>
        <div class="cbm-drawer-body" id="cbm-inputs-body"></div>
        <div class="cb-settings__links">${legalLinksHtml()}</div>
      </div>
    </div>`;

  const $ = (id) => root.querySelector(`#${id}`);
  const els = {
    race: $('cbm-race'), weektitle: $('cbm-weektitle'), subline: $('cbm-subline'),
    ring: $('cbm-ring'), strip: $('cbm-strip'), strips: $('cbm-strips'),
    feed: $('cbm-feed'), sheet: $('cbm-sheet'), fab: $('cbm-coach-fab'), msgs: $('cbm-msgs'), draft: $('cbm-draft'),
    scrim: $('cbm-scrim'), sync: $('cbm-sync'),
    plan: $('cbm-plan'), planBody: $('cbm-plan-body'),
    inputs: $('cbm-inputs'), inputsBody: $('cbm-inputs-body'),
    todayPill: $('cbm-today-pill'),
  };
  els.feedTop = els.feed.querySelector('.cbm-sentinel--top');
  els.feedBottom = els.feed.querySelector('.cbm-sentinel--bottom');

  // Renderers

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

  function renderAppbar() {
    const week = store.week();
    const plan = week?.activePlan || store.state.plannerActive?.active_plan;
    els.race.textContent = plan ? (GOAL_LABELS[plan.goal_type] || plan.goal_type) : 'Create a plan';
    const start = parseDay(store.state.weekStart);
    const end = parseDay(addDays(store.state.weekStart, 6));
    const isThisWeek = store.state.weekStart === mondayOf(store.state.today);
    els.weektitle.textContent = isThisWeek ? 'This Week' : `${MONTH_NAMES[start.getMonth()]} ${start.getDate()}`;
    const range = `${MONTH_NAMES[start.getMonth()]} ${start.getDate()} – ${start.getMonth() === end.getMonth() ? '' : `${MONTH_NAMES[end.getMonth()]} `}${end.getDate()}`;
    // A plan whose goal date has passed has no live phase to advertise.
    const phase = week?.phase && !planEndedModel(store) ? ` · ${week.phase.name}` : '';
    els.subline.textContent = `${range}${phase}`;
    els.ring.innerHTML = week
      ? `${renderLoadRing({ achieved: week.doneTss, planned: week.plannedTss, size: 60 })}<span class="cbm-ringlabel" title="${esc(TSS_HINT)}">Weekly load<button type="button" class="cbm-hint-btn" data-action="explain-tss" aria-label="What is training load?">?</button></span>`
      : '';
    // strip
    const days = week ? sessionsForRender(week) : [];
    els.strip.innerHTML = (week ? days : []).map((d, i) => {
      const selected = d.date === store.state.selectedDay;
      const isToday = d.date === store.state.today;
      const anchor = d.sessions.some((s) => s.anchor);
      const sports = [...new Set(d.sessions.filter((s) => s.sport !== 'rest').map((s) => s.sport))];
      const cls = ['cbm-day', selected && 'is-selected', isToday && 'is-today', anchor && 'is-anchor']
        .filter(Boolean).join(' ');
      return `<button type="button" class="${cls}" data-action="select-day" data-date="${d.date}">
        <span class="cbm-day-dow">${DOW1[i]}</span>
        <span class="cbm-day-num">${parseDay(d.date).getDate()}</span>
        <span class="cbm-day-dots">${sports.map((sp) => `<span class="cbm-day-dot" style="background:${selected ? 'rgb(from var(--cb-bg) r g b / .6)' : `var(--cb-sport-${sp})`}"></span>`).join('')}</span>
      </button>`;
    }).join('');
    root.querySelectorAll('[data-action="set-density"]').forEach((b) => {
      b.setAttribute('aria-checked', String(b.dataset.density === ui.density));
    });
  }

  function renderStrips() {
    const s = store.state;
    let html = '';
    // A finished plan's congratulations/next-step state must be visible on
    // the strip, not only inside the drawer — a pending preview wins.
    const ended = planEndedModel(store);
    if (ended && !ui.preview) {
      html = `<div class="cbm-previewbar">
        <div style="flex:1;min-width:0">
          <div class="t">${esc(ended.title)}</div>
          <div class="d">${esc(ended.line)}</div>
        </div>
        <button type="button" class="cbm-btn-ghost" data-action="archive-plan">Archive</button>
        <button type="button" class="cbm-btn-accent" data-action="new-plan">New plan</button>
      </div>`;
    } else if (s.planState === 'generating') {
      html = `<div class="cbm-genstrip" role="status">
        <div class="bar"><div class="fill" style="width:${ui.genPct}%"></div></div>
        <span>${esc(ui.genLabel || 'Generating plan preview…')}</span></div>`;
    } else if (s.planState === 'preview' && ui.preview) {
      const mp = ui.preview.macro_plan || {};
      const warning = ui.preview.warning;
      const maturityNote = ui.preview.maturity_note;
      const basis = planBasisLabel(ui.preview.athlete_state);
      html = `<div class="cbm-previewbar">
        <div style="flex:1;min-width:0">
          <div class="t">Plan preview ready — needs your approval</div>
          <div class="d">${mp.total_weeks ?? '—'} weeks · ${esc(mp.ambition_level ?? '')} · ${esc(GOAL_LABELS[ui.previewRequest?.races?.[0]?.goal_type] ?? '')}</div>
          ${basis ? `<div class="d">${esc(basis)}</div>` : ''}
          ${maturityNote ? `<div class="w">${esc(maturityNote)}</div>` : ''}
          ${warning ? `<div class="w">${esc(warning)}</div>` : ''}
        </div>
        <button type="button" class="cbm-btn-ghost" data-action="discard-plan">Discard</button>
        <button type="button" class="cbm-btn-accent" data-action="apply-plan">Apply</button>
      </div>`;
    }
    const chip = renderSyncChip(ui.sync);
    const chipRow = chip
      ? `<div class="cbm-sync-row">${chip}</div>`
      : '';
    // compact: 'intervals.icu · in sync' measured 100-120px against 88px of
    // slack beside Refresh at 375px.
    els.sync.innerHTML = renderSyncStatus(ui.sync, { compact: true });
    els.strips.innerHTML =
      html + chipRow + renderPushNotice(ui.push, { retry: ui.pushRetry });
  }

  // Continuous feed: week groups, sentinels, skeletons, row diffing

  function weekRangeLabel(monday) {
    const start = parseDay(monday);
    const end = parseDay(addDays(monday, 6));
    return `${MONTH_NAMES[start.getMonth()]} ${start.getDate()} – ${start.getMonth() === end.getMonth() ? '' : `${MONTH_NAMES[end.getMonth()]} `}${end.getDate()}`;
  }

  function phaseInfoForWeek(phase) {
    if (!phase) return null;
    const p = String(phase.phase_type || '').toLowerCase();
    const key = (p === 'recovery' || p === 'peak' || p === 'taper' || p === 'maintain') ? p : 'build';
    return { key, label: phase.name || '' };
  }

  function weekGroupClass(monday, model) {
    return ['cbm-weekgroup',
      !model && 'is-skeleton',
      monday === store.state.weekStart && 'is-cursor',
      monday === mondayOf(store.state.today) && 'is-current',
    ].filter(Boolean).join(' ');
  }

  // Cursor, current-week, skeleton and day selection are classes, not
  // content — stamping them on every render (not just the changed-model
  // branch) keeps a tapped/scrolled-to day's highlight from going stale.
  function stampRow(el, monday, model) {
    el.classList.toggle('is-cursor', monday === store.state.weekStart);
    el.classList.toggle('is-current', monday === mondayOf(store.state.today));
    el.classList.toggle('is-skeleton', !model);
    el.querySelectorAll('.cbm-daysec').forEach((sec) => {
      sec.classList.toggle('is-selected', sec.dataset.date === store.state.selectedDay);
    });
  }

  function skeletonWeekHtml() {
    return `<div class="cbm-weekhead is-skeleton" aria-hidden="true"></div>
      ${Array.from({ length: 7 }, () => '<div class="cbm-daysec is-skeleton" aria-hidden="true"></div>').join('')}`;
  }

  function daySectionHtml(d, i, week) {
    const isToday = d.date === store.state.today;
    const selected = d.date === store.state.selectedDay;
    const anchor = d.sessions.some((s) => s.anchor);
    const tss = Math.round(d.sessions.reduce((a, s) => a + (s.optional ? 0 : s.tss ?? 0), 0));
    const hasDone = d.sessions.some((s) => s.done);
    const doneTss = Math.round(
      d.sessions.reduce((a, s) => a + (s.done ? (s.actualTss ?? s.tss ?? 0) : 0), 0),
    );
    const race = (week.races || []).find((r) => r.race_date === d.date) || null;
    const dayLabel = race
      ? 'Race day'
      : (tss ? `${tss} load` : (hasDone ? (doneTss ? `${doneTss} load` : '—') : 'Rest'));
    // A touch device has no hover, so the TSS gloss needs a real tap
    // target here (not just the hover-only title web's day column keeps).
    const hasTssHint = dayLabel.includes('load');
    const dayTag = hasTssHint ? 'button' : 'span';
    const dayAttrs = hasTssHint
      ? ` type="button" data-action="explain-tss" title="${esc(TSS_HINT)}"` : '';
    const tag = isToday
      ? '<span class="cbm-tag cbm-tag--today">TODAY</span>'
      : (race
        ? `<button type="button" class="cbm-tag cbm-tag--race" data-action="open-race" data-date="${esc(d.date)}">RACE</button>`
        : (anchor ? '<span class="cbm-tag cbm-tag--anchor">ANCHOR</span>' : ''));
    const plan = week.activePlan || store.state.plannerActive?.active_plan;
    const planStartLabel = plan?.start_date && plan.start_date > store.state.today
      ? fmtDayLabel(plan.start_date) : null;
    const emptyMsg = race
      ? `<button type="button" class="cbm-linkbtn" data-action="open-race" data-date="${esc(d.date)}">${esc(race.name || 'Race')} — race day</button>`
      : (planStartLabel ? `Plan starts ${esc(planStartLabel)}` : 'Nothing planned');
    if (ui.density === 'compact') {
      const sports = [...new Set(d.sessions.filter((s) => s.sport !== 'rest').map((s) => s.sport))];
      return `<section class="cbm-daysec cbm-daysec--compact${selected ? ' is-selected' : ''}${d.preview ? ' cbw-preview' : ''}" data-date="${d.date}" id="cbm-sec-${d.date}">
        <span class="cbm-daysec-label">${DAY_NAMES[i].toUpperCase()} ${parseDay(d.date).getDate()}</span>
        <span class="cbm-day-dots">${sports.map((sp) => `<span class="cbm-day-dot" style="background:var(--cb-sport-${sp})"></span>`).join('')}</span>
        ${tag}
        <${dayTag} class="cbm-daysec-tss"${dayAttrs}>${dayLabel}</${dayTag}>
      </section>`;
    }
    return `<section class="cbm-daysec${selected ? ' is-selected' : ''}${d.preview ? ' cbw-preview' : ''}" data-date="${d.date}" id="cbm-sec-${d.date}">
      <div class="cbm-daysec-head">
        <span class="cbm-daysec-label">${DAY_NAMES[i].toUpperCase()} ${parseDay(d.date).getDate()}</span>
        ${tag}
        <${dayTag} class="cbm-daysec-tss"${dayAttrs}>${dayLabel}</${dayTag}>
      </div>
      ${d.sessions.map((s) => renderSessionCard(s, { variant: 'agenda', hasTake: hasTake(store, s.activityId) })).join('')
        || `<div style="font-size:var(--cb-fs-meta);color:var(--cb-text-muted);padding:2px 0 6px">${emptyMsg}</div>`}
    </section>`;
  }

  function weekGroupInnerHtml(monday, model) {
    if (!model) return skeletonWeekHtml();
    const weekContainsToday = monday <= store.state.today && store.state.today <= addDays(monday, 6);
    const phase = phaseInfoForWeek(model.weekPhase ?? (weekContainsToday ? model.phase : null));
    const phasePill = phase
      ? `<span class="cb-phase-pill cb-phase-pill--${phase.key}">${esc(phase.label)}</span>` : '';
    const days = sessionsForRender(model);
    return `<div class="cbm-weekhead">
        <span class="cbm-weekhead-range">${weekRangeLabel(monday)}</span>
        ${phasePill}
        <span class="cbm-weekhead-tss" title="${esc(TSS_HINT)}">${model.doneTss} / ${model.plannedTss} load</span>
      </div>
      ${days.map((d, i) => daySectionHtml(d, i, model)).join('')}`;
  }

  // Every mutation of the scroller (prepend/append/re-render/removal) goes
  // through this so the visible content never jumps.
  // Its own scrollTop reassignment still dispatches a genuine, trusted
  // 'scroll' event even when the visual position doesn't change (e.g. real
  // content collapsing to skeletons shrinks the layout above the anchor) —
  // the boot-arm listener below reads this timestamp to tell that apart
  // from an actual user scroll.
  // The anchor is the first day section reaching below the pinned week head
  // (a skeleton row has none, so the row itself): a whole detail week is far
  // taller than the port, and its own offset says nothing about which day
  // is in view. Rows rebuild their day sections, so it is found again by id.
  let lastAnchorAdjustAt = 0;
  function withScrollAnchor(fn) {
    const line = feedTopEdge() + weekHeadHeight() + 1;
    let anchorSel = null;
    let offset = 0;
    for (const row of els.feed.querySelectorAll('[data-week-start]')) {
      const secs = row.querySelectorAll('[data-date]');
      const el = [...(secs.length ? secs : [row])].find((e) => e.getBoundingClientRect().bottom > line);
      if (!el) continue;
      anchorSel = el.dataset.date
        ? `#cbm-sec-${CSS.escape(el.dataset.date)}`
        : `[data-week-start="${row.dataset.weekStart}"]`;
      offset = el.getBoundingClientRect().top - feedTopEdge();
      break;
    }
    fn();
    const anchor = anchorSel && els.feed.querySelector(anchorSel);
    if (anchor) {
      // Even a no-op assignment cancels a smooth scroll in flight (the Today
      // pill's crosses weeks, and each new cursor week re-renders the feed).
      const delta = anchor.getBoundingClientRect().top - feedTopEdge() - offset;
      if (delta) els.feed.scrollTop += delta;
      lastAnchorAdjustAt = performance.now();
    }
  }

  function feedTopEdge() {
    return els.feed.getBoundingClientRect().top;
  }

  // The pinned week head covers the top of the port; every head has one height.
  function weekHeadHeight() {
    return els.feed.querySelector('.cbm-weekhead')?.offsetHeight ?? 0;
  }

  const rowTemplate = document.createElement('template');

  // The week-start attribute is fixed for a row's lifetime (it's the map
  // key), so it's only ever written once, here, on first build.
  function buildWeekGroupEl(monday, model) {
    rowTemplate.innerHTML = `<section class="${weekGroupClass(monday, model)}" data-week-start="${monday}">${weekGroupInnerHtml(monday, model)}</section>`;
    return rowTemplate.content.firstElementChild;
  }

  function renderFeed() {
    const mondays = store.windowMondays();
    const mondaySet = new Set(mondays);
    // Density changes, preview-state changes and a new list of saved takes
    // (the cards' marks) force a full re-render of every row (still inside
    // withScrollAnchor).
    // A new date (a tab left open past midnight) moves the TODAY tag.
    const key = `${ui.density}:${store.state.planState === 'preview' && ui.preview ? 1 : 0}:${store.state.today}`;
    const forceAll = key !== ui.renderKey || ui.renderedTakes !== store.state.takes;
    ui.renderKey = key;
    ui.renderedTakes = store.state.takes;
    withScrollAnchor(() => {
      for (const [m, row] of [...ui.rows]) {
        if (!mondaySet.has(m)) { row.el.remove(); ui.rows.delete(m); }
      }
      let prevEl = els.feedTop;
      for (const m of mondays) {
        const model = store.state.weeks[m] || null;
        let row = ui.rows.get(m);
        if (!row) {
          row = { el: buildWeekGroupEl(m, model), model };
          ui.rows.set(m, row);
        } else if (forceAll || row.model !== model) {
          // ensureWeeks builds a new week object on every (re)fetch, so
          // object identity alone is a free revision token for "changed".
          row.el.innerHTML = weekGroupInnerHtml(m, model);
          row.model = model;
        }
        stampRow(row.el, m, model);
        if (prevEl.nextElementSibling !== row.el) prevEl.after(row.el);
        prevEl = row.el;
      }
    });
    updateTodayPill();
  }

  /** Put `el` `gap` px below the top of the feed; say whether that moved it. */
  function moveFeedTo(el, gap) {
    const delta = el.getBoundingClientRect().top - feedTopEdge() - gap;
    if (Math.abs(delta) < 1) return false;
    const before = els.feed.scrollTop;
    els.feed.scrollTop += delta;
    lastAnchorAdjustAt = performance.now();
    return els.feed.scrollTop !== before;
  }

  function placeWeek(monday) {
    const row = ui.rows.get(monday);
    return row ? moveFeedTo(row.el, 0) : false;
  }

  // The day's label lands just under the pinned week head, so its first card
  // reads from the title down. A skeleton week has no day sections yet: land
  // on its row until they come.
  function placeDay(date) {
    const sec = root.querySelector(`#cbm-sec-${CSS.escape(date)}`);
    return sec ? moveFeedTo(sec, weekHeadHeight()) : placeWeek(mondayOf(date));
  }

  // The calendar put somewhere on purpose stays there until the page settles
  // or the athlete moves, and the cursor does not read it back off the scroll.
  const landOnDay = (date) => land(() => placeDay(date));
  const landOnWeek = (monday) => land(() => placeWeek(monday));
  function land(place) {
    ui.stopLanding?.();
    ui.landing = true;
    ui.stopLanding = holdLanding(
      place,
      () => !ui.booted || Object.keys(store.state.loadingWeeks).length > 0,
      () => { ui.landing = false; updateTodayPill(); },
    );
  }

  // scrollIntoView scrolls *every* scrollable ancestor, and on iOS that
  // includes the pinned document — the app bar ends up under the status bar.
  // The feed is the only scrollport here, so move just that one.
  function scrollToDay(date) {
    const sec = root.querySelector(`#cbm-sec-${CSS.escape(date)}`);
    if (!sec) return;
    els.feed.scrollTo({
      top: els.feed.scrollTop + sec.getBoundingClientRect().top - feedTopEdge() - weekHeadHeight(),
      behavior: 'smooth',
    });
  }

  // The pill is the way back to today from anywhere today is out of view,
  // this week included (a detail week is several ports tall).
  function updateTodayPill() {
    const sec = root.querySelector(`#cbm-sec-${CSS.escape(store.state.today)}`);
    if (!sec) { els.todayPill.hidden = store.state.weekStart === mondayOf(store.state.today); return; }
    const port = els.feed.getBoundingClientRect();
    const r = sec.getBoundingClientRect();
    els.todayPill.hidden = r.bottom > port.top + weekHeadHeight() && r.top < port.bottom;
  }

  async function onSentinel(entries) {
    for (const entry of entries) {
      if (!entry.isIntersecting || !store.state.windowFrom) continue;
      const dir = entry.target.dataset.dir;
      if (!dir || ui.extending[dir]) continue;
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
  const feedObserver = new IntersectionObserver(onSentinel, { root: els.feed, rootMargin: '600px 0px' });

  // A mobile week group is taller than the port allows more of it to sit
  // above the fold than web's row, so both shells converge on the same
  // 15% line (matches web's CURSOR_LINE).
  const CURSOR_LINE = 0.15;

  let scrollScheduled = false;
  function updateCursorFromScroll() {
    if (scrollScheduled) return;
    scrollScheduled = true;
    requestAnimationFrame(() => {
      scrollScheduled = false;
      const mid = els.feed.scrollTop + els.feed.clientHeight * CURSOR_LINE;
      const row = [...ui.rows.entries()].find(([, r]) => (
        r.el.offsetTop <= mid && mid < r.el.offsetTop + r.el.offsetHeight
      ));
      const placed = ui.landing || performance.now() - lastAnchorAdjustAt < 50;
      if (!placed && row && row[0] !== store.state.weekStart) store.setCursorWeek(row[0]);
      updateTodayPill();
    });
  }
  els.feed.addEventListener('scroll', updateCursorFromScroll, { passive: true });

  // The feed scroll position must survive opening a session detail.
  function restoreFeedScroll() {
    const top = ui.scrollTop;
    ui.scrollTop = null;
    if (top == null) return;
    requestAnimationFrame(() => { els.feed.scrollTop = top; });
  }

  function setDrawerTitle(title) {
    els.plan.setAttribute('aria-label', title);
    const titleEl = els.plan.querySelector('.cbm-drawer-title');
    if (titleEl) titleEl.textContent = title;
  }

  function renderPlanDrawer() {
    // Once the goal date is behind us the drawer opens on the plan's ending:
    // congratulations + new plan / archive, never a replan. A pending
    // preview means the wizard flow is already underway — reopening the
    // drawer must return to it, not to the congratulations screen.
    const ended = planEndedModel(store);
    if (ended && !ui.newPlanFromEnded && !ui.preview && !ui.previewRequest) {
      setDrawerTitle(ended.title);
      els.planBody.innerHTML = `<div style="display:flex;flex-direction:column;gap:10px">
        <div style="font-size:var(--cb-fs-body);color:var(--cb-text-muted);line-height:1.4">${esc(ended.line)}</div>
        <button type="button" class="cb-btn-primary cb-btn-primary--block" data-action="new-plan">Start a new plan</button>
        <button type="button" class="cb-btn-secondary" style="width:100%" data-action="archive-plan">Archive plan</button>
      </div>`;
      return;
    }
    ui.editor = ui.editor || defaultEditor(store);
    syncEditorAthleteData(ui.editor, store.state.athleteData);
    ui.editor.myRaces = store.state.myRaces || [];
    setDrawerTitle(store.state.plannerActive?.active_plan && !ended ? 'Edit plan' : 'Choose a plan');
    const replanRow = store.state.plannerActive?.active_plan && !ended
      ? `<div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--cb-border)">
          <button type="button" data-action="replan" style="width:100%;font:inherit;font-size:var(--cb-fs-body);font-weight:600;color:var(--cb-text);background:var(--cb-surface-3);border:1px solid var(--cb-border);border-radius:9px;padding:10px;cursor:pointer">Replan from next Monday</button>
          <div style="font-size:var(--cb-fs-label);color:var(--cb-text-muted);margin-top:6px;line-height:1.4">Regenerates the rest of your current plan from what you actually trained — this week keeps its sessions.</div>
          <button type="button" data-action="delete-plan" style="width:100%;margin-top:14px;font:inherit;font-size:var(--cb-fs-body);font-weight:600;color:var(--cb-text-muted);background:var(--cb-surface-3);border:1px solid var(--cb-border);border-radius:9px;padding:10px;cursor:pointer">Delete plan</button>
          <div style="font-size:var(--cb-fs-label);color:var(--cb-text-muted);margin-top:6px;line-height:1.4">Removes this plan from endury and its upcoming workouts from your intervals.icu calendar. Earlier workouts and your activities stay.</div>
        </div>`
      : '';
    els.planBody.innerHTML = renderPlanEditor(editorModel(ui.editor, store.state.today)) + replanRow;
  }

  function renderCoachMsgs() {
    const st = coach.state;
    els.msgs.innerHTML = st.messages.map(renderMessage).join('')
      + (st.thinking ? renderThinking(st.thinkingText) : '');
    els.msgs.scrollTop = els.msgs.scrollHeight;
    // A reply that lands while the coach is hidden — the post-commit take,
    // a turn closed mid-stream, a card from a sheet — lights the FAB.
    if (st.replies !== ui.coachReplies && !ui.coachOpen) setCoachUnread(true);
    ui.coachReplies = st.replies;
  }

  // Fetches on open (no polling, no caching) — the panel is a point-in-time
  // snapshot of intervals.icu, not a live view.
  async function renderInputsDrawer() {
    els.inputsBody.innerHTML = '<div style="color:var(--cb-text-muted);font-size:var(--cb-fs-body);padding:20px 0">Loading…</div>';
    try {
      const payload = await api.getInputs();
      showInputsPanel(els.inputsBody, payload);
    } catch (err) {
      showInputsPanel(els.inputsBody, { available: false, reason: err.message });
    }
  }

  function setDrawer(which) {
    ui.drawer = which;
    els.scrim.classList.toggle('open', Boolean(which));
    els.plan.classList.toggle('open', which === 'plan');
    els.inputs.classList.toggle('open', which === 'inputs');
    if (which === 'plan') renderPlanDrawer();
    if (which === 'inputs') renderInputsDrawer();
  }

  function setCoach(open) {
    ui.coachOpen = open;
    els.sheet.classList.toggle('is-open', open);
    if (open) setCoachUnread(false);
  }

  function setCoachUnread(on) {
    els.fab.classList.toggle('has-unread', on);
  }

  function renderAll() {
    renderAppbar();
    renderStrips();
    renderFeed();
  }

  // Surfaces

  function findSession(id) {
    return store.windowMondays().flatMap((m) => store.state.weeks[m]?.sessions || [])
      .find((x) => x.id === id)
      || (store.state.planState === 'preview' && ui.preview?.sessions
        ? ui.preview.sessions.map((p) => toSession(p, {}))
          .find((x) => x.id === String(id))
        : null);
  }

  function moveDaysFor(session) {
    const week = store.state.weeks[mondayOf(session.date)];
    if (!week || session.done || session.status === 'unplanned'
      || session.eventId || session.skipped
      || store.state.planState === 'preview') return [];
    const raceDates = new Set((week.races || []).map((r) => r.race_date));
    return week.days.map((d, i) => ({
      date: d.date,
      dow: DAY_NAMES[i],
      current: d.date === session.date,
      disabled: raceDates.has(d.date),
    }));
  }

  function openSessionDetail(id) {
    const session = findSession(id);
    if (!session) return;
    ui.scrollTop = els.feed.scrollTop;
    ui.modal = openModal(renderSessionDetail(session, {
      moveDays: moveDaysFor(session), ...takesFor(store, session.activityId),
      ...briefsFor(store, `session:${session.id}`),
    }), {
      variant: 'sheet', label: 'Session detail', onClose: restoreFeedScroll, focusDialog: true,
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
    ui.scrollTop = els.feed.scrollTop;
    ui.modal = openModal(renderRaceDetail({ ...race, goalLabel: GOAL_LABELS[race.goal_type] || null }, {
      todayIso: store.state.today, ...briefsFor(store, `race:${date}`),
    }), {
      variant: 'sheet', label: 'Race detail', onClose: restoreFeedScroll, focusDialog: true,
    });
    fillBrief(ui.modal.el, `race:${date}`);
  }

  function moveSession(sessionId, date) {
    return sharedMoveSession(store, ui, sessionId, date, renderAll, { confirmVariant: 'sheet' });
  }

  // Plan lifecycle

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
        const ok = await openConfirm({ ...shortHorizonConfirmCopy(e.goal, join), variant: 'sheet' });
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
    setDrawer(null);
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
        } else if (ev.type === 'result') result = ev;
        else if (ev.type === 'error') result = ev;
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
          const ok = await openConfirm({
            ...shortHorizonConfirmCopy(e.goal, {
              min: result.min_weeks, weeks: result.actual_weeks, deficit: result.deficit_weeks,
            }),
            variant: 'sheet',
          });
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
          const ok = await openConfirm({
            ...experimentalPlanConfirmCopy(result.error_message || ''),
            variant: 'sheet',
          });
          if (ok) {
            e.experimentalAck = true;
            return startGenerate(true);
          }
          // A "no" here answers a question, it isn't a failed generation:
          // back to the drawer with the athlete's inputs untouched, no
          // error toast.
          store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
          setDrawer('plan');
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
        setDrawer('plan');
      }
    } catch (err) {
      ui.preview = null;
      ui.previewRequest = null;
      store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
      showToast(`Plan generation failed: ${err.message}`);
      setDrawer('plan');
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
        variant: 'sheet',
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

  // Everything the agenda shows is cached, so a plan write that did not come
  // from this shell (a replan, or a coach tool call in chat) has to evict
  // those caches and re-read — one path for both.
  function refreshPlanViews() {
    return sharedRefreshPlanViews(store, ui, renderAll);
  }

  function applyPlan() {
    return sharedApplyPlan(store, ui, {
      notePush,
      renderAll,
      coachPostCommitTake: () => coach.postCommitTake(),
      confirmVariant: 'sheet',
      resetExtra: () => { ui.newPlanFromEnded = false; },
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
        } else if (ev.type === 'result' || ev.type === 'error') result = ev;
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
      body: 'The rest of your plan is regenerated from next Monday based on what you actually trained — this week keeps its sessions. The updated plan is applied directly, and your intervals.icu calendar is updated with it. Manual reschedules in future weeks will be reset.',
      confirmLabel: 'Replan',
      variant: 'sheet',
    });
    if (!ok) return;
    setDrawer(null);
    const refusal = await runReplan(false);
    if (!refusal) return;
    const overwrite = await openConfirm({
      ...REPLAN_OVERWRITE_CONFIRM,
      body: renderReplanDiff(refusal.diff),
      variant: 'sheet',
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
      const ok = await openConfirm({ ...NEW_CHAT_CONFIRM, variant: 'sheet' });
      if (!ok) return;
    }
    coach.newChat();
  }

  // One backend verb (DELETE /api/planner/active) under two framings: the
  // post-race "Archive plan" and the mid-plan "Delete plan".
  function archivePlan({ mode = 'archive' } = {}) {
    return sharedArchivePlan(store, ui, {
      mode,
      notePush,
      renderAll,
      confirmVariant: 'sheet',
      onDeleteClose: () => setDrawer(null),
      resetExtra: () => { ui.newPlanFromEnded = false; },
      afterRender: () => renderPlanDrawer(),
    });
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
    switch (btn.dataset.action) {
      case 'set-density': ui.density = btn.dataset.density; renderAppbar(); renderFeed(); break;
      case 'prev-week': await navigateWeek(store.gotoWeek(-1)); landOnWeek(store.state.weekStart); break;
      case 'next-week': await navigateWeek(store.gotoWeek(1)); landOnWeek(store.state.weekStart); break;
      case 'today': await navigateWeek(store.gotoToday()); landOnDay(store.state.today); break;
      case 'select-day': store.selectDay(btn.dataset.date); scrollToDay(btn.dataset.date); break;
      case 'goto-day':
        await navigateWeek(store.gotoDay(btn.dataset.date));
        landOnDay(btn.dataset.date);
        break;
      case 'open-session': openSessionDetail(btn.dataset.id); break;
      case 'delete-adhoc':
        sharedDeleteAdHocSession(store, ui, btn.dataset.externalId, renderAll, {
          confirmVariant: 'sheet',
        });
        break;
      case 'pair-activity':
        ui.modal?.close();
        sharedPairActivity(store, ui, btn.dataset.activityId, JSON.parse(btn.dataset.body), btn.dataset.label, renderAll);
        break;
      case 'unlink-activity':
        ui.modal?.close();
        sharedUnlinkActivity(store, ui, btn.dataset.activityId, renderAll);
        break;
      case 'ask-coach': {
        const when = btn.dataset.date ? ` on ${btn.dataset.date}` : '';
        ui.modal?.close();
        setCoach(true);
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
      case 'open-coach': setCoach(true); break;
      case 'close-coach': setCoach(false); break;
      case 'open-plan': ui.newPlanFromEnded = false; setDrawer('plan'); break;
      case 'new-plan': ui.newPlanFromEnded = true; setDrawer('plan'); break;
      case 'open-inputs': setDrawer('inputs'); break;
      case 'open-settings': ui.modal = openSettingsPanel({ variant: 'sheet' }); break;
      case 'refresh': refreshFromIntervals(store, ui, btn, renderAll); break;
      case 'explain-tss': ui.hintModal?.close(); ui.hintModal = openTssExplainer({ variant: 'sheet', topic: btn.dataset.topic || 'tss' }); break;
      case 'archive-plan': archivePlan(); break;
      case 'delete-plan': archivePlan({ mode: 'delete' }); break;
      case 'close-drawer': setDrawer(null); break;
      case 'set-family':
        ui.editor.family = btn.dataset.fam;
        ui.editor.goal = defaultGoalForFamily(ui.editor.family);
        ui.editor.goalPickedByHand = false;
        ui.editor.shortHorizonAck = false;
        ui.editor.experimentalAck = false;
        renderPlanDrawer();
        break;
      case 'set-goal':
        ui.editor.goal = btn.dataset.goal;
        ui.editor.goalPickedByHand = true;
        ui.editor.shortHorizonAck = false;
        ui.editor.experimentalAck = false;
        renderPlanDrawer();
        break;
      case 'set-ambition':
        ui.editor.ambition = btn.dataset.amb;
        ui.editor.experimentalAck = false;
        renderPlanDrawer();
        break;
      case 'maint-weeks-adjust':
        ui.editor.maintWeeks = Math.min(16, Math.max(4,
          (ui.editor.maintWeeks || 8) + parseInt(btn.dataset.delta, 10)));
        renderPlanDrawer();
        break;
      case 'add-race':
        ui.editor.races.push({
          id: `r${Math.round(performance.now())}`,
          goal_type: defaultTuneUpGoalFor(ui.editor.family),
          date: '',
          priority: 'C',
        });
        renderPlanDrawer();
        break;
      case 'remove-race':
        ui.editor.races = ui.editor.races.filter((r) => r.id !== btn.dataset.id);
        renderPlanDrawer();
        break;
      case 'set-priority': {
        const race = ui.editor.races.find((r) => r.id === btn.dataset.id);
        if (race) race.priority = btn.dataset.pri;
        renderPlanDrawer();
        break;
      }
      case 'race-chip': {
        const race = (ui.editor.myRaces || []).find((r) => String(r.id) === btn.dataset.id);
        if (race) applyRaceChip(ui.editor, race);
        renderPlanDrawer();
        break;
      }
      case 'preview-plan': startGenerate(); break;
      case 'apply-plan': applyPlan(); break;
      case 'replan': startReplan(); break;
      case 'push-again': case 'sync-plan': pushAgain(); break;
      case 'dismiss-push': ui.push = null; renderStrips(); break;
      case 'discard-plan': {
        ui.preview = null;
        ui.previewRequest = null;
        store.setPlanState(store.state.plannerActive?.active_plan ? 'active' : 'none');
        showToast(store.state.plannerActive?.active_plan ? 'Preview discarded — keeping your current plan' : 'Preview discarded');
        renderAll();
        break;
      }
      case 'digest': coach.digest(Boolean(store.state.plannerActive?.active_plan) && !planEndedModel(store)); break;
      case 'open-memory': ui.modal = openMemoryPanel({ variant: 'sheet' }); break;
      case 'new-chat': startNewChat(); break;
      case 'chip': coach.send(btn.dataset.text); break;
      case 'coach-ideas': ui.modal = openCoachIdeas({ variant: 'sheet', messages: coach.state.messages }); break;
      case 'use-idea':
        ui.modal?.close();
        els.draft.value = btn.dataset.text;
        els.draft.focus();
        break;
      case 'compare-route':
        ui.modal?.close();
        setCoach(true);
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

  document.addEventListener('change', (ev) => {
    const input = ev.target.closest('[data-action]');
    if (!input || !ui.editor) return;
    if (input.dataset.action === 'a-race-date') {
      ui.editor.aRaceDate = input.value;
      ui.editor.shortHorizonAck = false;
      ui.editor.experimentalAck = false;
      renderPlanDrawer();
    } else if (input.dataset.action === 'race-date') {
      const race = ui.editor.races.find((r) => r.id === input.dataset.id);
      if (race) race.date = input.value;
      renderPlanDrawer();
    } else if (input.dataset.action === 'race-goal') {
      const race = ui.editor.races.find((r) => r.id === input.dataset.id);
      if (race) race.goal_type = input.value;
      renderPlanDrawer();
    }
  });

  // Self-assessment fields update state per keystroke; re-rendering the drawer
  // here would drop focus, so only the CTA's enabled state is synced in place.
  document.addEventListener('input', (ev) => {
    const input = ev.target.closest('[data-action]');
    if (!input || !ui.editor) return;
    if (handleTrailProfileInput(ui.editor, input)) return;
    const key = { 'assess-hours': 'hours', 'assess-longest': 'longest' }[input.dataset.action];
    if (!key) return;
    (ui.editor.assess[input.dataset.sport] ||= { hours: '', longest: '' })[key] = input.value.trim();
    const cta = els.planBody.querySelector('[data-action="preview-plan"]');
    if (cta) cta.disabled = editorModel(ui.editor, store.state.today).ctaDisabled;
  });

  els.draft.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      const text = els.draft.value;
      els.draft.value = '';
      coach.send(text);
    }
  });

  // Escape closes the open drawer, else the coach (modals handle their own
  // Escape in modal.js and stop propagation before this runs).
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (ui.drawer) setDrawer(null);
    else if (ui.coachOpen) setCoach(false);
  });

  // Boot
  store.subscribe(renderAll);
  store.onLand(() => afterSheets(() => landOnDay(store.state.today)));
  store.init().then(async () => {
    // A boot read that failed has to say so: silence here reads as "no plan
    // yet" or as an eternal "Loading…" with nothing naming the cause.
    if (store.state.plannerActiveFailed) {
      showToast('Could not read your training plan — reload to try again');
    } else if (store.state.error) {
      showToast(`Could not load your calendar: ${store.state.error}`);
    }
    // A missing plan is a legitimate calendar state, not a wall: completed
    // activities are read live from intervals.icu, so past weeks stay
    // meaningful with nothing planned. Creating one stays one tap away via
    // the appbar's "Create a plan" pill, without blocking navigation behind it.
    // Sync state is only knowable by asking intervals.icu, so the plan
    // view reads it once on boot. The weeks cached before it arrived are
    // dropped so their sessions re-adapt with it — and the visible window is
    // re-read straight away, since an empty cache renders as skeleton rows
    // and nothing else on the boot path would ever refill it.
    await refreshSync();
    store.state.weeks = {};
    await store.initWindow(store.state.weekStart, { force: true })
      .catch((err) => showToast(`Could not load your calendar: ${err.message}`));
    renderAll();
    // The boot scroll can land a sentinel inside the 600px rootMargin (e.g. a
    // short week above today's), so observing right away would fire an
    // upward extendWindow before the user has touched the feed. Wait for the
    // first real scroll instead — but a 'scroll' event alone isn't proof of
    // one: withScrollAnchor's own scrollTop reassignment (see above) fires
    // one too. A real scroll never lands within the same frame as that
    // reassignment, so ignore any 'scroll' event that does.
    requestAnimationFrame(() => {
      function armOnRealScroll() {
        if (performance.now() - lastAnchorAdjustAt < 50) return;
        els.feed.removeEventListener('scroll', armOnRealScroll);
        feedObserver.observe(els.feedTop);
        feedObserver.observe(els.feedBottom);
      }
      els.feed.addEventListener('scroll', armOnRealScroll);
    });
  }).finally(() => { ui.booted = true; });
  coach.init();
  renderAll();
  // The store opens on today, or on the place a quick return remembered: land
  // there from the first frame, so the weeks fill in around it.
  landOnDay(store.state.selectedDay);
}
