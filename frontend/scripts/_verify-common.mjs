/**
 * Shared plumbing for the two browser verification scripts.
 *
 * Neither script is part of `npm test`: they need the dev server on :5173, the
 * backend on :8001 (vite.config.ts proxies /api, /static and /health there) and a
 * Chromium that Playwright can launch. Folding them into `npm test` would let a
 * missing server read as a pass.
 *
 * Exit codes: 0 every check passed, 1 a check failed, 2 the script could not run
 * (no Playwright, no servers). A script that cannot run must not look like one
 * that passed.
 */
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

/**
 * Playwright is resolved without a baked-in path:
 *   1. $PLAYWRIGHT_PATH, a directory holding an existing install
 *   2. `playwright` resolvable from this repo
 */
export function loadPlaywright() {
  for (const candidate of [process.env.PLAYWRIGHT_PATH, 'playwright']) {
    if (!candidate) continue
    try {
      return require(candidate)
    } catch {
      /* try the next one */
    }
  }
  console.error(
    'Playwright not found. Either set PLAYWRIGHT_PATH to an existing install, or:\n' +
      '  npm i --no-save playwright && npx playwright install chromium',
  )
  process.exit(2)
}

export const BASE = process.env.VERIFY_BASE ?? 'http://localhost:5173'

/** Cesium needs a real GL context; SwiftShader gives it one headlessly. */
export const LAUNCH_ARGS = [
  '--use-gl=angle',
  '--use-angle=swiftshader',
  '--enable-unsafe-swiftshader',
  '--ignore-gpu-blocklist',
]

/** Fail fast, with a clear message, if the servers are not up. */
export async function requireServers() {
  for (const [name, url] of [
    ['Vite dev server', `${BASE}/`],
    ['backend (via the Vite proxy)', `${BASE}/health`],
  ]) {
    try {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (err) {
      console.error(`${name} is not reachable at ${url}: ${err.message}`)
      console.error('Start the backend on :8001 and `npm run dev` on :5173 first.')
      process.exit(2)
    }
  }
}

export function makeChecker() {
  const results = []
  const check = (name, ok, detail = '') => {
    results.push({ name, ok: Boolean(ok) })
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? ` - ${detail}` : ''}`)
  }
  const finish = () => {
    const failed = results.filter((r) => !r.ok).length
    console.log(`\n${results.length - failed}/${results.length} checks passed`)
    process.exit(failed === 0 ? 0 : 1)
  }
  return { check, finish }
}

/**
 * Console noise that is not a defect. Vite finishes loading its module graph with a
 * reload, and React emits a transient "Invalid hook call" while two module instances
 * swap. Uncaught exceptions still arrive through `pageerror`, which is the signal
 * that matters.
 */
const NOISE = /Invalid hook call|favicon|CORS policy|Failed to load resource/i

export function watchErrors(page) {
  const errors = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error' && !NOISE.test(m.text())) errors.push(m.text())
  })
  return errors
}

/**
 * Wait until the viewer exists and every 3D Tiles tileset has finished loading.
 *
 * The buildings and terrain are 3D Tiles, so counting entities on the data sources
 * (what this used to do, against 2,639 procedural envelopes) reads 0 and times out.
 */
export async function settleViewer(page, { timeout = 90000, extraMs = 1500 } = {}) {
  await page.waitForFunction(
    () => {
      const v = window.__cesiumViewer
      if (!v) return false
      const sets = []
      for (let i = 0; i < v.scene.primitives.length; i += 1) {
        const p = v.scene.primitives.get(i)
        if (p && typeof p.tilesLoaded === 'boolean') sets.push(p)
      }
      return sets.length > 0 && sets.every((s) => s.tilesLoaded)
    },
    null,
    { timeout },
  )
  await page.waitForTimeout(extraMs)
}
