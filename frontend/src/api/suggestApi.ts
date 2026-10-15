/**
 * Ranked candidate sites for the intervention loop's "suggest" beat.
 *
 * The backend reads the study area's rasters -- land cover, the wet grid and
 * the surveyed building footprints, all at 5 m -- and answers `GET
 * /api/scenes/{sceneId}/suggestions` with candidate *parcels* per axis
 * (drainage / heat / runoff), each carrying the footprint, the estimate a run
 * would produce, and a ready-made plan intervention. This module is the read
 * side (`fetchSuggestions`) and the write side (`pinSuggestion`).
 *
 * Two properties the whole loop depends on:
 *
 * 1. A candidate's geometry travels **verbatim** into the pin POST, exactly as
 *    the project catalog does. The candidate was computed from the rasters at
 *    generation time; re-deriving it here would mean the pinned footprint no
 *    longer matches the estimate the panel showed.
 * 2. `runoffDeltaM3` is **signed**. A reservoir can legitimately show a
 *    negative one -- filling storage means water that would have run off
 *    stands instead -- so the field is named a delta and never an "avoided"
 *    loss. Anything that takes an absolute value of it is lying about the
 *    direction the ledger will move.
 *
 * The response is also honest about what it is not: `candidatesRefused` names
 * the candidates the 25%-of-study-area cap kept out, and `warning` says the
 * parcels are raster reads, not surveyed sites.
 */
import { http } from './client'
import type { Geometry } from './types'

/** The parcel a candidate would rework: footprint, size, and why it is here. */
export interface CandidateSite {
  geometry: Geometry
  /** [lon, lat] of the parcel. */
  centroid: [number, number]
  areaHa: number
  /** 5 m cells inside the parcel. */
  cells: number
  /** Which raster reading put this parcel on the list. */
  evidence: string
}

/**
 * What a run is expected to say if this candidate is pinned.
 *
 * `runoffDeltaM3` is signed: negative means the scenario moves water into the
 * site instead of letting it run off. It is a delta, never an "avoided" total.
 */
export interface CandidateEstimate {
  cellsCovered: number
  cellsOfSite: number
  areaHa: number
  /** Land class today, e.g. "paved". */
  classToday: string
  /** Land class after the intervention, e.g. "water". */
  classAfter: string
  /** Signed change in runoff, m3. Negative is a real answer, not an error. */
  runoffDeltaM3: number
  /** Storage the intervention adds, m3. */
  storageAddedM3: number
  /** Change in the land-class heat index. */
  heatIndexDelta: number
  /** How the estimate was computed, in words a planner can argue with. */
  basis: string
}

/** One intervention inside a candidate's plan, as the backend assembled it. */
export interface PlanIntervention {
  geometry: Geometry
  interventionType: string
  source: 'manual' | 'ai'
  params: Record<string, unknown>
  style: Record<string, unknown>
}

/** One ranked candidate on one axis. */
export interface Suggestion {
  kind: string
  site: CandidateSite
  estimate: CandidateEstimate
  plan: {
    interventions: PlanIntervention[]
  }
}

/** A candidate the generator refused, and why. Shown, never hidden. */
export interface RefusedCandidate {
  kind: string
  cells: number
  why: string
}

/** One axis of the ledger, with its ranked candidates. */
export interface SuggestionAxis {
  axis: string
  /** How candidates on this axis were ranked, in words. */
  basis: string
  candidates: Suggestion[]
  /** How many candidates the generator found before capping. */
  candidatesAvailable: number
  /** Candidates kept out (e.g. over the 25% area cap). */
  candidatesRefused: RefusedCandidate[]
}

/** `GET /api/scenes/{sceneId}/suggestions` */
export interface SuggestionsResponse {
  status: string
  /** Disclosure: raster reads at 5 m, not surveyed sites. */
  warning: string
  generatedFrom: {
    deterministic: boolean
    landCover: string
    wetGrid: string
    buildings: string
  }
  runContext: {
    /** Storm depth the candidates were generated for, mm. */
    rainfallMm: number
    siteCells: number
    siteAreaHa: number
  }
  axes: SuggestionAxis[]
}

/** A `POST /api/scenes/{sceneId}/pins` body: one plan intervention, verbatim. */
export interface PinPayload {
  geometry: Geometry
  interventionType: string
  params: Record<string, unknown>
  style: Record<string, unknown>
}

/**
 * The pin acknowledgement.
 *
 * Pinning is idempotent server-side: the same footprint twice returns
 * `alreadyPinned: true` rather than a second feature, so the panel can say
 * "already pinned" instead of stacking duplicate rows.
 */
export interface PinResult {
  featureId: string
  planId: string
  pinned: boolean
  alreadyPinned: boolean
}

/**
 * Fetch the ranked candidates for a scene.
 *
 * `rainfallMm` and `maxPerAxis` are optional; when given they are sent as
 * `rainfall_mm` and `max_per_axis` query params (the backend's own default
 * storm applies when they are absent). Omitted parameters are left out of the
 * query entirely rather than sent as `undefined`.
 */
export async function fetchSuggestions(
  sceneId: string,
  rainfallMm?: number,
  maxPerAxis?: number,
): Promise<SuggestionsResponse> {
  const params: Record<string, number> = {}
  if (rainfallMm !== undefined) params.rainfall_mm = rainfallMm
  if (maxPerAxis !== undefined) params.max_per_axis = maxPerAxis
  const { data } = await http.get<SuggestionsResponse>(
    `/scenes/${sceneId}/suggestions`,
    { params },
  )
  return data
}

/**
 * `POST /api/scenes/{sceneId}/pins`.
 *
 * The payload is the candidate's plan intervention stripped to the four wire
 * fields, geometry included byte-for-byte: the pinned footprint must be the
 * one the estimate was computed from, or the before/after comparison the run
 * produces is not a comparison.
 */
export async function pinSuggestion(
  sceneId: string,
  payload: PinPayload,
): Promise<PinResult> {
  const { data } = await http.post<PinResult>(`/scenes/${sceneId}/pins`, payload)
  return data
}
