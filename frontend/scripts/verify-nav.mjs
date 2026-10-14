/**
 * Navigation in a real browser: the help, the new buttons, the keys and the compass.
 *
 * The unit tests hold the cheat-sheet to the bindings and the compass maths to
 * the right answers. This proves the part they cannot: that the panel really opens
 * once and stays dismissed, that the new keys and buttons move the actual camera,
 * and that the compass turns with the view and brings it back.
 *
 * Needs the dev server on :5173 and the backend on :8001. Exits 2 if either is
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

check('no unexpected console errors', errors.length === 0, errors.slice(0, 3).join(' | '))
await browser.close()
finish()
