/* Endury — the metrics explainer: what the numbers the athlete meets bare
   actually mean. Every "?" in the app (training load, the fitness trio,
   training zones, intensity factor) opens this same sheet on a different
   topic instead of duplicating copy. */

import { openModal } from './modal.js';
import { icons } from './icons.js';

/* One entry per topic: modal heading, aria label, and the prose (one
   paragraph per line). Every acronym is spelled out where it first lands —
   the athlete should never have to look a second one up. */
const TOPICS = {
  tss: {
    title: 'Training load',
    label: 'Training load',
    lines: [
      'Load measures how much strain a workout puts on you, from how long and how hard it was.',
      'For a feel: an easy hour scores around 50, a hard hour around 100, and a long weekend session can reach 200 or more.',
      "Your weekly total is your training load. The ring compares what you've completed so far with what the plan scheduled for this week.",
    ],
  },
  fitness: {
    title: 'Fitness, fatigue and form',
    label: 'Fitness and fatigue',
    lines: [
      "Fitness is your base: the training you've absorbed over the past weeks. Fatigue is what the past few days have left in your legs.",
      'Form is fitness minus fatigue. Positive means fresh, negative means you are carrying fatigue; race week aims for positive.',
      'We show these per sport and on a weekly scale, so the numbers run about seven times larger than the daily, all-sports figures on intervals.icu — and for a multi-sport athlete the two never line up exactly, since ours split what theirs adds together.',
    ],
  },
  zones: {
    title: 'Training zones',
    label: 'Training zones',
    lines: [
      'Zones split effort into five bands: Z1 recovery, Z2 steady endurance, Z3 tempo, Z4 threshold — about the hardest you could hold for an hour — and Z5 the short, hard work above it.',
      'Heart-rate, pace and power zones are numbered separately: the same effort can sit in different zones depending on which one a step targets, and heart rate always lags the other two.',
      'The thresholds behind every zone come from your intervals.icu sport settings. Change them in Planning Inputs or on intervals.icu and the targets here follow.',
    ],
  },
};

/** The "?" affordance that opens this explainer on a given topic.

    Args:
      topic: a key of TOPICS ('tss', 'fitness', 'zones').
      label: the aria-label — a plain-language question, since "?" alone
        reads as nothing to a screen reader.

    Returns:
      Button markup (the shells' click delegation does the rest).
*/
export function hintButton(topic, label) {
  return `<button type="button" class="cb-hint-btn" data-action="explain-tss" data-topic="${topic}" aria-label="${label}">?</button>`;
}

/** Open the metrics explainer modal (center dialog on web, bottom sheet on
    mobile) on one topic.

    Args:
      variant: 'center' (web) or 'sheet' (mobile) — passed straight through
        to openModal.
      topic: which metric to explain; unknown topics fall back to 'tss'.

    Returns:
      The openModal handle ({close, el}).
*/
export function openTssExplainer({ variant = 'center', topic = 'tss' } = {}) {
  const t = TOPICS[topic] || TOPICS.tss;
  const html = `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
      <div style="font-size:var(--cb-fs-title);font-weight:600">${t.title}</div>
      <button type="button" class="cb-ib" data-action="close-modal" aria-label="Close"
        style="width:30px;height:30px;border-radius:8px;border:1px solid var(--cb-border);background:var(--cb-surface-3);color:var(--cb-text-muted);display:flex;align-items:center;justify-content:center;cursor:pointer">${icons.close(15)}</button>
    </div>
    <div style="font-size:var(--cb-fs-body);line-height:1.55;color:var(--cb-text);display:flex;flex-direction:column;gap:10px">
      ${t.lines.map((line) => `<p style="margin:0">${line}</p>`).join('')}
    </div>`;
  return openModal(html, { variant, label: t.label, width: variant === 'center' ? 420 : undefined });
}
