/**
 * The staged scenario's wire types (`GET /api/scenes/{sceneId}/pins`).
 *
 * The staged scenario is one plan server-side (see backend
 * `app/services/pins.py`), and this module is the read side the run-selection
 * link polls: it answers which plan holds the pins and which of its rows enter
 * the next run. It exists so `StagedScenarioLink` never has to know the route
 * or the response shape inline -- the same separation the suggestion and
 * catalog APIs keep.
 *
 * The pinned geometry is read only, never re-derived: a candidate's footprint
 * travels from the raster to the plan verbatim, so the run scores the parcels
 * the panels quoted.
 */
import { http } from './client'
import type { Geometry } from './types'

/** One row of the staged plan, pinned or not. */
export interface StagedFeature {
  id: string
  interventionType: string
  geometry: Geometry
  params: Record<string, unknown>
  style: Record<string, unknown>
  /** False means the user took this one out of the next run. */
  pinned: boolean
  /** The provisioning the row was pinned with, or null when it carries none. */
  provenance: Record<string, unknown> | null
}

/** `GET /api/scenes/{sceneId}/pins` */
export interface StagedPinsResponse {
  /** One of `NOTHING_STAGED` / `ALL_UNPINNED` / `STAGED`. */
  status: string
  /** Disclosure: rasters at 5 m, not surveyed sites. */
  warning: string
  /** The staged plan's id, or null when nothing is pinned yet. */
  planId: string | null
  features: StagedFeature[]
}

/**
 * Fetch the scene's staged scenario.
 *
 * `planId` is what the run needs: the run composes `selectedPlanIds`, and the
 * staged plan is added to that list like any other, so no new composition rule
 * is introduced on either side.
 */
export async function fetchStagedPins(
  sceneId: string,
): Promise<StagedPinsResponse> {
  const { data } = await http.get<StagedPinsResponse>(
    `/scenes/${sceneId}/pins`,
  )
  return data
}
