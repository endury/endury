/* Endury — imperative toast: one shared element, auto-dismiss, retimed
   on repeat calls. No render/import-time DOM access (lazily creates on first call). */

let el = null;
let timer = null;

const AUTO_DISMISS_MS = 2200;
const MAX_DISMISS_MS = 12000;
const MS_PER_CHAR = 45;

/** How long `text` stays: 2200ms for a short notice, longer for a plan
    refusal of a few sentences (about 45ms a character, capped at 12s). */
function dismissAfter(text) {
  return Math.min(MAX_DISMISS_MS, Math.max(AUTO_DISMISS_MS, 1500 + MS_PER_CHAR * text.length));
}

/** Show (or update) the toast with `text`; auto-removes after a time that
    grows with the text, and re-calling before that resets the timer instead
    of stacking toasts. */
export function showToast(text) {
  if (!el) {
    el = document.createElement('div');
    el.className = 'cb-toast';
    el.setAttribute('role', 'status');   // polite live region for screen readers
    document.body.appendChild(el);
  }
  el.textContent = text;
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    if (el) { el.remove(); el = null; }
    timer = null;
  }, dismissAfter(text));
}
