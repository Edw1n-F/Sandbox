/**
 * Scene + terrain endpoints (spec §3.2, §3.4).
 *
 * CLAUDE.md: "Scene as entry anchor" — all rendering begins at `getScene`.
 */
import { http } from './client'
import type { RunResult, Scene, TerrainMeta } from './types'

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
 *
 * ## Why this request overrides the shared timeout
 *
 * The run's water and heat numbers are computed in about a second, but the
 * response also carries the model-written result card, and the server does not
 * return until that call finishes or gives up. A slow provider therefore makes a
 * run that *already succeeded* look like a failure: measured on a free tier at
 * 60-83 s, against the shared client's 60 s, so the browser aborted the request
 * and the user saw a timeout.
 *
 * The server now bounds the card call (`llm.CARD_DEADLINE_S`, 45 s) and falls
 * back to the code-assembled template rather than failing, which is what makes
 * the run deterministic. This ceiling is the second half of that: it has to sit
 * above the server's own budget, or the client kills the response before the
 * server has finished deciding what to send back.
 */
const RUN_TIMEOUT_MS = 90_000

export async function runSimulation(
  sceneId: string,
  body: {
    rainfallMm: number
    temperatureC: number
    tideState?: 'low' | 'mean' | 'high'
    planId: string | null
    planIds?: string[]
  },
): Promise<RunResult> {
  const { data } = await http.post<RunResult>(`/scenes/${sceneId}/run`, body, {
    timeout: RUN_TIMEOUT_MS,
  })
  return data
}

// There is deliberately no `listScenes`. §3.2 defines POST /api/scenes but no
// collection GET, and inventing one would make the frontend depend on an
// endpoint the spec does not have. The sceneId is configured, not discovered.
