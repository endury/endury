# web

The frontend: a single-page app of plain ES modules, no framework and no
bundler. What is in this folder is what the browser runs; `make site`
copies it and pins the asset URLs.

| Path | What it is |
| --- | --- |
| `index.html`, `main.js` | The entry point: picks a shell by viewport, shows the front door without an intervals.icu key, starts the worker, hands off to the shell. |
| `tokens.css`, `base.css`, `vendor/inter/` | The design tokens, the shared styles, the self-hosted font. |
| `theme.js` | Sets the dark or light theme before the first paint; Settings switches it. |
| `impressum.html`, `privacy.html`, `legal.css` | The hosted site's legal notice and privacy policy, linked from the landing and Settings. A self-hoster puts their own name in the marked places. |
| `_headers` | Cloudflare's response headers: the Content Security Policy (this origin, intervals.icu and OpenRouter only) and the usual hardening. Other hosts ignore it. |
| `components/` | HTML-string renderers and their CSS: session and race sheets with the coach's brief, week summary, plan editor, coach and charts, settings, front door, debrief, modal. |
| `data/api.js` | The RPC client: unwraps the worker's envelope, holds the one-writer lock, feeds the runtime banners. |
| `data/settings.js` | The keys and the chat consent in `localStorage`; "Forget my keys". |
| `data/openrouter_connect.js` | "Connect OpenRouter": the OAuth PKCE round trip that fills the key. |
| `data/adapters.js`, `data/store.js`, `data/format.js` | Wire shapes into view shapes; the calendar state both shells render, which opens on today unless the athlete left another day moments ago; formatting. |
| `worker/py_worker.js` | Loads the site's own Pyodide and the Python bundle, runs one RPC call at a time, and pins the Pyodide version and package list. |
| `worker/analysis_worker.js`, `data/analysis_sandbox.js` | The sandbox for the coach's analysis scripts: a second Pyodide with numpy, no key and no network once loaded. |
| `shells/` | `shared.js`, the goal catalogues and the editor model both shells use; `web/` and `mobile/` compose the components per form factor. |
| `sw.js` | The service worker: cache-first for the pinned Pyodide path and the hashed bundle, network-first for the rest of the site; never touches intervals.icu or OpenRouter. |
| `manifest.webmanifest`, `icons/` | The installable-app manifest and its icons. |

## Conventions

- Plain ES modules, no build step.
- Colours come from the design tokens in `tokens.css`; a new colour is a
  new token there, not a literal in a component, with a value in both the
  dark set and the light one, so neither theme shows a stray colour.
- Every path is relative, so the site works under a sub-path. The
  worker's `new URL('../../…', self.location)` paths are correct because
  it is served from `site/<build-id>/worker/`.
- The plan wizard's two native date inputs are deliberate, for
  accessibility and mobile use.

Start reading: [main.js](main.js), then [data/api.js](data/api.js).
