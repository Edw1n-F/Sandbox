/**
 * Scene + terrain endpoints (spec §3.2, §3.4).
 *
 * CLAUDE.md: "Scene as entry anchor" — all rendering begins at `getScene`.
 */
import { http } from './client'
import type { EnvParams, RunResult, Scene, TerrainMeta } from './types'

/** `GET /api/scenes/{sceneId}` — the 3D sandbox init entry point. */
export async function getScene(sceneId: string): Promise<Scene> {
  const { data } = await http.get<Scene>(`/scenes/${sceneId}`)
  return data
}

/** `GET /api/scenes/{sceneId}/terrain` — provider inputs for Cesium. */
export async function getTerrain(sceneId: string): Promise<TerrainMeta> {
  const { data } = await http.get<TerrainMeta>(`/scenes/${sceneId}/terrain`)
  return data
}

/** `PUT /api/scenes/{sceneId}` — partial update; omitted fields are preserved. */
export async function updateScene(
  sceneId: string,
  payload: { envParams?: EnvParams; name?: string },
): Promise<Scene> {
  const { data } = await http.put<Scene>(`/scenes/${sceneId}`, payload)
  return data
}

/**
 * `POST /api/scenes/{sceneId}/run` — one live simulation run.
 *
 * The sliders' values travel here rather than onto the scene (ADR-024), so a
 * run never mutates the object every plan hangs off. `planIds: []` scores
 * today's land cover with no interventions.
 *
 * `planIds` is the cumulative selection: every active project's plan, merged
 * server-side into one scenario. The brief's goal is comparing combined
 * projects ("rather than only assessing each project individually"), and one
 * `planId` cannot express that.
 */
export async function runSimulation(
  sceneId: string,
  body: {
    rainfallMm: number
    temperatureC: number
    planId: string | null
    planIds?: string[]
  },
): Promise<RunResult> {
  const { data } = await http.post<RunResult>(`/scenes/${sceneId}/run`, body)
  return data
}

// There is deliberately no `listScenes`. §3.2 defines POST /api/scenes but no
// collection GET, and inventing one would make the frontend depend on an
// endpoint the spec does not have. The sceneId is configured, not discovered.
