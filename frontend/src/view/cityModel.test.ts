/**
 * Unit tests for the plan-scoping geometry.
 *
 * `pointInRing` has to agree with the backend's `_point_in_ring` closely enough that a
 * scoped massing op hits the same buildings on both sides. When it drifts, the failure
 * is not an error — the op silently applies to the entire district or to nothing, and
 * the panel reports a plausible changed count either way. That is the class of bug this
 * file exists to make impossible to miss.
 */
import { describe, expect, it } from 'vitest'

import { pointInRing, ringCentroid, rotateRing, translateRing } from './cityModel'

/** A closed, axis-aligned rectangle ring, the shape every generated building has. */
const square = (west: number, south: number, east: number, north: number): number[][] => [
  [west, south],
  [east, south],
  [east, north],
  [west, north],
  [west, south],
]

// ~100 m across, which is the real order of magnitude of a generated block.
const RING = square(113.99, 22.44, 113.991, 22.441)

describe('pointInRing', () => {
  it('accepts a point inside', () => {
    expect(pointInRing(113.9905, 22.4405, RING)).toBe(true)
  })

  it('rejects a point outside', () => {
    expect(pointInRing(113.995, 22.44, RING)).toBe(false)
    expect(pointInRing(113.99, 22.45, RING)).toBe(false)
  })

  it('is stable across the ring winding', () => {
    // A GeoJSON ring may arrive clockwise or anticlockwise. Scoping must not depend on
    // which, or the same plan touches different buildings in different runs.
    const reversed = [...RING].reverse()
    expect(pointInRing(113.9905, 22.4405, reversed)).toBe(true)
    expect(pointInRing(113.995, 22.44, reversed)).toBe(false)
  })

  it('handles a concave shape without leaking', () => {
    // An L-shape: the notch must read as outside, which naive ray casting gets wrong
    // when the crossing parity is miscounted at a vertex.
    const ell: number[][] = [
      [0, 0],
      [2, 0],
      [2, 1],
      [1, 1],
      [1, 2],
      [0, 2],
      [0, 0],
    ]
    expect(pointInRing(0.5, 0.5, ell)).toBe(true)
    expect(pointInRing(1.5, 1.5, ell)).toBe(false)
  })

  it('is not fooled by a degenerate ring', () => {
    expect(pointInRing(1, 1, [[0, 0]])).toBe(false)
    expect(pointInRing(1, 1, [])).toBe(false)
  })
})

describe('ringCentroid', () => {
  it('finds the middle of a square', () => {
    const [lon, lat] = ringCentroid(RING)
    expect(lon).toBeCloseTo(113.9905, 6)
    expect(lat).toBeCloseTo(22.4405, 6)
  })

  it('does not double-count the repeated closing vertex', () => {
    // A closed ring repeats its first point. Averaging it twice pulls the centroid
    // towards that corner, so the centroid has to be the mean of the unique vertices
    // only. (An earlier version of this test compared against `slice(0, 4)`, which
    // drops a *real* vertex rather than the closure and so was not equivalent.)
    const unique = RING.slice(0, -1)
    const meanLon = unique.reduce((a, p) => a + p[0], 0) / unique.length
    const meanLat = unique.reduce((a, p) => a + p[1], 0) / unique.length
    const [lon, lat] = ringCentroid(RING)
    expect(lon).toBeCloseTo(meanLon, 9)
    expect(lat).toBeCloseTo(meanLat, 9)
  })

  it('survives a degenerate ring', () => {
    expect(ringCentroid([])).toEqual([0, 0])
  })
})

describe('translateRing', () => {
  it('moves a ring east and north by the requested metres', () => {
    // At 22.4 deg N, 1 deg of longitude is ~102.9 km and 1 deg of latitude ~110.6 km.
    const moved = translateRing(RING, 100, 200)
    expect(moved[0][0] - RING[0][0]).toBeCloseTo(100 / 102900, 6)
    expect(moved[0][1] - RING[0][1]).toBeCloseTo(200 / 110600, 6)
  })

  it('keeps the ring closed', () => {
    const moved = translateRing(RING, 50, -50)
    expect(moved[0]).toEqual(moved[moved.length - 1])
  })

  it('preserves the shape rather than rescaling it', () => {
    // A rotation bug in a translate would shear or resize the footprint, quietly
    // inflating every area in the plan.
    const moved = translateRing(RING, 10, 10)
    const before = RING[1][0] - RING[0][0]
    const after = moved[1][0] - moved[0][0]
    expect(after).toBeCloseTo(before, 9)
  })

  it('is a no-op for zero displacement', () => {
    expect(translateRing(RING, 0, 0)).toEqual(RING)
  })
})

describe('rotateRing', () => {
  it('keeps the centroid fixed', () => {
    // The centroid is the anchor, so a rotated building stays on its own plot. If it
    // drifted, rotating a whole district would walk it across the study area.
    const before = ringCentroid(RING)
    const after = ringCentroid(rotateRing(RING, 37))
    expect(after[0]).toBeCloseTo(before[0], 6)
    expect(after[1]).toBeCloseTo(before[1], 6)
  })

  it('preserves the side lengths, so the footprint neither shears nor resizes', () => {
    // Rotating in degree space shears by cos(lat) -- a 7% distortion at 22.4 N -- which
    // is why the implementation rotates in metres. Side lengths catch that directly.
    // (A shoelace area was tried first and is meaningless in degrees: the figure it
    // produces is not an area and its absolute value says nothing.)
    // The same metres-per-degree the implementation uses. Using a rounder number here
    // produced a 0.024 m discrepancy on a 103 m side that had nothing to do with shear.
    const kx = 111_320 * Math.cos((22.4405 * Math.PI) / 180)
    const ky = 110_574
    const sides = (r: number[][]) => {
      const pts = r.slice(0, -1)
      return pts
        .map((p, i) => {
          const q = pts[(i + 1) % pts.length]
          return Math.hypot((q[0] - p[0]) * kx, (q[1] - p[1]) * ky)
        })
        .sort((a, b) => a - b)
    }
    const before = sides(RING)
    const after = sides(rotateRing(RING, 90))
    expect(after).toHaveLength(before.length)
    // 0.05 m tolerance. Enough for the implementation's 7-decimal degree rounding,
    // and two orders of magnitude tighter than the ~7 m a cos(lat) shear would cause.
    for (let i = 0; i < before.length; i += 1) {
      expect(after[i]).toBeCloseTo(before[i], 1)
    }
  })

  it('keeps the ring closed', () => {
    const rotated = rotateRing(RING, 90)
    expect(rotated[0]).toEqual(rotated[rotated.length - 1])
  })

  it('is roughly a no-op at 360 degrees', () => {
    const rotated = rotateRing(RING, 360)
    for (let i = 0; i < RING.length; i += 1) {
      expect(rotated[i][0]).toBeCloseTo(RING[i][0], 6)
      expect(rotated[i][1]).toBeCloseTo(RING[i][1], 6)
    }
  })
})
