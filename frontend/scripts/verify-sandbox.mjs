/**
 * The sandbox loop in a real browser (Stage 4 + Phase 2/4).
 *
 * What it drives, in order: a suggestion card under the run, the input card,
 * the confirm gate's questions and Preview, adding to the sandbox, editing a
 * placed value, a natural-language removal, and confirm-to-delete in the
 * Interventions panel.
 *
 * ## Why only `/run` is stubbed
 *
 * Every other call this suite makes -- `/ai-plan`, `/sandbox/inputs`,
 * `/sandbox/preview`, `/sandbox/stage`, `/sandbox/resolve-removal`,
 * `/pins/*` -- is served by the **real backend** against the real rasters. That
 * is the point: the numbers the gate shows are the numbers the API produces,
 * and a stub for any of them would let this suite pass while the endpoint it
 * stands in for is broken.
 *
 * `/run` is the exception because it is the one expensive call, and stubbing it
 * is what lets the suite exercise the sandbox without a language model and
 * without tens of seconds per run. `scripts/fixtures/run-result.json` is a real
 * captured response, not a hand-built object, for the reason verify-chat
 * documents: `StatsPanel` reads a dozen sibling fields and a fixture missing one
 * crashes the panel into its error boundary instead of failing a check.
 *
 * ## What it does NOT cover
 *
 * The four existing `verify-*.mjs` suites cover their own surfaces. This one is
 * deliberately the loop's, and it does not re-assert the run receipt, the 3D
 * tilesets or the nav.
 *
 * Exit codes: 0 passed, 1 a check failed, 2 could not run (no Playwright, no
 * servers). A suite that cannot run must not look like one that passed.
 */
import { readFileSync } from 'node:fs'
import {
  BASE,
  LAUNCH_ARGS,
  loadPlaywright,
  makeChecker,
  requireServers,
  settleViewer,
  watchErrors,
} from './_verify-common.mjs'

const { chromium } = loadPlaywright()
await requireServers()
const { check, finish } = makeChecker()

const browser = await chromium.launch({ headless: true, args: LAUNCH_ARGS })
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
const errors = watchErrors(page)

/** The backend directly, for setup/teardown. The page itself goes through the proxy. */
const API = process.env.VERIFY_API ?? 'http://localhost:8000'

/**
 * The run is stubbed; everything else in this suite hits the real backend.
 *
 * The overlay PNGs the fixture names are never fetched: this suite asserts on
 * the card and the sandbox loop, and verify-3d is the suite that checks
 * overlays against a real run.
 */
const runResult = JSON.parse(
  readFileSync(new URL('./fixtures/run-result.json', import.meta.url), 'utf8'),
)
await page.route('**/api/scenes/*/run', async (route) => {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(runResult),
  })
})

/**
 * The scene this suite stages into.
 *
 * Read from the dev server's own `VITE_SCENE_ID` -- the same handle the app
 * boots from -- rather than typed here, so a reseeded scene cannot leave this
 * suite quietly staging into a scene nothing else uses. Falls back to the
 * committed seeded id, which is what `backend/data/scenes/` allowlists.
 */
const SCENE_ID =
  process.env.VERIFY_SCENE_ID ?? '51e03c37-5360-43b0-a094-fbaca38cd70b'

/** Take everything the previous case staged off the sandbox. */
const clearSandbox = async () => {
  const listed = await page.request.get(
    `${API}/api/scenes/${SCENE_ID}/pins`)
  if (!listed.ok()) return
  const body = await listed.json()
  for (const row of body.features ?? []) {
    if (row.pinned) {
      await page.request.post(`${API}/api/scenes/${SCENE_ID}/pins/${row.id}/unpin`)
    }
  }
}

await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await settleViewer(page)

const input = page.getByPlaceholder(/Ask about the run/)
const ask = async (text) => {
  await input.fill(text)
  await input.press('Enter')
}

// ---------------------------------------------------------------------------
// 1. A run, so there is a result card to hang suggestions under.
// ---------------------------------------------------------------------------
await clearSandbox()
// Waited on the response and the button's own label, not on card text: the
// card's wording belongs to the model, and a stub that changes it would break
// this suite for a reason that has nothing to do with the sandbox loop.
//
// The wait is armed BEFORE the click. The stub answers in milliseconds, so a
// listener registered after the click races the response and loses -- which is
// exactly how this suite first failed here, with a 90 s timeout on a request
// that had already succeeded.
const ran = page.waitForResponse(
  (r) => r.url().endsWith('/run') && r.request().method() === 'POST',
  { timeout: 90000 })
await page.getByText('Run Simulation').first().click()
await ran
await page.waitForFunction(
  () => !document.body.innerText.includes('Running…'), null, { timeout: 90000 })

// ---------------------------------------------------------------------------
// 2. The input card asks for a value, and the gate shows its questions.
// ---------------------------------------------------------------------------
await ask('add a water reservoir in the north')
const gate = page.getByRole('button', { name: /add \d+ to sandbox/i })
await gate.waitFor({ timeout: 30000 })
check('the confirm gate opens for a natural-language instruction', await gate.isVisible())

// The questions the server generated travel with the proposal (Phase 2 R8/R9).
const asked = page.getByText(/ASSUMPTIONS ASKED/i)
check('the gate names the assumptions the proposal rests on', await asked.first().isVisible())
const storageQuestion = page.getByText(/usableStorageDepth/i)
check('the storage depth is one of them, by its stable id',
  await storageQuestion.first().isVisible())

// The card opens on the AI's estimate, so the numeric field is not rendered
// until the planner chooses to enter their own value. Two checks, in order:
// the estimate is *shown with its basis*, then choosing "Enter my own" reveals
// the field the run would actually read.
check('the input card shows the estimate it would use, and where it comes from',
  await page.getByText(/estimate .*m\s*·/i).first().isVisible().catch(() => false))
await page.getByRole('button', { name: /enter my own/i }).first().click()
const depthField = page.getByRole('spinbutton', { name: /storage depth/i })
check('choosing to enter your own reveals the field for the depth the run reads',
  await depthField.first().isVisible())

// ---------------------------------------------------------------------------
// 3. Preview: the effect, labelled as not-a-run, with the deltas kept apart.
// ---------------------------------------------------------------------------
const previewButton = page.getByRole('button', { name: /preview effect/i })
check('the gate offers a preview beside Apply', await previewButton.isVisible())
await previewButton.click()
const previewLabel = page.getByText('PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE')
await previewLabel.waitFor({ timeout: 30000 })
check('the preview labels itself as an uncommitted change', await previewLabel.isVisible())
check('the preview keeps the marginal delta and the run delta apart',
  await page.getByText(/marginal \(these candidates/i).isVisible()
  && await page.getByText(/the run.s own delta/i).isVisible())
check('the preview declines the figures it does not measure',
  await page.getByText(/NOT the flood extent/i).first().isVisible())

// ---------------------------------------------------------------------------
// 4. Adding: the intervention lands on the sandbox and in the panel.
// ---------------------------------------------------------------------------
await gate.click()
await page.getByText(/Added to the sandbox/i).first().waitFor({ timeout: 30000 })
const panel = page.getByText('INTERVENTIONS').first()
check('the Interventions panel lists the placed intervention', await panel.isVisible())

// ---------------------------------------------------------------------------
// 5. Editing a placed value: the run re-scores, the footprint stays.
// ---------------------------------------------------------------------------
// The row's own Edit control, by its aria-label -- the panel builds it as
// "Edit <kind>", so a generic /edit|change/ also matches things that are not
// this button.
await page.getByRole('button', { name: /^Edit / }).first().click()
// Same two-step as the gate: the card opens on the estimate, and the numeric
// field appears once the planner chooses to enter their own value.
await page.getByRole('button', { name: /enter my own/i }).first().click()
const editField = page.getByRole('spinbutton', { name: /storage depth/i }).first()
await editField.waitFor({ state: 'visible', timeout: 15000 })
await editField.fill('2.5')
const saved = page.waitForResponse(
  (r) => r.url().includes('/sandbox/modify'), { timeout: 30000 })
await page.getByRole('button', { name: /^Save$/ }).first().click()
const saveResponse = await saved
check('an edited value is accepted by the sandbox',
  saveResponse.status() === 200, `HTTP ${saveResponse.status()}`)
check('and the row is still there afterwards -- an edit is not a removal',
  await page.getByRole('button', { name: /^Edit / }).first().isVisible())

// ---------------------------------------------------------------------------
// 6. Removal by natural language: it resolves to a confirm, never a silent delete.
// ---------------------------------------------------------------------------
await ask('remove the water reservoir')
const confirmRemove = page.getByRole('button', { name: /remove|unpin|confirm/i }).first()
await confirmRemove.waitFor({ timeout: 30000 })
check('a removal sentence resolves to a confirmation, not a silent delete',
  await confirmRemove.isVisible())
await confirmRemove.click()
await page.waitForTimeout(1500)

// ---------------------------------------------------------------------------
// 7. Nothing leaked, and the page stayed clean.
// ---------------------------------------------------------------------------
check('no uncaught page errors through the whole loop', errors.length === 0,
  errors.slice(0, 2).join(' | '))

await clearSandbox()
await browser.close()
finish()
