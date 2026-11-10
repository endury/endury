/* Endury — the athlete-memory document: what the coach durably remembers.
   The same markdown the coach rewrites through update_memory, editable by
   hand. Self-contained like openConfirm: it wires its own Save button
   (data-memory, not data-action) so no shell delegation is involved. It also
   offers a coach-proposed tidy-up, previewed into the editor only — Save is
   still what commits it. */

import { api } from '../data/api.js';
import { openModal } from './modal.js';
import { icons } from './icons.js';

const HINT = 'Goals, constraints, preferences — this is what your coach carries between '
  + 'chats. It keeps it current; correct it here whenever it gets something wrong.';

/** Non-empty trimmed lines of `a` that `b` lacks, counted as a multiset. */
function surplusLines(a, b) {
  const counts = (s) => s.split('\n').map((l) => l.trim()).filter(Boolean)
    .reduce((m, l) => m.set(l, (m.get(l) || 0) + 1), new Map());
  const other = counts(b);
  return [...counts(a)].reduce((n, [line, k]) => n + Math.max(0, k - (other.get(line) || 0)), 0);
}

/** Open the memory panel (center dialog on web, bottom sheet on mobile).
    Returns the modal handle from openModal. */
export function openMemoryPanel({ variant = 'center' } = {}) {
  const html = `<div class="cb-memory">
      <div class="cb-memory__head">
        <div class="cb-memory__title">Memory</div>
        <button type="button" class="cb-ib cb-memory__close" data-action="close-modal" aria-label="Close">${icons.close(15)}</button>
      </div>
      <div class="cb-memory__hint">${HINT}</div>
      <textarea class="cb-memory__text" aria-label="Athlete memory" placeholder="Loading…" disabled></textarea>
      <div class="cb-memory__foot">
        <button type="button" class="cb-confirm__btn" data-memory="restore" hidden>Restore previous</button>
        <button type="button" class="cb-confirm__btn" data-memory="tidy" disabled>Tidy up</button>
        <span class="cb-memory__status"></span>
        <button type="button" class="cb-confirm__btn cb-confirm__btn--primary" data-memory="save" disabled>Save</button>
      </div>
    </div>`;

  const modal = openModal(html, {
    variant,
    label: 'Memory',
    width: variant === 'center' ? 520 : undefined,
  });
  const text = modal.el.querySelector('.cb-memory__text');
  const status = modal.el.querySelector('.cb-memory__status');
  const save = modal.el.querySelector('[data-memory="save"]');
  const restore = modal.el.querySelector('[data-memory="restore"]');
  const tidy = modal.el.querySelector('[data-memory="tidy"]');

  /* Every save snapshots the version it replaces; the newest snapshot is the
     way back from a rewrite (the coach's or your own) that lost something.
     It only fills the editor — nothing is stored until Save. */
  let previous = null;
  /* What the editor last loaded or saved. Tidy up works on the saved document
     (the endpoint takes no body), so it refuses while the editor is dirty
     rather than overwrite unsaved edits or a restored version. */
  let loaded = '';
  api.getMemoryHistory().then((payload) => {
    previous = (payload?.versions || [])[0] || null;
    restore.hidden = !previous;
  }).catch(() => {});

  api.getMemory().then((payload) => {
    text.value = payload?.content || '';
    loaded = text.value;
    text.placeholder = 'Nothing remembered yet.';
    text.disabled = false;
    save.disabled = false;
    tidy.disabled = !payload?.content?.trim();
    status.textContent = payload?.updated_at ? `Updated ${payload.updated_at.slice(0, 10)}` : '';
  }).catch((err) => {
    status.textContent = `Could not load your memory: ${err.message}`;
  });

  modal.el.addEventListener('click', async (e) => {
    if (e.target.closest('[data-memory="restore"]')) {
      if (!previous) return;
      text.value = previous.content;
      status.textContent = `Loaded the version from ${previous.created_at.slice(0, 10)} — Save to keep it.`;
      return;
    }
    if (e.target.closest('[data-memory="tidy"]')) {
      if (tidy.disabled) return;
      if (text.value !== loaded) {
        status.textContent = 'Save your edits first — Tidy up works on the saved document.';
        return;
      }
      tidy.disabled = true;
      save.disabled = true;
      status.textContent = 'Tidying…';
      const before = text.value;
      try {
        const payload = await api.cleanupMemory();
        text.value = payload.content;
        loaded = text.value;
        /* Line counts as multisets, so a merged duplicate counts as dropped. */
        const dropped = surplusLines(before, payload.content);
        const added = surplusLines(payload.content, before);
        status.textContent = `Tidied — ${dropped} dropped, ${added} added. Save to keep it.`;
      } catch (err) {
        status.textContent = err.message;
      } finally {
        tidy.disabled = false;
        save.disabled = false;
      }
      return;
    }
    if (e.target.closest('[data-memory="save"]')) {
      save.disabled = true;
      status.textContent = 'Saving…';
      try {
        const saved = await api.putMemory(text.value);
        loaded = text.value;
        status.textContent = saved?.updated_at ? `Saved · ${saved.updated_at.slice(0, 10)}` : 'Saved';
      } catch (err) {
        status.textContent = err.message;
      }
      save.disabled = false;
    }
  });

  return modal;
}
