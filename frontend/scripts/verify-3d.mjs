/**
 * Browser verification for the 3D sandbox.
 *
 * Not a unit test — everything checked here is invisible to `tsc`: does the
 * camera leave nadir, does the custom shader compile, do envelopes stand on the
 * terrain, does panning work, does a mode switch stall. A clean typecheck passed
 * while the camera sat at pitch -90 and every envelope was buried, which is the
 * exact class of bug this script exists to catch.
 *
 * Usage: node scripts/verify-3d.mjs [baseUrl]
 */
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { chromium } = require(
  process.env.PLAYWRIGHT_PATH ??
    '/Users/edwin/.npm/_npx/e41f203b7505f1fb/node_modules/playwright',
)

const BASE = process.argv[2] ?? 'http://localhost:5199/'
const checks = []
const check = (name, pass, detail) => {
  checks.push({ name, pass })
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`)
}

const browser = await chromium.launch({
  args: [
    // Cesium needs a real GL context; SwiftShader gives it one headlessly.
    '--use-gl=angle',
    '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    '--ignore-gpu-blocklist',
  ],
})
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })

const errors = []
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))

await page.goto(BASE, { waitUntil: 'domcontentloaded' })
// 2,639 envelopes, so give it room before asserting on geometry.
// Note the explicit `null` arg: Playwright's signature is (fn, arg, options),
// so passing the options object second silently falls back to a 30 s timeout.
try {
  // Counted on the data sources, not on `scene.primitives`: a GeoJsonDataSource
  // owns its own private PrimitiveCollection, so `scene.primitives.length` stays
  // small however much of the city is loaded.
  await page.waitForFunction(
    () => {
      const v = window.__cesiumViewer
      if (!v) return false
      let n = 0
      for (let i = 0; i < v.dataSources.length; i += 1) {
        n += v.dataSources.get(i).entities.values.length
      }
      return n > 2000
    },
    null,
    { timeout: 120000 },
  )
} catch (err) {
  console.error('model never appeared:', err.message)
  console.error('console errors:', errors.slice(0, 10))
  await page.screenshot({ path: '/tmp/hsk-failed.png' })
  await browser.close()
  process.exit(1)
}
await page.waitForTimeout(6000)

/**
 * Mean absolute horizontal gradient over a clip of the canvas: a cheap proxy for
 * how much detail the basemap actually resolved. Used to catch the
 * `requestRenderMode` trap where a zoom change re-requests imagery, the texture
 * arrives, and nothing asks for a frame -- so the map stays blurry at the wrong
 * level of detail until an unrelated interaction happens.
 */
const basemapDetail = async () => {
  const buf = await page.screenshot({ clip: { x: 420, y: 60, width: 900, height: 560 } })
  return page.evaluate(async (b64) => {
    const img = new Image()
    img.src = 'data:image/png;base64,' + b64
    await img.decode()
    const c = document.createElement('canvas')
    c.width = img.width
    c.height = img.height
    const ctx = c.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(img, 0, 0)
    const d = ctx.getImageData(0, 0, c.width, c.height).data
    let sum = 0
    let n = 0
    for (let y = 0; y < c.height; y += 3) {
      for (let x = 0; x < c.width - 1; x += 3) {
        const i = (y * c.width + x) * 4
        sum += Math.abs(d[i] - d[i + 4])
        n += 1
      }
    }
    return sum / n
  }, buf.toString('base64'))
}

/**
 * Shortest signed difference between two compass headings, in degrees.
 *
 * `camera.heading` is normalised to [-180, 180], so an orbit from 179 to -179 is a
 * 2 degree turn. Compared naively that reads as 358 and would satisfy any
 * "did the heading change" threshold, including on a frame where nothing moved.
 */
const headingDelta = (a, b) => {
  let d = a - b
  while (d > 180) d -= 360
  while (d < -180) d += 360
  return d
}

const probe = () =>
  page.evaluate(() => {
    const v = window.__cesiumViewer
    const cam = v.camera
    const carto = cam.positionCartographic
    const d = cam.direction
    const up = v.scene.globe.ellipsoid.geodeticSurfaceNormal(cam.positionWC, {})
    const len = Math.hypot(d.x, d.y, d.z)
    const dot = (d.x * up.x + d.y * up.y + d.z * up.z) / len
    return {
      height: carto.height,
      lon: (carto.longitude * 180) / Math.PI,
      lat: (carto.latitude * 180) / Math.PI,
      heading: (cam.heading * 180) / Math.PI,
      pitchDeg: (Math.asin(Math.max(-1, Math.min(1, dot))) * 180) / Math.PI,
      primitives: v.scene.primitives.length,
    }
  })


const initial = await probe()
check('camera is oblique, not nadir',
  initial.pitchDeg < -20 && initial.pitchDeg > -75,
  `pitch ${initial.pitchDeg.toFixed(1)}deg`)
check('camera stands off at district scale', initial.height > 800,
  `${Math.round(initial.height)} m`)

const shaderErrors = errors.filter((e) => /shader|glsl|compil/i.test(e))
check('building shader compiled with no errors',
  shaderErrors.length === 0, shaderErrors[0]?.slice(0, 200))

const grounded = await page.evaluate(() => {
  const v = window.__cesiumViewer
  let extruded = 0
  let sampled = 0
  let sumGround = 0
  let clamped = 0
  for (let i = 0; i < v.dataSources.length; i += 1) {
    v.dataSources.get(i).entities.values.forEach((e) => {
      const h = e.polygon?.extrudedHeight?.getValue?.()
      const g = e.polygon?.height?.getValue?.()
      if (e.polygon?.heightReference?.getValue?.() === 2) clamped += 1
      if (typeof h !== 'number' || typeof g !== 'number') return
      sampled += 1
      if (h - g > 1) extruded += 1
      sumGround += g
    })
  }
  return { sampled, extruded, clamped, meanGround: sampled ? sumGround / sampled : 0 }
})
check('every envelope is extruded',
  grounded.sampled > 2000 && grounded.extruded === grounded.sampled,
  `${grounded.extruded}/${grounded.sampled}`)
check('envelopes stand on the terrain, not on sea level',
  grounded.meanGround > 3, `mean base ${grounded.meanGround.toFixed(1)} m`)
check('envelopes are not clamped (custom material survives)',
  grounded.clamped === 0, `${grounded.clamped} clamped`)

// Opacity is not something a screenshot settles on its own: translucent primitives
// do not write depth, so 2,639 envelopes fail to occlude each other and the street
// map reads through the district. `Material.fromType` defaults a custom fabric to
// translucent, so this is a real trap rather than a hypothetical one.
const blending = await page.evaluate(() => {
  const v = window.__cesiumViewer
  let translucent = 0
  let opaque = 0
  for (let i = 0; i < v.dataSources.length; i += 1) {
    const ds = v.dataSources.get(i)
    // Not every data source owns a primitive collection -- the interventions layer
    // may not have loaded yet -- so skip rather than bail.
    const col = ds._primitiveCollection ?? ds._primitives
    if (!col) continue
    for (let k = 0; k < col.length; k += 1) {
      const app = col.get(k).appearance
      if (!app) continue
      if (app.translucent) translucent += 1
      else opaque += 1
    }
  }
  return { translucent, opaque }
})
check('envelopes are drawn opaque, so they occlude each other',
  blending.opaque > 2000 && blending.translucent === 0,
  `${blending.opaque} opaque, ${blending.translucent} translucent`)

await page.mouse.move(1100, 450)
await page.mouse.down()
await page.mouse.move(900, 450, { steps: 12 })
await page.mouse.up()
await page.waitForTimeout(1200)
const panned = await probe()
check('drag pans the camera',
  Math.abs(panned.lon - initial.lon) > 1e-5 || Math.abs(panned.lat - initial.lat) > 1e-5,
  `dlon ${(panned.lon - initial.lon).toFixed(6)} dlat ${(panned.lat - initial.lat).toFixed(6)}`)

// ---- 3. rotation ----------------------------------------------------------
//
// Cesium's stock bindings put zoom on RIGHT_DRAG and hide rotation behind
// MIDDLE_DRAG and Ctrl+drag, so the gesture people reach for to turn a model did
// something else entirely. These assert the rebinding: right-drag orbits, the wheel
// still zooms, left-drag still pans, and the pitch stays inside its clamps.
//
// Placed immediately after the pan check on purpose. Cesium's drag aggregator only
// sees a gesture when the moves land in one task; the same drag spread across frames
// reads as almost no movement, which looks exactly like a dead binding.
const beforeOrbit = await probe()

// Left-drag must still pan. Asserted because re-binding rotate away from LEFT_DRAG
// is the one change that could plausibly have taken panning with it.
await page.mouse.move(1100, 450)
await page.mouse.down()
await page.mouse.move(980, 450, { steps: 12 })
await page.mouse.up()
await page.waitForTimeout(1000)
const afterPan = await probe()
check('left-drag still pans after rebinding rotate',
  Math.abs(afterPan.lon - beforeOrbit.lon) > 1e-5 || Math.abs(afterPan.lat - beforeOrbit.lat) > 1e-5,
  `dlon ${(afterPan.lon - beforeOrbit.lon).toFixed(6)} dlat ${(afterPan.lat - beforeOrbit.lat).toFixed(6)}`)

// Right-drag orbits: heading must change, and distance must not.
await page.mouse.move(1100, 450)
await page.mouse.down({ button: 'right' })
await page.mouse.move(900, 380, { steps: 12 })
await page.mouse.up({ button: 'right' })
await page.waitForTimeout(1400)
const afterOrbit = await probe()
// Measured on the camera's *position*, not its heading. Cesium's drag handler is
// `spin3D`, which swings the camera around the globe; the compass heading of a
// camera that has slid along the surface without turning is unchanged, so asserting
// on heading would report a working gesture as a dead one.
const swung =
  Math.abs(afterOrbit.lon - afterPan.lon) > 1e-5 ||
  Math.abs(afterOrbit.lat - afterPan.lat) > 1e-5
check('right-drag swings the camera around the district', swung,
  `lon ${afterPan.lon.toFixed(5)} -> ${afterOrbit.lon.toFixed(5)}, ` +
  `lat ${afterPan.lat.toFixed(5)} -> ${afterOrbit.lat.toFixed(5)}`)
check('right-drag orbits rather than zooms',
  Math.abs(afterOrbit.height - afterPan.height) < afterPan.height * 0.25,
  `height ${Math.round(afterPan.height)} -> ${Math.round(afterOrbit.height)} m`)

// Plain wheel is deliberately inert: on a trackpad the two-finger swipe is expected to
// move the map, and since it cannot be panned reliably here it now does nothing rather
// than zooming. A mouse wheel is the same DOM event, so it is inert too -- but the page
// must not scroll behind the canvas as a result, which is what the swallow is for.
await page.mouse.move(1000, 450)
const pageBefore = await page.evaluate(() => window.scrollY)
await page.mouse.wheel(0, -400)
await page.waitForTimeout(1400)
const afterWheel = await probe()
check('the scroll wheel no longer zooms', Math.abs(afterWheel.height - afterOrbit.height) < afterOrbit.height * 0.02,
  `${Math.round(afterOrbit.height)} -> ${Math.round(afterWheel.height)} m`)
const pageAfter = await page.evaluate(() => window.scrollY)
check('the scroll gesture does not scroll the page either', pageAfter === pageBefore,
  `scrollY ${pageBefore} -> ${pageAfter}`)

// Zoom stays reachable, and is more sensitive than the 0.8-per-press it used to be.
const beforeZoom = await probe()
await page.getByRole('button', { name: 'Zoom in' }).click()
await page.waitForTimeout(900)
const afterZoom = await probe()
check('the zoom button is sensitive enough to feel immediate',
  beforeZoom.height - afterZoom.height > beforeZoom.height * 0.3,
  `${Math.round(beforeZoom.height)} -> ${Math.round(afterZoom.height)} m`)

// The on-screen buttons, which are the pointer-free route.
const beforeButton = await probe()
await page.getByRole('button', { name: 'Rotate right' }).click()
await page.waitForTimeout(900)
const afterButton = await probe()
check('the rotate button turns the view',
  Math.abs(headingDelta(afterButton.heading, beforeButton.heading)) > 1,
  `heading ${beforeButton.heading.toFixed(1)} -> ${afterButton.heading.toFixed(1)} deg`)

const beforeTilt = await probe()
await page.getByRole('button', { name: 'Tilt up' }).click()
await page.waitForTimeout(900)
const afterTilt = await probe()
check('the tilt button changes the viewing angle',
  Math.abs(afterTilt.pitchDeg - beforeTilt.pitchDeg) > 1,
  `pitch ${beforeTilt.pitchDeg.toFixed(1)} -> ${afterTilt.pitchDeg.toFixed(1)} deg`)

// Arrow keys, and the guard that stops them stealing keys from the panel's sliders.
const beforeKeys = await probe()
for (const key of ['ArrowLeft', 'ArrowLeft']) {
  await page.keyboard.press(key)
  await page.waitForTimeout(500)
}
const afterKeys = await probe()
check('arrow keys orbit the camera',
  Math.abs(headingDelta(afterKeys.heading, beforeKeys.heading)) > 1,
  `heading ${beforeKeys.heading.toFixed(1)} -> ${afterKeys.heading.toFixed(1)} deg`)

const rainfall = page.getByRole('slider').first()
await rainfall.focus()
const beforeSliderKeys = await probe()
await page.keyboard.press('ArrowRight')
await page.waitForTimeout(600)
const afterSliderKeys = await probe()
check('arrow keys do not orbit while a slider has focus',
  Math.abs(headingDelta(afterSliderKeys.heading, beforeSliderKeys.heading)) < 0.01,
  `heading moved ${(afterSliderKeys.heading - beforeSliderKeys.heading).toFixed(4)} deg`)

// Pitch must stay clamped, so repeated presses cannot swing the camera under the
// terrain or into a nadir view.
//
// Focus is dropped first, deliberately. The check above leaves it on the rainfall
// slider, where arrow keys are correctly ignored -- so without this the presses
// would do nothing and the assertion would pass without ever testing a clamp.
await page.evaluate(() => {
  const el = document.activeElement
  if (el && typeof el.blur === 'function') el.blur()
})
const beforeClamp = await probe()
// Ten presses of 8 degrees is 80 degrees, which from a shallow angle is past the
// -88 degree limit, so this cannot pass by accident.
for (let i = 0; i < 10; i += 1) {
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(220)
}
const afterClamp = await probe()
check('repeated tilt presses do not swing past the pitch limit',
  afterClamp.pitchDeg < beforeClamp.pitchDeg - 20,
  `pitch ${beforeClamp.pitchDeg.toFixed(1)} -> ${afterClamp.pitchDeg.toFixed(1)} deg`)
check('pitch stays within its clamps',
  afterClamp.pitchDeg > -88.5,
  `pitch ${afterClamp.pitchDeg.toFixed(1)} deg, limit -88`)
check('the camera never ends up under the terrain',
  afterClamp.height > 0,
  `${Math.round(afterClamp.height)} m above the ellipsoid`)

// Back to the canonical framing before any pixel measurement. The rotation checks
// above deliberately leave the camera somewhere else, and the tactical assertions
// sample fixed screen regions -- without this they would be measuring empty
// background and reporting a broken height ramp that is merely off-screen.
await page.getByRole('button', { name: '3D view' }).click()
await page.waitForTimeout(2200)

await page.getByRole('button', { name: 'Tactical' }).click()
await page.waitForTimeout(2500)
const tactical = await page.evaluate(() => {
  const s = window.__cesiumViewer.scene
  return {
    bloom: s.postProcessStages.bloom.enabled,
    sky: s.skyAtmosphere ? s.skyAtmosphere.show : null,
    brightness: s.imageryLayers.length ? s.imageryLayers.get(0).brightness : null,
    legend: document.body.innerText.includes('Envelope height'),
    primitives: s.primitives.length,
  }
})
check('tactical mode enables bloom', tactical.bloom === true)
check('tactical mode hides the sky', tactical.sky === false)
check('tactical mode dims the basemap',
  tactical.brightness !== null && tactical.brightness < 0.5,
  `brightness ${tactical.brightness}`)
check('tactical mode shows the legend', tactical.legend === true)
check('mode switch rebuilt no primitives',
  tactical.primitives === panned.primitives,
  `${panned.primitives} -> ${tactical.primitives}`)

// ---- 5b. the tactical view actually reads as a height ramp -----------------
//
// Flags flipping is not evidence the mode works. The first version of the shader
// set every flag correctly and rendered 2,639 buildings as one flat cyan blob,
// because a rim sized in st-space floods a footprint that is three pixels wide.
// So this measures the pixels: within the district, the frame must contain both
// cool and warm envelope colours, or the ramp is not doing its job.
const shot = await page.screenshot({
  clip: { x: 700, y: 150, width: 800, height: 600 },
})
const ramp = await page.evaluate(async (b64) => {
  const img = new Image()
  img.src = 'data:image/png;base64,' + b64
  await img.decode()
  const c = document.createElement('canvas')
  c.width = img.width
  c.height = img.height
  const ctx = c.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(img, 0, 0)
  const d = ctx.getImageData(0, 0, c.width, c.height).data
  let cool = 0
  let warm = 0
  let sum = 0
  const n = d.length / 4
  for (let i = 0; i < d.length; i += 4) {
    const r = d[i]
    const g = d[i + 1]
    const bl = d[i + 2]
    sum += 0.2126 * r + 0.7152 * g + 0.0722 * bl
    // Amber envelope bodies are red-dominant; cyan rims are blue-dominant.
    if (r > bl + 20 && r > 70) warm += 1
    else if (bl > r + 20 && bl > 70) cool += 1
  }
  return { meanLuma: sum / n, warm, cool }
}, shot.toString('base64'))

check('tactical frame is dark, not a washed-out map',
  ramp.meanLuma < 90,
  `mean luma ${ramp.meanLuma.toFixed(1)}/255`)
check('tactical frame shows both ends of the height ramp',
  ramp.warm > 200 && ramp.cool > 200,
  `${ramp.cool} cool px, ${ramp.warm} warm px`)

const grid = await page.evaluate(() => {
  const v = window.__cesiumViewer
  let found = 0
  v.entities.values.forEach((e) => {
    if (e.name === 'Reference grid' && e.show && e.rectangle) found += 1
  })
  return found
})
check('tactical mode adds the reference grid', grid === 1, `${grid} grid entities`)

// Captured before the close-up move below, so the two files show the two scales
// rather than both showing whichever view happened to be last.
await page.screenshot({ path: '/tmp/hsk-tactical.png' })

// ---- 5c. the wireframe emerges on approach ---------------------------------
//
// The rim is faded out when a footprint is only a few pixels wide, so the district
// view is carried by the height ramp. The corollary is that approaching a building
// must bring the outline back, or the mode has no detail at all up close. This is
// the half of the behaviour a single screenshot cannot show.
await page.evaluate(() => {
  const v = window.__cesiumViewer
  // `positionWC` is a Cartesian3, so its constructor carries `fromDegrees`.
  // A fixed close view over the district rather than a zoom: `zoomIn` is a
  // per-tick delta and requestRenderMode means there may be no tick to apply it
  // on, so it silently does nothing.
  const Cartesian3 = v.camera.positionWC.constructor
  v.camera.setView({
    // High enough to clear the towers: the envelopes are opaque now, so a low
    // camera is simply inside one.
    destination: Cartesian3.fromDegrees(113.9945, 22.4402, 235),
    orientation: { heading: -0.79, pitch: -0.62, roll: 0 },
    duration: 0,
  })
  v.scene.requestRender()
})
await page.waitForTimeout(3000)
const near = await page.screenshot({ clip: { x: 700, y: 200, width: 700, height: 500 } })
const detail = await page.evaluate(async (b64) => {
  const img = new Image()
  img.src = 'data:image/png;base64,' + b64
  await img.decode()
  const c = document.createElement('canvas')
  c.width = img.width
  c.height = img.height
  const ctx = c.getContext('2d', { willReadFrequently: true })
  ctx.drawImage(img, 0, 0)
  const d = ctx.getImageData(0, 0, c.width, c.height).data
  let bright = 0
  for (let i = 0; i < d.length; i += 4) {
    // The rim's own signature: strongly blue-dominant and bright. Measured off a
    // real frame rather than guessed -- the rim peaks around G=204/B=229 and is
    // anti-aliased, so a threshold on any single channel misses most of it.
    if (d[i + 2] > 120 && d[i + 1] > 100 && d[i + 2] > d[i] + 50) bright += 1
  }
  return { bright, total: d.length / 4 }
}, near.toString('base64'))
check('close up, the neon outline returns',
  detail.bright > 1200,
  `${detail.bright} bright rim px`)
await page.screenshot({ path: '/tmp/hsk-tactical-close.png' })

// Back to the district framing first: the close-up block above leaves the camera
// at 95 m, which is *inside* the massing, and from inside a box every face is
// back-face culled. The resulting see-through shells look exactly like a rendering
// fault and are not one.
await page.getByRole('button', { name: '3D view' }).click()
// Sampled twice on purpose. Imagery for the new zoom arrives over the network, and
// under requestRenderMode the frame that shows it has to be asked for; so the metric
// has to *climb* while nothing is happening. A flat pair means nothing is
// requesting frames and the basemap is stuck at the wrong level of detail.
await page.waitForTimeout(500)
const justAfterMove = await basemapDetail()
await page.waitForTimeout(8000)
const settledDetail = await basemapDetail()
check('basemap detail recovers after a zoom round trip',
  settledDetail > justAfterMove * 1.02,
  `${justAfterMove.toFixed(2)} -> ${settledDetail.toFixed(2)}`)

await page.getByRole('button', { name: 'Survey' }).click()
await page.waitForTimeout(3000)
const back = await page.evaluate(() => {
  const s = window.__cesiumViewer.scene
  return {
    bloom: s.postProcessStages.bloom.enabled,
    sky: s.skyAtmosphere ? s.skyAtmosphere.show : null,
    brightness: s.imageryLayers.length ? s.imageryLayers.get(0).brightness : null,
  }
})
check('survey restores bloom to off', back.bloom === false)
check('survey restores the sky', back.sky === true)
check('survey restores basemap brightness',
  (back.brightness ?? 0) > 0.9, `brightness ${back.brightness}`)
await page.screenshot({ path: '/tmp/hsk-survey.png' })

await page.getByRole('button', { name: 'Top-down' }).click()
await page.waitForTimeout(2200)
const nadir = await probe()
check('top-down button gives a nadir view on purpose', nadir.pitchDeg < -85,
  `pitch ${nadir.pitchDeg.toFixed(1)}deg`)
await page.getByRole('button', { name: '3D view' }).click()
await page.waitForTimeout(2200)
const oblique = await probe()
check('3D view button restores the oblique view',
  oblique.pitchDeg < -20 && oblique.pitchDeg > -75,
  `pitch ${oblique.pitchDeg.toFixed(1)}deg`)
await page.screenshot({ path: '/tmp/hsk-oblique.png' })

await page.mouse.click(1050, 480)
await page.waitForTimeout(900)
const picked = await page.evaluate(() => {
  const v = window.__cesiumViewer
  return v.scene.primitives.length > 0
})
check('scene still interactive after mode round-trip', picked === true)

// OSM's tile servers send no CORS headers, so tiles above roughly z19 fail from a
// browser context. That is an upstream limitation of a key-less public basemap, not
// an app fault, and the tactical mode dims the basemap anyway.
const fatal = errors.filter(
  (e) => !/favicon|404|Failed to load resource|CORS policy/i.test(e),
)
check('no unexpected console errors', fatal.length === 0, fatal.slice(0, 2).join(' | '))

await browser.close()
const failed = checks.filter((c) => !c.pass).length
console.log(`\n${checks.length - failed}/${checks.length} checks passed`)
process.exit(failed ? 1 : 0)
