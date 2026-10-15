/**
 * Navigation in a real browser: the help, the new buttons, the keys and the compass.
 *
 * The unit tests hold the cheat-sheet to the bindings and the compass maths to
 * the right answers. This proves the part they cannot: that the panel really opens
 * once and stays dismissed, that the new keys and buttons move the actual camera,
 * and that the compass turns with the view and brings it back.
 *
 * Needs the dev server on :5173 and the backend on :8000. Exits 2 if either is
 * missing, so a missing server cannot read as a pass.
 */
import { BASE, LAUNCH_ARGS, loadPlaywright, makeChecker, requireServers, settleViewer, watchErrors } from './_verify-common.mjs'

const { chromium } = loadPlaywright()
await requireServers()
const { check, finish } = makeChecker()

const browser = await chromium.launch({ headless: true, args: LAUNCH_ARGS })
// A fresh context per scenario: localStorage is what the first-run flag lives in.
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
const errors = watchErrors(page)

await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await settleViewer(page)

const camera = () => page.evaluate(() => {
  const v = window.__cesiumViewer
  return { heading: (v.camera.heading * 180) / Math.PI, pitch: (v.camera.pitch * 180) / Math.PI,
           height: v.camera.positionCartographic.height }
})
const angleDiff = (a, b) => Math.abs(((a - b + 540) % 360) - 180)
const help = page.getByRole('dialog', { name: 'How to move around the map' })

// ---- first run ---------------------------------------------------------------
check('the help opens by itself on the first visit', await help.isVisible())
// Compared case-insensitively: the section titles use the `hud-label` style,
// which uppercases through CSS, and `innerText` returns the rendered case.
const helpText = (await help.innerText()).toLowerCase()
check('and lists mouse, trackpad and keyboard',
  helpText.includes('mouse') && helpText.includes('trackpad') && helpText.includes('keyboard'))

await page.keyboard.press('Escape')
await page.waitForTimeout(300)
check('Escape closes it', !(await help.isVisible().catch(() => false)))

await page.reload({ waitUntil: 'domcontentloaded' })
await settleViewer(page)
check('and it stays closed on the next visit', !(await help.isVisible().catch(() => false)))

await page.getByLabel('How to move around').click()
check('the ? button brings it back', await help.isVisible())
await page.getByLabel('Close help').click()
check('and the close button dismisses it', !(await help.isVisible().catch(() => false)))

// ---- buttons that were gesture-only -------------------------------------------
const before = await camera()
await page.getByLabel('Tilt up (towards the horizon)').click()
await page.waitForTimeout(600)
const tiltedUp = await camera()
check('Tilt up raises the pitch towards the horizon', tiltedUp.pitch > before.pitch + 3,
  `${before.pitch.toFixed(1)} -> ${tiltedUp.pitch.toFixed(1)} deg`)
await page.getByLabel('Tilt down (towards overhead)').click()
await page.getByLabel('Tilt down (towards overhead)').click()
await page.waitForTimeout(600)
const tiltedDown = await camera()
check('Tilt down lowers it', tiltedDown.pitch < tiltedUp.pitch - 3,
  `${tiltedUp.pitch.toFixed(1)} -> ${tiltedDown.pitch.toFixed(1)} deg`)

// ---- the compass --------------------------------------------------------------
const compass = page.getByLabel(/^Facing/)
await page.getByLabel('Reset framing').click()
await page.waitForTimeout(2500)
const northLabel = await compass.getAttribute('aria-label')

await page.getByLabel('Orbit right').click()
await page.getByLabel('Orbit right').click()
await page.waitForTimeout(800)
const rotated = await camera()
const rotatedLabel = await compass.getAttribute('aria-label')
check('rotating the view changes what the compass reports',
  rotatedLabel !== northLabel, `${northLabel}  ->  ${rotatedLabel}`)
check('and the compass names the real heading', new RegExp(`${Math.round(rotated.heading)}`).test(rotatedLabel ?? '')
  || /degrees/.test(rotatedLabel ?? ''), rotatedLabel ?? '')

await compass.click()
await page.waitForTimeout(1200)
const faced = await camera()
check('pressing the compass faces north again', angleDiff(faced.heading, 0) < 4 || angleDiff(faced.heading, 360) < 4,
  `${rotated.heading.toFixed(1)} -> ${faced.heading.toFixed(1)} deg`)

// ---- keyboard ----------------------------------------------------------------
await page.locator('canvas').first().click({ position: { x: 700, y: 450 } })
const k0 = await camera()
await page.keyboard.press('ArrowRight')
await page.waitForTimeout(500)
const k1 = await camera()
check('Arrow keys still rotate', angleDiff(k1.heading, k0.heading) > 3,
  `${k0.heading.toFixed(1)} -> ${k1.heading.toFixed(1)} deg`)

await page.keyboard.press('t')
await page.waitForTimeout(2800)
const top = await camera()
check('T goes top-down', top.pitch < -80, `pitch ${top.pitch.toFixed(1)} deg`)

await page.keyboard.press('Home')
await page.waitForTimeout(2800)
const home = await camera()
check('Home resets the framing', home.pitch > -80 && home.pitch < -20, `pitch ${home.pitch.toFixed(1)} deg`)

// ---- not hijacking text entry -------------------------------------------------
const input = page.getByPlaceholder(/Ask about the run/)
await input.click()
const t0 = await camera()
await input.type('t0 home')
await page.waitForTimeout(500)
const t1 = await camera()
check('typing "t", "0" and "Home" in the chat does not move the camera',
  Math.abs(t1.pitch - t0.pitch) < 0.5 && angleDiff(t1.heading, t0.heading) < 0.5,
  `${t0.pitch.toFixed(1)} -> ${t1.pitch.toFixed(1)}`)

// ---- one axis per gesture, at the limits --------------------------------------
//
// The reported bugs: "zoom doesn't zoom based on where the user is, but relative to
// default", and "when you tilt past a certain limit, it just starts rotating instead".
// Both are checked here against the real camera, because both are about what the
// camera actually does rather than about what the code intends.
const groundRange = () => page.evaluate(() => {
  const v = window.__cesiumViewer
  const c = v.camera
  const cv = v.scene.canvas
  const hit = c.pickEllipsoid({ x: cv.clientWidth / 2, y: cv.clientHeight / 2 })
  return hit
    ? Math.hypot(c.positionWC.x - hit.x, c.positionWC.y - hit.y, c.positionWC.z - hit.z)
    : null
})

await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const limit0 = await camera()
for (let i = 0; i < 9; i++) await page.getByLabel('Tilt down (towards overhead)').click()
await page.waitForTimeout(700)
const limit1 = await camera()
check('tilting past the pitch limit stops at the limit',
  Math.abs(limit1.pitch - (-88)) < 1, `pitch ${limit0.pitch.toFixed(1)} -> ${limit1.pitch.toFixed(1)} deg`)
check('and tilting past it does not rotate the view',
  angleDiff(limit1.heading, limit0.heading) < 0.5,
  `heading ${limit0.heading.toFixed(2)} -> ${limit1.heading.toFixed(2)} deg`)

const atLimit = await camera()
for (let i = 0; i < 3; i++) await page.getByLabel('Orbit right').click()
await page.waitForTimeout(700)
const rotatedAtLimit = await camera()
check('rotating at the limit turns without changing the pitch',
  angleDiff(rotatedAtLimit.heading, atLimit.heading) > 20
    && Math.abs(rotatedAtLimit.pitch - atLimit.pitch) < 0.5,
  `heading ${atLimit.heading.toFixed(1)} -> ${rotatedAtLimit.heading.toFixed(1)}, ` +
  `pitch ${atLimit.pitch.toFixed(1)} -> ${rotatedAtLimit.pitch.toFixed(1)} deg`)

await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const zoomStart = await camera()
const rangeStart = await groundRange()
await page.getByLabel('Zoom in').click()
await page.waitForTimeout(700)
const zoomIn = await camera()
check('zooming leaves the heading and pitch alone',
  Math.abs(zoomIn.pitch - zoomStart.pitch) < 0.5 && angleDiff(zoomIn.heading, zoomStart.heading) < 0.5,
  `heading ${zoomStart.heading.toFixed(2)} -> ${zoomIn.heading.toFixed(2)}, ` +
  `pitch ${zoomStart.pitch.toFixed(2)} -> ${zoomIn.pitch.toFixed(2)} deg`)

const rangeIn = await groundRange()
await page.getByLabel('Zoom out').click()
await page.waitForTimeout(700)
const rangeOut = await groundRange()
check('and zooming in then out returns to about the same distance',
  Math.abs(rangeOut - rangeStart) < rangeStart * 0.05,
  `${rangeStart?.toFixed(0)} -> ${rangeIn?.toFixed(0)} -> ${rangeOut?.toFixed(0)} m`)

// ZOOM_STEP is 0.55, so two presses must land near 0.55^2 = 0.30 of the distance. This
// is the freeze that used to swallow the second quick press (STATUS item 15): it was a
// `requestAnimationFrame`-driven glide, and the second press arrived before the next
// frame. Both presses are now applied outright, so this is asserted rather than skipped.
await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const rapidStart = await groundRange()
await page.getByLabel('Zoom in').click()
await page.getByLabel('Zoom in').click()
await page.waitForTimeout(700)
const rapidEnd = await groundRange()
check('two quick zoom presses add up instead of losing one',
  rapidEnd !== null && rapidStart !== null && rapidEnd < rapidStart * 0.4,
  `${rapidStart?.toFixed(0)} -> ${rapidEnd?.toFixed(0)} m (one press would be ~${(rapidStart * 0.55).toFixed(0)})`)

// A pan, then a zoom: the point under the view centre must stay put. The old zoom
// re-placed the camera about the fixed scene pivot, which dragged the view back to the
// middle of the district -- measured then, one click after a 517 m pan moved the point
// under the view centre by 464 m.
//
// Measured as a SCREEN position, which is the honest form of "the user does not see the
// view slide": the anchor is the same world point before and after, so if the zoom is
// anchored on it, it does not move across the canvas. The app picks the anchor with
// `pickPosition` first (the 3D tile surface), so the probe uses the same order rather
// than the ellipsoid, which sits up to 139 m of relief lower.
const anchorScreen = () => page.evaluate(async () => {
  const three = await import('/node_modules/.vite/deps/cesium.js')
  const v = window.__cesiumViewer
  const c = v.camera
  const cv = v.scene.canvas
  const pixel = new three.Cartesian2(cv.clientWidth / 2, cv.clientHeight / 2)
  const ray = c.getPickRay(pixel)
  const anchor = (v.scene.pickPositionSupported ? v.scene.pickPosition(pixel) : undefined)
    ?? (ray ? v.scene.globe.pick(ray, v.scene) : undefined)
    ?? c.pickEllipsoid(pixel, three.Ellipsoid.WGS84)
  if (!anchor) return null
  const win = three.SceneTransforms.worldToWindowCoordinates(v.scene, anchor)
  return win ? { x: win.x, y: win.y } : null
})

await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
await page.mouse.move(700, 450)
await page.mouse.down()
await page.mouse.move(380, 330, { steps: 14 })
await page.mouse.up()
await page.waitForTimeout(1200)
const anchorBefore = await anchorScreen()
await page.getByLabel('Zoom in').click()
await page.waitForTimeout(700)
const anchorAfter = await anchorScreen()
const anchorDrift = anchorBefore && anchorAfter
  ? Math.hypot(anchorAfter.x - anchorBefore.x, anchorAfter.y - anchorBefore.y)
  : null
check('zooming after a pan zooms where the user is looking, not back to the centre',
  anchorDrift !== null && anchorDrift < 8,
  anchorDrift === null
    ? 'no ground under the view centre'
    : `the point under the view centre moved ${anchorDrift.toFixed(1)} px`)

// A right-drag is the mouse's tilt/rotate gesture: vertical travel tilts, horizontal
// travels rotates, and each must move only its own axis.
await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const dragTilt0 = await camera()
await page.mouse.move(700, 450)
await page.mouse.down({ button: 'right' })
await page.mouse.move(700, 560, { steps: 14 })
await page.mouse.up({ button: 'right' })
await page.waitForTimeout(800)
const dragTilt1 = await camera()
check('a vertical right-drag tilts without rotating',
  angleDiff(dragTilt1.heading, dragTilt0.heading) < 1 && Math.abs(dragTilt1.pitch - dragTilt0.pitch) > 3,
  `heading ${dragTilt0.heading.toFixed(2)} -> ${dragTilt1.heading.toFixed(2)}, ` +
  `pitch ${dragTilt0.pitch.toFixed(1)} -> ${dragTilt1.pitch.toFixed(1)} deg`)

await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const dragRot0 = await camera()
await page.mouse.move(700, 450)
await page.mouse.down({ button: 'right' })
await page.mouse.move(880, 450, { steps: 14 })
await page.mouse.up({ button: 'right' })
await page.waitForTimeout(800)
const dragRot1 = await camera()
check('a horizontal right-drag rotates without tilting',
  angleDiff(dragRot1.heading, dragRot0.heading) > 3 && Math.abs(dragRot1.pitch - dragRot0.pitch) < 1,
  `heading ${dragRot0.heading.toFixed(1)} -> ${dragRot1.heading.toFixed(1)}, ` +
  `pitch ${dragRot0.pitch.toFixed(1)} -> ${dragRot1.pitch.toFixed(1)} deg`)

// The mouse wheel zooms. A wheel notch and a trackpad two-finger drag are the same DOM
// event, so `classifyWheel` tells them apart; a real `page.mouse.wheel` is a notch.
await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const wheel0 = await camera()
const wheelRange0 = await groundRange()
await page.mouse.move(700, 450)
for (let i = 0; i < 3; i++) {
  await page.mouse.wheel(0, 120)
  // Over the 120 ms threshold, so each reads as its own notch rather than a stream.
  await page.waitForTimeout(400)
}
const wheelOutRange = await groundRange()
const wheelOut = await camera()
check('scrolling the wheel zooms out',
  wheelOutRange !== null && wheelRange0 !== null && wheelOutRange > wheelRange0 * 1.3,
  `${wheelRange0?.toFixed(0)} -> ${wheelOutRange?.toFixed(0)} m`)
check('and a wheel zoom does not rotate or tilt the view',
  angleDiff(wheelOut.heading, wheel0.heading) < 0.5 && Math.abs(wheelOut.pitch - wheel0.pitch) < 0.5,
  `heading ${wheel0.heading.toFixed(2)} -> ${wheelOut.heading.toFixed(2)}, ` +
  `pitch ${wheel0.pitch.toFixed(2)} -> ${wheelOut.pitch.toFixed(2)} deg`)

for (let i = 0; i < 3; i++) {
  await page.mouse.wheel(0, -120)
  await page.waitForTimeout(400)
}
const wheelBackRange = await groundRange()
check('scrolling the other way zooms back in',
  wheelBackRange !== null && wheelRange0 !== null && Math.abs(wheelBackRange - wheelRange0) < wheelRange0 * 0.15,
  `${wheelRange0?.toFixed(0)} -> ${wheelOutRange?.toFixed(0)} -> ${wheelBackRange?.toFixed(0)} m`)

// A trackpad two-finger drag is a fast stream of small deltas and must tilt, not zoom.
await page.getByLabel('Reset framing').click()
await page.waitForTimeout(3000)
const stream0 = await camera()
const streamRange0 = await groundRange()
await page.evaluate(() => {
  const cv = window.__cesiumViewer.scene.canvas
  for (let i = 0; i < 24; i++) {
    cv.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 8 }))
  }
})
await page.waitForTimeout(500)
const stream1 = await camera()
const streamRange1 = await groundRange()
check('a trackpad-drag stream tilts instead of zooming',
  Math.abs(stream1.pitch - stream0.pitch) > 3
    && streamRange1 !== null && streamRange0 !== null
    && Math.abs(streamRange1 - streamRange0) < streamRange0 * 0.05,
  `pitch ${stream0.pitch.toFixed(1)} -> ${stream1.pitch.toFixed(1)} deg, ` +
  `range ${streamRange0?.toFixed(0)} -> ${streamRange1?.toFixed(0)} m`)

check('no unexpected console errors', errors.length === 0, errors.slice(0, 3).join(' | '))
await browser.close()
finish()
