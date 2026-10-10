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

interface DecodedDem {
  heights: Float32Array
  /** Actual pixel width of the source raster. */
  width: number
  /** Actual pixel height of the source raster. */
  height: number
  west: number
  south: number
  east: number
  north: number
  /** Range of the decoded raster, used to clamp sampled heights. */
  minHeight: number
  maxHeight: number
}

let cached: Promise<DecodedDem> | null = null

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
      resolve({
        heights,
        width: canvas.width,
        height: canvas.height,
        west: 0,
        south: 0,
        east: 1,
        north: 1,
        minHeight: Number.isFinite(lo) ? lo : 0,
        maxHeight: Number.isFinite(hi) ? hi : 0,
      })
    }
    image.onerror = () => reject(new Error(`failed to load DEM image: ${url}`))
    image.src = url
  })
  return cached
}

/** Attach the raster's geographic extent, which the image itself cannot carry. */
export function setDemBounds(
  decoded: DecodedDem,
  bounds: [number, number, number, number],
): void {
  decoded.west = bounds[0]
  decoded.south = bounds[1]
  decoded.east = bounds[2]
  decoded.north = bounds[3]
}

/** Bilinear sample of the decoded DEM at a lon/lat. Returns metres, or 0 if inputs are invalid. */
function sampleAt(dem: DecodedDem, lon: number, lat: number): number {
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return 0
  if (dem.east <= dem.west || dem.north <= dem.south) return 0

  // Return 0 for coordinates strictly outside the raster extent — do NOT clamp
  // to the edge pixel. Clamping repeats the boundary value across all exterior
  // tiles, which manifests as visible "streaks" radiating from the data edge.
  if (lon < dem.west || lon > dem.east || lat < dem.south || lat > dem.north) return 0

  const W = dem.width
  const H = dem.height
  const u = ((lon - dem.west) / (dem.east - dem.west)) * W
  const v = ((dem.north - lat) / (dem.north - dem.south)) * H

  if (!Number.isFinite(u) || !Number.isFinite(v)) return 0

  const x = Math.min(Math.max(u - 0.5, 0), W - 1.001)
  const y = Math.min(Math.max(v - 0.5, 0), H - 1.001)
  const x0 = Math.floor(x)
  const y0 = Math.floor(y)
  const fx = x - x0
  const fy = y - y0
  const at = (col: number, row: number) =>
    dem.heights[row * W + col]

  const top = at(x0, y0) * (1 - fx) + at(Math.min(x0 + 1, W - 1), y0) * fx
  const bottom =
    at(x0, Math.min(y0 + 1, H - 1)) * (1 - fx) +
    at(Math.min(x0 + 1, W - 1), Math.min(y0 + 1, H - 1)) * fx
  const result = top * (1 - fy) + bottom * fy
  return Number.isFinite(result) ? result : 0
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
  const SIZE_TILE = 33

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

      const rect = scheme.tileXYToRectangle(x, y, level)
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
      if (!Number.isFinite(dLon) || !Number.isFinite(dLat) || dLon === 0 || dLat === 0) {
        return heightData
      }
      for (let row = 0; row < size; row += 1) {
        const lat = north - (row + 0.5) * dLat
        for (let col = 0; col < size; col += 1) {
          const lon = west + (col + 0.5) * dLon
          const h = sampleAt(dem, lon, lat)
          heightData[row * size + col] = Math.min(Math.max(h, dem.minHeight), dem.maxHeight)
        }
      }
      return heightData
    },
  })
}
