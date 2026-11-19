/* Endury — "Connect OpenRouter": OpenRouter's OAuth PKCE flow, which hands
   the athlete a key of their own without copy-paste, and needs no client
   secret or app registration. startConnect() leaves for OpenRouter's sign-in
   page; OpenRouter sends the athlete back to this page with ?code=, and
   finishConnect() at boot trades that code for the key. The verifier waits
   in localStorage, not sessionStorage: signing up at OpenRouter can finish
   in another tab (an emailed link). */

import { pkcePair } from './pkce.js';

const AUTH_URL = 'https://openrouter.ai/auth';
const VERIFIER_KEY = 'endury.openrouter_verifier';
const DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1';

/** Leave for OpenRouter's sign-in; it returns to this page with ?code=. */
export async function startConnect() {
  const { verifier, challenge } = await pkcePair();
  localStorage.setItem(VERIFIER_KEY, verifier);
  const url = new URL(AUTH_URL);
  url.search = new URLSearchParams({
    callback_url: location.origin + location.pathname,
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  location.assign(url);
}

/** At boot: the athlete's new key when OpenRouter just sent them back with
    ?code=, else null. Throws with an athlete-facing message when the trade
    fails. The code leaves the address bar first, so a reload never trades
    it twice. */
export async function finishConnect(baseUrl, onStatus) {
  const code = new URLSearchParams(location.search).get('code');
  if (!code) return null;
  history.replaceState(null, '', location.pathname);
  onStatus('Connecting OpenRouter…');
  let key = null;
  try {
    const verifier = localStorage.getItem(VERIFIER_KEY);
    localStorage.removeItem(VERIFIER_KEY);
    const res = await fetch(`${baseUrl || DEFAULT_BASE_URL}/auth/keys`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, code_verifier: verifier, code_challenge_method: 'S256' }),
    });
    if (res.ok) ({ key } = await res.json());
  } catch { /* storage blocked or OpenRouter unreachable: same answer below */ }
  if (!key) throw new Error('Connecting OpenRouter didn’t work. Try Connect again, or paste a key.');
  return key;
}
