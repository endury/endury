/* Endury — "Connect intervals.icu": intervals.icu's OAuth flow for a public
   client with PKCE, so no client secret. It hands this browser an access
   token limited to SCOPES, which the athlete can withdraw at intervals.icu
   without touching their API key. startConnect() leaves for intervals.icu's
   consent page; intervals.icu sends the athlete back to this page with
   ?code= and the ?state= it was given, and finishConnect() at boot trades
   the code for the token. The round trip waits in localStorage, as
   OpenRouter's does. */

import { pkcePair, randomToken } from './pkce.js';

/* Endury's app at intervals.icu. A public client, so the id is no secret,
   but intervals.icu sends the athlete back only to an address registered
   for the app: Connect is offered on those hosts, and any other copy
   connects with an API key until it names an app of its own here. An
   overridden API address (the e2e fakes) is not intervals.icu and takes
   any id. */
const CLIENT_ID = '1881';
const CLIENT_HOSTS = ['endury.app', 'localhost'];
const clientId = (settings) => {
  if (settings.intervals_base_url) return 'endury';
  return CLIENT_HOSTS.includes(location.hostname) ? CLIENT_ID : '';
};

/* What Endury asks for: activities (WRITE pairs one with its session and
   saves feel and effort), wellness, the calendar (sessions, races and
   Endury's own notes) and settings (the thresholds Planning Inputs saves).
   Not chats, not the workout library. */
const SCOPES = ['ACTIVITY:WRITE', 'WELLNESS:READ', 'CALENDAR:WRITE', 'SETTINGS:WRITE'];

const DEFAULT_BASE_URL = 'https://intervals.icu/api/v1';
const PENDING_KEY = 'endury.intervals_connect';

const baseUrl = (settings) => settings.intervals_base_url || DEFAULT_BASE_URL;

/** Whether this copy offers Connect: it has an app at intervals.icu. */
export const canConnect = (settings) => Boolean(clientId(settings));

/** Whether `granted` (intervals.icu's comma list) covers SCOPES; WRITE
    implies READ. */
function coversScopes(granted) {
  const have = new Set(String(granted || '').split(',').map((s) => s.trim()));
  return SCOPES.every((s) => have.has(s) || have.has(s.replace(':READ', ':WRITE')));
}

/** Leave for intervals.icu's consent page; it returns here with ?code=.
    Throws when this browser refuses to keep the round trip. */
export async function startConnect(settings) {
  const { verifier, challenge } = await pkcePair();
  const state = randomToken(16);
  localStorage.setItem(PENDING_KEY, JSON.stringify({ verifier, state }));
  const url = new URL('/oauth/authorize', baseUrl(settings));
  url.search = new URLSearchParams({
    client_id: clientId(settings),
    // The page's folder, as registered: never index.html or a query.
    redirect_uri: new URL('./', location.href).href,
    scope: SCOPES.join(','),
    state,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  location.assign(url);
}

/** At boot: what a finished Connect adds to the settings (the token, and
    the athlete's name when none is set), else null. Throws with an
    athlete-facing message when the athlete declined, the trade failed or
    a permission is missing. The code leaves the address bar first, so a
    reload never trades it twice. */
export async function finishConnect(settings, onStatus) {
  const params = new URLSearchParams(location.search);
  let pending = null;
  try {
    pending = JSON.parse(localStorage.getItem(PENDING_KEY));
  } catch { /* storage blocked: nothing waits */ }
  // Ours carries the state; a decline carries ?error= alone. OpenRouter's
  // return has neither.
  if (!params.has('state') && !(pending && params.has('error'))) return null;
  history.replaceState(null, '', location.pathname);
  try {
    localStorage.removeItem(PENDING_KEY);
  } catch { /* storage blocked: nothing to remove */ }
  if (params.has('error')) throw new Error('intervals.icu isn’t connected: access was declined there.');
  onStatus('Connecting intervals.icu…');
  let grant = null;
  try {
    if (pending?.state === params.get('state')) {
      // A form post, not JSON: the token endpoint answers no preflight.
      const res = await fetch(new URL('/api/oauth/token', baseUrl(settings)), {
        method: 'POST',
        body: new URLSearchParams({
          client_id: clientId(settings),
          code: params.get('code'),
          code_verifier: pending.verifier,
        }),
      });
      if (res.ok) grant = await res.json();
    }
  } catch { /* intervals.icu unreachable: same answer below */ }
  if (!grant?.access_token) {
    throw new Error('Connecting intervals.icu didn’t work. Try Connect again, or use an API key.');
  }
  if (!coversScopes(grant.scope)) {
    throw new Error('Endury needs all four permissions on the intervals.icu page: activities, '
      + 'wellness, calendar and settings. Connect again and leave them ticked.');
  }
  return {
    intervals_access_token: grant.access_token,
    athlete_name: settings.athlete_name || grant.athlete?.name || '',
  };
}

/** Withdraw this browser's token at intervals.icu, best effort: "Forget my
    keys" forgets it here either way. */
export async function disconnect(settings) {
  if (!settings.intervals_access_token) return;
  try {
    await fetch(`${baseUrl(settings)}/disconnect-app`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${settings.intervals_access_token}` },
    });
  } catch { /* offline: intervals.icu's settings remove Endury too */ }
}
