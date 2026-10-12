/**
 * Terrain + imagery provider construction (spec §1.1 `view/terrainLoader.ts`).
 *
 * Two rules drive everything here:
 *
 * 1. **No Cesium Ion.** The default `Viewer` pulls Bing through Cesium Ion and
 *    renders a broken globe without a token. Imagery comes from OpenStreetMap
 *    instead, so the demo runs with zero credentials (ADR-033). A token is read
 *    from `VITE_CESIUM_ION_TOKEN` if one is ever supplied and is never
 *    committed — `frontend/.env.local` is gitignored.
 * 2. **Degrade honestly.** `terrainUrl` is `null` on Oct 6, because no DEM is
 *    downloadable without permission (see `backend/data/scene_hsk.json`).
 *    Rather than inventing terrain we fall back to `EllipsoidTerrainProvider`
 *    and *report that fact*, because a flat surface that is honestly labelled
 *    beats a fake hill nobody can defend.
 */
import {
  Math as CesiumMath,
  EllipsoidTerrainProvider,
  ImageryLayer,
  OpenStreetMapImageryProvider,
  Rectangle,
  SingleTileImageryProvider,
  UrlTemplateImageryProvider,
  type TerrainProvider,
} from 'cesium'

import type { Polygon, TerrainMeta } from '@/api/types'
import {
  createDemTerrainProvider,
  decodeTerrainRgb,
  setDemBounds,
} from './demProvider'

const OSM_TILE_TEMPLATE = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const OSM_ATTRIBUTION = '© OpenStreetMap contributors'

export interface TerrainBinding {
  terrainProvider: TerrainProvider
  /** False when we fell back to a flat surface. The UI must disclose this. */
  terrainIsReal: boolean
  /** Human-readable reason for any fallback, or null when terrain is real. */
  terrainNote: string | null
}

export function hasIonToken(): boolean {
  return Boolean(import.meta.env.VITE_CESIUM_ION_TOKEN)
}

/**
 * Recognise a Cesium quantized-mesh terrain endpoint (`.../{z}/{x}/{y}.terrain`).
 * The backend publishes `terrainUrl` as an opaque reference and does not encode
 * the format, so the convention is detected rather than assumed.
 */
function isQuantizedMesh(url: string | undefined): boolean {
  return /\.(terrain|json)(\?|$)/i.test(url ?? '')
}

/**
 * NOTE (Cesium 1.146): `CesiumTerrainProvider`'s ConstructorOptions no longer
 * exposes a `url`, so a real terrain endpoint cannot be wired from config with
 * the installed version. Rather than cast around the type and fail at runtime,
 * real terrain is reported as unsupported here. Reinstating it needs either a
 * Cesium version whose provider accepts a URL, or a small wrapper -- and either
 * way it is blocked behind having a DEM at all (see docs/ARCHITECTURE.md).
 */

/**
 * Build the terrain provider for a scene.
 *
 * Resolution order:
 *   1. `terrainFormat === 'terrain-rgb'` + a PNG -> decode it into a real
 *      heightmap. This is the path the open-DEM pipeline actually uses.
 *   2. A quantized-mesh endpoint -> reported as unsupported, because
 *      Cesium 1.146's `CesiumTerrainProvider` no longer accepts a `url`.
 *   3. A bare `s3://` / `.tif` COG -> refused, with a reason. Cesium cannot
 *      consume a COG and a provider that fails silently gives an empty globe
 *      with no clue why.
 *   4. Nothing -> flat surface, disclosed in the UI.
 *
 * **Async because decoding is async.** Decoding a terrain-RGB raster means
 * fetching an image and reading pixels; there is no synchronous version of this
 * that does not lie about being ready.
 */
export async function createTerrainProvider(
  meta: TerrainMeta,
  bbox: Polygon | null,
): Promise<TerrainBinding> {
  const url = meta.terrainUrl?.trim()

  if (url && /\.png($|\?)/i.test(url)) {
    try {
      const dem = await decodeTerrainRgb(url)
      const ring = polygonToRectangle(bbox)
      if (ring) {
        // The image cannot carry its own extent, so it comes from the scene's
        // own bbox. Both are derived from `scene_hsk.json`, so they agree by
        // construction rather than by two people typing the same numbers --
        // and reusing polygonToRectangle means the terrain and the overlay
        // layers are georeferenced from the identical code path.
        setDemBounds(dem, [
          CesiumMath.toDegrees(ring.west),
          CesiumMath.toDegrees(ring.south),
          CesiumMath.toDegrees(ring.east),
          CesiumMath.toDegrees(ring.north),
        ])
      }
      return {
        terrainProvider: createDemTerrainProvider(dem),
        terrainIsReal: true,
        terrainNote: null,
      }
    } catch (err) {
      return {
        terrainProvider: new EllipsoidTerrainProvider(),
        terrainIsReal: false,
        terrainNote:
          `terrainUrl is a terrain-RGB raster (${url}) but it could not be ` +
          `decoded, so the surface is flat. Cause: ${String(err)}`,
      }
    }
  }

  if (isQuantizedMesh(url)) {
    return {
      terrainProvider: new EllipsoidTerrainProvider(),
      terrainIsReal: false,
      terrainNote:
        `terrainUrl looks like a Cesium terrain endpoint (${url}), but ` +
        'Cesium 1.146 does not accept a terrain URL from configuration. ' +
        'Showing a flat surface.',
    }
  }

  if (/^s3:\/\//i.test(url ?? '') || /\.(tif|tiff)$/i.test(url ?? '')) {
    return {
      terrainProvider: new EllipsoidTerrainProvider(),
      terrainIsReal: false,
      terrainNote:
        `terrainUrl is a raster/COG reference (${url}). Cesium needs terrain ` +
        'tiles or a quantized-mesh service; run fetch_dem.py to publish a ' +
        'terrain-RGB raster instead.',
    }
  }

  return {
    terrainProvider: new EllipsoidTerrainProvider(),
    terrainIsReal: false,
    terrainNote:
      'No DEM loaded \u2014 flat surface. Run `python scripts/fetch_dem.py` to ' +
      'fetch real 1 arc-second elevation and publish it as terrain-RGB.',
  }
}

/** Build the base imagery layer. OSM by default, so no token is ever required. */
export function createBaseImageryLayer(meta: TerrainMeta): ImageryLayer {
  const url = meta.imageryUrl?.trim()

  if (!url) {
    // Cesium >= 1.124 takes no constructor options for the OSM provider; it
    // serves the standard tile set.
    return new ImageryLayer(new OpenStreetMapImageryProvider({}))
  }

  // Both a `{z}/{x}/{y}` template and a single-tile URL resolve through
  // UrlTemplateImageryProvider; overlays also arrive this way.
  return new ImageryLayer(new UrlTemplateImageryProvider({ url }))
}

/**
 * Convert a bbox polygon to a Cesium `Rectangle`.
 *
 * A `SingleTileImageryProvider` needs an explicit rectangle — the image knows
 * nothing about where it belongs — so deriving it here (rather than in the
 * viewer) keeps the two overlay layers provably aligned with the bbox.
 */
export function polygonToRectangle(
  bbox: Polygon | null | undefined,
): Rectangle | null {
  if (!bbox || bbox.type !== 'Polygon') return null

  const outer = bbox.coordinates?.[0]
  if (!outer || outer.length === 0) return null

  let west = Number.POSITIVE_INFINITY
  let east = Number.NEGATIVE_INFINITY
  let south = Number.POSITIVE_INFINITY
  let north = Number.NEGATIVE_INFINITY

  for (const position of outer) {
    const lon = position[0]
    const lat = position[1]
    if (lon < west) west = lon
    if (lon > east) east = lon
    if (lat < south) south = lat
    if (lat > north) north = lat
  }

  if (![west, east, south, north].every(Number.isFinite)) return null
  return Rectangle.fromDegrees(west, south, east, north)
}

/**
 * Build a georeferenced overlay layer from a PNG produced by
 * `scripts/make_wetgrid.py` / `scripts/make_heatmap.py`.
 *
 * `SingleTileImageryProvider` is the right technique for a continuous field
 * (ADR-032): one georeferenced image beats thousands of extruded polygons, both
 * in payload size and in frame rate. Extrusion is reserved for discrete
 * features such as projects and interventions.
 *
 * **Resolution is async and that is not optional.** The provider starts with no
 * image; adding such a layer to a scene throws a bare `DeveloperError` from
 * Cesium's render loop, which surfaces as an empty-stack page error and takes
 * the React tree down with it. `fromUrl` resolves only once the image has
 * loaded, so the layer is added to a ready provider or not at all.
 */
export async function createOverlayLayer(
  url: string | null | undefined,
  rectangle: Rectangle | null,
  alpha: number,
): Promise<ImageryLayer | null> {
  if (!url || !rectangle) return null
  const provider = await SingleTileImageryProvider.fromUrl(url, { rectangle })
  return new ImageryLayer(provider, { alpha })
}

export { OSM_ATTRIBUTION, OSM_TILE_TEMPLATE }
