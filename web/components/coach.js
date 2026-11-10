/* Endury — coach controller: message state over one chat call per turn
   (a typing indicator stands in until the whole reply lands). Quick-reply
   chips come from the coach's own %%REPLIES%%a|b|c marker (the established
   backend contract); the coach decides when to offer them, so an absent
   marker means no chips. A write the coach proposes arrives as an Apply
   card under its reply and happens only when the athlete taps Apply. The
   conversation itself is kept by the coach session and saved to
   intervals.icu, not on this device. */

import { api, chatStream } from '../data/api.js';
import { fmtDayLabel } from '../data/format.js';
import { bindChartReadout } from './chat_chart.js';

/* A stored reply keeps its charts as %%CHART%%{spec} lines after the text
   (endury/coach/tools/analysis_tools.py), so a reload draws them again. */
const CHART_LINE = /^%%CHART%%(.*)$/gm;

/** Split the %%REPLIES%% and %%CHART%% markers out of coach text →
    {text, chips, charts}. */
function splitReplies(raw) {
  const charts = [];
  for (const [, json] of raw.matchAll(CHART_LINE)) {
    try { charts.push(JSON.parse(json)); } catch { /* a damaged line draws nothing */ }
  }
  const body = raw.replace(/\n?%%CHART%%[^\n\r]*/g, '');
  const match = body.match(/%%REPLIES%%([^\n\r]+)/);
  const text = body.replace(/\n?%%REPLIES%%[^\n\r]*/g, '').trimEnd();
  const chips = match
    ? match[1].split('|').map((c) => c.trim().replace(/%%$/, '').trim()).filter(Boolean).slice(0, 4)
    : [];
  return { text, chips, charts };
}

/** Contextual "thinking" status line. */
function thinkFor(text) {
  const l = text.toLowerCase();
  if (/fuel|nutrition|eat|carb/.test(l)) return 'Reviewing your fueling…';
  if (/why|volume|much/.test(l)) return 'Reviewing your training history…';
  if (/tired|heavy|sore|recover|feel/.test(l)) return 'Checking how recovered you are…';
  if (/resched|move|swap|replan/.test(l)) return 'Rearranging your week…';
  return 'Thinking…';
}

const DIGEST_PROMPT =
  'Give me a short weekly digest: sessions done vs planned this week, load so far, '
  + 'how my form is trending, and what the rest of the week hinges on. Keep it tight.';

const DIGEST_PROMPT_NO_PLAN =
  "Give me a short digest of my training this week: what I've logged, load so far, "
  + 'how my form is trending, and what to watch for the rest of the week. Keep it tight.';

const POST_COMMIT_PROMPT =
  'I just committed the new training plan — give me a two-sentence take on the first week.';

/* An activity sheet's two takes, Quick and Full, asked on the athlete's
   behalf. The activity id rides along so the coach reads that one activity,
   not every activity of its day. The coach's reply is saved with the
   activity, so it ends on a statement: a question reads oddly a week later. */
const activityRef = ({ name, date, id }) =>
  `my ${name} on ${date} (activity ${id}). Pull its detail and, if it was planned, `
  + 'what the plan prescribed. This session only.';

/* A race's takes: judged as the race, against its brief, never as training. */
const raceRef = ({ name, date, id, race }) =>
  `my ${race} on ${date} (activity ${id}, recorded as ${name}). Pull its detail. `
  + 'This was the race itself, not a training session.';

const RACE_TAKE_PROMPTS = {
  quick: (a) => `Quick take on ${raceRef(a)} Two or three sentences: the result and `
    + 'what it means, the one number that shows it, and how long to recover. No table, '
    + 'no splits, no question at the end.',
  full: (a, brief) => `Full debrief of ${raceRef(a)} Also pull my activities of the seven `
    + 'days before it. I am reading it on its sheet, which already shows the headline '
    + 'numbers, the time in zones and every split, so do not list those back to me and '
    + 'build no table. A short paragraph each, with a bold lead-in: **Result**: my finish '
    + 'against what the brief and my goal expected. **Pacing**: how I spread the effort '
    + 'over the course, on a trail by grade-adjusted pace and terrain rather than raw '
    + 'splits, naming the one or two stretches that tell the story, judged against the '
    + 'race plan. **Fuelling and feel**: what my feel and effort say, and what fuelling '
    + 'did. **Race week**: the days before against the taper I was given, and whether they '
    + 'cost anything on the day, judged by the data. **What it says**: where my fitness is '
    + 'and what it means for my next goals. **Recovery**: how many easy days, when the '
    + 'first easy run and when the next hard session fit, as a concrete week. Where the '
    + 'file cannot show something, say so plainly as a statement. No question at the end.'
    + (brief ? briefLine(brief) : ''),
};

const TAKE_PROMPTS = {
  quick: (a) => `Quick take on ${activityRef(a)} Two or three sentences: did it do `
    + 'what it was for, the one number that shows it, and the one thing to keep or '
    + 'change next time. No table, no splits, no question at the end.',
  full: (a, brief) => `Full debrief of ${activityRef(a)} I am reading it on its sheet, which `
    + 'already shows the headline numbers (time, distance, pace or power, heart rate, '
    + 'load, decoupling), the time in zones, the interval groups and every split, so do '
    + 'not list those back to me and build no table: tell me what they mean, quoting a '
    + 'number only where it makes a point. A short paragraph each, with a bold lead-in: '
    + '**Purpose**: what it was for and whether it delivered that. '
    + '**Execution**: where the work hit or missed its targets, naming the one or two '
    + 'reps or splits that tell the story; whether the easy parts stayed easy; how the '
    + 'pace or power held late and what any heart-rate drift says. '
    + '**Context**: what it says about where my fitness is and how it sits in my current '
    + 'load and freshness. **Next time**: up to three changes, each tied to something '
    + 'above; if it went to plan, say what to keep instead of inventing a fix. End on '
    + 'what it means for my next key session, with no question.'
    + (brief ? briefLine(brief) : ''),
};

/* The brief a session was given, so its full debrief grades against it. */
const briefLine = (text) => ` The brief for it said: "${text.length > 600 ? text.slice(0, 600) : text}". `
  + 'Grade me against what it asked me to hold.';

/* A coming session's or race's brief, Quick and Full, asked on the
   athlete's behalf. Saved with the subject, so it ends on a statement. */
const briefRef = ({ handle, name, date }) => {
  const [kind, id] = handle.split(':');
  return `my ${name} on ${fmtDayLabel(date)} (${kind === 'race' ? `race on ${id}` : `session ${id}`}). `
    + 'Pull its brief with get_brief';
};

const BRIEF_PROMPTS = {
  quick: (s) => `Quick brief for ${briefRef(s)} and give me three sentences: the one thing to hold, `
    + 'the fuel line, the one risk. No table, no question.',
  full: (s) => `Full brief for ${briefRef(s)}. I am reading it on its sheet, which already shows `
    + 'the numbers (form, anchors, fuel, kit, routes, finish range), so do not list them back and '
    + 'build no table: tell me what they mean for this one. A short paragraph each with a bold '
    + 'lead-in: **Where you stand**, **What to hold**, **Fuel and water**, **Kit and conditions**, '
    + '**What success looks like** (a race adds **The race** first and **Finish time** after the '
    + 'hold). End on a statement, no question.',
};

/* Canned prompts the shell sends on the athlete's behalf (digest button,
   post-commit take, activity takes). These are never the athlete's own
   words, so they must never be echoed as a user bubble — live or restored
   from history. */
const CANNED_PROMPTS = new Set([DIGEST_PROMPT, DIGEST_PROMPT_NO_PLAN, POST_COMMIT_PROMPT]);
const TAKE_PROMPT = /^(Quick take on|Full debrief of) my .* \(activity [^)]+\)\. Pull its detail/;
const BRIEF_PROMPT = /^(Quick|Full) brief for my .* \((session \d+|race on [\d-]+)\)\. Pull its brief/;
const isCanned = (text) => CANNED_PROMPTS.has(text) || TAKE_PROMPT.test(text) || BRIEF_PROMPT.test(text);

/* The notes the coach's own history records when the athlete applies or
   dismisses a card (session.py's `_note`), shown as an outcome line when
   restoring the conversation rather than as something the athlete typed. */
const OUTCOME_NOTE = /^\[(Applied|Dismissed): ([\s\S]*)\]$/;

const REPLACED_NOTE = 'Things changed since this was suggested, so here is the updated version.';
const UPDATED_NOTE = 'Updated for the change you just applied.';

const openCard = (proposal) => ({ proposal, status: 'open', note: '' });

/* What an isolated reply leaves in the chat: its Apply cards, never its text. */
const cardsOf = (reply) => (reply?.proposals?.length
  ? { role: 'coach', text: 'A plan change from the sheet you were reading.', chips: [], proposals: reply.proposals }
  : null);

export function createCoach(onChange, onPlanChanged = () => {}) {
  const state = {
    messages: [],   // {role: 'coach'|'user'|'note', text, chips, proposals, kind}
    thinking: false,
    thinkingText: 'Thinking…',
    busy: false,
    replies: 0,     // turns that added to the chat, for a shell's unread edge
  };

  const notify = () => onChange(state);

  function pushCoach(text, chips = []) {
    state.messages.push({ role: 'coach', text, chips });
    notify();
  }

  /** Send a canned prompt on the athlete's behalf — never echoed as a user
      bubble, since it's not the athlete's own words. Resolves with the
      coach's reply, as runNow does. `isolated` runs it outside the
      conversation: the coach sees no chat history, the turn is not kept,
      the chat shows no dots, and only its Apply cards join the chat. */
  function prompt(text, { isolated = false } = {}) {
    return run(text, { echoUser: false, isolated });
  }

  /* One thing at a time, in order: a message sent or a card tapped while a
     turn or an Apply is in flight waits its turn instead of being dropped
     (the shell has already cleared the draft). */
  let queue = Promise.resolve();
  function enqueue(job) {
    const next = queue.then(job);
    queue = next.catch(() => {});
    return next;
  }

  function run(text, opts) {
    return enqueue(() => runNow(text, opts));
  }

  /* Resolves with the coach's reply message (`failed` when the turn did not
     get one), or null for an empty message. */
  async function runNow(text, { echoUser = true, isolated = false } = {}) {
    const msg = (text || '').trim();
    if (!msg) return null;
    state.busy = true;
    if (echoUser) state.messages.push({ role: 'user', text: msg, chips: [] });
    if (!isolated) {
      state.thinking = true;
      state.thinkingText = thinkFor(msg);
    }
    notify();

    let reply = null;
    let planChanged = false;
    try {
      await chatStream(msg, (event) => {
        if (event.type === 'done') {
          const parsed = splitReplies(event.text || '');
          reply = {
            role: 'coach',
            text: parsed.text,
            chips: parsed.chips,
            charts: event.charts || [],
            proposals: (event.proposals || []).map(openCard),
          };
          // The shell re-reads the calendar once, after the reply is shown.
          planChanged = Boolean(event.plan_changed);
        } else if (event.type === 'error') {
          // Show the backend's reason (e.g. a rejected API key) — a
          // generic "try again" is wrong advice for a non-transient error.
          reply = {
            role: 'coach',
            text: event.text || 'Something went wrong talking to your coach. Try again in a moment.',
            chips: [],
            failed: true,
          };
        }
      }, { isolated });
    } catch (err) {
      reply = { role: 'coach', text: `Connection problem: ${err.message}`, chips: [], failed: true };
    } finally {
      // Dots on during an isolated turn belong to a message waiting behind it.
      if (!isolated) state.thinking = false;
      state.busy = false;
      const joined = isolated ? cardsOf(reply) : reply;
      if (joined) {
        state.messages.push(joined);
        state.replies += 1;
      }
      notify();
      if (planChanged) onPlanChanged();
    }
    return reply;
  }

  function findCard(id) {
    for (const m of state.messages) {
      const card = (m.proposals || []).find((c) => c.proposal.id === id);
      if (card) return card;
    }
    return null;
  }

  /** Apply or dismiss one card. Applying may hand back a replacement card
      (the plan moved since the preview) or a refusal instead of a write;
      either way the card stays open with a note saying why. One thing at a
      time: a card waits while a turn or another card is in flight. */
  function settle(id, apply) {
    return enqueue(() => settleNow(id, apply));
  }

  async function settleNow(id, apply) {
    const card = findCard(id);
    if (!card || card.status !== 'open') return;
    state.busy = true;
    card.status = apply ? 'applying' : 'dismissing';
    card.note = '';
    notify();
    let planChanged = false;
    try {
      if (!apply) {
        await api.dismissProposal(card.proposal);
        card.status = 'dismissed';
      } else {
        const out = await api.applyProposal(card.proposal);
        planChanged = Boolean(out.plan_changed);
        if (out.status === 'applied') {
          card.status = 'applied';
          card.note = out.message || '';
          if (planChanged) await refreshOpenCards();
        } else {
          card.status = 'open';
          if (out.proposal) {
            card.proposal = out.proposal;
            card.note = out.reason === 'stale' ? REPLACED_NOTE : (out.message || REPLACED_NOTE);
          } else {
            card.note = out.message || 'This change could not be applied.';
          }
        }
      }
    } catch (err) {
      card.status = 'open';
      card.note = err.message;
    } finally {
      state.busy = false;
      notify();
      if (planChanged) onPlanChanged();
    }
  }

  /** A write moved the plan the other open cards describe: bring each up
      to date now, so it shows the current numbers and one tap applies it
      (two swaps from one reply used to need a second tap on the second). */
  async function refreshOpenCards() {
    for (const m of state.messages) {
      for (const card of m.proposals || []) {
        if (card.status !== 'open') continue;
        try {
          const out = await api.refreshProposal(card.proposal);
          if (out.status === 'current') continue;
          if (out.proposal) {
            card.proposal = out.proposal;
            card.note = UPDATED_NOTE;
          } else {
            card.note = out.message || 'This change could not be applied.';
          }
        } catch (err) {
          card.note = err.message;
        }
      }
    }
  }

  // Cards render inside the shell's message list; their buttons are
  // handled here so the shells need not know about them.
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action="apply-proposal"], [data-action="dismiss-proposal"]');
    if (btn) settle(btn.dataset.id, btn.dataset.action === 'apply-proposal');
  });

  return {
    state,

    async init() {
      bindChartReadout();
      let restoredHistory = false;
      let history = [];
      try {
        history = (await api.conversationRecent(20)).messages.filter((m) => m.content);
      } catch { /* no restored history — the welcome path below runs instead */ }
      if (history.length) {
        const lastCoachIdx = history.map((m) => m.role).lastIndexOf('assistant');
        history.forEach((m, i) => {
          if (m.role === 'user') {
            // Canned prompts sent on the athlete's behalf are not their
            // words — don't resurrect them as a user bubble on reload.
            if (isCanned(m.content.trim())) return;
            const outcome = m.content.match(OUTCOME_NOTE);
            if (outcome) {
              state.messages.push({ role: 'note', kind: outcome[1].toLowerCase(), text: outcome[2] });
              return;
            }
            state.messages.push({ role: 'user', text: m.content, chips: [] });
          } else if (m.role === 'assistant') {
            const parsed = splitReplies(m.content);
            state.messages.push({
              role: 'coach',
              text: parsed.text,
              chips: i === lastCoachIdx ? parsed.chips : [],
              charts: parsed.charts,
            });
          }
        });
        notify();
        restoredHistory = true;
      }

      if (!restoredHistory) {
        try {
          const w = await api.welcome();
          if (w?.message) {
            const parsed = splitReplies(w.message);
            pushCoach(parsed.text, parsed.chips);
          }
        } catch { /* welcome is best-effort */ }
      }

      // Say up front when chat can't work, instead of failing on first send.
      try {
        const h = await api.health();
        if (h?.coach_ready === false) {
          pushCoach('Heads up — coach chat is off because no OpenRouter API key is set. '
            + 'Add one in Settings to talk to your coach. Your plan and training data '
            + 'all work without it.');
        }
      } catch { /* health probe is best-effort */ }
    },

    /** Whether the thread holds anything the athlete would lose — a welcome
        (or the chat-disabled notice) on its own is not worth confirming. */
    hasUserTurns: () => state.messages.some((m) => m.role === 'user'),

    /** "New Chat": clear the view and reset the coach's own session, then
        re-welcome — the cleared view is what the next load restores. */
    async newChat() {
      if (state.busy) return;
      state.messages = [];
      state.thinking = false;
      notify();
      try {
        const w = await api.resetChat();
        if (w?.message) {
          const parsed = splitReplies(w.message);
          pushCoach(parsed.text, parsed.chips);
        }
      } catch (err) {
        pushCoach(`Could not start a new chat: ${err.message}`);
      }
    },

    send: (text) => {
      // A take or a brief in flight shows nothing here; a message behind it does.
      if (text?.trim() && state.busy && !state.thinking) {
        state.thinking = true;
        state.thinkingText = thinkFor(text);
        notify();
      }
      return run(text);
    },

    /** Weekly digest — on-demand, through the normal chat turn. Canned, so
        it's never echoed as the athlete's own words. `hasPlan` picks the
        variant: the planned-vs-actual framing only makes sense with an
        active plan. */
    digest: (hasPlan) => prompt(hasPlan ? DIGEST_PROMPT : DIGEST_PROMPT_NO_PLAN),

    /** Canned take on a just-committed plan, sent on the athlete's behalf. */
    postCommitTake: () => prompt(POST_COMMIT_PROMPT),

    /** One activity's take at `depth` ('quick' | 'full') for the activity
        sheet; `activity` is {name, date, id, race}, `race` the race's label
        when the activity was one. Asked outside the conversation, so the
        chat neither colours it nor keeps it; resolves with the reply. */
    take: (depth, activity, { brief = null } = {}) => prompt(
      (activity.race ? RACE_TAKE_PROMPTS : TAKE_PROMPTS)[depth](activity, brief),
      { isolated: true },
    ),

    /** One subject's brief at `depth`; `subject` is {handle, name, date}. */
    brief: (depth, subject) => prompt(BRIEF_PROMPTS[depth](subject), { isolated: true }),
  };
}
