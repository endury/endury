<!-- SPDX-License-Identifier: AGPL-3.0-or-later -->
# Privacy

Endury has no account and no Endury server holds your data. This page says
what it stores, where, what leaves your browser, and how to remove all of
it. The threat model and how to report a vulnerability are in
[SECURITY.md](../SECURITY.md).

## What the host sees

The app sends nothing you enter or do to its authors or to whoever hosts the
site. The host sees requests for the static files, as its own hosting provider
reports them. If you ask a person for help and share access with them, that is a
separate matter between the two of you.

## Your keys

They stay in this browser (`localStorage`). Each one is sent only to its
own service: the intervals.icu key to intervals.icu, the OpenRouter key
to OpenRouter. The intervals.icu key has full access to your account: API
keys have no narrower scope. intervals.icu's OAuth does offer per-scope grants,
and Endury currently uses a key. The optional name you enter beside the key
stays in this browser too and is never sent anywhere.

## What stays in your browser

Nothing is stored or cached until you start the demo or connect, except a theme
you pick. After that: your settings (keys, athlete id, AI model, service addresses
if you changed them, name, the version of the chat consent, the demo flag),
your last calendar place and your theme, a one-time code during OpenRouter
sign-in, and the cached runtime and site files. **Forget my keys** removes
the settings; clearing this site's data removes all of it.

## The demo

The demo athlete is generated in your browser; its "intervals.icu" is an
in-memory stand-in, not your account. Your browser keeps only that it runs the
demo and your last place. Nothing you do in it is sent anywhere, except to
OpenRouter if you add a chat key while trying it.

## What Endury writes to your intervals.icu account

Endury reads your activities, thresholds and wellness live and stores no
copy of them anywhere else. It writes:

- planned sessions and races, as calendar events tagged `endury` (older ones
  `coachie`, Endury's former name), and it removes the ones it created
  when a plan changes or goes;
- workouts the coach books for you;
- the pairing of a completed activity with its planned session, when you pair
  them or when only one session fits (it sets the activity's paired event on
  intervals.icu);
- the thresholds you save in Planning Inputs (FTP, threshold heart rate,
  threshold pace, swim CSS), to every intervals.icu sport settings entry
  of that sport, and the feel and effort you enter, to the activity;
- one hidden note, **"Endury data (do not edit)"**, parked on
  31 December 2099. It holds everything else Endury keeps: your plan,
  races, self-assessment, profile, and the coach's memory document
  (readable markdown at the top, a compressed data block below). Every
  device you use loads it. Do not edit it by hand;
- a second hidden note, **"Endury chat (do not edit)"**, on the same day,
  holding the last 50 chat messages, so every device picks up the
  conversation where you left it. **New Chat** empties it;
- a third hidden note, **"Endury session takes (do not edit)"**, on the
  same day, holding the coach's Quick takes and Full debriefs on your
  activities and its Quick and Full briefs on coming sessions and races,
  so a sheet shows its answer again on every device without asking the
  coach twice. **New Chat** leaves it alone.

"Hidden" means off your calendar view. The notes are not encrypted: the memory
document is readable text and the chat and takes notes are only compressed, so a
coach or app you have given access on intervals.icu may be able to read them,
including any health details you mention.

## What leaves your browser when you chat

Chat stays off until you add an OpenRouter key and tick the consent box in
Settings, which names all of this. Connect takes you to OpenRouter's own
sign-in. Your message, the recent conversation and the coach's working context
then go to OpenRouter (USA) and on to the company that runs the AI model. For
Endury's default model that is currently Google or Amazon, in the US, the EU or
elsewhere; another model you pick may go to another company. The working context
is your profile, the memory document, the plan summary, recent training
including activity names, which can name a place, recent wellness (health data
such as sleep, resting heart rate, HRV and weight, if you record them) and the
results of the lookups the coach makes. GPS tracks reach it only as distances
from a shared point.

Endury asks OpenRouter to avoid providers it lists as collecting the content
and to use only endpoints it lists as keeping no data. That rests on OpenRouter's
lists, which it calls its best knowledge, and a provider may still keep flagged
content under its own terms. Each call also names Endury as the calling app, which
OpenRouter may show in its public rankings. Chat is for adults. Clearing the
key, or **Forget my keys**, stops it. With no OpenRouter key, nothing goes there.

## The code the page loads

Everything the page loads comes from the site itself, the Python runtime
and its packages included; nothing is fetched from a third party. Like any
code the page loads, it could in principle read what is in the browser.
That is the trust you place in whoever hosts the page. If that matters to
you, [host your own copy](../README.md#run-or-host-your-own-copy).

## The hosted site

endury.app has a [legal notice](https://endury.app/impressum.html) and a
[privacy policy](https://endury.app/privacy.html) for the site itself: who
runs it, what Cloudflare sees when it serves a file, and your rights.

## Remove everything

Delete the plan in Endury, which removes its sessions and races from today on.
Past events, workouts the coach booked and races added outside a plan stay
until you delete them in intervals.icu; events carry the tag `endury` (older
ones `coachie`). Delete the "Endury data", "Endury chat" and "Endury session
takes" notes in intervals.icu, then clear this site's data in your browser.
