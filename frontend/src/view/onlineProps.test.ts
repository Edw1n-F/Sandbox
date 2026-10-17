/**
 * `placementMatrix`: the one pure function in the online-props layer.
 *
 * Everything else in `onlineProps.ts` talks to a Cesium `Viewer` and needs a
 * real GL context, so it is covered by `verify-3d.mjs` against a live render.
 * This module covers the arithmetic, because a transform bug here does not
 * throw and does not look broken: a heading applied about the wrong axis, a
 * scale dropped on the way through `moveTo`, or a height in the wrong place all
 * still draw *a* model somewhere on the map.
 *
 * The three properties that matter, each of which has been wrong in a similar
 * layer before:
 *
 * 1. **The anchor is the lon/lat/height, not the origin.** A model placed at
 *    (113.99, 22.43, 4) whose matrix translates to (0, 0, 0) renders at the
 *    centre of the earth -- which Cesium happily draws, and which reads as
 *    "the props did not load".
 * 2. **A heading is a rotation about local vertical**, so it must not change
 *    the translation or the scale.
 * 3. **Scale is uniform.** A non-uniform scale on a glTF model is a silently
 *    distorted mesh, not an error.
 */
import { Cartesian3, Matrix4 } from 'cesium'
import { describe, expect, it } from 'vitest'

import { ONLINE_PROPS, placementMatrix } from './onlineProps'

/** The ECEF position a matrix translates to. */
function originOf(matrix: Matrix4): Cartesian3 {
  return Matrix4.getTranslation(matrix, new Cartesian3())
}

/** The uniform scale a matrix applies, read back off its axes. */
function scaleOf(matrix: Matrix4): Cartesian3 {
  return Matrix4.getScale(matrix, new Cartesian3())
}

describe('placementMatrix', () => {
  it('translates to the requested position, not to the origin', () => {
    const m = placementMatrix(113.9959, 22.4451, 4, 0, 1)
    const expected = Cartesian3.fromDegrees(113.9959, 22.4451, 4)
    const actual = originOf(m)

    // Metres, not degrees: `fromDegrees` gives ECEF, and a degree-valued
    // translation would be a point near the earth's centre.
    expect(Math.abs(actual.x - expected.x)).toBeLessThan(1e-6)
    expect(Math.abs(actual.y - expected.y)).toBeLessThan(1e-6)
    expect(Math.abs(actual.z - expected.z)).toBeLessThan(1e-6)
    expect(Cartesian3.magnitude(actual)).toBeGreaterThan(6_000_000)
  })

  it('applies a uniform scale, and the scale is what was asked for', () => {
    const m = placementMatrix(113.9959, 22.4451, 4, 0, 2.5)
    const s = scaleOf(m)
    expect(s.x).toBeCloseTo(2.5, 6)
    expect(s.y).toBeCloseTo(2.5, 6)
    expect(s.z).toBeCloseTo(2.5, 6)
  })

  it('a heading changes the rotation but not the position or the scale', () => {
    const base = placementMatrix(113.9959, 22.4451, 4, 0, 1.5)
    const turned = placementMatrix(113.9959, 22.4451, 4, 90, 1.5)

    const a = originOf(base)
    const b = originOf(turned)
    expect(a.x).toBeCloseTo(b.x, 6)
    expect(a.y).toBeCloseTo(b.y, 6)
    expect(a.z).toBeCloseTo(b.z, 6)

    const sa = scaleOf(base)
    const sb = scaleOf(turned)
    expect(sa.x).toBeCloseTo(sb.x, 6)
    expect(sa.y).toBeCloseTo(sb.y, 6)
    expect(sa.z).toBeCloseTo(sb.z, 6)

    // ...and it does turn something: the matrices are not equal.
    expect(Matrix4.equals(base, turned)).toBe(false)
  })

  it('heading 0 and heading 360 place a model identically', () => {
    const a = placementMatrix(113.99, 22.43, 4, 0, 1)
    const b = placementMatrix(113.99, 22.43, 4, 360, 1)
    // Compare through the translation, which is exact, and the rotation applied
    // to one vector, which absorbs float noise from the radian conversion.
    const ta = originOf(a)
    const tb = originOf(b)
    expect(ta.x).toBeCloseTo(tb.x, 6)
    expect(ta.y).toBeCloseTo(tb.y, 6)
    expect(ta.z).toBeCloseTo(tb.z, 6)

    const probe = new Cartesian3(1, 0, 0)
    const ra = Matrix4.multiplyByPointAsVector(a, probe, new Cartesian3())
    const rb = Matrix4.multiplyByPointAsVector(b, probe, new Cartesian3())
    expect(ra.x).toBeCloseTo(rb.x, 5)
    expect(ra.y).toBeCloseTo(rb.y, 5)
    expect(ra.z).toBeCloseTo(rb.z, 5)
  })
})

describe('ONLINE_PROPS', () => {
  it('every prop names a distinct id and a loadable URL', () => {
    const ids = ONLINE_PROPS.map((p) => p.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const prop of ONLINE_PROPS) {
      // The scheme matters: a relative path would resolve against the dev
      // server, which does not serve the Khronos set, and the failure would
      // look like a CORS problem.
      expect(prop.url).toMatch(/^https:\/\//)
      expect(prop.scale).toBeGreaterThan(0)
    }
  })

  it('every prop has a positive nominal height, so its scale is meaningful', () => {
    for (const prop of ONLINE_PROPS) {
      expect(prop.nominalMetres).toBeGreaterThan(0)
      expect(prop.heightM).toBeGreaterThanOrEqual(0)
    }
  })
})
