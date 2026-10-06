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
  EllipsoidTerrainProvider,
  ImageryLayer,
  OpenStreetMapImageryProvider,
  Rectangle,
  SingleTileImageryProvider,
  UrlTemplateImageryProvider,
  type TerrainProvider,
} from 'cesium'

import type { Polygon, TerrainMeta } from '@/api/types'

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
function isQuantizedMesh(url: string): boolean {
  return /\.(terrain|json)(\?|$)/i.test(url)
}

/**
 * NOTE (Cesium 1.146): `CesiumTerrainProvider`'s ConstructorOptions no longer
 * exposes a `url`, so a real terrain endpoint cannot be wired from config with
 * the installed version. Rather than cast around the type and fail at runtime,
 * real terrain is reported as unsupported here. Reinstating it needs either a
 * Cesium version whose provider accepts a URL, or a small wrapper -- and either
 * way it is blocked behind having a DEM at all (see docs/OCT6_STATUS.md D1).
 */

/**
 * Build the terrain provider for a scene.
 *
 * A bare `s3://` or `.tif` reference is refused rather than handed to Cesium:
 * the spec's storage target is a COG, which Cesium cannot consume directly, and
 * a provider that fails silently produces an empty globe with no clue why.
 */
export function createTerrainProvider(meta: TerrainMeta): TerrainBinding {
  const url = meta.terrainUrl?.trim()

  if (!url) {
    return {
      terrainProvider: new EllipsoidTerrainProvider(),
      terrainIsReal: false,
      terrainNote:
        'No DEM loaded — flat surface. The Lands Dept 5 m DTM was not ' +
        'downloaded (~290 MB, needs permission, and is not bare-earth).',
    }
  }

  if (/^s3:\/\//i.test(url) || /\.(tif|tiff)$/i.test(url)) {
    return {
      terrainProvider: new EllipsoidTerrainProvider(),
      terrainIsReal: false,
      terrainNote:
        `terrainUrl is a raster/COG reference (${url}). Cesium needs terrain ` +
        'tiles or a quantized-mesh service; converting needs gdal_translate, ' +
        'which is not installed on this host.',
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

  return {
    terrainProvider: new EllipsoidTerrainProvider(),
    terrainIsReal: false,
    terrainNote: `Unrecognised terrainUrl format, showing flat: ${url}`,
  }
}

/** Build the base imagery layer. OSM by default, so no token is ever required. */
export function createBaseImageryLayer(meta: TerrainMeta): ImageryLayer {
  const url = meta.imageryUrl?.trim()

  if (!url) {
    // Cesium >= 1.124 takes no constructor options for the OSM provider; it
    // serves the standard tile set. The template constant is kept for the
    // `imageryUrl` fallback path and for documentation.
    return new ImageryLayer(new OpenStreetMapImageryProvider({}))
  }

  if (url.includes('{z}') && url.includes('{x}')) {
    return new ImageryLayer(new UrlTemplateImageryProvider({ url }))
  }

  // A non-templated URL is treated as a single tile (COG-derived PNG overlays
  // are served exactly this way, and Cesium needs a Rectangle for those).
  return new ImageryLayer(new UrlTemplateImageryProvider({ url }))
}

/**
 * Convert a bbox polygon to a Cesium `Rectangle`.
 *
 * A `SingleTileImageryProvider` needs an explicit rectangle — the image knows
 * nothing about where it belongs — so deriving it here (rather than in the
 * viewer) keeps the two overlay layers provably aligned with the bbox.
 */
export function polygonToRectangle(bbox: Polygon | null | undefined): Rectangle | null {
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
 */
export function createOverlayLayer(
  url: string | null | undefined,
  rectangle: Rectangle | null,
  alpha: number,
): ImageryLayer | null {
  if (!url || !rectangle) return null

  // Z-order follows insertion order: overlays are added after the base layer,
  // so they sit on top. ImageryLayer in Cesium 1.146 exposes `alpha` but no
  // `imageryIndex`, so ordering is managed by when the layer is added.
  return new ImageryLayer(new SingleTileImageryProvider({ url, rectangle }), { alpha })
}

export { OSM_ATTRIBUTION, OSM_TILE_TEMPLATE }
