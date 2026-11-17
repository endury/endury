/* Endury — PKCE for the two Connect round trips (OpenRouter, intervals.icu):
   a random verifier this browser keeps and the S256 challenge the sign-in
   page gets, so only this browser can trade the code that comes back. */

const base64url = (bytes) => btoa(String.fromCharCode(...bytes))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

/** `bytes` random bytes, base64url. */
export const randomToken = (bytes) => base64url(crypto.getRandomValues(new Uint8Array(bytes)));

/** A fresh {verifier, challenge} pair. */
export async function pkcePair() {
  const verifier = randomToken(32);
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}
