/* Endury — coach chat message bubble + typing indicator. */

import { renderChart } from './chat_chart.js';
import { renderProposalCard, renderProposalOutcome } from './proposal_card.js';
import { esc } from './util.js';

/** Render a run of already-escaped "| a | b |" lines as a table. The
    "|---|---|" separator row is dropped; the first row becomes the header. */
function tableHtml(lines) {
  const rows = lines
    .filter((ln) => !/^\|\s*:?-{2,}/.test(ln))
    .map((ln) => ln.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim()));
  if (!rows.length) return '';
  const cell = (tag, cells) => `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join('')}</tr>`;
  const [head, ...body] = rows;
  return `<div class="cb-msg__table-wrap"><table class="cb-msg__table">`
    + `<thead>${cell('th', head)}</thead>`
    + `<tbody>${body.map((r) => cell('td', r)).join('')}</tbody>`
    + `</table></div>`;
}

/** Markdown-lite: coach replies use **bold**, "- " bullets, and pipe tables
    (ad-hoc workouts, session splits); escape first, then convert. Never raw
    HTML. Also renders the takes saved on an activity sheet. */
export function formatBody(text) {
  const escaped = esc(text).replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  const out = [];
  let table = [];
  const flush = () => { if (table.length) { out.push(tableHtml(table)); table = []; } };
  for (const line of escaped.split('\n')) {
    if (/^\s*\|.*\|\s*$/.test(line)) { table.push(line); continue; }
    flush();
    out.push(line.replace(/^- (.*)$/, '<span class="cb-msg__bullet">• $1</span>'));
  }
  flush();
  return out.join('\n')
    .replace(/\n*(<div class="cb-msg__table-wrap">)/g, '$1')
    .replace(/(<\/div>)\n*/g, '$1')
    .replaceAll('\n\n', '<br><br>')
    .replaceAll('\n', '<br>');
}

/** Render one chat message. role: 'coach' | 'user' | 'note'. Text is
    escaped, then double newlines become paragraph breaks — replies are
    plain/markdown-lite, never raw HTML. A coach reply's charts sit under
    its text, its Apply cards under those, its chips last; a note is a
    restored "[Applied: …]" / "[Dismissed: …]" line (kind: 'applied' |
    'dismissed'). */
export function renderMessage({ role, text, kind, chips = [], proposals = [], charts = [] }) {
  if (role === 'note') return renderProposalOutcome(kind, text);
  const coach = role === 'coach';
  const meta = coach ? 'Endury' : 'You';
  const body = formatBody(text);
  const chipsHtml = chips.length
    ? `<div class="cb-msg__chips">${chips.map((c) => (
      `<button type="button" class="cb-chip" data-action="chip" data-text="${esc(c)}">${esc(c)}</button>`
    )).join('')}</div>`
    : '';
  return `<div class="cb-msg cb-msg--${coach ? 'coach" data-ai-generated="true' : 'user'}">`
    + `<div class="cb-msg__meta">${esc(meta)}</div>`
    + `<div class="cb-msg__text">${body}</div>`
    + charts.map(renderChart).join('')
    + proposals.map(renderProposalCard).join('')
    + chipsHtml
    + `</div>`;
}

/** Render the "thinking" indicator: three staggered pulsing dots + a label. */
export function renderThinking(text) {
  return `<div class="cb-thinking">`
    + `<span class="cb-dot"></span>`
    + `<span class="cb-dot" style="animation-delay:.2s"></span>`
    + `<span class="cb-dot" style="animation-delay:.4s"></span>`
    + `<span class="cb-thinking__label">${esc(text)}</span>`
    + `</div>`;
}
