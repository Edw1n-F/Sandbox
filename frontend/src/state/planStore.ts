/**
 * Plan + intervention state.
 *
 * The baseline plan is selected on load because the Oct 6 deliverable is
 * "existing region, nothing on top" — no planned projects are applied.
 */
import { create } from 'zustand'

import { listInterventions, listPlans } from '@/api/planApi'
import { errorMessage } from '@/api/client'
import type { FeatureCollection, Plan } from '@/api/types'

export interface PlanState {
  plans: Plan[]
  activePlanId: string | null
  interventions: FeatureCollection
  loading: boolean
  error: string | null

  load: (sceneId: string) => Promise<void>
  selectPlan: (planId: string) => Promise<void>
  /**
   * Re-fetch the plan list *without* changing the selection.
   *
   * Needed after anything that creates a plan outside this store -- currently
   * the AI module. Calling `load` instead would be wrong twice over: it
   * re-selects the baseline (undoing the caller's choice) and it discards the
   * interventions already fetched for the active plan. The symptom of getting
   * this wrong was a panel reading "No plan - 3 features", which is a store and
   * a list disagreeing about the same thing.
   */
  refreshPlans: (sceneId: string) => Promise<void>
  clearError: () => void
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

export const usePlanStore = create<PlanState>((set, get) => ({
  plans: [],
  activePlanId: null,
  interventions: EMPTY,
  loading: false,
  error: null,

  load: async (sceneId) => {
    set({ loading: true, error: null })
    try {
      const plans = await listPlans(sceneId)
      set({ plans })

      // Prefer the baseline plan; fall back to whatever exists so an empty or
      // oddly-typed store still renders something.
      const preferred =
        plans.find((p) => p.planType === 'baseline') ?? plans[0] ?? null
      if (preferred) {
        await get().selectPlan(preferred.planId)
      } else {
        set({ activePlanId: null, interventions: EMPTY, loading: false })
      }
    } catch (error) {
      set({ error: errorMessage(error), loading: false })
    }
  },

  refreshPlans: async (sceneId) => {
    try {
      set({ plans: await listPlans(sceneId) })
    } catch (error) {
      set({ error: errorMessage(error) })
    }
  },

  selectPlan: async (planId) => {
    set({ loading: true, error: null, activePlanId: planId })
    try {
      const fetched = await listInterventions(planId)
      // A FeatureCollection whose `features` is missing is not an empty plan, it is
      // an unreadable response, and the two must not be confused: storing it as-is
      // left `ImpactPanel` reading `.length` of undefined, which throws inside a
      // render and unmounts the whole app to a blank page.
      const interventions =
        fetched && Array.isArray(fetched.features)
          ? fetched
          : { type: 'FeatureCollection' as const, features: [] }
      set({ interventions, loading: false })
    } catch (error) {
      // A failed要素 load must not wipe the plan list; the viewer still has a
      // valid scene to draw.
      set({ interventions: EMPTY, error: errorMessage(error), loading: false })
    }
  },

  clearError: () => set({ error: null }),
}))
