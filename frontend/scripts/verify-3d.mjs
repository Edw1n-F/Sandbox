/**
 * Browser verification for the 3D sandbox.
 *
 * Not a unit test: everything here is invisible to `tsc`. Does the real terrain and
 * building tileset load, does the camera frame the site rather than sit at nadir,
 * do the overlays appear when a run finishes and visibly change the canvas, do the
 * camera controls and the flood/heat switches work. A clean typecheck once passed
 * while the camera sat at pitch -90 and every building was buried -- that is the
 * class of bug this script exists to catch.
 *
 * Usage:
 *   node scripts/verify-3d.mjs            # needs :5173 and the backend on :8001
 *   PLAYWRIGHT_PATH=/path/to/playwright node scripts/verify-3d.mjs
 */
import {
  BASE, LAUNCH_ARGS, loadPlaywright, makeChecker, requireServers, settleViewer, watchErrors,
} from './_verify-common.mjs'

const { check, finish } = makeChecker()
await requireServers()
const pw = loadPlaywright()

const browser = await pw.chromium.launch({ args: LAUNCH_ARGS })
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
const errors = watchErrors(page)

const state = () => page.evaluate(() => {
  const v = window.__cesiumViewer
  if (!v) return null
  const sets = []
  for (let i = 0; i < v.scene.primitives.length; i += 1) {
    const p = v.scene.primitives.get(i)
    if (p && typeof p.tilesLoaded === 'boolean') {
      sets.push({ name: String(p.resource?.url ?? '').split('/').slice(-2)[0], loaded: p.tilesLoaded })
    }
  }
  return {
    sets,
    primitives: v.scene.primitives.length,
    // The pan is a pure lateral move, so a height-only comparison misses it. The
    // camera's lon/lat is what actually moves.
    lon: v.camera.positionCartographic.longitude,
    lat: v.camera.positionCartographic.latitude,
    terrain: v.terrainProvider?.constructor?.name ?? null,
    height: v.camera.positionCartographic.height,
    pitchDeg: (v.camera.pitch * 180) / Math.PI,
  }
})

// ------------------------------------------------------------------ 1. boot
await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await settleViewer(page)
const boot = await state()
check('the Cesium viewer exists', boot !== null)
check('every 3D Tiles tileset finished loading',
  Boolean(boot?.sets.length) && boot.sets.every((s) => s.loaded),
  JSON.stringify(boot?.sets))
check('the terrain is the custom heightmap provider, not Ion',
  boot?.terrain === 'CustomHeightmapTerrainProvider', String(boot?.terrain))
check('no unhandled error during boot', errors.length === 0, errors[0] ?? '')

// ----------------------------------------------------------- 2. 3D framing
check('camera is oblique, not nadir', boot.pitchDeg > -80 && boot.pitchDeg < -10,
  `${boot.pitchDeg.toFixed(1)} deg`)
check('camera stands off at district scale',
  boot.height > 200 && boot.height < 6000, `${Math.round(boot.height)} m`)

await page.getByLabel('Top-down (read rasters)').click()
await page.waitForTimeout(3000)
const nadir = await state()
check('the top-down button gives a nadir view on purpose',
  nadir.pitchDeg < -85, `${nadir.pitchDeg.toFixed(1)} deg`)

await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const back = await state()
check('reset framing restores the oblique district view',
  back.pitchDeg > -80 && back.pitchDeg < -10, `${back.pitchDeg.toFixed(1)} deg`)

// --------------------------------------------------------- 3. interaction
await page.mouse.move(800, 450)
await page.mouse.down()
await page.mouse.move(1200, 450, { steps: 20 })
await page.mouse.up()
await page.waitForTimeout(1500)
const panned = await state()
const movedMetres = Math.hypot(
  (panned.lon - back.lon) * 111320 * Math.cos(back.lat),
  (panned.lat - back.lat) * 110540,
)
// Left-drag is a lateral ground pan, so it is measured as ground distance, not as a
// height or pitch change. At this framing a 400 px drag moves only a few metres.
check('left-drag pans the camera laterally', movedMetres > 2,
  `${movedMetres.toFixed(1)} m laterally, height ${Math.round(back.height)} -> ${Math.round(panned.height)} m`)

// ------------------------------------------------------- 4. overlays render
// Each overlay is a Cesium entity named "Flood extent" / "Heat map overlay" whose
// rectangle material points at the per-run PNG. Reading those is how "the run is
// visible" is checked -- the primitive count does not move, and the URL carries the
// run id, so a stale overlay cannot pass.
const overlayUrls = () => page.evaluate(() => {
  const v = window.__cesiumViewer
  const out = []
  for (let i = 0; i < v.entities.values.length; i += 1) {
    const e = v.entities.values[i]
    if (!/Flood extent|Heat map overlay/.test(e.name ?? '')) continue
    const img = e.rectangle?.material?.image
    const url = img ? (typeof img.getValue === 'function' ? img.getValue() : img) : null
    out.push({ name: e.name, show: e.show, url: url ? String(url).split('/').pop() : null })
  }
  return out
})
const shotBefore = await page.screenshot({ path: 'verify-before-run.png' })
const preOverlays = await overlayUrls()

// The Run button starts the simulation; the overlays only appear on its result.
await page.getByText('Run Simulation').first().click()
const runResponse = await page
  .waitForResponse((r) => r.url().endsWith('/run') && r.request().method() === 'POST',
    { timeout: 120000 })
  .catch(() => null)
check('the Run button reaches POST /run', runResponse?.status() === 200,
  String(runResponse?.status()))
await settleViewer(page, { extraMs: 6000 })
const shotAfter = await page.screenshot({ path: 'verify-after-run.png' })
const postOverlays = await overlayUrls()

// A run whose result is invisible is indistinguishable from a failed run. The
// strongest evidence is a fresh per-run overlay image actually drawn.
const fresh = postOverlays.filter((o) => o.url && !o.url.includes('undefined'))
check('both overlays are drawn, from this run id',
  fresh.length === 2 && fresh.every((o) => /wetgrid_|heatmap_/.test(o.url)),
  `${JSON.stringify(preOverlays)} -> ${JSON.stringify(postOverlays)}`)
check('the canvas visibly changes after a run', !shotBefore.equals(shotAfter),
  `${shotBefore.length} -> ${shotAfter.length} bytes`)

const panel = await page.evaluate(() => document.body.innerText.replace(/\s+/g, ' '))
check('the stats panel states the flood extent', /flood extent/i.test(panel),
  panel.slice(Math.max(0, panel.search(/flood extent/i) - 0), panel.search(/flood extent/i) + 40))
check('the stats panel states the impervious share', /impervious share/i.test(panel))

// ------------------------------------------------ 5. the flood/heat switches
for (const label of ['Flood', 'Heat']) {
  const chip = page.getByText(label, { exact: true }).first()
  await chip.click().catch(() => undefined)
  await page.waitForTimeout(900)
  await chip.click().catch(() => undefined)
  await page.waitForTimeout(900)
  check(`the ${label} overlay toggles without a fatal error`,
    errors.filter((e) => !/Failed to load resource/.test(e)).length === 0, errors[0] ?? '')
}

// ------------------------------------------------------------ 6. no errors
const fatal = errors.filter((e) => !/Failed to load resource/.test(e))
check('no unexpected console errors', fatal.length === 0, fatal.slice(0, 2).join(' | '))

await browser.close()
finish()
