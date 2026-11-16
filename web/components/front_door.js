/* Endury — the front door: what a browser with no intervals.icu key sees.
   Three screens rendered into the boot screen's body: the landing (demo,
   the guided connect flow, or straight to the key), the connect flow (the
   training log, then the key in a real <form> so a password manager saves
   it), and — after the first boot — the ready screen that checks history
   and watch upload. Each screen attaches its listeners to the element it
   rendered, so the next screen's innerHTML drops them. Its buttons carry
   data-door, not data-action: no shell is mounted yet. */

import { esc } from './util.js';
import {
  field, renderAdvanced, readSettingsForm, legalLinksHtml, TERMS_BOX,
} from './settings_panel.js';
import { saveSettings } from '../data/settings.js';

const INTERVALS = 'https://intervals.icu';
const INTERVALS_SETTINGS = 'https://intervals.icu/settings';

const SPORT_LABELS = { running: 'Run', cycling: 'Bike', swimming: 'Swim' };

/** Render `html` into `body`; `onDoor(door, button)` runs for every
    [data-door] click inside it. Returns the rendered root. */
function render(body, html, onDoor) {
  body.innerHTML = html;
  const root = body.firstElementChild;
  root.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-door]');
    if (btn) onDoor(btn.dataset.door, btn);
  });
  return root;
}

const externalLink = (href, label) =>
  `<a class="cb-confirm__btn cb-door__ext" href="${href}" target="_blank" rel="noopener">${label} ↗</a>`;

function progress(step) {
  const bars = [1, 2, 3].map((i) => `<i class="${i <= step ? 'is-done' : ''}"></i>`).join('');
  return `<div class="cb-door__progress">
      <span>Step ${step} of 3</span><span class="cb-door__bars" aria-hidden="true">${bars}</span>
    </div>`;
}

/* ── Landing ─────────────────────────────────────────────────────────── */

const DOORS = [
  {
    door: 'connect',
    label: 'Start with your training',
    note: 'Needs a free intervals.icu account. About five minutes.',
    primary: true,
  },
  { door: 'demo', label: 'Try the demo', note: 'A sample athlete, mid-plan. Nothing to sign up for.' },
];

const FACTS = [
  'Free and open source. No subscription, no Endury account.',
  'No Endury server holds your data. Your keys stay in this browser.',
  'Your plan and training live on your own intervals.icu.',
  'An AI coach that knows your plan and reworks your week when you ask. Optional, from about $0.008 a message with the default AI model through your own OpenRouter account; a full debrief or analysis costs more.',
  'Training software for adults, not medical advice. A hobby project in its early days.',
];

/* The privacy page's essentials, for the landing's fold-out. */
const PRIVACY = [
  'Your intervals.icu key goes only to intervals.icu. It has full access to your account; delete it there to stop Endury.',
  'Endury keeps your plan, chat and the coach’s memory in three hidden notes on your own intervals.icu calendar, so every device sees the same plan. They are not encrypted, so a coach or app you gave access there may be able to read them.',
  'Chat is optional: with your own OpenRouter key, and only after you agree in Settings, your messages and training context — health data such as sleep, resting heart rate and HRV included — go to OpenRouter (USA) and the company that runs the AI model.',
  'The demo is built in this tab. Your browser keeps only that it runs the demo and your last place.',
];

/** The landing; resolves 'demo' | 'connect' | 'key'. `error` (a failed
    OpenRouter Connect) shows above the doors. */
export function showLanding(body, { error } = {}) {
  const doors = DOORS.map((d) => `<div class="cb-door__door">
      <button type="button" class="cb-confirm__btn cb-door__btn${d.primary ? ' cb-confirm__btn--primary' : ''}"
        data-door="${d.door}">${d.label}</button>
      <div class="cb-door__note">${d.note}</div>
    </div>`).join('');
  return new Promise((resolve) => {
    render(body, `<div class="cb-door">
        <h1 class="cb-door__title">A free coach for your next race.</h1>
        <p class="cb-door__lede">Pick a race. Endury builds the whole preparation from the training
          you already do, sends your sessions to your watch where intervals.icu supports it, and adapts
          when life gets in the way.</p>
        ${error ? `<div class="cb-boot__error" role="alert">${esc(error)}</div>` : ''}
        <div class="cb-door__doors">${doors}
          <div class="cb-door__row cb-door__row--center">
            <button type="button" class="cb-door__link" data-door="key">I already have my intervals.icu key</button>
          </div>
        </div>
        <ul class="cb-door__facts">${FACTS.map((f) => `<li>${f}</li>`).join('')}</ul>
        <details class="cb-door__privacy">
          <summary>Where your data goes</summary>
          <ul class="cb-door__facts">${PRIVACY.map((f) => `<li>${f}</li>`).join('')}</ul>
        </details>
        <div class="cb-door__legal">${legalLinksHtml()}</div>
      </div>`, (door) => resolve(door));
  });
}

/* ── Connect flow ────────────────────────────────────────────────────── */

function logStepHtml() {
  return `<div class="cb-door" data-step="log">
      ${progress(1)}
      <h1 class="cb-door__title">Your training log</h1>
      <p class="cb-door__lede">Endury doesn’t talk to your watch directly. It works through
        intervals.icu, a free training log. Connect your watch brand there, not Strava: activities
        that arrive only through Strava don’t reach apps like Endury. intervals.icu expects Garmin
        activity data may stop reaching other apps too; files you upload yourself should keep working.
        Endury’s workouts go out to Garmin, Wahoo, Coros, Suunto, Zepp, Huawei and Zwift, when you
        turn that on in intervals.icu.</p>
      <ol class="cb-door__steps">
        <li><div>Create a free intervals.icu account.</div>${externalLink(INTERVALS, 'Open intervals.icu')}</li>
        <li><div>In intervals.icu, open Settings and connect your watch brand. Your recent activities
          appear within a few minutes.</div></li>
      </ol>
      <button type="button" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn" data-door="next">Done, next</button>
      <div class="cb-door__row">
        <button type="button" class="cb-door__quiet" data-door="back">Back</button>
        <button type="button" class="cb-door__link" data-door="skip">I already use intervals.icu</button>
      </div>
    </div>`;
}

function keyStepHtml(settings, intro) {
  return `<div class="cb-door" data-step="key">
      ${progress(2)}
      <h1 class="cb-door__title">Your key</h1>
      ${intro ? `<div class="cb-boot__error" role="alert">${esc(intro)}</div>` : ''}
      <p class="cb-door__lede">In intervals.icu, open
        <a href="${INTERVALS_SETTINGS}" target="_blank" rel="noopener">Settings</a> and scroll to
        Developer Settings near the bottom. Generate an API key if there is none, copy it and paste
        it here.</p>
      <form class="cb-door__form">
        ${field('athlete_name', 'Your name', settings.athlete_name, {
          autocomplete: 'username',
          hint: 'Optional. Lets your browser save the key under your name.',
        })}
        ${field('intervals_api_key', 'intervals.icu API key', settings.intervals_api_key, {
          type: 'password',
          autocomplete: 'current-password',
        })}
        <div class="cb-door__box">
          <div class="cb-door__box-title">What this key does</div>
          intervals.icu API keys have full access to your account, with no narrower option. Endury uses
          yours to read your training, write and remove the workouts and races it created, pair
          activities, save the thresholds and feel you enter, and keep its hidden notes. It stays in this
          browser and goes only to intervals.icu. Delete it in intervals.icu at any time and Endury stops.
        </div>
        ${TERMS_BOX}
        ${renderAdvanced(settings)}
        <span class="cb-settings__status" role="status"></span>
        <button type="submit" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn">Connect</button>
        <div class="cb-door__note">Coach chat is optional and comes later, in Settings.</div>
      </form>
      <div class="cb-door__row">
        <button type="button" class="cb-door__quiet" data-door="back">Back</button>
      </div>
    </div>`;
}

/** The guided connect flow. Resolves the new settings (saved already:
    `settings` with the key step's fields over it and the demo ended), or
    null when the athlete goes back from its first screen. */
export function runConnect(body, settings, { startAt = 'log', intro = '' } = {}) {
  return new Promise((resolve) => {
    const showLog = () => render(body, logStepHtml(), (door) => {
      if (door === 'back') resolve(null);
      if (door === 'next' || door === 'skip') showKey();
    });

    const showKey = () => {
      const root = render(body, keyStepHtml(settings, intro), (door) => {
        if (door !== 'back') return;
        if (startAt === 'key') resolve(null);
        else showLog();
      });
      const form = root.querySelector('form');
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const next = { ...settings, ...readSettingsForm(form), demo: '' };
        if (!next.intervals_api_key) {
          form.querySelector('.cb-settings__status').textContent = 'Your intervals.icu API key is required.';
          form.querySelector('[name="intervals_api_key"]').focus();
          return;
        }
        saveSettings(next);
        resolve(next);
      });
    };

    if (startAt === 'key') showKey();
    else showLog();
  });
}

/* ── Ready screen ────────────────────────────────────────────────────── */

const CHECK_ICONS = {
  ok: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round"><path d="M12 6v7M12 18h.01"/></svg>',
};

function checkRow(check, state, title, detail = '', extra = '') {
  return `<div class="cb-check" data-check="${check}" data-state="${state}">
      <span class="cb-check__icon" aria-hidden="true">${CHECK_ICONS[state]}</span>
      <div class="cb-check__text">
        <div class="cb-check__title">${esc(title)}</div>
        ${detail ? `<div class="cb-check__detail">${esc(detail)}</div>` : ''}
        ${extra}
      </div>
    </div>`;
}

/* The per-goal hour blocks merged into one line per sport: the most any
   goal measured ("Run 3.1 h/week · Bike 2.4 h/week"). */
function hoursLine(measured = {}) {
  const hours = {};
  Object.values(measured).forEach((block) => Object.entries(block || {}).forEach(([sport, h]) => {
    hours[sport] = Math.max(hours[sport] || 0, Number(h) || 0);
  }));
  return Object.entries(hours)
    .filter(([, h]) => h > 0)
    .map(([sport, h]) => `${SPORT_LABELS[sport] || sport} ${h.toFixed(1)} h/week`)
    .join(' · ');
}

function historyRow(bootstrap) {
  const data = bootstrap?.athlete_data;
  if (data?.history_sufficient) {
    const line = hoursLine(data.measured_weekly_hours);
    return checkRow('history', 'ok', 'Training history found',
      line ? `${line}. Your plan starts from this.` : 'Your plan starts from your recent training.');
  }
  const detail = data?.available_reason
    ? `${data.available_reason} The plan wizard asks about your recent weeks instead.`
    : 'The plan wizard asks about your recent weeks instead.';
  return checkRow('history', 'warn', 'Little training on intervals.icu yet', detail);
}

function uploadRow(inputs) {
  const destinations = inputs?.upload_destinations || [];
  if (destinations.length) {
    return checkRow('upload', 'ok', `Workouts will upload to ${destinations.join(', ')}`,
      'Endury’s sessions go out through that connection.');
  }
  return checkRow('upload', 'warn', 'Workouts don’t reach your watch yet',
    'In intervals.icu Settings, tick Upload planned workouts in the Garmin box and approve it '
      + 'on Garmin Connect. Endury’s sessions then appear on your watch.',
    `<div class="cb-check__actions">
        ${externalLink(INTERVALS_SETTINGS, 'Open intervals.icu')}
        <button type="button" class="cb-confirm__btn" data-door="recheck">Check again</button>
      </div>`);
}

/** The ready screen after the first boot; resolves 'plan' | 'look'.
    `bootstrap` / `inputs` are null when their read failed; `recheck()`
    resolves fresh inputs for the upload row. */
export function showReady(body, { settings, bootstrap, inputs, recheck }) {
  const connected = settings.athlete_name ? `Connected as ${settings.athlete_name}` : 'Connected to intervals.icu';
  return new Promise((resolve) => {
    const root = render(body, `<div class="cb-door" data-step="ready">
        ${progress(3)}
        <h1 class="cb-door__title">You’re connected</h1>
        <div class="cb-door__box cb-door__checks">
          ${checkRow('connected', 'ok', connected)}
          ${historyRow(bootstrap)}
          ${uploadRow(inputs)}
        </div>
        <button type="button" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn" data-door="plan">Plan my race</button>
        <div class="cb-door__row cb-door__row--center">
          <button type="button" class="cb-door__link" data-door="look">Look around first</button>
        </div>
      </div>`, async (door, btn) => {
      if (door === 'plan' || door === 'look') resolve(door);
      if (door !== 'recheck') return;
      btn.disabled = true;
      btn.textContent = 'Checking…';
      let fresh = null;
      try {
        fresh = await recheck();
      } catch { /* still unknown: the warn row stays */ }
      root.querySelector('[data-check="upload"]').outerHTML = uploadRow(fresh);
    });
  });
}
