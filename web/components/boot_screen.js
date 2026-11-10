/* Endury — boot screen: covers the page while the Python worker starts
   (Pyodide, its packages, the Endury bundle, then the store read from
   intervals.icu). It also hosts the front door (components/front_door.js,
   rendered into `body`) and the choices a failed boot offers. Its buttons
   carry data-boot, not data-action: no shell is mounted yet. */

import { esc } from './util.js';
import { openConfirm } from './modal.js';
import { SOURCE_URL } from './settings_panel.js';

const FIRST_VISIT_NOTE = 'First visit: Endury downloads its Python engine once (about 34 MB). '
  + 'Later visits start from this browser’s cache.';

const START_FRESH_CONFIRM = {
  title: 'Start fresh?',
  body: 'This deletes Endury’s saved data note on intervals.icu: your plan, races, memory '
    + 'and self-assessment. Workouts already on your intervals.icu calendar stay there.',
  confirmLabel: 'Delete and start fresh',
};

/** Mount the boot screen. Returns {body, status, fail, close}. */
export function createBootScreen({ firstVisit = false } = {}) {
  const el = document.createElement('div');
  el.className = 'cb-boot';
  el.innerHTML = `<div class="cb-boot__card">
      <div class="cb-boot__brand"><div class="cb-boot__logo" aria-hidden="true">E</div>Endury
        <a class="cb-boot__how" href="${SOURCE_URL}#readme" target="_blank" rel="noopener">How it works ↗</a></div>
      <div class="cb-boot__body"></div>
    </div>`;
  document.body.appendChild(el);
  const body = el.querySelector('.cb-boot__body');

  let onButton = null;
  el.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-boot]');
    if (btn && onButton) onButton(btn.dataset.boot);
  });

  /** Show a progress line (the spinner view). */
  function status(text) {
    let line = body.querySelector('.cb-boot__status-text');
    if (!line) {
      onButton = null;
      body.innerHTML = `<div class="cb-boot__status">
          <span class="cb-boot__spinner" aria-hidden="true"></span>
          <span class="cb-boot__status-text" role="status"></span>
        </div>
        ${firstVisit ? `<div class="cb-boot__note">${FIRST_VISIT_NOTE}</div>` : ''}`;
      line = body.querySelector('.cb-boot__status-text');
    }
    line.textContent = text;
  }

  /** Show a boot failure; resolves with the athlete's choice: 'retry',
      'settings', or — offered only for an unreadable store note — 'fresh',
      after its own confirmation. `details`, the boot report, sits folded
      under the message with a Copy button, for the athlete to send on. */
  function fail(message, { corrupt = false, details = '' } = {}) {
    body.innerHTML = `<div class="cb-boot__error" role="alert">${esc(message)}</div>
      ${details ? `<details class="cb-boot__details"><summary>Details for support</summary>
        <pre class="cb-boot__report">${esc(details)}</pre>
        <button type="button" class="cb-confirm__btn" data-boot="copy">Copy details</button></details>` : ''}
      <div class="cb-boot__actions">
        <button type="button" class="cb-confirm__btn" data-boot="settings">Settings</button>
        ${corrupt ? '<button type="button" class="cb-confirm__btn" data-boot="fresh">Start fresh</button>' : ''}
        <button type="button" class="cb-confirm__btn cb-confirm__btn--primary" data-boot="retry">Retry</button>
      </div>`;
    return new Promise((resolve) => {
      onButton = async (action) => {
        if (action === 'copy') {
          const btn = body.querySelector('[data-boot="copy"]');
          // No clipboard off a secure origin: the report stays selectable.
          btn.textContent = await (navigator.clipboard?.writeText(details) ?? Promise.reject())
            .then(() => 'Copied', () => 'Select and copy the text above');
          return;
        }
        if (action === 'fresh' && !(await openConfirm(START_FRESH_CONFIRM))) return;
        onButton = null;
        resolve(action);
      };
    });
  }

  return { body, status, fail, close: () => el.remove() };
}
