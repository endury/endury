/* Endury — the athlete's own keys and endpoints. They live in this
   browser's localStorage only; the Python worker sends each one to its own
   host (intervals.icu, OpenRouter) and nowhere else. intervals.icu takes
   the token Connect brought back (data/intervals_connect.js) or, without
   one, the athlete's API key. Boot hands them to
   Python unchanged: most are CoachConfig's fields, `demo` ('1' while this
   browser runs the demo athlete) picks the demo boot, and Python ignores
   `athlete_name` (it only lets a password manager save the intervals.icu
   key under a name, and greets the athlete) and `chat_consent` (the version
   of the consent text the athlete agreed to; Settings refuses an OpenRouter
   key without it, and boot withholds the key). Every storage access is wrapped: a private
   window or blocked site data reads as "no settings yet" instead of
   breaking the page. */

const STORAGE_KEY = 'endury.settings';

/* Raise when what chat sends, or where it goes, materially changes: the key
   stays in this browser, but chat is off until the athlete ticks the box again. */
export const CHAT_CONSENT_VERSION = '2';
export const hasChatConsent = (settings) => settings.chat_consent === CHAT_CONSENT_VERSION;

export const SETTINGS_FIELDS = [
  'intervals_api_key',
  'intervals_access_token',
  'intervals_athlete_id',
  'openrouter_api_key',
  'model',
  'intervals_base_url',
  'openrouter_base_url',
  'athlete_name',
  'chat_consent',
  'demo',
];

/** The stored settings, every field a string ('' when unset). */
export function loadSettings() {
  let raw = {};
  try {
    raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') || {};
  } catch { /* unreadable or blocked storage: start empty */ }
  return Object.fromEntries(SETTINGS_FIELDS.map((k) => [
    k, typeof raw[k] === 'string' ? raw[k].trim() : '',
  ]));
}

/** Store `settings`; false when this browser refuses to keep them. */
export function saveSettings(settings) {
  const clean = Object.fromEntries(SETTINGS_FIELDS.map((k) => [k, String(settings[k] ?? '').trim()]));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clean));
    return true;
  } catch {
    return false;
  }
}

/** Forget everything this browser holds, both keys included. */
export function clearSettings() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch { /* nothing stored, or storage blocked: nothing to forget */ }
}

/** Whether this browser can reach an intervals.icu account: by Connect's
    token or by an API key. */
const hasIntervals = (settings) =>
  Boolean(settings.intervals_access_token || settings.intervals_api_key);

/** Planning needs intervals.icu (or the demo); everything else is
    optional. */
export const hasRequiredSettings = (settings) =>
  hasIntervals(settings) || settings.demo === '1';

/** True while this browser runs the demo athlete: no account of its own yet. */
export const isDemo = (settings) => !hasIntervals(settings) && settings.demo === '1';

/** What boot passes to Python: blanks dropped, so CoachConfig's defaults
    apply (athlete "0" = the credential's own athlete, the public base URLs, the
    default model, and no OpenRouter key = chat off). */
export function bootSettings(settings) {
  return Object.fromEntries(SETTINGS_FIELDS
    .map((k) => [k, String(settings[k] ?? '').trim()])
    .filter(([k, v]) => v && (k !== 'openrouter_api_key' || hasChatConsent(settings))));
}

