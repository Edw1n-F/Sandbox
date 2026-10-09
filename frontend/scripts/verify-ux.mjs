/**
 * Frontend robustness checks.
 *
 * `verify-3d.mjs` proves the happy path renders and that the 3D work is correct. This
 * one probes what it never touches: failure, first paint, small screens, keyboard
 * reach and reduced motion. Those are the paths a demo never exercises and a user
 * hits on the first bad day.
 *
 *   node scripts/verify-ux.mjs      # dev server on :5173, backend on :8000
 */
import { createRequire } from 'node:module'
const require = createRequire(import.meta.url)
const { chromium } = require(
  '/Users/edwin/.npm/_npx/e41f203b7505f1fb/node_modules/playwright',
)

const BASE = 'http://localhost:5173'
let pass = 0
let fail = 0
const results = []
const check = (name, ok, detail = '') => {
  results.push(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`)
  ok ? pass++ : fail++
}

const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
})

const newPage = async (viewport = { width: 1600, height: 900 }) => {
  const page = await browser.newPage({ viewport })
  const errors = []
  page.on('pageerror', (e) => errors.push(String(e.message)))
  page.on('console', (m) => {
    const t = m.text()
    // Vite's dev server finishes loading its module graph with a reload, and React
    // emits a transient "Invalid hook call" while that happens -- two module instances
    // mid-swap. Not reproducible on a settled page and not a defect, so it is filtered
    // rather than left to fail a run at random. Uncaught exceptions are still caught
    // via `pageerror`, which is the signal that actually matters.
    const hmrNoise = /Invalid hook call|favicon|CORS policy|Failed to load resource/i
    if (m.type() === 'error' && !hmrNoise.test(t)) errors.push(t)
  })
  return { page, errors }
}

const settle = async (page, ms = 9000) => {
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
    { timeout: 60000 },
  )
  await page.waitForTimeout(ms)
}

// ---------------------------------------------------------------- 1. boot
{
  const { page, errors } = await newPage()
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  // Vite's dev server does a full reload as it finishes its module graph, which
  // destroys the execution context mid-evaluate. Wait for the canvas to exist before
  // measuring first paint, and retry once if the context goes anyway.
  await page.waitForSelector('canvas', { timeout: 20000 }).catch(() => undefined)
  await page.waitForTimeout(600)

  // First paint, before the 1.27 MB city model lands. There must be *something*
  // honest on screen: a blank canvas reads as a hung app, not as "loading".
  const early = await page.evaluate(() => {
    const canvas = document.querySelector('canvas')
    return {
      text: document.body.innerText.trim().slice(0, 80),
      hasCanvas: Boolean(canvas),
      size: canvas ? `${canvas.clientWidth}x${canvas.clientHeight}` : 'none',
    }
  })
  check('first paint shows content, not a blank page',
    early.hasCanvas && early.text.length > 0, `canvas ${early.size}, text "${early.text}"`)
  check('no unhandled error during boot', errors.length === 0, errors[0] ?? '')

  await settle(page)
  const loaded = await page.evaluate(() => ({
    alerts: [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText),
    drawer: Boolean(document.querySelector('.MuiDrawer-root')),
  }))
  check('the scene loads with no error alert', loaded.alerts.length === 0,
    loaded.alerts.join('; ').slice(0, 80))
  check('the control panel is present', loaded.drawer)
  await page.close()
}

// ------------------------------------------------- 2. bad scene id -> error
{
  const { page } = await newPage()
  await page.route('**/api/scenes/**', (route) =>
    route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: '{"detail":"scene not found"}',
    }),
  )
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const err = await page.evaluate(() => ({
    alerts: [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText),
    text: document.body.innerText,
  }))
  check('a missing scene surfaces an error to the user',
    err.alerts.length > 0 || /not found/i.test(err.text),
    (err.alerts.join(' | ') || err.text).slice(0, 90))
  await page.close()
}

// ---------------------------------------------------- 3. backend unreachable
{
  const { page } = await newPage()
  // Match only the proxied backend, NOT `**/api/**`. That glob also matches Vite's own
  // module URLs -- `/src/api/client.ts` contains `/api/` -- so it aborts the app's source
  // modules, the graph never loads, React never mounts, and the test reports a
  // "blank white screen" that is entirely an artefact of the test. Measured, and the
  // reason this is worth a comment.
  await page.route('http://localhost:5173/api/**', (route) => route.abort())
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
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
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await settle(page, 7000)
  const layout = await page.evaluate(() => {
    const canvas = document.querySelector('canvas')
    const drawer = document.querySelector('.MuiDrawer-root')
    const doc = document.documentElement
    return {
      canvasW: canvas?.clientWidth ?? 0,
      canvasH: canvas?.clientHeight ?? 0,
      drawerW: drawer ? Math.round(drawer.getBoundingClientRect().width) : 0,
      drawerVisible: Boolean(drawer && drawer.getBoundingClientRect().width > 0),
      toggle: Boolean(document.querySelector('[aria-label*="control panel"]')),
      hScroll: doc.scrollWidth > doc.clientWidth,
    }
  })
  check('the map still renders on a 390 px screen',
    layout.canvasW > 0 && layout.canvasH > 0, `${layout.canvasW}x${layout.canvasH}`)
  // The panel collapses itself below 768 px, so on a phone the map is full-bleed and
  // the toggle brings the controls back. Before that it covered 100% of the viewport.
  check('the panel does not swallow a phone viewport',
    layout.drawerVisible === false, `drawer visible=${layout.drawerVisible}`)
  check('the panel can be summoned on a phone', layout.toggle)
  check('no horizontal overflow on mobile', !layout.hScroll)
  check('no errors on a small screen', errors.length === 0, errors[0] ?? '')
  await page.screenshot({ path: '/tmp/hsk-mobile.png' })
  await page.close()
}

// --------------------------------------------------------- 5. keyboard access
{
  const { page } = await newPage()
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await settle(page, 7000)
  // Resolve accessible names the way a browser does: `aria-labelledby`, then
  // `aria-label`, then an associated <label>, then own content, then `title`.
  //
  // Sniffing only `aria-label` — which is what the first version of this check did —
  // reported all seven layer switches and both sliders as unnamed, and they are not.
  // MUI's `FormControlLabel` wires the label through the `labels` association and
  // `SliderField` sets `aria-labelledby`; neither puts text in the input itself. A
  // check that cries wolf three times gets ignored, so it is worth doing properly.
  const reach = await page.evaluate(() => {
    const text = (el) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const accName = (el) => {
      const by = el.getAttribute('aria-labelledby')
      if (by) {
        const t = by.split(/\s+/)
          .map((id) => text(document.getElementById(id))).filter(Boolean).join(' ')
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
    // third-party attribution markup, not this app's, and stripping it would break
    // Cesium's attribution terms — so it is excluded rather than "fixed".
    const isCesiumCredit = (el) =>
      Boolean(el.closest('.cesium-credit-wrapper, .cesium-credit-logoContainer'))
    const els = [...document.querySelectorAll(
      'button, input, [role="slider"], [role="switch"], a[href]',
    )].filter(
      (el) =>
        !isCesiumCredit(el) &&
        // MUI renders hidden native inputs beside its real controls (form submission
        // and the like). They are `aria-hidden` or out of the tab order, so a screen
        // reader never reaches them — counting them reported a phantom unnamed
        // control that no user could ever meet.
        el.getAttribute('aria-hidden') !== 'true' &&
        el.tabIndex >= 0,
    )
    const unnamed = els.filter((el) => !accName(el))
    return { total: els.length, unnamed: unnamed.length }
  })
  check('every focusable control has an accessible name',
    reach.total > 0 && reach.unnamed === 0,
    `${reach.total - reach.unnamed}/${reach.total} named`)

  // Without a keyboard-operable slider the rainfall and temperature scenario cannot
  // be changed at all, which makes the app mouse-only for its core input.
  // MUI renders its Slider as `input[type=range]`, whose implicit ARIA role is
  // `slider`. Querying `[role="slider"]` finds nothing and would have reported a false
  // gap -- the attribute is only present when a name is supplied, which is itself the
  // issue the next check finds.
  const sliders = await page.evaluate(() => {
    const text = (el) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    // `SliderField` labels its slider with `aria-labelledby` pointing at the caption,
    // so checking `aria-label` alone reports "0 named" for a correctly wired control.
    const named = (el) => {
      const by = el.getAttribute('aria-labelledby')
      if (by) {
        const t = by.split(/\s+/).map((id) => text(document.getElementById(id))).filter(Boolean).join(' ')
        if (t) return true
      }
      return Boolean(
        (el.getAttribute('aria-label') ?? '').trim() ||
        (el.labels && el.labels.length) ||
        text(el),
      )
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
    sliders.count > 0 && sliders.named === sliders.count,
    `${sliders.named}/${sliders.count} named`)
  await page.close()
}

// ------------------------------------------------------- 6. reduced motion
{
  const { page } = await newPage()
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await settle(page, 7000)
  await page.getByRole('button', { name: 'Tactical' }).click()
  await page.waitForTimeout(1500)
  const mode = await page.evaluate(() => {
    const v = window.__cesiumViewer
    return { ok: Boolean(v), bloom: v?.scene?.postProcessStages?.bloom?.enabled ?? null }
  })
  check('the app works with prefers-reduced-motion set', mode.ok && mode.bloom === true)
  await page.close()
}

// --------------------------------------------- 7. 5xx from a real endpoint
{
  const { page, errors } = await newPage()
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) =>
      route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: '{"detail":"analysis service unavailable"}',
      }),
  )
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const state = await page.evaluate(() => ({
    alerts: [...document.querySelectorAll('.MuiAlert-root')].map((a) => a.innerText),
    text: document.body.innerText,
  }))
  // A 503 has to say something specific. A generic "failed" would be indistinguishable
  // from the network being down, and the two need different responses from the user.
  check('a 5xx surfaces the server message',
    state.alerts.length > 0 || /unavailable|503|error/i.test(state.text),
    (state.alerts.join(' | ') || state.text.slice(0, 90)).trim())
  check('a 5xx does not crash the viewer',
    errors.filter((e) => !/Failed to load resource/.test(e)).length === 0,
    errors[0] ?? '')
  await page.close()
}

// ------------------------------------------------ 8. malformed success payload
{
  const { page } = await newPage()
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: '{"unexpected":"shape"}',
      }),
  )
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(6000)
  const state = await page.evaluate(() => ({
    text: document.body.innerText.trim(),
    pageErrors: document.getElementById('root')?.childElementCount ?? -1,
  }))
  // A scene with every required field missing is the shape of a contract drift. It must
  // not blank the app: an empty-but-alive shell is recoverable, a white page is not.
  // Not "renders a canvas": with an unreadable scene the honest outcome is a message
  // and *no* map. Rendering a map anyway would imply there is data behind it.
  check('an unexpected payload explains itself instead of blanking',
    state.pageErrors > 0 && /fail|read|expect|shape|match/i.test(state.text),
    `${state.pageErrors} root child, text "${state.text.slice(0, 60)}"`)
  await page.close()
}

// ------------------------------------------------------ 9. slow network
{
  const { page } = await newPage()
  // Long enough to catch a missing loading affordance, short enough not to time the
  // suite out. The API has a 20 s axios timeout, so this sits inside it.
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      await new Promise((r) => setTimeout(r, 2500))
      await route.continue()
    },
  )
  await page.goto(`${BASE}/`, { waitUntil: 'domcontentloaded' })
  await page.waitForTimeout(1200)
  const during = await page.evaluate(() => document.body.innerText.trim().length)
  check('something is on screen while the data is still loading', during > 0,
    `${during} chars of UI during load`)
  await page.close()
}

await browser.close()
console.log(results.join('\n'))
console.log(`\n${pass}/${pass + fail} checks passed`)
process.exit(fail === 0 ? 0 : 1)
