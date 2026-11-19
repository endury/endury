/* Endury — the front door: what a browser with no intervals.icu account
   sees. Three screens rendered into the boot screen's body: the landing
   (demo, the guided connect flow, or straight to its last step), the
   connect flow (the training log, then Connect intervals.icu where this
   copy has an app there (data/intervals_connect.js), or the API key in a
   real <form> so a password manager saves it), and — after the first
   boot — the ready screen that checks history
   and watch upload. Each screen attaches its listeners to the element it
   rendered, so the next screen's innerHTML drops them. Its buttons carry
   data-door, not data-action: no shell is mounted yet. */

import { esc } from './util.js';
import {
  field, renderAdvanced, readSettingsForm, legalLinksHtml,
} from './settings_panel.js';
import { saveSettings } from '../data/settings.js';
import { canConnect, startConnect } from '../data/intervals_connect.js';

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
  const bars = [1, 2].map((i) => `<i class="${i <= step ? 'is-done' : ''}"></i>`).join('');
  return `<div class="cb-door__progress">
      <span>Step ${step} of 2</span><span class="cb-door__bars" aria-hidden="true">${bars}</span>
    </div>`;
}

const more = (summary, html) => `<details class="cb-door__more">
      <summary>${summary}</summary>${html}</details>`;

/* The terms in one line under the button; the rest folds out below it. */
const TERMS_LINE = `<div class="cb-door__note">By connecting you accept the
        <a href="impressum.html#terms" target="_blank" rel="noopener">terms of use</a>.
        Training software for adults, not medical advice.</div>`;
const BEFORE_YOU_START = more('Before you start', `<p>Endury comes as is. See a doctor before you
        start or return to hard training, and stop when something hurts. The coach chat is an AI
        model that can be wrong. The
        <a href="privacy.html" target="_blank" rel="noopener">privacy policy</a> explains your data.</p>`);

/* ── Landing ─────────────────────────────────────────────────────────── */

const FACTS = [
  'Endury builds the whole preparation from the training you already do, sends your sessions to your watch where intervals.icu supports it, and adapts when life gets in the way.',
  'Free and open source. No subscription, no Endury account.',
  'No Endury server holds your data. Your keys stay in this browser.',
  'Your plan and training live on your own intervals.icu.',
  'An AI coach that knows your plan and reworks your week when you ask. Optional, from about $0.008 a message with the default AI model through your own OpenRouter account; a full debrief or analysis costs more.',
  'Training software for adults, not medical advice. A hobby project in its early days.',
];

/* The privacy page's essentials, for the landing's fold-out. */
const KEY_PRIVACY = 'Your intervals.icu key goes only to intervals.icu. It has full access to your account; delete it there to stop Endury.';
const CONNECT_PRIVACY = 'Connect asks intervals.icu for your activities, wellness, calendar and settings, not your chats or workout library. That access stays in this browser and goes only to intervals.icu; remove Endury in intervals.icu’s settings to stop it.';
const PRIVACY = [
  'Endury keeps your plan, chat and the coach’s memory in three hidden notes on your own intervals.icu calendar, so every device sees the same plan. They are not encrypted, so a coach or app you gave access there may be able to read them.',
  'Chat is optional: with your own OpenRouter key, and only after you agree in Settings, your messages and training context — health data such as sleep, resting heart rate and HRV included — go to OpenRouter (USA) and the company that runs the AI model.',
  'The demo is built in this tab. Your browser keeps only that it runs the demo and your last place.',
];

/** The landing; resolves 'demo' | 'connect' | 'key'. `error` (a failed
    Connect) shows above the doors. */
export function showLanding(body, { settings = {}, error } = {}) {
  const facts = (items) => `<ul class="cb-door__facts">${items.map((f) => `<li>${f}</li>`).join('')}</ul>`;
  return new Promise((resolve) => {
    render(body, `<div class="cb-door">
        <h1 class="cb-door__title">A free coach for your next race.</h1>
        <p class="cb-door__lede">Pick a race. Endury plans the training and sends it to your watch.</p>
        ${error ? `<div class="cb-boot__error" role="alert">${esc(error)}</div>` : ''}
        <div class="cb-door__doors">
          <div class="cb-door__ask">Do you use intervals.icu?</div>
          <button type="button" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn" data-door="key">Yes, connect it</button>
          <button type="button" class="cb-confirm__btn cb-door__btn" data-door="connect">Not yet, set it up</button>
          <div class="cb-door__note">A free account. About five minutes.</div>
          <div class="cb-door__row cb-door__row--center">
            <button type="button" class="cb-door__link" data-door="demo">Try the demo first</button>
          </div>
        </div>
        <div class="cb-door__note">Free · open source · your data stays yours</div>
        ${more('What Endury is', facts(FACTS))}
        ${more('Where your data goes', facts([canConnect(settings) ? CONNECT_PRIVACY : KEY_PRIVACY, ...PRIVACY]))}
        <div class="cb-door__legal">${legalLinksHtml()}</div>
      </div>`, (door) => resolve(door));
  });
}

/* ── Connect flow ────────────────────────────────────────────────────── */

function logStepHtml() {
  return `<div class="cb-door" data-step="log">
      ${progress(1)}
      <h1 class="cb-door__title">Set up intervals.icu</h1>
      <p class="cb-door__lede">Endury reads your training and reaches your watch through
        intervals.icu, a free training log.</p>
      <ol class="cb-door__steps">
        <li><div>Create a free account.</div>${externalLink(INTERVALS, 'Open intervals.icu')}</li>
        <li><div>In its Settings, connect your watch brand, not Strava.</div></li>
      </ol>
      ${more('Which watches work', `<p>Endury’s workouts go out to Garmin, Wahoo, Coros, Suunto,
        Zepp, Huawei and Zwift, when you turn that on in intervals.icu. Activities that arrive only
        through Strava don’t reach apps like Endury. intervals.icu expects Garmin activity data may
        stop reaching other apps too; files you upload yourself should keep working. Your recent
        activities appear within a few minutes.</p>`)}
      <button type="button" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn" data-door="next">Done, connect</button>
      <div class="cb-door__row">
        <button type="button" class="cb-door__quiet" data-door="back">Back</button>
      </div>
    </div>`;
}

function keyStepHtml(settings, intro, connect, guided) {
  return `<div class="cb-door" data-step="key">
      ${guided ? progress(2) : ''}
      <h1 class="cb-door__title">Your key</h1>
      ${intro ? `<div class="cb-boot__error" role="alert">${esc(intro)}</div>` : ''}
      <p class="cb-door__lede">In intervals.icu, open
        <a href="${INTERVALS_SETTINGS}" target="_blank" rel="noopener">Settings</a>, scroll to
        Developer Settings and copy your API key.</p>
      <form class="cb-door__form">
        ${field('athlete_name', 'Your name', settings.athlete_name, {
          autocomplete: 'username',
          hint: 'Optional. Lets your browser save the key under your name.',
        })}
        ${field('intervals_api_key', 'intervals.icu API key', settings.intervals_api_key, {
          type: 'password',
          autocomplete: 'current-password',
        })}
        <span class="cb-settings__status" role="status"></span>
        <button type="submit" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn">Connect</button>
        ${TERMS_LINE}
        ${more('What this key does', `<p>intervals.icu API keys have full access to your account,
          with no narrower option. Generate one in Developer Settings if there is none. Endury uses
          yours to read your training, write and remove the workouts and races it created, pair
          activities, save the thresholds and feel you enter, and keep its hidden notes. It stays in
          this browser and goes only to intervals.icu. Delete it in intervals.icu at any time and
          Endury stops.</p>`)}
        ${BEFORE_YOU_START}
        ${renderAdvanced(settings)}
      </form>
      <div class="cb-door__row">
        <button type="button" class="cb-door__quiet" data-door="back">Back</button>
        ${connect ? '<button type="button" class="cb-door__link" data-door="use-connect">Connect intervals.icu instead</button>' : ''}
      </div>
    </div>`;
}

function connectStepHtml(settings, intro, guided) {
  return `<div class="cb-door" data-step="connect">
      ${guided ? progress(2) : ''}
      <h1 class="cb-door__title">Connect intervals.icu</h1>
      ${intro ? `<div class="cb-boot__error" role="alert">${esc(intro)}</div>` : ''}
      <p class="cb-door__lede">Sign in there, approve, and you come straight back.</p>
      <div class="cb-door__form">
        <span class="cb-settings__status" role="status"></span>
        <button type="button" class="cb-confirm__btn cb-confirm__btn--primary cb-door__btn" data-door="oauth">Connect intervals.icu</button>
        ${TERMS_LINE}
        ${more('What Endury asks for', `<p>Your activities, wellness, calendar and settings: to read
          your training, write and remove the workouts and races it created, pair activities, save
          the thresholds and feel you enter, and keep its hidden notes. Not your chats or your
          workout library. The access stays in this browser and goes only to intervals.icu. Remove
          Endury in <a href="${INTERVALS_SETTINGS}" target="_blank" rel="noopener">intervals.icu’s
          settings</a> at any time and it stops.</p>`)}
        ${BEFORE_YOU_START}
        ${renderAdvanced(settings)}
      </div>
      <div class="cb-door__row">
        <button type="button" class="cb-door__quiet" data-door="back">Back</button>
        <button type="button" class="cb-door__link" data-door="use-key">Use an API key instead</button>
      </div>
    </div>`;
}

/** The guided connect flow. Its last step is Connect where this copy can
    (each of the two screens links to the other), else the key; an athlete
    correcting a stored key starts on the key. Resolves the new settings
    (saved already: `settings` with the key step's fields over it and the
    demo ended), or null when the athlete goes back from its first screen.
    Connect leaves the page instead; boot finishes it on the way back. */
export function runConnect(body, settings, { startAt = 'log', intro = '' } = {}) {
  const connect = canConnect(settings);
  // `intro` belongs to the screen the step opens on.
  const keyFirst = !connect || Boolean(settings.intervals_api_key);
  const guided = startAt !== 'key';
  return new Promise((resolve) => {
    const back = () => (startAt === 'key' ? resolve(null) : showLog());

    const showLog = () => render(body, logStepHtml(), (door) => {
      if (door === 'back') resolve(null);
      if (door === 'next') showLast();
    });

    const showConnect = () => {
      const root = render(body, connectStepHtml(settings, keyFirst ? '' : intro, guided), async (door) => {
        if (door === 'back') back();
        if (door === 'use-key') showKey();
        if (door !== 'oauth') return;
        // The Advanced overrides ride along: boot reads them on the way back.
        const next = { ...settings, ...readSettingsForm(root) };
        try {
          if (!saveSettings(next)) throw new Error('storage');
          await startConnect(next);
        } catch {
          root.querySelector('.cb-settings__status').textContent = 'This browser is blocking '
            + 'storage, so Connect can’t work here. Use an API key instead.';
        }
      });
    };

    const showKey = () => {
      const root = render(body, keyStepHtml(settings, keyFirst ? intro : '', connect, guided), (door) => {
        if (door === 'back') back();
        if (door === 'use-connect') showConnect();
      });
      const form = root.querySelector('form');
      form.addEventListener('submit', (e) => {
        e.preventDefault();
        const next = {
          ...settings, ...readSettingsForm(form), intervals_access_token: '', demo: '',
        };
        if (!next.intervals_api_key) {
          form.querySelector('.cb-settings__status').textContent = 'Your intervals.icu API key is required.';
          form.querySelector('[name="intervals_api_key"]').focus();
          return;
        }
        saveSettings(next);
        resolve(next);
      });
    };

    const showLast = keyFirst ? showKey : showConnect;
    if (startAt === 'key') showLast();
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
