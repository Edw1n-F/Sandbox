/**
 * The sandbox loop's routes (Stage 4) -- `backend/app/routers/sandbox.py`.
 *
 * The sandbox is the scene's staged plan: every intervention the user confirms
 * is pinned there, the Interventions panel lists it, and every run composes it.
 * Removing one is an unpin (`/pins/{id}/unpin`), so it can be pinned back.
 */
import { http } from './client'
import type { Geometry } from './types'

/** One value the run reads for a kind, with the AI's estimate standing by. */
export interface SandboxInput {
  key: string
  label: string
  unit: string
  min: number
  max: number
  /** The value used when the user leaves the field to the AI. */
  estimate: number
  /** Where the estimate comes from, in words a planner can check. */
  estimateBasis: string
}

/** A feature ready to stage: geometry verbatim, never re-derived. */
export interface SandboxFeature {
  geometry: Geometry
  interventionType: string
  params: Record<string, unknown>
  style: Record<string, unknown>
  source?: 'manual' | 'ai'
}

/** One of the three suggestions shown under a run's card. */
export interface SandboxSuggestion {
  axis: string
  kind: string
  label: string
  evidence: string
  estimate: {
    areaHa: number
    runoffDeltaM3: number
    storageAddedM3: number
    heatIndexDelta: number
    basis: string
  }
  feature: SandboxFeature
  inputs: SandboxInput[]
}

export interface SandboxSuggestions {
  status: string
  warning: string
  rainfallMm: number
  suggestions: SandboxSuggestion[]
}

/** What a removal instruction refers to on the sandbox. */
export interface RemovalMatch {
  type: 'intervention' | 'project'
  label: string
  action: 'unpin' | 'toggle_off'
  featureId?: string
  interventionType?: string
  planId?: string
  projectId?: string
}

export interface StageResult {
  planId: string
  features: number
  featureIds: string[]
  alreadyPinned: string[]
  types: string[]
}

/** The best site per axis for the run just made. Takes seconds; ask after the run. */
export async function fetchRunSuggestions(
  sceneId: string,
  rainfallMm: number,
  planIds: string[],
): Promise<SandboxSuggestions> {
  const { data } = await http.post<SandboxSuggestions>(
    `/scenes/${sceneId}/sandbox/suggestions`,
    { rainfallMm, planIds },
  )
  return data
}

/** The input card for one intervention. An empty list means nothing to ask. */
export async function fetchInputs(
  sceneId: string,
  interventionType: string,
  params: Record<string, unknown>,
): Promise<SandboxInput[]> {
  const { data } = await http.post<{ inputs: SandboxInput[] }>(
    `/scenes/${sceneId}/sandbox/inputs`,
    { interventionType, params },
  )
  return data.inputs
}

/**
 * Confirm features onto the sandbox. `values[i]` are the card's answers for
 * `features[i]`; a key left out is handed to the AI's estimate.
 */
export async function stageFeatures(
  sceneId: string,
  features: SandboxFeature[],
  values: Array<Record<string, number>>,
  source: 'manual' | 'ai' = 'manual',
): Promise<StageResult> {
  const { data } = await http.post<StageResult>(`/scenes/${sceneId}/sandbox/stage`, {
    features,
    values,
    source,
  })
  return data
}

/** Resolve a removal instruction to what it refers to. Removes nothing. */
export async function resolveRemoval(
  sceneId: string,
  instruction: string,
): Promise<{ matches: RemovalMatch[]; message: string | null }> {
  const { data } = await http.post<{ matches: RemovalMatch[]; message: string | null }>(
    `/scenes/${sceneId}/sandbox/resolve-removal`,
    { instruction },
  )
  return data
}

/** Take one intervention off the sandbox. It can be pinned back later. */
export async function unpinFeature(sceneId: string, featureId: string): Promise<void> {
  await http.post(`/scenes/${sceneId}/pins/${featureId}/unpin`)
}

/**
 * Put an unpinned row back into the next run.
 *
 * The inverse of `unpinFeature`. It is a flag flip rather than a re-stage, so
 * the row keeps its id, fingerprint and provenance — which is what makes undo
 * an *undo* rather than a second intervention with a different history.
 */
export async function repinFeature(sceneId: string, featureId: string): Promise<void> {
  await http.post(`/scenes/${sceneId}/pins/${featureId}/pin`)
}

/** A placed intervention a change sentence refers to, with its card. */
export interface ModifyTarget {
  featureId: string
  interventionType: string
  label: string
  inputs: SandboxInput[]
  /** Values the sentence itself named, e.g. "5 m deep". */
  suggestedValues: Record<string, number>
}

/** Does this sentence change something placed? `isModification: false` means add instead. */
export async function resolveModification(
  sceneId: string,
  instruction: string,
): Promise<{ isModification: boolean; targets?: ModifyTarget[] }> {
  const { data } = await http.post<{ isModification: boolean; targets?: ModifyTarget[] }>(
    `/scenes/${sceneId}/sandbox/resolve-modification`,
    { instruction },
  )
  return data
}

/** Change a placed intervention's values. Its footprint stays where it is. */
export async function modifyFeature(
  sceneId: string,
  featureId: string,
  values: Record<string, number>,
): Promise<{ featureId: string }> {
  const { data } = await http.post<{ featureId: string }>(`/scenes/${sceneId}/sandbox/modify`, {
    featureId,
    values,
  })
  return data
}

/**
 * What a preview measured, split by whether it is a *state* or a *difference*.
 *
 * Three deltas now exist and they are not interchangeable: the run's own
 * `runDelta` (scenario − today's land cover), `now` (the current sandbox state),
 * and `previewDelta` (preview − now, the **marginal** effect of these
 * candidates). Once prior work exists `previewDelta !== runDelta`, and they
 * differ by exactly the prior work — so `previewDelta` deliberately does not
 * reuse the run's key, and a UI showing one must be able to show the other.
 */
export interface SandboxPreview {
  /** `PREVIEW_NOT_A_RUN_UNCOMMITTED_CHANGE`, distinct from a run's own status. */
  status: string
  /** Non-empty, always: display it verbatim. */
  warning: string
  /** What is measured, and what deliberately is not. Display verbatim. */
  scope: string
  rainfallMm: number
  /** The ledger reading with prior work in place. */
  now: Record<string, number>
  /** The same reading with the candidates added. */
  preview: Record<string, number>
  /** Per-key `preview - now`: the marginal effect. */
  previewDelta: Record<string, number>
  /** The run's own delta, under the run's own keys. Never merged with the above. */
  runDelta: Record<string, number>
  interventionsApplied: Array<Record<string, unknown>>
  /** What the run would SKIP, each with its `why`. */
  interventionsSkipped: Array<Record<string, unknown>>
  sources: Record<string, unknown>
  ledgerSource: string
}

/**
 * Measure an uncommitted change without running it (Phase 4 T01).
 *
 * Writes nothing, starts no run and calls no model: there is no `runId`, no
 * overlay and no result card, and the response carries none of those keys at
 * all. A value the ledger would refuse comes back as a 422.
 */
export async function previewChange(
  sceneId: string,
  body: {
    interventions: Array<Record<string, unknown>>
    values?: Array<Record<string, number> | null>
    planIds?: string[]
    rainfallMm?: number
  },
): Promise<SandboxPreview> {
  const { data } = await http.post<SandboxPreview>(
    `/scenes/${sceneId}/sandbox/preview`,
    {
      interventions: body.interventions,
      values: body.values ?? [],
      planIds: body.planIds ?? [],
      rainfallMm: body.rainfallMm ?? 450,
    },
  )
  return data
}

/**
 * One row of the screening parameter card.
 *
 * ``value`` may be **null**, and that is a finding rather than a gap: a row
 * this project has no number for (`soilGroup` — no soil survey exists) says so
 * with a stated reason instead of being omitted. An absent row and a row we
 * cannot fill look identical on the wire, and the difference between "we did
 * not measure this" and "we forgot to include it" is exactly what the card
 * exists to keep visible.
 */
export interface ParameterCardEntry {
  key: string
  label: string
  value: number | null
  unit: string
  /** `MEASURED` | `DERIVED` | `ASSUMPTION` | `UNAVAILABLE`. */
  status: string
  /** What produced the value — or, for an unavailable row, what is absent. */
  source: string
  /** `null` unless a real acquisition date exists; never today's date. */
  date: string | null
  confidence: string
  note: string
}

/** `GET /api/scenes/{sceneId}/parameter-card` response. */
export interface ParameterCard {
  sceneId: string
  planId: string | null
  status: string
  /** Non-empty on every response: this is a screening artefact and says so. */
  warning: string
  /** The rasters the numbers came from, so a reader knows what they are reading. */
  sources: Record<string, string>
  /** The inputs the caller chose, or the defaults. */
  inputs: Record<string, unknown>
  entries: ParameterCardEntry[]
}

/**
 * The numbers a reader needs *before* trusting a result (brief §2).
 *
 * Includes the rows this project cannot fill, each with a reason. Idempotent
 * and reads the same ledger a run does, so the card cannot disagree with a run.
 */
export async function fetchParameterCard(
  sceneId: string,
  options: { planId?: string; rainfallMm?: number } = {},
): Promise<ParameterCard> {
  const params: Record<string, string | number> = {}
  if (options.planId) params.plan_id = options.planId
  if (options.rainfallMm !== undefined) params.rainfall_mm = options.rainfallMm
  const { data } = await http.get<ParameterCard>(`/scenes/${sceneId}/parameter-card`, { params })
  return data
}
