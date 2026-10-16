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
