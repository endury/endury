/* Endury service worker. Three kinds of request:
   - the site's Pyodide copy (a version-pinned path) and the content-hashed
     Python bundle (py/endury-<sha12>.zip): cache-first. Their bytes never
     change under a URL, and without this cache every visit re-downloads
     ~34 MB (scipy alone is ~14 MB).
   - the site's own files: network-first, so a deploy is picked up on the
     next load, with the cached copy as the offline fallback.
   - everything else — intervals.icu, OpenRouter — is left alone: that
     traffic carries the athlete's keys and data and is never cached. */

const PYODIDE_PATH = /\/pyodide\/v314\.0\.7\//;
const BUNDLE_PATH = /\/py\/endury-[0-9a-f]{12}\.zip$/;
const RUNTIME_CACHE = 'endury-runtime-v314.0.7';
const BUNDLE_CACHE = 'endury-bundle';
const SITE_CACHE = 'endury-site';
const OWN_CACHES = new Set([RUNTIME_CACHE, BUNDLE_CACHE, SITE_CACHE]);

self.addEventListener('install', () => self.skipWaiting());

// Drop the caches of an older Pyodide pin. Only endury-* names: on a shared
// <user>.github.io origin the other caches belong to other sites.
self.addEventListener('activate', (e) => e.waitUntil((async () => {
  const stale = (await caches.keys()).filter((k) => k.startsWith('endury-') && !OWN_CACHES.has(k));
  await Promise.all(stale.map((k) => caches.delete(k)));
  await self.clients.claim();
})()));

/* `latestOnly`: a new bundle replaces the previous one instead of piling up. */
async function cacheFirst(name, request, { latestOnly = false } = {}) {
  const cache = await caches.open(name);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) {
    if (latestOnly) await Promise.all((await cache.keys()).map((k) => cache.delete(k)));
    await cache.put(request, response.clone());
  }
  return response;
}

/* A page load starts from an empty site cache, so it only ever holds the
   files of the latest load — every deploy's build-id directory is new URLs. */
async function networkFirst(request) {
  const cache = await caches.open(SITE_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) {
      if (request.mode === 'navigate') await Promise.all((await cache.keys()).map((k) => cache.delete(k)));
      await cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (PYODIDE_PATH.test(url.pathname)) {
    e.respondWith(cacheFirst(RUNTIME_CACHE, request));
  } else if (BUNDLE_PATH.test(url.pathname)) {
    e.respondWith(cacheFirst(BUNDLE_CACHE, request, { latestOnly: true }));
  } else {
    e.respondWith(networkFirst(request));
  }
});
