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
  durationHours?: number | null
  tideState?: TideState | null
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
  /** Building footprint GeoJSON (kept for backend analysis; not rendered as pillars). */
  buildingsUrl?: string | null
  buildingsMeta?: BuildingsMeta | null
  /** Real 3D Tiles tileset with textured meshes from 6-NW-12D. */
  buildings3DUrl?: string | null
  /** Alias: the API serialises as camelCase; accept both spellings. */
  buildings3dUrl?: string | null
  /** Visual terrain mesh from 6-NW-12D terrain(TB) 3D Tiles. */
  terrain3DUrl?: string | null
  waterbodyUrl?: string | null
  infrastructureUrl?: string | null
  planProjectUrl?: string | null
  planProjectFootprintUrl?: string | null
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

/** Screening tidal-backwater state applied to the D8 outfall. */
export type TideState = 'low' | 'mean' | 'high'

/** Which overlay layers are switched on, rendered by the viewer's layer pills. */
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
  /** Online glTF assets fetched from the Khronos sample set. */
  props: boolean
  /** Real 3D buildings from 6-NW-12D 3D Tiles (textured meshes). */
  realBuildings: boolean
  /** Water bodies from 6-NW-12D WATERBODY glTF. */
  waterbody: boolean
  /** Infrastructure structures from 6-NW-12D INFRASTRUCTURE glTF. */
  infrastructure: boolean
  planProject: boolean
}

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
  /** Routed cells with >= flood threshold standing water, in ha (null if routing unavailable). */
  floodAreaHa?: number | null
  heatBlendedMean: number
  /** Mean surface (skin) temperature, in C, anchored to observed LST. */
  surfaceMeanC: number
  /** Maximum surface temperature across the grid, in C. */
  surfaceMaxC: number
  /** Maximum deviation from the site mean, in C (the anomaly map's hot end). */
  anomalyMaxC: number
  /** Air-temperature estimate = surface minus a documented offset, in C. */
  airMeanC: number
  /** Total assumed usable storage across the grid, in m3. */
  storageCapacityM3?: number
  /** Paved share of all cells, 0-1. */
  imperviousShare?: number
  /** Green + water + wetland + floodable area, in m2. */
  greenWaterAreaM2?: number
}

/** The result of one live simulation run (`POST /scenes/{id}/run`). */
export interface RunResult {
  runId: string
  sceneId: string
  inputs: {
    rainfallMm: number
    temperatureC: number
    /** The scenario mean surface temperature (C) the slider set. */
    scenarioMeanC?: number
    tideState?: TideState
    planId: string | null
    /** The cumulative selection this run composed. Empty for a baseline run. */
    planIds?: string[]
  }
  /** Cache-busted by `runId`, and named per run, so a new run always repaints. */
  overlays: { wetGridUrl: string; heatmapUrl: string }
  /** The data this run stood on, as the backend loaded it. Absent on older runs. */
  sources?: { terrain: string; landCover: string; lst: string | null }
  stats: {
    /** The screening tidal-backwater condition this run applied, if any. */
    tide?: {
      state: TideState
      label: string
      tailwaterM: number
      source: string
      active: boolean
    }
    /** Surface-temperature context for the run (quantity, anchor, offset). */
    temperature?: {
      quantity: string
      unit: string
      observedMeanC: number | null
      scenarioMeanC: number
      surfaceToAirOffsetC: number
      source: string
      note: string
    }
    baseline: RunLedgerSide
    scenario: RunLedgerSide
    delta: {
      unstoredM3: number
      runoffM3: number
      wetAreaHa: number
      /** Change in routed flood extent, in ha (null if routing unavailable). */
      floodAreaHa?: number | null
      heatMean: number
      heatBlendedMean: number
      /** Change in mean surface temperature, in C. */
      surfaceMeanC: number
      /** Change in maximum surface temperature, in C. */
      surfaceMaxC: number
      greenAreaM2: number
      pavedAreaM2: number
      /** Change in total available storage: positive means the scenario adds it. */
      storageCapacityM3?: number
      /** Change in impervious fraction, 0-1. */
      imperviousShare?: number
      /** Change in green-and-water area (green + water + wetland + floodable). */
      greenWaterAreaM2?: number
    }
    /**
     * Derived shares the run publishes so a card can quote a percentage that
     * still traces back to the run. Computed in the run, never in the agent.
     */
    ratios?: Record<string, number>
  }
  /** Combinations that are legal but not credible (brief step 3g). */
  sanityChecks?: Array<{ id: string; severity: string; message: string }>
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
   * `template` means code assembled the paragraph, not a model. It is reported
   * rather than hidden, for the same reason the AI plan badge names the
   * deterministic parser: a demo with no key must not read as if a model wrote it.
   */
  explanationSource: 'anthropic' | 'openai-compatible' | 'template'
  /**
   * Why code wrote it, when it did. This exists because "no model is
   * configured" and "the model was rate-limited" look identical from the
   * source alone, and only one of them is worth retrying. Null/absent when a
   * model wrote the explanation.
   */
  explanationNote?: string | null
  status: string
  warning: string
}

/** Provenance for the building footprint GeoJSON. */
export interface BuildingsMeta {
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
