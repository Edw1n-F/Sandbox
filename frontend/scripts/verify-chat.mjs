/**
 * Chat panel in a real browser: the receipt, the live region, the in-progress
 * shimmer, and the dropped-stream message.
 *
 * The chat stream is mocked (Playwright route interception), so this does not
 * depend on a language model being configured or on the free tier being under its
 * rate limit. `/run` is real, patched only to add `sources`, so the receipt is
 * checked against a response with the field regardless of how old the running
 * backend is.
 *
 * Needs the dev server on :5173 and the backend on :8001. Exits 2 if either is
 * missing, so a missing server cannot read as a pass.
 */
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

const browser = await chromium.launch({ headless: true, args: LAUNCH_ARGS })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = watchErrors(page)

// ---- routes ----------------------------------------------------------------
await page.route('**/api/scenes/*/run', async (route) => {
  const res = await route.fetch()
  const body = await res.json()
  body.sources = { terrain: 'open3dhk-tiles', landCover: 'esa-worldcover', lst: 'Landsat 8/9 C2 L2 ST_B10' }
  await route.fulfill({ response: res, json: body })
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
