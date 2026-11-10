/* Endury — modal shell: center dialog (web) or bottom sheet (mobile).
   Structural container only; contentHtml is caller-rendered (trusted) HTML.
   No render/import-time DOM access — all DOM work happens inside openModal(). */

const FOCUSABLE_SELECTOR = [
  'a[href]', 'button:not([disabled])', 'textarea:not([disabled])',
  'input:not([disabled])', 'select:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusableIn(root) {
  return Array.from(root.querySelectorAll(FOCUSABLE_SELECTOR))
    .filter((node) => node.offsetParent !== null);
}

/** Open a modal (center dialog or bottom sheet) containing contentHtml.
    Returns {close, el} where el is the dialog element. Traps focus, closes
    on Escape/backdrop click/[data-action="close-modal"], and restores focus
    to the previously-focused element on close. `focusDialog` starts focus on
    the dialog itself, for a sheet that opens to read rather than to answer. */
export function openModal(contentHtml, {
  variant = 'center', label = 'Dialog', onClose, width, focusDialog = false,
} = {}) {
  const previouslyFocused = document.activeElement;
  let closed = false;

  const overlay = document.createElement('div');
  overlay.className = `cb-modal-overlay cb-modal-overlay--${variant}`;

  const dialog = document.createElement('div');
  dialog.className = `cb-modal cb-modal--${variant}`;
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', label);
  dialog.tabIndex = -1;
  if (width) dialog.style.width = typeof width === 'number' ? `${width}px` : width;

  const handle = variant === 'sheet' ? '<div class="cb-modal__handle" aria-hidden="true"></div>' : '';
  dialog.innerHTML = handle + contentHtml;

  function close() {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKeydown, true);
    overlay.remove();
    if (previouslyFocused && typeof previouslyFocused.focus === 'function') previouslyFocused.focus();
    if (onClose) onClose();
  }

  function onKeydown(e) {
    if (e.key === 'Escape') {
      e.preventDefault();
      // The modal owns this Escape: don't let shell-level Escape handlers
      // (drawers, overlays) also close things behind it.
      e.stopPropagation();
      close();
      return;
    }
    if (e.key === 'Tab') {
      const focusable = focusableIn(dialog);
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  // Close on true backdrop clicks only. Clicks inside the dialog must keep
  // bubbling to the shell's document-level [data-action] delegation — do not
  // stopPropagation here or every button inside a modal goes dead.
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) close();
    else if (e.target.closest('[data-action="close-modal"]')) close();
  });
  document.addEventListener('keydown', onKeydown, true);

  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  const focusable = focusableIn(dialog);
  (focusDialog ? dialog : focusable[0] || dialog).focus();

  return { close, el: dialog };
}

/** Confirmation dialog on top of openModal. title/body are caller-rendered
    (trusted) HTML, like contentHtml. Resolves true on confirm, false on
    cancel/backdrop/Escape. */
export function openConfirm({
  title, body, confirmLabel = 'Confirm', cancelLabel = 'Cancel', variant = 'center',
} = {}) {
  return new Promise((resolve) => {
    let result = false;
    const html = `<div class="cb-confirm">
      <div class="cb-confirm__title">${title}</div>
      <div class="cb-confirm__body">${body}</div>
      <div class="cb-confirm__actions">
        <button type="button" class="cb-confirm__btn" data-confirm="no">${cancelLabel}</button>
        <button type="button" class="cb-confirm__btn cb-confirm__btn--primary" data-confirm="yes">${confirmLabel}</button>
      </div>
    </div>`;
    const modal = openModal(html, { variant, label: title, width: 380, onClose: () => resolve(result) });
    modal.el.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-confirm]');
      if (!btn) return;
      result = btn.dataset.confirm === 'yes';
      modal.close();
    });
  });
}
