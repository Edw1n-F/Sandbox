/**
 * GeoJSON + contract types (spec §3).
 *
 * Kept hand-written rather than pulled from a GeoJSON package: the shapes in
 * play are four of them, and an explicit type is what stops a coordinate
 * ordering mistake from compiling.
 */
export interface Point {
  type: 'Point'
  coordinates: [number, number]
}

export interface Polygon {
  type: 'Polygon'
  /** List of rings. The first is the outer ring and is explicitly closed. */
  coordinates: number[][][]
}

export type Geometry = Point | { type: 'LineString'; coordinates: number[][] }

export interface DefaultCamera {
  longitude?: number | null
  latitude?: number | null
  height: number
  heading: number
  pitch: number
}

export interface EnvParams {
  temperature?: number | null
  precipitation?: number | null
  [key: string]: unknown
}

export interface Scene {
  sceneId: string
  name: string
  center: Point
  bbox: Polygon | null
  terrainUrl: string | null
  imageryUrl: string | null
  defaultCamera: DefaultCamera
  envParams: EnvParams
  createdAt: string
  updatedAt: string
  wetGridUrl?: string | null
  heatmapUrl?: string | null
  /** Set client-side by `terrainLoader` so the UI can disclose a DEM fallback. */
  terrainIsReal?: boolean
  terrainNote?: string | null
}

export interface TerrainMeta {
  terrainUrl: string | null
  imageryUrl: string | null
  /** [lonMin, latMin, lonMax, latMax] — the tight rectangle the imagery tile covers. */
  imageryBbox: [number, number, number, number] | null
  bbox: Polygon | null
  center?: Point | null
  terrainFormat?: string | null
  terrainSource?: string | null
  wetGridUrl?: string | null
  heatmapUrl?: string | null
  /** Real 3D Tiles tileset with textured meshes from 6-NW-12D. */
  cityModel3DUrl?: string | null
  /** Alias: the API serialises as camelCase; accept both spellings. */
  cityModel3dUrl?: string | null
  waterbodyUrl?: string | null
  infrastructureUrl?: string | null
}

export type PlanType =
  | 'baseline'
  | 'economic'
  | 'environmental'
  | 'comprehensive'
  | 'custom'

export interface Plan {
  planId: string
  sceneId: string
  planType: PlanType
  label: string | null
  source: 'manual' | 'ai'
  nlInstruction: string | null
  params: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

export interface InterventionProperties {
  id: string | null
  interventionType: string
  style: {
    color?: string
    opacity?: number
    extrusionHeight?: number
    [key: string]: unknown
  }
  source: 'manual' | 'ai'
  status: 'draft' | 'validated'
  params: Record<string, unknown>
}

export interface InterventionFeature {
  type: 'Feature'
  id?: string | null
  properties: InterventionProperties
  geometry: Geometry | Polygon
}

/** A valid, renderable answer even when empty — the frontend must still draw. */
export interface FeatureCollection {
  type: 'FeatureCollection'
  features: InterventionFeature[]
}

/** Which overlay layers are switched on. Rendered by `LayerToggle`. */
export interface LayerVisibility {
  imagery: boolean
  boundary: boolean
  wetGrid: boolean
  heatmap: boolean
  /**
   * Discrete features (projects, interventions) render as geometry rather than
   * as a raster overlay, because extrusion and picking only work on entities
   * (ADR-032). Separate switch for that reason, not for presentation.
   */
  interventions: boolean
  /** Real 3D buildings from 6-NW-12D 3D Tiles (textured meshes). */
  realBuildings: boolean
  /** Online glTF assets fetched from the Khronos sample set. */
  props: boolean
  /** Water bodies from 6-NW-12D WATERBODY glTF. */
  waterbody: boolean
  /** Infrastructure structures from 6-NW-12D INFRASTRUCTURE glTF. */
  infrastructure: boolean
}

/**
 * A proposal produced by the AI module.
 *
 * The shape is deliberately identical to a hand-drawn intervention: same
 * geometry, same `style`, same validation. That is the point — §3.3.2 requires
 * user-drawn and AI-proposed features to share one
 * normalise/validate/render pipeline, so nothing downstream needs to know
 * which produced a feature except the `source` flag.
 */
export type InterventionSource = 'manual' | 'ai'


