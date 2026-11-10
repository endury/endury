/* Endury — settings: the intervals.icu key (required) and athlete id, the
   optional OpenRouter key and model for coach chat, an Advanced section
   of base-URL overrides (the e2e fakes use them), and the theme
   (theme.js), which applies on tap without the Save. The header's Settings
   panel renders it; the front door's key step (components/front_door.js)
   reuses its field and Advanced helpers. The intervals.icu group is a real
   <form>, so a password manager offers to save the key under the name. Self-contained like the memory panel: its buttons carry
   data-settings, not data-action, so no shell delegation is involved.
   Saving from the panel reloads the page — the Python worker was booted
   with the old keys. "Connect OpenRouter" (data/openrouter_connect.js)
   leaves the page and boots again on the way back. */

import { openModal, openConfirm } from './modal.js';
import { icons } from './icons.js';
import { esc } from './util.js';
import {
  loadSettings, saveSettings, clearSettings, hasRequiredSettings, isDemo, SETTINGS_FIELDS,
  CHAT_CONSENT_VERSION, hasChatConsent,
} from '../data/settings.js';
import { startConnect } from '../data/openrouter_connect.js';

/** Where the source lives: the AGPL's offer of it, linked from the front door,
    Settings and the mobile Planning Inputs drawer. */
export const SOURCE_URL = 'https://github.com/endury/endury';

/** Sliders glyph for the header entry, drawn like icons.js's set. */
export const settingsIcon = (s = 17, w = 2) =>
  `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg>`;

const PRIVACY = 'Your keys stay in this browser. Endury sends each one only to its own '
  + 'service — intervals.icu or OpenRouter.';

/* The explicit consent chat needs: what goes where, health data named, how
   to stop. The hosted site's privacy page says the same at length. */
const CHAT_CONSENT = 'I agree that my messages, the recent conversation and the coach’s working '
  + 'context — my profile, memory, plan, recent training, what the coach looks up, and health data '
  + 'such as sleep, resting heart rate, HRV, weight and any injury or illness I mention — go to '
  + 'OpenRouter (USA) and on to the company that runs the AI model: for Endury’s default, Google or '
  + 'Amazon, in the US, the EU or elsewhere. Endury asks for services listed as keeping no data, which '
  + 'rests on OpenRouter’s lists. The USA may protect my data less, including from access by '
  + 'authorities. A copy of the chat and the coach’s memory stays in unencrypted notes on my '
  + 'intervals.icu account. Chat is for adults (18+). Clearing the key, or Forget my keys, stops it.';
const CONSENT_LAPSED = 'Chat is off until you tick this box: what it says has changed since you agreed.';
const CONSENT_NEEDED = 'Tick the chat box to use an OpenRouter key, or leave the key empty.';

/** What an athlete accepts on giving Endury a key; the demo asks for nothing. */
export const TERMS_BOX = `<div class="cb-door__box">
          <div class="cb-door__box-title">Before you start</div>
          Endury is training software for adults, not medical advice, and it comes as is. See a
          doctor before you start or return to hard training, and stop when something hurts. The
          coach chat is an AI model that can be wrong. By connecting you accept the
          <a href="impressum.html#terms" target="_blank" rel="noopener">terms of use</a>. The
          <a href="privacy.html" target="_blank" rel="noopener">privacy policy</a> explains your data.
        </div>`;

/** The privacy page, the legal notice and the source, as one inline row. */
export const legalLinksHtml = () => `<a href="privacy.html" target="_blank" rel="noopener">Privacy</a>
    <span aria-hidden="true">·</span>
    <a href="impressum.html" target="_blank" rel="noopener">Legal notice and terms</a>
    <span aria-hidden="true">·</span>
    <a href="${SOURCE_URL}" target="_blank" rel="noopener">Source (AGPL)</a>`;

/** One labelled input; `hint` is trusted HTML. */
export function field(name, label, value, {
  type = 'text', placeholder = '', hint = '', autocomplete = 'off',
} = {}) {
  return `<label class="cb-settings__field">${label}
      <input class="cb-settings__input" name="${name}" type="${type}" value="${esc(value)}"
        placeholder="${esc(placeholder)}" autocomplete="${autocomplete}" autocapitalize="off" spellcheck="false">
      ${hint ? `<span class="cb-settings__hint">${hint}</span>` : ''}
    </label>`;
}

/** The Advanced section: the base-URL overrides, open when one is set. */
export function renderAdvanced(settings) {
  const advancedOpen = settings.intervals_base_url || settings.openrouter_base_url;
  return `<details class="cb-settings__advanced"${advancedOpen ? ' open' : ''}>
      <summary>Advanced</summary>
      <div class="cb-settings__group">
        ${field('intervals_base_url', 'intervals.icu API address', settings.intervals_base_url, {
          placeholder: 'https://intervals.icu/api/v1',
        })}
        ${field('openrouter_base_url', 'OpenRouter API address', settings.openrouter_base_url, {
          placeholder: 'https://openrouter.ai/api/v1',
        })}
      </div>
    </details>`;
}

/** The form's fields (no buttons), filled from `settings`. */
export function renderSettingsForm(settings) {
  return `<form class="cb-settings__group" id="cb-settings-intervals">
      <div class="cb-settings__legend">intervals.icu</div>
      ${field('athlete_name', 'Your name', settings.athlete_name, { autocomplete: 'username' })}
      ${field('intervals_api_key', 'API key (required)', settings.intervals_api_key, {
        type: 'password',
        autocomplete: 'current-password',
        hint: 'intervals.icu → <a href="https://intervals.icu/settings" target="_blank" rel="noopener">Settings</a> → Developer Settings.',
      })}
      ${field('intervals_athlete_id', 'Athlete id', settings.intervals_athlete_id, {
        placeholder: '0 — the key’s own athlete',
      })}
    </form>
    <div class="cb-settings__group">
      <div class="cb-settings__legend">Coach chat (optional)</div>
      ${settings.openrouter_api_key && !hasChatConsent(settings) ? `<div class="cb-settings__hint">${CONSENT_LAPSED}</div>` : ''}
      <label class="cb-settings__consent">
        <input type="checkbox" name="chat_consent"${hasChatConsent(settings) ? ' checked' : ''}>
        <span>${CHAT_CONSENT}</span>
      </label>
      <button type="button" class="cb-confirm__btn cb-settings__connect" data-settings="connect">Connect OpenRouter</button>
      ${field('openrouter_api_key', 'OpenRouter API key', settings.openrouter_api_key, {
        type: 'password',
        hint: 'Connect signs you in at OpenRouter and fills this in, or paste a key. '
          + 'Without one, planning works and chat stays off.',
      })}
      ${field('model', 'AI model', settings.model, { placeholder: 'Endury’s default' })}
    </div>
    ${renderAdvanced(settings)}`;
}

/** The settings the inputs under `root` currently hold — only the fields
    it has an input for (never `demo`), so callers merge it over the stored
    settings. */
export function readSettingsForm(root) {
  return Object.fromEntries(SETTINGS_FIELDS
    .map((k) => [k, root.querySelector(`[name="${k}"]`)])
    .filter(([, input]) => input)
    .map(([k, input]) => [k, input.type === 'checkbox' ? (input.checked ? CHAT_CONSENT_VERSION : '') : input.value.trim()]));
}

/** An OpenRouter key without the chat consent: say so and focus the box.
    Returns whether the settings may be kept. */
function chatConsented(next, root) {
  if (!next.openrouter_api_key || hasChatConsent(next)) return true;
  root.querySelector('.cb-settings__status').textContent = CONSENT_NEEDED;
  root.querySelector('[name="chat_consent"]').focus();
  return false;
}

/** `current` with the form's fields over it; a key of its own ends the demo. */
function mergeForm(current, root) {
  const next = { ...current, ...readSettingsForm(root) };
  return next.intervals_api_key ? { ...next, demo: '' } : next;
}

/** Keep what the form under `root` holds so far, then leave for OpenRouter's
    sign-in; the page boots again when OpenRouter sends the athlete back. */
export function connectOpenRouter(root) {
  const next = mergeForm(loadSettings(), root);
  if (!hasChatConsent(next)) {
    root.querySelector('.cb-settings__status').textContent = CONSENT_NEEDED;
    root.querySelector('[name="chat_consent"]').focus();
    return;
  }
  if (!saveSettings(next)) {
    root.querySelector('.cb-settings__status').textContent = 'This browser is blocking storage, so '
      + 'Connect can’t work here. Paste a key instead.';
    return;
  }
  startConnect();
}

const THEMES = [['dark', 'Dark'], ['light', 'Light'], ['system', 'Auto']];

/** The theme row: applies on tap, outside the form and its Save. */
function renderThemeRow() {
  const chosen = globalThis.enduryTheme?.choice() ?? 'dark';
  return `<div class="cb-settings__theme">
      <span class="cb-settings__legend">Theme</span>
      <div class="cb-settings__seg" role="group" aria-label="Theme">
        ${THEMES.map(([value, label]) => `<button type="button" data-theme-choice="${value}"
          aria-pressed="${value === chosen}"${value === 'system' ? ' title="Follows your device"' : ''}>${label}</button>`).join('')}
      </div>
    </div>`;
}

/** The shared intro line under a settings title. */
export const settingsPrivacyHtml = () => `<div class="cb-settings__hint">${PRIVACY}</div>`;

/** Open the Settings panel (center dialog on web, bottom sheet on mobile).
    Returns the modal handle from openModal. */
export function openSettingsPanel({ variant = 'center' } = {}) {
  const current = loadSettings();
  const html = `<div class="cb-settings">
      <div class="cb-settings__head">
        <div class="cb-settings__title">Settings</div>
        <button type="button" class="cb-ib cb-settings__close" data-action="close-modal" aria-label="Close">${icons.close(15)}</button>
      </div>
      ${isDemo(current) ? '<div class="cb-settings__hint">You’re in the demo. Add your intervals.icu key here to make it yours.</div>' : ''}
      ${settingsPrivacyHtml()}
      ${isDemo(current) ? TERMS_BOX : ''}
      ${renderSettingsForm(current)}
      <div class="cb-settings__foot">
        <span class="cb-settings__status" role="status"></span>
        <button type="submit" form="cb-settings-intervals" class="cb-confirm__btn cb-confirm__btn--primary" data-settings="save">Save and reload</button>
      </div>
      ${renderThemeRow()}
      <div class="cb-settings__links">
        ${legalLinksHtml()}
        ${isDemo(current) ? '' : '<button type="button" class="cb-door__link" data-settings="forget">Forget my keys</button>'}
      </div>
    </div>`;

  const modal = openModal(html, {
    variant,
    label: 'Settings',
    width: variant === 'center' ? 460 : undefined,
  });
  const status = modal.el.querySelector('.cb-settings__status');

  modal.el.addEventListener('click', async (e) => {
    if (e.target.closest('[data-settings="connect"]')) connectOpenRouter(modal.el);
    const theme = e.target.closest('[data-theme-choice]');
    if (theme) {
      globalThis.enduryTheme?.set(theme.dataset.themeChoice);
      modal.el.querySelectorAll('[data-theme-choice]').forEach((b) => b.setAttribute('aria-pressed', String(b === theme)));
    }
    if (!e.target.closest('[data-settings="forget"]')) return;
    const sure = await openConfirm({
      title: 'Forget your keys?',
      body: 'This browser forgets your intervals.icu and OpenRouter keys and goes back to the '
        + 'front door. Nothing on your intervals.icu account changes; the privacy page says how '
        + 'to remove Endury’s data there too.',
      confirmLabel: 'Forget keys',
      variant,
    });
    if (!sure) return;
    clearSettings();
    location.reload();
  });
  modal.el.querySelector('#cb-settings-intervals').addEventListener('submit', (e) => {
    e.preventDefault();
    const next = mergeForm(current, modal.el);
    if (!hasRequiredSettings(next)) {
      status.textContent = 'Your intervals.icu API key is required.';
      modal.el.querySelector('[name="intervals_api_key"]').focus();
      return;
    }
    if (!chatConsented(next, modal.el)) return;
    if (!next.openrouter_api_key) next.chat_consent = '';
    if (SETTINGS_FIELDS.every((k) => next[k] === current[k])) {
      modal.close();
      return;
    }
    if (!saveSettings(next)) {
      status.textContent = 'This browser is blocking storage, so the new settings can’t be kept.';
      return;
    }
    status.textContent = 'Saved — reloading…';
    location.reload();
  });

  return modal;
}
