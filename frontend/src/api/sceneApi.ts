/**
 * Scene + terrain endpoints (spec §3.2, §3.4).
 *
 * CLAUDE.md: "Scene as entry anchor" — all rendering begins at `getScene`.
 */
import { http } from './client'
import type { EnvParams, Scene, TerrainMeta } from './types'

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

// There is deliberately no `listScenes`. §3.2 defines POST /api/scenes but no
// collection GET, and inventing one would make the frontend depend on an
// endpoint the spec does not have. The sceneId is configured, not discovered.
