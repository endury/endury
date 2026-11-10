<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Security policy

## Supported versions

There is no version history to patch separately. The only supported
deployments are the live site at [endury.app](https://endury.app) and the
`main` branch. A fix ships as a new build, not a backport.

## Threat model

Endury has no server. It runs entirely in your browser: your intervals.icu
API key, and your OpenRouter API key if you use chat, live in this
browser's `localStorage` and are sent only to those two services. Any code
the page loads runs in the same origin and could in principle read those
keys; that is why the site serves everything itself, the Pyodide runtime
and its Python packages included (staged by `make site` from Pyodide's
release, each package checked against the sha256 in Pyodide's lock), and
sends a Content Security Policy that allows no other origin (`web/_headers`,
honoured by Cloudflare). The intervals.icu key has full access to your
account: API keys have no narrower scope. intervals.icu's OAuth does offer
per-scope grants, and Endury currently uses a key. This is why the README
asks you to host Endury on an origin that serves nothing else. What Endury
stores where, and how to remove it, is in [docs/privacy.md](docs/privacy.md).

## In scope

- A way for the page's own code, or something it loads, to send an API key
  anywhere other than intervals.icu or OpenRouter.
- Cross-origin exposure of `localStorage` or of the site's own requests.
- The service worker serving stale, wrong, or attacker-controlled code
  instead of the current build.
- A prompt-injection path, through an activity note, the memory document
  or a tool result, that gets the coach to make a plan change that needs an
  Apply card without the athlete tapping one. A few small writes happen at
  once by design and report back in the reply: booking one workout, moving
  a session, marking one done, saving a race, and the memory and profile.
- A way for one athlete's browser to read or write another athlete's
  intervals.icu data through Endury.

## Out of scope

- Vulnerabilities in intervals.icu or OpenRouter themselves; report those to
  their own teams.
- The coaching quality or correctness of the AI model's chat replies.
  File that as a normal bug or plan-quality issue instead.

## Your key

Never send your API key to anyone working on Endury, by any channel: nobody here
will ask for it. To report a bug, show it on the demo or with invented data. If a
key leaks, generate a new one at intervals.icu or OpenRouter.

## Reporting

Please use GitHub's private vulnerability reporting: open this repository's
"Security" tab and choose "Report a vulnerability", rather than a public
issue.

Endury is maintained by one person, in their own time. There is no fixed
response time, but you will hear back as soon as they can manage.
