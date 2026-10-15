/**
 * The fixed project catalog (P0.1 of the intervention loop).
 *
 * The catalog used to live in this component as four hardcoded projects whose
 * phases were *sentences* ("place a large expanded school campus in the
 * northeast"), which the AI planner re-derived into a different footprint on
 * every toggle. The panel said so in its own caption.
 *
 * Now the footprints are generated once from the rasters by
 * `backend/scripts/make_project_catalog.py`, served by
 * `GET /api/scenes/{sceneId}/project-catalog`, and every one of them carries
 * its provenance (which raster, what date, what confidence). This module is
 * the read side; `createPlan` is the write side, and it sends the geometry
 * *verbatim* -- no re-derivation, so the same phase twice is the same plan.
 */
import { http } from './client'
import type { Geometry } from './types'

/** One intervention inside a catalog phase, provenance included. */
export interface CatalogIntervention {
  interventionType: string
  geometry: Geometry
  params: Record<string, unknown>
  style: Record<string, unknown>
  provenance: {
    /** Why this footprint is here, in words a planner can argue with. */
    why: string
    /** How many cells the ledger will convert (the number to quote). */
    cells: number
    /** How many of the site's own cells the polygon claims. */
    maskCells: number
    areaHa: number
  }
}

export interface CatalogPhase {
  id: string
  label: string
  interventions: CatalogIntervention[]
  grounding: {
    evidence: string
    metrics: Record<string, number>
  }
}

export interface CatalogProject {
  id: string
  name: string
  note: string
  /** Which ledger axis this project is expected to move. Stated, never scored. */
  axis: string
  source: string
  date: string
  confidence: string
  phases: CatalogPhase[]
}

export interface ProjectCatalog {
  schema: string
  generatedBy: string
  grid: {
    rows: number
    cols: number
    cellM: number
    cellAreaM2: number
    bounds: number[]
  }
  sources: Record<string, string>
  warning: string
  projects: CatalogProject[]
}

/** `GET /api/scenes/{sceneId}/project-catalog` */
export async function fetchProjectCatalog(sceneId: string): Promise<ProjectCatalog> {
  const { data } = await http.get<ProjectCatalog>(`/scenes/${sceneId}/project-catalog`)
  return data
}

/** A `POST /api/scenes/{sceneId}/plans` body for one catalog phase. */
export interface CreatePlanPayload {
  planType?: string
  label: string
  source: 'manual' | 'ai'
  params: Record<string, unknown>
  interventions: Array<{
    geometry: Geometry
    interventionType: string
    source: 'manual' | 'ai'
    params: Record<string, unknown>
    style: Record<string, unknown>
  }>
}

/** `POST /api/scenes/{sceneId}/plans` -> 201 with the new plan id. */
export async function createPlan(
  sceneId: string,
  payload: CreatePlanPayload,
): Promise<{ planId: string; interventionCount: number }> {
  const { data } = await http.post<{ planId: string; interventionCount: number }>(
    `/scenes/${sceneId}/plans`,
    payload,
  )
  return data
}
