/* Endury — runtime banner: the one line above the app that says when this
   tab's data is not safely on intervals.icu, most serious state first —
   a conflict (another device saved first: reload), read-only (another tab
   holds the writer lock, or the saved data is newer than this app), or an
   unsaved change (a failed save, retried on the next call) — or, with none
   of those, the demo, whose "Make it yours" opens the connect flow over
   the running app. Its buttons carry data-banner, not data-action, so no
   shell delegation is involved. */

import { onRuntimeChange, flushStore } from '../data/api.js';
import { loadSettings } from '../data/settings.js';
import { esc } from './util.js';
import { createBootScreen } from './boot_screen.js';
import { runConnect } from './front_door.js';

const DEMO = {
  tone: 'info',
  text: 'Demo: a sample athlete’s plan. Nothing you change is saved.',
  action: 'connect',
  label: 'Make it yours',
};

/* The connect flow over the running app: a key reloads the page, which
   then boots for real; Back from its first screen returns to the demo. */
async function makeItYours() {
  const screen = createBootScreen();
  const next = await runConnect(screen.body, loadSettings(), { startAt: 'log' });
  if (next) location.reload();
  else screen.close();
}

function bannerFor(s, demo) {
  if (s.conflict) {
    return {
      tone: 'alert',
      text: 'Your Endury data changed on another device. Reload to pick up the latest before changing anything.',
      action: 'reload',
      label: 'Reload',
    };
  }
  if (s.readOnly) return { tone: 'info', text: s.readOnlyReason, action: 'reload', label: 'Reload' };
  if (s.unsaved) {
    return {
      tone: 'warn',
      text: 'Some changes aren’t saved to intervals.icu yet. Endury retries with your next action.',
      action: 'save',
      label: 'Save now',
    };
  }
  return demo ? DEMO : null;
}

/** Render the banner into `el` and keep it current; `demo` shows the demo
    line when nothing more serious does. */
export function mountRuntimeBanner(el, { demo = false } = {}) {
  el.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-banner]');
    if (!btn) return;
    if (btn.dataset.banner === 'reload') {
      location.reload();
      return;
    }
    if (btn.dataset.banner === 'connect') {
      makeItYours();
      return;
    }
    btn.disabled = true;
    await flushStore();
    btn.disabled = false;
  });
  onRuntimeChange((s) => {
    const view = bannerFor(s, demo);
    el.hidden = !view;
    el.className = view ? `cb-banner cb-banner--${view.tone}` : 'cb-banner';
    el.innerHTML = view
      ? `<span class="cb-banner__text">${esc(view.text)}</span>
        <button type="button" class="cb-banner__btn" data-banner="${view.action}">${view.label}</button>`
      : '';
  });
}
