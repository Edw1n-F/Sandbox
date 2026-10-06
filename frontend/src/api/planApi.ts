/**
 * Plan + intervention endpoints (spec §3.3).
 *
 * `listInterventions` returns a standard GeoJSON FeatureCollection that goes
 * straight to Cesium's `GeoJsonDataSource` with no conversion (CLAUDE.md
 * principle 2). An empty `features: []` is a valid answer.
 */
import { http } from './client'
import type { FeatureCollection, Plan } from './types'

/** `GET /api/scenes/{sceneId}/plans` */
export async function listPlans(sceneId: string): Promise<Plan[]> {
  const { data } = await http.get<{ plans: Plan[] }>(`/scenes/${sceneId}/plans`)
  return data.plans ?? []
}

/** `GET /api/plans/{planId}/interventions` -> GeoJSON FeatureCollection */
export async function listInterventions(planId: string): Promise<FeatureCollection> {
  const { data } = await http.get<FeatureCollection>(`/plans/${planId}/interventions`)
  return { type: 'FeatureCollection', features: data.features ?? [] }
}
