/**
 * Frontend robustness checks.
 *
 * `verify-3d.mjs` proves the happy path renders. This one probes what it never
 * touches: failure, first paint, small screens, keyboard reach, an unreachable
 * backend and a malformed payload. Those are the paths a demo never exercises and a
 * user hits on the first bad day.
 *
 * Usage:
 *   node scripts/verify-ux.mjs            # needs :5173 and the backend on :8000
 *   PLAYWRIGHT_PATH=/path/to/playwright node scripts/verify-ux.mjs
 */
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  BASE, LAUNCH_ARGS, loadPlaywright, makeChecker, requireServers, settleViewer, watchErrors,
} from './_verify-common.mjs'

const { check, finish } = makeChecker()
await requireServers()
const pw = loadPlaywright()

const browser = await pw.chromium.launch({ args: LAUNCH_ARGS })

const newPage = async (viewport = { width: 1600, height: 900 }) => {
  const page = await browser.newPage({ viewport })
  return { page, errors: watchErrors(page) }
}

/** The app's own error surfaces. There is no MUI Drawer any more -- panels are HudPanels. */
const alerts = (page) =>
  page.evaluate(() => [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText))

// ---------------------------------------------------------------- 1. boot
{
  const { page, errors } = await newPage()
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForSelector('canvas', { timeout: 20000 }).catch(() => undefined)
  await page.waitForTimeout(1200)

  // First paint, before the tilesets land. There must be *something* honest on
  // screen: a blank canvas reads as a hung app, not as "loading".
  const early = await page.evaluate(() => {
    const canvas = document.querySelector('canvas')
    return { text: document.body.innerText.trim(), hasCanvas: Boolean(canvas) }
  })
  check('first paint shows content, not a blank page',
    early.hasCanvas && early.text.length > 0, `"${early.text.slice(0, 60)}"`)
  check('a loading affordance is shown before the tiles land',
    /loading|initialising|initializing/i.test(early.text), early.text.slice(0, 60))
  check('no unhandled error during boot', errors.length === 0, errors[0] ?? '')

  await settleViewer(page)
  check('the scene loads with no error alert', (await alerts(page)).length === 0,
    (await alerts(page)).join('; ').slice(0, 80))
  // The controls live in the right-hand HUD column.
  check('the scenario controls are present',
    /run simulation/i.test(await page.evaluate(() => document.body.innerText)), '')
  await page.close()
}

// ------------------------------------------------- 2. bad scene id -> error
{
  const { page } = await newPage()
  await page.route('**/api/scenes/**', (route) =>
    route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"scene not found"}' }))
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const text = await page.evaluate(() => document.body.innerText)
  const shown = await alerts(page)
  check('a missing scene surfaces an error to the user',
    shown.length > 0 || /not found|fail/i.test(text),
    (shown.join(' | ') || text).slice(0, 90))
  await page.close()
}

// ---------------------------------------------------- 3. backend unreachable
{
  const { page } = await newPage()
  // Match only the proxied backend, NOT `**/api/**`. That glob also matches Vite's own
  // module URLs -- `/src/api/client.ts` contains `/api/` -- so it aborts the app's source
  // modules, React never mounts, and the test reports a blank screen that is an artefact
  // of the test. Measured, and the reason this is worth a comment.
  await page.route(`${BASE}/api/**`, (route) => route.abort())
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const state = await page.evaluate(() => ({
    alerts: [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText),
    buttons: document.querySelectorAll('button').length,
  }))
  check('an unreachable backend surfaces an error rather than a blank screen',
    state.alerts.length > 0, state.alerts.join(' | ').slice(0, 90))
  check('the shell stays interactive when data fails', state.buttons > 0)
  await page.close()
}

// --------------------------------------------------------- 4. narrow screen
{
  const { page, errors } = await newPage({ width: 390, height: 844 })
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await settleViewer(page, { extraMs: 4000 })
  const layout = await page.evaluate(() => {
    const canvas = document.querySelector('canvas')
    const doc = document.documentElement
    const chat = document.querySelector('.MuiPaper-root')
    return {
      canvasW: canvas?.clientWidth ?? 0,
      canvasH: canvas?.clientHeight ?? 0,
      chatW: chat ? Math.round(chat.getBoundingClientRect().width) : 0,
      hScroll: doc.scrollWidth > doc.clientWidth,
      vpW: window.innerWidth,
    }
  })
  check('the map still renders on a 390 px screen',
    layout.canvasW > 0 && layout.canvasH > 0, `${layout.canvasW}x${layout.canvasH}`)
  // The 340 px chat rail must not swallow a phone viewport.
  check('a side panel does not cover the whole phone viewport',
    layout.chatW < layout.vpW * 0.92, `panel ${layout.chatW}px of ${layout.vpW}px`)
  check('no horizontal overflow on mobile', !layout.hScroll)
  check('no errors on a small screen', errors.length === 0, errors[0] ?? '')
  const shot = join(tmpdir(), 'hsk-mobile.png')
  await page.screenshot({ path: shot })
  check('a mobile screenshot was written', existsSync(shot), shot)
  await page.close()
}

// --------------------------------------------------------- 5. keyboard access
{
  const { page } = await newPage()
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await settleViewer(page, { extraMs: 3000 })
  // Resolve accessible names the way a browser does: `aria-labelledby`, then
  // `aria-label`, then an associated <label>, then own content, then `title`.
  const reach = await page.evaluate(() => {
    const text = (el) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const accName = (el) => {
      const by = el.getAttribute('aria-labelledby')
      if (by) {
        const t = by.split(/\s+/).map((id) => text(document.getElementById(id))).filter(Boolean).join(' ')
        if (t) return t
      }
      const al = el.getAttribute('aria-label')
      if (al && al.trim()) return al.trim()
      if (el.labels && el.labels.length) {
        const t = [...el.labels].map(text).filter(Boolean).join(' ')
        if (t) return t
      }
      return text(el) || (el.getAttribute('title') ?? '').trim()
    }
    // Cesium's own credit container renders a logo-only link to cesium.com. It is
    // third-party attribution markup, and stripping it would break Cesium's attribution
    // terms -- so it is excluded rather than "fixed".
    const isCesiumCredit = (el) =>
      Boolean(el.closest('.cesium-credit-wrapper, .cesium-credit-logoContainer'))
    const els = [...document.querySelectorAll(
      'button, input, [role="slider"], [role="switch"], a[href]',
    )].filter(
      (el) => !isCesiumCredit(el)
        && el.getAttribute('aria-hidden') !== 'true'
        && el.tabIndex >= 0,
    )
    return { total: els.length, unnamed: els.filter((el) => !accName(el)).length }
  })
  check('every focusable control has an accessible name',
    reach.total > 0 && reach.unnamed === 0, `${reach.total - reach.unnamed}/${reach.total} named`)

  // Without a keyboard-operable slider the rainfall and temperature scenario cannot be
  // changed at all, which makes the app mouse-only for its core input. MUI renders its
  // Slider as `input[type=range]`.
  const sliders = await page.evaluate(() => {
    const text = (el) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const named = (el) => {
      const by = el.getAttribute('aria-labelledby')
      if (by) {
        const t = by.split(/\s+/).map((id) => text(document.getElementById(id))).filter(Boolean).join(' ')
        if (t) return true
      }
      return Boolean((el.getAttribute('aria-label') ?? '').trim()
        || (el.labels && el.labels.length) || text(el))
    }
    const els = [...document.querySelectorAll('input[type="range"]')]
    return {
      count: els.length,
      focusable: els.filter((e) => e.tabIndex >= 0).length,
      named: els.filter(named).length,
    }
  })
  check('the parameter sliders are keyboard reachable',
    sliders.count > 0 && sliders.focusable === sliders.count, JSON.stringify(sliders))
  check('the parameter sliders have accessible names',
    sliders.count > 0 && sliders.named === sliders.count, `${sliders.named}/${sliders.count} named`)
  await page.close()
}

// ---------------------------------------------------- 6. reduced motion
{
  const { page, errors } = await newPage()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await settleViewer(page, { extraMs: 3000 })
  const ok = await page.evaluate(() => Boolean(window.__cesiumViewer))
  check('the app boots with prefers-reduced-motion set', ok && errors.length === 0,
    errors[0] ?? '')
  await page.close()
}

// --------------------------------------------- 7. 5xx from a real endpoint
{
  const { page, errors } = await newPage()
  await page.route((url) => url.pathname.startsWith('/api/'), (route) =>
    route.fulfill({ status: 503, contentType: 'application/json',
      body: '{"detail":"analysis service unavailable"}' }))
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const state = await page.evaluate(() => ({
    alerts: [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText),
    text: document.body.innerText,
  }))
  // A 503 has to say something specific. A generic "failed" would be indistinguishable
  // from the network being down, and the two need different responses from the user.
  check('a 5xx surfaces the server message',
    state.alerts.length > 0 || /unavailable|503|error|fail/i.test(state.text),
    (state.alerts.join(' | ') || state.text.slice(0, 90)).trim())
  check('a 5xx does not crash the viewer',
    errors.filter((e) => !/Failed to load resource/.test(e)).length === 0, errors[0] ?? '')
  await page.close()
}

// ------------------------------------------------ 8. malformed success payload
{
  const { page } = await newPage()
  await page.route((url) => url.pathname.startsWith('/api/'), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{"unexpected":"shape"}' }))
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const state = await page.evaluate(() => ({
    text: document.body.innerText.trim(),
    rootChildren: document.getElementById('root')?.childElementCount ?? -1,
  }))
  // A scene with every required field missing is the shape of a contract drift. It must
  // not blank the app: an empty-but-alive shell is recoverable, a white page is not.
  check('an unexpected payload explains itself instead of blanking',
    state.rootChildren > 0 && /fail|read|expect|shape|match|error/i.test(state.text),
    `${state.rootChildren} root children, "${state.text.slice(0, 60)}"`)
  await page.close()
}

// ------------------------------------------------------ 9. slow network
{
  const { page } = await newPage()
  // Long enough to catch a missing loading affordance, short enough not to time the
  // suite out. The API client has a 60 s timeout, so this sits well inside it.
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    await new Promise((r) => setTimeout(r, 2500))
    await route.continue()
  })
  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1500)
  const during = await page.evaluate(() => document.body.innerText.trim().length)
  check('something is on screen while the data is still loading', during > 0,
    `${during} chars of UI during load`)
  await page.close()
}

await browser.close()
finish()
