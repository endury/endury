/* Endury — the pages of a session or race sheet. The overview answers each
   question in one line, and a row with more to say opens a page. Every page
   sits in the sheet from the start and the navigator only swaps which one
   shows, so a slot the shell fills later works wherever it lives. */

import { esc } from './util.js';
import { icons } from './icons.js';

/** The sheet's close button; the modal variant sizes it (surfaces.css). */
export const sheetClose = () => `<button type="button" class="cb-ib cb-sheet-ib" data-action="close-modal"
  aria-label="Close">${icons.close(18)}</button>`;

/** The overview's pinned head: the sport or race marker, the title, one line
    of facts, and any badge or extra button. `meta` is trusted HTML. */
export function sheetHead({ color, title, meta = '', badge = '', extra = '' }) {
  return `
    <header class="cb-sheet__head">
      <span class="cb-detail-dot" style="background:${color}"></span>
      <div class="cb-sheet__heading">
        <div class="cb-detail-title">${esc(title)}</div>
        ${meta ? `<div class="cb-sheet__meta">${meta}</div>` : ''}
      </div>
      ${badge}${extra}${sheetClose()}
    </header>`;
}

/** One overview row: a title and its one-line answer, which opens `page`, or
    stands whole when there is no more to say. `key` names the answer for
    setSummary; `lines: 2` lets a coach's reply run to a second line; `body`
    is trusted HTML under the answer, such as a small bar. */
export function sheetRow({ title, summary = '', page = null, key = null, lines = 1, body = '' }) {
  const fit = !page ? ' cb-sheet-row__answer--whole' : lines === 2 ? ' cb-sheet-row__answer--two' : '';
  const answer = summary || key
    ? `<span class="cb-sheet-row__answer${fit}"${key ? ` data-summary="${esc(key)}"` : ''}>${esc(summary)}</span>`
    : '';
  const text = `<span class="cb-sheet-row__text"><span class="cb-sheet-row__title">${esc(title)}</span>${answer}${body}</span>`;
  if (!page) return `<div class="cb-sheet-row cb-sheet-row--plain">${text}</div>`;
  return `<button type="button" class="cb-sheet-row" data-action="sheet-open" data-page="${esc(page)}">${text}${icons.chevronRight(16)}</button>`;
}

/** A page behind a row, hidden until the row opens it. `back` names the way
    back (the sheet's title); `body` is trusted HTML. */
export function sheetPage({ id, title, sub = '', back = 'Back', body }) {
  return `
    <section class="cb-sheet__page" data-sheet-page="${esc(id)}" hidden>
      <header class="cb-sheet__head">
        <button type="button" class="cb-sheet-back" data-action="sheet-back">${icons.chevronLeft(18, 2)}<span>${esc(back)}</span></button>
        ${sheetClose()}
      </header>
      <div class="cb-sheet__body cb-sheet__body--page">
        <div>
          <h2 class="cb-sheet__title">${esc(title)}</h2>
          ${sub ? `<div class="cb-sheet__sub">${esc(sub)}</div>` : ''}
        </div>
        ${body}
      </div>
    </section>`;
}

/** The sheet's own scroller, the modal around it. */
const scrollerOf = (sheet) => sheet.parentElement;

/** Open the page a row names, and put focus on the way back. */
export function openSheetPage(btn) {
  const sheet = btn.closest('.cb-sheet');
  const page = sheet.querySelector(`[data-sheet-page="${btn.dataset.page}"]`);
  if (!page) return;
  sheet.dataset.overviewScroll = scrollerOf(sheet).scrollTop;
  sheet.dataset.openedFrom = btn.dataset.page;
  // A short page keeps the sheet's height, so the way back stays under the thumb.
  sheet.style.minHeight = `${Math.min(sheet.offsetHeight, scrollerOf(sheet).clientHeight)}px`;
  sheet.querySelector('[data-sheet-page="overview"]').hidden = true;
  page.hidden = false;
  scrollerOf(sheet).scrollTop = 0;
  page.querySelector('.cb-sheet-back').focus({ preventScroll: true });
}

/** Back to the overview, where the athlete was, focus on the row they came from. */
export function closeSheetPage(btn) {
  const sheet = btn.closest('.cb-sheet');
  btn.closest('[data-sheet-page]').hidden = true;
  const overview = sheet.querySelector('[data-sheet-page="overview"]');
  overview.hidden = false;
  scrollerOf(sheet).scrollTop = Number(sheet.dataset.overviewScroll) || 0;
  overview.querySelector(`[data-page="${sheet.dataset.openedFrom}"]`)?.focus({ preventScroll: true });
}

/** The title the sheet's pages name as the way back. */
export const sheetBackLabel = (root) => root.querySelector('.cb-detail-title')?.textContent || 'Back';

/** Add pages the worker's answer brought to the sheet in `root`. */
export function addSheetPages(root, pages) {
  root.querySelector('[data-sheet-pages]')?.insertAdjacentHTML('beforeend', pages);
}

/** Bring a row's answer up to date after its page changed. */
export function setSummary(root, key, text) {
  const answer = root?.querySelector(`[data-summary="${key}"]`);
  if (answer) answer.textContent = text;
}

/** A coach reply's opening as a row's answer: no markdown, tables or charts. */
export function previewText(text) {
  return text.split('\n').filter((line) => !/^\s*(\||%%)/.test(line))
    .map((line) => line.replace(/^- /, '')).join(' ')
    .replace(/\*\*/g, '').replace(/\s+/g, ' ').trim().slice(0, 200);
}
