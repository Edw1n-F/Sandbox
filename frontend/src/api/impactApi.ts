/**
 * The four impact endpoints: what the plan actually costs.
 *
 * Separate from `sceneApi.ts` and `planApi.ts` because these are all
 * read-only reporting, none of them is part of the spec's endpoint list, and
 * they were added after the spec was implemented. Keeping them in their own file
 * means the spec endpoints stay exactly as documented.
 *
 * Every response here carries its own caveats from the backend -- the screening
 * limits, the datum note, which interventions were refused and why. This layer
 * passes them through rather than summarising them away: the whole value of these
 * numbers is that they came out of the project's own storage ledger, and a
 * tidier panel would be a less honest one.
 */
import { http } from './client'

export interface LedgerApplied {
  id: string
  kind: string
  cellsChanged: number
  areaM2: number
  coverageFraction: number
  why: string
  basis?: string | null
}

export interface LedgerSkipped {
  id: string
  kind: string
  why: string
  coverageFraction?: number
}

export interface Ledger {
  sceneId: string
  planId: string | null
  bufferDeficitM3: number
  scenarioDeficitM3: number
  deltaUnstoredM3: number
  deltaRunoffM3: number
  deltaGreenAreaM2: number
  worsens: boolean
  baseline: Record<string, number>
  scenario: Record<string, number>
  interventionsApplied: LedgerApplied[]
  interventionsSkipped: LedgerSkipped[]
  interpretation: string
  note: string
}

export interface SeaLevelProjection {
  id: string
  label: string
  source: string
  seaLevelM: number
  lostRetentionM3: number
  retentionCellsSubmerged: number
  deltaUnstoredM3: number
}

export interface SeaLevelReport {
  sceneId: string
  bufferDeficitM3: number
  groundElevationM: { min: number; max: number }
  currentWaterLevelM: number
  projections: SeaLevelProjection[]
  caveat: string
  finding: string
}

export interface PublishedScenario {
  scenarioId: string
  label: string
  source: string
  worksAreaHa: number
  assumptionNote: string
  assumedConversion: Record<string, number>
  deltaUnstoredM3: number
  scenarioDeficitM3: number
  worsens: boolean
  cellsConverted: Record<string, number>
  cellsWanted: number
  areaShortfallHa: number
  note: string
}

export interface PublishedScenarios {
  sceneId: string
  source: { name: string; publisher: string; url: string; retrieved: string }
  status: string
  why: string[]
  notPublished: string[]
  milestones: { date: string; event: string }[]
  scenarios: PublishedScenario[]
  studyAreaHa: number
}

/** `GET /api/scenes/{sceneId}/ledger` — the buffer deficit, and a scenario's delta. */
export async function getLedger(sceneId: string, planId?: string): Promise<Ledger> {
  const { data } = await http.get<Ledger>(
    `/scenes/${sceneId}/ledger`,
    planId ? { params: { plan_id: planId } } : undefined,
  )
  return data
}

/** `GET /api/scenes/{sceneId}/sea-level` — the official HKO projections, run. */
export async function getSeaLevel(sceneId: string): Promise<SeaLevelReport> {
  const { data } = await http.get<SeaLevelReport>(`/scenes/${sceneId}/sea-level`)
  return data
}

/** `GET /api/scenes/{sceneId}/scenarios` — three published CEDD programmes. */
export async function getPublishedScenarios(
  sceneId: string,
): Promise<PublishedScenarios> {
  const { data } = await http.get<PublishedScenarios>(`/scenes/${sceneId}/scenarios`)
  return data
}

export interface FitCandidate {
  id: string
  label: string
  cellsChanged: number
  deltaUnstoredM3: number
  deltaGreenAreaM2: number
  cost: number
  feasible: boolean
  constraintBreaches: string[]
}

export interface BuildingFit {
  buildingId: string
  recommended: string | null
  candidatesEvaluated: number
  candidates: FitCandidate[]
  constraints: Record<string, unknown>
  weights: Record<string, number>
  note: string
  scaleNote?: string
}

/** `GET /api/scenes/{sceneId}/buildings/{buildingId}/fit` — score the options. */
export async function getBuildingFit(
  sceneId: string,
  buildingId: string,
  objective = 'balanced',
): Promise<BuildingFit> {
  const { data } = await http.get<BuildingFit>(
    `/scenes/${sceneId}/buildings/${buildingId}/fit`,
    { params: { objective } },
  )
  return data
}
