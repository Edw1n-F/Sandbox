/**
 * Real terrain for Cesium, decoded from a mapbox terrain-RGB raster.
 *
 * ## Why not `CesiumTerrainProvider`
 *
 * Cesium's stock provider wants quantized-mesh, which needs a tiling service.
 * The Lands Dept quantized-mesh tiles need approval we do not have, and Cesium
 * ion needs a token we deliberately do not use (ADR-033). So this serves a
 * single terrain-RGB image instead: the whole study area is 2.4 x 3.3 km, which
 * is a handful of requests as one image rather than a tile pyramid.
 *
 * ## Why this is not a cop-out
 *
 * `CustomHeightmapTerrainProvider` is a first-class Cesium API, not a
 * workaround. It asks for a heightmap per requested tile; this decodes the PNG
 * once, then slices it. The terrain is real 1 arc-second elevation (see
 * `backend/scripts/fetch_dem.py`) -- what is synthetic is nothing.
 *
 * ## The decode
 *
 * mapbox terrain-RGB: `metres = (R * 65536 + G * 256 + B) * 0.1 - 32768`.
 * Decoded once into a Float32Array, then sampled bilinearly per tile, so the
 * render loop never touches image data.
 */

import {
  Math as CesiumMath,

  CustomHeightmapTerrainProvider,
  WebMercatorTilingScheme,
} from 'cesium'

export interface DecodedDem {
  heights: Float32Array
  /** Pixel dimensions of `heights`, which are not square. */
  width: number
  height: number
  west: number
  south: number
  east: number
  north: number
  /** Range of the decoded raster, reported in the panel and used to frame the camera. */
  minHeight: number
  maxHeight: number
}

let cached: Promise<DecodedDem> | null = null

/**
 * The settled raster, kept so it can be sampled synchronously.
 *
 * A separate handle rather than awaiting `cached` at every call site: height
 * sampling happens inside a `forEach` over 2,639 buildings, and a promise per
 * envelope would turn one array read into 2,639 microtasks. Null until the
 * decode finishes, and null again if it failed — callers treat "no DEM" and "no
 * DEM yet" identically, which is what they must do, because the model can load
 * before the image does.
 */
let raster: DecodedDem | null = null

/**
 * Settles when `raster` does, or when the decode has definitively failed.
 *
 * Created eagerly rather than from `whenDemReady` because the resolver has to be
 * reachable from the image `onload` below, and a caller may well register before
 * any decode has started.
 */
let signalReady: () => void = () => undefined
const readyPromise = new Promise<void>((resolve) => {
  signalReady = resolve
})

/** Decode a terrain-RGB PNG URL into a Float32Array of metres. */
export function decodeTerrainRgb(url: string): Promise<DecodedDem> {
  if (cached) return cached
  cached = new Promise<DecodedDem>((resolve, reject) => {
    const image = new Image()
    // Same-origin in production; via the dev proxy in development.
    image.crossOrigin = 'anonymous'
    image.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = image.width
      canvas.height = image.height
      const ctx = canvas.getContext('2d', { willReadFrequently: true })
      if (!ctx) {
        reject(new Error('2d canvas context unavailable for DEM decode'))
        return
      }
      ctx.drawImage(image, 0, 0)
      const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height)
      const heights = new Float32Array(canvas.width * canvas.height)
      for (let i = 0, p = 0; i < heights.length; i += 1, p += 4) {
        heights[i] =
          (data[p] * 65536 + data[p + 1] * 256 + data[p + 2]) * 0.1 - 32768
      }
      let lo = Infinity
      let hi = -Infinity
      for (const value of heights) {
        if (value < lo) lo = value
        if (value > hi) hi = value
      }
      const decoded: DecodedDem = {
        heights,
        width: canvas.width,
        height: canvas.height,
        west: 0,
        south: 0,
        east: 1,
        north: 1,
        minHeight: Number.isFinite(lo) ? lo : 0,
        maxHeight: Number.isFinite(hi) ? hi : 0,
      }
      raster = decoded
      resolve(decoded)
    }
    image.onerror = () => {
      // Released as well as rejected: a caller waiting to ground its geometry must
      // not hang on a scene whose DEM will never arrive. It gets "no ground", which
      // it already has a defined answer for.
      signalReady()
      reject(new Error(`failed to load DEM image: ${url}`))
    }
    image.src = url
  })
  return cached
}

/**
 * Attach the raster's geographic extent, which the image itself cannot carry.
 *
 * This is also the moment the raster becomes *samplable*, so it is where the
 * readiness signal fires rather than in the image `onload`: a decode that finishes
 * a millisecond earlier still leaves `west`/`east` at their placeholder 0/1, and a
 * sample taken then is silently out of range rather than absent. Signalling on
 * decode meant every waiter lost that race and every envelope stayed at sea level.
 */
export function setDemBounds(
  decoded: DecodedDem,
  bounds: [number, number, number, number],
): void {
  decoded.west = bounds[0]
  decoded.south = bounds[1]
  decoded.east = bounds[2]
  decoded.north = bounds[3]
  signalReady()
}

/**
 * Ground height in metres at a lon/lat, or null if no DEM is available there.
 *
 * ## Why the buildings read the raster directly
 *
 * The obvious way to stand an envelope on the ground is
 * `heightReference = CLAMP_TO_GROUND`, and it is the wrong way here. Cesium
 * routes clamped geometry through its ground-primitive path, which forces
 * `PerInstanceColorAppearance` and so discards any custom material -- silently
 * deleting the tactical view's entire reason for existing. Sampling
 * `globe.getHeight()` instead is no better: it returns `undefined` until the
 * containing tile has loaded, and these tiles arrive lazily with the camera, so
 * base heights would keep moving underneath the model.
 *
 * Reading the decoded array sidesteps both. This DEM is one 256 px image over
 * the whole study area, decoded once and never refined, so a sample is exact and
 * stable — and a base height that does not move is what lets a height edit stay
 * a single property write instead of becoming a geometry rebuild.
 */
export function sampleDemHeight(lon: number, lat: number): number | null {
  if (!raster) return null
  if (lon < raster.west || lon > raster.east) return null
  if (lat < raster.south || lat > raster.north) return null
  const value = sampleAt(raster, lon, lat)
  return Number.isFinite(value) ? value : null
}

/**
 * Resolves once a DEM raster is available to sample, never rejects.
 *
 * ## Why this exists
 *
 * The city model and the terrain image load in parallel, and neither is reliably
 * first — the model is a large JSON, the image is a decode. Anything that needs a
 * ground height has to be able to *wait* for the raster rather than sample once
 * and hope, because a single early sample means sea level, and at 141 m of relief
 * sea level means every uphill envelope is buried inside a hill with no visible
 * error anywhere.
 *
 * Never rejects: a scene with no DEM is a supported configuration, and a caller
 * that had to catch would be a caller that could forget to.
 */
export function whenDemReady(): Promise<void> {
  return readyPromise
}

/**
 * Read rather than typed in. The relief decides how much vertical extent the
 * camera has to keep in frame, and "141 m" written into the camera code would be
 * a constant that quietly goes stale the next time the DEM is re-fetched over a
 * different bbox. The sidecar records the same number; this is the same source.
 */
export function demElevationRange(): { min: number; max: number } | null {
  if (!raster) return null
  return { min: raster.minHeight, max: raster.maxHeight }
}

/**
 * Bilinear sample of the decoded DEM at a lon/lat. Returns metres.
 *
 * ## Why the dimensions are read, not assumed
 *
 * This used to index a hard-coded 256 px grid. The shipped HSK raster is **28 x 37**
 * — deliberately, since it is resampled to roughly 85 m posts over a 2.4 x 3.3 km
 * bbox — so every sample read past the end of a 1,036-element array, came back
 * `undefined`, and silently reported "no terrain" for the whole district. Nothing
 * failed: the model rendered every envelope standing on sea level, which is exactly
 * what it had always done. The dimensions now travel with the data.
 */
/**
 * Exported for tests.
 *
 * This is a pure function with no I/O, and it is the exact function that once indexed
 * a hard-coded 256 px grid against a raster that is really 28 x 37 -- reading past the
 * end of the array, returning `undefined`, and reporting "no terrain" for the entire
 * district without throwing. It was unreachable from a test while module-private, so
 * the bug had nothing to catch it. `sampleDemHeight` cannot substitute: it reads the
 * module-level decoded raster, which only exists after a real image has loaded.
 */
export function sampleAt(dem: DecodedDem, lon: number, lat: number): number {
  const u = ((lon - dem.west) / (dem.east - dem.west)) * dem.width
  // Image row 0 is the northernmost pixel, so north maps to v = 0.
  const v = ((dem.north - lat) / (dem.north - dem.south)) * dem.height

  const lastX = dem.width - 1
  const lastY = dem.height - 1
  const x = Math.min(Math.max(u - 0.5, 0), lastX - 0.001)
  const y = Math.min(Math.max(v - 0.5, 0), lastY - 0.001)
  const x0 = Math.floor(x)
  const y0 = Math.floor(y)
  const fx = x - x0
  const fy = y - y0
  const at = (col: number, row: number) => dem.heights[row * dem.width + col]

  const top = at(x0, y0) * (1 - fx) + at(Math.min(x0 + 1, lastX), y0) * fx
  const bottom =
    at(x0, Math.min(y0 + 1, lastY)) * (1 - fx) +
    at(Math.min(x0 + 1, lastX), Math.min(y0 + 1, lastY)) * fx
  return top * (1 - fy) + bottom * fy
}

/**
 * Build a terrain provider over the decoded DEM.
 *
 * `outsideDemRange` returns null, which tells Cesium "no data here, fall back to
 * the parent level". That is what keeps the viewer correct when the camera
 * leaves the study area, instead of extruding a flat slab to the horizon.
 */
export function createDemTerrainProvider(
  dem: DecodedDem,
  credit?: string,
): CustomHeightmapTerrainProvider {
  const scheme = new WebMercatorTilingScheme()
  const SIZE_TILE = 32

  return new CustomHeightmapTerrainProvider({
    width: SIZE_TILE,
    height: SIZE_TILE,
    tilingScheme: scheme,
    credit: credit ?? 'Elevation: Open-Meteo / SRTM, 1 arc-second',
    /**
     * Cesium's `GeometryCallback` returns a flat typed array of `width * height`
     * heights.
     *
     * **Tiles outside the raster return zeros, not `undefined`.** Returning
     * `undefined` is the documented "no data" signal, but in Cesium 1.146 it
     * propagated a hole in the globe surface and the entire scene rendered
     * black -- terrain and base imagery both gone, with no console error to
     * explain it. A zero-filled heightmap is valid geometry at sea level, so
     * tiles outside the study area simply lie flat, which is both correct-looking
     * and honest: we have no elevation for Hong Kong as a whole, only for 2.4 km
     * of it.
     *
     * Heights are also clamped to the raster's own range. One bad sample
     * otherwise becomes an unbounded spike that tears the surface.
     */
    callback(x: number, y: number, level: number): Float32Array {
      const native = 2 ** level
      const size = SIZE_TILE
      const heightData = new Float32Array(size * size)
      if (x < 0 || y < 0 || x >= native || y >= native) return heightData

      const rect = scheme.tileXYToNativeRectangle(x, y, level)
      const west = CesiumMath.toDegrees(rect.west)
      const east = CesiumMath.toDegrees(rect.east)
      const north = CesiumMath.toDegrees(rect.north)
      const south = CesiumMath.toDegrees(rect.south)

      if (
        east < dem.west ||
        west > dem.east ||
        north < dem.south ||
        south > dem.north
      ) {
        return heightData
      }

      const dLon = (east - west) / size
      const dLat = (north - south) / size
      for (let row = 0; row < size; row += 1) {
        const lat = north - (row + 0.5) * dLat
        for (let col = 0; col < size; col += 1) {
          const lon = west + (col + 0.5) * dLon
          const h = sampleAt(dem, lon, lat)
          heightData[row * size + col] = Number.isFinite(h)
            ? Math.min(Math.max(h, dem.minHeight), dem.maxHeight)
            : 0
        }
      }
      return heightData
    },
  })
}
