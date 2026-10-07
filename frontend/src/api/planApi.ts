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
/** What `POST /api/scenes/{sceneId}/ai-plan` produced, and how. */
export interface AiPlanResult {
  planId: string | null
  features: number
  provider: string
  types: string[]
  groundingNotes: string[]
  message: string | null
}

/**
 * `POST /api/scenes/{sceneId}/ai-plan` -- natural language to a draft plan.
 *
 * The spec reserves this endpoint ("AI 相关接口只保留占位"); this fills it in.
 * The result is a normal plan, so the scene updates through exactly the same
 * store and render path as a hand-drawn intervention -- the AI is not a
 * separate rendering pipeline, it is a proposal generator feeding the one that
 * already exists.
 *
 * `provider` defaults to `auto`: the LLM when a key is configured, the
 * deterministic heuristic otherwise. The response always says which ran, so
 * the UI never has to guess and the pitch can be honest about it.
 */
export async function createAiPlan(
  sceneId: string,
  instruction: string,
  provider: 'auto' | 'llm' | 'heuristic' = 'auto',
): Promise<AiPlanResult> {
  const { data } = await http.post<AiPlanResult>(`/scenes/${sceneId}/ai-plan`, {
    instruction,
    provider,
  })
  return data
}
