/**
 * Chat panel in a real browser: the receipt, the live region, the in-progress
 * shimmer, and the dropped-stream message.
 *
 * BOTH the chat stream and `/run` are stubbed (Playwright route interception), so
 * this suite tests the panel and depends on no language model at all.
 *
 * `/run` used to be real and only patched to add `sources`. That made the suite
 * wait on the run's *optional* card call: `run_service` asks a model to write the
 * result card and falls back to a labelled template when the call fails, so the
 * HTTP response does not return until the provider answers or times out --
 * `TIMEOUT_S` is 90 s, and a fallback chain multiplies it. Measured here: 33 s
 * on a good call, 119 s when the provider timed out, and 42-160 s across a run
 * of them. `route.fetch()` has its own 30 s default, so the suite died on
 * "route.fetch: Timeout 30000ms exceeded" whenever the model was slower than
 * that -- which is most of the time on a free tier, and it is why this suite
 * looked like it hung.
 *
 * The fix is the one the header always claimed: mock the run too. Nothing here
 * is about whether a model can write a card -- the chat stream was already
 * mocked, so the assertions never covered that -- and the run path itself is
 * verified server-side by backend/tests/test_run.py.
 *
 * Needs the dev server on :5173 and the backend on :8000. Exits 2 if either is
 * missing, so a missing server cannot read as a pass.
 */
import { readFileSync } from 'node:fs'

import { BASE, LAUNCH_ARGS, loadPlaywright, makeChecker, requireServers, settleViewer, watchErrors } from './_verify-common.mjs'

const { chromium } = loadPlaywright()
await requireServers()
const { check, finish } = makeChecker()

const sse = (events) => events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
const STREAM_HEADERS = { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' }

const card = (over = {}) => ({
  kind: 'run',
  title: 'The buffer still overflows',
  eyebrow: 'Sandbox run',
  sentence: 'Runoff exceeds storage.',
  l1: 'What the run shows',
  cause: 'Wet area is large.',
  l2: 'What to do',
  todo: 'Add a retention basin.',
  stats: [{ l: 'Wet area', v: '11.4 ha', ci: null }],
  confidence: { level: 'Medium', pct: 60, note: 'From the ledger.' },
  caveats: ['Screening estimate.'],
  followups: [],
  source: 'model',
  grounding: { ok: true, ungrounded: [], revised: false },
  ...over,
})

const steps = [
  { event: 'run_started', run_id: 'chat-1' },
  { event: 'step_started', index: 0, title: 'Read the run', desc: 'd', tool: 'facts' },
  { event: 'step_finished', index: 0, title: 'Read the run', desc: 'd', tool: 'facts', ms: 3, result: 'ok' },
]
const done = { event: 'done', status: 'done', ms: 10 }

/** How long the stubbed run is held open, so its wait affordances are visible. */
const RUN_DELAY_MS = 1200

const browser = await chromium.launch({ headless: true, args: LAUNCH_ARGS })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = watchErrors(page)

// ---- routes ----------------------------------------------------------------
/**
 * The `/run` result, read from a committed fixture.
 *
 * `scripts/fixtures/run-result.json` is a real response from a 50 mm baseline
 * run on this scene, captured once and committed, with `runId` and the overlay
 * URLs pinned so the receipt assertions are stable. Using the actual response
 * rather than a hand-built object matters: `StatsPanel` reads `stats.delta
 * .unstoredM3` and a dozen siblings, and a fixture that omits one crashes the
 * panel into its error boundary instead of failing the check that cares.
 *
 * The overlay PNGs it names do not exist. That is deliberate -- this suite
 * asserts on the receipt text, and the overlays are verified against a real run
 * by verify-3d.
 */
const runResult = JSON.parse(
  readFileSync(new URL('./fixtures/run-result.json', import.meta.url), 'utf8'),
)

await page.route('**/api/scenes/*/run', async (route) => {
  // Held briefly so the run-wait affordances below are actually observable. A
  // real run took tens of seconds, so the shimmer was easy to catch; an instant
  // stub would flash and vanish, and the check would report a false failure.
  await new Promise((r) => setTimeout(r, RUN_DELAY_MS))
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(runResult),
  })
})

/** Queue of bodies the next chat questions will receive, in order. */
const queue = []
await page.route('**/api/scenes/*/chat/stream', async (route) => {
  const next = queue.shift()
  if (next.delayMs) await new Promise((r) => setTimeout(r, next.delayMs))
  await route.fulfill({ status: 200, headers: STREAM_HEADERS, body: next.body })
})

await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await settleViewer(page)

const input = page.getByPlaceholder(/Ask about the run/)
const ask = async (text) => {
  await input.fill(text)
  await input.press('Enter')
}
const status = page.getByRole('status').filter({ hasNotText: 'Initialising' })

// A real run, so the chat has a run to be asked about. The click also gives the
// one window in which the run wait is on screen, so the run-wait affordances are
// checked here rather than in a separate pass.
await page.getByText('Run Simulation').first().click()
const shimmerDuringRun = page.locator('.hud-shimmer').first()
await shimmerDuringRun.waitFor({ state: 'visible', timeout: 6000 }).catch(() => undefined)
check('the run wait shimmers rather than sitting on a static label',
  await shimmerDuringRun.isVisible().catch(() => false),
  (await shimmerDuringRun.innerText().catch(() => '')).slice(0, 60))
check('the run button carries a spinner, since a shimmer would fight its fill',
  await page.locator('.hud-spinner').first().isVisible().catch(() => false))
await page.waitForResponse((r) => r.url().endsWith('/run') && r.request().method() === 'POST', { timeout: 90000 })
await page.waitForFunction(() => !document.body.innerText.includes('Running…'), null, { timeout: 90000 })
check('the run affordances are gone once the run finishes',
  (await page.locator('.hud-spinner').count()) === 0)

// ---- 1. a complete run card: shimmer while working, receipt after ----------
queue.push({
  delayMs: 1800,
  body: sse([...steps, { event: 'answer', answer: card() }, done]),
})
await ask('Is this better than the baseline?')

const shimmer = page.locator('.hud-shimmer').first()
await shimmer.waitFor({ state: 'visible', timeout: 5000 }).catch(() => undefined)
check('an in-progress label carries the shimmer class while the answer is pending',
  await shimmer.isVisible().catch(() => false))

await page.getByText('The buffer still overflows').first().waitFor({ timeout: 15000 })
check('the shimmer is gone once the answer lands',
  (await page.locator('.hud-shimmer').count()) === 0)

// A run now posts its own card, and that card carries a receipt -- so counting
// `details` elements is no longer a way to tell "run answer" from "general
// answer". The count simply has to rise when a run card appears.
const receiptsAfterRun = await page.locator('details').count()
check('a run card carries a provenance receipt',
  receiptsAfterRun >= 1, `found ${receiptsAfterRun}`)
const receipt = page.locator('details').last()
check('the receipt is collapsed by default', (await receipt.getAttribute('open')) === null)
check('the collapsed receipt names the run and the rainfall',
  /BASED ON · RUN [0-9A-F]{8} · \d+ MM/i.test(await receipt.locator('summary').innerText()),
  (await receipt.locator('summary').innerText()).trim())

await receipt.locator('summary').click()
const receiptText = await receipt.innerText()
check('expanding it shows the terrain and land cover the run used',
  receiptText.includes('open3dhk-tiles') && receiptText.includes('esa-worldcover'))
check('and the screening status travels with it', /Status/.test(receiptText))

// ---- 2. the live region ----------------------------------------------------
const announced = await status.allInnerTexts()
check('a live region announces the finished answer',
  announced.some((t) => t.includes('The buffer still overflows') && t.includes('Runoff exceeds storage')),
  JSON.stringify(announced))
check('the live region is polite, not assertive',
  (await page.locator('[role="status"][aria-live="polite"]').count()) >= 1)

// ---- 3. a general answer has no receipt ------------------------------------
queue.push({
  body: sse([...steps, { event: 'answer', answer: card({ kind: 'general', title: 'Out of scope', sentence: 'The sandbox cannot know.', cause: null, todo: null, stats: [] }) }, done]),
})
await ask('What will this cost?')
await page.getByText('Out of scope').first().waitFor({ timeout: 15000 })
// The run card's receipt is still on screen, so the assertion is that the
// general answer added none of its own -- the count is unchanged from before
// this question was asked.
check('a general answer adds no receipt of its own (it did not use the run)',
  (await page.locator('details').count()) === receiptsAfterRun,
  `${await page.locator('details').count()} vs ${receiptsAfterRun} before`)

// ---- 4. a stream cut before done is reported, and the panel recovers -------
queue.push({ body: sse(steps) }) // no answer, no done
await ask('And at double the rain?')
await page.getByText(/connection closed before the answer finished/i).first().waitFor({ timeout: 15000 })
check('a stream that ends without done tells the user', true)
check('and the input is usable again afterwards', await input.isEnabled())
check('and no steps are left ticking', (await page.locator('.hud-shimmer').count()) === 0)

check('no unexpected console errors', errors.length === 0, errors.slice(0, 3).join(' | '))
await browser.close()
finish()
