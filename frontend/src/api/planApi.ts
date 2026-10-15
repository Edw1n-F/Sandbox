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

/** `DELETE /api/plans/{planId}/interventions/{interventionId}` -- hard delete. */
export async function deleteIntervention(
  planId: string,
  interventionId: string,
): Promise<void> {
  await http.delete(`/plans/${planId}/interventions/${interventionId}`)
}
/** One AI-proposed feature, not yet persisted (plan v2 §5 confirm gate). */
export interface InterventionProposal {
  geometry: unknown
  interventionType: string
  source: string
  params: Record<string, unknown>
  style: Record<string, unknown>
  siteId: string | null
  rationale: string | null
  basis: string | null
}

/** What `POST /api/scenes/{sceneId}/ai-plan` produced, and how. */
export interface AiPlanResult {
  planId: string | null
  features: number
  provider: string
  types: string[]
  groundingNotes: string[]
  message: string | null
  /** Non-empty when the request used `apply: false`. */
  proposals?: InterventionProposal[]
  candidateCounts?: Record<string, number>
  fallbackNote?: string | null
}

/**
 * `POST /api/scenes/{sceneId}/ai-plan` -- natural language to a draft plan.
 *
 * The spec reserves this endpoint ("AI 相关接口只保留占位"); this fills it in.
 * When `apply` is true (default) the result is a persisted plan, so the scene
 * updates through exactly the same store and render path as a hand-drawn
 * intervention. When `apply` is false the server returns an un-persisted list
 * of proposals the UI shows for user confirmation (plan v2 §5): the accepted
 * subset is then sent to `applyAiPlan`.
 *
 * `provider` defaults to `auto`: the LLM when a key is configured, the
 * deterministic heuristic otherwise. The response always says which ran, so
 * the UI never has to guess and the pitch can be honest about it.
 */
export async function createAiPlan(
  sceneId: string,
  instruction: string,
  options: {
    provider?: 'auto' | 'llm' | 'heuristic'
    apply?: boolean
    /** Plan v2 §8: non-empty = whitelist; null/omitted = no restriction. */
    allowedTypes?: string[] | null
  } = {},
): Promise<AiPlanResult> {
  const payload: Record<string, unknown> = {
    instruction,
    provider: options.provider ?? 'auto',
    apply: options.apply ?? true,
  }
  if (options.allowedTypes && options.allowedTypes.length > 0) {
    payload.allowedTypes = options.allowedTypes
  }
  const { data } = await http.post<AiPlanResult>(`/scenes/${sceneId}/ai-plan`, payload)
  return data
}

/**
 * `POST /api/scenes/{sceneId}/ai-plan/apply` -- persist a confirmed subset.
 *
 * The accepted proposals are echoed back verbatim; the server rewraps them in
 * a `PlanCreate` and stores a draft plan through the standard pipeline. No
 * server-held list bridges the two calls: the proposals live only in the
 * client until a user accepts (plan v2 §5.1).
 */
export async function applyAiPlan(
  sceneId: string,
  args: {
    instruction: string
    provider: string
    proposals: InterventionProposal[]
    groundingNotes?: string[]
    candidateCounts?: Record<string, number>
  },
): Promise<AiPlanResult> {
  const { data } = await http.post<AiPlanResult>(
    `/scenes/${sceneId}/ai-plan/apply`,
    {
      instruction: args.instruction,
      provider: args.provider,
      proposals: args.proposals,
      groundingNotes: args.groundingNotes ?? [],
      candidateCounts: args.candidateCounts ?? {},
    },
  )
  return data
}
