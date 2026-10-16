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
/** The **served** size of a proposal (design §2.2). */
export interface ProposalSize {
  /** Rasterised cell count of the served geometry, holes subtracted. */
  cells: number
  /** Shoelace area of the served geometry, hectares. */
  areaHa: number
  /** `cells / 17,316`. */
  coverageFraction: number
}

/**
 * The model's raw centre + size ask, echoed verbatim (design §2.2).
 *
 * Provenance only: the served `geometry` is what the server produced from this
 * after snapping and clipping, and it is never rendered or scored from here.
 */
export interface ProposalRequested {
  centre?: { lon: number; lat: number } | null
  sizeCells?: number | null
  /** The ask is echoed verbatim, so unnamed keys survive. */
  [key: string]: unknown
}

/**
 * One parameter the run reads for a kind, with its honesty label (design §2.2).
 *
 * When a planner sets the value by hand (Phase 2 R17), `status` becomes
 * `USER_ASSUMPTION`, `edited` becomes true, and `defaultValue` carries the
 * class default that was replaced — mandatory, because without it the card
 * cannot show what was overridden.
 */
export interface ParameterEntry {
  value: number
  unit: string
  status: string
  source: string
  date?: string | null
  /** How well the number is *known*, never how good the option is. */
  confidence?: string
  defaultValue?: number | null
  edited?: boolean
}

/**
 * One assumption a proposal rests on, and the planner's answer (Phase 2 R8).
 *
 * `scope` discriminates the two kinds: `PROPOSAL` rows travel on each proposal
 * (a reservoir and a paving slab have different footprints and depths), and
 * `SITE` rows travel once per batch on `AiPlanResult.questions`.
 *
 * `answeredValue` is the **server's** parse of `answer`, so a client that sends
 * a mismatched pair can see the disagreement. The server's parse is the one
 * that reaches `params`.
 */
export interface ProposalQuestion {
  /** Derived from `parameter` alone, so it is stable on any machine. */
  id: string
  parameter: string
  scope: string
  prompt: string
  unit: string
  value?: unknown
  answer?: string | null
  answeredValue?: number | null
  /** `OPEN` | `ANSWERED` | `NOT_APPLICABLE`. */
  status: string
}

/** A planner's answer, echoed back on the next request (Phase 2 R13). */
export interface AnsweredQuestion {
  id: string
  parameter?: string
  answer: string
}

/** What `POST /api/scenes/{sceneId}/ai-plan/revalidate` returns (Phase 2 R19.1). */
export interface RevalidateResult {
  proposals: InterventionProposal[]
  rejected: ProposalRejection[]
  questions: ProposalQuestion[]
  status: string
  warning: string
  /** Empty on the edit path: a parameter edit moves no cell (R15). */
  geometryAffectingParameters: string[]
  valuesAffectingOnly: string[]
  explicitStorageAddedM3: number | null
  netStorageCapacityChangeM3: number | null
  provider: string
}

/** The constraint report carried by every proposal (design §2.2). */
export interface ProposalConstraints {
  buildingsClear: boolean
  buildingOverlapCells: number
  buildingIds: string[]
  overlapCells: number
  overlapsWith: string[]
  clippedCells: number
  clippedFraction: number
  withinSingleCap: boolean
  withinCumulativeCap: boolean
  /** `class_change` | `cn_only` | `none`. */
  effect: string
  effectNote: string
}

/** One refused proposal, in `AiPlanResult.rejected` (design §2.2). */
export interface ProposalRejection {
  interventionType: string
  siteId: string | null
  requested: ProposalRequested | null
  /** Closed set: BUILDING_OVERLAP | OVERLAPS_STAGED | OVER_SINGLE_CAP | ... */
  reasonCode: string
  buildingOverlapCells: number
  overlapCells: number
  status: string
  warning: string
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
  /** Additive (design §2.2): the size, ask, constraints and parameters. */
  size?: ProposalSize
  requested?: ProposalRequested | null
  constraints?: ProposalConstraints
  parameters?: Record<string, ParameterEntry>
  /** Phase 2 R8: this proposal's PROPOSAL-scope questions. */
  questions?: ProposalQuestion[]
  status?: string
  warning?: string
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
  /** Refusals that did not survive the constraint engine (design §2.2). */
  rejected?: ProposalRejection[]
  /** Phase 2 R8: the SITE-scope questions, asked once per batch. */
  questions?: ProposalQuestion[]
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
    /** Phase 2 R13: answers to a previous response's questions. */
    answeredQuestions?: AnsweredQuestion[]
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
  if (options.answeredQuestions && options.answeredQuestions.length > 0) {
    payload.answeredQuestions = options.answeredQuestions
  }
  const { data } = await http.post<AiPlanResult>(`/scenes/${sceneId}/ai-plan`, payload)
  return data
}

/**
 * `POST /api/scenes/{sceneId}/ai-plan/revalidate` — what an edit just did (Phase 2 R19.1).
 *
 * The confirm gate's third call. It writes nothing, starts no run and calls no
 * model, so it is safe to call whenever the edited set changes. A depth outside
 * the ledger's accepted range comes back as a 422 with a `detail` naming the
 * parameter — deliberately not a constraint refusal, because the closed
 * `reasonCode` set carries placement predicates on geometry and a typo is not one.
 */
export async function revalidateAiPlan(
  sceneId: string,
  body: {
    proposals: InterventionProposal[]
    instruction?: string
    answeredQuestions?: AnsweredQuestion[]
  },
): Promise<RevalidateResult> {
  const { data } = await http.post<RevalidateResult>(
    `/scenes/${sceneId}/ai-plan/revalidate`,
    body,
  )
  return data
}

/**
 * `POST /api/scenes/{sceneId}/ai-plan/apply` -- put a confirmed subset on the sandbox.
 *
 * The accepted proposals are echoed back verbatim and pinned into the scene's
 * staged plan, which the Interventions panel lists and every run composes. No
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
    /** The input card's answers, one per proposal; an absent key is the AI's estimate. */
    values?: Array<Record<string, number>>
    /** Phase 2 R13: answers to the gate's questions, honoured on this path too. */
    answeredQuestions?: AnsweredQuestion[]
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
      values: args.values ?? [],
      answeredQuestions: args.answeredQuestions ?? [],
    },
  )
  return data
}
