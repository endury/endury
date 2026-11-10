/* Endury — Apply card: a change the coach proposed, which only the athlete
   makes happen. The coach never writes the plan on its own; a write it
   wants shows up under its reply as one of these, with Apply and Dismiss.
   Clicks carry data-action="apply-proposal" | "dismiss-proposal" and the
   proposal's id; coach.js handles them. */

import { esc } from './util.js';

/* The card's heading per proposing tool; anything else is a "Suggested
   change". */
const TITLES = {
  create_plan: 'New training plan',
  set_plan_directives: 'Update your plan',
  replan_active_plan: 'Replan from next Monday',
  adjust_current_week: 'Adjust this week',
  pause_training: 'Pause training',
  swap_planned_session: 'Swap a session',
  skip_planned_session: 'Skip a session',
  schedule_workout_override: 'Add a workout',
  unschedule_workout: 'Remove a workout',
  add_race_to_plan: 'Add a race',
};

/** A settled card, or a restored "[Applied: …]" / "[Dismissed: …]" note:
    one quiet line. kind: 'applied' | 'dismissed'. */
export function renderProposalOutcome(kind, text) {
  const mark = kind === 'applied' ? '✓ Applied' : 'Dismissed';
  return `<div class="cb-proposal cb-proposal--${kind === 'applied' ? 'applied' : 'dismissed'}">`
    + `<span class="cb-proposal__mark">${mark}</span>`
    + `<span class="cb-proposal__outcome">${esc(text)}</span>`
    + `</div>`;
}

/** Render one card. status: 'open' | 'applying' | 'dismissing' | 'applied'
    | 'dismissed'; note is a line under the summary (why a card was
    replaced, or why applying it did not go through). */
export function renderProposalCard({ proposal, status, note = '' }) {
  const summary = proposal.preview_summary || '';
  if (status === 'applied') return renderProposalOutcome('applied', note || summary);
  if (status === 'dismissed') return renderProposalOutcome('dismissed', summary);
  const busy = status !== 'open';
  const id = esc(proposal.id);
  return `<div class="cb-proposal">`
    + `<div class="cb-proposal__title">${esc(TITLES[proposal.tool] || 'Suggested change')}</div>`
    + `<div class="cb-proposal__summary">${esc(summary)}</div>`
    + (note ? `<div class="cb-proposal__note">${esc(note)}</div>` : '')
    + `<div class="cb-proposal__actions">`
    + `<button type="button" class="cb-proposal__btn cb-proposal__btn--apply" data-action="apply-proposal" data-id="${id}"${busy ? ' disabled' : ''}>`
    + `${status === 'applying' ? 'Applying…' : 'Apply'}</button>`
    + `<button type="button" class="cb-proposal__btn" data-action="dismiss-proposal" data-id="${id}"${busy ? ' disabled' : ''}>Dismiss</button>`
    + `</div>`
    + `</div>`;
}
