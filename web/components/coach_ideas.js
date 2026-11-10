/* Endury — "What you can ask": example questions to read through, grouped
   by what the coach can do, then the athlete's own recent ones. A tap puts
   the question in the draft (shells, `use-idea`), where the athlete can
   change it before it goes. */

import { openModal } from './modal.js';
import { icons } from './icons.js';
import { esc } from './util.js';

/* The questions the coach answers well, one group per thing it can do. */
const IDEAS = [
  {
    title: 'Charts',
    questions: [
      'Show my weekly hours by sport for the last three months',
      'How is my fitness trending? Show me',
      'Plot my HRV and resting heart rate for the last month',
      'Plot my pace and heart rate per km in my last race',
    ],
  },
  {
    title: 'Compare',
    questions: [
      'Compare my last two rides on the same loop: where did I gain or lose time?',
      'Have my easy runs become more efficient since the summer?',
      'How did my times on the main climb of my usual loop develop?',
    ],
  },
  {
    title: 'Your sessions',
    questions: [
      'How did my last run go?',
      'How did I pace the climbs in my last race?',
      'Was my long run at the weekend too hard?',
    ],
  },
  {
    title: 'Your plan',
    questions: [
      "What's coming up this week?",
      "Move tomorrow's session to Friday",
      "I'm ill. Pause my training for three days",
    ],
  },
  {
    title: 'Recovery and races',
    questions: [
      'My legs feel heavy. Should I train today?',
      'What marathon time am I in shape for?',
      'Brief me for my next race',
    ],
  },
];

/** The most recent questions shown, newest first. */
const RECENT = 4;

/** The athlete's last distinct questions in this chat, newest first. */
function recentQuestions(messages) {
  const asked = messages.filter((m) => m.role === 'user' && m.text?.trim())
    .map((m) => m.text.trim()).reverse();
  return [...new Set(asked)].slice(0, RECENT);
}

const group = (title, questions) => `
    <div class="cb-ideas__group">
      <div class="cb-reschedule-label">${esc(title)}</div>
      ${questions.map((q) => `<button type="button" class="cb-idea" data-action="use-idea"
        data-text="${esc(q)}">${esc(q)}</button>`).join('')}
    </div>`;

/** Open the ideas sheet; `messages` is the chat so far (coach.state). */
export function openCoachIdeas({ variant = 'center', messages = [] } = {}) {
  const recent = recentQuestions(messages);
  const html = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
      <div style="font-size:var(--cb-fs-title);font-weight:600">What you can ask</div>
      <button type="button" class="cb-ib" data-action="close-modal" aria-label="Close"
        style="width:30px;height:30px;border-radius:8px;border:1px solid var(--cb-border);background:var(--cb-surface-3);color:var(--cb-text-muted);display:flex;align-items:center;justify-content:center;cursor:pointer">${icons.close(15)}</button>
    </div>
    <div class="cb-detail-note" style="margin:0 0 12px">Tap one to put it in your message, and change it as you like before you send.</div>
    ${IDEAS.map((g) => group(g.title, g.questions)).join('')}
    ${recent.length ? group('You asked recently', recent) : ''}`;
  return openModal(html, { variant, label: 'What you can ask', width: variant === 'center' ? 460 : undefined });
}
