"""Browser E2E for the static site: Pyodide in a worker, fakes for everything else.

Self-contained and offline once ``make site`` has cached Pyodide: builds
``site/``, serves it from a stdlib HTTP server, and stands up two fakes
on their own ports — a :class:`~devtools.frontend.fakes.FakeIntervals`
account (empty: no history, so the wizard asks for self-assessed volume)
and a :class:`~devtools.frontend.fakes.FakeOpenRouter` scripted to propose
one ``pause_training`` write and to run one analysis that draws a chart.
No real key or account is ever touched: the
site reaches the fakes through the settings' Advanced base-URL overrides.

Chromium (service workers blocked) then drives the whole life of a plan:

1. the front door: "I have my intervals.icu key" straight to the key step
   (base-URL overrides, athlete name, the key), the ready screen it boots
   into, then "Look around first" mounts the shell (later contexts get the
   same settings injected into ``localStorage`` directly, skipping the
   door); Connect OpenRouter then happens from the app's own Settings
   panel (OpenRouter's sign-in page stubbed to approve at once), which
   reboots with the key present;
2. boot: Pyodide, the Endury bundle, the empty store;
3. the wizard, with the self-assessment questionnaire;
4. generate -> preview (a sheet row opens its page and goes back) -> apply
   (the commit pushes the plan and saves the store note);
5. reschedule from the session sheet's Move page, then drag-to-reschedule;
6. replan;
7. reload: the same active plan comes back from the store note, and boot
   writes nothing to intervals.icu;
8. chat -> Apply card -> Apply: the pause lands on the intervals.icu calendar;
   then a chart request: the analysis runs in the sandbox worker, its chart
   draws under the reply and is still there after a reload; then a brief from
   a session sheet's Coach's brief page, answered without the conversation and
   kept out of it, its answer on the sheet's row;
9. a new browser context (no local state) hydrates the identical plan and
   the chat message sent in step 8, both from intervals.icu;
10. a second tab in the first context is read-only and refuses writes;
11. the mobile shell renders the plan, and its agenda scrolls on;
12. the demo door boots a sample athlete on its own in-memory
    intervals.icu, never touching the fake account;
13. a fresh context builds an Experimental plan (trail_20k at a low volume):
    cancelling its confirm dialog leaves the wizard open with the
    athlete's answers intact and no error toast, confirming it builds the
    plan and the preview names the maturity caveat;
14. an account whose trail maintenance block ended last Sunday: the plan
    card shows the block finished, "Start a new plan" applies the next
    block over it, and every workout the finished block put on the
    calendar stays there.

Console and page errors are collected throughout; the exit code is non-zero
on any failed step or page error. The site build downloads Pyodide the
first time, so it is not part of ``make test``.

Usage::

    make run-devtool TOOL=e2e_lifecycle
    make run-devtool TOOL=e2e_lifecycle ARGS='--goal trail_maintenance --weeks 6 --keep'

The wizard shows only the plans the menus offer, so ``--goal`` and
``--ambition`` must name one of them.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import date, datetime, time, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlsplit

from devtools.frontend.fakes import (
    BurstHTTPServer,
    CorsJsonServer,
    FakeIntervals,
    FakeOpenRouter,
    Script,
)
from devtools.site import build_site
from endury.browser import rpc
from endury.intervals.models import AthleteProfile, SportSettingsRow

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Route

#: Generous: a cold boot downloads ~34 MB of Pyodide and packages.
BOOT_TIMEOUT_MS = 300_000

#: The athlete message the fake coach answers with its one proposed write.
PAUSE_TRIGGER = "I'm sick"
#: Asks for a chart: the fake coach answers with an analysis script, which the
#: sandbox worker runs in the browser (the account has no history, so numpy
#: draws the bars).
CHART_TRIGGER = "Plot my activities"
CHART_CODE = (
    "print(len(ana.history), ana.athlete)\n"
    "hours = np.array([4.5, 6.0, 3.5])\n"
    "ana.chart('bar', ['swim', 'ride', 'run'], [hours], 'Activities by sport', unit='h')\n"
)
CHART_REPLY = "Here they are by sport in the chart below."

#: OpenRouter's sign-in page, stubbed: it sends the page straight back to its
#: callback with CONNECT_CODE, as an athlete approving at once would.
OPENROUTER_AUTH = "https://openrouter.ai/auth"
CONNECT_CODE = "e2e-connect-code"

DRAG_JS = """([cardSel, colSel]) => {
  const card = document.querySelector(cardSel);
  const col = document.querySelector(colSel);
  if (!card || !col) return JSON.stringify({missing: !card ? 'card' : 'col'});
  const dt = new DataTransfer();
  card.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
  const targets = document.querySelectorAll('.cbw-daycol.is-droptarget').length;
  col.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt}));
  const hover = col.classList.contains('cb-drop');
  col.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt}));
  card.dispatchEvent(new DragEvent('dragend', {bubbles: true}));
  return JSON.stringify({targets, hover});
}"""

# Calls one api.js export through the page's own module instance (the same
# worker the UI uses); a rejection comes back as {error}.
API_JS = """async ([url, name, args]) => {
  const { api } = await import(url);
  try {
    return { result: await api[name](...args) };
  } catch (err) {
    return { error: err.message, code: err.code || null };
  }
}"""


# True once the calendar shows cards on the given dates, all of them skipped.
SKIPPED_IN_WINDOW_JS = """(dates) => {
  const cards = dates.flatMap((d) =>
    [...document.querySelectorAll(`.cbw-daycol[data-date="${d}"] .cb-card`)]);
  return cards.length > 0 && cards.every((c) => c.querySelector('.cb-badge--skip'));
}"""

# The landing holds the calendar until the athlete touches it: a script that
# scrolls has to say so first.
ATHLETE_MOVES_JS = "window.dispatchEvent(new Event('wheel'))"

# What the phone's calendar shows, read against the page's own (faked) clock.
LANDED_JS = """() => {
  const d = new Date();
  const today = [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'),
    String(d.getDate()).padStart(2, '0')].join('-');
  const feed = document.querySelector('#cbm-feed');
  const sec = document.getElementById(`cbm-sec-${today}`);
  const head = feed.querySelector('.cbm-weekhead')?.offsetHeight ?? 0;
  return {
    today,
    offset: sec ? Math.round(sec.getBoundingClientRect().top - feed.getBoundingClientRect().top - head) : null,
    tag: document.querySelector('.cbm-tag--today')?.closest('[data-date]')?.dataset.date ?? null,
    title: document.querySelector('#cbm-weektitle')?.textContent ?? null,
  };
}"""

# No scroll anchoring, as on iOS Safari, and a height change above today just
# after the real day sections arrive.
LATE_SHIFT_JS = """addEventListener('DOMContentLoaded', () => {
  const style = document.createElement('style');
  style.textContent = '* { overflow-anchor: none !important; }';
  document.head.append(style);
  new MutationObserver((_, watcher) => {
    const first = document.querySelector('[data-week-start] .cbm-daysec:not(.is-skeleton)');
    if (!first) return;
    watcher.disconnect();
    first.style.paddingTop = '240px';
    window.__shifted = true;
  }).observe(document.body, { childList: true, subtree: true });
});"""

# Playwright's service_workers="block" stubs register() to resolve undefined,
# which no browser does; refuse the registration the way a browser can.
REFUSE_SW_JS = """if (navigator.serviceWorker) navigator.serviceWorker.register = () =>
  Promise.reject(new DOMException('Service workers are blocked in the E2E', 'SecurityError'));"""


class QuietSiteHandler(SimpleHTTPRequestHandler):
    """Static files for the site, without a log line per request."""

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet: the harness reports what matters."""


def triathlete() -> AthleteProfile:
    """An intervals.icu athlete with swim, bike and run thresholds."""
    return AthleteProfile(
        id="i1",
        sportSettings=[
            SportSettingsRow(
                types=["Run"], lthr=165, max_hr=190, threshold_pace=1000 / 270
            ),
            SportSettingsRow(types=["Ride", "VirtualRide"], ftp=250, lthr=160),
            SportSettingsRow(types=["Swim"], threshold_pace=100 / 105),
        ],
    )


def finished_block_account() -> FakeIntervals:
    """An account whose eight-week trail maintenance block ended last Sunday.

    The block is generated and committed natively through the worker's own
    methods with the clock at its first Monday, so the store note and the
    calendar hold what a real block leaves behind.
    """
    fake = FakeIntervals(athlete=triathlete())
    today = date.today()
    start = today - timedelta(days=today.weekday(), weeks=8)
    request = {
        "races": [
            {
                "goal_type": "trail_maintenance",
                "race_date": (start + timedelta(weeks=8, days=-1)).isoformat(),
                "priority": "A",
            }
        ],
        "training_start": start.isoformat(),
        "ambition_level": "basic",
        "accept_experimental": True,
    }

    async def commit() -> dict:
        await rpc.boot({"intervals_api_key": "e2e-key"}, transport=fake.transport())
        await rpc.dispatch(
            "put_self_assessment",
            {"sports": {"running": {"weekly_hours": 4, "longest_session_min": 60}}},
        )
        event = (await rpc.dispatch("generate_plan", request))["result"]
        preview = {k: v for k, v in event.items() if k not in ("type", "source")}
        params = {
            "name": "Autumn block",
            "generate_request": request,
            "generate_result": preview,
        }
        return await rpc.dispatch("planner_commit", params)

    os.environ["COACHING_BUDDY_SIM_DATE"] = start.isoformat()
    try:
        outcome = asyncio.run(commit())
    finally:
        del os.environ["COACHING_BUDDY_SIM_DATE"]
    if outcome.get("result", {}).get("status") != "ok":
        raise RuntimeError(f"the finished block was not committed: {outcome}")
    return fake


class Harness:
    """One lifecycle run: the browser flow against the site and its fakes."""

    def __init__(
        self,
        base: str,
        build_id: str,
        settings: dict[str, str],
        icu: FakeIntervals,
        coach: FakeOpenRouter,
        shots: Path,
    ) -> None:
        self.base = base
        self.api_url = f"{base}/{build_id}/data/api.js"
        self.settings = settings
        self.icu = icu
        self.coach = coach
        self.shots = shots
        self.results: list[tuple[str, bool, str]] = []
        self.console_errors: list[dict] = []
        self.page_errors: list[dict] = []
        self.step = "boot"

    # -- plumbing ---------------------------------------------------------

    def record(self, step: str, ok: bool, evidence: str) -> None:
        """Record and print one step verdict."""
        self.results.append((step, ok, evidence))
        print(f"[{'PASS' if ok else 'FAIL'}] {step}: {evidence}", flush=True)

    def api(self, page: Page, name: str, *args: Any) -> Any:
        """Call ``api.<name>(*args)`` in the page; raise on a rejected call."""
        out = page.evaluate(API_JS, [self.api_url, name, list(args)])
        if "error" in out:
            raise RuntimeError(f"api.{name} failed: {out['error']}")
        return out["result"]

    def active_plan(self, page: Page) -> dict:
        """The page's ``planner_active`` payload."""
        payload: dict = self.api(page, "plannerActive")
        return payload

    def plan_state(self, page: Page) -> dict:
        """The active plan plus its whole calendar window, as the page reads them."""
        active = self.active_plan(page)
        plan = active["active_plan"]
        calendar = self.api(page, "calendarData", plan["start_date"], plan["end_date"])
        return {"active": active, "calendar": calendar}

    def writes(self) -> int:
        """How many writing requests the fake intervals.icu has served."""
        return sum(1 for method, _ in list(self.icu.calls) if method != "GET")

    def plan_events(self, plan_id: int) -> dict[str, dict]:
        """The fake calendar's events for this plan, by external id."""
        prefix = f"coachie:{plan_id}:"
        return {k: v for k, v in list(self.icu.events.items()) if k.startswith(prefix)}

    def wire(self, page: Page) -> None:
        """Attach console/page error collectors to a page."""
        page.on(
            "console",
            lambda m: (
                self.console_errors.append({"step": self.step, "text": m.text})
                if m.type == "error"
                else None
            ),
        )
        page.on(
            "pageerror",
            lambda e: self.page_errors.append({"step": self.step, "text": str(e)}),
        )

    def new_context(
        self, browser: Browser, width: int, height: int, *, settings: bool = True
    ) -> BrowserContext:
        """A fresh context (no local state), by default with the settings stored."""
        ctx = browser.new_context(
            viewport={"width": width, "height": height}, service_workers="block"
        )
        ctx.add_init_script(REFUSE_SW_JS)
        if settings:
            stored = json.dumps(json.dumps(self.settings))
            ctx.add_init_script(f"localStorage.setItem('endury.settings', {stored});")
        return ctx

    def wait_booted(self, page: Page, ready: str) -> None:
        """Wait until the shell has mounted and ``ready`` rendered; raise on a failed boot."""
        page.wait_for_selector(f"{ready}, .cb-boot__error", timeout=BOOT_TIMEOUT_MS)
        failure = page.locator(".cb-boot__error")
        if failure.count():
            raise RuntimeError(f"boot failed: {failure.inner_text()}")

    def wait_toast(self, page: Page, expected: str, timeout: int = 30000) -> str:
        """Poll the shared .cb-toast node until its text contains expected.

        The toast is a single reused element whose textContent is overwritten
        in place, so a presence-wait can match a stale previous toast.

        Args:
            page: The Playwright page.
            expected: Substring the toast text must contain.
            timeout: Milliseconds to wait before raising.
        """
        page.wait_for_function(
            "(x) => { const el = document.querySelector('.cb-toast');"
            " return !!el && el.textContent.includes(x); }",
            arg=expected,
            timeout=timeout,
        )
        return page.locator(".cb-toast").inner_text()

    def sheet_page_roundtrip(self, page: Page) -> tuple[bool, str]:
        """Open the first row's page in the open sheet, then go back.

        A row opens its page and hides the overview; the way back shows the
        overview again with focus on the row it left.

        Args:
            page: The Playwright page, with a session sheet open.

        Returns:
            Whether both held, and the evidence.
        """
        overview = page.locator('.cb-sheet [data-sheet-page="overview"]')
        rows = overview.locator('.cb-sheet-list [data-action="sheet-open"]')
        if not rows.count():
            return False, "no row of the overview opens a page"
        name = rows.first.get_attribute("data-page")
        sheet_page = page.locator(f'.cb-sheet [data-sheet-page="{name}"]')
        rows.first.click()
        sheet_page.wait_for(state="visible", timeout=5000)
        opened = overview.is_hidden() and sheet_page.is_visible()
        sheet_page.locator('[data-action="sheet-back"]').click()
        overview.wait_for(state="visible", timeout=5000)
        back = sheet_page.is_hidden() and overview.is_visible()
        focused = page.evaluate(
            "(n) => document.activeElement?.dataset.action === 'sheet-open'"
            " && document.activeElement.dataset.page === n",
            name,
        )
        return opened and back and focused, (
            f"row {name!r}: page shown, overview hidden {opened}; back shows the "
            f"overview, page hidden {back}; focus on the row {focused}"
        )

    # -- the flow ---------------------------------------------------------

    def run_settings_and_boot(self, page: Page) -> None:
        """Front door -> key -> ready -> shell, then Connect OpenRouter from Settings."""
        self.step = "01-front-door"
        auth: dict[str, str] = {}

        def sign_in(route: Route) -> None:
            auth.update(parse_qsl(urlsplit(route.request.url).query))
            back = f"{auth['callback_url']}?code={CONNECT_CODE}"
            route.fulfill(status=302, headers={"Location": back})

        page.route(lambda url: url.startswith(OPENROUTER_AUTH), sign_in)
        page.goto(f"{self.base}/?shell=web")
        page.wait_for_selector('[data-door="key"]', timeout=30000)
        page.click('[data-door="key"]')
        page.wait_for_selector('[name="intervals_api_key"]', timeout=10000)
        page.click(".cb-settings__advanced summary")
        page.fill('[name="intervals_base_url"]', self.settings["intervals_base_url"])
        page.fill('[name="openrouter_base_url"]', self.settings["openrouter_base_url"])
        page.fill('[name="athlete_name"]', "E2E")
        page.fill('[name="intervals_api_key"]', self.settings["intervals_api_key"])
        page.click('.cb-door__form button[type="submit"]')

        self.step = "01-ready"
        page.wait_for_selector('[data-step="ready"]', timeout=BOOT_TIMEOUT_MS)
        page.screenshot(path=str(self.shots / "01_ready.png"))
        connected = page.get_attribute(
            '.cb-check[data-check="connected"]', "data-state"
        )
        upload = page.get_attribute('.cb-check[data-check="upload"]', "data-state")
        self.record(
            "01 front door -> key -> ready",
            connected == "ok" and upload == "warn",
            f"connected row {connected!r}, upload row {upload!r} "
            "(the fake athlete forwards to no device)",
        )
        page.click('[data-door="look"]')

        self.step = "02-boot"
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")

        # OpenRouter is connected from the app's own Settings panel;
        # the key is already in, so the round trip reboots straight into the
        # shell with no gate to fill in.
        page.click('[data-action="open-settings"]')
        page.wait_for_selector('[data-settings="connect"]', timeout=10000)
        page.check('[name="chat_consent"]')
        page.click('[data-settings="connect"]')
        # The sign-in round trip lands on the callback URL (the page's own
        # origin and path, ?code= stripped at boot); only then is the shell
        # on screen the rebooted one.
        page.wait_for_url(f"{self.base}/", timeout=30000)
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        stored = json.loads(page.evaluate("localStorage.getItem('endury.settings')"))
        exchange = self.coach.key_requests[-1] if self.coach.key_requests else {}
        digest = hashlib.sha256(str(exchange.get("code_verifier")).encode()).digest()
        pkce_ok = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == auth.get(
            "code_challenge"
        )
        self.record(
            "02a connect OpenRouter from Settings",
            all(stored.get(k) == v for k, v in self.settings.items())
            and exchange.get("code") == CONNECT_CODE
            and pkce_ok
            and "code=" not in page.url,
            f"stored {sorted(k for k, v in stored.items() if v)}, "
            f"verifier matches challenge: {pkce_ok}, url {page.url}",
        )
        page.screenshot(path=str(self.shots / "02_booted.png"))
        health = self.api(page, "health")
        self.record(
            "02 boot",
            health.get("ready") is True and health.get("coach_ready") is True,
            f"health={health}",
        )

    def run_web_flow(
        self,
        page: Page,
        race_date: str,
        goal: str = "trail_20k",
        weeks: int = 8,
        ambition: str = "basic",
    ) -> None:
        """Drive wizard -> generate -> preview -> apply -> move -> drag -> replan.

        Maintenance goals swap the race-date fill for the block-duration
        stepper (clicked from the default 8 to *weeks*); the rest of the
        lifecycle is identical.
        """
        s = self.shots
        maintenance = goal.endswith("_maintenance")
        family = (
            goal.split("_")[0] if goal.startswith(("running", "trail")) else "triathlon"
        )
        self.step = "03-wizard"
        page.click('[data-action="open-plan"]')
        page.wait_for_selector("text=Choose a plan", timeout=10000)
        page.click(f'[data-action="set-family"][data-fam="{family}"]')
        page.click(f'[data-action="set-goal"][data-goal="{goal}"]')
        page.click(f'[data-action="set-ambition"][data-amb="{ambition}"]')
        if maintenance:
            delta = 1 if weeks >= 8 else -1
            for _ in range(abs(weeks - 8)):
                page.click(f'[data-action="maint-weeks-adjust"][data-delta="{delta}"]')
                page.wait_for_timeout(120)
            filled = page.locator(".cb-maint-weeks").inner_text() == f"{weeks} wk"
        else:
            page.fill('input[data-action="a-race-date"]', race_date)
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)
            filled = page.input_value('input[data-action="a-race-date"]') == race_date
        # The fake account has no history, so every goal sport renders the
        # self-assessment questionnaire, and the CTA waits for its answers.
        hours = page.query_selector_all('[data-action="assess-hours"]')
        for field in hours:
            field.fill("4")
        for longest in page.query_selector_all('[data-action="assess-longest"]'):
            longest.fill("60")
        page.screenshot(path=str(s / "03_wizard_filled.png"))
        self.record(
            "03 wizard + self-assessment",
            filled and bool(hours),
            f"{goal} {race_date if not maintenance else f'{weeks} wk'}, "
            f"{len(hours)} sports self-assessed",
        )

        self.step = "04-generate"
        page.click('[data-action="preview-plan"]')
        # Any goal can land Experimental for its own conditions — an
        # out-of-range horizon here, say, once the wizard's own
        # next-Sunday race date pushes a couple of weeks past what the
        # scorecard sweeps. Pass the confirm honestly rather than dodging
        # it with a goal or date the gate never questions.
        page.wait_for_selector(".cbw-previewbar, .cb-confirm__title", timeout=180000)
        if page.locator(".cb-confirm__title").count():
            page.click('[data-confirm="yes"]')
            page.wait_for_selector(".cbw-previewbar", timeout=180000)
        page.wait_for_timeout(500)
        titles = page.eval_on_selector_all(
            ".cbw-daycol.cbw-preview .cb-card__title",
            "els => els.map(e => e.textContent)",
        )
        page.screenshot(path=str(s / "04_preview.png"))
        self.record(
            "04a generate preview",
            bool(titles) and all("undefined" not in t for t in titles),
            f"{len(titles)} preview cards, e.g. {titles[:3]}",
        )
        page.click(".cbw-daycol.cbw-preview .cb-card >> nth=0")
        page.wait_for_selector(".cb-detail-title", timeout=10000)
        page.wait_for_timeout(400)  # cb-pop animation starts at opacity 0
        detail = page.locator(".cb-detail-title").inner_text()
        self.record("04b preview detail modal", bool(detail), repr(detail))
        ok, evidence = self.sheet_page_roundtrip(page)
        self.record("04b2 sheet row -> page -> back", ok, evidence)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        self.step = "04-apply"
        page.click('[data-action="apply-plan"]')
        toast = self.wait_toast(page, "Plan applied", timeout=180000)
        page.wait_for_timeout(1500)
        active = self.active_plan(page).get("active_plan") or {}
        plan_id = active.get("id")
        pushed = self.plan_events(plan_id) if plan_id else {}
        page.screenshot(path=str(s / "04_applied.png"))
        self.record(
            "04c apply plan",
            bool(plan_id)
            and bool(pushed)
            and self.icu.event("coachie:store") is not None,
            f"toast={toast!r} plan={plan_id} goal={active.get('goal_type')} "
            f"{len(pushed)} events pushed, store note saved",
        )
        # The commit re-reads the weeks and the macro in parallel, and the
        # worker answers the weeks first: a row rendered before the macro
        # lands must still pick up its "Wk N" number, not keep the bare date
        # until the next reload.
        plan_start = active.get("start_date")
        unnumbered = page.eval_on_selector_all(
            ".cbw-weekrow[data-week-start]",
            "(els, m) => els.filter(e => e.dataset.weekStart >= m"
            " && !(e.querySelector('.cbw-weeksum__wk')?.textContent || '')"
            ".includes('Wk ')).length",
            arg=plan_start,
        )
        self.record(
            "04d week rows numbered after apply",
            bool(plan_start) and unnumbered == 0,
            f"{unnumbered} rows from {plan_start} without a 'Wk N' label",
        )

        self.step = "05-move-day"
        page.click('[data-action="next-week"]')
        page.wait_for_timeout(600)
        # Week status anchors to today, not the viewed week: no week at or
        # after this one may read "Done" while the plan is still ahead.
        today_monday = (
            date.today() - timedelta(days=date.today().weekday())
        ).isoformat()
        done_rows = page.eval_on_selector_all(
            ".cbw-weekrow[data-week-start]",
            "(els, m) => els.filter(e => e.dataset.weekStart >= m"
            " && e.querySelector('.cbw-weeksum')?.textContent.includes('Done')).length",
            arg=today_monday,
        )
        self.record(
            "05a week status anchored to today",
            done_rows == 0,
            f"{done_rows} 'Done' week summaries at/after {today_monday}",
        )
        # Scope the pick to the cursor week's row: several weeks render at
        # once, and an unscoped pick could land on a past, unmovable session.
        cursor_week = page.eval_on_selector(
            ".cbw-weekrow.is-cursor", "el => el.dataset.weekStart"
        )
        page.click(f'.cbw-weekrow[data-week-start="{cursor_week}"] .cb-card >> nth=0')
        page.wait_for_selector(".cb-detail-title", timeout=10000)
        # The day chips sit on the sheet's Move page, which the footer opens.
        page.click('[data-action="sheet-open"][data-page="move"]')
        page.click('[data-action="move-day"]:not([disabled]) >> nth=0')
        toast = self.wait_toast(page, "Moved to", timeout=60000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(s / "05_moved.png"))
        self.record("05b reschedule via modal", "Moved to" in toast, toast)

        self.step = "05-drag"
        # Source and target sit in the same week row, and the target is a
        # legal drop day (>= today): the app refuses earlier days. The row
        # ends after today, so it holds such a day besides the card's own;
        # on a Sunday the current week does not.
        today = date.today()
        cards = page.eval_on_selector_all(
            ".cb-card[draggable='true']",
            "els => els.map(el => ({id: el.dataset.id,"
            " day: el.closest('.cbw-daycol').dataset.date,"
            " week: el.closest('.cbw-weekrow').dataset.weekStart}))",
        )
        src = next(
            c
            for c in cards
            if date.fromisoformat(c["week"]) + timedelta(days=6) > today
        )
        days = page.eval_on_selector_all(
            f'.cbw-weekrow[data-week-start="{src["week"]}"] .cbw-daycol',
            "els => els.map(e => e.dataset.date)",
        )
        today_iso = today.isoformat()
        target = next(d for d in days if d != src["day"] and d >= today_iso)
        info = page.evaluate(
            DRAG_JS,
            [f".cb-card[data-id='{src['id']}']", f".cbw-daycol[data-date='{target}']"],
        )
        # A session of the same sport on that day asks: swap, or keep both.
        keep_both = page.locator('[data-confirm="no"]')
        if keep_both.count():
            keep_both.click()
        page.wait_for_function(
            "([sel, id]) => [...document.querySelectorAll(sel)].some(e => e.dataset.id === id)",
            arg=[f".cbw-daycol[data-date='{target}'] .cb-card", src["id"]],
            timeout=60000,
        )
        page.screenshot(path=str(s / "05_dragged.png"))
        self.record("05c drag-to-reschedule", True, f"{info} -> {target}")

        self.step = "06-replan"
        rev_before = (self.active_plan(page).get("macro_plan") or {}).get("revision")
        page.click('[data-action="replan"]')
        page.wait_for_selector('[data-confirm="yes"]', timeout=10000)
        page.click('[data-confirm="yes"]')
        toast = self.wait_toast(page, "Plan updated", timeout=180000)
        page.wait_for_timeout(1000)
        rev_after = (self.active_plan(page).get("macro_plan") or {}).get("revision")
        page.screenshot(path=str(s / "06_replanned.png"))
        self.record(
            "06 replan",
            rev_after != rev_before,
            f"revision {rev_before}->{rev_after} toast={toast!r}",
        )

        self.step = "06-infinite-scroll"
        before = page.locator(".cbw-weekrow[data-week-start]").count()
        page.evaluate(ATHLETE_MOVES_JS)
        page.evaluate("document.querySelector('#cbw-scroller').scrollTo(0, 1e6)")
        page.wait_for_function(
            "(n) => document.querySelectorAll('.cbw-weekrow[data-week-start]').length > n",
            arg=before,
            timeout=30000,
        )
        after = page.locator(".cbw-weekrow[data-week-start]").count()
        new_errors = [e for e in self.console_errors if e["step"] == self.step]
        self.record(
            "06b infinite scroll",
            after >= before + 1 and not new_errors,
            f"{before} -> {after} week rows, {len(new_errors)} console errors",
        )

    def run_reload(self, page: Page) -> None:
        """Reload: the store note brings back the same plan; boot writes nothing."""
        self.step = "07-reload"
        banner_hidden = page.locator("#cb-banner").is_hidden()
        before = self.plan_state(page)
        writes = self.writes()
        page.reload()
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.wait_for_timeout(1500)
        after = self.plan_state(page)
        page.screenshot(path=str(self.shots / "07_reloaded.png"))
        self.record(
            "07 reload -> same state",
            banner_hidden and after == before and self.writes() == writes,
            f"nothing unsaved before: {banner_hidden}; plan equal: {after == before}; "
            f"{self.writes() - writes} writes during boot",
        )

    def run_chat_apply(self, page: Page) -> None:
        """Chat -> Apply card -> Apply: the pause reaches the calendar."""
        self.step = "08-chat"
        plan_id = int(self.active_plan(page)["active_plan"]["id"])
        through = date.fromisoformat(self.coach.arguments["through_date"])
        window = {
            (date.today() + timedelta(days=d)).isoformat()
            for d in range((through - date.today()).days + 1)
        }
        before = self.plan_events(plan_id)
        page.fill("#cbw-draft", f"{PAUSE_TRIGGER}, can't train for a few days.")
        page.click('[data-action="send"]')
        page.wait_for_selector(
            '.cb-proposal [data-action="apply-proposal"]:not([disabled])',
            timeout=180000,
        )
        title = page.locator(".cb-proposal__title").last.inner_text()
        page.screenshot(path=str(self.shots / "08_card.png"))
        self.record(
            "08a chat -> Apply card",
            self.plan_events(plan_id) == before,
            f"card {title!r}; the calendar is untouched until Apply",
        )

        self.step = "08-apply"
        page.click('.cb-proposal [data-action="apply-proposal"]')
        page.wait_for_selector(".cb-proposal--applied", timeout=180000)
        # plan_changed re-reads the calendar: every card in the window skipped.
        page.wait_for_function(SKIPPED_IN_WINDOW_JS, arg=sorted(window), timeout=60000)
        after = self.plan_events(plan_id)

        def in_window(events: dict[str, dict]) -> int:
            return sum(
                1 for e in events.values() if e["start_date_local"][:10] in window
            )

        page.screenshot(path=str(self.shots / "08_applied.png"))
        self.record(
            "08b Apply -> calendar changed",
            after != before and in_window(after) < in_window(before),
            f"events in the paused window {in_window(before)} -> {in_window(after)}, "
            f"its cards all read skipped; plan events {len(before)} -> {len(after)}",
        )

    def run_chat_chart(self, page: Page) -> None:
        """Chat -> analysis in the sandbox worker -> a chart that survives a reload."""
        self.step = "08-chart"
        page.fill("#cbw-draft", CHART_TRIGGER)
        page.click('[data-action="send"]')
        # The first analysis also starts the sandbox: Pyodide and numpy.
        page.wait_for_selector(".cb-msg .cb-chart svg rect", timeout=180000)
        title = page.locator(".cb-chart__title").last.inner_text()
        bars = page.locator(".cb-msg .cb-chart").last.locator("rect").count()
        page.screenshot(path=str(self.shots / "08_chart.png"))
        self.record(
            "08c chat -> chart",
            title == "Activities by sport" and bars == 3,
            f"chart {title!r} with {bars} bars, drawn from the sandbox's run",
        )

        self.step = "08-chart-reload"
        page.reload()
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.wait_for_selector(".cb-msg .cb-chart svg rect", timeout=60000)
        kept = page.locator(".cb-chart__title").last.inner_text()
        self.record(
            "08d reload -> chart kept",
            kept == title,
            f"after a reload the reply still carries {kept!r}",
        )

    def run_chat_ideas(self, page: Page) -> None:
        """The ideas sheet: example questions and the athlete's own, one tap to the draft."""
        self.step = "08-ideas"
        page.click('[data-action="coach-ideas"]')
        page.wait_for_selector(".cb-modal .cb-idea")
        ideas = page.locator(".cb-modal .cb-idea")
        texts = [ideas.nth(i).inner_text() for i in range(ideas.count())]
        page.screenshot(path=str(self.shots / "08_ideas.png"))
        ideas.first.click()
        drafted = page.input_value("#cbw-draft")
        page.fill("#cbw-draft", "")
        self.record(
            "08e ideas -> draft",
            CHART_TRIGGER in texts
            and drafted == texts[0]
            and not page.locator(".cb-modal").count(),
            f"{len(texts)} questions, the athlete's own among them; the first went to the draft",
        )

    def run_sheet_brief(self, page: Page) -> None:
        """A brief from a session sheet: no conversation in, none kept, none shown."""
        self.step = "08-brief"
        bubbles = page.locator("#cbw-msgs .cb-msg").count()
        kept = self.api(page, "conversationRecent", 50)["messages"]
        served = len(self.coach.requests)
        page.click(".cbw-weekrow[data-week-start] .cb-card >> nth=-1")
        # The brief buttons sit on the sheet's Coach's brief page.
        page.click('[data-action="sheet-open"][data-page="coach"]')
        page.click('[data-action="activity-brief"][data-depth="quick"]')
        page.wait_for_selector(
            '[data-action="activity-brief"]:has-text("Redo quick brief")',
            timeout=180000,
        )
        # The row on the overview now carries the brief, not the offer to ask.
        answer = page.locator('[data-summary="brief"]').text_content() or ""
        page.screenshot(path=str(self.shots / "08_brief.png"))
        roles = [m["role"] for m in self.coach.requests[served]["messages"]]
        self.record(
            "08f brief -> outside the chat",
            roles.count("user") == 1
            and "assistant" not in roles
            and self.api(page, "conversationRecent", 50)["messages"] == kept
            and page.locator("#cbw-msgs .cb-msg").count() == bubbles
            and bool(answer.strip())
            and "Ask for a quick or a full brief" not in answer,
            f"the model saw {roles.count('user')} user message; the chat kept "
            f"{len(kept)} rows and {bubbles} bubbles; the brief row reads "
            f"{answer.strip()[:40]!r}",
        )
        page.keyboard.press("Escape")

    def run_new_context(self, browser: Browser, expected: dict) -> None:
        """A fresh context hydrates the identical plan from intervals.icu.

        Args:
            browser: The browser to open the context in.
            expected: The writer page's :meth:`plan_state`.
        """
        self.step = "09-new-context"
        ctx = self.new_context(browser, 1440, 900)
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=web")
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.wait_for_timeout(1000)
        got = self.plan_state(page)
        plan = got["active"]["active_plan"]
        page.screenshot(path=str(self.shots / "09_new_context.png"))
        self.record(
            "09a new context -> same plan",
            got == expected,
            f"plan {plan['id']} with {plan['sessions_count']} sessions, "
            f"equal: {got == expected}",
        )
        # The step-8 chat also comes back from intervals.icu, not the device.
        page.wait_for_selector(
            f'.cb-msg--user:has-text("{PAUSE_TRIGGER}")', timeout=15000
        )
        self.record(
            "09b new context -> chat restored",
            True,
            f"athlete message {PAUSE_TRIGGER!r} present in the restored thread",
        )
        ctx.close()

    def run_second_tab(self, ctx: BrowserContext) -> None:
        """A second tab in the writer's context is read-only and refuses writes."""
        self.step = "10-second-tab"
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=web")
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.wait_for_selector(".cb-banner--info", timeout=30000)
        banner = page.locator("#cb-banner").inner_text()
        writes = self.writes()
        out = page.evaluate(
            API_JS, [self.api_url, "putMemory", ["from the second tab"]]
        )
        page.screenshot(path=str(self.shots / "10_second_tab.png"))
        self.record(
            "10 second tab read-only",
            "another tab" in banner and "error" in out and self.writes() == writes,
            f"banner={banner.splitlines()[0]!r} write refused: {out.get('error')!r}",
        )
        page.close()

    def run_mobile_smoke(self, browser: Browser) -> None:
        """The mobile shell renders the plan's agenda feed and scrolls on."""
        self.step = "11-mobile"
        ctx = self.new_context(browser, 390, 820)
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=mobile")
        self.wait_booted(page, ".cbm-daysec:not(.is-skeleton)")
        page.wait_for_timeout(1000)
        n = page.locator(".cbm-daysec:not(.is-skeleton)").count()
        race = page.locator("#cbm-race").inner_text()
        page.screenshot(path=str(self.shots / "11_mobile.png"))
        self.record(
            "11a mobile smoke",
            n >= 7 and n % 7 == 0 and race not in ("Plan", "Create a plan"),
            f"{n} agenda day sections, race pill {race!r}",
        )

        page.evaluate(ATHLETE_MOVES_JS)
        page.evaluate("document.querySelector('#cbm-feed').scrollTo(0, 1e6)")
        page.wait_for_function(
            "(n) => document.querySelectorAll('.cbm-daysec:not(.is-skeleton)').length > n",
            arg=n,
            timeout=30000,
        )
        after = page.locator(".cbm-daysec:not(.is-skeleton)").count()
        page.screenshot(path=str(self.shots / "11_mobile_scrolled.png"))
        self.record(
            "11b mobile infinite scroll", after > n, f"{n} -> {after} day sections"
        )
        ctx.close()

    def landed(self, page: Page) -> tuple[bool, dict]:
        """Whether the phone's calendar shows today, and what it read."""
        read = page.evaluate(LANDED_JS)
        return (
            read["offset"] is not None
            and abs(read["offset"]) <= 2
            and read["tag"] == read["today"]
            and read["title"] == "This Week",
            read,
        )

    def wait_landed(self, page: Page) -> tuple[bool, dict]:
        """Give a landing its time to finish, then read it."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        with contextlib.suppress(PlaywrightTimeoutError):
            page.wait_for_function(
                f"() => {{ const r = ({LANDED_JS})();"
                " return r.offset !== null && Math.abs(r.offset) <= 2 && r.tag === r.today; }",
                timeout=30000,
            )
        return self.landed(page)

    def open_phone(self, ctx: BrowserContext) -> Page:
        """Boot the phone shell in a new page of ``ctx``, as a fresh open does."""
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=mobile")
        self.wait_booted(page, ".cbm-daysec:not(.is-skeleton)")
        return page

    def run_landing(self, browser: Browser) -> None:
        """The calendar shows today: every open, every return, every new day."""
        self.step = "15-landing"
        ctx = self.new_context(browser, 390, 820)
        noon = datetime.combine(date.today(), time(12))
        ctx.clock.install(time=noon)
        # What iOS Safari does: no scroll anchoring, and a height change above
        # today just as the weeks arrive.
        ctx.add_init_script(LATE_SHIFT_JS)
        page = self.open_phone(ctx)
        page.wait_for_function("() => window.__shifted === true", timeout=30000)
        ok, read = self.wait_landed(page)
        self.record("15a first open, late layout shift", ok, str(read))

        page.mouse.move(195, 500)
        for _ in range(16):
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(80)
        page.wait_for_timeout(500)
        away = page.evaluate(LANDED_JS)
        page.close()
        ctx.clock.set_system_time(noon + timedelta(minutes=2))
        page = self.open_phone(ctx)
        page.wait_for_timeout(1500)
        back = page.evaluate(LANDED_JS)
        self.record(
            "15b reopen within minutes keeps the place",
            away["title"] != "This Week" and back["title"] == away["title"],
            f"left on {away['title']!r}, back on {back['title']!r}",
        )

        page.close()
        ctx.clock.set_system_time(noon + timedelta(hours=2))
        page = self.open_phone(ctx)
        ok, read = self.wait_landed(page)
        self.record("15c reopen later the same day lands on today", ok, str(read))

        page.mouse.move(195, 500)
        for _ in range(16):
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(80)
        page.wait_for_timeout(500)
        tomorrow = datetime.combine(date.today() + timedelta(days=1), time(0, 1))
        ctx.clock.set_system_time(tomorrow)
        ctx.clock.run_for(61000)
        ok, read = self.wait_landed(page)
        self.record("15d open through midnight, no events", ok, str(read))

        monday = date.today() + timedelta(days=7 - date.today().weekday())
        ctx.clock.set_system_time(datetime.combine(monday, time(7)))
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        ok, read = self.wait_landed(page)
        self.record(
            "15e back next Monday",
            ok and read["today"] == monday.isoformat(),
            str(read),
        )
        ctx.close()

        ctx = self.new_context(browser, 1440, 900)
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=web")
        self.wait_booted(page, ".cbw-daycol")
        page.wait_for_selector(".cbw-weekrow.is-cursor", timeout=30000)
        page.wait_for_timeout(2000)
        first = page.eval_on_selector(
            ".cbw-weekrow.is-cursor", "el => el.dataset.weekStart"
        )
        page.click('[data-action="set-density"][data-density="compact"]')
        page.click('[data-action="next-week"]')
        page.wait_for_timeout(1200)
        cursor = page.eval_on_selector(
            ".cbw-weekrow.is-cursor", "el => el.dataset.weekStart"
        )
        want = (date.fromisoformat(first) + timedelta(days=7)).isoformat()
        self.record(
            "15f Next in compact moves the cursor one week",
            cursor == want,
            f"{first} -> {cursor}, wanted {want}",
        )
        ctx.close()

    def run_demo(self, browser: Browser) -> None:
        """The demo door boots a sample athlete without touching the fake intervals.icu."""
        self.step = "12-demo"
        ctx = self.new_context(browser, 1440, 900, settings=False)
        page = ctx.new_page()
        self.wire(page)
        calls_before = len(self.icu.calls)
        page.goto(f"{self.base}/?shell=web")
        page.wait_for_selector('[data-door="demo"]', timeout=30000)
        page.click('[data-door="demo"]')
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.wait_for_selector(".cb-banner--info", timeout=30000)
        # The banner mounts at boot; the done cards render once the sample
        # athlete's activities arrive.
        page.wait_for_selector(".cb-badge--done", state="attached", timeout=30000)
        banner = page.locator(".cb-banner--info").inner_text()
        done_cards = page.locator(".cb-badge--done").count()
        page.screenshot(path=str(self.shots / "12_demo.png"))
        self.record(
            "12 demo athlete",
            "Demo" in banner and done_cards > 0 and len(self.icu.calls) == calls_before,
            f"banner={banner.splitlines()[0]!r}, {done_cards} done session cards, "
            f"{len(self.icu.calls) - calls_before} fake intervals.icu calls during the step",
        )
        ctx.close()

    def run_experimental_confirm(self, browser: Browser, race_date: str) -> None:
        """An Experimental goal's confirm dialog, honestly, through the wizard.

        A fresh context (no active plan) builds a trail_20k plan for an
        athlete whose recent volume is under what the scorecard checks, so
        the plan is Experimental for its conditions:
        cancelling the ``experimental_plan`` dialog leaves the editor open
        with the athlete's answers intact and no error toast, confirming it
        retries with ``accept_experimental`` and the preview names the
        maturity caveat.
        """
        self.step = "13-experimental"
        ctx = self.new_context(browser, 1440, 900)
        page = ctx.new_page()
        self.wire(page)
        page.goto(f"{self.base}/?shell=web")
        self.wait_booted(page, ".cbw-weekrow[data-week-start]")
        page.click('[data-action="open-plan"]')
        page.wait_for_selector(
            '[data-action="set-family"][data-fam="trail"]', timeout=10000
        )
        page.click('[data-action="set-family"][data-fam="trail"]')
        page.wait_for_selector(
            '[data-action="set-goal"][data-goal="trail_20k"]', timeout=10000
        )
        page.click('[data-action="set-goal"][data-goal="trail_20k"]')
        page.fill('input[data-action="a-race-date"]', race_date)
        page.keyboard.press("Tab")
        page.wait_for_timeout(200)
        for field in page.query_selector_all('[data-action="assess-hours"]'):
            field.fill("1")
        for field in page.query_selector_all('[data-action="assess-longest"]'):
            field.fill("60")

        page.click('[data-action="preview-plan"]')
        page.wait_for_selector(".cb-confirm__title", timeout=30000)
        title = page.locator(".cb-confirm__title").inner_text()
        page.screenshot(path=str(self.shots / "13_experimental_dialog.png"))
        page.click('[data-confirm="no"]')
        page.wait_for_selector('[data-action="preview-plan"]', timeout=10000)
        page.wait_for_timeout(600)
        editor_date = page.input_value('input[data-action="a-race-date"]')
        failed_toast = page.locator(
            ".cb-toast:has-text('Plan generation failed')"
        ).count()
        self.record(
            "13a experimental dialog cancel -> editor stays, no error toast",
            "experimental" in title.lower()
            and editor_date == race_date
            and failed_toast == 0,
            f"title={title!r}, race date kept={editor_date == race_date!r}, "
            f"failed toast present: {bool(failed_toast)}",
        )

        page.click('[data-action="preview-plan"]')
        page.wait_for_selector(".cb-confirm__title", timeout=30000)
        page.click('[data-confirm="yes"]')
        page.wait_for_selector(".cbw-previewbar", timeout=180000)
        caveat = page.locator(".cbw-pv-warning").first.inner_text()
        page.screenshot(path=str(self.shots / "13_experimental_preview.png"))
        self.record(
            "13b experimental confirm -> preview names the caveat",
            bool(caveat),
            caveat,
        )
        ctx.close()

    def run_finished_block(self, browser: Browser, ended: FakeIntervals) -> None:
        """A block that ended last Sunday: its ending, then the next block.

        The plan card shows the ending instead of a replan; "Start a new
        plan" opens the wizard on the same block, and applying it replaces
        the finished plan without a replace confirm (nothing of it is left
        to lose) while every workout the finished block put on the calendar
        (all of them in the past) stays there.
        """
        self.step = "14-finished-block"
        server = CorsJsonServer(ended.handle).start()
        try:
            ctx = self.new_context(browser, 1440, 900, settings=False)
            stored = json.dumps(
                json.dumps(
                    {**self.settings, "intervals_base_url": f"{server.url}/api/v1"}
                )
            )
            ctx.add_init_script(f"localStorage.setItem('endury.settings', {stored});")
            page = ctx.new_page()
            self.wire(page)
            page.goto(f"{self.base}/?shell=web")
            self.wait_booted(page, ".cbw-weekrow[data-week-start]")
            page.wait_for_selector('[data-action="new-plan"]', timeout=30000)
            card = page.locator(
                '.cb-insight:has([data-action="new-plan"])'
            ).inner_text()
            old = self.active_plan(page)["active_plan"]
            prefix = f"coachie:{old['id']}:"
            before = {k: v for k, v in ended.events.items() if k.startswith(prefix)}
            # The weeks after the block are no weeks of it.
            numbered = page.eval_on_selector_all(
                ".cbw-weekrow[data-week-start]",
                "(els, end) => els.filter(e => e.dataset.weekStart > end"
                " && (e.querySelector('.cbw-weeksum__wk')?.textContent || '')"
                ".includes('Wk ')).length",
                arg=old["goal_date"],
            )
            page.screenshot(path=str(self.shots / "14_block_finished.png"))
            self.record(
                "14a a finished block shows its ending",
                "Block finished" in card
                and old["goal_date"] < date.today().isoformat()
                and numbered == 0,
                f"{' / '.join(card.splitlines()[:3])!r}, block ended {old['goal_date']}, "
                f"{len(before)} of its workouts on the calendar, "
                f"{numbered} later weeks numbered as block weeks",
            )

            page.click('[data-action="new-plan"]')
            page.wait_for_selector("text=Choose a plan", timeout=10000)
            for field in page.query_selector_all('[data-action="assess-hours"]'):
                field.fill("4")
            for field in page.query_selector_all('[data-action="assess-longest"]'):
                field.fill("60")
            page.click('[data-action="preview-plan"]')
            page.wait_for_selector(
                ".cbw-previewbar, .cb-confirm__title", timeout=180000
            )
            if page.locator(".cb-confirm__title").count():
                page.click('[data-confirm="yes"]')
                page.wait_for_selector(".cbw-previewbar", timeout=180000)
            page.click('[data-action="apply-plan"]')
            page.wait_for_selector(
                ".cb-confirm__title, .cb-toast:has-text('Plan applied')",
                timeout=180000,
            )
            asked = page.locator(".cb-confirm__title").count()
            if asked:
                page.click('[data-confirm="yes"]')
            toast = self.wait_toast(page, "Plan applied", timeout=180000)
            page.wait_for_timeout(1000)
            new = self.active_plan(page)["active_plan"]
            kept = {k: v for k, v in ended.events.items() if k.startswith(prefix)}
            pushed = sum(
                1 for k in ended.events if k.startswith(f"coachie:{new['id']}:")
            )
            ending = page.locator('[data-action="new-plan"]').count()
            page.screenshot(path=str(self.shots / "14_next_block.png"))
            self.record(
                "14b Start a new plan -> the next block; the finished one stays",
                new["id"] != old["id"]
                and new["goal_date"] > date.today().isoformat()
                and kept == before
                and pushed > 0
                and ending == 0
                and not asked,
                f"toast={toast!r}, replace confirm shown={bool(asked)}, "
                f"next block {new['goal_type']} to {new['goal_date']} "
                f"({pushed} workouts pushed), finished block's workouts "
                f"{len(kept)}/{len(before)} kept",
            )
            ctx.close()
        finally:
            server.stop()

    def summary(self) -> int:
        """Print the verdict block; return the process exit code."""
        print("=" * 70)
        print(f"CONSOLE ERRORS: {len(self.console_errors)}")
        for e in self.console_errors:
            print(f"  [{e['step']}] {e['text']}")
        print(f"PAGE ERRORS: {len(self.page_errors)}")
        for e in self.page_errors:
            print(f"  [{e['step']}] {e['text']}")
        n_fail = sum(1 for _, ok, _ in self.results if not ok)
        print(f"RESULT: {len(self.results) - n_fail}/{len(self.results)} steps passed")
        print(f"Screenshots: {self.shots}")
        return 1 if (n_fail or self.page_errors) else 0


def _launch(p: Playwright, channel: str | None) -> Browser:
    """Launch Chromium; without Playwright's own build, the installed Chrome."""
    from playwright.sync_api import Error as PlaywrightError

    try:
        return p.chromium.launch(channel=channel)
    except PlaywrightError as exc:
        if channel or "Executable doesn't exist" not in str(exc):
            raise
        print("Playwright's Chromium is not installed; using Google Chrome.")
        return p.chromium.launch(channel="chrome")


def main() -> int:
    """Build the site, start it and the fakes, run the lifecycle, tear down."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--goal",
        default="trail_20k",
        help="wizard goal to drive; *_maintenance goals use the "
        "block-duration stepper (default: %(default)s)",
    )
    parser.add_argument(
        "--ambition",
        default="basic",
        choices=["basic", "moderate", "competitive"],
        help="wizard ambition tier to click (default: %(default)s)",
    )
    parser.add_argument(
        "--race-weeks",
        type=int,
        default=16,
        help="weeks until the wizard A-race date; must meet the goal's "
        "minimum horizon, e.g. 20+ for ironman (default: %(default)s)",
    )
    parser.add_argument(
        "--weeks",
        type=int,
        default=8,
        help="maintenance block duration to step to, 4-16 "
        "(default: %(default)s; ignored for race goals)",
    )
    parser.add_argument(
        "--port", type=int, default=8126, help="site port (default: %(default)s)"
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="browser channel, e.g. chrome (default: Playwright's Chromium, "
        "else the installed Chrome)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output dir for screenshots (default: fresh temp dir)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="also write the fake calendar and the coach requests to the "
        "output dir as JSON",
    )
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="endury_e2e_"))
    out.mkdir(parents=True, exist_ok=True)

    build_site.main()
    build_id = json.loads((build_site.SITE_DIR / "version.json").read_text())["build"]
    site = BurstHTTPServer(
        ("127.0.0.1", args.port),
        partial(QuietSiteHandler, directory=str(build_site.SITE_DIR)),
    )
    threading.Thread(target=site.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{args.port}"

    # Into the plan's first week whatever the weekday: from Wednesday on a
    # new plan starts next Monday, and a pause must reach its sessions.
    through = date.today() + timedelta(days=10)
    icu = FakeIntervals(athlete=triathlete())
    coach = FakeOpenRouter(
        trigger=PAUSE_TRIGGER,
        tool="pause_training",
        arguments={"through_date": through.isoformat()},
        more=[Script(CHART_TRIGGER, "run_analysis", {"code": CHART_CODE}, CHART_REPLY)],
    )
    icu_server = CorsJsonServer(icu.handle).start()
    coach_server = CorsJsonServer(coach.handle).start()
    settings = {
        "intervals_api_key": "e2e-intervals-key",
        "openrouter_api_key": coach.key,
        "intervals_base_url": f"{icu_server.url}/api/v1",
        "openrouter_base_url": f"{coach_server.url}/api/v1",
    }
    print(
        f"Site {base}  fake intervals.icu {icu_server.url}  fake OpenRouter {coach_server.url}"
    )

    race_date = date.today() + timedelta(weeks=args.race_weeks)
    race_date += timedelta(days=(6 - race_date.weekday()) % 7)  # next Sunday
    # 12 weeks: inside trail_20k's checked range (12-20), at its
    # minimum — the plan is Experimental for the athlete's volume alone, not
    # also for a short-notice join.
    exp_race_date = date.today() + timedelta(weeks=12)
    exp_race_date += timedelta(days=(6 - exp_race_date.weekday()) % 7)  # next Sunday
    # Seeded before Playwright starts: its sync API runs an event loop of its own.
    ended = finished_block_account()
    harness = Harness(base, build_id, settings, icu, coach, out)
    try:
        with sync_playwright() as p:
            browser = _launch(p, args.channel)
            ctx = harness.new_context(browser, 1440, 900, settings=False)
            page = ctx.new_page()
            harness.wire(page)
            harness.run_settings_and_boot(page)
            harness.run_web_flow(
                page,
                race_date.isoformat(),
                goal=args.goal,
                weeks=args.weeks,
                ambition=args.ambition,
            )
            harness.run_reload(page)
            harness.run_chat_apply(page)
            harness.run_chat_chart(page)
            harness.run_chat_ideas(page)
            harness.run_sheet_brief(page)
            expected = harness.plan_state(page)
            harness.run_new_context(browser, expected)
            harness.run_second_tab(ctx)
            ctx.close()
            harness.run_mobile_smoke(browser)
            harness.run_landing(browser)
            harness.run_demo(browser)
            harness.run_experimental_confirm(browser, exp_race_date.isoformat())
            harness.run_finished_block(browser, ended)
            browser.close()
    except Exception as exc:
        harness.record(harness.step, False, f"{type(exc).__name__}: {exc}")
    finally:
        site.shutdown()
        site.server_close()
        icu_server.stop()
        coach_server.stop()
        if args.keep:
            (out / "intervals_events.json").write_text(json.dumps(icu.events, indent=2))
            (out / "coach_requests.json").write_text(
                json.dumps(coach.requests, indent=2)
            )
    return harness.summary()


if __name__ == "__main__":
    sys.exit(main())
