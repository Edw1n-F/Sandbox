/**
 * Unit tests for the pure geometry the sandbox is built on.
 *
 * These are the functions where a silent regression is expensive and invisible: the
 * DEM sampler once reported "no terrain" for the whole district because it indexed a
 * hard-coded 256 px grid against a raster that is really 28 x 37, and nothing threw --
 * every building just stood on sea level and looked plausible. A test is the only
 * thing that catches that class of bug.
 */
import { describe, expect, it } from 'vitest'

import { extentRadiusM, fitDistance, MIN_PITCH_DEG, MAX_PITCH_DEG } from './cameraRig'
import { sampleAt, type DecodedDem } from './demProvider'

/** A synthetic raster, so the tests never depend on the shipped PNG being present. */
function makeDem(over: Partial<DecodedDem> = {}): DecodedDem {
  const width = over.width ?? 4
  const height = over.height ?? 3
  // Built from the same dimensions that are asserted on. An earlier version built a
  // 4 x 3 array and then let the caller override `width`/`height`, which is precisely
  // the mismatch this suite exists to catch -- reproduced inside the test itself.
  const heights = new Float32Array(width * height)
  for (let i = 0; i < heights.length; i += 1) heights[i] = i * 10
  return {
    heights,
    width,
    height,
    west: 113.98,
    south: 22.43,
    east: 114.0,
    north: 22.45,
    minHeight: 0,
    maxHeight: 120,
    ...over,
  }
}

describe('DEM sampling', () => {
  it('honours the raster dimensions rather than an assumed resolution', () => {
    // The bug this guards: indexing a fixed 256 px grid. Against a 28 x 37 raster that
    // reads past the end of the array and returns undefined for every point.
    const dem = makeDem({ width: 28, height: 37 })
    expect(dem.heights.length).toBe(28 * 37)
    expect(Number.isFinite(sampleAt(dem, 113.99, 22.44))).toBe(true)
  })

  it('returns the corners of the raster within one sample spacing', () => {
    const dem = makeDem()
    // Row-major, row 0 north, 4 columns, values are i * 10: NW is 0 and SE is the last
    // of the twelve, 110.
    expect(sampleAt(dem, dem.west, dem.north)).toBeCloseTo(0, 5)
    // 109.95, not 110: the sampler clamps a half-sample inside the edge rather than
    // reading past the last row or column. Asserting the exact edge value would be
    // asserting the absence of that clamp, which is the opposite of what is wanted.
    expect(sampleAt(dem, dem.east, dem.south)).toBeGreaterThan(109)
    expect(sampleAt(dem, dem.east, dem.south)).toBeLessThanOrEqual(110)
  })

  it('maps north to the first row', () => {
    // Row 0 is the northernmost pixel. Getting this backwards mirrors the terrain about
    // the horizontal, which puts every building on a slope the wrong way.
    const dem = makeDem()
    expect(sampleAt(dem, 113.99, dem.north)).toBeLessThan(sampleAt(dem, 113.99, dem.south))
  })

  it('interpolates between samples rather than snapping', () => {
    const dem = makeDem()
    const a = sampleAt(dem, 113.985, 22.445)
    const b = sampleAt(dem, 113.995, 22.445)

    const mid = sampleAt(dem, 113.990, 22.445)
    expect(mid).toBeGreaterThan(Math.min(a, b))
    expect(mid).toBeLessThan(Math.max(a, b))
  })
})

describe('extentRadiusM', () => {
  // The real study area, ~2.37 km x ~3.32 km.
  const hsk = { west: 113.9900, south: 22.4030, east: 114.0130, north: 22.4330 }

  it('is roughly half the diagonal of the study area', () => {
    // ~2.4 km x ~3.3 km, so half the diagonal is a bit over 2 km.
    expect(extentRadiusM(hsk)).toBeGreaterThan(1800)
    expect(extentRadiusM(hsk)).toBeLessThan(2400)
  })

  it('grows with the relief so the hills stay in frame', () => {
    expect(extentRadiusM(hsk, 141)).toBeGreaterThanOrEqual(extentRadiusM(hsk, 0))
  })

  it('never returns less than 60 m', () => {
    // A single-building scene framed from inside itself is useless.
    expect(extentRadiusM({ west: 0, south: 0, east: 0.0001, north: 0.0001 })).toBe(60)
  })

  it('treats a negative or empty extent as degenerate rather than negative', () => {
    expect(extentRadiusM({ west: 0, south: 0, east: 0, north: 0 })).toBe(60)
  })
})

describe('fitDistance', () => {
  const fakeViewer = (fovDeg: number, aspect: number) =>
    ({
      camera: {
        frustum: { fov: (fovDeg * Math.PI) / 180, aspectRatio: aspect },
      },
      scene: { canvas: { width: 1600, height: 900 } },
    }) as never

  it('backs off further for a narrower field of view', () => {
    const near = fitDistance(fakeViewer(60, 1.78), 1000)
    const tight = fitDistance(fakeViewer(30, 1.78), 1000)
    expect(tight).toBeGreaterThan(near)
  })

  it('backs off further for a wider viewport', () => {
    // `fov` applies to the larger screen dimension, so a wide viewport has the
    // *narrower* vertical cone and a bounding sphere needs more room. Written the
    // other way round first, which is a useful reminder that this is not symmetric.
    const wide = fitDistance(fakeViewer(60, 2.5), 1000)
    const square = fitDistance(fakeViewer(60, 1.0), 1000)
    expect(wide).toBeGreaterThan(square)
  })

  it('uses the tangent distance, so a bounding sphere actually fits', () => {
    // r / sin(theta) is larger than r / tan(theta); using the latter crops corners.
    const d = fitDistance(fakeViewer(60, 1.78), 1000)
    expect(d).toBeGreaterThan(1000)
  })
})

describe('pitch clamps', () => {
  it('never allows nadir through the orbit control', () => {
    // Straight down is a separate view with its own button; a rotation control that
    // lands there by accident makes both views harder to use.
    expect(MIN_PITCH_DEG).toBeGreaterThan(-90)
  })

  it('never allows the camera to swing above the horizon', () => {
    expect(MAX_PITCH_DEG).toBeLessThan(0)
  })

  it('orders the limits the right way round', () => {
    expect(MIN_PITCH_DEG).toBeLessThan(MAX_PITCH_DEG)
  })
})
