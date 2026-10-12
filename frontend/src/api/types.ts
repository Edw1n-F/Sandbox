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
  bbox: Polygon | null
  center?: Point | null
  terrainFormat?: string | null
  terrainSource?: string | null
  wetGridUrl?: string | null
  heatmapUrl?: string | null
  /**
   * 3D massing envelopes, plus their provenance. `status` is deliberately blunt
   * -- these are procedural massing derived from land cover, not surveyed
   * buildings -- and the panel shows it rather than letting a skyline imply
   * otherwise.
   */
  cityModelUrl?: string | null
  cityModelMeta?: CityModelMeta | null
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

/**
 * How the scene is dressed, as opposed to what is in it.
 *
 * `survey` is the daylight planning view; `tactical` is the dark instrument view
 * with neon buildings. Both render the same data — a mode changes the renderer,
 * never the dataset, which is why this is not a field on the scene record.
 * Defined here beside `LayerVisibility` rather than in the view layer, because
 * the store owns it and `src/view/` must not be imported from state.
 */
export type ViewMode = 'survey' | 'tactical'

/** Which overlay layers are switched on. Rendered by `LayerToggle`. */
export interface LayerVisibility {  imagery: boolean
  boundary: boolean
  wetGrid: boolean
  heatmap: boolean
  /**
   * Discrete features (projects, interventions) render as geometry rather than
   * as a raster overlay, because extrusion and picking only work on entities
   * (ADR-032). Separate switch for that reason, not for presentation.
   */
  interventions: boolean
  /**
   * The 3D massing model. On by default, because an intervention floating over
   * empty grass gives the user nothing to judge it against.
   */
  cityModel: boolean
  /** Online glTF assets fetched from the Khronos sample set. */
  props: boolean
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


/**
 * One side of a live run's ledger: today (`baseline`) or the scenario.
 *
 * `heatMean` is the land-class index the ledger has always reported;
 * `heatBlendedMean` is the same field after the sky-view and shade blend the
 * rendered overlay actually shows. Both are carried because they answer
 * different questions -- "how hot is this land cover?" and "what does the
 * picture the planner is looking at mean?" -- and collapsing them would make
 * the number disagree with the map.
 */
export interface RunLedgerSide {
  runoffM3: number
  unstoredM3: number
  heatMean: number
  greenAreaM2: number
  pavedAreaM2: number
  wetAreaHa: number
  heatBlendedMean: number
}

/** The result of one live simulation run (`POST /scenes/{id}/run`). */
export interface RunResult {
  runId: string
  sceneId: string
  inputs: {
    rainfallMm: number
    temperatureC: number
    heatPreset: 'normal' | 'heatwave'
    planId: string | null
  }
  /** Cache-busted by `runId`, so a new run always repaints. */
  overlays: { wetGridUrl: string; heatmapUrl: string }
  stats: {
    baseline: RunLedgerSide
    scenario: RunLedgerSide
    delta: {
      unstoredM3: number
      runoffM3: number
      wetAreaHa: number
      heatMean: number
      heatBlendedMean: number
      greenAreaM2: number
      pavedAreaM2: number
    }
  }
  worsens: boolean
  interventionsApplied: Array<{
    id: string | null
    kind: string
    areaM2: number
    cellsChanged: number
  }>
  interventionsSkipped: Array<{
    id: string | null
    kind: string
    why: string
    coverageFraction?: number
  }>
  explanation: string
  /**
   * `template` means no model was configured and the paragraph was assembled
   * by code. It is reported rather than hidden, for the same reason the AI
   * plan badge names the deterministic parser: a demo with no key must not
   * read as if a model wrote it.
   */
  explanationSource: 'anthropic' | 'openai-compatible' | 'template'
  status: string
  warning: string
}

/** Provenance for the 3D massing model, as served alongside the rasters. */
export interface CityModelMeta {
  status: string
  features: number
  heightM: { min: number; max: number; mean: number }
  byClass: Record<string, number>
  footprintSource: string
  heightSource: string
  whyNotOsm: string
  editable: boolean
  note: string
}
