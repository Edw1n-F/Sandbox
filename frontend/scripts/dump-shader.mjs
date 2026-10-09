/**
 * Dumps the GLSL Cesium generates for the building material, with line numbers.
 *
 * A compile error in a custom fabric reports something like "ERROR: 0:5: cannot
 * convert from 'const 3-component vector of float' to 'highp float'" and nothing
 * else useful: the line number refers to the *assembled* shader, and the offending
 * source is buried among automatic uniform declarations. This prints what Cesium
 * actually built so the mismatch can be read directly.
 *
 * Usage: node scripts/dump-shader.mjs [baseUrl]
 */
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'

const require = createRequire(import.meta.url)
const { chromium } = require(
  process.env.PLAYWRIGHT_PATH ??
    '/Users/edwin/.npm/_npx/e41f203b7505f1fb/node_modules/playwright',
)

// Pull the fabric straight out of the source file so this cannot drift from it.
const tech = readFileSync(new URL('../src/view/techMaterial.ts', import.meta.url), 'utf8')
const SOURCE = tech.match(/const SOURCE = `([\s\S]*?)`\n/)[1]

const BASE = process.argv[2] ?? 'http://localhost:5199/'
const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
})
const page = await browser.newPage({ viewport: { width: 1200, height: 800 } })
await page.goto(BASE, { waitUntil: 'domcontentloaded' })
await page.waitForFunction(() => Boolean(window.__cesium), null, { timeout: 60000 })

const out = await page.evaluate((src) => {
  const C = window.__cesium
  const m = new C.Material({
    fabric: {
      type: 'HskTechMassing',
      uniforms: {
        u_lit: { type: 'float', value: 1 },
        u_body: { type: 'vec3', value: new C.Color(0.7, 0.7, 0.7) },
        u_edge: { type: 'vec3', value: new C.Color(0, 0, 0) },
        u_glow: { type: 'float', value: 0 },
        u_grid: { type: 'float', value: 0 },
        u_height01: { type: 'float', value: 0 },
      },
      source: src,
    },
    translucent: false,
  })
  return {
    source: m.shaderSource.split('\n'),
    uniforms: Object.entries(m.uniforms).map(([k, v]) => [k, v.type]),
  }
}, SOURCE)

out.source.forEach((l, i) => console.log(String(i).padStart(3), '|', l))
console.log('--- uniforms ---')
out.uniforms.forEach(([k, t]) => console.log(k, '=>', t))
await browser.close()
